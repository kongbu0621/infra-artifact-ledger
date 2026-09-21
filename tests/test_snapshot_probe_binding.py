"""LOGIC_ONLY NAS probe binding tests with two real child processes.

Only filesystem classification is mocked. These tests do not certify a NAS or
physical mount loss; directory replacement models a changed pathname binding.
"""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError


_TOOL = Path(__file__).resolve().parents[1] / "tools" / "acceptance" / "a2_nas_exercise.py"
_SPEC = importlib.util.spec_from_file_location("a2_nas_probe_binding", _TOOL)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor-bound storage")
class NasProbeBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="a2-probe-binding-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive_path = self.root / "archive"
        self.archive_path.mkdir()
        classification = patch.object(storage, "_classify", return_value=None)
        classification.start()
        self.addCleanup(classification.stop)
        self.archive = storage.open_directory(self.archive_path, budget=Budget())
        self.addCleanup(self.archive.close)
        self.processes = []
        self.popen = tool.subprocess.Popen

    def launch(self, *args, **kwargs):
        process = self.popen(*args, **kwargs)
        self.processes.append(process)
        return process

    def assert_children_closed(self):
        for process in self.processes:
            self.assertIsNotNone(process.poll(), "A probe child is still running")
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    self.assertTrue(pipe.closed, "A probe pipe was not closed")

    def test_two_children_compete_using_noninheritable_parent_descriptor(self):
        (self.archive_path / "unrelated").write_bytes(b"keep")
        self.assertFalse(os.get_inheritable(self.archive.fd))
        with patch.object(tool.subprocess, "Popen", side_effect=self.launch):
            result = tool.concurrent_directory_probe(self.archive)
        self.assertEqual(result, {"processes": 2, "created": 1, "already_exists": 1})
        self.assertEqual(len(self.processes), 2)
        self.assertEqual([process.returncode for process in self.processes], [0, 0])
        self.assert_children_closed()
        self.assertFalse(os.get_inheritable(self.archive.fd))
        self.assertEqual(os.listdir(self.archive.fd), ["unrelated"])
        self.assertEqual((self.archive_path / "unrelated").read_bytes(), b"keep")

    def test_replacement_after_initial_check_receives_no_child_writes(self):
        original_path = self.root / "original-archive"

        def replace_then_launch(*args, **kwargs):
            if not self.processes:
                self.archive_path.rename(original_path)
                self.archive_path.mkdir()
                (self.archive_path / "unrelated").write_bytes(b"keep replacement")
            return self.launch(*args, **kwargs)

        with patch.object(tool.subprocess, "Popen", side_effect=replace_then_launch):
            with self.assertRaises(RecoveryError) as caught:
                tool.concurrent_directory_probe(self.archive)
        self.assertEqual(caught.exception.code, "IO_ERROR")
        self.assertEqual(len(self.processes), 2)
        self.assertEqual([process.returncode for process in self.processes], [0, 0])
        self.assert_children_closed()
        self.assertEqual(os.listdir(self.archive_path), ["unrelated"])
        self.assertEqual((self.archive_path / "unrelated").read_bytes(), b"keep replacement")
        # Binding failure stops cleanup; only the originally held directory
        # contains the known synthetic probe. The replacement remains untouched.
        members = os.listdir(self.archive.fd)
        self.assertEqual(len(members), 1)
        self.assertTrue(members[0].startswith("mkdir-race-"))
        self.assertEqual(list(original_path.iterdir()), [original_path / members[0]])
        self.assertFalse(os.get_inheritable(self.archive.fd))

    def test_second_spawn_failure_reaps_waiting_child_and_preserves_archive(self):
        failure = OSError("synthetic second spawn failure")

        def fail_second(*args, **kwargs):
            if self.processes:
                raise failure
            return self.launch(*args, **kwargs)

        with patch.object(tool.subprocess, "Popen", side_effect=fail_second):
            with self.assertRaises(OSError) as caught:
                tool.concurrent_directory_probe(self.archive)
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(self.processes), 1)
        self.assertNotEqual(self.processes[0].returncode, 0)
        self.assert_children_closed()
        self.assertEqual(os.listdir(self.archive.fd), [])
        self.assertFalse(os.get_inheritable(self.archive.fd))


if __name__ == "__main__":
    unittest.main()
