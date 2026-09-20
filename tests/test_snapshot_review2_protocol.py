"""LOGIC_ONLY mixed-failure regressions for recovery publication state.

Only filesystem classification is mocked. Real SQLite snapshots, existing
targets, descriptor closes and preserved target bytes exercise the API.
"""
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, recovery, snapshot_cli
from infra_artifact_ledger import snapshot_format as fmt
from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError


class SnapshotReview2ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a2-review2-protocol-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output, self.scratch, self.archive = (
            self.root / name for name in ("output", "scratch", "archive"))
        for directory in (self.output, self.scratch, self.archive):
            directory.mkdir()
        classification = patch.object(storage, "_classify", return_value=None)
        classification.start()
        self.addCleanup(classification.stop)
        self.source = self.root / "source.sqlite"
        with initialize(self.source):
            pass
        self.identity = "b" * 32
        self.created = recovery.create(
            db=self.source, output_root=self.output, snapshot_id=self.identity,
            source_commit="a" * 40)["data"]

    def config(self):
        return {"format": "infra-artifact-ledger-storage/v1", "profile": "mounted-posix-v1",
                "storage_ref": "review2", "mount_point": str(self.root), "mount_root": "/",
                "mount_source": "synthetic:/review2", "fs_type": "nfs4",
                "archive_root": str(self.archive)}

    def conflict_case(self, operation, *, temporary_close=False):
        if operation == "create":
            target, close_path = self.output / self.identity, self.output
            invoke = lambda: recovery.create(
                db=self.source, output_root=self.output, snapshot_id=self.identity,
                source_commit="a" * 40)
        elif operation == "restore":
            target, close_path = self.root / "restored", self.root
            target.mkdir()
            (target / "existing").write_bytes(b"preserve existing target")
            invoke = lambda: recovery.restore(
                snapshot=self.created["snapshot_path"], target_dir=target,
                expected_manifest_sha256=self.created["manifest_sha256"],
                scratch_parent=self.scratch)
        else:
            target, close_path = self.archive / self.identity, self.archive
            target.mkdir()
            (target / "existing").write_bytes(b"preserve existing generation")
            invoke = lambda: recovery.publish(
                snapshot=self.created["snapshot_path"], storage_config=self.config(),
                expected_manifest_sha256=self.created["manifest_sha256"],
                scratch_parent=self.scratch)
        before = {entry.name: entry.read_bytes() for entry in target.iterdir()}
        actual_close = storage.Directory.close
        injected = []

        def close_then_fail(directory):
            matches = (directory.path.parent == self.scratch
                       and directory.path.name.startswith(".snapshot-tmp-")) if temporary_close else (
                           directory.path == close_path)
            was_open = not directory._closed
            actual_close(directory)
            if was_open and matches and not injected:
                injected.append(directory.path)
                raise OSError(errno.EIO, "synthetic error after actual descriptor close")

        with patch.object(storage.Directory, "close", close_then_fail):
            with self.assertRaises(recovery.RecoveryError) as caught:
                invoke()
        self.assertEqual(len(injected), 1)
        self.assertEqual(before, {entry.name: entry.read_bytes() for entry in target.iterdir()})
        self.assertEqual((caught.exception.code, caught.exception.publication_state),
                         ("TARGET_EXISTS", "not_applicable"))

    def test_create_existing_generation_survives_parent_close_failure(self):
        self.conflict_case("create")

    def test_restore_existing_target_survives_parent_close_failure(self):
        self.conflict_case("restore")

    def test_publish_existing_generation_survives_destination_close_failure(self):
        self.conflict_case("publish")

    def test_publish_existing_generation_survives_private_close_failure(self):
        self.conflict_case("publish", temporary_close=True)

    def test_postpublication_mount_failure_keeps_sync_stage(self):
        identity = "c" * 32
        target = self.output / identity
        actual_fsync, actual_mounts = storage.Directory.fsync, storage._mounts
        armed, injected = [], []

        def arm_final_sync(directory):
            if directory.path == target and (target / "COMMITTED.json").exists():
                armed.append(directory.mount.mount_id)
            return actual_fsync(directory)

        def disappear_mount_once():
            mounts = actual_mounts()
            if armed and not injected:
                injected.append(armed[0])
                return tuple(mount for mount in mounts if mount.mount_id != armed[0])
            return mounts

        with patch.object(storage.Directory, "fsync", arm_final_sync), \
                patch.object(storage, "_mounts", disappear_mount_once):
            with self.assertRaises(recovery.RecoveryError) as caught:
                recovery.create(db=self.source, output_root=self.output,
                                snapshot_id=identity, source_commit="a" * 40)
        self.assertEqual(len(injected), 1)
        self.assertEqual({entry.name for entry in target.iterdir()},
                         {"ledger.sqlite", "manifest.json", "COMMITTED.json"})
        expected = hashlib.sha256((target / "manifest.json").read_bytes()).hexdigest()
        checked = recovery.verify(snapshot=target, expected_manifest_sha256=expected,
                                  scratch_parent=self.scratch)
        self.assertEqual(checked["status"], "OK")
        self.assertEqual((caught.exception.code, caught.exception.publication_state,
                          caught.exception.stage), ("PUBLICATION_UNKNOWN", "unknown", "sync"))

    def test_postpublication_sync_error_and_partial_stdout_do_not_retry_output(self):
        target = self.root / "restored"
        actual_fsync = storage.Directory.fsync
        sync_faults = []

        def fail_published_target_sync(directory):
            if directory.path == target and (target / "ledger.sqlite").exists():
                sync_faults.append(directory.path)
                raise OSError(errno.EIO, "synthetic final sync error")
            return actual_fsync(directory)

        class PartialBrokenBuffer:
            def __init__(self):
                self.calls, self.raw = 0, b""

            def write(self, raw):
                self.calls += 1
                self.raw += raw[:19]
                raise BrokenPipeError("synthetic partial response then closed reader")

        class Output:
            def __init__(self):
                self.buffer = PartialBrokenBuffer()

        stdout, stderr = Output(), io.StringIO()
        argv = ["restore", "--snapshot", self.created["snapshot_path"],
                "--target-dir", str(target), "--expected-manifest-sha256",
                self.created["manifest_sha256"], "--scratch-parent", str(self.scratch)]
        with patch.object(storage.Directory, "fsync", fail_published_target_sync), \
                patch.object(snapshot_cli.sys, "stdout", stdout), \
                patch.object(snapshot_cli.sys, "stderr", stderr):
            result = snapshot_cli.main(argv)
        self.assertEqual(sync_faults, [target])
        self.assertEqual(result, 9)
        self.assertEqual(stdout.buffer.calls, 1)
        self.assertEqual(len(stdout.buffer.raw), 19)
        self.assertNotIn(b"\n", stdout.buffer.raw)
        self.assertIn("retain the original snapshot identity", stderr.getvalue())
        self.assertEqual({entry.name for entry in target.iterdir()}, {"ledger.sqlite"})
        self.assertEqual(hashlib.sha256((target / "ledger.sqlite").read_bytes()).hexdigest(),
                         self.created["database_sha256"])
        checked = recovery.check_restore(target_dir=target,
                                         expected_database_sha256=self.created["database_sha256"],
                                         scratch_parent=self.scratch)
        self.assertEqual(checked["status"], "OK")

    def test_handled_caller_exception_does_not_hide_restore_temporary_close_failure(self):
        target = self.root / "restored"
        actual_close = storage.Directory.close
        injected = []

        def close_stage_then_fail(directory):
            matches = directory.path.parent == target and directory.path.name.startswith(".snapshot-tmp-")
            was_open = not directory._closed
            actual_close(directory)
            if matches and was_open and not injected:
                injected.append(directory.path)
                raise OSError(errno.EIO, "synthetic close error after successful publication body")

        # A normal recovery operation is allowed inside an application's
        # unrelated exception handler. That handled exception must not make a
        # new cleanup/close error look secondary to this operation.
        try:
            raise ValueError("unrelated handled caller exception")
        except ValueError:
            with patch.object(storage.Directory, "close", close_stage_then_fail):
                with self.assertRaises(RecoveryError) as caught:
                    recovery.restore(snapshot=self.created["snapshot_path"], target_dir=target,
                                     expected_manifest_sha256=self.created["manifest_sha256"],
                                     scratch_parent=self.scratch)
        self.assertEqual(len(injected), 1)
        self.assertEqual((caught.exception.code, caught.exception.publication_state),
                         ("PUBLICATION_UNKNOWN", "unknown"))
        self.assertEqual({entry.name for entry in target.iterdir()}, {"ledger.sqlite"})
        self.assertEqual(hashlib.sha256((target / "ledger.sqlite").read_bytes()).hexdigest(),
                         self.created["database_sha256"])

    def report_failure(self, operation, failure, transport):
        identity = "c" * 32
        if operation == "create":
            target = self.output / identity
            kwargs = dict(db=self.source, output_root=self.output,
                          snapshot_id=identity, source_commit="a" * 40)
        else:
            target = self.root / "restored"
            kwargs = dict(snapshot=self.created["snapshot_path"], target_dir=target,
                          expected_manifest_sha256=self.created["manifest_sha256"],
                          scratch_parent=self.scratch)
        actual_check, actual_link = Budget.check, os.link
        report_checks, links = [], []

        def fail_report_deadline(budget, stage="validate"):
            if stage == "report":
                report_checks.append(stage)
                raise RecoveryError("TIMEOUT", "Synthetic report checkpoint deadline", stage)
            return actual_check(budget, stage)

        def record_link(*args, **kwargs):
            links.append(args)
            return actual_link(*args, **kwargs)

        fault = (patch.object(Budget, "check", fail_report_deadline) if failure == "TIMEOUT"
                 else patch.object(fmt, "MAX_RESPONSE", 1))
        with fault, patch.object(recovery.os, "link", record_link):
            if transport == "api":
                with self.assertRaises(RecoveryError) as caught:
                    getattr(recovery, operation)(**kwargs)
                envelope = caught.exception.to_envelope(operation)
            else:
                class Output:
                    def __init__(self):
                        self.buffer = io.BytesIO()

                stdout, stderr = Output(), io.StringIO()
                argv = [operation, *(item for key, value in kwargs.items()
                                     for item in ("--" + key.replace("_", "-"), str(value)))]
                with patch.object(snapshot_cli.sys, "stdout", stdout), \
                        patch.object(snapshot_cli.sys, "stderr", stderr):
                    exit_code = snapshot_cli.main(argv)
                self.assertEqual(exit_code, 8 if failure == "TIMEOUT" else 7)
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(stdout.buffer.getvalue().count(b"\n"), 1)
                envelope = json.loads(stdout.buffer.getvalue())
        self.assertEqual(len(links), 1)
        if failure == "TIMEOUT":
            self.assertEqual(report_checks, ["report"])
        if operation == "create":
            self.assertEqual({entry.name for entry in target.iterdir()},
                             {"ledger.sqlite", "manifest.json", "COMMITTED.json"})
            raw = (target / "manifest.json").read_bytes()
            expected = hashlib.sha256(raw).hexdigest()
            before = {entry.name: entry.read_bytes() for entry in target.iterdir()}
            checked = recovery.verify(snapshot=target, expected_manifest_sha256=expected,
                                      scratch_parent=self.scratch)
            self.assertEqual(checked["data"]["database_sha256"],
                             json.loads(raw)["database"]["sha256"])
        else:
            self.assertEqual({entry.name for entry in target.iterdir()}, {"ledger.sqlite"})
            before = {entry.name: entry.read_bytes() for entry in target.iterdir()}
            self.assertEqual(hashlib.sha256(before["ledger.sqlite"]).hexdigest(),
                             self.created["database_sha256"])
            checked = recovery.check_restore(target_dir=target,
                                             expected_database_sha256=self.created["database_sha256"],
                                             scratch_parent=self.scratch)
        self.assertEqual(checked["status"], "OK")
        self.assertEqual(before, {entry.name: entry.read_bytes() for entry in target.iterdir()})
        self.assertEqual((envelope["error"]["code"], envelope["publication_state"],
                          envelope["error"]["stage"]), (failure, "published", "report"))

    def test_create_report_timeout_retains_published(self):
        self.report_failure("create", "TIMEOUT", "api")

    def test_restore_report_timeout_retains_published(self):
        self.report_failure("restore", "TIMEOUT", "api")

    def test_create_report_limit_retains_published(self):
        self.report_failure("create", "RESOURCE_LIMIT", "api")

    def test_restore_report_limit_retains_published(self):
        self.report_failure("restore", "RESOURCE_LIMIT", "api")

    def test_create_cli_report_timeout_retains_published(self):
        self.report_failure("create", "TIMEOUT", "cli")

    def test_restore_cli_report_timeout_retains_published(self):
        self.report_failure("restore", "TIMEOUT", "cli")

    def test_create_cli_report_limit_retains_published(self):
        self.report_failure("create", "RESOURCE_LIMIT", "cli")

    def test_restore_cli_report_limit_retains_published(self):
        self.report_failure("restore", "RESOURCE_LIMIT", "cli")


if __name__ == "__main__":
    unittest.main()
