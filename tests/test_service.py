"""Service-specific corruption, reuse, resource and historical-result checks."""

import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from infra_artifact_ledger.fingerprint import canonical_bytes, manifest_digest
from infra_artifact_ledger import service

from acceptance_helpers import (ARTIFACT, SCOPE, append, create, encode,
                                import_kwargs, import_request)


class ServiceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "ledger.sqlite3"
        self.ledger = initialize(self.path)
        self.ledger.execute(encode(create()))

    def tearDown(self):
        self.ledger.close()
        self.directory.cleanup()

    def assert_error(self, code, callback, state="not_committed"):
        with self.assertRaises(LedgerError) as failure:
            callback()
        self.assertEqual((failure.exception.code, failure.exception.commit_state), (code, state))
        return failure.exception

    def append(self, suffix="v1", data=b"first", **kwargs):
        request, payloads = append(suffix, data, **kwargs)
        result = self.ledger.execute(encode(request), payloads=payloads)
        return request, payloads, result

    def test_stored_duplicate_key_and_nonfinite_json_are_integrity_failures(self):
        connection = self.ledger._store.connection
        original = connection.execute("SELECT data FROM records WHERE id=?", (ARTIFACT,)).fetchone()[0]
        malformed = [original[:-1] + ',"namespace_ref":"other"}',
                     original[:-1] + ',"extra":NaN}',
                     original[:-1] + ',"extra":"\\ud800"}']
        for raw in malformed:
            with self.subTest(raw=raw[-50:]):
                connection.execute("UPDATE records SET data=? WHERE id=?", (raw, ARTIFACT))
                self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.get_record("artifact", ARTIFACT), "not_applicable")
                error = self.assert_error("INTEGRITY_FAILURE", self.ledger.verify, "not_applicable")
                self.assertEqual(set(error.details), {"failures", "truncated"})
                connection.execute("UPDATE records SET data=? WHERE id=?", (original, ARTIFACT))

    def test_reference_index_corruption_is_detected_and_never_repaired_by_write(self):
        self.append()
        self.ledger._store.execute("DELETE FROM refs")
        self.assert_error("INTEGRITY_FAILURE", self.ledger.verify, "not_applicable")
        req, payloads = append("v2", b"second")
        self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.execute(encode(req), payloads=payloads))
        self.assertEqual(self.ledger._store.execute("SELECT count(*) FROM refs").fetchone()[0], 0)

    def test_operation_index_disagreement_is_detected_in_query_and_verify(self):
        self.ledger._store.execute("UPDATE operations SET key='other-key'")
        self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.get_operation(SCOPE, "create_artifact", "other-key"), "not_applicable")
        self.assert_error("INTEGRITY_FAILURE", self.ledger.verify, "not_applicable")

    def test_stored_operation_without_fingerprint_does_not_escape_as_internal_error(self):
        raw = self.ledger._store.operation(SCOPE, "create_artifact", "create-quarterly")
        del raw["request_fingerprint"]
        self.ledger._store.execute("UPDATE operations SET data=?", (encode(raw).decode(),))
        self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.execute(encode(create())))

    def test_unknown_existing_format_and_missing_path_are_never_initialized(self):
        absent = Path(self.directory.name) / "absent.sqlite"
        self.assert_error("NOT_FOUND", lambda: open_ledger(absent), "not_applicable")
        self.assertFalse(absent.exists())
        unknown = Path(self.directory.name) / "unknown.sqlite"
        with sqlite3.connect(unknown) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        before = unknown.read_bytes()
        self.assert_error("UNSUPPORTED_VERSION", lambda: open_ledger(unknown), "not_applicable")
        self.assertEqual(unknown.read_bytes(), before)

    def test_close_is_idempotent_and_later_calls_use_public_errors(self):
        self.ledger.close()
        self.ledger.close()
        self.assert_error("IO_ERROR", lambda: self.ledger.execute(encode(create())))
        self.assert_error("IO_ERROR", lambda: self.ledger.get_record("artifact", ARTIFACT), "not_applicable")

    def test_response_limit_includes_the_envelope_and_final_lf(self):
        data = self.ledger.get_record("artifact", ARTIFACT)
        exact = len(canonical_bytes({"status": "OK", "commit_state": "not_applicable", "data": data})) + 1
        with patch.object(service, "MAX_JSON", exact):
            self.assertEqual(self.ledger.get_record("artifact", ARTIFACT), data)
        with patch.object(service, "MAX_JSON", exact - 1):
            self.assert_error("RESOURCE_LIMIT", lambda: self.ledger.get_record("artifact", ARTIFACT), "not_applicable")

    def test_ledger_cumulative_bytes_are_not_the_per_call_payload_limit(self):
        with patch.object(service, "MAX_PAYLOAD", 10):
            self.append("v1", b"12345678")
            self.append("v2", b"87654321")
            self.assertEqual(self.ledger.verify()["verified_byte_length"], 16)
            self.assert_error("RESOURCE_LIMIT", self.ledger.export_bundle, "not_applicable")
            self.assertEqual(self.ledger.read_blob("blob:quarterly-v1"), b"12345678")

    def manifest_request(self, refs):
        request, _ = append("aggregate", b"")
        body = request["body"]
        entries = [{"entry_key": str(i), "blob_ref": ref} for i, ref in enumerate(refs)]
        body["content_roots"] = [{"content_root_ref": body["version"]["content_root_ref"],
                                  "kind": "manifest", "manifest_ref": "manifest:aggregate"}]
        body["blobs"] = []
        body["manifests"] = [{"manifest_ref": "manifest:aggregate", "digest": manifest_digest(entries), "entries": entries}]
        return request

    def test_reused_content_closure_counts_distinct_ids_even_without_retransmission(self):
        self.append("v1", b"12345678")
        self.append("v2", b"12345678")
        request = self.manifest_request(["blob:quarterly-v1", "blob:quarterly-v2"])
        with patch.object(service, "MAX_PAYLOAD", 10):
            self.assert_error("RESOURCE_LIMIT", lambda: self.ledger.execute(encode(request), payloads={}))
        self.assertEqual(len(self.ledger.get_history(ARTIFACT)["versions"]), 2)

    def test_manifest_repeated_blob_references_count_once(self):
        self.append("v1", b"12345678")
        request = self.manifest_request(["blob:quarterly-v1", "blob:quarterly-v1"])
        with patch.object(service, "MAX_PAYLOAD", 10):
            self.assertEqual(self.ledger.execute(encode(request), payloads={})["status"], "COMMITTED")
        self.assertEqual(self.ledger.verify()["verified_byte_length"], 8)

    def test_append_rejects_unrelated_even_identical_reused_content(self):
        old, _, _ = self.append()
        req, payloads = append("v2", b"second")
        req["body"]["content_roots"].extend(old["body"]["content_roots"])
        self.assert_error("INVALID_INPUT", lambda: self.ledger.execute(encode(req), payloads=payloads))
        self.assertEqual(len(self.ledger.get_history(ARTIFACT)["versions"]), 1)

    def test_reusing_corrupt_blob_cannot_repair_it_with_correct_input(self):
        original, payloads, _ = self.append()
        self.ledger._store.execute("UPDATE payloads SET data=?", (b"BROKEN",))
        request = copy.deepcopy(original)
        request["idempotency_key"] = "reuse"
        request["body"]["version"]["version_id"] = "version:reuse"
        self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.execute(encode(request), payloads=payloads))
        self.assertEqual(self.ledger._store.execute("SELECT data FROM payloads").fetchone()[0], b"BROKEN")

    def test_replay_extra_payload_is_invalid_input_not_internal_or_new_commit(self):
        request, payloads, result = self.append()
        bad = dict(payloads, **{"blob:extra": b""})
        self.assert_error("INVALID_INPUT", lambda: self.ledger.execute(encode(request), payloads=bad))
        replay = self.ledger.execute(encode(request), payloads=payloads)
        self.assertEqual(replay["result_ref"], result["result_ref"])
        self.assertTrue(replay["replayed"])

    def test_replay_bad_reused_payload_is_rejected_without_claiming_stored_corruption(self):
        self.append()
        request, _ = append("reuse", b"")
        request["body"]["version"]["content_root_ref"] = "root:quarterly-v1"
        request["body"]["content_roots"] = []
        request["body"]["blobs"] = []
        self.ledger.execute(encode(request), payloads={})
        self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.execute(encode(request), payloads={"blob:quarterly-v1": b"wrong"}))
        self.assertEqual(self.ledger.read_blob("blob:quarterly-v1"), b"first")

    def test_replay_detects_schema_valid_result_metadata_changed_after_commit(self):
        artifact = self.ledger.get_record("artifact", ARTIFACT)
        artifact["namespace_ref"] = "changed:namespace"
        self.ledger._store.execute("UPDATE records SET data=? WHERE id=?", (encode(artifact).decode(), ARTIFACT))
        error = self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.execute(encode(create())), "committed")
        self.assertEqual(error.details["result_ref"], ARTIFACT)

    def test_replay_detects_schema_valid_success_result_reference_corruption(self):
        self.ledger.execute(encode(create("artifact:other", key="create-other")))
        record = self.ledger._store.operation(SCOPE, "create_artifact", "create-quarterly")
        record["result_ref"] = "artifact:other"
        self.ledger._store.execute("UPDATE operations SET data=?, result_ref=? WHERE key=?", (
            encode(record).decode(), "artifact:other", "create-quarterly"))
        self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.execute(encode(create())), "committed")

    def test_import_replay_validates_original_records_while_permitting_new_history(self):
        self.append()
        bundle = self.ledger.export_bundle()
        destination = initialize(Path(self.directory.name) / "destination.sqlite")
        try:
            request = import_request()
            destination.execute(encode(request), **import_kwargs(bundle))
            artifact = destination.get_record("artifact", ARTIFACT)
            artifact["namespace_ref"] = "changed:namespace"
            destination._store.execute("UPDATE records SET data=? WHERE id=?", (encode(artifact).decode(), ARTIFACT))
            self.assert_error("INTEGRITY_FAILURE", lambda: destination.execute(encode(request), **import_kwargs(bundle)), "committed")
        finally:
            destination.close()

    def test_import_cannot_take_the_identity_of_an_operation_inside_the_package(self):
        empty = initialize(Path(self.directory.name) / "empty.sqlite")
        source = initialize(Path(self.directory.name) / "source.sqlite")
        target = initialize(Path(self.directory.name) / "target.sqlite")
        try:
            request = import_request("historical")
            source.execute(encode(request), **import_kwargs(empty.export_bundle()))
            bundle = source.export_bundle()
            colliding = copy.deepcopy(request)
            colliding["body"]["import_receipt_id"] = "receipt:new"
            self.assert_error("IDEMPOTENCY_CONFLICT", lambda: target.execute(encode(colliding), **import_kwargs(bundle)))
            self.assertEqual(target.verify()["counts"]["import_receipts"], 0)
        finally:
            empty.close()
            source.close()
            target.close()

    def test_verify_reports_at_most_100_failures_but_checks_every_blob(self):
        for index in range(101):
            self.append(f"item-{index}", b"valid")
        self.ledger._store.execute("UPDATE payloads SET data=?", (b"invalid",))
        with patch.object(self.ledger, "_blob", wraps=self.ledger._blob) as check:
            error = self.assert_error("INTEGRITY_FAILURE", self.ledger.verify, "not_applicable")
            self.assertEqual(check.call_count, 101)
        self.assertEqual(len(error.details["failures"]), 100)
        self.assertTrue(error.details["truncated"])

    def test_exception_after_begin_is_rolled_back_and_handle_can_continue(self):
        begin = self.ledger._store.begin

        def fail_after_begin(write=False):
            begin(write=write)
            raise OSError("simulated failure immediately after BEGIN")

        with patch.object(self.ledger._store, "begin", side_effect=fail_after_begin):
            self.assert_error("IO_ERROR", lambda: self.ledger.execute(encode(create("artifact:later", key="later"))))
        self.assertFalse(self.ledger._store.connection.in_transaction)
        self.assertEqual(self.ledger.execute(encode(create("artifact:later", key="later")))["status"], "COMMITTED")


if __name__ == "__main__":
    unittest.main()
