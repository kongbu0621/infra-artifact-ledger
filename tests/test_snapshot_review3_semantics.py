"""LOGIC_ONLY regressions for primary errors and owned recovery file handles.

The filesystem classifier is mocked; file contents, descriptors, SQLite and
close operations are real. A close fault is injected only after actual close.
"""
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, recovery
from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError


class SnapshotReview3SemanticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="a2-review3-semantics-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output, self.scratch = self.root / "output", self.root / "scratch"
        self.output.mkdir()
        self.scratch.mkdir()
        classification = patch.object(storage, "_classify", return_value=None)
        classification.start()
        self.addCleanup(classification.stop)
        self.source = self.root / "source.sqlite"
        with initialize(self.source):
            pass
        self.created = recovery.create(db=self.source, output_root=self.output,
                                       snapshot_id="a" * 32, source_commit="b" * 40)["data"]
        self.snapshot = Path(self.created["snapshot_path"])

    @contextmanager
    def close_fault(self, file_path):
        """Release the selected real fd, then emulate an I/O error from close."""
        info = file_path.stat()
        identity = info.st_dev, info.st_ino
        actual_close = os.close
        released = []

        def fail_once(fd):
            current = os.fstat(fd)
            matches = (current.st_dev, current.st_ino) == identity and not released
            actual_close(fd)
            if matches:
                released.append(fd)
                raise OSError(errno.EIO, "synthetic close failure after actual release")

        with patch.object(recovery.os, "close", fail_once):
            yield released
        self.assertEqual(len(released), 1)

    def corrupt_snapshot(self):
        database = self.snapshot / "ledger.sqlite"
        original = database.read_bytes()
        database.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        return database

    def test_verify_digest_failure_survives_source_close_failure(self):
        database = self.corrupt_snapshot()
        before = database.read_bytes()
        with self.close_fault(database), self.assertRaises(RecoveryError) as caught:
            recovery.verify(snapshot=self.snapshot,
                            expected_manifest_sha256=self.created["manifest_sha256"],
                            scratch_parent=self.scratch)
        self.assertEqual((caught.exception.code, caught.exception.stage,
                          caught.exception.publication_state),
                         ("INTEGRITY_FAILURE", "copy", "not_applicable"))
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_restore_digest_failure_survives_source_close_failure(self):
        database = self.corrupt_snapshot()
        target = self.root / "restored"
        with self.close_fault(database), self.assertRaises(RecoveryError) as caught:
            recovery.restore(snapshot=self.snapshot, target_dir=target,
                             expected_manifest_sha256=self.created["manifest_sha256"],
                             scratch_parent=self.scratch)
        self.assertEqual((caught.exception.code, caught.exception.publication_state),
                         ("INTEGRITY_FAILURE", "not_published"))
        self.assertFalse(target.exists())
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_check_restore_hash_failure_survives_target_close_failure(self):
        target = self.root / "restored"
        recovery.restore(snapshot=self.snapshot, target_dir=target,
                         expected_manifest_sha256=self.created["manifest_sha256"],
                         scratch_parent=self.scratch)
        database = target / "ledger.sqlite"
        before = database.read_bytes()
        with self.close_fault(database), self.assertRaises(RecoveryError) as caught:
            recovery.check_restore(target_dir=target, expected_database_sha256="0" * 64,
                                   scratch_parent=self.scratch)
        self.assertEqual((caught.exception.code, caught.exception.publication_state),
                         ("INTEGRITY_FAILURE", "not_applicable"))
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual({p.name for p in target.iterdir()}, {"ledger.sqlite"})

    def test_database_budget_failure_survives_its_close_failure(self):
        folder = self.root / "empty-database"
        folder.mkdir()
        database = folder / "ledger.sqlite"
        database.touch()
        with storage.open_directory(folder, budget=Budget()) as directory:
            with self.close_fault(database), self.assertRaises(RecoveryError) as caught:
                recovery._database_info(directory, Budget())
        self.assertEqual((caught.exception.code, caught.exception.stage),
                         ("RESOURCE_LIMIT", "verify"))
        self.assertEqual(database.read_bytes(), b"")

    def test_archive_copy_failure_survives_its_source_close_failure(self):
        destination = self.root / "copy-target"
        destination.mkdir()
        database = self.snapshot / "ledger.sqlite"
        budget = Budget()
        with storage.open_directory(self.snapshot, budget=budget) as source, \
                storage.open_directory(destination, budget=budget) as target:
            context = recovery._Operation("create", budget)
            with self.close_fault(database), self.assertRaises(RecoveryError) as caught:
                recovery._copy_database(source, target,
                                        {"byte_length": database.stat().st_size,
                                         "sha256": "0" * 64}, context)
        self.assertEqual((caught.exception.code, caught.exception.stage),
                         ("INTEGRITY_FAILURE", "copy"))
        self.assertFalse((destination / "COMMITTED.json").exists())

    def test_restore_sync_primary_survives_final_file_close_failure(self):
        target = self.root / "restored"
        actual_fsync, actual_close = os.fsync, os.close
        injected, released = [], []
        primary = OSError(errno.ENOSPC, "synthetic fsync out of space")

        def fsync_fault(fd):
            location = os.readlink(f"/proc/self/fd/{fd}")
            if location.startswith(str(target) + "/.snapshot-tmp-") and \
                    location.endswith("/ledger.sqlite") and \
                    fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
                injected.append(fd)
                raise primary
            return actual_fsync(fd)

        def close_fault(fd):
            actual_close(fd)
            if injected and fd == injected[0] and not released:
                released.append(fd)
                raise OSError(errno.EIO, "synthetic secondary close error")

        with patch.object(recovery.os, "fsync", fsync_fault), \
                patch.object(recovery.os, "close", close_fault), \
                self.assertRaises(RecoveryError) as caught:
            recovery.restore(snapshot=self.snapshot, target_dir=target,
                             expected_manifest_sha256=self.created["manifest_sha256"],
                             scratch_parent=self.scratch)
        self.assertEqual(len(injected), 1)
        self.assertEqual(released, injected)
        self.assertIs(caught.exception.__cause__, primary)
        self.assertEqual((caught.exception.code, caught.exception.stage,
                          caught.exception.publication_state), ("IO_ERROR", "sync", "not_published"))
        self.assertFalse((target / "ledger.sqlite").exists())

    def test_handled_caller_error_does_not_hide_successful_read_close_failure(self):
        database = self.snapshot / "ledger.sqlite"
        try:
            raise ValueError("unrelated handled caller exception")
        except ValueError:
            with self.close_fault(database), self.assertRaises(RecoveryError) as caught:
                recovery.verify(snapshot=self.snapshot,
                                expected_manifest_sha256=self.created["manifest_sha256"],
                                scratch_parent=self.scratch)
        self.assertEqual((caught.exception.code, caught.exception.publication_state),
                         ("IO_ERROR", "not_applicable"))

    def test_source_close_failure_after_restore_publication_stays_unknown(self):
        database = self.snapshot / "ledger.sqlite"
        target = self.root / "restored"
        with self.close_fault(database), self.assertRaises(RecoveryError) as caught:
            recovery.restore(snapshot=self.snapshot, target_dir=target,
                             expected_manifest_sha256=self.created["manifest_sha256"],
                             scratch_parent=self.scratch)
        self.assertEqual((caught.exception.code, caught.exception.publication_state),
                         ("PUBLICATION_UNKNOWN", "unknown"))
        self.assertEqual({p.name for p in target.iterdir()}, {"ledger.sqlite"})
        self.assertEqual(hashlib.sha256((target / "ledger.sqlite").read_bytes()).hexdigest(),
                         self.created["database_sha256"])


if __name__ == "__main__":
    unittest.main()
