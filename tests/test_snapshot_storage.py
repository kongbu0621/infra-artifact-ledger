"""Storage contract tests with explicit synthetic mount classification.

The runner filesystem is NOT thereby declared an A2 durable storage backend.
"""
import errno
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError


class MountParsingTests(unittest.TestCase):
    def test_fields_optional_tokens_and_escaped_paths(self):
        raw = "25 1 0:27 /some\\040root /mnt/archive\\040one rw shared:2 - nfs4 server:/some\\040export rw\n"
        item, = storage.parse_mountinfo(raw)
        self.assertEqual((item.mount_id, item.parent_id, item.root, item.point,
                          item.fs_type, item.source),
                         (25, 1, "/some root", "/mnt/archive one", "nfs4", "server:/some export"))

    def test_unicode_whitespace_inside_paths_is_not_a_kernel_separator(self):
        raw = "25 1 0:27 /export\u00a0root /mnt/archive\u2028one rw - nfs4 server:/some\u2003export rw\n"
        item, = storage.parse_mountinfo(raw)
        self.assertEqual(item.root, "/export\u00a0root")
        self.assertEqual(item.point, "/mnt/archive\u2028one")
        self.assertEqual(item.source, "server:/some\u2003export")

    def test_bad_mountinfo_fails_closed(self):
        for raw in ("", "25 1 nope", "0 1 0:1 / / rw - ext4 device rw",
                    "1 1 0:1 / / rw - ext4 device rw\n1 1 0:1 / / rw - ext4 device rw"):
            with self.subTest(raw=raw), self.assertRaises(RecoveryError) as caught:
                storage.parse_mountinfo(raw)
            self.assertEqual(caught.exception.code, "UNSUPPORTED_STORAGE")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        descriptor = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.mount_id = storage._fd_mount_id(descriptor)
        finally:
            os.close(descriptor)
        self.mount = storage.Mount(self.mount_id, 1, "0:1", "/", "/", "ext4", "synthetic-device")
        self.patcher = mock.patch.object(storage, "_mounts", return_value=(self.mount,))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.budget = Budget()

    def directory(self):
        return storage.open_directory(self.base, budget=self.budget)

    def assert_code(self, expected, action):
        with self.assertRaises(RecoveryError) as caught:
            action()
        self.assertEqual(caught.exception.code, expected)
        return caught.exception

    def test_directory_binding_rejects_replacement_and_does_not_touch_replacement(self):
        owned = self.base / "owned"
        owned.mkdir()
        with storage.open_directory(owned, budget=self.budget) as directory:
            owned.rename(self.base / "old")
            owned.mkdir()
            self.assert_code("IO_ERROR", directory.check)
            self.assert_code("IO_ERROR", lambda: directory.mkdir("must-not-exist"))
        self.assertFalse((owned / "must-not-exist").exists())

    def test_symlink_parent_is_rejected(self):
        (self.base / "real").mkdir()
        (self.base / "alias").symlink_to(self.base / "real", target_is_directory=True)
        self.assert_code("UNSUPPORTED_STORAGE", lambda: storage.open_directory(self.base / "alias", budget=self.budget))
        self.assert_code("UNSUPPORTED_STORAGE", lambda: storage.open_directory(self.base / "alias" / "child", budget=self.budget))

    def test_local_candidates_and_overlay_network_unknown_rejected(self):
        for fs_type in ("ext4", "xfs", "btrfs"):
            mount = storage.Mount(self.mount_id, 1, "0:1", "/", "/", fs_type, "synthetic")
            with mock.patch.object(storage, "_mounts", return_value=(mount,)):
                with self.directory() as directory:
                    directory.check()
        for fs_type in ("overlay", "tmpfs", "nfs4", "cifs", "unknown"):
            mount = storage.Mount(self.mount_id, 1, "0:1", "/", "/", fs_type, "synthetic")
            with mock.patch.object(storage, "_mounts", return_value=(mount,)):
                self.assert_code("UNSUPPORTED_STORAGE", self.directory)

    def test_network_config_exact_endpoint_and_archive_containment(self):
        mount = storage.Mount(self.mount_id, 1, "0:1", "/export", str(self.base), "nfs4", "host:/export")
        config = {"mount_point": str(self.base), "mount_root": "/export", "fs_type": "nfs4",
                  "mount_source": "host:/export", "archive_root": str(self.base)}
        with mock.patch.object(storage, "_mounts", return_value=(mount,)):
            with storage.open_directory(self.base, budget=self.budget, storage_config=config) as directory:
                directory.check()
            for field in ("mount_point", "mount_root", "fs_type", "mount_source", "archive_root"):
                wrong = dict(config)
                wrong[field] = "/mismatch" if field != "fs_type" else "cifs"
                self.assert_code("UNSUPPORTED_STORAGE", lambda: storage.open_directory(self.base, budget=self.budget, storage_config=wrong))

    def test_deepest_mount_and_fd_id_are_both_required(self):
        child_mount = storage.Mount(self.mount_id + 1, 1, "0:2", "/", str(self.base), "ext4", "submount")
        with mock.patch.object(storage, "_mounts", return_value=(self.mount, child_mount)):
            self.assert_code("UNSUPPORTED_STORAGE", self.directory)
        with self.directory() as directory:
            with mock.patch.object(storage, "_fd_mount_id", return_value=self.mount_id + 1):
                self.assert_code("UNSUPPORTED_STORAGE", directory.check)
            with mock.patch.object(storage, "_mounts", return_value=(storage.Mount(
                    self.mount_id, 1, "0:1", "/changed", "/", "ext4", "synthetic-device"),)):
                self.assert_code("UNSUPPORTED_STORAGE", directory.check)

    def test_mkdir_is_exclusive_for_directory_file_and_dangling_link(self):
        with self.directory() as parent:
            with parent.mkdir("child") as child:
                self.assertEqual(child.path, self.base / "child")
                child.check()
            (self.base / "file").write_bytes(b"old")
            (self.base / "link").symlink_to("missing")
            for name in ("child", "file", "link"):
                error = self.assert_code("TARGET_EXISTS", lambda: parent.mkdir(name))
                self.assertEqual(error.publication_state, "not_applicable")
        self.assertEqual((self.base / "file").read_bytes(), b"old")
        self.assertTrue((self.base / "link").is_symlink())

    def test_open_file_rejects_links_fifo_directory_and_cross_mount(self):
        (self.base / "data").write_bytes(b"bytes")
        (self.base / "sym").symlink_to("data")
        os.link(self.base / "data", self.base / "hard")
        os.mkfifo(self.base / "fifo")
        (self.base / "dir").mkdir()
        with self.directory() as directory:
            for name in ("sym", "fifo", "dir"):
                self.assert_code("INTEGRITY_FAILURE", lambda: directory.open_file(name))
            self.assert_code("INTEGRITY_FAILURE", lambda: directory.open_file("data", single_link=True))
            descriptor = directory.open_file("data")
            os.close(descriptor)
            original = storage._fd_mount_id
            def file_mismatch(fd):
                mode = os.fstat(fd).st_mode
                return self.mount_id + 1 if not __import__("stat").S_ISDIR(mode) else original(fd)
            with mock.patch.object(storage, "_fd_mount_id", side_effect=file_mismatch):
                self.assert_code("UNSUPPORTED_STORAGE", lambda: directory.open_file("data"))

    def test_member_bound_and_no_read_side_effects(self):
        for name in ("a", "b", "c", "d"):
            (self.base / name).write_bytes(name.encode())
        before = {p.name: p.read_bytes() for p in self.base.iterdir()}
        with self.directory() as directory:
            self.assert_code("INTEGRITY_FAILURE", lambda: directory.members(limit=3))
            self.assertEqual(directory.members(limit=4), set(before))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.base.iterdir()})

    def test_temporary_directory_cleanup_only_owned_tree(self):
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "keep"
        sentinel.write_bytes(b"keep")
        with self.directory() as parent:
            with storage.temporary_directory(parent, self.budget) as temp:
                temp_path = temp.path
                storage.write_file(temp, "data", b"test", self.budget)
                (temp.path / "alias").symlink_to(outside, target_is_directory=True)
                with temp.mkdir("nested") as nested:
                    storage.write_file(nested, "child", b"child", self.budget)
            self.assertFalse(temp_path.exists())
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_walk_close_failure_closes_child_without_reclosing_reused_parent_fd(self):
        real_open, real_close = os.open, os.close
        opened, reused, failed = [], [], []

        def track_open(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor

        def close_then_fail(descriptor):
            real_close(descriptor)
            if not failed:
                failed.append(descriptor)
                reused.append(real_open("/dev/null", os.O_RDONLY))
                self.assertEqual(reused[-1], descriptor)
                raise OSError(errno.EIO, "close reported failure after releasing parent")

        try:
            with mock.patch.object(storage.os, "open", side_effect=track_open), \
                    mock.patch.object(storage.os, "close", side_effect=close_then_fail):
                self.assert_code("IO_ERROR", lambda: storage._walk_directory(self.base))
            self.assertEqual(len(opened), 2)
            self.assertNotEqual(opened[1], reused[0])
            os.fstat(reused[0])
            with self.assertRaises(OSError):
                os.fstat(opened[1])
        finally:
            for descriptor in set(opened + reused):
                try:
                    real_close(descriptor)
                except OSError:
                    pass

    def test_copy_close_failure_does_not_close_a_reused_descriptor(self):
        (self.base / "source").write_bytes(b"contents")
        real_close, real_exclusive = os.close, storage._exclusive_file
        output, reused, failed = [], [], []

        def exclusive(*args, **kwargs):
            descriptor = real_exclusive(*args, **kwargs)
            output.append(descriptor)
            return descriptor

        def close_then_fail(descriptor):
            real_close(descriptor)
            if output and descriptor == output[0] and not failed:
                failed.append(descriptor)
                reused.append(os.open("/dev/null", os.O_RDONLY))
                self.assertEqual(reused[-1], descriptor)
                raise OSError(errno.EIO, "close reported failure after releasing output")

        with self.directory() as directory:
            source = directory.open_file("source")
            try:
                with mock.patch.object(storage, "_exclusive_file", side_effect=exclusive), \
                        mock.patch.object(storage.os, "close", side_effect=close_then_fail):
                    self.assert_code("IO_ERROR", lambda: storage.copy_file(source, directory, "copy", self.budget))
                self.assertTrue(reused)
                os.fstat(reused[0])
                self.assertEqual((self.base / "copy").read_bytes(), b"contents")
            finally:
                real_close(source)
                for descriptor in reused:
                    try:
                        real_close(descriptor)
                    except OSError:
                        pass

    def test_cleanup_rejects_replaced_child_before_deleting_its_contents(self):
        replacement = self.base / "replacement"
        replacement.mkdir()
        (replacement / "unrelated").write_bytes(b"must survive")
        original_open = os.open
        changed = []
        with self.directory() as parent, parent.mkdir("owned") as owned:
            with owned.mkdir("nested") as nested:
                storage.write_file(nested, "original", b"also preserved", self.budget)

            def replace_before_open(name, *args, **kwargs):
                if name == "nested" and kwargs.get("dir_fd") == owned.fd and not changed:
                    changed.append(True)
                    (owned.path / "nested").rename(self.base / "retained")
                    replacement.rename(owned.path / "nested")
                return original_open(name, *args, **kwargs)

            with mock.patch.object(storage.os, "open", side_effect=replace_before_open):
                self.assert_code("IO_ERROR", lambda: storage._remove_owned_contents(owned))
            self.assertEqual((owned.path / "nested" / "unrelated").read_bytes(), b"must survive")
            self.assertEqual((self.base / "retained" / "original").read_bytes(), b"also preserved")

    def test_copy_hash_short_writes_and_existing_target(self):
        raw = b"x" * (storage.COPY_CHUNK + 23)
        source = self.base / "source"
        source.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        original = os.write
        with self.directory() as directory:
            fd = directory.open_file("source")
            try:
                with mock.patch.object(storage.os, "write", side_effect=lambda descriptor, data: original(descriptor, data[:37])):
                    result = storage.copy_file(fd, directory, "copied", self.budget,
                                               expected_size=len(raw), expected_hash=digest)
                self.assertEqual(result, {"byte_length": len(raw), "sha256": digest})
                self.assert_code("TARGET_EXISTS", lambda: storage.copy_file(fd, directory, "copied", self.budget))
            finally:
                os.close(fd)
        self.assertEqual((self.base / "copied").read_bytes(), raw)

    def test_copy_source_mutation_and_wrong_digest_preserve_source(self):
        source = self.base / "source"
        source.write_bytes(b"original")
        with self.directory() as directory:
            fd = directory.open_file("source")
            try:
                self.assert_code("INTEGRITY_FAILURE", lambda: storage.copy_file(fd, directory, "wrong", self.budget, expected_hash="0" * 64))
                self.assertEqual(source.read_bytes(), b"original")
                original_read = os.read
                changed = False
                def change(descriptor, count):
                    nonlocal changed
                    value = original_read(descriptor, count)
                    if descriptor == fd and not changed:
                        changed = True
                        source.write_bytes(b"modified")
                    return value
                with mock.patch.object(storage.os, "read", side_effect=change):
                    self.assert_code("INTEGRITY_FAILURE", lambda: storage.copy_file(fd, directory, "mutated", self.budget))
            finally:
                os.close(fd)

    def test_copy_limit_and_length_before_target_creation(self):
        (self.base / "source").write_bytes(b"12345")
        with self.directory() as directory:
            fd = directory.open_file("source")
            try:
                with mock.patch.object(storage, "MAX_DATABASE_BYTES", 4):
                    self.assert_code("RESOURCE_LIMIT", lambda: storage.copy_file(fd, directory, "over", self.budget))
                self.assert_code("INTEGRITY_FAILURE", lambda: storage.copy_file(fd, directory, "length", self.budget, expected_size=6))
            finally:
                os.close(fd)
        self.assertFalse((self.base / "over").exists())
        self.assertFalse((self.base / "length").exists())

    def test_read_byte_boundary_and_no_overwrite(self):
        with self.directory() as directory:
            storage.write_file(directory, "data", b"12345", self.budget)
            self.assertEqual(storage.read_file(directory, "data", 5, self.budget), b"12345")
            self.assert_code("RESOURCE_LIMIT", lambda: storage.read_file(directory, "data", 4, self.budget))
            self.assert_code("TARGET_EXISTS", lambda: storage.write_file(directory, "data", b"bad", self.budget))
        self.assertEqual((self.base / "data").read_bytes(), b"12345")

    def test_file_fsync_failures_report_sync_stage_and_preserve_partial_files(self):
        (self.base / "source").write_bytes(b"copy contents")
        with self.directory() as directory:
            source = directory.open_file("source")
            try:
                with mock.patch.object(storage.os, "fsync", side_effect=OSError(errno.EIO, "sync failed")):
                    copied = self.assert_code("IO_ERROR", lambda: storage.copy_file(
                        source, directory, "copied", self.budget))
                    written = self.assert_code("IO_ERROR", lambda: storage.write_file(
                        directory, "written", b"written contents", self.budget))
                self.assertEqual(copied.stage, "sync")
                self.assertEqual(written.stage, "sync")
                self.assertEqual(copied.publication_state, "not_published")
                self.assertEqual(written.publication_state, "not_published")
                self.assertEqual((self.base / "copied").read_bytes(), b"copy contents")
                self.assertEqual((self.base / "written").read_bytes(), b"written contents")
                self.assertEqual((self.base / "source").read_bytes(), b"copy contents")
            finally:
                os.close(source)

    def test_deadline_and_fsync_failures(self):
        with self.directory() as directory:
            with mock.patch.object(storage.os, "fsync", side_effect=OSError(errno.EINVAL, "unsupported")):
                self.assert_code("UNSUPPORTED_STORAGE", directory.fsync)
            with mock.patch.object(storage.os, "fsync", side_effect=OSError(errno.EIO, "failed")):
                self.assert_code("IO_ERROR", directory.fsync)
            self.budget.deadline = 0
            self.assert_code("TIMEOUT", lambda: directory.mkdir("must-not-exist"))
        self.assertFalse((self.base / "must-not-exist").exists())

    def test_preflight_is_explicit_and_removes_only_probe(self):
        (self.base / "keep").write_bytes(b"preserve")
        with self.directory() as directory:
            result = storage.preflight(directory, self.budget)
            self.assertEqual(result["mount_id"], self.mount_id)
            self.assertEqual(directory.members(), {"keep"})
        self.assertEqual((self.base / "keep").read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
