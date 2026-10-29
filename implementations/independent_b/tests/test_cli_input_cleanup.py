"""Input failures retain their classification before any Ledger write."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, cli, initialize, open as open_ledger
from test_public_contract import create, wire


class InputStream:
    def __init__(self, real, failure, cleanup, growth):
        self.real, self.failure, self.cleanup, self.growth = real, failure, cleanup, growth
        self.closes = 0

    def __getattr__(self, name):
        return getattr(self.real, name)

    def read(self, count):
        if self.failure is not None:
            raise self.failure
        return b'x' * count if self.growth else self.real.read(count)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.closes += 1
        self.real.close()
        raise self.cleanup


@contextmanager
def input_fault(path, cleanup, failure=None, growth=False):
    original_open, original_fdopen, original_close = os.open, os.fdopen, os.close
    descriptors, streams, closes = [], [], []

    def opening(name, *args, **kwargs):
        fd = original_open(name, *args, **kwargs)
        if os.fspath(name) == os.fspath(path):
            descriptors.append(fd)
        return fd

    def wrapping(fd, *args, **kwargs):
        stream = original_fdopen(fd, *args, **kwargs)
        if fd in descriptors:
            stream = InputStream(stream, failure, cleanup, growth)
            streams.append(stream)
        return stream

    def closing(fd):
        original_close(fd)
        if fd in descriptors:
            closes.append(fd)
            raise cleanup

    with patch.object(cli.os, 'open', opening), patch.object(cli.os, 'fdopen', wrapping), \
         patch.object(cli.os, 'close', closing):
        yield descriptors, streams, closes


class CLIInputCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input = self.root / 'input'
        self.input.write_bytes(b'abc')

    def read(self, path, maximum):
        if hasattr(cli, 'read_file'):
            return cli.read_file(path, maximum)
        return cli._read_file(str(path), maximum, 'input')

    def released(self, observed):
        descriptors, streams, closes = observed
        self.assertEqual(len(descriptors), 1)
        for fd in descriptors:
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertTrue(all(stream.real.closed and stream.closes == 1 for stream in streams))
        # B retains fd ownership; A transfers ownership to its file object.
        self.assertEqual(len(closes), 1 if hasattr(cli, 'read_file') else 0)

    def test_validation_failure_survives_both_cleanup_layers(self):
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        for case in ('size', 'fifo', 'growth'):
            for kind in (OSError, KeyboardInterrupt, SystemExit):
                with self.subTest(case=case, cleanup=kind.__name__):
                    path = fifo if case == 'fifo' else self.input
                    with input_fault(path, kind('secondary cleanup'), growth=case == 'growth') as observed:
                        with self.assertRaises(BaseException) as caught:
                            self.read(path, 2 if case == 'size' else 3)
                    self.released(observed)
                    self.assertIsInstance(caught.exception, LedgerError)
                    self.assertEqual(caught.exception.code, 'INVALID_INPUT' if case == 'fifo' else 'RESOURCE_LIMIT')
                    self.assertTrue(getattr(caught.exception, '__notes__', []))

    def test_read_failure_and_interruption_survive_cleanup(self):
        for primary_type in (OSError, KeyboardInterrupt, SystemExit):
            for cleanup_type in (OSError, KeyboardInterrupt, SystemExit):
                with self.subTest(primary=primary_type.__name__, cleanup=cleanup_type.__name__):
                    primary = primary_type('original read failure')
                    with input_fault(self.input, cleanup_type('secondary cleanup'), primary) as observed:
                        with self.assertRaises(BaseException) as caught:
                            self.read(self.input, 3)
                    self.released(observed)
                    if isinstance(primary, OSError):
                        self.assertIsInstance(caught.exception, LedgerError)
                        self.assertEqual(caught.exception.code, 'IO_ERROR')
                        self.assertIs(caught.exception.__cause__, primary)
                    else:
                        self.assertIs(caught.exception, primary)
                    self.assertTrue(getattr(primary, '__notes__', []))

    def test_cleanup_alone_is_not_success_even_inside_caller_exception(self):
        for cleanup_type in (OSError, KeyboardInterrupt, SystemExit):
            with self.subTest(cleanup=cleanup_type.__name__):
                cleanup = cleanup_type('standalone cleanup')
                try:
                    raise ValueError('unrelated caller exception')
                except ValueError:
                    with input_fault(self.input, cleanup) as observed:
                        with self.assertRaises(BaseException) as caught:
                            self.read(self.input, 3)
                self.released(observed)
                if isinstance(cleanup, OSError):
                    self.assertIsInstance(caught.exception, LedgerError)
                    self.assertEqual(caught.exception.code, 'IO_ERROR')
                    self.assertIs(caught.exception.__cause__, cleanup)
                else:
                    self.assertIs(caught.exception, cleanup)

    def test_input_boundaries_and_retry_after_failure(self):
        for raw, maximum in ((b'', 0), (b'abc', 3), (b'abc', 4)):
            self.input.write_bytes(raw)
            self.assertEqual(self.read(self.input, maximum), raw)
        with self.assertRaises(LedgerError) as caught:
            self.read(self.input, 2)
        self.assertEqual(caught.exception.code, 'RESOURCE_LIMIT')
        self.assertEqual(self.read(self.input, 3), b'abc')

    def test_cli_rejects_without_write_and_original_request_can_continue(self):
        for mode in ('size', 'read', 'close'):
            with self.subTest(mode=mode):
                db = self.root / (mode + '.sqlite')
                with initialize(db) as ledger:
                    ledger.execute(wire(create('artifact:existing', 'existing')))
                    baseline = ledger.export_bundle()
                before = db.read_bytes()
                request = wire(create('artifact:new', 'new'))
                self.input.write_bytes(request)
                maximum = cli.c.DOCUMENT if hasattr(cli, 'read_file') else cli.MAX_JSON
                if mode == 'size':
                    with self.input.open('r+b') as stream:
                        stream.truncate(maximum + 1)
                primary = OSError('original read') if mode == 'read' else None
                cleanup = KeyboardInterrupt('secondary close') if mode != 'close' else OSError('standalone close')
                args = ['write', '--db', str(db), '--request', str(self.input)]
                with tempfile.TemporaryFile('w+', encoding='utf-8') as output:
                    with input_fault(self.input, cleanup, primary) as observed, patch.object(cli.sys, 'stdout', output):
                        try:
                            status = cli.main(args)
                        except BaseException as error:
                            self.fail('CLI lost its error response: ' + type(error).__name__)
                    self.released(observed)
                    output.seek(0)
                    result = json.loads(output.read())
                self.assertEqual((status, result['status'], result['code'], result['commit_state']),
                                 (2 if mode == 'size' else 1, 'ERROR',
                                  'RESOURCE_LIMIT' if mode == 'size' else 'IO_ERROR', 'not_committed'))
                self.assertEqual(db.read_bytes(), before)
                with open_ledger(db) as ledger:
                    self.assertEqual(ledger.export_bundle(), baseline)
                self.input.write_bytes(request)
                with tempfile.TemporaryFile('w+', encoding='utf-8') as output, patch.object(cli.sys, 'stdout', output):
                    self.assertEqual(cli.main(args), 0)
                    output.seek(0)
                    success = json.loads(output.read())
                self.assertEqual((success['status'], success['replayed']), ('COMMITTED', False))
                with open_ledger(db) as ledger:
                    self.assertEqual(ledger.execute(request), dict(success, replayed=True))
                    self.assertEqual(ledger.verify()['counts']['idempotency_records'], 2)


if __name__ == '__main__':
    unittest.main()
