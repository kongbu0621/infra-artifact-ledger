import copy
import unittest

from infra_artifact_ledger.errors import LedgerError
from infra_artifact_ledger.fingerprint import (
    canonical_bytes, digest_bytes, manifest_digest, request_fingerprint,
)
from infra_artifact_ledger.records import empty_metadata


class FingerprintTests(unittest.TestCase):
    def request(self):
        return {
            "profile": "bounded-local-v0.1", "contract_version": "0.1.0",
            "operation_kind": "create_artifact", "idempotency_scope_ref": "demo:scope",
            "idempotency_key": "first-key", "body": {"artifact": {
                "artifact_id": "artifact:report", "namespace_ref": "demo:reports",
                "type_ref": {"namespace": "demo", "type_id": "report", "type_version": "1"},
            }},
        }

    def test_request_known_bytes_and_independent_sha256_vector(self):
        # Written out independently of the implementation's envelope builder.
        expected = (
            b'{"body":{"artifact":{"artifact_id":"artifact:report","namespace_ref":"demo:reports",'
            b'"type_ref":{"namespace":"demo","type_id":"report","type_version":"1"}}},'
            b'"contract_version":"0.1.0","idempotency_scope_ref":"demo:scope",'
            b'"operation_kind":"create_artifact"}'
        )
        request = self.request()
        envelope = {key: request[key] for key in (
            "contract_version", "idempotency_scope_ref", "operation_kind", "body")}
        self.assertEqual(canonical_bytes(envelope), expected)
        self.assertEqual(request_fingerprint(request), {
            "algorithm": "sha256",
            "value": "9cf1e431202cf2afcbacdacc3d0917bc5a77b409129911f107d902d134cbf8b8",
        })

    def test_digest_standard_known_vectors(self):
        self.assertEqual(digest_bytes(b"")["value"],
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        self.assertEqual(digest_bytes(b"abc")["value"],
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_manifest_exact_encoding_and_known_digest(self):
        entries = [{"entry_key": "报告/é.txt", "blob_ref": "blob:abc"},
                   {"entry_key": "../not-a-path", "blob_ref": "blob:empty"}]
        self.assertEqual(canonical_bytes({"entries": entries}),
                         '{"entries":[{"blob_ref":"blob:abc","entry_key":"报告/é.txt"},'
                         '{"blob_ref":"blob:empty","entry_key":"../not-a-path"}]}'.encode())
        self.assertEqual(manifest_digest(entries)["value"],
                         "1bdde4c4e5c733a4ea8b879a410dc036b86b1da192a9799522cc62ba456ec253")
        self.assertNotEqual(manifest_digest(entries), manifest_digest(list(reversed(entries))))

    def test_control_escapes_unicode_and_slash(self):
        obj = {"s": '"\\\b\t\n\f\r\x00\x1f/é😀', "n": 0, "b": True}
        self.assertEqual(canonical_bytes(obj),
                         b'{"b":true,"n":0,"s":"\\\"\\\\\\b\\t\\n\\f\\r\\u0000\\u001f/'
                         + 'é😀'.encode() + b'"}')
        self.assertNotEqual(canonical_bytes({"s": "é"}), canonical_bytes({"s": "e\u0301"}))

    def test_key_and_claimed_fingerprint_are_excluded_but_body_is_not(self):
        request = self.request()
        original = request_fingerprint(request)
        request["idempotency_key"] = "another-key"
        request["request_fingerprint"] = {"algorithm": "sha256", "value": "0" * 64}
        self.assertEqual(request_fingerprint(request), original)
        request["body"]["artifact"]["type_ref"]["type_version"] = "2"
        self.assertNotEqual(request_fingerprint(request), original)

    def test_import_fingerprint_preserves_absent_optional_and_history_order(self):
        request = self.request()
        request["operation_kind"] = "import_bundle"
        request["body"] = {"import_receipt_id": "receipt:one"}
        bundle = empty_metadata()
        original = request_fingerprint(request, bundle)
        absent = copy.deepcopy(bundle)
        del absent["import_receipts"]
        self.assertNotEqual(request_fingerprint(request, absent), original)
        bundle["artifacts"] = [{"artifact_id": "artifact:b"}, {"artifact_id": "artifact:a"}]
        before = request_fingerprint(request, bundle)
        bundle["artifacts"].reverse()
        self.assertNotEqual(request_fingerprint(request, bundle), before)
        with self.assertRaises(LedgerError):
            request_fingerprint(request)

    def test_invalid_canonical_values_and_cycles_are_domain_errors(self):
        cycle = []
        cycle.append(cycle)
        for value in ({"x": 1.0}, {"x": float("nan")}, {"x": "\ud800"}, {"é": 1}, cycle):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(LedgerError):
                    canonical_bytes(value)


if __name__ == "__main__":
    unittest.main()
