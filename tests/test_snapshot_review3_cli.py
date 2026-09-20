"""Configuration transport owns its fd and preserves the first failure."""

from contextlib import contextmanager
import errno
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import snapshot_cli
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError


class ConfigOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name) / "config.json"

    @contextmanager
    def close_failure(self):
        original_open, original_close = os.open, os.close
        opened, closed = [], []

        def tracked_open(*args, **kwargs):
            descriptor = original_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor

        def failed_close(descriptor):
            original_close(descriptor)
            closed.append(descriptor)
            raise OSError(errno.EIO, "injected close after release")

        with patch.object(snapshot_cli.os, "open", side_effect=tracked_open), \
                patch.object(snapshot_cli.os, "close", side_effect=failed_close):
            yield
        self.assertEqual(len(opened), 1)
        self.assertEqual(closed, opened)
        with self.assertRaises(OSError) as error:
            os.fstat(opened[0])
        self.assertEqual(error.exception.errno, errno.EBADF)

    def test_oversize_and_close_failure_keep_cli_resource_state(self):
        self.config.write_bytes(b" " * (snapshot_cli.MAX_CONFIG + 1))
        for command in ("publish", "verify", "restore"):
            with self.subTest(command=command):
                argv = [command, "--snapshot", "/unused/snapshot",
                        "--storage-config", str(self.config),
                        "--expected-manifest-sha256", "a" * 64,
                        "--scratch-parent", "/unused/scratch"]
                if command == "restore":
                    argv += ["--target-dir", "/unused/target"]
                output = type("Output", (), {"buffer": io.BytesIO()})()
                with self.close_failure(), \
                        patch("infra_artifact_ledger.recovery." + command) as operation, \
                        patch.object(snapshot_cli.sys, "stdout", output):
                    result = snapshot_cli.main(argv)
                operation.assert_not_called()
                raw = output.buffer.getvalue()
                value = json.loads(raw)
                self.assertEqual(raw.count(b"\n"), 1)
                self.assertEqual(result, 7)
                self.assertEqual(value["error"]["code"], "RESOURCE_LIMIT")
                self.assertEqual(value["error"]["stage"], "validate")
                self.assertEqual(value["publication_state"],
                                 "not_applicable" if command == "verify" else "not_published")
        self.assertEqual(self.config.read_bytes(), b" " * (snapshot_cli.MAX_CONFIG + 1))

    def test_nonregular_and_close_failure_keep_invalid_input(self):
        self.config.mkdir()
        with self.close_failure(), self.assertRaises(RecoveryError) as error:
            snapshot_cli._read_config(str(self.config), Budget())
        self.assertEqual(error.exception.code, "INVALID_INPUT")

    def test_short_reads_collect_whole_config_at_exact_limit(self):
        self.config.write_bytes(b'{}' + b' ' * (snapshot_cli.MAX_CONFIG - 2))
        original_read = os.read
        with patch.object(snapshot_cli.os, "read", side_effect=lambda fd, n: original_read(fd, min(n, 7))):
            self.assertEqual(snapshot_cli._read_config(str(self.config), Budget()), {})

    def test_growth_after_stat_respects_serialized_limit(self):
        self.config.write_bytes(b"{}")
        original_read = os.read
        grown = False

        def grow(descriptor, count):
            nonlocal grown
            if not grown:
                grown = True
                with self.config.open("ab") as stream:
                    stream.write(b" " * snapshot_cli.MAX_CONFIG)
            return original_read(descriptor, min(count, 4096))

        with patch.object(snapshot_cli.os, "read", side_effect=grow), self.assertRaises(RecoveryError) as error:
            snapshot_cli._read_config(str(self.config), Budget())
        self.assertEqual(error.exception.code, "RESOURCE_LIMIT")

    def test_checkpoint_and_close_failure_keep_timeout(self):
        self.config.write_bytes(b"{}")

        class ExpiredOnRead:
            calls = 0

            def check(self, stage):
                self.calls += 1
                if self.calls > 1:
                    raise RecoveryError("TIMEOUT", "injected checkpoint expiry", stage)

        with self.close_failure(), self.assertRaises(RecoveryError) as error:
            snapshot_cli._read_config(str(self.config), ExpiredOnRead())
        self.assertEqual(error.exception.code, "TIMEOUT")

    def test_close_failure_inside_outer_except_is_not_swallowed(self):
        self.config.write_bytes(b"{}")
        try:
            raise ValueError("already handled by caller")
        except ValueError:
            with self.close_failure(), self.assertRaises(RecoveryError) as error:
                snapshot_cli._read_config(str(self.config), Budget())
            self.assertEqual(error.exception.code, "IO_ERROR")


if __name__ == "__main__":
    unittest.main()
