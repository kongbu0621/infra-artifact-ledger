"""Secondary close failures must not erase the operation's original outcome."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger, storage
from test_failures import ConnectionFault
from test_public_contract import append, create, wire


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


if __name__ == '__main__':
    unittest.main()
