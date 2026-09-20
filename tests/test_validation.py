import copy
from itertools import combinations
import json
import unittest

from infra_artifact_ledger.errors import EXIT_CODES, LedgerError
from infra_artifact_ledger.fingerprint import manifest_digest
from infra_artifact_ledger.records import KINDS, empty_metadata
from infra_artifact_ledger.validation import (
    MAX_BLOB, MAX_INTEGER, check_payload, content_blob_refs, parse_json,
    validate_metadata, validate_operation_query, validate_record, validate_request,
)

TIME = "2026-09-20T00:00:00Z"
EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def fixture():
    state = empty_metadata()
    state["artifacts"] = [{"artifact_id": "artifact:a", "namespace_ref": "demo:a",
                           "type_ref": {"namespace": "demo", "type_id": "report", "type_version": "1"},
                           "recorded_at": TIME}]
    state["versions"] = [{"version_id": "version:a", "artifact_id": "artifact:a",
                          "content_root_ref": "root:aaa", "parent_version_refs": [],
                          "external_source_refs": [], "capture_refs": [],
                          "handling_policy_refs": [], "recorded_at": TIME}]
    state["content_roots"] = [{"content_root_ref": "root:aaa", "kind": "blob", "blob_ref": "blob:aaa"}]
    state["blobs"] = [{"blob_ref": "blob:aaa", "digest": {"algorithm": "sha256", "value": EMPTY_HASH},
                       "byte_length": 0, "payload_availability": "available"}]
    return state


class ValidationTests(unittest.TestCase):
    def rejects(self, code, call, *args, **kwargs):
        with self.assertRaises(LedgerError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_strict_json_rejections(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}', b'NaN', b'Infinity',
                    b'-Infinity', b'1e9999', b'\xef\xbb\xbf{}', b'"\xff"', b'{}{}',
                    b'"\\ud800"', b'{"\\udfff":0}', b'"unclosed', b'{"x":01}', b'[1,]'):
            with self.subTest(raw=raw):
                self.rejects("INVALID_INPUT", parse_json, raw)
        self.rejects("INVALID_INPUT", parse_json, bytearray(b"{}"))
        self.rejects("RESOURCE_LIMIT", parse_json, b"[" * 10000 + b"]" * 10000)

    def test_utf8_surrogate_pairs_and_string_escapes(self):
        self.assertEqual(parse_json(b'"\\ud83d\\ude00"'), "😀")
        self.assertEqual(parse_json(b'{"x":"[{{\\\"\\\\", "y":2}'), {"x": '[{{"\\', "y": 2})
        self.assertEqual(parse_json(b'{"b":2,"a":1}'), {"b": 2, "a": 1})
        self.assertEqual(list(parse_json(b'{"b":2,"a":1}')), ["b", "a"])

    def test_exact_serialized_and_depth_boundaries(self):
        self.assertEqual(parse_json(b'"abc"', limit=5), "abc")
        self.rejects("RESOURCE_LIMIT", parse_json, b'"abcd"', limit=5)
        self.assertEqual(parse_json(b"[" * 16 + b"0" + b"]" * 16)[0][0][0][0][0][0][0][0]
                         [0][0][0][0][0][0][0][0], 0)
        self.rejects("RESOURCE_LIMIT", parse_json, b"[" * 17 + b"0" + b"]" * 17)

    def test_integer_token_semantics(self):
        blob = fixture()["blobs"][0]
        for token in ("0.0", "0e0", "true", "-1"):
            value = parse_json(json.dumps(blob).replace('"byte_length": 0', '"byte_length": ' + token).encode())
            self.rejects("INVALID_INPUT", validate_record, "blob", value)
        blob["byte_length"] = MAX_INTEGER + 1
        self.rejects("RESOURCE_LIMIT", validate_record, "blob", blob)
        blob["byte_length"] = MAX_BLOB
        validate_record("blob", blob)
        blob["byte_length"] += 1
        self.rejects("RESOURCE_LIMIT", validate_record, "blob", blob)

    def test_record_unknown_fields_and_scalar_constraints(self):
        state = fixture()
        for kind, (collection, _) in KINDS.items():
            if state[collection]:
                bad = copy.deepcopy(state[collection][0])
                bad["unexpected"] = 0
                self.rejects("INVALID_INPUT", validate_record, kind, bad)
        artifact = state["artifacts"][0]
        for identity in ("ab", "-aaa", "aéa", "a b", "a" * 257):
            bad = {**artifact, "artifact_id": identity}
            self.rejects("INVALID_INPUT", validate_record, "artifact", bad)
        for namespace in ("", "a\x00b", "a\x7fb", "a" * 513, "\ud800"):
            bad = {**artifact, "namespace_ref": namespace}
            self.rejects("INVALID_INPUT", validate_record, "artifact", bad)
        bad = copy.deepcopy(artifact)
        bad["type_ref"]["type_version"] = ""
        self.rejects("INVALID_INPUT", validate_record, "artifact", bad)

    def test_times_and_new_record_rejects_caller_recorded_at(self):
        artifact = fixture()["artifacts"][0]
        for time in ("2024-02-29T12:34:56.123+08:00", "2016-12-31T23:59:60Z", "2026-09-20t00:00:00z",
                     "2017-01-01T07:59:60+08:00", "2016-12-31T15:59:60-08:00", "0000-02-29T00:00:00Z"):
            validate_record("artifact", {**artifact, "recorded_at": time})
        for time in ("2026-02-29T00:00:00Z", "2026-09-20", "2026-09-20T25:00:00Z",
                     "2026-09-20T00:00:00", "2026-09-20T00:00:00+24:00",
                     "2016-12-30T23:59:60Z", "2016-12-31T22:59:60Z", "2017-01-01T08:00:60+08:00"):
            self.rejects("INVALID_INPUT", validate_record, "artifact", {**artifact, "recorded_at": time})
        self.rejects("INVALID_INPUT", validate_record, "artifact", artifact, new=True)
        del artifact["recorded_at"]
        validate_record("artifact", artifact, new=True)

    def test_request_and_query_control_character_key(self):
        artifact = fixture()["artifacts"][0]
        del artifact["recorded_at"]
        query = {"idempotency_scope_ref": "demo:a", "operation_kind": "create_artifact", "idempotency_key": "key\x00\n"}
        request = {**query, "profile": "bounded-local-v0.1", "contract_version": "0.1.0", "body": {"artifact": artifact}}
        self.assertIs(validate_request(request), request)
        self.assertIs(validate_operation_query(query), query)
        self.rejects("UNSUPPORTED_PROFILE", validate_request, {**request, "profile": "other"})
        self.rejects("UNSUPPORTED_VERSION", validate_request, {**request, "contract_version": "2"})
        self.rejects("INVALID_INPUT", validate_operation_query, {**query, "unexpected": True})
        self.rejects("INVALID_INPUT", validate_operation_query, {**query, "operation_kind": []})
        imported = {**request, "operation_kind": "import_bundle", "body": {"import_receipt_id": "receipt:a"}}
        validate_request(imported)
        imported["body"]["bundle"] = empty_metadata()
        self.rejects("INVALID_INPUT", validate_request, imported)

    def test_empty_metadata_optional_receipts_preserved(self):
        state = empty_metadata()
        del state["import_receipts"]
        self.assertIs(validate_metadata(state), state)
        self.assertNotIn("import_receipts", state)
        self.assertIsNot(empty_metadata()["artifacts"], empty_metadata()["artifacts"])
        bad = copy.deepcopy(state)
        bad["theory_baseline"]["commit"] = "0" * 40
        self.rejects("UNSUPPORTED_VERSION", validate_metadata, bad)

    def test_all_seven_owned_ids_share_collision_domain(self):
        state = fixture()
        entries = [{"entry_key": "x", "blob_ref": "blob:aaa"}]
        state["manifests"] = [{"manifest_ref": "manifest:a", "entries": entries, "digest": manifest_digest(entries)}]
        state["content_roots"] = [{"content_root_ref": "root:aaa", "kind": "manifest", "manifest_ref": "manifest:a"}]
        state["provenance_links"] = [{"provenance_id": "provenance:a", "subject_version_ref": "version:a",
                                     "relation_ref": {"namespace": "demo", "relation_id": "derived", "relation_version": "1"},
                                     "object": {"kind": "other", "ref": "external:a"}, "recorded_at": TIME}]
        state["import_receipts"] = [{"import_receipt_id": "receipt:a", "imported_artifact_refs": ["artifact:a"], "recorded_at": TIME}]
        validate_metadata(state)
        for first, second in combinations(KINDS.values(), 2):
            with self.subTest(first=first[0], second=second[0]):
                bad = copy.deepcopy(state)
                bad[second[0]][0][second[1]] = bad[first[0]][0][first[1]]
                self.rejects("IDENTITY_CONFLICT", validate_metadata, bad)
        state = fixture()
        state["artifacts"].append(copy.deepcopy(state["artifacts"][0]))
        self.rejects("IDENTITY_CONFLICT", validate_metadata, state)

    def test_unknown_field_diagnostic_does_not_echo_unbounded_input(self):
        state = empty_metadata()
        state["x" * (1024 * 1024)] = 0
        error = self.rejects("INVALID_INPUT", validate_metadata, state)
        self.assertLess(len(json.dumps(error.to_envelope())), 512)

    def test_dangling_and_unreachable_content_rejected(self):
        for field in ("artifact_id", "content_root_ref"):
            state = fixture()
            state["versions"][0][field] = "missing:ref"
            self.rejects("INTEGRITY_FAILURE", validate_metadata, state)
        state = fixture()
        state["blobs"].append({**state["blobs"][0], "blob_ref": "blob:unused"})
        self.rejects("INTEGRITY_FAILURE", validate_metadata, state)
        validate_metadata(state, check_closure=False)

    def test_parent_dag_branch_merge_cycle_and_cross_artifact(self):
        state = fixture()
        first = state["versions"][0]
        state["versions"].extend([
            {**first, "version_id": "version:b", "parent_version_refs": ["version:a"]},
            {**first, "version_id": "version:c", "parent_version_refs": ["version:a"]},
            {**first, "version_id": "version:d", "parent_version_refs": ["version:b", "version:c"]},
        ])
        validate_metadata(state)
        first["parent_version_refs"] = ["version:d"]
        self.rejects("INTEGRITY_FAILURE", validate_metadata, state)
        first["parent_version_refs"] = ["version:a"]
        self.rejects("INTEGRITY_FAILURE", validate_metadata, state)
        first["parent_version_refs"] = ["version:missing"]
        self.rejects("INTEGRITY_FAILURE", validate_metadata, state)
        first["parent_version_refs"] = []
        state["artifacts"].append({**state["artifacts"][0], "artifact_id": "artifact:b"})
        state["versions"][1]["artifact_id"] = "artifact:b"
        self.rejects("INTEGRITY_FAILURE", validate_metadata, state)

    def test_manifest_digest_exact_keys_and_distinct_blob_closure(self):
        state = fixture()
        entries = [{"entry_key": key, "blob_ref": "blob:aaa"} for key in ("A", "a", "é", "e\u0301", "../x")]
        manifest = {"manifest_ref": "manifest:a", "entries": entries, "digest": manifest_digest(entries)}
        state["manifests"] = [manifest]
        state["content_roots"] = [{"content_root_ref": "root:aaa", "kind": "manifest", "manifest_ref": "manifest:a"}]
        validate_metadata(state)
        self.assertEqual(content_blob_refs(state, state["versions"][0]), {"blob:aaa"})
        manifest["digest"]["value"] = "0" * 64
        self.rejects("INTEGRITY_FAILURE", validate_metadata, state)
        entries.append(dict(entries[0]))
        manifest["digest"] = manifest_digest(entries)
        self.rejects("INVALID_INPUT", validate_metadata, state)

    def test_per_version_limit_is_not_a_ledger_lifetime_limit(self):
        state = fixture()
        first = state["versions"][0]
        state["blobs"][0]["byte_length"] = MAX_BLOB
        for i in range(1, 5):
            state["blobs"].append({**state["blobs"][0], "blob_ref": f"blob:{i:03}"})
            state["content_roots"].append({"content_root_ref": f"root:{i:03}", "kind": "blob", "blob_ref": f"blob:{i:03}"})
            state["versions"].append({**first, "version_id": f"version:{i:03}", "content_root_ref": f"root:{i:03}"})
        validate_metadata(state)  # Five historical 64 MiB versions are legal.
        entries = [{"entry_key": str(i), "blob_ref": blob["blob_ref"]} for i, blob in enumerate(state["blobs"])]
        manifest = {"manifest_ref": "manifest:a", "entries": entries[:4], "digest": manifest_digest(entries[:4])}
        state["manifests"] = [manifest]
        state["content_roots"].append({"content_root_ref": "root:all", "kind": "manifest", "manifest_ref": "manifest:a"})
        state["versions"].append({**first, "version_id": "version:all", "content_root_ref": "root:all"})
        validate_metadata(state)
        manifest["entries"] = entries
        manifest["digest"] = manifest_digest(entries)
        self.rejects("RESOURCE_LIMIT", validate_metadata, state)

    def test_provenance_receipt_and_idempotency_reference_types(self):
        state = fixture()
        state["provenance_links"] = [{"provenance_id": "provenance:a", "subject_version_ref": "version:a",
                                     "relation_ref": {"namespace": "demo", "relation_id": "derived", "relation_version": "1"},
                                     "object": {"kind": "artifact_version", "ref": "version:a"}, "recorded_at": TIME}]
        state["import_receipts"] = [{"import_receipt_id": "receipt:a", "imported_artifact_refs": ["artifact:a"], "recorded_at": TIME}]
        operation = {"idempotency_scope_ref": "demo:a", "operation_kind": "import_bundle", "idempotency_key": "key\x00",
                     "request_fingerprint": {"algorithm": "sha256", "value": "0" * 64}, "result_ref": "receipt:a", "recorded_at": TIME}
        state["idempotency_records"] = [operation]
        validate_metadata(state)
        operation["result_ref"] = "artifact:a"
        self.rejects("INTEGRITY_FAILURE", validate_metadata, state)
        operation["result_ref"] = "receipt:a"
        state["idempotency_records"].append(copy.deepcopy(operation))
        self.rejects("IDEMPOTENCY_CONFLICT", validate_metadata, state)
        state["idempotency_records"].pop()
        state["provenance_links"][0]["object"]["ref"] = "artifact:a"
        self.rejects("INTEGRITY_FAILURE", validate_metadata, state)

    def test_payload_checks_empty_content_and_erased(self):
        blob = fixture()["blobs"][0]
        check_payload(blob, b"")
        self.rejects("INTEGRITY_FAILURE", check_payload, blob, b"x")
        self.rejects("INVALID_INPUT", check_payload, blob, bytearray())
        blob["payload_availability"] = "erased"
        self.rejects("UNSUPPORTED_PROFILE", check_payload, blob, b"")

    def test_stable_error_envelope_preserves_known_commit_state(self):
        error = LedgerError("INTEGRITY_FAILURE", "damaged", "committed", {"result_ref": "version:a"})
        self.assertEqual(error.to_envelope(), {"status": "ERROR", "code": "INTEGRITY_FAILURE", "message": "damaged",
                                              "commit_state": "committed", "details": {"result_ref": "version:a"}})
        self.assertEqual(EXIT_CODES[error.code], 5)
        self.assertNotIn("details", LedgerError("NOT_FOUND", "missing", "not_applicable").to_envelope())


if __name__ == "__main__":
    unittest.main()
