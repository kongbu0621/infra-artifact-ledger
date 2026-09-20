"""Reject irregular Mapping item streams without silently overwriting payloads.

Ordinary dict mappings cannot contain duplicate keys. These tests exercise the
accepted Mapping interface when a custom implementation violates that usual
expectation, plus ownership of bytes after the source mapping changes.
"""
from collections.abc import Mapping
from pathlib import Path
import tempfile
import unittest

from infra_artifact_ledger import LedgerError, initialize

try:
    from .acceptance_helpers import SCOPE, append, create, encode
except ImportError:
    from acceptance_helpers import SCOPE, append, create, encode


class ItemStream(Mapping):
    """A deliberately irregular Mapping whose items can repeat a key."""

    def __init__(self, pairs):
        self.pairs = pairs
        self.items_calls = 0

    def __iter__(self):
        return iter(key for key, _ in self.pairs)

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, key):
        for candidate, value in self.pairs:
            if candidate == key:
                return value
        raise KeyError(key)

    def items(self):
        self.items_calls += 1
        return iter(self.pairs)


class ChangingMapping(Mapping):
    def __init__(self, values):
        self.values = dict(values)
        self.items_calls = 0

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)

    def __getitem__(self, key):
        return self.values[key]

    def items(self):
        self.items_calls += 1
        captured = list(self.values.items())
        yield from captured
        self.values.clear()
        self.values["blob:replacement"] = b"changed after enumeration"


class PayloadMappingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ledger-mapping-")
        self.ledger = initialize(Path(self.directory.name) / "ledger.sqlite")
        self.ledger.execute(encode(create()))

    def tearDown(self):
        self.ledger.close()
        self.directory.cleanup()

    def reject_without_changes(self, request, mapping):
        before = self.ledger.verify()
        with self.assertRaises(LedgerError) as caught:
            self.ledger.execute(encode(request), payloads=mapping)
        self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assertEqual(caught.exception.commit_state, "not_committed")
        self.assertEqual(mapping.items_calls, 1)
        self.assertEqual(self.ledger.verify(), before)

    def test_duplicate_item_keys_rejected_instead_of_last_value_winning(self):
        request, payloads = append("stream", b"expected bytes")
        ref, expected = next(iter(payloads.items()))
        for first, last in ((b"wrong bytes", expected), (expected, b"wrong bytes"),
                            (expected, expected)):
            with self.subTest(first=first, last=last):
                self.reject_without_changes(request, ItemStream([(ref, first), (ref, last)]))

    def test_duplicate_replay_input_does_not_dispute_the_original_commit(self):
        request, payloads = append("replay", b"original bytes")
        original = self.ledger.execute(encode(request), payloads=payloads)
        ref, content = next(iter(payloads.items()))
        self.reject_without_changes(request, ItemStream([(ref, content), (ref, content)]))
        operation = self.ledger.get_operation(SCOPE, "append_version", request["idempotency_key"])
        self.assertEqual(operation["result"]["version_id"], original["result_ref"])
        replay = self.ledger.execute(encode(request), payloads=payloads)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["result_ref"], original["result_ref"])
        self.assertEqual(replay["recorded_at"], original["recorded_at"])

    def test_source_mutation_after_enumeration_does_not_change_committed_bytes(self):
        request, payloads = append("frozen", b"original bytes")
        source = ChangingMapping(payloads)
        self.ledger.execute(encode(request), payloads=source)
        self.assertEqual(source.items_calls, 1)
        self.assertEqual(source.values, {"blob:replacement": b"changed after enumeration"})
        for ref, content in payloads.items():
            self.assertEqual(self.ledger.read_blob(ref), content)
        self.assertEqual(self.ledger.verify()["counts"]["blobs"], 1)

    def test_invalid_item_key_uses_domain_error_before_hashing(self):
        request, _ = append("invalid-key", b"")
        for key in ([], {}, None, 1, True):
            with self.subTest(key_type=type(key).__name__):
                self.reject_without_changes(request, ItemStream([(key, b"")]))


if __name__ == "__main__":
    unittest.main()
