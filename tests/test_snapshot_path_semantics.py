"""Original path components are checked before lexical parent normalization.

Mount classification is synthetic. These tests exercise path/operation logic,
not supported-filesystem durability or a real NAS.
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, recovery, snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError, path


class SnapshotPathSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a2-path-semantics-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.step = self.root / "step"
        self.step.mkdir()
        self.archives = self.root / "archives"
        self.archives.mkdir()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.db = self.root / "source.sqlite"
        with initialize(self.db):
            pass
        (self.root / "alias").symlink_to(self.step, target_is_directory=True)
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.mount_id = storage._fd_mount_id(fd)
        finally:
            os.close(fd)
        self.mount = storage.Mount(self.mount_id, 1, "0:1", "/", "/", "ext4", "synthetic")
        self.mounts = patch.object(storage, "_mounts", return_value=(self.mount,))
        self.mounts.start()
        self.addCleanup(self.mounts.stop)

    def create(self, **overrides):
        arguments = dict(db=self.db, output_root=self.archives,
                         snapshot_id="a" * 32, source_commit="b" * 40)
        arguments.update(overrides)
        return recovery.create(**arguments)

    def assert_error(self, code, callback):
        with self.assertRaises(RecoveryError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_path_keeps_parent_components_until_descriptor_walk(self):
        raw = self.root / "alias" / ".." / "archives"
        self.assertEqual(path(raw), raw)
        self.assertIn("..", path(raw).parts)

    def test_real_parent_component_walks_then_returns_canonical_binding(self):
        raw = self.step / ".." / "archives"
        with storage.open_directory(raw, budget=Budget()) as directory:
            self.assertEqual(directory.path, self.archives)
            self.assertEqual(directory.identity,
                             (self.archives.stat().st_dev, self.archives.stat().st_ino))
            directory.check()

    def test_original_parent_traversal_is_rechecked_after_initial_open(self):
        other = self.root / "other"
        other.mkdir()
        (other / "step").mkdir()
        other_archives = other / "archives"
        other_archives.mkdir()
        (other_archives / "unrelated").write_bytes(b"keep")
        with storage.open_directory(self.step / ".." / "archives", budget=Budget()) as directory:
            self.step.rmdir()
            self.step.symlink_to(other / "step", target_is_directory=True)
            self.assert_error("UNSUPPORTED_STORAGE", directory.check)
            self.assert_error("UNSUPPORTED_STORAGE", lambda: directory.mkdir("must-not-exist"))
        self.assertEqual(list(self.archives.iterdir()), [])
        self.assertEqual({p.name for p in other_archives.iterdir()}, {"unrelated"})
        self.assertEqual((other_archives / "unrelated").read_bytes(), b"keep")

    def test_child_rechecks_original_parent_traversal_before_writing(self):
        with storage.open_directory(self.step / ".." / "archives", budget=Budget()) as directory:
            with directory.mkdir("child") as child:
                self.step.rmdir()
                self.assert_error("NOT_FOUND", lambda: storage.write_file(child, "must-not-exist", b"x", Budget()))
                self.assertEqual(list(child.path.iterdir()), [])

    def test_symlink_missing_and_regular_components_cannot_be_erased(self):
        before = self.db.read_bytes()
        for name, code in (("alias", "UNSUPPORTED_STORAGE"),
                           ("missing", "NOT_FOUND"),
                           ("source.sqlite", "UNSUPPORTED_STORAGE")):
            with self.subTest(component=name):
                self.assert_error(code, lambda: storage.open_directory(
                    self.root / name / ".." / "archives", budget=Budget()))
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(list(self.archives.iterdir()), [])

    def test_create_accepts_ordinary_parent_paths_without_changing_source(self):
        before = self.db.read_bytes()
        result = self.create(db=self.step / ".." / "source.sqlite",
                             output_root=self.step / ".." / "archives")
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["data"]["snapshot_path"], str(self.archives / ("a" * 32)))
        self.assertEqual(self.db.read_bytes(), before)

    def test_create_rejects_hidden_symlink_source_and_output_before_writes(self):
        before = self.db.read_bytes()
        for overrides in ({"db": self.root / "alias" / ".." / "source.sqlite"},
                          {"output_root": self.root / "alias" / ".." / "archives"}):
            with self.subTest(overrides=overrides):
                self.assert_error("UNSUPPORTED_STORAGE", lambda: self.create(**overrides))
                self.assertEqual(list(self.archives.iterdir()), [])
                self.assertEqual(self.db.read_bytes(), before)

    def test_verify_and_restore_keep_normal_parent_paths(self):
        result = self.create()["data"]
        snapshot = self.step / ".." / "archives" / ("a" * 32)
        arguments = dict(snapshot=snapshot, expected_manifest_sha256=result["manifest_sha256"],
                         scratch_parent=self.step / ".." / "scratch")
        self.assertEqual(recovery.verify(**arguments)["status"], "OK")
        target = self.step / ".." / "restored"
        restored = recovery.restore(target_dir=target, **arguments)
        self.assertEqual(restored["status"], "OK")
        self.assertEqual(recovery.check_restore(
            target_dir=target, expected_database_sha256=result["database_sha256"],
            scratch_parent=self.scratch)["status"], "OK")

    def test_verify_restore_and_check_reject_hidden_symlink_paths(self):
        result = self.create()["data"]
        snapshot = Path(result["snapshot_path"])
        original = {entry.name: entry.read_bytes() for entry in snapshot.iterdir()}
        arguments = dict(snapshot=snapshot, expected_manifest_sha256=result["manifest_sha256"],
                         scratch_parent=self.scratch)
        bad_snapshot = self.root / "alias" / ".." / "archives" / ("a" * 32)
        self.assert_error("UNSUPPORTED_STORAGE", lambda: recovery.verify(
            **{**arguments, "snapshot": bad_snapshot}))
        self.assert_error("UNSUPPORTED_STORAGE", lambda: recovery.restore(
            target_dir=self.root / "alias" / ".." / "restored", **arguments))
        self.assertFalse((self.root / "restored").exists())
        self.assert_error("UNSUPPORTED_STORAGE", lambda: recovery.check_restore(
            target_dir=self.root / "alias" / ".." / "archives" / ("a" * 32),
            expected_database_sha256=result["database_sha256"], scratch_parent=self.scratch))
        self.assertEqual({entry.name: entry.read_bytes() for entry in snapshot.iterdir()}, original)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_check_restore_rejects_scratch_hidden_inside_target(self):
        result = self.create()["data"]
        target = self.root / "restored"
        recovery.restore(snapshot=result["snapshot_path"], target_dir=target,
                         expected_manifest_sha256=result["manifest_sha256"], scratch_parent=self.scratch)
        nested = target / "unused-scratch"
        nested.mkdir()
        before = (target / "ledger.sqlite").read_bytes()
        self.assert_error("INVALID_INPUT", lambda: recovery.check_restore(
            target_dir=self.step / ".." / "restored",
            expected_database_sha256=result["database_sha256"], scratch_parent=nested))
        self.assertEqual(list(nested.iterdir()), [])
        self.assertEqual((target / "ledger.sqlite").read_bytes(), before)

    def test_configured_archive_parent_components_are_walked_before_normalizing(self):
        network = storage.Mount(self.mount_id, 1, "0:1", "/export", str(self.root),
                                "nfs4", "synthetic:/export")
        config = {"mount_point": str(self.root), "mount_root": "/export", "fs_type": "nfs4",
                  "mount_source": "synthetic:/export", "archive_root": str(self.step / ".." / "archives")}
        with patch.object(storage, "_mounts", return_value=(network,)):
            with storage.open_directory(self.archives, budget=Budget(), storage_config=config) as directory:
                self.assertEqual(directory.path, self.archives)
                directory.check()
            config["archive_root"] = str(self.root / "alias" / ".." / "archives")
            self.assert_error("UNSUPPORTED_STORAGE", lambda: storage.open_directory(
                self.archives, budget=Budget(), storage_config=config))
        self.assertEqual(list(self.archives.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
