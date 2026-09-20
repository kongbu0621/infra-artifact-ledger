"""Real source-header helper lifetime under compound cleanup failures.

The child is a fresh interpreter and its pipe is really closed/reaped. Only
cleanup faults are injected; these cases do not attest storage durability.
"""

import errno
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize
from infra_artifact_ledger import snapshot_sqlite as snapshot
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError


class _Pipe:
    def __init__(self, raw, fail_close):
        self.raw = raw
        self.fail_close = fail_close

    def fileno(self):
        return self.raw.fileno()

    @property
    def closed(self):
        return self.raw.closed

    def close(self):
        self.raw.close()
        if self.fail_close:
            raise OSError(errno.EIO, "Injected pipe close failure after actual close.")


class SourceHeaderCleanupReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a2-review3-helper-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.sqlite"
        with initialize(self.source):
            pass
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            # Bypass the deliberately injected method during emergency cleanup.
            subprocess.Popen.wait(child)
            child.stdout.raw.close()
        self.temporary.cleanup()

    def run_failure(self, code, *, sleeping=False, fail_close=False,
                    fail_cleanup_wait=False, outer_exception=False):
        original = subprocess.Popen
        budget = Budget()
        if sleeping:
            budget.deadline = time.monotonic() + 0.15
        before = self.source.read_bytes()
        members = set(self.root.iterdir())

        def child(*args, **kwargs):
            if sleeping:
                args = ([sys.executable, "-I", "-c", "import time; time.sleep(30)"],)
            process = original(*args, **kwargs)
            process.stdout = _Pipe(process.stdout, fail_close)
            self.children.append(process)
            if fail_cleanup_wait:
                actual_wait = process.wait
                calls = 0

                def wait(*wait_args, **wait_kwargs):
                    nonlocal calls
                    calls += 1
                    result = actual_wait(*wait_args, **wait_kwargs)
                    # A sleeping child exits through the timeout path, so its
                    # first wait is cleanup. A valid child is already waited
                    # once before interpreting its header result.
                    if sleeping or calls >= 2:
                        raise OSError(errno.EIO, "Injected wait failure after actual reap.")
                    return result

                process.wait = wait
            return process

        def invoke():
            with patch.object(snapshot.subprocess, "Popen", side_effect=child):
                with self.assertRaises(RecoveryError) as caught:
                    snapshot.preflight_source(self.source, budget)
            return caught.exception

        if outer_exception:
            try:
                raise ValueError("Already handled caller exception.")
            except ValueError:
                error = invoke()
        else:
            error = invoke()
        self.assertEqual((error.code, error.stage, error.publication_state),
                         (code, "validate", "not_published"))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), members)
        self.assertEqual(len(self.children), 1)
        self.assertIsNotNone(self.children[0].poll())
        self.assertTrue(self.children[0].stdout.closed)
        return error

    def test_helper_timeout_survives_pipe_close_failure(self):
        error = self.run_failure("TIMEOUT", sleeping=True, fail_close=True)
        self.assertTrue(any("cleanup" in note for note in error.__notes__))

    def test_header_rejection_survives_pipe_close_failure(self):
        header = bytearray(self.source.read_bytes())
        header[18:20] = b"\x02\x02"
        self.source.write_bytes(header)
        error = self.run_failure("UNSUPPORTED_FORMAT", fail_close=True)
        self.assertTrue(any("cleanup" in note for note in error.__notes__))

    def test_successful_header_does_not_hide_pipe_close_failure(self):
        self.run_failure("IO_ERROR", fail_close=True)

    def test_caller_handled_exception_does_not_hide_pipe_close_failure(self):
        self.run_failure("IO_ERROR", fail_close=True, outer_exception=True)

    def test_helper_timeout_survives_reap_failure_and_still_closes_pipe(self):
        error = self.run_failure("TIMEOUT", sleeping=True, fail_cleanup_wait=True)
        self.assertTrue(any("cleanup" in note for note in error.__notes__))

    def test_successful_header_reap_failure_still_closes_pipe(self):
        self.run_failure("IO_ERROR", fail_cleanup_wait=True)


if __name__ == "__main__":
    unittest.main()
