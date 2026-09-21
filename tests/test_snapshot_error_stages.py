"""LOGIC_ONLY public error-stage regression with real local file operations."""
import errno
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, recovery
from infra_artifact_ledger import snapshot_storage as storage


class SnapshotErrorStageTests(unittest.TestCase):
    def final_hash_read_failure(self, operation):
        with tempfile.TemporaryDirectory(prefix="a2-final-hash-stage-") as temporary, \
                patch.object(storage, "_classify", return_value=None):
            root = Path(temporary)
            source = root / "source.sqlite"
            with initialize(source):
                pass
            original_source = source.read_bytes()
            output, scratch = root / "output", root / "scratch"
            output.mkdir()
            scratch.mkdir()
            created = recovery.create(db=source, output_root=output,
                                      snapshot_id="a" * 32, source_commit="b" * 40)["data"]
            if operation == "create":
                target = output / ("c" * 32)
                invoke = lambda: recovery.create(db=source, output_root=output,
                                                  snapshot_id="c" * 32, source_commit="b" * 40)
            else:
                target = root / "restored"
                invoke = lambda: recovery.restore(snapshot=created["snapshot_path"], target_dir=target,
                                                   expected_manifest_sha256=created["manifest_sha256"],
                                                   scratch_parent=scratch)
            actual_read = os.read
            injected = []

            def final_read_fault(fd, length):
                location = Path(os.readlink(f"/proc/self/fd/{fd}"))
                if location == target / "ledger.sqlite" and not injected and (
                        operation == "restore" or (target / "COMMITTED.json").exists()):
                    injected.append(location)
                    raise OSError(errno.EIO, "synthetic final database hash read failure")
                return actual_read(fd, length)

            with patch.object(recovery.os, "read", final_read_fault), \
                    self.assertRaises(recovery.RecoveryError) as caught:
                invoke()
            self.assertEqual(injected, [target / "ledger.sqlite"])
            self.assertEqual(source.read_bytes(), original_source)
            self.assertEqual(hashlib.sha256((target / "ledger.sqlite").read_bytes()).hexdigest(),
                             created["database_sha256"])
            if operation == "create":
                self.assertEqual({entry.name for entry in target.iterdir()},
                                 {"ledger.sqlite", "manifest.json", "COMMITTED.json"})
                expected = hashlib.sha256((target / "manifest.json").read_bytes()).hexdigest()
                checked = recovery.verify(snapshot=target, expected_manifest_sha256=expected,
                                          scratch_parent=scratch)
            else:
                self.assertEqual({entry.name for entry in target.iterdir()}, {"ledger.sqlite"})
                checked = recovery.check_restore(target_dir=target,
                                                 expected_database_sha256=created["database_sha256"],
                                                 scratch_parent=scratch)
            self.assertEqual(checked["status"], "OK")
            self.assertEqual((caught.exception.code, caught.exception.stage,
                              caught.exception.publication_state),
                             ("PUBLICATION_UNKNOWN", "verify", "unknown"))

    def test_create_final_hash_read_failure_is_verify_after_publication(self):
        self.final_hash_read_failure("create")

    def test_restore_final_hash_read_failure_is_verify_after_publication(self):
        self.final_hash_read_failure("restore")

    def test_restore_final_file_sync_failure_is_sync_before_publication(self):
        with tempfile.TemporaryDirectory(prefix="a2-stage-") as temporary, \
                patch.object(storage, "_classify", return_value=None):
            root = Path(temporary)
            source = root / "source.sqlite"
            with initialize(source):
                pass
            output = root / "output"
            output.mkdir()
            created = recovery.create(db=source, output_root=output,
                                      snapshot_id="e" * 32, source_commit="d" * 40)["data"]
            archive = Path(created["snapshot_path"])
            before = {entry.name: entry.read_bytes() for entry in archive.iterdir()}
            target = root / "restored"
            original_fsync = os.fsync
            staged_syncs = []

            def fail_final_sync(descriptor):
                locator = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
                if locator.name == "ledger.sqlite" and locator.parent.parent == target:
                    staged_syncs.append(locator)
                    if len(staged_syncs) == 2:
                        raise OSError(errno.EIO, "synthetic final file fsync failure")
                return original_fsync(descriptor)

            with patch.object(recovery.os, "fsync", side_effect=fail_final_sync):
                with self.assertRaises(recovery.RecoveryError) as caught:
                    recovery.restore(snapshot=archive, target_dir=target,
                                     expected_manifest_sha256=created["manifest_sha256"],
                                     scratch_parent=root)
            self.assertEqual(len(staged_syncs), 2)
            self.assertEqual((caught.exception.code, caught.exception.stage,
                              caught.exception.publication_state),
                             ("IO_ERROR", "sync", "not_published"))
            self.assertFalse((target / "ledger.sqlite").exists())
            self.assertEqual(list(target.iterdir()), [])
            self.assertEqual(before, {entry.name: entry.read_bytes() for entry in archive.iterdir()})


if __name__ == "__main__":
    unittest.main()
