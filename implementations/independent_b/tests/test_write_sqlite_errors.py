"""Native SQLite integrity failures retain their category and known outcome.

Faults affect new synthetic databases only; A and B use the same public oracle.
"""
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import infra_artifact_ledger as implementation
from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from cross_implementation import assert_metadata, assert_replay, portable_metadata
from test_public_contract import append, create, import_request, wire
from test_failures import ConnectionFault


class SQLiteFault:
    def __init__(self, real, code, rollback_fails=False):
        self.real = real
        self.error = sqlite3.OperationalError('injected SQLite failure')
        self.error.sqlite_errorcode = code
        self.rollback_fails = rollback_fails

    def __getattr__(self, name):
        return getattr(self.real, name)

    def execute(self, sql, *args):
        # Original operation lookup succeeds before the retained-record read.
        if sql.startswith('SELECT') and any(table in sql for table in (' FROM b_objects', ' FROM records')):
            raise self.error
        if sql == 'ROLLBACK' and self.rollback_fails:
            raise OSError('injected secondary rollback failure')
        return self.real.execute(sql, *args)


def connection_slot(ledger):
    return (ledger, '_db') if hasattr(ledger, '_db') else (ledger._store, 'connection')


class WriteSQLiteErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.sqlite'
        self.ledger = initialize(self.path)
        self.addCleanup(lambda: self.ledger.close())
        self.calls = [(create(), {}), (append(), {'payloads': {'blob:one': b'original\x00\xff'}})]
        for request, kwargs in self.calls:
            self.ledger.execute(wire(request), **kwargs)
        bundle = self.ledger.export_bundle()
        imported = (import_request(), {'package': bundle['package_utf8'], 'descriptor': bundle['descriptor_utf8']})
        self.ledger.execute(wire(imported[0]), **imported[1])
        self.calls.append(imported)
        self.expected = portable_metadata(self.ledger.export_bundle())
        self.before = self.path.read_bytes()

    @contextmanager
    def fault(self, code, rollback_fails=False):
        owner, attribute = connection_slot(self.ledger)
        real = getattr(owner, attribute)
        fault = SQLiteFault(real, code, rollback_fails)
        try:
            with patch.object(owner, attribute, fault):
                yield fault.error
        finally:
            # Failed assertions must not contaminate the next fault case.
            if not rollback_fails:
                self.assertFalse(real.in_transaction)
            self.ledger.close()
            self.ledger = open_ledger(self.path)
            self.assert_preserved()

    def assert_preserved(self):
        self.assertEqual(self.path.read_bytes(), self.before)
        assert_metadata(self.ledger, self.expected)
        self.assertIs(type(self.ledger.read_blob('blob:one')), bytes)
        self.assertEqual(self.ledger.read_blob('blob:one'), b'original\x00\xff')
        for request, kwargs in self.calls:
            assert_replay(self.ledger, request, kwargs, self.expected)

    def assert_committed(self, error, request, code):
        self.assertEqual((error.code, error.commit_state), (code, 'committed'))
        operation, = [op for op in self.expected['idempotency_records']
                      if all(op[key] == request[key] for key in (
                          'idempotency_scope_ref', 'operation_kind', 'idempotency_key'))]
        self.assertEqual(error.details, {key: operation[key] for key in (
            'result_ref', 'idempotency_scope_ref', 'operation_kind', 'idempotency_key', 'recorded_at')})

    def test_native_replay_error_survives_secondary_rollback_failure(self):
        cases = [(code, 'INTEGRITY_FAILURE') for code in (
            sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_CORRUPT_VTAB,
            sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CONSTRAINT_TRIGGER)]
        cases += [(sqlite3.SQLITE_IOERR_READ, 'IO_ERROR'), (sqlite3.SQLITE_ERROR, 'IO_ERROR')]
        for request, kwargs in self.calls:
            for native, expected in cases:
                for rollback_fails in (False, True):
                    with self.subTest(kind=request['operation_kind'], native=native, rollback_fails=rollback_fails):
                        with self.fault(native, rollback_fails) as primary:
                            with self.assertRaises(LedgerError) as caught:
                                self.ledger.execute(wire(request), **kwargs)
                            self.assert_committed(caught.exception, request, expected)
                            self.assertIs(caught.exception.__cause__, primary)

    def test_real_sqlite_constraint_rolls_back_and_original_request_can_continue(self):
        owner, attribute = connection_slot(self.ledger)
        db = getattr(owner, attribute)
        # Fail the operation insert after owned records have been inserted.
        table = 'b_operations' if hasattr(self.ledger, '_db') else 'operations'
        request = create(artifact='artifact:new', key='new')
        for action in ('ABORT', 'ROLLBACK'):
            with self.subTest(action=action):
                db.execute(f"CREATE TEMP TRIGGER reject_insert BEFORE INSERT ON {table} BEGIN SELECT RAISE({action}, 'synthetic constraint'); END")
                try:
                    with self.assertRaises(LedgerError) as caught:
                        self.ledger.execute(wire(request))
                    self.assertEqual((caught.exception.code, caught.exception.commit_state), ('INTEGRITY_FAILURE', 'not_committed'))
                    self.assertEqual(caught.exception.__cause__.sqlite_errorcode, sqlite3.SQLITE_CONSTRAINT_TRIGGER)
                    self.assertFalse(db.in_transaction)
                    # No retained writer lock may survive either rollback path.
                    with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as observer:
                        observer.execute('BEGIN IMMEDIATE')
                        observer.execute('ROLLBACK')
                    self.assert_preserved()
                finally:
                    db.execute('DROP TRIGGER reject_insert')
        result = self.ledger.execute(wire(request))
        self.assertIs(result['replayed'], False)
        self.assertEqual(self.ledger.execute(wire(request)), {**result, 'replayed': True})

    def test_begin_failure_releases_lock_and_preserves_original_results(self):
        owner, attribute = connection_slot(self.ledger)
        real = getattr(owner, attribute)
        request, kwargs = self.calls[0]
        for after in (False, True):
            for interrupt in (False, True):
                with self.subTest(after=after, interrupt=interrupt):
                    fault = ConnectionFault(real, 'BEGIN IMMEDIATE', after=after, interrupt=interrupt)
                    try:
                        with patch.object(owner, attribute, fault):
                            with self.assertRaises(KeyboardInterrupt if interrupt else LedgerError) as caught:
                                self.ledger.execute(wire(request), **kwargs)
                        if not interrupt:
                            self.assertEqual((caught.exception.code, caught.exception.commit_state), ('IO_ERROR', 'not_committed'))
                        self.assertFalse(real.in_transaction)
                        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as observer:
                            observer.execute('BEGIN IMMEDIATE')
                            observer.execute('ROLLBACK')
                        # Reuse this handle, without close masking a leaked lock.
                        self.assert_preserved()
                        with open_ledger(self.path) as reopened:
                            assert_metadata(reopened, self.expected)
                    finally:
                        if real.in_transaction:
                            real.execute('ROLLBACK')

    def test_begin_busy_and_unconfirmed_cleanup_are_distinct(self):
        owner, attribute = connection_slot(self.ledger)
        real = getattr(owner, attribute)
        request, kwargs = self.calls[0]
        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            try:
                with self.assertRaises(LedgerError) as caught:
                    self.ledger.execute(wire(request), **kwargs)
                self.assertEqual((caught.exception.code, caught.exception.commit_state), ('BUSY', 'not_committed'))
                self.assertFalse(real.in_transaction)
            finally:
                writer.execute('ROLLBACK')
        self.assert_preserved()
        fault = ConnectionFault(real, 'BEGIN IMMEDIATE', after=True, rollback_fails=True)
        try:
            with patch.object(owner, attribute, fault):
                with self.assertRaises(LedgerError) as caught:
                    self.ledger.execute(wire(request), **kwargs)
            self.assertEqual((caught.exception.code, caught.exception.commit_state), ('DURABILITY_UNKNOWN', 'unknown'))
        finally:
            self.ledger.close()
            self.ledger = open_ledger(self.path)
            self.assert_preserved()

    def test_closed_handle_remains_uncommitted(self):
        self.ledger.close()
        request, kwargs = self.calls[0]
        try:
            with self.assertRaises(LedgerError) as caught:
                self.ledger.execute(wire(request), **kwargs)
            self.assertEqual((caught.exception.code, caught.exception.commit_state), ('IO_ERROR', 'not_committed'))
        finally:
            self.ledger = open_ledger(self.path)
            self.assert_preserved()

    def test_unconfirmed_new_write_rollback_remains_unknown(self):
        request = create(artifact='artifact:new', key='new')
        with self.fault(sqlite3.SQLITE_CORRUPT, rollback_fails=True):
            with self.assertRaises(LedgerError) as caught:
                self.ledger.execute(wire(request))
            self.assertEqual((caught.exception.code, caught.exception.commit_state), ('DURABILITY_UNKNOWN', 'unknown'))
        # The public state is checked after close/reopen before retrying.
        result = self.ledger.execute(wire(request))
        self.assertIs(result['replayed'], False)
        self.assertEqual(self.ledger.execute(wire(request)), {**result, 'replayed': True})

    def test_cli_native_integrity_error_exit_five_and_original_identity(self):
        request, _ = self.calls[0]
        request_path = self.path.parent / 'request.json'
        request_path.write_bytes(wire(request))
        program = '''import sqlite3, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[3])
from test_write_sqlite_errors import SQLiteFault, connection_slot
import infra_artifact_ledger as implementation
from infra_artifact_ledger import cli
print(implementation.__file__, file=sys.stderr)
opened = cli.open_ledger
def open_fault(path):
    ledger = opened(path)
    owner, attribute = connection_slot(ledger)
    setattr(owner, attribute, SQLiteFault(getattr(owner, attribute), sqlite3.SQLITE_CORRUPT, True))
    return ledger
with patch.object(cli, 'open_ledger', side_effect=open_fault):
    raise SystemExit(cli.main(['write', '--db', sys.argv[1], '--request', sys.argv[2]]))
'''
        result = subprocess.run([sys.executable, *(['-I'] if sys.flags.isolated else []),
            '-B', '-c', program, str(self.path), str(request_path), str(Path(__file__).parent)],
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertEqual(result.stderr.decode().strip(), implementation.__file__)
        envelope = json.loads(result.stdout)
        self.assertEqual(set(envelope), {'status', 'code', 'message', 'commit_state', 'details'})
        self.assertEqual(envelope['status'], 'ERROR')
        self.assert_committed(LedgerError(envelope['code'], envelope['message'],
            envelope['commit_state'], envelope['details']), request, 'INTEGRITY_FAILURE')
        self.assert_preserved()


if __name__ == '__main__':
    unittest.main()
