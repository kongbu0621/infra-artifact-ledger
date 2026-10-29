"""SQLite fault categories must survive public read and CLI boundaries."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from infra_artifact_ledger import storage
from test_public_contract import append, create, wire


def database_error(code):
    error = sqlite3.OperationalError('injected SQLite failure')
    error.sqlite_errorcode = code
    return error


CASES = (
    (sqlite3.SQLITE_IOERR_READ, 'IO_ERROR'),
    (sqlite3.SQLITE_IOERR_FSYNC, 'IO_ERROR'),
    (sqlite3.SQLITE_FULL, 'IO_ERROR'),
    (sqlite3.SQLITE_CANTOPEN, 'IO_ERROR'),
    (sqlite3.SQLITE_READONLY, 'IO_ERROR'),
    (sqlite3.SQLITE_PERM, 'IO_ERROR'),
    (sqlite3.SQLITE_BUSY, 'BUSY'),
    (sqlite3.SQLITE_LOCKED_SHAREDCACHE, 'BUSY'),
    (sqlite3.SQLITE_CORRUPT, 'INTEGRITY_FAILURE'),
    (sqlite3.SQLITE_NOTADB, 'INTEGRITY_FAILURE'),
    (sqlite3.SQLITE_ERROR, 'INTEGRITY_FAILURE'),
)


class ReadErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root/'ledger.sqlite'
        self.ledger = initialize(self.path)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.ledger.close)
        self.ledger.execute(wire(create()))
        self.ledger.execute(wire(append()), payloads={'blob:one': b'original\x00\xff'})

    def test_read_failures_preserve_category_and_handle_can_continue(self):
        real = self.ledger._db
        before = self.path.read_bytes()
        reads = (
            lambda: self.ledger.get_record('artifact', 'artifact:report'),
            lambda: self.ledger.get_history('artifact:report'),
            lambda: self.ledger.get_operation('demo:scope', 'append_version', 'append'),
            lambda: self.ledger.read_blob('blob:one'),
            self.ledger.verify,
            self.ledger.export_bundle,
        )
        for code, expected in CASES:
            for statement in ('BEGIN', 'SELECT'):
                for index, read in enumerate(reads):
                    with self.subTest(code=code, statement=statement, read=index):
                        class Fault:
                            def __getattr__(self, name):
                                return getattr(real, name)

                            def execute(self, sql, *args):
                                if sql.startswith(statement):
                                    raise database_error(code)
                                return real.execute(sql, *args)
                        with patch.object(self.ledger, '_db', Fault()):
                            with self.assertRaises(LedgerError) as caught:
                                read()
                        self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                         (expected, 'not_applicable'))
                        self.assertFalse(real.in_transaction)
                        self.assertEqual(self.path.read_bytes(), before)
                        self.assertEqual(self.ledger.read_blob('blob:one'), b'original\x00\xff')
        self.assertEqual(self.ledger.verify()['counts']['idempotency_records'], 2)

    def test_open_failures_preserve_category_and_existing_database(self):
        before = self.path.read_bytes()
        for code, expected in CASES:
            with self.subTest(code=code):
                with patch.object(storage.sqlite3, 'connect', side_effect=database_error(code)):
                    with self.assertRaises(LedgerError) as caught:
                        open_ledger(self.path)
                self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                 (expected, 'not_applicable'))
                self.assertEqual(self.path.read_bytes(), before)
                with open_ledger(self.path) as reopened:
                    self.assertEqual(reopened.verify()['counts']['idempotency_records'], 2)

    def test_cli_read_error_envelope_and_exit_codes(self):
        before = self.path.read_bytes()
        program = '''import sqlite3, sys
from unittest.mock import patch
from infra_artifact_ledger import cli, storage
error = sqlite3.OperationalError('injected SQLite failure')
error.sqlite_errorcode = int(sys.argv[2])
with patch.object(storage.sqlite3, 'connect', side_effect=error):
    code = cli.main(['verify', '--db', sys.argv[1]])
sys.exit(code)
'''
        for code, expected, exit_code in ((sqlite3.SQLITE_IOERR_READ, 'IO_ERROR', 1),
                                          (sqlite3.SQLITE_BUSY, 'BUSY', 6),
                                          (sqlite3.SQLITE_CORRUPT, 'INTEGRITY_FAILURE', 5)):
            with self.subTest(code=code):
                argv = [sys.executable, *(['-I'] if sys.flags.isolated else []),
                        '-c', program, str(self.path), str(code)]
                result = subprocess.run(argv, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, exit_code, result.stderr)
                self.assertEqual(result.stderr, b'')
                self.assertEqual(result.stdout.count(b'\n'), 1)
                response = json.loads(result.stdout)
                self.assertEqual((response['status'], response['code'], response['commit_state']),
                                 ('ERROR', expected, 'not_applicable'))
                self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
