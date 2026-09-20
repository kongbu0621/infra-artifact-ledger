"""LOGIC_ONLY interrupted copies, publication and abrupt process termination.

Only storage classification is mocked to permit the isolated test filesystem.
File writes, SQLite validation, hard links and the abrupt child exit are real.
No test certifies NAS durability, performs a mount change or touches user data.
"""
import errno
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, recovery, snapshot_storage as storage

try:
    from .acceptance_helpers import append, create, encode
except ImportError:
    from acceptance_helpers import append, create, encode


_SOURCE_COMMIT = "d" * 40
_CHILD_EXIT_AFTER_LINK = r"""
import os, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from infra_artifact_ledger import recovery, snapshot_storage as storage
original = os.link
def interrupt(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(73)
with patch.object(storage, '_classify', return_value=None), patch.object(recovery.os, 'link', interrupt):
    recovery.restore(snapshot=sys.argv[2], target_dir=sys.argv[3],
                     expected_manifest_sha256=sys.argv[4], scratch_parent=sys.argv[5])
raise AssertionError('publication interruption was not reached')
"""


def image(directory):
    """Contents and identities, intentionally excluding read-access timestamps."""
    result = {}
    for entry in sorted(directory.rglob("*")):
        info = entry.lstat()
        result[str(entry.relative_to(directory))] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns,
            hashlib.sha256(entry.read_bytes()).hexdigest() if entry.is_file() else None,
        )
    return result


class SnapshotFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a2-failure-logic-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output, self.archive, self.scratch = (self.root / name for name in ("output", "archive", "scratch"))
        for directory in (self.output, self.archive, self.scratch):
            directory.mkdir()
        classification = patch.object(storage, "_classify", return_value=None)
        classification.start()
        self.addCleanup(classification.stop)
        self.source = self.root / "source.sqlite"
        with initialize(self.source) as ledger:
            ledger.execute(encode(create()))
            request, payloads = append("interrupted", b"synthetic full-byte copy\x00" * 128)
            ledger.execute(encode(request), payloads=payloads)
            self.summary = ledger.verify()
        self.source_bytes = self.source.read_bytes()
        self.created = self.make_snapshot("a" * 32)
        self.old_archive = recovery.publish(
            snapshot=self.created["snapshot_path"], storage_config=self.config(),
            expected_manifest_sha256=self.created["manifest_sha256"], scratch_parent=self.scratch,
        )["data"]
        self.old_output_image = image(Path(self.created["snapshot_path"]))
        self.old_archive_image = image(Path(self.old_archive["snapshot_path"]))

    def config(self):
        return {"format": "infra-artifact-ledger-storage/v1", "profile": "mounted-posix-v1",
                "storage_ref": "synthetic-logic-only", "mount_point": str(self.root),
                "mount_root": "/", "mount_source": "synthetic:/unused", "fs_type": "nfs4",
                "archive_root": str(self.archive)}

    def make_snapshot(self, identity):
        return recovery.create(db=self.source, output_root=self.output, snapshot_id=identity,
                               source_commit=_SOURCE_COMMIT)["data"]

    def unchanged_inputs(self):
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertEqual(image(Path(self.created["snapshot_path"])), self.old_output_image)
        self.assertEqual(image(Path(self.old_archive["snapshot_path"])), self.old_archive_image)

    def assert_failure(self, code, state, callback):
        with self.assertRaises(recovery.RecoveryError) as caught:
            callback()
        self.assertEqual((caught.exception.code, caught.exception.publication_state), (code, state))

    def interrupt_copy(self, operation, number):
        identity = ("b" if operation == "create" else "c") * 32
        if operation == "publish":
            created = self.make_snapshot(identity)
            destination = self.archive / identity
            callback = lambda: recovery.publish(
                snapshot=created["snapshot_path"], storage_config=self.config(),
                expected_manifest_sha256=created["manifest_sha256"], scratch_parent=self.scratch)
        elif operation == "create":
            destination = self.output / identity
            callback = lambda: self.make_snapshot(identity)
        else:
            destination = self.root / "restored"
            callback = lambda: recovery.restore(
                snapshot=self.created["snapshot_path"], target_dir=destination,
                expected_manifest_sha256=self.created["manifest_sha256"], scratch_parent=self.scratch)
        original_write = os.write
        writes = []

        def partial_then_fail(fd, value):
            locator = Path(os.readlink(f"/proc/self/fd/{fd}"))
            is_destination = locator == destination / "ledger.sqlite" if operation != "restore" else (
                locator.name == "ledger.sqlite" and locator.parent.parent == destination)
            if is_destination:
                written = original_write(fd, memoryview(value)[:17])
                writes.append(written)
                self.assertEqual(written, 17)
                raise OSError(number, "injected after a real partial database write")
            return original_write(fd, value)

        with patch.object(storage.os, "write", side_effect=partial_then_fail):
            self.assert_failure("IO_ERROR", "not_published", callback)
        self.assertEqual(writes, [17])
        self.assertTrue(destination.is_dir())
        self.assertFalse((destination / "COMMITTED.json").exists())
        if operation == "restore":
            self.assertFalse((destination / "ledger.sqlite").exists())
            self.assertEqual(list(destination.iterdir()), [])
        else:
            self.assertEqual((destination / "ledger.sqlite").stat().st_size, 17)
            self.assertEqual({p.name for p in destination.iterdir()}, {"ledger.sqlite"})
        self.unchanged_inputs()

    def test_create_partial_database_write_eio_is_not_published(self):
        self.interrupt_copy("create", errno.EIO)

    def test_create_partial_database_write_enospc_is_not_published(self):
        self.interrupt_copy("create", errno.ENOSPC)

    def test_publish_partial_database_write_eio_is_not_published(self):
        self.interrupt_copy("publish", errno.EIO)

    def test_publish_partial_database_write_enospc_is_not_published(self):
        self.interrupt_copy("publish", errno.ENOSPC)

    def test_restore_partial_database_write_eio_is_not_published(self):
        self.interrupt_copy("restore", errno.EIO)

    def test_restore_partial_database_write_enospc_is_not_published(self):
        self.interrupt_copy("restore", errno.ENOSPC)

    def test_abrupt_child_exit_after_restore_link_leaves_two_links_and_readonly_check(self):
        target = self.root / "interrupted-restore"
        # Child uses the same package as this parent, including installed-wheel
        # runs; the location of the test checkout does not select its software.
        package_source = Path(recovery.__file__).resolve().parents[1]
        run = subprocess.run(
            [sys.executable, "-I", "-c", _CHILD_EXIT_AFTER_LINK, str(package_source),
             self.created["snapshot_path"], str(target), self.created["manifest_sha256"], str(self.scratch)],
            capture_output=True, timeout=30, cwd=self.root,
        )
        self.assertEqual((run.returncode, run.stdout, run.stderr), (73, b"", b""))
        database = target / "ledger.sqlite"
        self.assertEqual(database.stat().st_nlink, 2)
        private, = (entry for entry in target.iterdir() if entry.is_dir())
        self.assertEqual((private / "ledger.sqlite").stat().st_ino, database.stat().st_ino)
        before = image(target)
        checked = recovery.check_restore(target_dir=target,
                                         expected_database_sha256=self.created["database_sha256"],
                                         scratch_parent=self.scratch)
        self.assertEqual((checked["status"], checked["publication_state"]), ("OK", "not_applicable"))
        self.assertEqual(checked["data"]["summary"], self.summary)
        self.assertEqual(image(target), before)
        self.unchanged_inputs()

    def test_mount_binding_failure_after_marker_link_is_unknown_and_preserves_archive(self):
        created = self.make_snapshot("c" * 32)
        linked = False
        original_link, original_mount_id = os.link, storage._fd_mount_id

        def link_then_lose_binding(*args, **kwargs):
            nonlocal linked
            original_link(*args, **kwargs)
            linked = True

        def changed_binding(fd):
            return original_mount_id(fd) + (100_000 if linked else 0)

        with patch.object(recovery.os, "link", side_effect=link_then_lose_binding), \
                patch.object(storage, "_fd_mount_id", side_effect=changed_binding):
            self.assert_failure("PUBLICATION_UNKNOWN", "unknown", lambda: recovery.publish(
                snapshot=created["snapshot_path"], storage_config=self.config(),
                expected_manifest_sha256=created["manifest_sha256"], scratch_parent=self.scratch))
        self.assertTrue(linked)
        destination = self.archive / ("c" * 32)
        self.assertEqual({entry.name for entry in destination.iterdir()},
                         {"ledger.sqlite", "manifest.json", "COMMITTED.json"})
        checked = recovery.verify(snapshot=destination, expected_manifest_sha256=created["manifest_sha256"],
                                   scratch_parent=self.scratch, storage_config=self.config())
        self.assertEqual(checked["publication_state"], "not_applicable")
        self.assertEqual(checked["data"]["summary"], self.summary)
        self.unchanged_inputs()


if __name__ == "__main__":
    unittest.main()
