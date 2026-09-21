"""Strict A2 child response parsing through real local synthetic processes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_snapshot_process_cleanup import FaultyPipe


_SPEC = importlib.util.spec_from_file_location(
    "a2_child_protocol", Path(__file__).resolve().parents[1] / "tools/acceptance/a2_nas_exercise.py")
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


def response(data=None):
    return {"protocol": "infra-artifact-ledger-recovery/v1", "status": "OK",
            "operation": "verify", "publication_state": "not_applicable",
            "data": {} if data is None else data}


def raw_response(data=None):
    return json.dumps(response(data), separators=(",", ":")).encode() + b"\n"


class ChildProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a2-child-protocol-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.children = []
        self.addCleanup(self.release_children)

    def release_children(self):
        for child in self.children:
            if child.poll() is None:
                subprocess.Popen.kill(child)
            subprocess.Popen.wait(child, timeout=10)
            for pipe in (child.stdout, child.stderr):
                (pipe.raw if isinstance(pipe, FaultyPipe) else pipe).close()

    def invoke(self, raw, *, accepted=False, exit_code=0, close_failure=False):
        actual = subprocess.Popen
        calls = []

        def spawn(*args, **kwargs):
            script = "import sys;sys.stdout.buffer.write(" + repr(raw) + ");sys.stdout.flush();sys.exit(" + str(exit_code) + ")"
            child = actual([sys.executable, "-I", "-c", script], **kwargs)
            if close_failure:
                child.stdout = FaultyPipe(child.stdout, OSError("synthetic close failure"))
            self.children.append(child)
            return child

        with patch.object(tool.subprocess, "Popen", side_effect=spawn):
            if accepted:
                result = tool.child_cli(["verify"], self.root, calls)
            else:
                with self.assertRaises(tool.ExerciseError) as caught:
                    tool.child_cli(["verify"], self.root, calls)
                result = caught.exception
                # A response decoder failure cannot classify publication.
                self.assertIsNone(getattr(result, "publication_state", None))
        child = self.children[-1]
        self.assertEqual(child.returncode, exit_code)
        self.assertTrue(child.stdout.closed and child.stderr.closed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["exit_code"], exit_code)
        self.assertEqual(calls[0]["stdout_sha256"], tool.digest(raw))
        self.assertEqual(calls[0]["stderr_sha256"], tool.digest(b""))
        return result, calls[0]

    def test_invalid_json_encodings_and_values_are_rejected(self):
        canonical = raw_response()
        cases = {
            "bom": b"\xef\xbb\xbf" + canonical,
            "utf16be": canonical.decode().encode("utf-16-be"),
            "duplicate_status": canonical.replace(b'"status":"OK"', b'"status":"ERROR","status":"OK"'),
            "nan": raw_response({"value": float("nan")}),
            "infinite_exponent": raw_response({"value": 0}).replace(b'"value":0', b'"value":1e400'),
            "surrogate": raw_response({"value": "\ud800"}),
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                _, record = self.invoke(raw)
                self.assertIsNone(record["response"])

    def test_json_depth_and_node_budgets_reject_before_recursive_allocation(self):
        cases = {
            "deep": b"[" * 1500 + b"0" + b"]" * 1500 + b"\n",
            "depth9": raw_response({"value": [[[[[[[0]]]]]]]}),
            "nodes1025": raw_response({"value": [None] * 1018}),
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                _, record = self.invoke(raw)
                self.assertIsNone(record["response"])

    def test_valid_json_boundaries_remain_accepted(self):
        base = raw_response({"padding": ""})
        maximum = raw_response({"padding": "x" * (65536 - len(base))})
        self.assertEqual(len(maximum), 65536)
        for name, raw in {
            "depth8": raw_response({"value": [[[[[[0]]]]]]}),
            "nodes1024": raw_response({"value": [None] * 1017}),
            "bytes65536": maximum,
        }.items():
            with self.subTest(case=name):
                actual, record = self.invoke(raw, accepted=True)
                self.assertEqual(actual, json.loads(raw))
                self.assertEqual(record["response"], actual)

    def test_malformed_output_does_not_replace_command_failure_or_lose_record(self):
        for raw in (b"[" * 1500 + b"0" + b"]" * 1500 + b"\n", b"\xff\n", b""):
            with self.subTest(bytes=len(raw)):
                error, record = self.invoke(raw, exit_code=7)
                self.assertIn("command failed", str(error))
                self.assertIsNone(record["response"])

    def test_valid_error_envelope_is_preserved_in_failed_command_record(self):
        value = {"protocol": "infra-artifact-ledger-recovery/v1", "status": "ERROR",
                 "operation": "verify", "publication_state": "not_applicable",
                 "error": {"code": "INTEGRITY_FAILURE", "stage": "verify", "message": "synthetic failure"}}
        error, record = self.invoke(json.dumps(value).encode() + b"\n", exit_code=6)
        self.assertIn("command failed", str(error))
        self.assertEqual(record["response"], value)

    def test_invalid_json_survives_cleanup_failure_with_record(self):
        error, record = self.invoke(b"[" * 1500 + b"0" + b"]" * 1500 + b"\n", close_failure=True)
        self.assertTrue(any("cleanup" in note for note in error.__notes__))
        self.assertIsNone(record["response"])


if __name__ == "__main__":
    unittest.main()
