"""Synthetic harness safety tests; these do not execute or certify a NAS."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "acceptance" / "a2_nas_exercise.py"
_SPEC = importlib.util.spec_from_file_location("a2_nas_exercise", _TOOL)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


class NasExerciseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.run = tool.OwnedRun(self.parent)
        self.addCleanup(self.run.close)

    def test_removes_only_owned_disposable_and_keeps_evidence_and_other_data(self):
        outside = self.parent / "real-data"
        outside.write_bytes(b"keep")
        self.run.write_new("expected.json", b"{}")
        disposable = self.run.path / "disposable"
        (disposable / "nested").mkdir()
        (disposable / "nested" / "source.sqlite").write_bytes(b"synthetic")
        self.run.remove_disposable()
        self.assertFalse(disposable.exists())
        self.assertEqual(outside.read_bytes(), b"keep")
        self.assertEqual((self.run.path / "expected.json").read_bytes(), b"{}")
        self.assertTrue((self.run.path / "OWNED.json").exists())

    def test_symlink_anywhere_refuses_before_deleting_any_file(self):
        disposable = self.run.path / "disposable"
        original = disposable / "a-first.txt"
        original.write_bytes(b"keep")
        outside = self.parent / "external"
        outside.mkdir()
        (outside / "real.sqlite").write_bytes(b"keep external")
        (disposable / "z-link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(tool.ExerciseError):
            self.run.remove_disposable()
        self.assertEqual(original.read_bytes(), b"keep")
        self.assertEqual((outside / "real.sqlite").read_bytes(), b"keep external")

    def test_hardlink_refuses_without_removing_either_name(self):
        outside = self.parent / "external.sqlite"
        outside.write_bytes(b"external")
        link = self.run.path / "disposable" / "linked.sqlite"
        os.link(outside, link)
        with self.assertRaises(tool.ExerciseError):
            self.run.remove_disposable()
        self.assertEqual(outside.read_bytes(), b"external")
        self.assertEqual(link.stat().st_nlink, 2)

    def test_tampered_ownership_marker_prevents_cleanup(self):
        (self.run.path / "OWNED.json").write_bytes(b"different")
        with self.assertRaises(tool.ExerciseError):
            self.run.remove_disposable()
        self.assertTrue((self.run.path / "disposable").is_dir())

    def test_replaced_disposable_directory_prevents_cleanup(self):
        disposable = self.run.path / "disposable"
        disposable.rename(self.run.path / "original")
        disposable.mkdir()
        (disposable / "unrelated").write_bytes(b"keep")
        with self.assertRaises(tool.ExerciseError):
            self.run.remove_disposable()
        self.assertEqual((disposable / "unrelated").read_bytes(), b"keep")

    def test_nested_mount_identity_change_prevents_cleanup(self):
        disposable = self.run.path / "disposable"
        nested = disposable / "nested"
        nested.mkdir()
        protected = nested / "database.sqlite"
        protected.write_bytes(b"keep")
        original = tool.mount_id

        def changed(fd):
            return original(fd) + 1 if Path(os.readlink(f"/proc/self/fd/{fd}")) == nested else original(fd)

        with patch.object(tool, "mount_id", changed):
            with self.assertRaises(tool.ExerciseError):
                self.run.remove_disposable()
        self.assertEqual(protected.read_bytes(), b"keep")

    def test_nonisolated_interpreter_rejected_before_wheel_read(self):
        if not __import__("sys").flags.isolated:
            with self.assertRaises(tool.ExerciseError):
                tool.installed_identity(self.parent / "missing.whl")

    def test_new_run_collision_never_adopts_existing_directory(self):
        name = "a2-nas-" + "a" * 32
        existing = self.parent / name
        existing.mkdir()
        (existing / "real.txt").write_bytes(b"keep")
        with patch.object(tool.secrets, "token_hex", return_value="a" * 32):
            with self.assertRaises(FileExistsError):
                tool.OwnedRun(self.parent)
        self.assertEqual(list(p.name for p in existing.iterdir()), ["real.txt"])

    def test_directory_race_helper_has_one_winner_and_removes_only_probe(self):
        # A local logic test, explicitly not a NAS or durability attestation.
        before = set(os.listdir(self.run.fd))
        archive = SimpleNamespace(path=self.run.path, fd=self.run.fd, check=self.run.check,
                                  fsync=lambda: os.fsync(self.run.fd))
        result = tool.concurrent_directory_probe(archive)
        self.assertEqual(result, {"processes": 2, "created": 1, "already_exists": 1})
        self.assertEqual(set(os.listdir(self.run.fd)), before)

    def test_unsupported_local_storage_never_creates_new_run(self):
        from infra_artifact_ledger.snapshot_common import RecoveryError
        from infra_artifact_ledger import snapshot_storage
        config = self.parent / "private-storage.json"
        config.write_bytes(tool.encode({"format": "infra-artifact-ledger-storage/v1",
            "profile": "mounted-posix-v1", "storage_ref": "acceptance", "mount_point": "/nas",
            "mount_root": "/", "mount_source": "synthetic:/archive", "fs_type": "nfs4",
            "archive_root": "/nas/archive"}))
        before = set(self.parent.iterdir())
        args = SimpleNamespace(source_commit="a" * 40, wheel=self.parent / "wheel.whl",
                               storage_config=config, local_parent=self.parent)
        with patch.object(tool, "installed_identity", return_value={}):
            with patch.object(snapshot_storage, "open_directory",
                              side_effect=RecoveryError("UNSUPPORTED_STORAGE", "fixture")):
                with self.assertRaises(RecoveryError):
                    tool.exercise(args)
        self.assertEqual(set(self.parent.iterdir()), before)

    def test_self_contained_fixture_has_branch_manifest_provenance_import_and_replay(self):
        from infra_artifact_ledger import open as open_ledger
        source = self.run.path / "disposable" / "source.sqlite"
        donor = self.run.path / "disposable" / "donor.sqlite"
        expected = tool.populate(source, donor)
        counts = expected["summary"]["counts"]
        self.assertEqual(counts["versions"], 3)
        self.assertEqual(counts["manifests"], 1)
        self.assertEqual(counts["provenance_links"], 2)
        self.assertEqual(counts["import_receipts"], 1)
        self.assertEqual(len(expected["image"]["payloads"]), 3)
        with open_ledger(source) as ledger:
            replay = ledger.execute(tool.encode(expected["replay_request"]))
            self.assertEqual(replay, dict(expected["replay_result"], replayed=True))
        self.assertEqual(tool.database_image(source), expected["image"])
        self.run.remove_disposable()
        self.assertFalse(source.exists())
        self.assertFalse(donor.exists())

    def exercise_args(self):
        archive = self.parent / "mock-archive"
        archive.mkdir()
        local = self.parent / "mock-local"
        local.mkdir()
        config = self.parent / "mock-storage.json"
        config.write_bytes(tool.encode({"format": "infra-artifact-ledger-storage/v1",
            "profile": "mounted-posix-v1", "storage_ref": "synthetic", "mount_point": str(self.parent),
            "mount_root": "/", "mount_source": "synthetic:/archive", "fs_type": "nfs4",
            "archive_root": str(archive)}))
        return SimpleNamespace(source_commit="a" * 40, wheel=self.parent / "unused.whl",
                               storage_config=config, local_parent=local)

    def test_mocked_lifecycle_removes_local_recovery_sources_before_restore(self):
        # Only orchestration is under test: filesystem classification and child
        # isolation are mocked. This result is not a NAS/installed-wheel result.
        from infra_artifact_ledger import recovery, snapshot_storage
        args = self.exercise_args()
        calls = []

        def inline(argv, cwd, records=None):
            operation, *remaining = argv
            options = {remaining[i][2:].replace("-", "_"): remaining[i + 1]
                       for i in range(0, len(remaining), 2)}
            options["storage_config"] = json.loads(Path(options["storage_config"]).read_text())
            if operation == "restore":
                self.assertFalse(os.path.lexists(cwd / "disposable"))
                self.assertTrue(str(options["snapshot"]).startswith(str(self.parent / "mock-archive")))
            calls.append(operation)
            return getattr(recovery, operation)(**options)

        with patch.object(tool, "installed_identity", return_value={"mode": "MOCK"}), \
                patch.object(snapshot_storage, "_classify"), patch.object(tool, "child_cli", side_effect=inline):
            result = tool.exercise(args)
        self.assertEqual(calls, ["verify", "restore"])
        self.assertEqual(result["status"], "PASS")
        run = args.local_parent / result["run_id"]
        report = json.loads((run / "report.json").read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertTrue((run / "restored" / "ledger.sqlite").is_file())
        self.assertFalse((run / "disposable").exists())
        self.assertTrue((self.parent / "mock-archive" / result["run_id"]).is_dir())

    def test_failed_independent_nas_verify_preserves_all_local_recovery_sources(self):
        from infra_artifact_ledger import snapshot_storage
        args = self.exercise_args()
        with patch.object(tool, "installed_identity", return_value={"mode": "MOCK"}), \
                patch.object(snapshot_storage, "_classify"), \
                patch.object(tool, "child_cli", side_effect=tool.ExerciseError("fixture failure")):
            with self.assertRaises(tool.ExerciseError):
                tool.exercise(args)
        runs = list(args.local_parent.iterdir())
        self.assertEqual(len(runs), 1)
        self.assertTrue((runs[0] / "disposable" / "source.sqlite").is_file())
        self.assertTrue((runs[0] / "disposable" / "donor.sqlite").is_file())
        self.assertTrue(any((runs[0] / "disposable" / "snapshots").iterdir()))
        report = json.loads((runs[0] / "report.json").read_text())
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["failure"]["stage"], "independent_nas_verify")


if __name__ == "__main__":
    unittest.main()
