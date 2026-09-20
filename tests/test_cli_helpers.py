"""Fault boundaries use real files and ledgers, without product fault flags."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from infra_artifact_ledger import cli
from acceptance_helpers import append, create, encode, SCOPE


class CLIFileBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_competing_destination_created_at_publish_is_preserved(self):
        destination = self.root / "result.bin"
        real_link = os.link

        def competing_link(source, target):
            Path(target).write_bytes(b"other writer")
            return real_link(source, target)

        with patch.object(cli.os, "link", side_effect=competing_link):
            with self.assertRaises(LedgerError) as caught:
                cli._publish(str(destination), b"requested output")
        self.assertEqual(caught.exception.code, "IO_ERROR")
        self.assertEqual(caught.exception.commit_state, "not_applicable")
        self.assertEqual(destination.read_bytes(), b"other writer")
        self.assertFalse(list(self.root.glob(".artifact-ledger-*")))

    def test_file_fsync_failure_has_no_published_output(self):
        destination = self.root / "result.bin"
        with patch.object(cli.os, "fsync", side_effect=OSError("synthetic fsync failure")):
            with self.assertRaises(LedgerError) as caught:
                cli._publish(str(destination), b"requested output")
        self.assertEqual(caught.exception.code, "IO_ERROR")
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob(".artifact-ledger-*")))

    def test_directory_fsync_failure_keeps_already_published_output_and_reports_failure(self):
        destination = self.root / "result.bin"
        real_fsync = os.fsync
        calls = 0

        def fail_after_publish(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic directory fsync failure")
            return real_fsync(descriptor)

        with patch.object(cli.os, "fsync", side_effect=fail_after_publish):
            with self.assertRaises(LedgerError) as caught:
                cli._publish(str(destination), b"requested output")
        self.assertEqual(caught.exception.code, "IO_ERROR")
        self.assertEqual(caught.exception.commit_state, "not_applicable")
        self.assertEqual(destination.read_bytes(), b"requested output")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "Linux input-file boundary")
    def test_fifo_input_is_rejected_without_waiting_for_a_writer(self):
        source = self.root / "pipe"
        os.mkfifo(source)
        with self.assertRaises(LedgerError) as caught:
            cli._read_file(str(source), 1024, "request")
        self.assertEqual(caught.exception.code, "INVALID_INPUT")

    def test_payload_file_is_not_reopened_after_validated_snapshot(self):
        database = self.root / "ledger.sqlite"
        with initialize(database) as ledger:
            ledger.execute(encode(create()))
        request, payloads = append(payload=b"original bytes")
        request_path = self.root / "request.json"
        payload_path = self.root / "payload.bin"
        mapping_path = self.root / "payload-map.json"
        request_path.write_bytes(encode(request))
        payload_path.write_bytes(b"original bytes")
        mapping_path.write_bytes(encode([{"blob_ref": next(iter(payloads)), "input_path": str(payload_path)}]))
        original_payloads = cli._payloads

        def change_after_read(path):
            snapshot = original_payloads(path)
            payload_path.write_bytes(b"changed after validation")
            return snapshot

        args = cli._parser().parse_args(["write", "--db", str(database), "--request", str(request_path),
                                        "--payload-map", str(mapping_path)])
        with patch.object(cli, "_payloads", side_effect=change_after_read):
            cli._dispatch(args)
        with open_ledger(database) as ledger:
            self.assertEqual(ledger.read_blob("blob:quarterly-v1"), b"original bytes")
        self.assertEqual(payload_path.read_bytes(), b"changed after validation")

    def test_cleanup_error_preserves_confirmed_write_identity(self):
        database = self.root / "ledger.sqlite"
        initialize(database).close()
        request = create(key="cleanup\0key")
        request_path = self.root / "request.json"
        request_path.write_bytes(encode(request))
        args = cli._parser().parse_args(["write", "--db", str(database), "--request", str(request_path)])
        real_open = cli.open_ledger

        def failing_close_open(path):
            handle = real_open(path)
            real_close = handle.close

            def close():
                real_close()
                raise OSError("synthetic close failure")

            handle.close = close
            return handle

        with patch.object(cli, "open_ledger", side_effect=failing_close_open):
            with self.assertRaises(LedgerError) as caught:
                cli._dispatch(args)
        error = caught.exception
        self.assertEqual(error.code, "IO_ERROR")
        self.assertEqual(error.commit_state, "committed")
        self.assertEqual(error.details["idempotency_key"], request["idempotency_key"])
        with open_ledger(database) as ledger:
            stored = ledger.get_operation(SCOPE, "create_artifact", request["idempotency_key"])
            self.assertEqual(stored["idempotency_record"]["recorded_at"], error.details["recorded_at"])
            self.assertEqual(stored["result"]["artifact_id"], error.details["result_ref"])
            self.assertEqual(ledger.verify()["counts"]["idempotency_records"], 1)

    def test_oversized_error_keeps_exact_known_commit_identity(self):
        import json
        identity = {"result_ref": "artifact:report", "idempotency_scope_ref": "scope:report",
                    "operation_kind": "create_artifact", "idempotency_key": "key\0\n",
                    "recorded_at": "2026-09-20T00:00:00Z"}
        error = LedgerError("IO_ERROR", "x" * cli.MAX_JSON, "committed", dict(identity, noisy="y"))
        response = cli._encode_error(error)
        self.assertLessEqual(len(response), cli.MAX_JSON)
        parsed = json.loads(response)
        self.assertEqual(parsed["commit_state"], "committed")
        self.assertEqual(parsed["details"], identity)


if __name__ == "__main__":
    unittest.main()
