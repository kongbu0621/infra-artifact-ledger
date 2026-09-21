"""Real process output failures must retain A2 exit codes through shutdown.

The argument-error command needs no database or supported filesystem. The
64-KiB case exercises the response writer directly, including a real partial
pipe write; no storage result or durability claim is simulated.
"""

import fcntl
import io
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import snapshot_cli


class SnapshotOutputChannelTests(unittest.TestCase):
    def setUp(self):
        self.environment = os.environ.copy()
        self.environment["PYTHONPATH"] = str(Path(snapshot_cli.__file__).resolve().parents[1])
        self.command = [sys.executable, "-m", "infra_artifact_ledger", "snapshot", "verify"]

    def run_command(self, *, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    closed=(), unbuffered=False):
        command = self.command[:]
        if unbuffered:
            command.insert(1, "-u")
        if closed:
            # exec lets Python observe absent descriptors at startup, where
            # sys.stdout / sys.stderr become None rather than closed wrappers.
            launcher = ("import os,sys; "
                        + "; ".join(f"os.close({fd})" for fd in closed)
                        + "; os.execv(sys.argv[1], sys.argv[1:])")
            command = [sys.executable, "-c", launcher, *command]
        return subprocess.run(command, env=self.environment, stdout=stdout,
                              stderr=stderr, timeout=15)

    def assert_channel_failure(self, result, *, diagnostic=True):
        self.assertEqual(result.returncode, 9, result.stderr)
        if diagnostic:
            self.assertIn(b"retain the original snapshot identity", result.stderr)
            self.assertNotIn(b"Traceback", result.stderr)
            self.assertNotIn(b"Exception ignored", result.stderr)
            self.assertEqual(result.stderr.count(b"\n"), 1)

    def broken_writer(self):
        reader, writer = os.pipe()
        os.close(reader)
        self.addCleanup(os.close, writer)
        return writer

    def test_closed_pipe_retains_exit_nine_in_buffered_and_unbuffered_processes(self):
        for unbuffered in (False, True):
            with self.subTest(unbuffered=unbuffered):
                result = self.run_command(stdout=self.broken_writer(), unbuffered=unbuffered)
                self.assert_channel_failure(result)

    def test_full_output_device_retains_exit_nine(self):
        with open("/dev/full", "wb", buffering=0) as full:
            self.assert_channel_failure(self.run_command(stdout=full))

    def test_absent_stdout_retains_exit_nine(self):
        result = self.run_command(closed=(1,))
        self.assert_channel_failure(result)
        self.assertEqual(result.stdout, b"")

    def test_output_failure_with_absent_or_failed_stderr_retains_exit_nine(self):
        cases = ({"closed": (1, 2)},
                 {"stdout": self.broken_writer(), "closed": (2,)},
                 {"stdout": self.broken_writer(), "stderr": self.broken_writer()})
        for index, arguments in enumerate(cases):
            with self.subTest(case=index):
                self.assert_channel_failure(self.run_command(**arguments), diagnostic=False)
        with open("/dev/full", "wb", buffering=0) as full:
            self.assert_channel_failure(
                self.run_command(stdout=self.broken_writer(), stderr=full), diagnostic=False)

    def test_absent_stderr_does_not_change_valid_stdout_error_envelope(self):
        result = self.run_command(closed=(2,))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout.count(b"\n"), 1)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_INPUT")

    def response_process(self, stdout):
        script = (
            "from infra_artifact_ledger import snapshot_cli as cli; "
            "response=b'{\"padding\":\"'+b'x'*(65536-15)+b'\"}\\n'; "
            "assert len(response)==65536; "
            "raise SystemExit(0 if cli._write_response(response) else 9)"
        )
        return subprocess.Popen([sys.executable, "-c", script], env=self.environment,
                                stdout=stdout, stderr=subprocess.PIPE)

    def test_real_partial_pipe_failure_emits_only_original_prefix(self):
        reader, writer = os.pipe()
        try:
            fcntl.fcntl(writer, fcntl.F_SETPIPE_SZ, 4096)
            child = self.response_process(writer)
        finally:
            os.close(writer)
        try:
            self.assertTrue(select.select([reader], [], [], 10)[0], "writer produced no bytes")
            prefix = os.read(reader, 1024)
        finally:
            os.close(reader)
        try:
            _, diagnostic = child.communicate(timeout=15)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
        expected = b'{"padding":"' + b'x' * (65536 - 15) + b'"}\n'
        self.assertTrue(prefix)
        self.assertEqual(prefix, expected[:len(prefix)])
        self.assertNotIn(b"\n", prefix)
        self.assertEqual(child.returncode, 9, diagnostic)
        self.assertEqual(diagnostic.count(b"\n"), 1)
        self.assertNotIn(b"Exception ignored", diagnostic)

    def test_full_response_uses_exactly_one_complete_line(self):
        child = self.response_process(subprocess.PIPE)
        stdout, stderr = child.communicate(timeout=15)
        self.assertEqual((child.returncode, stderr), (0, b""))
        self.assertEqual(len(stdout), 65536)
        self.assertEqual(stdout.count(b"\n"), 1)
        self.assertEqual(json.loads(stdout)["padding"], "x" * (65536 - 15))

    def test_embedded_main_keeps_caller_stream_and_existing_buffer_order(self):
        with tempfile.TemporaryFile() as binary:
            stream = io.TextIOWrapper(binary, encoding="utf-8")
            diagnostic = io.StringIO()
            try:
                stream.write("before\n")
                with patch.object(snapshot_cli.sys, "stdout", stream), \
                        patch.object(snapshot_cli.sys, "stderr", diagnostic):
                    self.assertEqual(snapshot_cli.main(["verify"]), 2)
                    self.assertIs(snapshot_cli.sys.stdout, stream)
                    self.assertFalse(stream.closed)
                    stream.write("after\n")
                    stream.flush()
                binary.seek(0)
                before, envelope, after = binary.read().splitlines()
                self.assertEqual((before, after), (b"before", b"after"))
                self.assertEqual(json.loads(envelope)["error"]["code"], "INVALID_INPUT")
                self.assertEqual(diagnostic.getvalue(), "")
            finally:
                stream.detach()

    def test_embedded_real_file_retries_short_writes_without_replacing_stream(self):
        original_write = os.write
        calls = []

        def short_write(descriptor, value):
            calls.append(len(value))
            return original_write(descriptor, value[:7])

        with tempfile.TemporaryFile() as binary:
            stream = io.TextIOWrapper(binary, encoding="utf-8")
            try:
                with patch.object(snapshot_cli.sys, "stdout", stream), \
                        patch.object(snapshot_cli.os, "write", short_write):
                    self.assertTrue(snapshot_cli._write_response(b'{"value":"short write"}\n'))
                    self.assertIs(snapshot_cli.sys.stdout, stream)
                self.assertGreater(len(calls), 1)
                binary.seek(0)
                self.assertEqual(binary.read(), b'{"value":"short write"}\n')
            finally:
                stream.detach()

    def test_simulated_partial_stream_gets_no_second_envelope_inside_outer_except(self):
        class PartialBuffer:
            calls = 0
            raw = b""

            def write(self, value):
                self.calls += 1
                self.raw += value[:11]
                return 11

        output = type("Output", (), {"buffer": PartialBuffer()})()
        diagnostic = io.StringIO()
        try:
            raise ValueError("unrelated handled caller error")
        except ValueError:
            with patch.object(snapshot_cli.sys, "stdout", output), \
                    patch.object(snapshot_cli.sys, "stderr", diagnostic):
                self.assertEqual(snapshot_cli.main(["verify"]), 9)
                self.assertIs(snapshot_cli.sys.stdout, output)
        self.assertEqual(output.buffer.calls, 1)
        self.assertEqual(len(output.buffer.raw), 11)
        self.assertNotIn(b"\n", output.buffer.raw)
        self.assertIn("retain the original snapshot identity", diagnostic.getvalue())

    def test_text_only_stdout_is_a_controlled_failure_and_remains_open(self):
        output, diagnostic = io.StringIO(), io.StringIO()
        with patch.object(snapshot_cli.sys, "stdout", output), \
                patch.object(snapshot_cli.sys, "stderr", diagnostic):
            self.assertEqual(snapshot_cli.main(["verify"]), 9)
            self.assertIs(snapshot_cli.sys.stdout, output)
        self.assertFalse(output.closed)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("retain the original snapshot identity", diagnostic.getvalue())


if __name__ == "__main__":
    unittest.main()
