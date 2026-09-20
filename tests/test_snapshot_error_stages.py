"""LOGIC_ONLY public error-stage regression with real local file operations."""
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, recovery
from infra_artifact_ledger import snapshot_storage as storage


class SnapshotErrorStageTests(unittest.TestCase):
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
