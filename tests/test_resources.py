"""Explicit, expensive profile-boundary acceptance at the unmodified limits.

Run separately: PYTHONPATH=src python tests/test_resources.py -v
Normal unittest discovery skips this suite to avoid repeatedly allocating large
synthetic data. No runtime limit is patched, and no fixture payload is committed.
"""
from __future__ import annotations

import gc
import json
from pathlib import Path
import platform
import resource
import sqlite3
import tempfile
import time
import unittest

from infra_artifact_ledger import LedgerError, initialize
from infra_artifact_ledger.portable import decode_bundle, encode_bundle
from infra_artifact_ledger.records import empty_metadata
from infra_artifact_ledger.validation import parse_json

try:
    from .acceptance_helpers import ARTIFACT, append, create, digest, encode, import_kwargs, import_request, repackage
except ImportError:
    from acceptance_helpers import ARTIFACT, append, create, digest, encode, import_kwargs, import_request, repackage

MiB = 1024 * 1024
EXPLICIT_RESOURCE_RUN = __name__ == "__main__"


@unittest.skipUnless(EXPLICIT_RESOURCE_RUN, "run tests/test_resources.py explicitly for actual profile limits")
class ActualProfileResourceTests(unittest.TestCase):
    def setUp(self):
        self.started = time.monotonic()
        self.temp = tempfile.TemporaryDirectory(prefix="ledger-resource-")
        self.root = Path(self.temp.name)
        self.ledger = initialize(self.root / "ledger.sqlite")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()
        gc.collect()
        print(json.dumps({
            "case": self._testMethodName, "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version, "platform": platform.platform(),
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }), flush=True)

    def limited(self, function, *args, expected_state="not_committed", **kwargs):
        with self.assertRaises(LedgerError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, "RESOURCE_LIMIT")
        self.assertEqual(caught.exception.commit_state, expected_state)

    def test_json_container_depth_15_16_17_and_nodes_499999_500000_500001(self):
        for depth in (15, 16):
            raw = b"[" * depth + b"0" + b"]" * depth
            parse_json(raw)
        self.limited(parse_json, b"[" * 17 + b"0" + b"]" * 17)
        # One root array + N scalar values. Object keys are not value nodes.
        for node_count in (499999, 500000):
            raw = b"[" + b"0," * (node_count - 2) + b"0]"
            self.assertEqual(len(parse_json(raw)), node_count - 1)
        raw = b"[" + b"0," * 499999 + b"0]"
        self.limited(parse_json, raw)
        self.assertEqual(self.ledger.verify()["counts"]["artifacts"], 0)

    def test_write_request_serialized_8mib_minus_one_equal_plus_one(self):
        limit = 8 * MiB
        for length, name in ((limit - 1, "below"), (limit, "at")):
            raw = encode(create(f"artifact:{name}", name))
            raw += b" " * (length - len(raw))
            self.assertEqual(len(raw), length)
            self.assertFalse(self.ledger.execute(raw)["replayed"])
        before = self.ledger.verify()
        raw = encode(create("artifact:over", "over"))
        raw += b" " * (limit + 1 - len(raw))
        self.limited(self.ledger.execute, raw)
        self.assertEqual(self.ledger.verify(), before)

    def test_public_history_response_limit_includes_complete_envelope_and_lf(self):
        def fill_references(added_bytes):
            # For an ASCII string list, N values add sum(lengths)+3*N-1
            # bytes relative to []. This is an independent fixture calculation.
            count, remainder = divmod(added_bytes + 1, 515)
            values = [(f"ref:{index:08d}:" + "x" * 499) for index in range(count)]
            self.assertTrue(all(len(value) == 512 for value in values))
            if remainder:
                if remainder < 4:
                    values[-1] = values[-1][:-(4 - remainder)]
                    remainder = 4
                values.append("q" * (remainder - 3))
            self.assertEqual(len(encode(values)) - 2, added_bytes)
            return values

        for length in (8 * MiB - 1, 8 * MiB, 8 * MiB + 1):
            with self.subTest(envelope_bytes=length), initialize(self.root / f"history-{length}.sqlite") as ledger:
                ledger.execute(encode(create()))
                first, payloads = append("history-first", b"a")
                first["body"]["version"]["external_source_refs"] = fill_references(4 * MiB)
                ledger.execute(encode(first), payloads=payloads)
                artifact = ledger.get_record("artifact", ARTIFACT)
                first_record = ledger.get_record("version", "version:quarterly-history-first")
                second, payloads = append("history-second", b"b")
                predicted_record = {**second["body"]["version"], "recorded_at": first_record["recorded_at"]}
                predicted_history = {"artifact": artifact, "versions": [first_record, predicted_record], "provenance_links": []}
                envelope = {"status": "OK", "commit_state": "not_applicable", "data": predicted_history}
                padding = length - len(encode(envelope)) - 1
                second["body"]["version"]["external_source_refs"] = fill_references(padding)
                ledger.execute(encode(second), payloads=payloads)
                if length <= 8 * MiB:
                    actual = ledger.get_history(ARTIFACT)
                    actual_envelope = {"status": "OK", "commit_state": "not_applicable", "data": actual}
                    self.assertEqual(len(encode(actual_envelope)) + 1, length)
                else:
                    self.limited(ledger.get_history, ARTIFACT, expected_state="not_applicable")
                self.assertEqual(ledger.verify()["counts"]["versions"], 2)

    def test_descriptor_4095_4096_4097_bytes(self):
        bundle = encode_bundle(empty_metadata(), {})
        original = bundle["descriptor_utf8"]
        for length in (4095, 4096):
            padded = original + b" " * (length - len(original))
            metadata, payloads = decode_bundle(bundle["package_utf8"], padded)
            self.assertEqual(metadata, empty_metadata())
            self.assertEqual(payloads, {})
        self.limited(decode_bundle, bundle["package_utf8"], original + b" " * (4097 - len(original)))

    def test_package_384mib_minus_one_equal_plus_one(self):
        original = encode_bundle(empty_metadata(), {})["package_utf8"]
        limit = 384 * MiB
        for length in (limit - 1, limit):
            raw = original + b" " * (length - len(original))
            descriptor = encode({"transport_version": "0.1.0", "package_byte_length": length,
                                 "package_digest": digest(raw)})
            metadata, payloads = decode_bundle(raw, descriptor)
            self.assertEqual(metadata, empty_metadata())
            self.assertEqual(payloads, {})
            del raw, descriptor
            gc.collect()
        raw = original + b" " * (limit + 1 - len(original))
        self.limited(decode_bundle, raw, b"{}")

    def test_blob_real_64mib_minus_one_equal_plus_one(self):
        self.ledger.execute(encode(create()))
        for length, suffix in ((64 * MiB - 1, "below"), (64 * MiB, "at")):
            payload = b"x" * length
            req, payloads = append(suffix, payload)
            self.ledger.execute(encode(req), payloads=payloads)
            self.assertEqual(self.ledger.read_blob(f"blob:quarterly-{suffix}"), payload)
            del payload, payloads
        before = self.ledger.verify()
        payload = b"x" * (64 * MiB + 1)
        req, payloads = append("over", payload)
        self.limited(self.ledger.execute, encode(req), payloads=payloads)
        self.assertEqual(self.ledger.verify(), before)

    def test_incoming_total_reused_closure_and_decoded_bundle_at_256mib(self):
        self.ledger.execute(encode(create()))
        payload = b"r" * (64 * MiB)
        req, _ = append("four", b"")
        entries = [{"entry_key": str(index), "blob_ref": f"blob:chunk-{index}"} for index in range(4)]
        manifest_digest = digest(json.dumps({"entries": entries}, sort_keys=True,
                                            separators=(",", ":")).encode())
        req["body"]["content_roots"] = [{"content_root_ref": "root:quarterly-four",
                                            "kind": "manifest", "manifest_ref": "manifest:four"}]
        req["body"]["manifests"] = [{"manifest_ref": "manifest:four", "entries": entries,
                                      "digest": manifest_digest}]
        req["body"]["blobs"] = [{"blob_ref": entry["blob_ref"], "digest": digest(payload),
                                  "byte_length": len(payload), "payload_availability": "available"}
                                 for entry in entries]
        # Four distinct BlobRefs count four times even when the immutable bytes
        # object is shared in this caller. SQLite must persist 256 MiB of content.
        payloads = {entry["blob_ref"]: payload for entry in entries}
        self.ledger.execute(encode(req), payloads=payloads)
        self.assertEqual(self.ledger.verify()["verified_byte_length"], 256 * MiB)
        reuse, _ = append("reuse-four", b"")
        reuse["body"]["version"]["content_root_ref"] = "root:quarterly-four"
        reuse["body"]["content_roots"] = reuse["body"]["blobs"] = []
        self.ledger.execute(encode(reuse), payloads={})
        bundle = self.ledger.export_bundle()
        metadata, recovered_payloads = decode_bundle(bundle["package_utf8"], bundle["descriptor_utf8"])
        self.assertEqual(sum(map(len, recovered_payloads.values())), 256 * MiB)
        self.assertEqual(set(recovered_payloads), set(payloads))
        del recovered_payloads, metadata, bundle
        gc.collect()
        # A fifth one-byte historical Blob is legal: ledger lifetime size is not
        # the per-operation/per-version limit. Reusing all five in one Version is not.
        extra, extra_payload = append("extra", b"z")
        self.ledger.execute(encode(extra), payloads=extra_payload)
        before = self.ledger.verify()
        closure, _ = append("over-closure", b"")
        over_entries = entries + [{"entry_key": "4", "blob_ref": "blob:quarterly-extra"}]
        closure["body"]["content_roots"] = [{"content_root_ref": "root:quarterly-over-closure",
                                                "kind": "manifest", "manifest_ref": "manifest:over"}]
        closure["body"]["blobs"] = []
        closure["body"]["manifests"] = [{"manifest_ref": "manifest:over", "entries": over_entries,
                                         "digest": digest(json.dumps({"entries": over_entries}, sort_keys=True,
                                                                      separators=(",", ":")).encode())}]
        self.limited(self.ledger.execute, encode(closure), payloads={})
        self.assertEqual(self.ledger.verify(), before)
        # Explicitly supplied bytes also exceed the incoming 256 MiB limit by one.
        self.limited(self.ledger.execute, encode(closure), payloads={**payloads, **extra_payload})
        self.assertEqual(self.ledger.verify(), before)
        self.limited(self.ledger.export_bundle, expected_state="not_applicable")

    def test_decoded_payload_total_256mib_minus_one_and_plus_one(self):
        metadata = empty_metadata()
        timestamp = "2026-09-20T00:00:00Z"
        metadata["artifacts"].append({**create()["body"]["artifact"], "recorded_at": timestamp})
        content = b"b" * (64 * MiB)
        payloads = {}
        for index in range(4):
            value = content if index < 3 else content[:-1]
            req, supplied = append(f"limit-{index}", value)
            body = req["body"]
            metadata["versions"].append({**body["version"], "recorded_at": timestamp})
            metadata["content_roots"].extend(body["content_roots"])
            metadata["blobs"].extend(body["blobs"])
            payloads.update(supplied)
        bundle = encode_bundle(metadata, payloads)
        recovered_metadata, recovered_payloads = decode_bundle(bundle["package_utf8"], bundle["descriptor_utf8"])
        self.assertEqual(sum(map(len, recovered_payloads.values())), 256 * MiB - 1)
        self.assertEqual(recovered_metadata, metadata)
        # This also uses the real import transaction for the below-limit input.
        self.ledger.execute(encode(import_request("below-total")), **import_kwargs(bundle))
        self.assertEqual(self.ledger.verify()["verified_byte_length"], 256 * MiB - 1)
        del bundle, recovered_metadata, recovered_payloads
        gc.collect()
        extra, supplied = append("limit-extra", b"xy")
        metadata["versions"].append({**extra["body"]["version"], "recorded_at": timestamp})
        metadata["content_roots"].extend(extra["body"]["content_roots"])
        metadata["blobs"].extend(extra["body"]["blobs"])
        payloads.update(supplied)
        # Each Version is independently below its closure limit, while the
        # transport contains 256 MiB + 1 in total. The independent fixture writer
        # deliberately does not impose the implementation's export precondition.
        bundle = repackage(metadata, payloads)
        self.limited(decode_bundle, bundle["package_utf8"], bundle["descriptor_utf8"])
        with initialize(self.root / "over-total.sqlite") as target:
            self.limited(target.execute, encode(import_request("over-total")), **import_kwargs(bundle))
            self.assertEqual(target.verify()["counts"]["artifacts"], 0)


if __name__ == "__main__":
    unittest.main()
