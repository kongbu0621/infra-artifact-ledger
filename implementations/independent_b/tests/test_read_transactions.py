"""Public reads recover from BEGIN failures without taking another transaction.

The fault adapter executes real SQLite statements on new synthetic databases.
It models failures before/after their return, not physical crash durability.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from cross_implementation import assert_metadata, portable_metadata
from test_failures import ConnectionFault
from test_public_contract import append, create, wire


class ReadTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.sqlite'
        self.ledger = initialize(self.path)
        self.addCleanup(lambda: self.ledger.close())
        self.ledger.execute(wire(create()))
        self.ledger.execute(wire(append()), payloads={'blob:one': b'original\x00\xff'})
        self.before = self.path.read_bytes()
        self.expected = portable_metadata(self.ledger.export_bundle())

    def reads(self):
        return (
            ('get_record', lambda: self.ledger.get_record('artifact', 'artifact:report')),
            ('get_history', lambda: self.ledger.get_history('artifact:report')),
            ('get_operation', lambda: self.ledger.get_operation('demo:scope', 'append_version', 'append')),
            ('read_blob', lambda: self.ledger.read_blob('blob:one')),
            ('verify', self.ledger.verify),
            ('export_bundle', self.ledger.export_bundle),
        )

    def assert_preserved(self, ledger):
        self.assertEqual(self.path.read_bytes(), self.before)
        assert_metadata(ledger, self.expected)
        content = ledger.read_blob('blob:one')
        self.assertIs(type(content), bytes)
        self.assertEqual(content, b'original\x00\xff')

    def test_begin_failure_releases_owned_transaction_and_same_handle_continues(self):
        real = self.ledger._db
        for name, read in self.reads():
            for after in (False, True):
                for interrupt in (False, True):
                    with self.subTest(read=name, after=after, interrupt=interrupt):
                        fault = ConnectionFault(real, 'BEGIN', after=after, interrupt=interrupt)
                        try:
                            with patch.object(self.ledger, '_db', fault):
                                with self.assertRaises(KeyboardInterrupt if interrupt else LedgerError) as caught:
                                    read()
                            if not interrupt:
                                self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                                 ('IO_ERROR', 'not_applicable'))
                                self.assertEqual(str(caught.exception.__cause__), 'injected operation failure')
                            self.assertFalse(real.in_transaction)
                            # All records, history, operations and actual content
                            # must remain available on the original handle.
                            self.assert_preserved(self.ledger)
                            self.assertTrue(self.ledger.execute(wire(append()), payloads={})['replayed'])
                            self.assert_preserved(self.ledger)
                        finally:
                            if real.in_transaction:
                                real.rollback()
        with open_ledger(self.path) as reopened:
            self.assert_preserved(reopened)

    def test_begin_failure_does_not_rollback_preexisting_transaction(self):
        real = self.ledger._db
        real.execute('CREATE TEMP TABLE retained (value TEXT)')
        for name, read in self.reads():
            with self.subTest(read=name):
                real.execute('BEGIN IMMEDIATE')
                real.execute('INSERT INTO retained VALUES (?)', (name,))
                try:
                    with self.assertRaises(LedgerError) as caught:
                        read()
                    self.assertEqual(caught.exception.commit_state, 'not_applicable')
                    self.assertTrue(real.in_transaction)
                    self.assertEqual(real.execute('SELECT value FROM retained').fetchall(), [(name,)])
                finally:
                    real.rollback()
                self.assert_preserved(self.ledger)

    def test_begin_cleanup_failure_keeps_primary_and_close_allows_reopen(self):
        for name in (name for name, _ in self.reads()):
            for interrupt in (False, True):
                with self.subTest(read=name, interrupt=interrupt):
                    real = self.ledger._db
                    fault = ConnectionFault(real, 'BEGIN', after=True,
                                            rollback_fails=True, interrupt=interrupt)
                    read = dict(self.reads())[name]
                    with patch.object(self.ledger, '_db', fault):
                        with self.assertRaises(KeyboardInterrupt if interrupt else LedgerError) as caught:
                            read()
                    if not interrupt:
                        self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                         ('IO_ERROR', 'not_applicable'))
                        self.assertEqual(str(caught.exception.__cause__), 'injected operation failure')
                    self.assertTrue(real.in_transaction)
                    self.ledger.close()
                    self.ledger = open_ledger(self.path)
                    self.assert_preserved(self.ledger)


if __name__ == '__main__':
    unittest.main()
