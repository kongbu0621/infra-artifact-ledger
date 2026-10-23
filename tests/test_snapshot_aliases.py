"""LOGIC_ONLY mount-alias containment cases for held directory bindings.

The mount table and descriptor observations are controlled inputs here. These
checks do not certify a filesystem or NAS; real bind-mount probes are separate.
"""

import errno
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError


def mount(number, device, root, point):
    return storage.Mount(number, 1, device, root, point, "ext4", "/dev/synthetic")


def directory(path, identity, binding):
    return SimpleNamespace(path=Path(path), identity=identity, mount=binding,
                           check=Mock(), budget=SimpleNamespace(check=Mock()))


class SnapshotAliasContainmentTests(unittest.TestCase):
    def exercise(self, candidate, readonly, mounts, observed, *, reject):
        opened, closed, descriptors = [], [], {}
        def walk(readonly_input, path):
            self.assertIs(readonly_input, readonly)
            path = str(path)
            self.assertIn(path, observed, "only relevant projected paths may be opened")
            fd = 10000 + len(opened)
            opened.append(path)
            descriptors[fd] = observed[path]
            return fd
        def fstat(fd):
            device, inode = descriptors[fd]
            return SimpleNamespace(st_dev=device, st_ino=inode)
        with patch.object(storage, "_mounts", return_value=tuple(mounts)), \
                patch.object(storage, "_open_projected_directory", side_effect=walk), \
                patch.object(storage.os, "fstat", side_effect=fstat), \
                patch.object(storage.os, "close", side_effect=closed.append):
            if reject:
                with self.assertRaises(RecoveryError) as caught:
                    storage._reject_input_alias(candidate, readonly)
                self.assertEqual(caught.exception.code, "INVALID_INPUT")
            else:
                self.assertIsNone(storage._reject_input_alias(candidate, readonly))
        self.assertEqual(sorted(closed), sorted(descriptors), "all projected descriptors close once")
        if not reject:
            self.assertGreaterEqual(candidate.check.call_count, 2)
            self.assertGreaterEqual(readonly.check.call_count, 2)
        return opened

    def test_same_filesystem_descendant_bind_alias_is_rejected(self):
        base = mount(1, "8:2", "/", "/")
        alias = mount(2, "8:2", "/primary/restore/retained", "/outside/scratch")
        readonly = directory("/primary/restore", (2050, 10), base)
        scratch = directory("/outside/scratch", (2050, 20), alias)
        opened = self.exercise(scratch, readonly, [base, alias],
                              {"/primary/restore/retained": scratch.identity}, reject=True)
        self.assertEqual(opened, ["/primary/restore/retained"])

    def test_descendant_on_different_device_is_still_part_of_input(self):
        base = mount(1, "8:2", "/", "/")
        child = mount(2, "8:3", "/backup", "/primary/restore/attached")
        alias = mount(3, "8:3", "/backup/retained", "/outside/scratch")
        readonly = directory("/primary/restore", (2050, 10), base)
        scratch = directory("/outside/scratch", (2051, 20), alias)
        self.assertNotEqual(readonly.identity[0], scratch.identity[0])
        self.exercise(scratch, readonly, [base, child, alias],
                      {"/primary/restore/attached/retained": scratch.identity}, reject=True)

    def test_hidden_lower_mount_projection_does_not_reject_other_object(self):
        base = mount(1, "8:2", "/", "/")
        hidden = mount(2, "8:3", "/backup", "/primary/restore/attached")
        visible = mount(3, "8:4", "/", "/primary/restore/attached")
        alias = mount(4, "8:3", "/backup/retained", "/outside/scratch")
        readonly = directory("/primary/restore", (2050, 10), base)
        scratch = directory("/outside/scratch", (2051, 20), alias)
        self.exercise(scratch, readonly, [base, hidden, visible, alias],
                      {"/primary/restore/attached/retained": (2052, 90)}, reject=False)

    def test_missing_hidden_mount_projection_does_not_reject_independent_scratch(self):
        base = mount(1, "8:2", "/", "/")
        hidden = mount(2, "8:2", "/lower", "/primary/restore/attached")
        visible = mount(3, "8:2", "/upper", "/primary/restore/attached")
        alias = mount(4, "8:2", "/lower/retained", "/outside/scratch")
        readonly = directory("/primary/restore", (2050, 10), base)
        scratch = directory("/outside/scratch", (2050, 20), alias)
        with patch.object(storage, "_mounts", return_value=(base, hidden, visible, alias)), \
                patch.object(storage, "_open_projected_directory", return_value=None) as walk, \
                patch.object(storage.os, "close") as close:
            self.assertIsNone(storage._reject_input_alias(scratch, readonly))
        walk.assert_called_once_with(readonly, Path("/primary/restore/attached/retained"))
        close.assert_not_called()
        self.assertEqual(scratch.check.call_count, 2)
        self.assertEqual(readonly.check.call_count, 2)

    def test_missing_hidden_projection_does_not_skip_another_visible_alias(self):
        base = mount(1, "8:2", "/", "/")
        hidden = mount(2, "8:2", "/lower", "/primary/restore/attached")
        visible = mount(3, "8:2", "/upper", "/primary/restore/attached")
        alias = mount(4, "8:2", "/lower/retained", "/outside/scratch")
        other_alias = mount(5, "8:2", "/lower/retained", "/primary/restore/other")
        readonly = directory("/primary/restore", (2050, 10), base)
        scratch = directory("/outside/scratch", (2050, 20), alias)
        with patch.object(storage, "_mounts", return_value=(base, hidden, visible, alias, other_alias)), \
                patch.object(storage, "_open_projected_directory", side_effect=(None, 10000)) as walk, \
                patch.object(storage.os, "fstat", return_value=SimpleNamespace(st_dev=2050, st_ino=20)), \
                patch.object(storage.os, "close") as close:
            with self.assertRaises(RecoveryError) as caught:
                storage._reject_input_alias(scratch, readonly)
        self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assertEqual([call.args[1] for call in walk.call_args_list], [
            Path("/primary/restore/attached/retained"), Path("/primary/restore/other")])
        close.assert_called_once_with(10000)

    def test_complete_root_bind_input_preserves_physical_containment(self):
        base = mount(1, "8:2", "/", "/")
        input_alias = mount(2, "8:2", "/", "/root-bind")
        scratch_alias = mount(3, "8:2", "/primary/restore/retained", "/outside/scratch")
        readonly = directory("/root-bind/primary/restore", (2050, 10), input_alias)
        scratch = directory("/outside/scratch", (2050, 20), scratch_alias)
        self.exercise(scratch, readonly, [base, input_alias, scratch_alias],
                      {"/root-bind/primary/restore/retained": scratch.identity}, reject=True)

    def test_independent_scratch_is_allowed_without_directory_scan(self):
        base = mount(1, "8:2", "/", "/")
        readonly = directory("/primary/restore", (2050, 10), base)
        scratch = directory("/independent/scratch", (2050, 20), base)
        self.assertEqual(self.exercise(scratch, readonly, [base], {}, reject=False), [])

    def test_same_inode_number_on_other_device_is_not_an_alias(self):
        base = mount(1, "8:2", "/", "/")
        other = mount(2, "8:3", "/", "/other-device")
        readonly = directory("/primary/restore", (2050, 10), base)
        scratch = directory("/other-device/scratch", (2051, 10), other)
        self.assertEqual(self.exercise(scratch, readonly, [base, other], {}, reject=False), [])

    def test_unreadable_projected_path_is_not_treated_as_proof_of_separation(self):
        base = mount(1, "8:2", "/", "/")
        alias = mount(2, "8:2", "/primary/restore/retained", "/outside/scratch")
        readonly = directory("/primary/restore", (2050, 10), base)
        scratch = directory("/outside/scratch", (2050, 20), alias)
        failure = RecoveryError("IO_ERROR", "Synthetic projected binding cannot be checked")
        with patch.object(storage, "_mounts", return_value=(base, alias)), \
                patch.object(storage, "_open_projected_directory", side_effect=failure), \
                patch.object(storage.os, "close") as close:
            with self.assertRaises(RecoveryError) as caught:
                storage._reject_input_alias(scratch, readonly)
        self.assertIs(caught.exception, failure)
        close.assert_not_called()


class ProjectedDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="snapshot-projection-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        fd = os.open(self.root, storage._DIR_FLAGS)
        self.addCleanup(os.close, fd)
        self.input = SimpleNamespace(path=self.root, fd=fd, budget=Budget())
        (self.root / "directory").mkdir()
        (self.root / "file").write_bytes(b"unrelated retained input")
        (self.root / "link").symlink_to(self.root / "directory", target_is_directory=True)

    def test_missing_and_regular_file_components_are_not_directory_aliases(self):
        for relative in ("missing", "missing/child", "file", "file/child"):
            with self.subTest(relative=relative):
                self.assertIsNone(storage._open_projected_directory(self.input, self.root / relative))
        self.assertEqual((self.root / "file").read_bytes(), b"unrelated retained input")

    def test_visible_directory_returns_its_actual_identity(self):
        fd = storage._open_projected_directory(self.input, self.root / "directory")
        try:
            self.assertEqual(storage._identity(os.fstat(fd)),
                             storage._identity((self.root / "directory").stat()))
        finally:
            os.close(fd)

    def test_symlinks_are_not_mistaken_for_regular_file_separation(self):
        for relative in ("link", "link/child"):
            with self.subTest(relative=relative), self.assertRaises(RecoveryError) as caught:
                storage._open_projected_directory(self.input, self.root / relative)
            self.assertEqual(caught.exception.code, "UNSUPPORTED_STORAGE")

    def test_unreadable_projection_keeps_its_failure(self):
        with patch.object(storage.os, "stat", side_effect=PermissionError(errno.EACCES, "test denied")), \
                self.assertRaises(RecoveryError) as caught:
            storage._open_projected_directory(self.input, self.root / "directory")
        self.assertEqual(caught.exception.code, "IO_ERROR")

    def test_successful_separation_does_not_hide_a_close_failure(self):
        real_close = os.close
        def fail_after_close(fd):
            real_close(fd)
            raise OSError(errno.EIO, "test close failure after release")
        for relative in ("missing", "file"):
            with self.subTest(relative=relative):
                try:
                    raise ValueError("unrelated caller exception is being handled")
                except ValueError:
                    with patch.object(storage.os, "close", side_effect=fail_after_close), \
                            self.assertRaises(OSError) as caught:
                        storage._open_projected_directory(self.input, self.root / relative)
                self.assertEqual(caught.exception.errno, errno.EIO)
                os.fstat(self.input.fd)


if __name__ == "__main__":
    unittest.main()
