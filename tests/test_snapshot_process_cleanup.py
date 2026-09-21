"""Real synthetic child lifetimes and fault receipts; no NAS operations."""
import errno
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


_TOOL = Path(__file__).resolve().parents[1] / "tools/acceptance/a2_nas_exercise.py"
_SPEC = importlib.util.spec_from_file_location("a2_process_cleanup", _TOOL)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


class FaultyPipe:
    def __init__(self, raw, error):
        self.raw, self.error = raw, error
        self.close_calls = 0

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def close(self):
        self.close_calls += 1
        self.raw.close()
        raise self.error


class BrokenInput:
    def __init__(self, raw, error):
        self.raw, self.error = raw, error

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def write(self, value):
        raise self.error


class ChildCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a2-child-cleanup-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.children = []
        self.addCleanup(self.cleanup_children)

    def cleanup_children(self):
        for child in self.children:
            if child.poll() is None:
                subprocess.Popen.kill(child)
            subprocess.Popen.wait(child, timeout=10)
            for name in ("stdin", "stdout", "stderr"):
                pipe = getattr(child, name)
                if pipe is not None:
                    (pipe.raw if isinstance(pipe, (FaultyPipe, BrokenInput)) else pipe).close()

    def assert_released(self, child):
        self.assertIsNotNone(child.poll())
        for name in ("stdin", "stdout", "stderr"):
            pipe = getattr(child, name)
            if pipe is not None:
                self.assertTrue(pipe.closed, name + " leaked")
                if isinstance(pipe, FaultyPipe):
                    self.assertEqual(pipe.close_calls, 1, name + " was retried")

    def invoke(self, *, valid, fault, outer_exception=False):
        actual = subprocess.Popen
        secondary = OSError(errno.EIO, "synthetic cleanup failure after release")
        response = {"protocol": "infra-artifact-ledger-recovery/v1", "status": "OK",
                    "operation": "verify", "publication_state": "not_applicable", "data": {}}
        raw = json.dumps(response if valid else {}) + "\n"
        calls = []

        def spawn(*args, **kwargs):
            child = actual([sys.executable, "-I", "-c", "import sys; sys.stdout.write(" + repr(raw) + ")"], **kwargs)
            self.children.append(child)
            if fault in ("stdout", "stderr"):
                setattr(child, fault, FaultyPipe(getattr(child, fault), secondary))
            elif fault == "wait":
                actual_wait = child.wait
                wait_count = 0

                def wait(*args, **kwargs):
                    nonlocal wait_count
                    result = actual_wait(*args, **kwargs)
                    wait_count += 1
                    if wait_count == 2:
                        raise secondary
                    return result

                child.wait = wait
            return child

        def run():
            with patch.object(tool.subprocess, "Popen", side_effect=spawn):
                with self.assertRaises(Exception) as caught:
                    tool.child_cli(["verify"], self.root, calls)
            return caught.exception

        if outer_exception:
            try:
                raise ValueError("already handled caller failure")
            except ValueError:
                error = run()
        else:
            error = run()
        child = self.children[-1]
        if valid:
            self.assertIs(error, secondary)
        else:
            self.assertIsInstance(error, tool.ExerciseError)
            self.assertIn("mismatched envelope", str(error))
            self.assertTrue(any("cleanup" in note for note in error.__notes__))
        self.assert_released(child)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["exit_code"], 0)
        self.assertEqual(calls[0]["response"], response if valid else {})
        self.assertEqual(calls[0]["stdout_sha256"], tool.digest(raw.encode()))

    def test_bad_response_survives_each_cleanup_error_and_keeps_receipt(self):
        for fault in ("stdout", "stderr", "wait"):
            with self.subTest(fault=fault):
                self.invoke(valid=False, fault=fault)

    def test_successful_response_does_not_hide_cleanup_error_or_lose_receipt(self):
        for fault in ("stdout", "stderr", "wait"):
            with self.subTest(fault=fault):
                self.invoke(valid=True, fault=fault)

    def test_handled_caller_exception_does_not_hide_cleanup_error(self):
        self.invoke(valid=True, fault="stdout", outer_exception=True)

    def test_probe_spawn_failure_survives_close_failure_and_releases_child(self):
        actual = subprocess.Popen
        primary = OSError(errno.EAGAIN, "synthetic second spawn failure")
        secondary = OSError(errno.EIO, "synthetic stdout close failure")

        def spawn(*args, **kwargs):
            if self.children:
                raise primary
            child = actual([sys.executable, "-I", "-c", "import sys; sys.stdin.buffer.read(1)"],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            child.stdout = FaultyPipe(child.stdout, secondary)
            self.children.append(child)
            return child

        archive = SimpleNamespace(fd=0, check=lambda: None)
        with patch.object(tool.subprocess, "Popen", side_effect=spawn):
            with self.assertRaises(OSError) as caught:
                tool.concurrent_directory_probe(archive)
        self.assertIs(caught.exception, primary)
        self.assertTrue(any("cleanup" in note for note in primary.__notes__))
        self.assertEqual(len(self.children), 1)
        self.assert_released(self.children[0])

    def test_probe_releases_both_children_after_first_cleanup_fails(self):
        actual = subprocess.Popen
        primary = OSError(errno.EPIPE, "synthetic start signal failure")
        secondary = OSError(errno.EIO, "synthetic stdout close failure")

        def spawn(*args, **kwargs):
            child = actual([sys.executable, "-I", "-c", "import sys; sys.stdin.buffer.read(1)"],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if not self.children:
                child.stdin = BrokenInput(child.stdin, primary)
                child.stdout = FaultyPipe(child.stdout, secondary)
            self.children.append(child)
            return child

        with patch.object(tool.subprocess, "Popen", side_effect=spawn):
            with self.assertRaises(OSError) as caught:
                tool.concurrent_directory_probe(SimpleNamespace(fd=0, check=lambda: None))
        self.assertIs(caught.exception, primary)
        self.assertEqual(len(self.children), 2)
        for child in self.children:
            self.assert_released(child)

    def test_child_exit_during_kill_preserves_read_failure_and_reaps(self):
        actual, actual_read = subprocess.Popen, tool.os.read
        primary = OSError(errno.EIO, "synthetic response read failure")
        calls = []

        def spawn(*args, **kwargs):
            child = actual([sys.executable, "-I", "-c",
                            "import time; print('ready', flush=True); time.sleep(30)"], **kwargs)
            self.children.append(child)
            actual_kill = child.kill

            def kill():
                actual_kill()
                raise ProcessLookupError(errno.ESRCH, "synthetic exit during kill")

            child.kill = kill
            return child

        def read(fd, size):
            if self.children and fd == self.children[-1].stdout.fileno():
                raise primary
            return actual_read(fd, size)

        with patch.object(tool.subprocess, "Popen", side_effect=spawn), patch.object(tool.os, "read", side_effect=read):
            with self.assertRaises(OSError) as caught:
                tool.child_cli(["verify"], self.root, calls)
        self.assertIs(caught.exception, primary)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["exit_code"], self.children[0].returncode)
        self.assertLess(calls[0]["exit_code"], 0)
        self.assert_released(self.children[0])


if __name__ == "__main__":
    unittest.main()
