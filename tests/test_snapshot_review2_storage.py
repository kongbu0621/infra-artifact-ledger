"""Round-two Linux host-path regressions; mount classification is LOGIC_ONLY."""
import errno
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, recovery, snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux path semantics")
class DoubleLeadingSlashTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="a2-review2-path-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "output"
        self.scratch = self.root / "scratch"
        self.output.mkdir()
        self.scratch.mkdir()
        self.source = self.root / "source.sqlite"
        with initialize(self.source):
            pass
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            mount_id = storage._fd_mount_id(fd)
        finally:
            os.close(fd)
        mounts = patch.object(storage, "_mounts", return_value=(storage.Mount(
            mount_id, 1, "0:1", "/", "/", "ext4", "synthetic-logic-only"),))
        mounts.start()
        self.addCleanup(mounts.stop)

    def double(self, path):
        return "/" + str(path)

    def test_same_linux_directory_has_same_mount_binding(self):
        alternate = self.double(self.output)
        self.assertTrue(os.path.samefile(self.output, alternate))
        with storage.open_directory(self.output, budget=Budget()) as ordinary, \
                storage.open_directory(alternate, budget=Budget()) as doubled:
            self.assertEqual(doubled.identity, ordinary.identity)
            self.assertEqual(doubled.path, ordinary.path)
            doubled.check()

    def test_double_slash_source_output_snapshot_scratch_and_target_lifecycle(self):
        before = self.source.read_bytes()
        created = recovery.create(
            db=self.double(self.source), output_root=self.double(self.output),
            snapshot_id="a" * 32, source_commit="b" * 40)["data"]
        snapshot = self.double(Path(created["snapshot_path"]))
        arguments = dict(snapshot=snapshot,
                         expected_manifest_sha256=created["manifest_sha256"],
                         scratch_parent=self.double(self.scratch))
        self.assertEqual(recovery.verify(**arguments)["status"], "OK")
        target = self.root / "restored"
        restored = recovery.restore(target_dir=self.double(target), **arguments)
        self.assertEqual(restored["status"], "OK")
        checked = recovery.check_restore(
            target_dir=self.double(target), scratch_parent=self.double(self.scratch),
            expected_database_sha256=created["database_sha256"])
        self.assertEqual(checked["status"], "OK")
        self.assertTrue(os.path.samefile(restored["data"]["database_path"],
                                        target / "ledger.sqlite"))
        archive = self.root / "archive"
        archive.mkdir()
        config = {"format": "infra-artifact-ledger-storage/v1", "profile": "mounted-posix-v1",
                  "storage_ref": "synthetic-logic-only", "mount_point": str(self.root),
                  "mount_root": "/", "mount_source": "synthetic:/unused", "fs_type": "nfs4",
                  "archive_root": str(archive)}
        # Only exercise the public publish path here; this synthetic endpoint
        # is not evidence of an actual network mount or its durability.
        with patch.object(storage, "_classify", return_value=None):
            published = recovery.publish(storage_config=config, **arguments)
        self.assertEqual(published["status"], "OK")
        self.assertEqual(Path(published["data"]["snapshot_path"]).parent, archive)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_root_spelling_does_not_erase_symlink_or_missing_parent_components(self):
        step = self.root / "step"
        step.mkdir()
        (self.root / "alias").symlink_to(step, target_is_directory=True)
        for component, expected in (("alias", "UNSUPPORTED_STORAGE"),
                                    ("missing", "NOT_FOUND")):
            with self.subTest(component=component), self.assertRaises(RecoveryError) as caught:
                storage.open_directory(
                    self.double(self.root / component / ".." / "output"), budget=Budget())
            self.assertEqual(caught.exception.code, expected)
        self.assertEqual(list(self.output.iterdir()), [])

    def close_then_fail_for(self, inode, faults):
        original = os.close

        def close(fd):
            matches = os.fstat(fd).st_ino == inode
            original(fd)
            if matches and not faults:
                faults.append(fd)
                raise OSError(errno.EIO, "injected close failure after releasing descriptor")

        return close

    def test_unsupported_mount_keeps_primary_error_when_owned_fd_close_fails(self):
        faults = []
        mount = storage._mounts()[0]
        unsupported = storage.Mount(mount.mount_id, mount.parent_id, mount.device,
                                    mount.root, mount.point, "tmpfs", mount.source)
        with patch.object(storage, "_mounts", return_value=(unsupported,)), \
                patch.object(storage.os, "close", self.close_then_fail_for(self.output.stat().st_ino, faults)):
            with self.assertRaises(RecoveryError) as caught:
                storage.open_directory(self.output, budget=Budget())
        self.assertEqual(caught.exception.code, "UNSUPPORTED_STORAGE")
        self.assertEqual(len(faults), 1)

    def test_invalid_member_keeps_primary_error_when_owned_fd_close_fails(self):
        data = self.output / "data"
        data.write_bytes(b"bytes")
        os.link(data, self.output / "alias")
        faults = []
        with storage.open_directory(self.output, budget=Budget()) as directory:
            with patch.object(storage.os, "close", self.close_then_fail_for(data.stat().st_ino, faults)):
                with self.assertRaises(RecoveryError) as caught:
                    directory.open_file("data", single_link=True)
        self.assertEqual(caught.exception.code, "INTEGRITY_FAILURE")
        self.assertEqual(len(faults), 1)

    def test_copy_digest_failure_survives_destination_close_failure(self):
        (self.output / "source").write_bytes(b"source")
        faults, output_fds = [], []
        original_create, original_close = storage._exclusive_file, os.close

        def create(*args, **kwargs):
            fd = original_create(*args, **kwargs)
            output_fds.append(fd)
            return fd

        def close(fd):
            original_close(fd)
            if output_fds and fd == output_fds[0] and not faults:
                faults.append(fd)
                raise OSError(errno.EIO, "injected close failure after releasing descriptor")

        with storage.open_directory(self.output, budget=Budget()) as directory:
            source = directory.open_file("source")
            try:
                with patch.object(storage, "_exclusive_file", side_effect=create), \
                        patch.object(storage.os, "close", side_effect=close):
                    with self.assertRaises(RecoveryError) as caught:
                        storage.copy_file(source, directory, "copy", Budget(), expected_hash="0" * 64)
                self.assertEqual(caught.exception.code, "INTEGRITY_FAILURE")
                self.assertEqual(len(faults), 1)
            finally:
                os.close(source)
        self.assertEqual((self.output / "source").read_bytes(), b"source")


if __name__ == "__main__":
    unittest.main()
