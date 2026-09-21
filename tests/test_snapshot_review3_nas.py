"""NAS acceptance fixture semantics; local logic only, never real NAS proof."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from infra_artifact_ledger import open as open_ledger


_TOOL = Path(__file__).resolve().parents[1] / "tools" / "acceptance" / "a2_nas_exercise.py"
_SPEC = importlib.util.spec_from_file_location("a2_nas_review3", _TOOL)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


class NasAcceptanceCoverageReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)

    def test_fixture_contains_multi_parent_version_and_shared_content(self):
        source, donor = self.parent / "source.sqlite", self.parent / "donor.sqlite"
        expected = tool.populate(source, donor)
        with open_ledger(source) as ledger:
            merged = ledger.get_record("version", "version:nas-merged")
            self.assertIsNotNone(merged, "The normative NAS fixture requires multiple parents")
            self.assertEqual(merged["parent_version_refs"], ["version:nas-left", "version:nas-right"])
            base = ledger.get_record("version", "version:nas-base")
            self.assertEqual(merged["content_root_ref"], base["content_root_ref"])
            self.assertEqual(ledger.verify(), expected["summary"])
        self.assertEqual(expected["summary"]["counts"]["versions"], 4)
        self.assertEqual(expected["summary"]["counts"]["content_roots"], 3)
        self.assertEqual(expected["summary"]["counts"]["blobs"], 3)

    def exercise_args(self):
        archive, local = self.parent / "mock-archive", self.parent / "mock-local"
        archive.mkdir()
        local.mkdir()
        config = self.parent / "mock-storage.json"
        config.write_bytes(tool.encode({"format": "infra-artifact-ledger-storage/v1",
            "profile": "mounted-posix-v1", "storage_ref": "synthetic", "mount_point": str(self.parent),
            "mount_root": "/", "mount_source": "synthetic:/archive", "fs_type": "nfs4",
            "archive_root": str(archive)}))
        return SimpleNamespace(source_commit="a" * 40, wheel=self.parent / "unused.whl",
                               storage_config=config, local_parent=local)

    def test_restored_database_accepts_new_version_with_content_and_parent(self):
        # Classification and child isolation are mocked; actual DB, snapshot,
        # publish/restore bytes and the final A1 append operation remain real.
        from infra_artifact_ledger import recovery, snapshot_storage
        args = self.exercise_args()

        def inline(argv, cwd, records=None):
            options = {argv[i][2:].replace("-", "_"): argv[i + 1] for i in range(1, len(argv), 2)}
            options["storage_config"] = json.loads(Path(options["storage_config"]).read_text())
            if argv[0] == "restore":
                self.assertFalse((cwd / "disposable").exists())
            return getattr(recovery, argv[0])(**options)

        with patch.object(tool, "installed_identity", return_value={"mode": "MOCK"}), \
                patch.object(snapshot_storage, "_classify"), patch.object(tool, "child_cli", side_effect=inline):
            result = tool.exercise(args)
        self.assertEqual(result["status"], "PASS")
        run = args.local_parent / result["run_id"]
        expected = json.loads((run / "expected.json").read_text())
        with open_ledger(run / "restored" / "ledger.sqlite") as ledger:
            appended = ledger.get_record("version", "version:nas-after-restore")
            self.assertIsNotNone(appended, "A new Artifact does not prove appending a restored Version")
            self.assertEqual(appended["artifact_id"], "artifact:nas-seed")
            self.assertEqual(appended["parent_version_refs"], ["version:nas-merged"])
            root = ledger.get_record("content_root", appended["content_root_ref"])
            self.assertEqual(root["blob_ref"], "blob:nas-after-restore")
            self.assertEqual(ledger.read_blob(root["blob_ref"]), "NAS 恢复后的新增版本\n".encode())
            self.assertEqual(ledger.verify()["counts"]["versions"], expected["summary"]["counts"]["versions"] + 1)
            self.assertEqual(ledger.verify()["counts"]["artifacts"], expected["summary"]["counts"]["artifacts"])


if __name__ == "__main__":
    unittest.main()
