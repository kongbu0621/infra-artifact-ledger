"""CLI outputs must not occupy the current database's SQLite sidecar paths."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from infra_artifact_ledger import initialize
from acceptance_helpers import append, create, encode


class CLIOutputPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "ledger.sqlite"
        self.payload = b"Quarterly report v1\n"
        request, payloads = append(payload=self.payload)
        self.blob_ref = next(iter(payloads))
        with initialize(self.database) as ledger:
            ledger.execute(encode(create()))
            ledger.execute(encode(request), payloads=payloads)
        self.original = self.database.read_bytes()
        self.environment = os.environ.copy()
        self.environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    def run_cli(self, *args, db=None, exit_code=0):
        response = subprocess.run(
            [sys.executable, "-m", "infra_artifact_ledger", *map(str, args),
             "--db", str(self.database if db is None else db)],
            cwd=self.root, env=self.environment, capture_output=True, timeout=20,
        )
        self.assertEqual(response.returncode, exit_code, response.stdout + response.stderr)
        self.assertEqual(response.stderr, b"")
        self.assertEqual(response.stdout.count(b"\n"), 1)
        return json.loads(response.stdout)

    def assert_rejected(self, *args, db=None):
        result = self.run_cli(*args, db=db, exit_code=2)
        self.assertEqual((result["code"], result["commit_state"]),
                         ("INVALID_INPUT", "not_applicable"))
        self.assertEqual(self.database.read_bytes(), self.original)

    def test_blob_output_rejects_each_reserved_sidecar_before_open(self):
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                output = Path(str(self.database) + suffix)
                self.assert_rejected("read-blob", "--blob-ref", self.blob_ref, "--output", output)
                self.assertFalse(output.exists())
        self.run_cli("verify")

    def test_export_preflights_both_outputs_without_partial_publication(self):
        for suffix in ("-journal", "-wal", "-shm"):
            for option in ("package", "descriptor"):
                with self.subTest(suffix=suffix, option=option):
                    reserved = Path(str(self.database) + suffix)
                    other = self.root / "other.json"
                    outputs = {"package": other, "descriptor": other, option: reserved}
                    self.assert_rejected("export", "--package", outputs["package"],
                                         "--descriptor", outputs["descriptor"])
                    self.assertFalse(reserved.exists())
                    self.assertFalse(other.exists())
        self.run_cli("verify")

    def test_relative_and_symlink_aliases_cannot_name_reserved_outputs(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        (self.root / "nested").mkdir()
        db_alias = self.root / "alias.sqlite"
        db_alias.symlink_to(self.database)
        cases = [("ledger.sqlite", "nested/../ledger.sqlite-journal"),
                 ("ledger.sqlite", "alias/ledger.sqlite-wal"),
                 ("alias/ledger.sqlite", "ledger.sqlite-shm"),
                 (db_alias, "ledger.sqlite-journal"),
                 (db_alias, "alias.sqlite-journal")]
        for database, output in cases:
            with self.subTest(database=database, output=output):
                self.assert_rejected("read-blob", "--blob-ref", self.blob_ref,
                                     "--output", output, db=database)
                self.assertFalse((self.root / output).exists())

    def test_similarly_named_normal_output_survives_later_database_operations(self):
        output = self.root / "ledger.sqlite-journal.txt"
        result = self.run_cli("read-blob", "--blob-ref", self.blob_ref, "--output", output)
        self.assertEqual(result["status"], "OK")
        self.run_cli("verify")
        self.assertEqual(output.read_bytes(), self.payload)
        self.assertEqual(self.database.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
