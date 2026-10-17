"""A1 transport failures must not report success or replay a committed write."""
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

from infra_artifact_ledger import cli, open as open_ledger


class CLIOutputChannelTests(unittest.TestCase):
    def setUp(self):
        self.environment = dict(os.environ, PYTHONPATH=str(Path(cli.__file__).resolve().parents[1]))

    def broken_writer(self):
        reader, writer = os.pipe()
        os.close(reader)
        self.addCleanup(os.close, writer)
        return writer

    def run_command(self, *, stdout=subprocess.PIPE, stderr=subprocess.PIPE, closed=(), unbuffered=False):
        command = [sys.executable, *(['-u'] if unbuffered else []), '-m', 'infra_artifact_ledger', 'verify']
        if closed:
            launcher = ('import os,sys; ' + '; '.join(f'os.close({fd})' for fd in closed)
                        + '; os.execv(sys.argv[1], sys.argv[1:])')
            command = [sys.executable, '-c', launcher, *command]
        return subprocess.run(command, env=self.environment, stdout=stdout, stderr=stderr, timeout=15)

    def assert_channel_failure(self, result, *, diagnostic=True):
        self.assertEqual(result.returncode, 1, result.stderr)
        if diagnostic:
            self.assertIn(b'query the original operation identity', result.stderr)
            self.assertNotIn(b'Traceback', result.stderr)
            self.assertNotIn(b'Exception ignored', result.stderr)
            self.assertEqual(result.stderr.count(b'\n'), 1)

    def test_broken_pipe_in_buffered_and_unbuffered_processes(self):
        for unbuffered in (False, True):
            with self.subTest(unbuffered=unbuffered):
                self.assert_channel_failure(self.run_command(stdout=self.broken_writer(), unbuffered=unbuffered))

    def test_full_output_device(self):
        with open('/dev/full', 'wb', buffering=0) as full:
            self.assert_channel_failure(self.run_command(stdout=full))

    def test_absent_stdout_has_no_traceback(self):
        self.assert_channel_failure(self.run_command(closed=(1,)))

    def test_failed_or_absent_diagnostic_channel(self):
        cases = ({'closed': (1, 2)}, {'stdout': self.broken_writer(), 'closed': (2,)},
                 {'stdout': self.broken_writer(), 'stderr': self.broken_writer()})
        for index, arguments in enumerate(cases):
            with self.subTest(index=index):
                self.assert_channel_failure(self.run_command(**arguments), diagnostic=False)
        with open('/dev/full', 'wb', buffering=0) as full:
            self.assert_channel_failure(self.run_command(stdout=self.broken_writer(), stderr=full), diagnostic=False)

    def test_absent_stderr_preserves_normal_error_envelope(self):
        result = self.run_command(closed=(2,))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b'')
        self.assertEqual(result.stdout.count(b'\n'), 1)
        self.assertEqual(json.loads(result.stdout)['code'], 'INVALID_INPUT')

    def response_process(self, stdout, *, unbuffered=False):
        # A known valid response isolates transport; no database operation is
        # stubbed into a claimed storage/commit success in this test.
        script = ('from infra_artifact_ledger import cli; '
                  'cli._dispatch=lambda args:b\'{"padding":"\'+b"x"*65536+b\'"}\\n\'; '
                  'raise SystemExit(cli.main(["verify","--db","unused"]))')
        return subprocess.Popen([sys.executable, *(['-u'] if unbuffered else []), '-c', script],
                                env=self.environment, stdout=stdout, stderr=subprocess.PIPE)

    def test_real_partial_pipe_failure_is_not_reported_as_success(self):
        for unbuffered in (False, True):
            with self.subTest(unbuffered=unbuffered):
                reader, writer = os.pipe()
                try:
                    fcntl.fcntl(writer, fcntl.F_SETPIPE_SZ, 4096)
                    child = self.response_process(writer, unbuffered=unbuffered)
                finally:
                    os.close(writer)
                try:
                    self.assertTrue(select.select([reader], [], [], 10)[0])
                    prefix = os.read(reader, 1024)
                finally:
                    os.close(reader)
                try:
                    _, diagnostic = child.communicate(timeout=15)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()
                expected = b'{"padding":"' + b'x' * 65536 + b'"}\n'
                self.assertTrue(prefix)
                self.assertEqual(prefix, expected[:len(prefix)])
                self.assertNotIn(b'\n', prefix)
                self.assertEqual(child.returncode, 1, diagnostic)
                self.assertEqual(diagnostic.count(b'\n'), 1)
                self.assertNotIn(b'Exception ignored', diagnostic)

    def test_complete_response_remains_exactly_one_line(self):
        child = self.response_process(subprocess.PIPE)
        stdout, stderr = child.communicate(timeout=15)
        self.assertEqual((child.returncode, stderr), (0, b''))
        self.assertEqual(stdout.count(b'\n'), 1)
        self.assertEqual(json.loads(stdout)['padding'], 'x' * 65536)

    def test_partial_custom_output_cannot_turn_initialization_into_success(self):
        class PartialBuffer:
            calls = 0
            received = b''
            def write(self, value):
                self.calls += 1
                self.received += value[:11]
                return 11
            def flush(self):
                pass
        output = type('Output', (), {'buffer': PartialBuffer()})()
        diagnostic = io.StringIO()
        with tempfile.TemporaryDirectory() as root:
            database = Path(root) / 'ledger.sqlite'
            with patch.object(cli.sys, 'stdout', output), patch.object(cli.sys, 'stderr', diagnostic):
                code = cli.main(['init', '--db', str(database)])
            self.assertEqual(code, 1)
            self.assertEqual(output.buffer.calls, 1)
            self.assertEqual(len(output.buffer.received), 11)
            self.assertIn('query the original operation identity', diagnostic.getvalue())
            with open_ledger(database) as ledger:
                self.assertEqual(ledger.verify()['counts']['idempotency_records'], 0)

    def test_embedded_main_preserves_stream_ownership_and_pending_output(self):
        with tempfile.TemporaryFile() as binary:
            output = io.TextIOWrapper(binary, encoding='utf-8')
            try:
                output.write('before\n')
                with patch.object(cli.sys, 'stdout', output):
                    self.assertEqual(cli.main(['verify']), 2)
                    self.assertIs(cli.sys.stdout, output)
                    self.assertFalse(output.closed)
                    output.write('after\n')
                    output.flush()
                binary.seek(0)
                before, envelope, after = binary.read().splitlines()
                self.assertEqual((before, after), (b'before', b'after'))
                self.assertEqual(json.loads(envelope)['code'], 'INVALID_INPUT')
            finally:
                output.detach()

    def test_text_only_output_is_a_controlled_failure(self):
        output, diagnostic = io.StringIO(), io.StringIO()
        with patch.object(cli.sys, 'stdout', output), patch.object(cli.sys, 'stderr', diagnostic):
            self.assertEqual(cli.main(['verify']), 1)
            self.assertIs(cli.sys.stdout, output)
        self.assertFalse(output.closed)
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
