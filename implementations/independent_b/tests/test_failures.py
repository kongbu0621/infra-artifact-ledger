"""Fault injection supplements public checks; it does not assert physical crash durability."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from infra_artifact_ledger import cli
from test_public_contract import append, create, wire


class ConnectionFault:
    def __init__(self, real, statement, *, after=False, rollback_fails=False, interrupt=False):
        self.real, self.statement = real, statement
        self.after, self.rollback_fails, self.interrupt = after, rollback_fails, interrupt
        self.armed = True

    @property
    def in_transaction(self):
        return self.real.in_transaction

    def execute(self, sql, *args):
        if sql == 'ROLLBACK' and self.rollback_fails:
            raise OSError('injected rollback failure')
        if self.armed and sql.startswith(self.statement):
            self.armed = False
            if self.after:
                self.real.execute(sql, *args)
            if self.interrupt:
                raise KeyboardInterrupt()
            raise OSError('injected operation failure')
        return self.real.execute(sql, *args)

    def close(self):
        return self.real.close()


class FailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'database'
        self.ledger = initialize(self.path)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.ledger.close)

    def test_precommit_failure_rolls_back_owned_and_operation_rows(self):
        self.ledger._db = ConnectionFault(self.ledger._db, 'INSERT INTO b_operations')
        with self.assertRaises(LedgerError) as caught:
            self.ledger.execute(wire(create()))
        self.assertEqual((caught.exception.code, caught.exception.commit_state), ('IO_ERROR', 'not_committed'))
        self.assertEqual(self.ledger.verify()['counts']['artifacts'], 0)
        self.assertFalse(self.ledger.execute(wire(create()))['replayed'])

    def test_commit_acknowledgement_lost_is_unknown_then_queryable(self):
        self.ledger._db = ConnectionFault(self.ledger._db, 'COMMIT', after=True)
        with self.assertRaises(LedgerError) as caught:
            self.ledger.execute(wire(create()))
        self.assertEqual((caught.exception.code, caught.exception.commit_state), ('DURABILITY_UNKNOWN', 'unknown'))
        self.assertEqual(self.ledger.get_operation('demo:scope', 'create_artifact', 'create')['result']['artifact_id'], 'artifact:report')
        self.assertTrue(self.ledger.execute(wire(create()))['replayed'])

    def test_unconfirmed_rollback_stays_unknown(self):
        self.ledger._db = ConnectionFault(self.ledger._db, 'INSERT INTO b_operations', rollback_fails=True)
        with self.assertRaises(LedgerError) as caught:
            self.ledger.execute(wire(create()))
        self.assertEqual((caught.exception.code, caught.exception.commit_state), ('DURABILITY_UNKNOWN', 'unknown'))
        self.ledger.close()
        with open_ledger(self.path) as ledger:
            self.assertEqual(ledger.verify()['counts']['artifacts'], 0)

    def test_interruption_rolls_back_before_handle_reuse(self):
        self.ledger._db = ConnectionFault(self.ledger._db, 'INSERT INTO b_operations', interrupt=True)
        with self.assertRaises(KeyboardInterrupt):
            self.ledger.execute(wire(create()))
        self.assertEqual(self.ledger.verify()['counts']['artifacts'], 0)
        self.ledger.execute(wire(create()))

    def test_cli_cleanup_failure_preserves_known_commit_and_identity(self):
        request_path = self.path.parent/'request'
        request_path.write_bytes(wire(create()))
        args = cli.parser().parse_args(['write', '--db', str(self.path), '--request', str(request_path)])
        real_close = self.ledger.close
        def close_then_fail():
            real_close()
            raise LedgerError('IO_ERROR', 'injected close error')
        with patch.object(cli, 'open_ledger', return_value=self.ledger), patch.object(self.ledger, 'close', side_effect=close_then_fail):
            with self.assertRaises(LedgerError) as caught:
                cli.run(args)
        error = caught.exception
        self.assertEqual(error.commit_state, 'committed')
        self.assertEqual(error.details['idempotency_key'], 'create')
        self.assertEqual(error.details['result_ref'], 'artifact:report')
        with open_ledger(self.path) as ledger:
            self.assertEqual(ledger.verify()['counts']['idempotency_records'], 1)

    def test_oversized_or_nonblob_persisted_content_is_not_read_as_valid(self):
        self.ledger.execute(wire(create()))
        self.ledger.execute(wire(append(raw=b'a')), payloads={'blob:one': b'a'})
        for raw in ('a', b'aa', None):
            with sqlite3.connect(self.path) as db:
                db.execute("UPDATE b_objects SET content=? WHERE owned_id='blob:one'", (raw,))
            with self.assertRaises(LedgerError) as caught:
                self.ledger.read_blob('blob:one')
            self.assertEqual(caught.exception.code, 'INTEGRITY_FAILURE')
            self.assertEqual(self.ledger.get_record('blob', 'blob:one')['byte_length'], 1)

    def test_open_during_exclusive_writer_is_busy_not_corruption(self):
        db = sqlite3.connect(self.path, isolation_level=None)
        try:
            db.execute('BEGIN EXCLUSIVE')
            with self.assertRaises(LedgerError) as caught:
                open_ledger(self.path)
            self.assertEqual(caught.exception.code, 'BUSY')
        finally:
            db.close()
        with open_ledger(self.path) as ledger:
            self.assertEqual(ledger.verify()['counts']['artifacts'], 0)

    def test_foreign_journal_is_preserved_before_sqlite_open(self):
        foreign = self.path.parent/'foreign.sqlite'
        db = sqlite3.connect(foreign)
        self.addCleanup(db.close)
        db.execute('CREATE TABLE original (content BLOB)')
        db.commit()
        db.execute('INSERT INTO original VALUES (?)', (b'x'*100000,))
        journal = Path(str(foreign)+'-journal')
        self.assertTrue(journal.exists())
        before = (foreign.read_bytes(), journal.read_bytes())
        with self.assertRaises(LedgerError) as caught:
            open_ledger(foreign)
        self.assertEqual(caught.exception.code, 'UNSUPPORTED_VERSION')
        self.assertEqual((foreign.read_bytes(), journal.read_bytes()), before)
        db.rollback()

    def test_request_document_and_json_allocation_boundaries(self):
        raw = wire(create())
        for limit in (8*1048576-1, 8*1048576):
            accepted = raw + b' '*(limit-len(raw))
            self.ledger.execute(accepted)
        with self.assertRaises(LedgerError) as caught:
            self.ledger.execute(raw+b' '*(8*1048576+1-len(raw)))
        self.assertEqual(caught.exception.code, 'RESOURCE_LIMIT')
        # The <= limit arrays are invalid request shapes, but must not be called
        # node-limit violations. One root array plus N scalar nodes.
        for count, expected in ((499998, 'INVALID_INPUT'), (499999, 'INVALID_INPUT'), (500000, 'RESOURCE_LIMIT')):
            with self.assertRaises(LedgerError) as caught:
                self.ledger.execute(b'['+b','.join([b'null']*count)+b']')
            self.assertEqual(caught.exception.code, expected)


if __name__ == '__main__':
    unittest.main()
