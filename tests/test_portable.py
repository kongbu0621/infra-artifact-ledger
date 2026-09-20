"""Independent transport corruption cases and exact metadata preservation."""

import base64
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from infra_artifact_ledger.errors import LedgerError
from infra_artifact_ledger.fingerprint import request_fingerprint
from infra_artifact_ledger.portable import decode_bundle, encode_bundle
from infra_artifact_ledger.records import PROFILE, TRANSPORT_VERSION, empty_metadata


def sha(data):
    return {"algorithm": "sha256", "value": hashlib.sha256(data).hexdigest()}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def content(data, **extra):
    return {"encoding": "base64", "data": base64.b64encode(data).decode("ascii"),
            "byte_length": len(data), "digest": sha(data), **extra}


def packed(envelope):
    raw = json_bytes(envelope)
    return raw, json_bytes({"transport_version": TRANSPORT_VERSION,
                            "package_byte_length": len(raw), "package_digest": sha(raw)})


def package_for(metadata, payloads=None, metadata_raw=None):
    raw = json_bytes(metadata) if metadata_raw is None else metadata_raw
    return packed({"transport_version": TRANSPORT_VERSION, "profile": PROFILE,
                   "metadata": content(raw),
                   "payloads": [content(data, blob_ref=ref)
                                for ref, data in (payloads or {}).items()]})


def fixture(data=b"abc"):
    metadata = empty_metadata()
    metadata["artifacts"] = [{
        "artifact_id": "artifact:report", "namespace_ref": "示例:e\u0301/é",
        "type_ref": {"namespace": "例", "type_id": "report", "type_version": "1"},
        "recorded_at": "2026-09-20T08:00:00+08:00",
    }]
    metadata["blobs"] = [{"blob_ref": "blob:report", "digest": sha(data),
                          "byte_length": len(data), "payload_availability": "available"}]
    metadata["content_roots"] = [{"content_root_ref": "root:report", "kind": "blob",
                                   "blob_ref": "blob:report"}]
    for version, parents in (("version:z", []), ("version:a", []),
                             ("version:merge", ["version:z", "version:a"])):
        metadata["versions"].append({
            "version_id": version, "artifact_id": "artifact:report",
            "content_root_ref": "root:report", "parent_version_refs": parents,
            "external_source_refs": ["source:z", "source:a"], "capture_refs": [],
            "handling_policy_refs": [], "recorded_at": "2026-09-20T00:00:00Z",
        })
    return metadata, {"blob:report": data}


class PortableTests(unittest.TestCase):
    def assert_error(self, code, fn, *args):
        with self.assertRaises(LedgerError) as raised:
            fn(*args)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.commit_state, "not_committed")

    def mutated(self, mutation):
        metadata, payloads = fixture()
        envelope = json.loads(package_for(metadata, payloads)[0])
        mutation(envelope)
        return packed(envelope)

    def test_empty_roundtrip_and_export_explicit_receipts(self):
        original = empty_metadata()
        del original["import_receipts"]
        before = copy.deepcopy(original)
        exported = encode_bundle(original, {})
        restored, payloads = decode_bundle(exported["package_utf8"], exported["descriptor_utf8"])
        self.assertEqual(original, before)
        self.assertNotIn("import_receipts", original)
        self.assertEqual(restored["import_receipts"], [])
        self.assertEqual(payloads, {})

    def test_nonempty_roundtrip_and_zero_byte_payload(self):
        for data in (b"", b"abc", "中文\U0001f642e\u0301é".encode()):
            with self.subTest(data=data):
                metadata, payloads = fixture(data)
                output = encode_bundle(metadata, payloads)
                restored, decoded = decode_bundle(output["package_utf8"], output["descriptor_utf8"])
                self.assertEqual(decoded, payloads)
                self.assertEqual(restored["artifacts"], metadata["artifacts"])

    def test_export_sorts_collections_without_mutating_record_arrays(self):
        metadata, payloads = fixture()
        for key in ("é", "e\u0301", "\u0000", "中"):
            metadata["idempotency_records"].append({
                "idempotency_scope_ref": "scope:example", "operation_kind": "create_artifact",
                "idempotency_key": key, "request_fingerprint": sha(key.encode()),
                "result_ref": "artifact:report", "recorded_at": "2026-09-20T00:00:00Z",
            })
        original = copy.deepcopy(metadata)
        encoded = encode_bundle(metadata, payloads)
        decoded, _ = decode_bundle(encoded["package_utf8"], encoded["descriptor_utf8"])
        self.assertEqual(metadata, original)
        self.assertEqual([v["version_id"] for v in decoded["versions"]],
                         ["version:a", "version:merge", "version:z"])
        self.assertEqual(decoded["versions"][1]["parent_version_refs"], ["version:z", "version:a"])
        self.assertEqual(decoded["versions"][1]["external_source_refs"], ["source:z", "source:a"])
        self.assertEqual([r["idempotency_key"] for r in decoded["idempotency_records"]],
                         ["\u0000", "e\u0301", "é", "中"])

    def test_decode_preserves_absence_and_array_order_for_import_fingerprint(self):
        metadata, payloads = fixture()
        del metadata["import_receipts"]
        first, _ = decode_bundle(*package_for(metadata, payloads))
        self.assertEqual(first, metadata)
        self.assertNotIn("import_receipts", first)
        changed = copy.deepcopy(metadata)
        changed["versions"].reverse()
        second, _ = decode_bundle(*package_for(changed, payloads))
        request = {"contract_version": "0.1.0", "operation_kind": "import_bundle",
                   "idempotency_scope_ref": "scope:demo", "body": {"import_receipt_id": "receipt:new"}}
        self.assertNotEqual(request_fingerprint(request, first), request_fingerprint(request, second))
        changed = copy.deepcopy(metadata)
        changed["import_receipts"] = []
        self.assertNotEqual(request_fingerprint(request, first), request_fingerprint(request, changed))

    def test_metadata_digest_is_over_exact_raw_bytes(self):
        metadata, payloads = fixture()
        raw = json.dumps(metadata, ensure_ascii=True, indent=3).encode() + b"\n"
        decoded, _ = decode_bundle(*package_for(metadata, payloads, raw))
        self.assertEqual(decoded, metadata)
        package, _ = package_for(metadata, payloads, raw)
        envelope = json.loads(package)
        envelope["metadata"]["digest"] = sha(json_bytes(metadata))
        self.assert_error("INTEGRITY_FAILURE", decode_bundle, *packed(envelope))

    def test_package_descriptor_detects_truncated_changed_and_missing_bytes(self):
        package, descriptor = package_for(empty_metadata())
        for bad in (b"", package[:-1], package + b" ", package.replace(b'"profile"', b'"PROFILE"')):
            with self.subTest(length=len(bad)):
                self.assert_error("INTEGRITY_FAILURE", decode_bundle, bad, descriptor)
        self.assert_error("INVALID_INPUT", decode_bundle, package, b"")

    def test_each_layer_rejects_unknown_fields(self):
        mutations = (
            lambda e: e.update(extra=1),
            lambda e: e["metadata"].update(extra=1),
            lambda e: e["payloads"][0].update(extra=1),
        )
        for change in mutations:
            with self.subTest(change=change):
                self.assert_error("INVALID_INPUT", decode_bundle, *self.mutated(change))
        package, descriptor = package_for(empty_metadata())
        description = json.loads(descriptor)
        description["extra"] = 1
        self.assert_error("INVALID_INPUT", decode_bundle, package, json_bytes(description))

    def test_unknown_transport_and_profile(self):
        for field, value, code in (("profile", "other", "UNSUPPORTED_PROFILE"),
                                   ("transport_version", "9", "UNSUPPORTED_VERSION")):
            with self.subTest(field=field):
                self.assert_error(code, decode_bundle, *self.mutated(lambda e: e.update({field: value})))
        package, description = package_for(empty_metadata())
        description = json.loads(description)
        description["transport_version"] = "9"
        self.assert_error("UNSUPPORTED_VERSION", decode_bundle, package, json_bytes(description))

    def test_payload_missing_extra_and_duplicate(self):
        self.assert_error("INTEGRITY_FAILURE", decode_bundle,
                          *self.mutated(lambda e: e["payloads"].clear()))
        self.assert_error("INVALID_INPUT", decode_bundle,
                          *self.mutated(lambda e: e["payloads"].append(copy.deepcopy(e["payloads"][0]))))
        self.assert_error("INTEGRITY_FAILURE", decode_bundle,
                          *self.mutated(lambda e: e["payloads"].append(content(b"", blob_ref="blob:extra"))))

    def test_actual_payload_digest_and_metadata_agreement(self):
        self.assert_error("INTEGRITY_FAILURE", decode_bundle,
                          *self.mutated(lambda e: e["payloads"][0].update(data="YWJk")))
        self.assert_error("INTEGRITY_FAILURE", decode_bundle,
                          *self.mutated(lambda e: e["payloads"][0].update(digest=sha(b"abd"))))
        self.assert_error("INTEGRITY_FAILURE", decode_bundle,
                          *self.mutated(lambda e: e["payloads"][0].update(byte_length=2)))

    def test_strict_and_noncanonical_base64(self):
        for bad in ("YR==", "YQ=", "YQ==\n", "YQ-=", "YQ===", "YQé=", "===="):
            with self.subTest(bad=bad):
                metadata, payloads = fixture(b"a")
                envelope = json.loads(package_for(metadata, payloads)[0])
                envelope["payloads"][0]["data"] = bad
                self.assert_error("INVALID_INPUT", decode_bundle, *packed(envelope))

    def test_transport_lengths_reject_boolean_float_and_negative(self):
        for value in (True, 3.0, -1):
            with self.subTest(value=value):
                self.assert_error("INVALID_INPUT", decode_bundle,
                                  *self.mutated(lambda e: e["payloads"][0].update(byte_length=value)))
        self.assert_error("RESOURCE_LIMIT", decode_bundle,
                          *self.mutated(lambda e: e["payloads"][0].update(byte_length=9007199254740992)))

    def test_erased_payload_is_unsupported_even_if_bytes_present(self):
        metadata, payloads = fixture()
        metadata["blobs"][0]["payload_availability"] = "erased"
        self.assert_error("UNSUPPORTED_PROFILE", decode_bundle, *package_for(metadata, payloads))
        self.assert_error("UNSUPPORTED_PROFILE", encode_bundle, metadata, payloads)

    def test_malformed_metadata_and_duplicate_json_keys(self):
        for raw in (b'{"a":1,"a":2}', b"\xef\xbb\xbf{}", b'"\\ud800"', b"{} null", b"NaN"):
            with self.subTest(raw=raw):
                self.assert_error("INVALID_INPUT", decode_bundle,
                                  *package_for(empty_metadata(), metadata_raw=raw))

    def test_export_validates_supplied_bytes_and_mapping_coverage(self):
        metadata, payloads = fixture()
        self.assert_error("INTEGRITY_FAILURE", encode_bundle, metadata, {})
        self.assert_error("INTEGRITY_FAILURE", encode_bundle, metadata,
                          {**payloads, "blob:extra": b""})
        self.assert_error("INTEGRITY_FAILURE", encode_bundle, metadata, {"blob:report": b"abd"})
        self.assert_error("INVALID_INPUT", encode_bundle, metadata, {"blob:report": bytearray(b"abc")})

    def test_input_transport_must_be_immutable_bytes(self):
        package, descriptor = package_for(empty_metadata())
        self.assert_error("INVALID_INPUT", decode_bundle, bytearray(package), descriptor)
        self.assert_error("INVALID_INPUT", decode_bundle, package, memoryview(descriptor))

    def test_claimed_oversize_fails_before_base64_decode(self):
        from infra_artifact_ledger.validation import MAX_BLOB
        package, descriptor = self.mutated(lambda e: e["payloads"][0].update(byte_length=MAX_BLOB + 1))
        # metadata decoding legitimately precedes payload preflight, but the
        # oversized payload must never reach the allocating Base64 decoder.
        original = base64.b64decode
        calls = []

        def observed(data, *args, **kwargs):
            calls.append(data)
            return original(data, *args, **kwargs)

        with patch("infra_artifact_ledger.portable.base64.b64decode", side_effect=observed):
            self.assert_error("RESOURCE_LIMIT", decode_bundle, package, descriptor)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
