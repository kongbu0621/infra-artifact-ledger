"""Secondary close failures must not erase the operation's original outcome."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger, storage
from test_failures import ConnectionFault
from test_public_contract import append, create, wire
from test_read_errors import database_error


class ReadCleanupFault:
    def __init__(self, real, primary=None, *, ended=False):
        self.real, self.primary, self.ended = real, primary, ended
        self.rollbacks = 0
        self.secondary = database_error(sqlite3.SQLITE_IOERR_READ)

    def __getattr__(self, name):
        return getattr(self.real, name)

    def execute(self, sql, *args):
        if sql.startswith('SELECT') and self.primary is not None:
            # Model a read error with either an active transaction or one
            # already ended by the storage layer. Both require the original
            # error to survive cleanup, without reporting read success.
            if self.ended:
                self.real.execute('ROLLBACK')
            raise self.primary
        if sql == 'ROLLBACK':
            self.rollbacks += 1
            if not self.ended:
                raise self.secondary
            # Real SQLite rejects this rollback after the transaction ended.
        return self.real.execute(sql, *args)


class CloseFault:
    def __init__(self, real, fail_statement=None):
        self.real = real
        self.fail_statement = fail_statement
        self.closes = 0
        self.primary = None

    def __getattr__(self, name):
        return getattr(self.real, name)

    def execute(self, sql, *args):
        if self.fail_statement and sql.startswith(self.fail_statement):
            self.primary = sqlite3.OperationalError('injected primary read failure')
            self.primary.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise self.primary
        return self.real.execute(sql, *args)

    def close(self):
        self.closes += 1
        self.real.close()
        error = sqlite3.OperationalError('injected secondary close failure')
        error.sqlite_errorcode = sqlite3.SQLITE_IOERR
        raise error


class CleanupErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_context_close_preserves_primary_error_and_commit_state(self):
        for mode, expected in (
            ('read', ('NOT_FOUND', 'not_applicable')),
            ('invalid-write', ('INVALID_INPUT', 'not_committed')),
            ('lost-commit-ack', ('DURABILITY_UNKNOWN', 'unknown')),
            ('corrupt-replay', ('INTEGRITY_FAILURE', 'committed')),
            ('interrupted', None),
        ):
            with self.subTest(mode=mode):
                path = self.root/(mode+'.sqlite')
                ledger = initialize(path)
                ledger.execute(wire(create()))
                ledger.execute(wire(append()), payloads={'blob:one': b'original\x00\xff'})
                raw_db = ledger._db
                original_operation = ledger.get_operation('demo:scope', 'append_version', 'append')
                if mode == 'corrupt-replay':
                    raw_db.execute("UPDATE b_objects SET content=? WHERE owned_id='blob:one'", (b'corrupt',))
                before = path.read_bytes()
                wrapped = ConnectionFault(raw_db, 'COMMIT', after=True) if mode == 'lost-commit-ack' else raw_db
                close_fault = CloseFault(wrapped)
                ledger._db = close_fault
                primary = None
                try:
                    with self.assertRaises(BaseException) as caught:
                        with ledger:
                            try:
                                if mode == 'read':
                                    ledger.get_record('artifact', 'artifact:missing')
                                elif mode == 'invalid-write':
                                    ledger.execute(b'{}')
                                elif mode == 'lost-commit-ack':
                                    ledger.execute(wire(create('artifact:second', 'second')))
                                elif mode == 'corrupt-replay':
                                    ledger.execute(wire(append()), payloads={})
                                else:
                                    raise KeyboardInterrupt('injected interruption')
                            except BaseException as exc:
                                primary = exc
                                raise
                    self.assertIs(caught.exception, primary)
                    if expected is not None:
                        self.assertIsInstance(primary, LedgerError)
                        self.assertEqual((primary.code, primary.commit_state), expected)
                    else:
                        self.assertIsInstance(primary, KeyboardInterrupt)
                    self.assertEqual(close_fault.closes, 1)
                    if mode != 'lost-commit-ack':
                        self.assertEqual(path.read_bytes(), before)
                    if mode == 'corrupt-replay':
                        self.assertEqual(primary.details['result_ref'], 'version:one')
                        self.assertEqual(primary.details['recorded_at'], original_operation['idempotency_record']['recorded_at'])
                    with open_ledger(path) as reopened:
                        self.assertEqual(reopened.get_operation('demo:scope', 'append_version', 'append'), original_operation)
                        if mode == 'corrupt-replay':
                            with self.assertRaises(LedgerError) as bad_content:
                                reopened.read_blob('blob:one')
                            self.assertEqual(bad_content.exception.code, 'INTEGRITY_FAILURE')
                        else:
                            self.assertEqual(reopened.verify()['counts']['idempotency_records'], 3 if mode == 'lost-commit-ack' else 2)
                        if mode == 'lost-commit-ack':
                            original = reopened.get_operation('demo:scope', 'create_artifact', 'second')
                            replay = reopened.execute(wire(create('artifact:second', 'second')))
                            self.assertEqual(replay['result_ref'], original['result']['artifact_id'])
                            self.assertTrue(replay['replayed'])
                            self.assertEqual(reopened.verify()['counts']['idempotency_records'], 3)
                finally:
                    raw_db.close()

    def test_context_success_still_reports_close_failure(self):
        path = self.root/'successful-read.sqlite'
        ledger = initialize(path)
        real = ledger._db
        fault = CloseFault(real)
        ledger._db = fault
        try:
            with self.assertRaises(LedgerError) as caught:
                with ledger:
                    self.assertEqual(ledger.verify()['counts']['artifacts'], 0)
            self.assertEqual((caught.exception.code, caught.exception.commit_state), ('IO_ERROR', 'not_applicable'))
            self.assertEqual(fault.closes, 1)
            with open_ledger(path) as reopened:
                self.assertEqual(reopened.verify()['counts']['artifacts'], 0)
        finally:
            real.close()

    def test_open_cleanup_preserves_read_or_format_error(self):
        path = self.root/'open.sqlite'
        with initialize(path):
            pass
        real_connect = sqlite3.connect
        for mode, expected in (('read', 'BUSY'), ('format', 'UNSUPPORTED_VERSION')):
            with self.subTest(mode=mode):
                if mode == 'format':
                    with real_connect(path) as db:
                        db.execute("UPDATE b_format SET name='unknown-format'")
                before = path.read_bytes()
                faults = []
                def connect(*args, **kwargs):
                    fault = CloseFault(real_connect(*args, **kwargs), 'SELECT name FROM b_format' if mode == 'read' else None)
                    faults.append(fault)
                    return fault
                with patch.object(storage.sqlite3, 'connect', side_effect=connect):
                    with self.assertRaises(LedgerError) as caught:
                        open_ledger(path)
                self.assertEqual((caught.exception.code, caught.exception.commit_state), (expected, 'not_applicable'))
                self.assertEqual(len(faults), 1)
                self.assertEqual(faults[0].closes, 1)
                if mode == 'read':
                    self.assertIs(caught.exception.__cause__, faults[0].primary)
                    with open_ledger(path) as reopened:
                        self.assertEqual(reopened.verify()['counts']['artifacts'], 0)
                self.assertEqual(path.read_bytes(), before)

    def test_read_cleanup_preserves_storage_error_or_interruption(self):
        for ended in (False, True):
            for mode, primary, expected in (
                ('io', database_error(sqlite3.SQLITE_IOERR_READ), 'IO_ERROR'),
                ('busy', database_error(sqlite3.SQLITE_BUSY), 'BUSY'),
                ('interrupt', KeyboardInterrupt('injected read interruption'), None),
            ):
                for method in ('get_record', 'get_history', 'get_operation',
                               'read_blob', 'verify', 'export_bundle'):
                    with self.subTest(ended=ended, mode=mode, method=method):
                        path = self.root/f'{ended}-{mode}-{method}.sqlite'
                        with initialize(path) as ledger:
                            ledger.execute(wire(create()))
                            ledger.execute(wire(append()), payloads={'blob:one': b'original\x00\xff'})
                            operation = ledger.get_operation('demo:scope', 'append_version', 'append')
                            before = path.read_bytes()
                            args = {'get_record': ('artifact', 'artifact:report'),
                                    'get_history': ('artifact:report',),
                                    'get_operation': ('demo:scope', 'append_version', 'append'),
                                    'read_blob': ('blob:one',)}.get(method, ())
                            fault = ReadCleanupFault(ledger._db, primary, ended=ended)
                            with patch.object(ledger, '_db', fault):
                                with self.assertRaises(BaseException) as caught:
                                    getattr(ledger, method)(*args)
                            self.assertEqual(fault.rollbacks, 1)
                            if expected is None:
                                self.assertIs(caught.exception, primary)
                            else:
                                self.assertIsInstance(caught.exception, LedgerError)
                                self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                                 (expected, 'not_applicable'))
                                self.assertIs(caught.exception.__cause__, primary)
                            self.assertEqual(ledger._db.in_transaction, not ended)
                            if ended:
                                self.assertEqual(ledger.read_blob('blob:one'), b'original\x00\xff')
                        # Closing releases an unresolved read transaction. The
                        # caller may then reopen and use the same request.
                        self.assertEqual(path.read_bytes(), before)
                        with open_ledger(path) as reopened:
                            self.assertEqual(reopened.get_operation('demo:scope', 'append_version', 'append'), operation)
                            self.assertTrue(reopened.execute(wire(append()), payloads={})['replayed'])
                            self.assertEqual(reopened.verify()['counts']['idempotency_records'], 2)

    def test_read_cleanup_preserves_domain_failure(self):
        for mode, expected in (('missing', 'NOT_FOUND'), ('invalid', 'INVALID_INPUT'),
                               ('corrupt', 'INTEGRITY_FAILURE')):
            with self.subTest(mode=mode):
                path = self.root/f'domain-{mode}.sqlite'
                with initialize(path) as ledger:
                    ledger.execute(wire(create()))
                    ledger.execute(wire(append()), payloads={'blob:one': b'original\x00\xff'})
                    if mode == 'corrupt':
                        ledger._db.execute("UPDATE b_objects SET content=? WHERE owned_id='blob:one'", (b'corrupt',))
                    before = path.read_bytes()
                    def read():
                        if mode == 'missing':
                            return ledger.get_record('artifact', 'artifact:missing')
                        if mode == 'invalid':
                            return ledger.get_record('unknown', 'artifact:report')
                        return ledger.read_blob('blob:one')
                    with self.assertRaises(LedgerError) as reference:
                        read()
                    with patch.object(ledger, '_db', ReadCleanupFault(ledger._db)):
                        with self.assertRaises(LedgerError) as caught:
                            read()
                    self.assertEqual(caught.exception.envelope(), reference.exception.envelope())
                    self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                     (expected, 'not_applicable'))
                self.assertEqual(path.read_bytes(), before)
                with open_ledger(path) as reopened:
                    self.assertEqual(reopened.get_operation('demo:scope', 'append_version', 'append')['result']['version_id'], 'version:one')

    def test_successful_read_still_reports_rollback_failure(self):
        path = self.root/'read-rollback.sqlite'
        with initialize(path) as ledger:
            ledger.execute(wire(create()))
            before = path.read_bytes()
            fault = ReadCleanupFault(ledger._db)
            with patch.object(ledger, '_db', fault):
                with self.assertRaises(LedgerError) as caught:
                    ledger.get_record('artifact', 'artifact:report')
            self.assertEqual((caught.exception.code, caught.exception.commit_state),
                             ('IO_ERROR', 'not_applicable'))
            self.assertIs(caught.exception.__cause__, fault.secondary)
            self.assertEqual(fault.rollbacks, 1)
        self.assertEqual(path.read_bytes(), before)
        with open_ledger(path) as reopened:
            self.assertEqual(reopened.verify()['counts']['idempotency_records'], 1)

    def test_cli_read_cleanup_error_envelope_and_no_output_publication(self):
        path = self.root/'cli-read.sqlite'
        with initialize(path) as ledger:
            ledger.execute(wire(create()))
            ledger.execute(wire(append()), payloads={'blob:one': b'original\x00\xff'})
        before = path.read_bytes()
        program = '''import json, sqlite3, sys
from unittest.mock import patch
from infra_artifact_ledger import cli
real_open = cli.open_ledger
def open_fault(path):
    ledger = real_open(path)
    real = ledger._db
    class Fault:
        def __getattr__(self, name):
            return getattr(real, name)
        def execute(self, sql, *args):
            if sql.startswith('SELECT') and sys.argv[2] != '0':
                error = sqlite3.OperationalError('primary read failure')
                error.sqlite_errorcode = int(sys.argv[2])
                raise error
            if sql == 'ROLLBACK':
                error = sqlite3.OperationalError('secondary cleanup failure')
                error.sqlite_errorcode = sqlite3.SQLITE_IOERR_READ
                raise error
            return real.execute(sql, *args)
        def close(self):
            real.close()
    ledger._db = Fault()
    return ledger
with patch.object(cli, 'open_ledger', side_effect=open_fault):
    sys.exit(cli.main(json.loads(sys.argv[1])))
'''
        for primary, expected, status in ((sqlite3.SQLITE_BUSY, 'BUSY', 6),
                                           (sqlite3.SQLITE_IOERR_READ, 'IO_ERROR', 1),
                                           (0, 'IO_ERROR', 1)):
            for command, options in (
                ('verify', []),
                ('read-blob', ['--blob-ref', 'blob:one', '--output', str(self.root/'blob-output')]),
                ('export', ['--package', str(self.root/'package'), '--descriptor', str(self.root/'descriptor')]),
            ):
                with self.subTest(primary=primary, command=command):
                    args = [command, '--db', str(path), *options]
                    argv = [sys.executable, *(['-I'] if sys.flags.isolated else []),
                            '-c', program, json.dumps(args), str(primary)]
                    result = subprocess.run(argv, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertEqual(result.stderr, b'')
                    self.assertEqual(result.stdout.count(b'\n'), 1)
                    response = json.loads(result.stdout)
                    self.assertEqual((response['status'], response['code'], response['commit_state']),
                                     ('ERROR', expected, 'not_applicable'))
                    for name in ('blob-output', 'package', 'descriptor'):
                        self.assertFalse((self.root/name).exists())
                    self.assertEqual(path.read_bytes(), before)
        with open_ledger(path) as reopened:
            self.assertEqual(reopened.verify()['counts']['idempotency_records'], 2)
            self.assertEqual(reopened.read_blob('blob:one'), b'original\x00\xff')


if __name__ == '__main__':
    unittest.main()
