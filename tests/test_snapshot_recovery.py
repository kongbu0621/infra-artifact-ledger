"""LOGIC_ONLY A2 public lifecycle and failure-state checks.

Only filesystem classification is mocked to admit this test sandbox. Actual
directory/fd/mount bindings, SQLite, files, hard links and fsync still run.
These tests do not certify overlay, tmpfs, NFS or any deployed NAS profile.
"""

import errno
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, open as open_ledger
from infra_artifact_ledger import recovery
from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger import snapshot_format as fmt
from infra_artifact_ledger import snapshot_cli
from infra_artifact_ledger.fingerprint import manifest_digest

try:
    from .acceptance_helpers import (ARTIFACT, REPORT_V1, REPORT_V2, SCOPE, append,
                                     create, encode, import_kwargs, import_request)
except ImportError:
    from acceptance_helpers import (ARTIFACT, REPORT_V1, REPORT_V2, SCOPE, append,
                                    create, encode, import_kwargs, import_request)


SNAPSHOT_ID = "1023456789abcdef" * 2
SOURCE_COMMIT = "3" * 40
_REAL_CLASSIFY = storage._classify


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table_rows(path):
    """Independent physical-state inspection, not the recovery summary."""
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        return {name: sorted(connection.execute("SELECT * FROM " + name).fetchall(), key=repr)
                for name in ("ledger_format", "records", "payloads", "operations", "refs")}
    finally:
        connection.close()


class SnapshotRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a2-public-logic-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output, self.scratch, self.archive = (self.root / name for name in ("output", "scratch", "archive"))
        for directory in (self.output, self.scratch, self.archive):
            directory.mkdir()
        classification = patch.object(storage, "_classify", return_value=None)
        classification.start()
        self.addCleanup(classification.stop)
        self.database = self.root / "source.sqlite"
        with initialize(self.database):
            pass

    def assert_error(self, code, state, function, **kwargs):
        with self.assertRaises(recovery.RecoveryError) as caught:
            function(**kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.publication_state, state)
        return caught.exception

    def create_snapshot(self, identity=SNAPSHOT_ID):
        return recovery.create(db=self.database, output_root=self.output,
                               snapshot_id=identity, source_commit=SOURCE_COMMIT)

    def test_source_preflight_rejects_before_any_scratch_creation(self):
        for source_kind in ("invalid_header", "wal"):
            with self.subTest(source_kind=source_kind):
                if source_kind == "invalid_header":
                    self.database.write_bytes(b"invalid database")
                else:
                    self.database.unlink()
                    with initialize(self.database):
                        pass
                    connection = sqlite3.connect(self.database)
                    try:
                        connection.execute("PRAGMA journal_mode=WAL")
                    finally:
                        connection.close()
                before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
                with patch.object(storage, "temporary_directory", side_effect=AssertionError("scratch before preflight")):
                    with self.assertRaises(recovery.RecoveryError) as caught:
                        self.create_snapshot()
                self.assertEqual(caught.exception.publication_state, "not_published")
                self.assertEqual(list(self.output.iterdir()), [])
                self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}, before)

    def verify_snapshot(self, result, **kwargs):
        return recovery.verify(snapshot=result["data"]["snapshot_path"],
                               expected_manifest_sha256=result["data"]["manifest_sha256"],
                               scratch_parent=self.scratch, **kwargs)

    def restore_snapshot(self, result, target=None, **kwargs):
        return recovery.restore(snapshot=result["data"]["snapshot_path"],
                                expected_manifest_sha256=result["data"]["manifest_sha256"],
                                scratch_parent=self.scratch,
                                target_dir=target or self.root / "restored", **kwargs)

    def storage_config(self):
        return {"format": fmt.STORAGE_FORMAT, "profile": "mounted-posix-v1",
                "storage_ref": "synthetic-logic-only", "mount_point": str(self.root),
                "mount_root": "/", "mount_source": "synthetic:/unused",
                "fs_type": "nfs4", "archive_root": str(self.archive)}

    def publish_snapshot(self, result, config=None):
        return recovery.publish(snapshot=result["data"]["snapshot_path"],
                                expected_manifest_sha256=result["data"]["manifest_sha256"],
                                scratch_parent=self.scratch,
                                storage_config=config or self.storage_config())

    def seed_rich(self):
        donor = self.root / "donor.sqlite"
        with initialize(donor) as ledger:
            ledger.execute(encode(create()))
            first, payloads = append()
            ledger.execute(encode(first), payloads=payloads)
            second, payloads = append("v2", REPORT_V2, ["version:quarterly-v1"], provenance=True)
            ledger.execute(encode(second), payloads=payloads)
            branch, payloads = append("branch", b"branch\n", ["version:quarterly-v1"])
            ledger.execute(encode(branch), payloads=payloads)
            merge, _ = append("merge", b"", ["version:quarterly-v2", "version:quarterly-branch"])
            entries = [{"entry_key": "report/v1", "blob_ref": "blob:quarterly-v1"},
                       {"entry_key": "report/v2", "blob_ref": "blob:quarterly-v2"}]
            merge["body"]["content_roots"] = [{"content_root_ref": "root:quarterly-merge",
                                                "kind": "manifest", "manifest_ref": "manifest:reports"}]
            merge["body"]["blobs"] = []
            merge["body"]["manifests"] = [{"manifest_ref": "manifest:reports", "entries": entries,
                                           "digest": manifest_digest(entries)}]
            ledger.execute(encode(merge), payloads={})
            self.bundle = ledger.export_bundle()
        self.import_request = import_request("before-snapshot")
        with open_ledger(self.database) as ledger:
            self.import_result = ledger.execute(encode(self.import_request), **import_kwargs(self.bundle))
            self.expected_summary = ledger.verify()
            self.expected_history = ledger.get_history(ARTIFACT)
            self.expected_operation = ledger.get_operation(SCOPE, "append_version", "append-v2")
        self.original_rows = table_rows(self.database)

    def reseal_metadata(self, generation, edit):
        manifest = json.loads((generation / "manifest.json").read_bytes())
        edit(manifest)
        raw = encode(manifest)
        (generation / "manifest.json").write_bytes(raw)
        expected = hashlib.sha256(raw).hexdigest()
        (generation / "COMMITTED.json").write_bytes(encode(fmt.make_marker(SNAPSHOT_ID, expected)))
        return expected

    def test_rich_lifecycle_all_tables_bytes_history_replay_and_new_write(self):
        self.seed_rich()
        before = self.database.read_bytes()
        created = self.create_snapshot()
        self.assertEqual(created["publication_state"], "published")
        self.assertEqual(created["data"]["summary"], self.expected_summary)
        self.assertEqual(self.database.read_bytes(), before)
        snapshot = Path(created["data"]["snapshot_path"])
        self.assertEqual({p.name for p in snapshot.iterdir()}, {"ledger.sqlite", "manifest.json", "COMMITTED.json"})
        self.assertEqual(self.verify_snapshot(created)["publication_state"], "not_applicable")
        published = self.publish_snapshot(created)
        remote = Path(published["data"]["snapshot_path"])
        for name in ("ledger.sqlite", "manifest.json", "COMMITTED.json"):
            self.assertEqual((snapshot / name).read_bytes(), (remote / name).read_bytes())
        self.assertEqual(self.verify_snapshot(published, storage_config=self.storage_config())["data"]["summary"],
                         self.expected_summary)
        restored = self.restore_snapshot(published, storage_config=self.storage_config())
        restored_db = Path(restored["data"]["database_path"])
        self.assertEqual(table_rows(restored_db), self.original_rows)
        self.assertEqual(file_hash(restored_db), created["data"]["database_sha256"])
        self.assertEqual({p.name for p in restored_db.parent.iterdir()}, {"ledger.sqlite"})
        checked = recovery.check_restore(target_dir=restored_db.parent,
                                         expected_database_sha256=created["data"]["database_sha256"],
                                         scratch_parent=self.scratch)
        self.assertEqual(checked["publication_state"], "not_applicable")
        self.assertEqual(checked["data"]["summary"], self.expected_summary)
        with open_ledger(restored_db) as ledger:
            self.assertEqual(ledger.get_history(ARTIFACT), self.expected_history)
            self.assertEqual(ledger.get_operation(SCOPE, "append_version", "append-v2"), self.expected_operation)
            self.assertEqual(ledger.read_blob("blob:quarterly-v1"), REPORT_V1)
            self.assertEqual(ledger.read_blob("blob:quarterly-v2"), REPORT_V2)
            self.assertEqual(ledger.read_blob("blob:quarterly-branch"), b"branch\n")
            replay = ledger.execute(encode(self.import_request), **import_kwargs(self.bundle))
            self.assertEqual(replay, dict(self.import_result, replayed=True))
            self.assertEqual(ledger.verify(), self.expected_summary)
            request, payloads = append("after-restore", b"new content\n", ["version:quarterly-merge"])
            ledger.execute(encode(request), payloads=payloads)
            self.assertEqual(ledger.read_blob("blob:quarterly-after-restore"), b"new content\n")
            self.assertEqual(ledger.verify()["counts"]["versions"], self.expected_summary["counts"]["versions"] + 1)
        self.assertEqual(table_rows(self.database), self.original_rows)
        self.assertEqual(table_rows(snapshot / "ledger.sqlite"), self.original_rows)
        self.assertEqual(table_rows(remote / "ledger.sqlite"), self.original_rows)

    def test_empty_ledger_create_restore_and_readonly_check(self):
        created = self.create_snapshot()
        restored = self.restore_snapshot(created)
        checked = recovery.check_restore(target_dir=Path(restored["data"]["database_path"]).parent,
                                         expected_database_sha256=created["data"]["database_sha256"],
                                         scratch_parent=self.scratch)
        self.assertEqual(checked["data"]["summary"]["verified_blob_count"], 0)
        self.assertTrue(all(count == 0 for count in checked["data"]["summary"]["counts"].values()))

    def test_create_existing_target_types_are_preserved(self):
        for index, kind in enumerate(("empty", "nonempty", "file", "dangling", "symlink")):
            identity = f"{index:032x}"
            target = self.output / identity
            if kind in ("empty", "nonempty"):
                target.mkdir()
                if kind == "nonempty":
                    (target / "keep").write_bytes(b"keep")
            elif kind == "file":
                target.write_bytes(b"keep")
            else:
                target.symlink_to(self.root / ("absent" if kind == "dangling" else "source.sqlite"))
            before = target.lstat()
            self.assert_error("TARGET_EXISTS", "not_applicable", recovery.create, db=self.database,
                              output_root=self.output, snapshot_id=identity, source_commit=SOURCE_COMMIT)
            self.assertEqual(target.lstat(), before)
            if kind == "nonempty":
                self.assertEqual((target / "keep").read_bytes(), b"keep")

    def test_restore_existing_target_types_are_preserved(self):
        created = self.create_snapshot()
        for index, kind in enumerate(("empty", "nonempty", "file", "dangling", "symlink")):
            target = self.root / f"target-{index}"
            if kind in ("empty", "nonempty"):
                target.mkdir()
                if kind == "nonempty":
                    (target / "ledger.sqlite-wal").write_bytes(b"keep")
            elif kind == "file":
                target.write_bytes(b"keep")
            else:
                target.symlink_to(self.root / ("absent" if kind == "dangling" else "scratch"))
            before = target.lstat()
            self.assert_error("TARGET_EXISTS", "not_applicable", recovery.restore,
                              snapshot=created["data"]["snapshot_path"], target_dir=target,
                              expected_manifest_sha256=created["data"]["manifest_sha256"], scratch_parent=self.scratch)
            self.assertEqual(target.lstat(), before)
            if kind == "nonempty":
                self.assertEqual((target / "ledger.sqlite-wal").read_bytes(), b"keep")

    def test_publish_existing_complete_and_incomplete_generations_preserved(self):
        created = self.create_snapshot()
        published = self.publish_snapshot(created)
        target = Path(published["data"]["snapshot_path"])
        before = {p.name: p.read_bytes() for p in target.iterdir()}
        for complete in (True, False):
            if not complete:
                (target / "COMMITTED.json").unlink()
                before.pop("COMMITTED.json")
            self.assert_error("TARGET_EXISTS", "not_applicable", recovery.publish,
                              snapshot=created["data"]["snapshot_path"], storage_config=self.storage_config(),
                              expected_manifest_sha256=created["data"]["manifest_sha256"], scratch_parent=self.scratch)
            self.assertEqual({p.name: p.read_bytes() for p in target.iterdir()}, before)

    def test_missing_extra_and_symlink_members_refused_without_mutation(self):
        created = self.create_snapshot()
        snapshot = Path(created["data"]["snapshot_path"])
        marker = (snapshot / "COMMITTED.json").read_bytes()
        (snapshot / "COMMITTED.json").unlink()
        self.assert_error("INCOMPLETE_SNAPSHOT", "not_applicable", recovery.verify,
                          snapshot=snapshot, expected_manifest_sha256=created["data"]["manifest_sha256"],
                          scratch_parent=self.scratch)
        (snapshot / "COMMITTED.json").write_bytes(marker)
        (snapshot / "extra").write_bytes(b"preserve")
        self.assert_error("INTEGRITY_FAILURE", "not_applicable", recovery.verify,
                          snapshot=snapshot, expected_manifest_sha256=created["data"]["manifest_sha256"],
                          scratch_parent=self.scratch)
        self.assertEqual((snapshot / "extra").read_bytes(), b"preserve")
        (snapshot / "extra").unlink()
        for member in ("ledger.sqlite", "manifest.json", "COMMITTED.json"):
            moved = self.root / (member + ".saved")
            (snapshot / member).rename(moved)
            (snapshot / member).symlink_to(moved)
            self.assert_error("INTEGRITY_FAILURE", "not_applicable", recovery.verify,
                              snapshot=snapshot, expected_manifest_sha256=created["data"]["manifest_sha256"],
                              scratch_parent=self.scratch)
            self.assertTrue((snapshot / member).is_symlink())
            (snapshot / member).unlink()
            moved.rename(snapshot / member)

    def test_extra_database_hardlink_refused_marker_hardlink_accepted(self):
        created = self.create_snapshot()
        snapshot = Path(created["data"]["snapshot_path"])
        os.link(snapshot / "COMMITTED.json", self.root / "marker-link")
        self.verify_snapshot(created)
        os.link(snapshot / "ledger.sqlite", self.root / "database-link")
        self.assert_error("INTEGRITY_FAILURE", "not_applicable", recovery.verify,
                          snapshot=snapshot, expected_manifest_sha256=created["data"]["manifest_sha256"],
                          scratch_parent=self.scratch)

    def test_database_byte_corruption_stops_restore_before_target_creation(self):
        created = self.create_snapshot()
        snapshot = Path(created["data"]["snapshot_path"])
        data = bytearray((snapshot / "ledger.sqlite").read_bytes())
        data[-1] ^= 1
        (snapshot / "ledger.sqlite").write_bytes(data)
        target = self.root / "restored"
        self.assert_error("INTEGRITY_FAILURE", "not_published", recovery.restore,
                          snapshot=snapshot, expected_manifest_sha256=created["data"]["manifest_sha256"],
                          target_dir=target, scratch_parent=self.scratch)
        self.assertFalse(target.exists())
        self.assertEqual((snapshot / "ledger.sqlite").read_bytes(), data)

    def test_correct_hash_cannot_hide_forged_summary_or_bad_payload(self):
        self.seed_rich()
        created = self.create_snapshot()
        snapshot = Path(created["data"]["snapshot_path"])
        expected = self.reseal_metadata(snapshot, lambda m: m["summary"]["counts"].__setitem__("artifacts", 2))
        self.assert_error("INTEGRITY_FAILURE", "not_applicable", recovery.verify,
                          snapshot=snapshot, expected_manifest_sha256=expected, scratch_parent=self.scratch)
        # Reset the valid summary, then alter a Blob and honestly re-hash the DB.
        with sqlite3.connect(snapshot / "ledger.sqlite") as connection:
            connection.execute("UPDATE payloads SET data=? WHERE blob_ref=?", (b"corrupt content", "blob:quarterly-v1"))
        def update(manifest):
            manifest["summary"] = self.expected_summary
            manifest["database"]["sha256"] = file_hash(snapshot / "ledger.sqlite")
            manifest["database"]["byte_length"] = (snapshot / "ledger.sqlite").stat().st_size
        expected = self.reseal_metadata(snapshot, update)
        self.assert_error("INTEGRITY_FAILURE", "not_applicable", recovery.verify,
                          snapshot=snapshot, expected_manifest_sha256=expected, scratch_parent=self.scratch)

    def test_expected_manifest_and_marker_hash_bindings_are_both_checked(self):
        created = self.create_snapshot()
        snapshot = Path(created["data"]["snapshot_path"])
        self.assert_error("INTEGRITY_FAILURE", "not_applicable", recovery.verify,
                          snapshot=snapshot, expected_manifest_sha256="0" * 64, scratch_parent=self.scratch)
        (snapshot / "COMMITTED.json").write_bytes(encode(fmt.make_marker(SNAPSHOT_ID, "0" * 64)))
        self.assert_error("INTEGRITY_FAILURE", "not_applicable", recovery.verify,
                          snapshot=snapshot, expected_manifest_sha256=created["data"]["manifest_sha256"],
                          scratch_parent=self.scratch)

    def test_check_restore_rejects_each_sidecar_including_dangling_links(self):
        created = self.create_snapshot()
        restored = self.restore_snapshot(created)
        target = Path(restored["data"]["database_path"]).parent
        before = (target / "ledger.sqlite").read_bytes()
        for name in ("ledger.sqlite-wal", "ledger.sqlite-shm", "ledger.sqlite-journal"):
            for dangling in (False, True):
                entry = target / name
                if dangling:
                    entry.symlink_to(target / "absent")
                else:
                    entry.write_bytes(b"preserve sidecar")
                self.assert_error("INTEGRITY_FAILURE", "not_applicable", recovery.check_restore,
                                  target_dir=target, expected_database_sha256=created["data"]["database_sha256"],
                                  scratch_parent=self.scratch)
                self.assertTrue(os.path.lexists(entry))
                self.assertEqual((target / "ledger.sqlite").read_bytes(), before)
                entry.unlink()

    def test_local_marker_link_known_no_effect_and_unsupported_are_not_published(self):
        before = file_hash(self.database)
        for index, (number, code) in enumerate(((errno.ENOSPC, "IO_ERROR"), (errno.EDQUOT, "IO_ERROR"),
                                               (errno.EXDEV, "UNSUPPORTED_STORAGE"))):
            identity = f"{index + 60:032x}"
            with self.subTest(errno=number):
                with patch.object(recovery.os, "link", side_effect=OSError(number, "injected")) as link:
                    error = self.assert_error(code, "not_published", recovery.create, db=self.database,
                                              output_root=self.output, snapshot_id=identity, source_commit=SOURCE_COMMIT)
                link.assert_called_once()
                self.assertEqual(error.stage, "publish")
                target = self.output / identity
                self.assertFalse(os.path.lexists(target / "COMMITTED.json"))
                self.assertEqual({p.name for p in target.iterdir()}, {"ledger.sqlite", "manifest.json"})
                self.assertEqual(file_hash(self.database), before)

    def test_restore_link_known_no_effect_is_not_published(self):
        created = self.create_snapshot()
        snapshot = Path(created["data"]["snapshot_path"])
        before = {p.name: file_hash(p) for p in snapshot.iterdir()}
        for number in (errno.ENOSPC, errno.EDQUOT):
            with self.subTest(errno=number):
                target = self.root / f"restored-{number}"
                with patch.object(recovery.os, "link", side_effect=OSError(number, "injected")) as link:
                    error = self.assert_error("IO_ERROR", "not_published", recovery.restore,
                                              snapshot=snapshot,
                                              expected_manifest_sha256=created["data"]["manifest_sha256"],
                                              target_dir=target, scratch_parent=self.scratch)
                link.assert_called_once()
                self.assertEqual(error.stage, "publish")
                self.assertTrue(target.is_dir())
                self.assertEqual(list(target.iterdir()), [])
                self.assertEqual({p.name: file_hash(p) for p in snapshot.iterdir()}, before)

    def test_local_quota_failure_cli_retains_unpublished_state_and_exit_code(self):
        created = self.create_snapshot()
        argv_by_operation = {
            "create": ["--db", str(self.database), "--output-root", str(self.output),
                       "--snapshot-id", "f" * 32, "--source-commit", SOURCE_COMMIT],
            "restore": ["--snapshot", created["data"]["snapshot_path"],
                        "--expected-manifest-sha256", created["data"]["manifest_sha256"],
                        "--target-dir", str(self.root / "cli-restored"), "--scratch-parent", str(self.scratch)],
        }
        for operation, argv in argv_by_operation.items():
            with self.subTest(operation=operation):
                output_bytes, diagnostic = io.BytesIO(), io.StringIO()
                output = io.TextIOWrapper(output_bytes, encoding="utf-8")
                self.addCleanup(output.close)
                with patch.object(recovery.os, "link", side_effect=OSError(errno.EDQUOT, "injected")) as link:
                    with patch.object(snapshot_cli.sys, "stdout", output), patch.object(snapshot_cli.sys, "stderr", diagnostic):
                        code = snapshot_cli.main([operation, *argv])
                link.assert_called_once()
                self.assertEqual(code, 9)
                self.assertEqual(diagnostic.getvalue(), "")
                self.assertEqual(output_bytes.getvalue().count(b"\n"), 1)
                response = json.loads(output_bytes.getvalue())
                self.assertEqual(response["operation"], operation)
                self.assertEqual(response["error"]["code"], "IO_ERROR")
                self.assertEqual(response["error"]["stage"], "publish")
                self.assertEqual(response["publication_state"], "not_published")

    def test_marker_postlink_sync_failure_unknown_but_readonly_verify_possible(self):
        original = storage.Directory.fsync
        def fail_after_marker(directory):
            if directory.path.name == SNAPSHOT_ID and (directory.path / "COMMITTED.json").exists():
                raise OSError(errno.EIO, "injected final directory sync failure")
            return original(directory)
        with patch.object(storage.Directory, "fsync", fail_after_marker):
            self.assert_error("PUBLICATION_UNKNOWN", "unknown", recovery.create,
                              db=self.database, output_root=self.output,
                              snapshot_id=SNAPSHOT_ID, source_commit=SOURCE_COMMIT)
        snapshot = self.output / SNAPSHOT_ID
        expected = file_hash(snapshot / "manifest.json")
        verified = recovery.verify(snapshot=snapshot, expected_manifest_sha256=expected,
                                   scratch_parent=self.scratch)
        self.assertEqual(verified["publication_state"], "not_applicable")
        self.assertEqual({p.name for p in snapshot.iterdir()}, {"ledger.sqlite", "manifest.json", "COMMITTED.json"})

    def test_restore_postlink_sync_failure_unknown_and_target_preserved(self):
        created = self.create_snapshot()
        target = self.root / "restored"
        original = storage.Directory.fsync
        def fail_final(directory):
            if directory.path == target:
                raise OSError(errno.EIO, "injected final directory sync failure")
            return original(directory)
        with patch.object(storage.Directory, "fsync", fail_final):
            self.assert_error("PUBLICATION_UNKNOWN", "unknown", recovery.restore,
                              snapshot=created["data"]["snapshot_path"],
                              expected_manifest_sha256=created["data"]["manifest_sha256"],
                              target_dir=target, scratch_parent=self.scratch)
        self.assertEqual({p.name for p in target.iterdir()}, {"ledger.sqlite"})
        self.assertEqual(file_hash(target / "ledger.sqlite"), created["data"]["database_sha256"])
        recovery.check_restore(target_dir=target,
                               expected_database_sha256=created["data"]["database_sha256"], scratch_parent=self.scratch)

    def test_restore_cleanup_failure_preserves_two_links_and_check_is_readonly(self):
        created = self.create_snapshot()
        target = self.root / "restored"
        original = storage._remove_owned_contents
        def fail_cleanup(directory):
            if directory.path.parent == target and (target / "ledger.sqlite").exists():
                raise OSError(errno.EACCES, "injected temporary cleanup failure")
            return original(directory)
        with patch.object(storage, "_remove_owned_contents", fail_cleanup):
            self.assert_error("PUBLICATION_UNKNOWN", "unknown", recovery.restore,
                              snapshot=created["data"]["snapshot_path"],
                              expected_manifest_sha256=created["data"]["manifest_sha256"],
                              target_dir=target, scratch_parent=self.scratch)
        self.assertEqual((target / "ledger.sqlite").stat().st_nlink, 2)
        leftovers = {p.name for p in target.iterdir()}
        # Other entries are neither walked nor cleaned by check_restore.
        (target / "other-link").symlink_to(self.root / "does-not-exist")
        checked = recovery.check_restore(target_dir=target,
                                         expected_database_sha256=created["data"]["database_sha256"],
                                         scratch_parent=self.scratch)
        self.assertEqual(checked["status"], "OK")
        self.assertEqual({p.name for p in target.iterdir()}, leftovers | {"other-link"})
        self.assertEqual((target / "ledger.sqlite").stat().st_nlink, 2)

    def test_restore_success_removes_temporary_before_final_sync(self):
        created = self.create_snapshot()
        target = self.root / "restored"
        original = storage.Directory.fsync
        checked = []
        def observe(directory):
            if directory.path == target:
                checked.append({p.name for p in target.iterdir()})
            return original(directory)
        with patch.object(storage.Directory, "fsync", observe):
            self.restore_snapshot(created, target)
        self.assertTrue(checked)
        self.assertTrue(all(names == {"ledger.sqlite"} for names in checked))

    def test_remote_link_created_but_error_remains_unknown_and_archive_usable(self):
        original = os.link
        for number in (errno.EIO, errno.EDQUOT):
            with self.subTest(errno=number):
                identity = f"{number:032x}"
                created = self.create_snapshot(identity)
                def created_but_error(*args, **kwargs):
                    original(*args, **kwargs)
                    raise OSError(number, "synthetic server committed but client lost acknowledgement")
                with patch.object(recovery.os, "link", created_but_error):
                    error = self.assert_error("PUBLICATION_UNKNOWN", "unknown", recovery.publish,
                                              snapshot=created["data"]["snapshot_path"], storage_config=self.storage_config(),
                                              expected_manifest_sha256=created["data"]["manifest_sha256"], scratch_parent=self.scratch)
                self.assertEqual(error.stage, "publish")
                destination = self.archive / identity
                self.assertEqual({p.name for p in destination.iterdir()}, {"ledger.sqlite", "manifest.json", "COMMITTED.json"})
                result = recovery.verify(snapshot=destination, storage_config=self.storage_config(),
                                         expected_manifest_sha256=created["data"]["manifest_sha256"], scratch_parent=self.scratch)
                self.assertEqual(result["publication_state"], "not_applicable")

    def test_source_metadata_change_after_validation_cannot_change_published_copy(self):
        created = self.create_snapshot()
        source = Path(created["data"]["snapshot_path"])
        original = recovery._seal_archive
        expected_bytes = (source / "ledger.sqlite").read_bytes()
        def change_after_validation(parent, private, manifest, raw, marker_raw, context, **kwargs):
            (source / "ledger.sqlite").write_bytes(b"source changed after validation")
            return original(parent, private, manifest, raw, marker_raw, context, **kwargs)
        with patch.object(recovery, "_seal_archive", change_after_validation):
            published = self.publish_snapshot(created)
        self.assertEqual((Path(published["data"]["snapshot_path"]) / "ledger.sqlite").read_bytes(), expected_bytes)

    def test_configuration_capture_precedes_filesystem_effects_and_caller_mutation(self):
        created = self.create_snapshot()
        original = storage.open_directory
        caller = self.storage_config()
        other = self.root / "not-the-authorized-target"
        def mutate_once(value, **kwargs):
            caller["archive_root"] = str(other)
            return original(value, **kwargs)
        with patch.object(storage, "open_directory", mutate_once):
            published = self.publish_snapshot(created, caller)
        self.assertEqual(Path(published["data"]["snapshot_path"]).parent, self.archive)
        self.assertFalse(other.exists())
        malformed = dict(self.storage_config(), archive_root=Path(self.archive))
        with patch.object(storage, "open_directory", side_effect=AssertionError("must not touch filesystem")):
            self.assert_error("INVALID_INPUT", "not_published", recovery.publish,
                              snapshot=created["data"]["snapshot_path"], storage_config=malformed,
                              expected_manifest_sha256=created["data"]["manifest_sha256"], scratch_parent=self.scratch)

    def test_missing_output_root_is_not_created_and_signature_errors_are_python(self):
        absent = self.root / "absent-output"
        self.assert_error("NOT_FOUND", "not_published", recovery.create, db=self.database,
                          output_root=absent, snapshot_id=SNAPSHOT_ID, source_commit=SOURCE_COMMIT)
        self.assertFalse(absent.exists())
        for function in (recovery.create, recovery.publish, recovery.verify, recovery.restore, recovery.check_restore):
            with self.assertRaises(TypeError):
                function()
            with self.assertRaises(TypeError):
                function(unknown="not-a-keyword")

    def test_scratch_and_restore_targets_cannot_write_into_readonly_inputs(self):
        created = self.create_snapshot()
        snapshot = Path(created["data"]["snapshot_path"])
        before = {p.name: p.read_bytes() for p in snapshot.iterdir()}
        for scratch in (snapshot, snapshot / "nested"):
            for function, extras, state in (
                (recovery.verify, {}, "not_applicable"),
                (recovery.publish, {"storage_config": self.storage_config()}, "not_published"),
                (recovery.restore, {"target_dir": self.root / "new"}, "not_published"),
            ):
                with self.subTest(operation=function.__name__, scratch=scratch):
                    self.assert_error("INVALID_INPUT", state, function, snapshot=snapshot,
                                      expected_manifest_sha256=created["data"]["manifest_sha256"],
                                      scratch_parent=scratch, **extras)
            self.assertEqual({p.name: p.read_bytes() for p in snapshot.iterdir()}, before)
        self.assert_error("INVALID_INPUT", "not_published", recovery.restore,
                          snapshot=snapshot, target_dir=snapshot / "restored",
                          expected_manifest_sha256=created["data"]["manifest_sha256"], scratch_parent=self.scratch)
        restored = self.restore_snapshot(created)
        target = Path(restored["data"]["database_path"]).parent
        for scratch in (target, target / "nested"):
            self.assert_error("INVALID_INPUT", "not_applicable", recovery.check_restore,
                              target_dir=target, expected_database_sha256=created["data"]["database_sha256"],
                              scratch_parent=scratch)
        self.assertEqual({p.name for p in target.iterdir()}, {"ledger.sqlite"})
        self.assertEqual({p.name: p.read_bytes() for p in snapshot.iterdir()}, before)

    def test_scratch_alias_with_same_directory_identity_is_rejected(self):
        created = self.create_snapshot()
        snapshot = Path(created["data"]["snapshot_path"])
        original = storage.open_directory
        alias_target = snapshot
        def return_alias(value, **kwargs):
            # Model a same-inode alias while retaining actual directory handles.
            return original(alias_target if Path(value) == self.scratch else value, **kwargs)
        with patch.object(storage, "open_directory", return_alias):
            self.assert_error("INVALID_INPUT", "not_applicable", recovery.verify,
                              snapshot=snapshot, expected_manifest_sha256=created["data"]["manifest_sha256"],
                              scratch_parent=self.scratch)
        restored = self.restore_snapshot(created)
        alias_target = Path(restored["data"]["database_path"]).parent
        with patch.object(storage, "open_directory", return_alias):
            self.assert_error("INVALID_INPUT", "not_applicable", recovery.check_restore,
                              target_dir=alias_target, expected_database_sha256=created["data"]["database_sha256"],
                              scratch_parent=self.scratch)
        self.assertEqual({p.name for p in snapshot.iterdir()}, {"ledger.sqlite", "manifest.json", "COMMITTED.json"})
        self.assertEqual({p.name for p in alias_target.iterdir()}, {"ledger.sqlite"})

    def test_real_default_classification_rejects_unsupported_sandbox_filesystem(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            mount = storage._mount_for(self.root, storage._fd_mount_id(fd))
        finally:
            os.close(fd)
        if mount.fs_type in storage.LOCAL_FILESYSTEMS:
            self.skipTest("Current test directory is on a candidate local filesystem, not overlay/tmpfs.")
        before = self.database.read_bytes()
        with patch.object(storage, "_classify", _REAL_CLASSIFY):
            self.assert_error("UNSUPPORTED_STORAGE", "not_published", recovery.create,
                              db=self.database, output_root=self.output,
                              snapshot_id=SNAPSHOT_ID, source_commit=SOURCE_COMMIT)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.database.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
