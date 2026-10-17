"""The installed consumer acceptance must not silently lose its checks."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InstalledWalkthroughStartupTests(unittest.TestCase):
    def test_optimized_modes_stop_before_reading_inputs_or_creating_fixtures(self):
        script = Path(__file__).with_name("installed_walkthrough.py")
        with tempfile.TemporaryDirectory() as root:
            missing_repo = str(Path(root) / "does-not-exist")
            for flag in ("-O", "-OO"):
                with self.subTest(flag=flag):
                    run = subprocess.run(
                        [sys.executable, "-I", flag, str(script), "--repo", missing_repo],
                        cwd=root, capture_output=True, timeout=10,
                    )
                    self.assertNotEqual(run.returncode, 0)
                    self.assertEqual(run.stdout, b"")
                    self.assertIn(b"requires assertions", run.stderr)
                    self.assertNotIn(b"Traceback", run.stderr)
                    self.assertEqual(list(Path(root).iterdir()), [])

    def test_nonisolated_invocation_stops_before_reading_inputs(self):
        script = Path(__file__).with_name("installed_walkthrough.py")
        run = subprocess.run([sys.executable, str(script), "--repo", "does-not-exist"],
                             capture_output=True, timeout=10)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(run.stdout, b"")
        self.assertIn(b"with -I", run.stderr)
        self.assertNotIn(b"Traceback", run.stderr)


if __name__ == "__main__":
    unittest.main()
