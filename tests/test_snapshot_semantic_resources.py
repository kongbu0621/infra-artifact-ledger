"""Opt-in, semantically valid T09 metadata and reference boundary lifecycles.

All input ledgers are produced by public A1 writes/import, with unmodified
limits. No quota-only SQL rows or padding whitespace stand in for valid data.
Filesystem classification alone is mocked: LOGIC_ONLY, not NAS certification.

Run with A2_RESOURCE_TESTS=1 and Python 3.11. Each public recovery operation
retains the actual 300-second budget. Logs report actual independent SQL totals.
"""

import gc
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, open as open_ledger
from infra_artifact_ledger import recovery
from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.records import empty_metadata
from infra_artifact_ledger.fingerprint import canonical_bytes

try:
    from .acceptance_helpers import append, create, encode, import_kwargs, repackage, request
except ImportError:
    from acceptance_helpers import append, create, encode, import_kwargs, repackage, request


MiB = 1024 * 1024
ENABLED = os.environ.get("A2_RESOURCE_TESTS") == "1"
TEXT_COLUMNS = {
    "ledger_format": ("profile",), "records": ("id", "kind", "data"),
    "payloads": ("blob_ref",), "operations": ("scope", "kind", "key", "data", "result_ref"),
    "refs": ("source_id", "field", "target_id"),
}
ORDERS = {"ledger_format": "version,profile", "records": "id", "payloads": "blob_ref",
          "operations": "scope,kind,key", "refs": "source_id,field,target_id"}


def file_digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(MiB):
            result.update(block)
    return result.hexdigest()


def measure(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        totals = {}
        for table, columns in TEXT_COLUMNS.items():
            lengths = "+".join(f"length(CAST({column} AS BLOB))" for column in columns)
            totals[table] = connection.execute(
                f"SELECT count(*),coalesce(sum({lengths}),0) FROM {table}").fetchone()
        data = sum(connection.execute(
            f"SELECT coalesce(sum(length(CAST(data AS BLOB))),0) FROM {table}").fetchone()[0]
                   for table in ("records", "operations"))
    return {"metadata_rows": totals["records"][0] + totals["operations"][0],
            "metadata_bytes": data, "refs_rows": totals["refs"][0], "refs_bytes": totals["refs"][1],
            "text_bytes": sum(item[1] for item in totals.values()), "file_bytes": path.stat().st_size}


def state_digest(path):
    """Compare every SQL value, independently from the recovery summary."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        result = {}
        for table, order in ORDERS.items():
            digest = hashlib.sha256()
            count = 0
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                encoded = repr(row).encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
                count += 1
            result[table] = (count, digest.hexdigest())
        return result


def artifacts(count):
    return [{"artifact_id": f"a{i:05d}", "namespace_ref": "n",
             "type_ref": {"namespace": "n", "type_id": "t", "type_version": "1"},
             "recorded_at": "2026-01-01T00:00:00Z"} for i in range(count)]


def receipt(identity, targets):
    return {"import_receipt_id": identity, "imported_artifact_refs": list(targets),
            "recorded_at": "2026-01-01T00:00:00Z"}


def receipt_id(index, length):
    prefix = f"r{index:05d}"
    return prefix + "x" * (length - len(prefix))


def public_import(path, metadata, identity="receipt:semantic-boundary"):
    bundle = repackage(metadata, {})
    command = request("import_bundle", {"import_receipt_id": identity}, "boundary-import")
    with initialize(path) as ledger:
        ledger.execute(encode(command), **import_kwargs(bundle))


def external_refs(extra_bytes):
    """Replace [] with unique strings adding exactly N UTF-8 JSON bytes."""
    count = (extra_bytes + 1 + 514) // 515
    total_string_bytes = extra_bytes - 3 * count + 1
    minimum = 9  # ref00000:
    assert minimum * count <= total_string_bytes <= 512 * count
    remaining = total_string_bytes - minimum * count
    values = []
    for index in range(count):
        added = min(512 - minimum, remaining)
        remaining -= added
        prefix = f"ref{index:05d}:"
        # One three-byte UTF-8 character replaces three ASCII bytes, making
        # actual legal Unicode part of the exact SQL byte-limit exercise.
        if index == 0:
            prefix = "中" + prefix[3:]
        values.append(prefix + "x" * added)
    assert len(canonical_bytes(values)) - 2 == extra_bytes
    return values


def append_metadata_bytes(command):
    body = command["body"]
    timestamp = "2026-01-01T00:00:00.000000Z"  # Actual generated time has this width.
    records = [dict(body["version"], recorded_at=timestamp), *body["content_roots"],
               *body["blobs"], *body["manifests"]]
    records += [dict(value, recorded_at=timestamp) for value in body["provenance_links"]]
    operation = {"idempotency_scope_ref": command["idempotency_scope_ref"],
                 "operation_kind": "append_version", "idempotency_key": command["idempotency_key"],
                 "request_fingerprint": {"algorithm": "sha256", "value": "0" * 64},
                 "result_ref": body["version"]["version_id"], "recorded_at": timestamp}
    return sum(len(canonical_bytes(value)) for value in [*records, operation])


@unittest.skipUnless(ENABLED, "set A2_RESOURCE_TESTS=1 for legal T09 limits")
class SemanticSnapshotResourceTests(unittest.TestCase):
    def setUp(self):
        self.started = time.monotonic()
        self.temporary = tempfile.TemporaryDirectory(prefix="a2-semantic-resource-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        classification = patch.object(storage, "_classify", return_value=None)
        classification.start()
        self.addCleanup(classification.stop)
        self.observed = []

    def tearDown(self):
        gc.collect()
        print(json.dumps({"case": self._testMethodName, "evidence_scope": "LOGIC_ONLY",
                          "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                          "elapsed_seconds": round(time.monotonic() - self.started, 3),
                          "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                          "observed": self.observed}, sort_keys=True), flush=True)

    def validate_and_roundtrip(self, source, label, accepted):
        started = time.monotonic()
        observed = {"boundary": label, **measure(source)}
        self.assertLess(observed["file_bytes"], 1024 * MiB)
        before_hash = file_digest(source)
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        with open_ledger(source) as ledger:
            expected = ledger.verify()
        observed["a1_valid"] = True
        output, scratch, target = (source.parent / name for name in ("output", "scratch", "restored"))
        output.mkdir()
        scratch.mkdir()
        kwargs = dict(db=source, output_root=output, snapshot_id="a" * 32, source_commit="b" * 40)
        if not accepted:
            with self.assertRaises(recovery.RecoveryError) as caught:
                recovery.create(**kwargs)
            self.assertEqual(caught.exception.code, "RESOURCE_LIMIT")
            self.assertEqual(caught.exception.publication_state, "not_published")
            self.assertEqual(list(output.iterdir()), [])
            self.assertEqual(file_digest(source), before_hash)
            observed["result"] = "RESOURCE_LIMIT"
        else:
            expected_state = state_digest(source)
            created = recovery.create(**kwargs)
            data = created["data"]
            self.assertEqual(data["summary"], expected)
            verified = recovery.verify(snapshot=data["snapshot_path"],
                                       expected_manifest_sha256=data["manifest_sha256"], scratch_parent=scratch)
            self.assertEqual(verified["data"]["summary"], expected)
            restored = recovery.restore(snapshot=data["snapshot_path"], target_dir=target,
                                         expected_manifest_sha256=data["manifest_sha256"], scratch_parent=scratch)
            restored_path = Path(restored["data"]["database_path"])
            self.assertEqual(file_digest(restored_path), data["database_sha256"])
            self.assertEqual(state_digest(restored_path), expected_state)
            checked = recovery.check_restore(target_dir=target,
                                             expected_database_sha256=data["database_sha256"], scratch_parent=scratch)
            self.assertEqual(checked["data"]["summary"], expected)
            self.assertEqual(file_digest(source), before_hash)
            self.assertEqual(list(scratch.iterdir()), [])
            observed["result"] = "FULL_LIFECYCLE_PASS"
        observed["elapsed_seconds"] = round(time.monotonic() - started, 3)
        observed["process_peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        self.observed.append(observed)
        print(json.dumps(observed, sort_keys=True), flush=True)
        return observed

    def test_legal_metadata_16mib_minus_equal_plus_one(self):
        baseline = self.root / "baseline.sqlite"
        with initialize(baseline) as ledger:
            ledger.execute(encode(create()))
            for suffix in ("large-one", "large-two"):
                command, payloads = append(suffix, b"metadata-boundary")
                command["body"]["version"]["external_source_refs"] = external_refs(5 * MiB)
                ledger.execute(encode(command), payloads=payloads)
        baseline_bytes = measure(baseline)["metadata_bytes"]
        for delta in (-1, 0, 1):
            with self.subTest(delta=delta), tempfile.TemporaryDirectory(dir=self.root) as directory:
                source = Path(directory) / "source.sqlite"
                shutil.copy2(baseline, source)
                command, payloads = append("large-three", b"metadata-boundary")
                extra = 16 * MiB + delta - baseline_bytes - append_metadata_bytes(command)
                command["body"]["version"]["external_source_refs"] = external_refs(extra)
                with open_ledger(source) as ledger:
                    ledger.execute(encode(command), payloads=payloads)
                self.assertEqual(measure(source)["metadata_bytes"], 16 * MiB + delta)
                self.validate_and_roundtrip(source, f"metadata_16MiB{delta:+d}", delta <= 0)

    def test_legal_metadata_rows_49999_50000_50001(self):
        for rows in (49_999, 50_000, 50_001):
            with self.subTest(rows=rows), tempfile.TemporaryDirectory(dir=self.root) as directory:
                metadata = empty_metadata()
                # Import contributes one new Receipt record and one operation.
                metadata["artifacts"] = artifacts(rows - 2)
                source = Path(directory) / "source.sqlite"
                public_import(source, metadata)
                del metadata
                self.assertEqual(measure(source)["metadata_rows"], rows)
                self.validate_and_roundtrip(source, f"metadata_rows_{rows}", rows <= 50_000)

    def test_legal_reference_rows_249999_250000_250001(self):
        for rows in (249_999, 250_000, 250_001):
            with self.subTest(rows=rows), tempfile.TemporaryDirectory(dir=self.root) as directory:
                metadata = empty_metadata()
                metadata["artifacts"] = artifacts(1000)
                targets = [value["artifact_id"] for value in metadata["artifacts"]]
                full, remainder = divmod(rows - len(targets), len(targets))
                metadata["import_receipts"] = [receipt(receipt_id(index, 12), targets) for index in range(full)]
                if remainder:
                    metadata["import_receipts"].append(receipt(receipt_id(full, 12), targets[:remainder]))
                source = Path(directory) / "source.sqlite"
                public_import(source, metadata)
                del metadata
                self.assertEqual(measure(source)["refs_rows"], rows)
                self.validate_and_roundtrip(source, f"refs_rows_{rows}", rows <= 250_000)

    def test_legal_reference_bytes_32mib_minus_equal_plus_one(self):
        for delta in (-1, 0, 1):
            with self.subTest(delta=delta), tempfile.TemporaryDirectory(dir=self.root) as directory:
                metadata = empty_metadata()
                metadata["artifacts"] = artifacts(1000)
                targets = [value["artifact_id"] for value in metadata["artifacts"]]
                groups = [targets] * 248 + [targets[:999], targets[:1]]
                final_id = receipt_id(99999, 102)
                base = sum(102 + len(f"imported_artifact_refs.{index}") + len(target)
                           for group in [*groups, targets] for index, target in enumerate(group))
                difference = 32 * MiB + delta - base
                solution = None
                for partial in range(155):
                    for single in range(155):
                        rest = difference - 999 * partial - single
                        if rest >= 0 and rest % 1000 == 0 and rest // 1000 <= 248 * 154:
                            solution = rest // 1000, partial, single
                            break
                    if solution is not None:
                        break
                self.assertIsNotNone(solution)
                full_units, partial, single = solution
                lengths = [102] * 250
                for index in range(248):
                    added = min(154, full_units)
                    lengths[index] += added
                    full_units -= added
                self.assertEqual(full_units, 0)
                lengths[248] += partial
                lengths[249] += single
                metadata["import_receipts"] = [receipt(receipt_id(index, length), group)
                                                for index, (length, group) in enumerate(zip(lengths, groups))]
                source = Path(directory) / "source.sqlite"
                public_import(source, metadata, final_id)
                del metadata
                totals = measure(source)
                self.assertEqual(totals["refs_bytes"], 32 * MiB + delta)
                self.assertEqual(totals["refs_rows"], 250_000)
                self.assertLess(totals["metadata_bytes"], 16 * MiB)
                self.assertLess(totals["text_bytes"], 64 * MiB)
                self.validate_and_roundtrip(source, f"refs_32MiB{delta:+d}", delta <= 0)


if __name__ == "__main__":
    unittest.main()
