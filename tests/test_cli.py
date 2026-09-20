"""Real CLI subprocess acceptance: file boundaries, JSON, identity and reuse."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from acceptance_helpers import append, create, encode, import_request, SCOPE


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = str(self.root / "ledger.sqlite")
        self.environment = os.environ.copy()
        self.environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    def run_cli(self, *args, exit_code=0, db=True):
        command = [sys.executable, "-m", "infra_artifact_ledger", *args]
        if db:
            command.extend(["--db", self.database])
        response = subprocess.run(command, cwd=self.root, env=self.environment,
                                  capture_output=True, timeout=20)
        self.assertEqual(response.returncode, exit_code, response.stderr.decode("utf-8", "replace") + response.stdout.decode("utf-8", "replace"))
        self.assertEqual(response.stderr, b"")
        self.assertTrue(response.stdout.endswith(b"\n"))
        self.assertEqual(response.stdout.count(b"\n"), 1)
        return json.loads(response.stdout)

    def put(self, name, value):
        path = self.root / name
        path.write_bytes(value if isinstance(value, bytes) else encode(value))
        return str(path)

    def initialized(self):
        result = self.run_cli("init")
        self.assertEqual(result, {"status": "OK", "commit_state": "not_applicable",
                                 "data": {"profile": "bounded-local-v0.1", "storage_schema_version": 1}})

    def registered(self, *, empty=False):
        self.initialized()
        creation = self.put("create.json", create())
        self.run_cli("write", "--request", creation)
        request, payloads = append(payload=b"" if empty else b"Quarterly report v1\n")
        payload_map = self.put("payloads.json", [{"blob_ref": ref, "input_path": self.put("payload.bin", data)}
                                                  for ref, data in payloads.items()])
        append_file = self.put("append.json", request)
        self.run_cli("write", "--request", append_file, "--payload-map", payload_map)
        return append_file, payload_map

    def test_invalid_argv_is_json_and_never_creates_database(self):
        cases = [(), ("write",), ("write", "--request", "missing", "--unused", "secret"),
                 ("init", "--d", self.database), ("init", "--db", self.database),
                 ("get", "--kind", "version")]
        for args in cases:
            with self.subTest(args=args):
                result = self.run_cli(*args, exit_code=2)
                self.assertEqual(result["code"], "INVALID_INPUT")
                self.assertFalse(Path(self.database).exists())

    def test_read_and_auxiliary_argv_errors_are_not_applicable(self):
        commands = {
            "init": (), "get": ("--kind", "version", "--id", "version:test"),
            "history": ("--artifact-id", "artifact:test"),
            "operation": ("--request", "missing.json"),
            "read-blob": ("--blob-ref", "blob:test", "--output", "output.bin"),
            "verify": (), "export": ("--package", "package.json", "--descriptor", "descriptor.json"),
        }
        for command, options in commands.items():
            valid = (command, "--db", self.database, *options)
            cases = ((command, *options), (*valid, "--db", self.database),
                     (*valid, "--unused", "value"), ("--unused", *valid))
            if options:
                cases += (valid[:-2],)
            for args in cases:
                with self.subTest(args=args):
                    result = self.run_cli(*args, db=False, exit_code=2)
                    self.assertEqual(result["code"], "INVALID_INPUT")
                    self.assertEqual(result["commit_state"], "not_applicable")
                    self.assertFalse(Path(self.database).exists())

    def test_write_argv_errors_remain_not_committed(self):
        valid = ("write", "--db", self.database, "--request", "missing.json")
        for args in [("write", "--db", self.database), (*valid, "--db", self.database),
                     (*valid, "--unused", "value"), ("--unused", *valid)]:
            with self.subTest(args=args):
                result = self.run_cli(*args, db=False, exit_code=2)
                self.assertEqual(result["code"], "INVALID_INPUT")
                self.assertEqual(result["commit_state"], "not_committed")
                self.assertFalse(Path(self.database).exists())

    def test_help_remains_text_without_executing_an_operation(self):
        for command in (None, "init", "write", "get", "history", "operation", "read-blob", "verify", "export"):
            args = ["--help"] if command is None else [command, "--help"]
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, "-m", "infra_artifact_ledger", *args],
                                        cwd=self.root, env=self.environment, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, b"")
                self.assertTrue(result.stdout.startswith(b"usage: artifact-ledger"))
                self.assertFalse(Path(self.database).exists())

    def test_operation_specific_transport_options_rejected_before_db_open(self):
        create_file = self.put("create.json", create())
        append_file = self.put("append.json", append()[0])
        import_file = self.put("import.json", import_request())
        for args in [(create_file, "--payload-map", "missing"),
                     (create_file, "--package", "missing", "--descriptor", "missing"),
                     (append_file,), (append_file, "--package", "missing"),
                     (import_file, "--package", "missing")]:
            with self.subTest(args=args):
                result = self.run_cli("write", "--request", *args, exit_code=2)
                self.assertEqual(result["code"], "INVALID_INPUT")
                self.assertEqual(result["commit_state"], "not_committed")
                self.assertFalse(Path(self.database).exists())

    def test_control_character_key_write_query_and_replay(self):
        self.initialized()
        key = "write\0\n\t\u007f中文"
        request = self.put("control-key.json", create(key=key))
        first = self.run_cli("write", "--request", request)
        query = self.put("query.json", {"operation_kind": "create_artifact",
                                        "idempotency_scope_ref": SCOPE, "idempotency_key": key})
        queried = self.run_cli("operation", "--request", query)
        self.assertEqual(queried["data"]["idempotency_record"]["idempotency_key"], key)
        self.assertEqual(queried["data"]["result"]["artifact_id"], first["result_ref"])
        replay = self.run_cli("write", "--request", request)
        self.assertTrue(replay["replayed"])
        self.assertEqual({k: v for k, v in first.items() if k != "replayed"},
                         {k: v for k, v in replay.items() if k != "replayed"})
        self.assertEqual(self.run_cli("verify")["data"]["counts"]["idempotency_records"], 1)

    def test_closed_stdout_after_write_recovers_by_original_identity_without_duplicate(self):
        self.initialized()
        key = "lost-stdout-response"
        request = self.put("lost-response.json", create(key=key))
        process = subprocess.Popen(
            [sys.executable, "-m", "infra_artifact_ledger", "write", "--db", self.database,
             "--request", request], cwd=self.root, env=self.environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        # Close the only reader before Python startup completes. The real write
        # still commits, then encounters EPIPE while delivering its response.
        process.stdout.close()
        try:
            returncode = process.wait(timeout=20)
            diagnostics = process.stderr.read()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stderr.close()
        self.assertNotEqual(returncode, 0)
        self.assertIn(b"response channel failed", diagnostics)
        query = self.put("lost-response-query.json", {
            "idempotency_scope_ref": SCOPE, "operation_kind": "create_artifact", "idempotency_key": key,
        })
        recovered = self.run_cli("operation", "--request", query)["data"]
        replayed = self.run_cli("write", "--request", request)
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["result_ref"], recovered["result"]["artifact_id"])
        self.assertEqual(replayed["recorded_at"], recovered["idempotency_record"]["recorded_at"])
        counts = self.run_cli("verify")["data"]["counts"]
        self.assertEqual(counts["artifacts"], 1)
        self.assertEqual(counts["idempotency_records"], 1)

    def test_empty_blob_readback_and_no_clobber(self):
        self.registered(empty=True)
        output = self.root / "empty.bin"
        read = self.run_cli("read-blob", "--blob-ref", "blob:quarterly-v1", "--output", str(output))
        self.assertEqual(read["data"]["byte_length"], 0)
        self.assertEqual(output.read_bytes(), b"")
        output.write_bytes(b"existing sentinel")
        failed = self.run_cli("read-blob", "--blob-ref", "blob:quarterly-v1", "--output", str(output), exit_code=1)
        self.assertEqual(failed["code"], "IO_ERROR")
        self.assertEqual(failed["commit_state"], "not_applicable")
        self.assertEqual(output.read_bytes(), b"existing sentinel")

    def test_new_empty_blob_requires_a_real_zero_byte_input(self):
        self.initialized()
        self.run_cli("write", "--request", self.put("create.json", create()))
        request = self.put("append.json", append(payload=b"")[0])
        result = self.run_cli("write", "--request", request, "--payload-map", self.put("empty-map.json", []), exit_code=2)
        self.assertEqual(result["code"], "INVALID_INPUT")
        self.assertEqual(self.run_cli("verify")["data"]["counts"]["versions"], 0)

    def test_read_history_query_and_export_import_across_processes(self):
        self.registered()
        metadata = self.run_cli("get", "--kind", "version", "--id", "version:quarterly-v1")["data"]
        history = self.run_cli("history", "--artifact-id", metadata["artifact_id"])["data"]
        self.assertEqual(history["versions"], [metadata])
        package, descriptor = str(self.root / "bundle.json"), str(self.root / "descriptor.json")
        export = self.run_cli("export", "--package", package, "--descriptor", descriptor)
        self.assertEqual(export["data"]["descriptor"], json.loads(Path(descriptor).read_bytes()))
        self.database = str(self.root / "copy.sqlite")
        self.initialized()
        request = self.put("import.json", import_request())
        self.run_cli("write", "--request", request, "--package", package, "--descriptor", descriptor)
        result = self.run_cli("verify")["data"]
        self.assertEqual(result["counts"]["import_receipts"], 1)
        self.assertEqual(result["counts"]["idempotency_records"], 3)
        self.assertEqual(self.run_cli("get", "--kind", "version", "--id", "version:quarterly-v1")["data"], metadata)
        output = self.root / "copied.bin"
        self.run_cli("read-blob", "--blob-ref", "blob:quarterly-v1", "--output", str(output))
        self.assertEqual(output.read_bytes(), b"Quarterly report v1\n")

    def test_export_second_target_failure_is_not_success_or_clobber(self):
        self.initialized()
        package = self.root / "bundle.json"
        descriptor = self.root / "existing.json"
        descriptor.write_bytes(b"sentinel")
        result = self.run_cli("export", "--package", str(package), "--descriptor", str(descriptor), exit_code=1)
        self.assertEqual(result["commit_state"], "not_applicable")
        self.assertTrue(package.is_file())
        self.assertEqual(descriptor.read_bytes(), b"sentinel")
        self.assertFalse(list(self.root.glob(".artifact-ledger-*")))
        self.assertEqual(self.run_cli("verify")["data"]["counts"]["artifacts"], 0)

    def test_export_same_paths_rejected_before_publication(self):
        self.initialized()
        output = str(self.root / "same.json")
        result = self.run_cli("export", "--package", output, "--descriptor", output, exit_code=2)
        self.assertEqual(result["code"], "INVALID_INPUT")
        self.assertFalse(Path(output).exists())

    def test_symlink_output_is_not_followed_or_replaced(self):
        self.registered()
        output, target = self.root / "symlink", self.root / "target.bin"
        target.write_bytes(b"sentinel")
        output.symlink_to(target)
        result = self.run_cli("read-blob", "--blob-ref", "blob:quarterly-v1", "--output", str(output), exit_code=1)
        self.assertEqual(result["code"], "IO_ERROR")
        self.assertTrue(output.is_symlink())
        self.assertEqual(target.read_bytes(), b"sentinel")

    def test_invalid_payload_map_checked_before_input_reads(self):
        request = self.put("append.json", append()[0])
        entries = [{"blob_ref": "blob:quarterly-v1", "input_path": "missing"}]
        maps = [entries * 2, [dict(entries[0], undeclared=True)],
                [{"blob_ref": "blob:quarterly-v1", "input_path": "contains\0nul"}], {}]
        for value in maps:
            with self.subTest(value=value):
                mapping = self.put("invalid-map.json", value)
                result = self.run_cli("write", "--request", request, "--payload-map", mapping, exit_code=2)
                self.assertEqual(result["code"], "INVALID_INPUT")
                self.assertFalse(Path(self.database).exists())

    def test_json_byte_limit_checked_before_decoding(self):
        oversized = self.root / "oversized.json"
        with oversized.open("wb") as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        result = self.run_cli("write", "--request", str(oversized), exit_code=2)
        self.assertEqual(result["code"], "RESOURCE_LIMIT")
        self.assertFalse(Path(self.database).exists())

    def test_invalid_json_and_queries_are_structured_failures(self):
        for value in [b'{} trailing', b'{"profile":"a","profile":"b"}', b'\xef\xbb\xbf{}', b'{"x":NaN}']:
            result = self.run_cli("write", "--request", self.put("bad.json", value), exit_code=2)
            self.assertEqual(result["code"], "INVALID_INPUT")
        query = self.put("bad-query.json", {"idempotency_scope_ref": SCOPE,
                                           "operation_kind": "create_artifact", "idempotency_key": "k", "extra": True})
        result = self.run_cli("operation", "--request", query, exit_code=2)
        self.assertEqual(result["code"], "INVALID_INPUT")
        self.assertFalse(Path(self.database).exists())


if __name__ == "__main__":
    unittest.main()
