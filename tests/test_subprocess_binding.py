"""Subprocess acceptance must exercise the same package as its parent."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


class SubprocessBindingTests(unittest.TestCase):
    def exercise(self, filename, class_name, method):
        with tempfile.TemporaryDirectory(prefix="ledger-test-binding-") as directory:
            root = Path(directory)
            copied_tests = root / "tests"
            copied_tests.mkdir()
            source = root / "src" / "infra_artifact_ledger"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text(
                "raise RuntimeError('adjacent source must not replace the active package')\n",
                encoding="utf-8",
            )
            for name in (filename, "acceptance_helpers.py"):
                shutil.copyfile(Path(__file__).with_name(name), copied_tests / name)
            spec = importlib.util.spec_from_file_location("binding_subject", copied_tests / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            case = getattr(module, class_name)(method)
            result = unittest.TestResult()
            # A parent's isolated interpreter does not clear a child's env.
            # Neither inherited PYTHONPATH nor this test's adjacent src may
            # redirect a subprocess away from the already imported package.
            with patch.dict(os.environ, {"PYTHONPATH": str(root / "src")}):
                case.run(result)
            self.assertEqual(result.testsRun, 1)
            self.assertEqual(result.skipped, [])
            self.assertTrue(result.wasSuccessful(), result.errors + result.failures)

    def test_cli_uses_active_package_with_adjacent_source_trap(self):
        self.exercise("test_cli.py", "CLITests",
                      "test_control_character_key_write_query_and_replay")

    def test_output_paths_use_active_package_with_adjacent_source_trap(self):
        self.exercise("test_cli_output_paths.py", "CLIOutputPathTests",
                      "test_export_preflights_both_outputs_without_partial_publication")

    def test_acceptance_workers_use_active_package_with_adjacent_source_trap(self):
        self.exercise("test_acceptance.py", "PublicLifecycleTests",
                      "test_new_processes_write_then_restart_and_read_exact_content")


if __name__ == "__main__":
    unittest.main()
