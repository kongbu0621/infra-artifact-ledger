"""Reject invalid source journals before any source SQLite connection.

The connection boundary is blocked even on the old implementation: these tests
check preventive validation and never exercise SQLite against a linked journal.
Filesystem classification alone is mocked (LOGIC_ONLY).
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, recovery
from infra_artifact_ledger import snapshot_sqlite as snapshot
from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError


class SourceJournalTests(unittest.TestCase):
    def test_create_rejects_invalid_journal_before_header_or_sqlite_open(self):
        for kind in ("hardlink", "symlink", "dangling", "directory", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, output = root / "source.sqlite", root / "output"
                output.mkdir()
                with initialize(source):
                    pass
                original = source.read_bytes()
                journal = Path(str(source) + "-journal")
                if kind == "hardlink":
                    os.link(source, journal)
                elif kind in ("symlink", "dangling"):
                    journal.symlink_to(source if kind == "symlink" else root / "absent")
                elif kind == "directory":
                    journal.mkdir()
                else:
                    os.mkfifo(journal)
                before = journal.lstat()
                blocked = RecoveryError("IO_ERROR", "Test blocks the source-open boundary.")
                with patch.object(storage, "_classify", return_value=None), \
                        patch.object(snapshot, "_read_header", side_effect=blocked) as header, \
                        patch.object(snapshot, "_connect_readonly", side_effect=blocked) as connect:
                    with self.assertRaises(RecoveryError) as caught:
                        recovery.create(db=source, output_root=output,
                                        snapshot_id="a" * 32, source_commit="b" * 40)
                self.assertEqual((caught.exception.code, caught.exception.stage,
                                  caught.exception.publication_state),
                                 ("INTEGRITY_FAILURE", "validate", "not_published"))
                header.assert_not_called()
                connect.assert_not_called()
                self.assertEqual(journal.lstat(), before)
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(list(output.iterdir()), [])

    def test_cached_preflight_does_not_bypass_journal_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "source.sqlite", root / "target.sqlite"
            with initialize(source):
                pass
            budget = Budget()
            binding = snapshot.preflight_source(source, budget)
            # An entry present at the connection boundary must still be checked.
            # No SQLite connection is permitted against this input in this test.
            journal = Path(str(source) + "-journal")
            journal.symlink_to(root / "absent")
            with patch.object(snapshot, "_connect_readonly", side_effect=RecoveryError(
                    "IO_ERROR", "Test blocks the source-open boundary.")) as connect:
                with self.assertRaises(RecoveryError) as caught:
                    snapshot.snapshot_database(source, target, budget, source_binding=binding)
            self.assertEqual((caught.exception.code, caught.exception.stage),
                             ("INTEGRITY_FAILURE", "snapshot"))
            connect.assert_not_called()
            self.assertTrue(journal.is_symlink())
            self.assertFalse(target.exists())

    def test_regular_single_link_journal_remains_eligible_for_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.sqlite"
            with initialize(source):
                pass
            journal = Path(str(source) + "-journal")
            journal.write_bytes(b"\0" * 512)
            before = journal.lstat()
            binding = snapshot.preflight_source(source, Budget())
            self.assertEqual(binding[0][-1], (source.stat().st_dev, source.stat().st_ino))
            self.assertEqual(journal.lstat(), before)
            self.assertEqual(journal.read_bytes(), b"\0" * 512)


if __name__ == "__main__":
    unittest.main()
