"""A2 argv/config/response boundaries; mocks do not attest storage support."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import cli, snapshot_cli
from infra_artifact_ledger.snapshot_common import EXIT_CODES, PROTOCOL, RecoveryError, operation_budget


class _Output:
    def __init__(self, buffer=None):
        self.buffer = io.BytesIO() if buffer is None else buffer


class SnapshotCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = os.environ.copy()
        self.environment["PYTHONPATH"] = str(Path(snapshot_cli.__file__).resolve().parents[1])

    def invoke(self, *argv, expected=2):
        run = subprocess.run([sys.executable, "-m", "infra_artifact_ledger", "snapshot", *argv],
                             cwd=self.root, env=self.environment, capture_output=True, timeout=20)
        self.assertEqual(run.returncode, expected, (run.stdout, run.stderr))
        self.assertEqual(run.stderr, b"")
        self.assertEqual(run.stdout.count(b"\n"), 1)
        self.assertTrue(run.stdout.endswith(b"\n"))
        self.assertLessEqual(len(run.stdout), 64 * 1024)
        value = json.loads(run.stdout)
        self.assertEqual(value["protocol"], PROTOCOL)
        self.assertEqual(set(value), {"protocol", "status", "operation", "publication_state", "error"})
        self.assertEqual(set(value["error"]), {"code", "message", "stage"})
        self.assertLessEqual(len(value["error"]["message"].encode()), 1024)
        return value

    def valid_argv(self, command):
        values = {"db": str(self.root / "source.sqlite"), "output-root": str(self.root),
                  "snapshot-id": "a" * 32, "source-commit": "b" * 40,
                  "snapshot": str(self.root / ("a" * 32)),
                  "storage-config": str(self.root / "storage.json"),
                  "expected-manifest-sha256": "c" * 64,
                  "scratch-parent": str(self.root), "target-dir": str(self.root / "restored"),
                  "expected-database-sha256": "d" * 64}
        return [command, *(item for option in snapshot_cli._COMMANDS[command]
                           for item in ("--" + option, values[option]))]

    def in_process(self, argv, function, *, value=None, error=None):
        output, diagnostic = _Output(), io.StringIO()
        with patch("infra_artifact_ledger.recovery." + function, return_value=value, side_effect=error) as call:
            with patch.object(snapshot_cli.sys, "stdout", output), patch.object(snapshot_cli.sys, "stderr", diagnostic):
                code = cli.main(["snapshot", *argv])
        self.assertEqual(diagnostic.getvalue(), "")
        raw = output.buffer.getvalue()
        self.assertEqual(raw.count(b"\n"), 1)
        self.assertLessEqual(len(raw), 64 * 1024)
        return code, json.loads(raw), call

    def test_unknown_or_missing_subcommand_has_null_operation(self):
        for argv in [(), ("surprise-secret-value",), ("--unknown",), ("--db", "secret")]:
            with self.subTest(argv=argv):
                value = self.invoke(*argv)
                self.assertIsNone(value["operation"])
                self.assertEqual(value["error"]["code"], "INVALID_INPUT")
                self.assertNotIn("secret", json.dumps(value))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_duplicate_inapplicable_and_abbreviated_options_fail_before_io(self):
        for command in snapshot_cli._COMMANDS:
            valid = self.valid_argv(command)
            variants = [[command], valid[:-2], valid + valid[1:3],
                        valid + ["--unused", "private-value"], valid + ["--d", "private-value"]]
            for argv in variants:
                with self.subTest(argv=argv):
                    value = self.invoke(*argv)
                    self.assertEqual(value["operation"], command.replace("-", "_"))
                    expected_state = "not_applicable" if command in {"verify", "check-restore"} else "not_published"
                    self.assertEqual(value["publication_state"], expected_state)
                    self.assertEqual(value["error"]["code"], "INVALID_INPUT")
                    self.assertNotIn("private-value", json.dumps(value))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_help_is_text_and_advertised_without_running_an_operation(self):
        for argv in [["--help"], ["snapshot", "--help"],
                     *[["snapshot", command, "--help"] for command in snapshot_cli._COMMANDS]]:
            run = subprocess.run([sys.executable, "-m", "infra_artifact_ledger", *argv],
                                 cwd=self.root, env=self.environment, capture_output=True, timeout=20)
            self.assertEqual((run.returncode, run.stderr), (0, b""))
            self.assertTrue(run.stdout.startswith(b"usage: artifact-ledger"))
            self.assertIn(b"snapshot", run.stdout)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_all_five_cli_routes_use_keyword_api_and_same_envelope(self):
        config = {"format": "infra-artifact-ledger-storage/v1", "profile": "mounted-posix-v1",
                  "storage_ref": "test", "mount_point": "/mnt/example", "mount_root": "/",
                  "mount_source": "host:/test", "fs_type": "nfs4", "archive_root": "/mnt/example/archive"}
        (self.root / "storage.json").write_text(json.dumps(config))
        for command in snapshot_cli._COMMANDS:
            operation = command.replace("-", "_")
            state = "not_applicable" if operation in {"verify", "check_restore"} else "published"
            value = {"protocol": PROTOCOL, "status": "OK", "operation": operation,
                     "publication_state": state, "data": {}}
            with self.subTest(command=command):
                code, actual, call = self.in_process(self.valid_argv(command), operation, value=value)
                self.assertEqual(code, 0)
                self.assertEqual(actual, value)
                self.assertEqual(call.call_args.args, ())
                expected_keys = {name.replace("-", "_") for name in snapshot_cli._COMMANDS[command]}
                if command in {"verify", "restore"}:
                    expected_keys.add("storage_config")
                    self.assertIsNone(call.call_args.kwargs["storage_config"])
                if command == "publish":
                    self.assertEqual(call.call_args.kwargs["storage_config"], config)
                self.assertEqual(set(call.call_args.kwargs), expected_keys)

    def test_storage_file_is_read_and_parsed_once_before_api(self):
        config_file = self.root / "storage.json"
        config_file.write_bytes(b'{"original":"captured"}')
        original_parse = snapshot_cli.parse_json

        def replace_file_after_parse(raw, **kwargs):
            value = original_parse(raw, **kwargs)
            config_file.write_bytes(b'not JSON after the first read')
            return value

        value = {"protocol": PROTOCOL, "status": "OK", "operation": "publish",
                 "publication_state": "published", "data": {}}
        with patch.object(snapshot_cli, "parse_json", side_effect=replace_file_after_parse) as parse:
            code, _, call = self.in_process(self.valid_argv("publish"), "publish", value=value)
        self.assertEqual(code, 0)
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(call.call_args.kwargs["storage_config"], {"original": "captured"})

    def test_config_invalid_bytes_duplicate_keys_and_raw_limits(self):
        config_file = self.root / "storage.json"
        cases = [(b'{"a":"one","a":"two"}', "INVALID_INPUT", 2),
                 (b'\xef\xbb\xbf{}', "INVALID_INPUT", 2), (b'\xff', "INVALID_INPUT", 2),
                 (b'{"value":NaN}', "INVALID_INPUT", 2), (b'{} trailing', "INVALID_INPUT", 2),
                 (b" " * (16 * 1024 + 1), "RESOURCE_LIMIT", 7)]
        for raw, error, code in cases:
            config_file.write_bytes(raw)
            with self.subTest(error=error, raw=raw[:40]):
                value = self.invoke(*self.valid_argv("publish"), expected=code)
                self.assertEqual(value["error"]["code"], error)
                self.assertEqual(value["publication_state"], "not_published")
        # Exactly the raw byte budget is accepted by transport; typed config
        # validation belongs to the shared API and is tested separately.
        config_file.write_bytes(b"{}" + b" " * (16 * 1024 - 2))
        with operation_budget() as budget:
            self.assertEqual(snapshot_cli._read_config(str(config_file), budget), {})

    def test_readonly_config_failure_keeps_not_applicable(self):
        for command in ("verify", "restore"):
            value = self.invoke(*self.valid_argv(command), "--storage-config", str(self.root / "missing"), expected=5)
            self.assertEqual(value["error"]["code"], "NOT_FOUND")
            self.assertEqual(value["publication_state"], "not_applicable" if command == "verify" else "not_published")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO file boundary")
    def test_config_fifo_does_not_block(self):
        os.mkfifo(self.root / "storage.json")
        value = self.invoke(*self.valid_argv("publish"))
        self.assertEqual(value["error"]["code"], "INVALID_INPUT")

    def test_error_codes_and_publication_state_pass_through(self):
        for name, expected in EXIT_CODES.items():
            for state in ("not_published", "published", "unknown", "not_applicable"):
                with self.subTest(code=name, state=state):
                    error = RecoveryError(name, "bounded explanation", stage="sync", publication_state=state)
                    code, value, _ = self.in_process(self.valid_argv("create"), "create", error=error)
                    self.assertEqual(code, expected)
                    self.assertEqual(value, error.to_envelope("create"))

    def test_unexpected_api_exception_does_not_claim_no_publication(self):
        for command, expected_state, expected_code in [("create", "unknown", "PUBLICATION_UNKNOWN"),
                                                       ("verify", "not_applicable", "IO_ERROR")]:
            code, value, _ = self.in_process(self.valid_argv(command), command,
                                             error=RuntimeError("private error detail"))
            self.assertEqual(code, 9)
            self.assertEqual(value["publication_state"], expected_state)
            self.assertEqual(value["error"]["code"], expected_code)
            self.assertNotIn("private error detail", json.dumps(value))

    def test_response_limit_includes_lf_and_retains_known_publication(self):
        result = {"protocol": PROTOCOL, "status": "OK", "operation": "create",
                  "publication_state": "published", "data": {"padding": ""}}
        base_length = len(snapshot_cli._encode(result))
        result["data"]["padding"] = "a" * (64 * 1024 - base_length)
        code, value, _ = self.in_process(self.valid_argv("create"), "create", value=result)
        self.assertEqual(code, 0)
        self.assertEqual(value, result)
        result["data"]["padding"] += "a"
        code, value, _ = self.in_process(self.valid_argv("create"), "create", value=result)
        self.assertEqual(code, 7)
        self.assertEqual(value["error"]["code"], "RESOURCE_LIMIT")
        self.assertEqual(value["publication_state"], "published")
        self.assertEqual(value["error"]["stage"], "report")

    def test_config_and_api_share_cumulative_budget(self):
        config_file = self.root / "storage.json"
        config_file.write_bytes(b"{}")
        original = snapshot_cli._read_config
        seen = []

        def remember(path, budget):
            seen.append(budget)
            return original(path, budget)

        def invoke(**kwargs):
            with operation_budget() as budget:
                self.assertIs(budget, seen[0])
                return {"protocol": PROTOCOL, "status": "OK", "operation": "publish",
                        "publication_state": "published", "data": {}}

        with patch.object(snapshot_cli, "_read_config", side_effect=remember):
            code, _, _ = self.in_process(self.valid_argv("publish"), "publish", error=invoke)
        self.assertEqual(code, 0)

    def test_broken_stdout_emits_no_second_envelope_or_changed_result(self):
        class BrokenBuffer:
            def __init__(self):
                self.calls = 0

            def write(self, value):
                self.calls += 1
                raise BrokenPipeError("closed reader")

        buffer, diagnostic = BrokenBuffer(), io.StringIO()
        value = {"protocol": PROTOCOL, "status": "OK", "operation": "create",
                 "publication_state": "published", "data": {}}
        with patch("infra_artifact_ledger.recovery.create", return_value=value) as call:
            with patch.object(snapshot_cli.sys, "stdout", _Output(buffer)), patch.object(snapshot_cli.sys, "stderr", diagnostic):
                code = cli.main(["snapshot", *self.valid_argv("create")])
        self.assertEqual(code, 9)
        self.assertEqual(buffer.calls, 1)
        self.assertEqual(call.call_count, 1)
        self.assertIn("retain the original snapshot identity", diagnostic.getvalue())
        self.assertEqual(value["publication_state"], "published")


if __name__ == "__main__":
    unittest.main()
