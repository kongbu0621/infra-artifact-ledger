"""Public database paths follow the host filesystem's encoding boundary."""

import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger

from acceptance_helpers import ARTIFACT, create, encode


class LocalPath:
    def __init__(self, value):
        self.value = value

    def __fspath__(self):
        return self.value


class LibraryPathTests(unittest.TestCase):
    def test_unencodable_path_is_invalid_before_sqlite_or_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = str(root / "unencodable-\ud800.sqlite")
            for operation in (initialize, open_ledger):
                for path in (invalid, Path(invalid), LocalPath(invalid)):
                    with self.subTest(operation=operation.__name__, path_type=type(path).__name__):
                        with patch("infra_artifact_ledger.sqlite_store.sqlite3.connect",
                                   wraps=sqlite3.connect) as connect, \
                                patch("infra_artifact_ledger.sqlite_store.tempfile.TemporaryDirectory",
                                      wraps=tempfile.TemporaryDirectory) as staging:
                            with self.assertRaises(LedgerError) as failure:
                                operation(path)
                            self.assertEqual((failure.exception.code, failure.exception.commit_state),
                                             ("INVALID_INPUT", "not_applicable"))
                            connect.assert_not_called()
                            staging.assert_not_called()
                        self.assertEqual(list(root.iterdir()), [])

    def test_unicode_spaces_and_uri_punctuation_remain_literal_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, wrap in enumerate((str, Path, LocalPath)):
                path = Path(directory) / f"报告 ledger ?#{index}.sqlite"
                with self.subTest(path_type=wrap.__name__):
                    with initialize(wrap(str(path))) as ledger:
                        ledger.execute(encode(create()))
                    with open_ledger(wrap(str(path))) as ledger:
                        self.assertEqual(ledger.get_record("artifact", ARTIFACT)["artifact_id"], ARTIFACT)
                        self.assertEqual(ledger.verify()["counts"]["artifacts"], 1)
                    self.assertTrue(path.is_file())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux surrogateescape filename case")
    def test_filesystem_representable_surrogateescape_path_roundtrips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local-\udcff.sqlite"
            self.assertIn(b"\xff", os.fsencode(path))
            with initialize(path) as ledger:
                ledger.execute(encode(create()))
            with open_ledger(path) as ledger:
                self.assertEqual(ledger.get_record("artifact", ARTIFACT)["artifact_id"], ARTIFACT)
                self.assertEqual(ledger.verify()["counts"]["artifacts"], 1)
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
