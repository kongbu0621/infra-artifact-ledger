"""Exercise real file/directory handles through B's public storage paths."""
import builtins
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, cli, initialize, open as open_ledger, storage
from test_public_contract import append, create, wire


class FileFault:
    def __init__(self, real, method, primary, cleanup):
        self.real, self.method = real, method
        self.primary, self.cleanup = primary, cleanup
        self.closes = 0

    def __getattr__(self, name):
        if name == self.method and self.primary is not None:
            def fail(*args, **kwargs):
                raise self.primary
            return fail
        return getattr(self.real, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.closes += 1
        self.real.close()
        raise self.cleanup


class NestedCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def assert_failure(self, caught, primary, operation, published):
        if isinstance(primary, Exception):
            self.assertIsInstance(caught, LedgerError)
            self.assertEqual(caught.code, getattr(primary, 'code', 'IO_ERROR'))
            if operation == 'init':
                self.assertEqual(caught.details, {'target_published': published})
            error, seen = caught, set()
            while error is not primary and error is not None and id(error) not in seen:
                seen.add(id(error))
                error = error.__cause__
            self.assertIs(error, primary)
        else:
            self.assertIs(caught, primary)

    def call(self, operation, path):
        if operation == 'init':
            initialize(path).close()
        elif operation == 'open':
            open_ledger(path).close()
        else:
            storage.publish(path, b'complete output')

    def check_result(self, operation, path, published, before=None):
        self.assertEqual(path.exists(), published or operation == 'open')
        self.assertEqual(list(self.root.glob('.ledger-b-*')), [])
        if operation == 'open':
            self.assertEqual(path.read_bytes(), before)
        if not path.exists():
            self.call(operation, path)
        if operation == 'publish':
            self.assertEqual(path.read_bytes(), b'complete output')
        else:
            with open_ledger(path) as ledger:
                self.assertEqual(ledger.verify()['counts']['idempotency_records'], 0)

    def directory_cases(self, with_primary):
        real_fsync, real_close = os.fsync, os.close
        for operation in ('init', 'publish'):
            for primary_type in ((OSError, KeyboardInterrupt, SystemExit) if with_primary else (None,)):
                for cleanup_type in (OSError, KeyboardInterrupt, SystemExit):
                    with self.subTest(operation=operation, primary=primary_type, cleanup=cleanup_type):
                        path = self.root / f'{operation}-{primary_type}-{cleanup_type}'
                        primary = primary_type('directory sync') if primary_type else None
                        cleanup = cleanup_type('directory close')
                        closed = []
                        def sync(fd):
                            if stat.S_ISDIR(os.fstat(fd).st_mode) and primary is not None:
                                raise primary
                            return real_fsync(fd)
                        def close(fd):
                            is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                            real_close(fd)
                            if is_directory:
                                closed.append(fd)
                                raise cleanup
                        try:
                            raise ValueError('already handled by caller')
                        except ValueError:
                            with patch.object(storage.os, 'fsync', side_effect=sync), \
                                 patch.object(storage.os, 'close', side_effect=close):
                                with self.assertRaises(BaseException) as caught:
                                    self.call(operation, path)
                        self.assertEqual(len(closed), 1)
                        with self.assertRaises(OSError):
                            os.fstat(closed[0])
                        self.assert_failure(caught.exception, primary or cleanup, operation, True)
                        if primary is not None:
                            self.assertTrue(getattr(primary, '__notes__', []))
                        self.check_result(operation, path, True)

    def test_directory_primary_survives_close_failure(self):
        self.directory_cases(True)

    def test_directory_close_alone_is_reported(self):
        self.directory_cases(False)

    def file_cases(self, with_primary):
        for operation in ('init', 'open', 'publish'):
            for primary_type in ((OSError, KeyboardInterrupt, SystemExit) if with_primary else (None,)):
                for cleanup_type in (OSError, KeyboardInterrupt, SystemExit):
                    with self.subTest(operation=operation, primary=primary_type, cleanup=cleanup_type):
                        path = self.root / f'{operation}-{primary_type}-{cleanup_type}'
                        if operation == 'open':
                            initialize(path).close()
                        before = path.read_bytes() if path.exists() else None
                        primary = primary_type('file operation') if primary_type else None
                        cleanup = cleanup_type('file close')
                        captured = []
                        def wrap(real):
                            method = 'fileno' if operation == 'init' else 'read'
                            fault = FileFault(real, method, primary, cleanup)
                            captured.append(fault)
                            return fault
                        real_fdopen = os.fdopen
                        opener = patch.object(storage, 'open', side_effect=lambda *a, **kw: wrap(builtins.open(*a, **kw)), create=True) \
                            if operation == 'init' else patch.object(storage.os, 'fdopen', side_effect=lambda *a, **kw: wrap(real_fdopen(*a, **kw)))
                        try:
                            raise ValueError('already handled by caller')
                        except ValueError:
                            with opener:
                                with self.assertRaises(BaseException) as caught:
                                    self.call(operation, path)
                        self.assertEqual(len(captured), 1)
                        self.assertEqual(captured[0].closes, 1)
                        self.assertTrue(captured[0].real.closed)
                        self.assert_failure(caught.exception, primary or cleanup, operation, False)
                        if primary is not None:
                            self.assertTrue(getattr(primary, '__notes__', []))
                        self.check_result(operation, path, False, before)

    def test_file_primary_survives_close_failure(self):
        self.file_cases(True)

    def test_file_close_alone_is_reported(self):
        self.file_cases(False)

    def test_content_verification_error_survives_close_failure(self):
        path = self.root / 'output.bin'
        real_fdopen = os.fdopen
        captured = []
        def wrap(*args, **kwargs):
            fault = FileFault(real_fdopen(*args, **kwargs), None, None, OSError('close'))
            fault.read = lambda count: b'corrupt'
            captured.append(fault)
            return fault
        with patch.object(storage.os, 'fdopen', side_effect=wrap):
            with self.assertRaises(LedgerError) as caught:
                storage.publish(path, b'complete output')
        self.assertEqual(caught.exception.code, 'INTEGRITY_FAILURE')
        self.assertTrue(getattr(caught.exception, '__notes__', []))
        self.assertTrue(captured[0].real.closed)
        self.check_result('publish', path, False)

    def test_cli_keeps_error_envelope_and_original_results(self):
        db = self.root / 'original.sqlite'
        with initialize(db) as ledger:
            ledger.execute(wire(create()))
            result = ledger.execute(wire(append()), payloads={'blob:one': b'original\x00\xff'})
            bundle = ledger.export_bundle()
        before = db.read_bytes()
        real_fdopen = os.fdopen
        for fault, expected, status in (('read', 'IO_ERROR', 1), ('content', 'INTEGRITY_FAILURE', 5)):
            with self.subTest(fault=fault):
                target = self.root / fault
                def wrap(fd, mode, *args, **kwargs):
                    real = real_fdopen(fd, mode, *args, **kwargs)
                    if mode != 'w+b':
                        return real
                    if fault == 'read':
                        return FileFault(real, 'read', OSError('read'), KeyboardInterrupt('close'))
                    damaged = FileFault(real, None, None, OSError('close'))
                    damaged.read = lambda count: b'corrupt'
                    return damaged
                with tempfile.TemporaryFile() as output, \
                     patch.object(cli.sys, 'stdout', output), \
                     patch.object(storage.os, 'fdopen', side_effect=wrap):
                    try:
                        code = cli.main(['read-blob', '--db', str(db), '--blob-ref', 'blob:one', '--output', str(target)])
                    except BaseException as exc:
                        raise AssertionError('CLI lost the primary failure envelope') from exc
                    output.seek(0)
                    envelope = json.loads(output.read())
                self.assertEqual(code, status)
                self.assertEqual((envelope['status'], envelope['code'], envelope['commit_state']),
                                 ('ERROR', expected, 'not_applicable'))
                self.assertFalse(target.exists())
                self.assertEqual(db.read_bytes(), before)
                with open_ledger(db) as ledger:
                    self.assertEqual(ledger.export_bundle(), bundle)
                    self.assertEqual(ledger.execute(wire(append()), payloads={}), dict(result, replayed=True))
                self.assertEqual(db.read_bytes(), before)
