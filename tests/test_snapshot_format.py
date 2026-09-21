"""Exact A2 serialization budgets and format/configuration contract checks."""

import copy
import hashlib
from importlib import metadata
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import Mock, patch

from infra_artifact_ledger.snapshot_common import RecoveryError
from infra_artifact_ledger import snapshot_format as fmt


ID = "0123456789abcdef" * 2
SHA = "1" * 64
COMMIT = "2" * 40


def empty_summary():
    return {"counts": {key: 0 for key in fmt.COUNTS},
            "verified_blob_count": 0, "verified_byte_length": 0}


def config():
    return {"format": fmt.STORAGE_FORMAT, "profile": "mounted-posix-v1",
            "storage_ref": "test-storage", "mount_point": "/mnt/test",
            "mount_root": "/", "mount_source": "server:/synthetic",
            "fs_type": "nfs4", "archive_root": "/mnt/test/snapshots"}


class SnapshotFormatTests(unittest.TestCase):
    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(RecoveryError) as result:
            function(*args, **kwargs)
        self.assertEqual(result.exception.code, code)
        return result.exception

    def manifest(self):
        with patch.object(fmt, "_package_version", return_value="0.2.0a1"):
            return fmt.make_manifest(ID, COMMIT, 40960, SHA, empty_summary())

    def test_canonical_bytes_and_raw_hash_are_distinct(self):
        raw = fmt.encode({"z": "中", "a": "\n/"})
        self.assertEqual(raw, '{"a":"\\n/","z":"中"}'.encode())
        spaced = b' { "a": "\\n/", "z":"\\u4e2d" }\n'
        self.assertEqual(fmt.parse_json(spaced), fmt.parse_json(raw))
        self.assertNotEqual(hashlib.sha256(raw).digest(), hashlib.sha256(spaced).digest())

    def test_each_real_byte_limit_is_inclusive(self):
        for limit in (fmt.MAX_MARKER, fmt.MAX_MANIFEST, fmt.MAX_STORAGE_CONFIG):
            with self.subTest(limit=limit):
                value = "x" * (limit - 2)
                raw = fmt.encode(value, limit)
                self.assertEqual(len(raw), limit)
                self.assertEqual(fmt.parse_json(raw, limit), value)
                self.assert_code("RESOURCE_LIMIT", fmt.encode, value + "x", limit)
                self.assert_code("RESOURCE_LIMIT", fmt.parse_json, raw + b" ", limit)

    def test_utf8_and_escaped_characters_count_encoded_bytes(self):
        self.assertEqual(fmt.encode("中", 5), b'"\xe4\xb8\xad"')
        self.assert_code("RESOURCE_LIMIT", fmt.encode, "中", 4)
        self.assertEqual(fmt.encode("\x00", 8), b'"\\u0000"')
        self.assert_code("RESOURCE_LIMIT", fmt.encode, "\x00", 7)

    def test_node_1024_boundary_and_keys_are_not_value_nodes(self):
        value = [None] * 1023
        self.assertEqual(fmt.parse_json(fmt.encode(value)), value)
        self.assert_code("RESOURCE_LIMIT", fmt.encode, value + [None])
        self.assert_code("RESOURCE_LIMIT", fmt.parse_json, b"[" + b"0," * 1023 + b"0]")
        obj = {str(n): 0 for n in range(1023)}
        self.assertEqual(fmt.parse_json(fmt.encode(obj)), obj)

    def test_container_depth_8_boundary(self):
        value = 0
        for _ in range(8):
            value = [value]
        self.assertEqual(fmt.parse_json(fmt.encode(value)), value)
        self.assert_code("RESOURCE_LIMIT", fmt.encode, [value])
        self.assert_code("RESOURCE_LIMIT", fmt.parse_json, b"[" * 9 + b"0" + b"]" * 9)

    def test_invalid_json_unicode_duplicates_and_nonfinite(self):
        cases = [b'{"x":1,"x":2}', b'{"x":1,"\\u0078":2}', b"NaN", b"Infinity",
                 b"-Infinity", b"1e999", b"\xef\xbb\xbf{}", b'"\xff"',
                 b'"\\ud800"', b'"\\udc00"', b"{}x", b"", b"[1,]"]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assert_code("INVALID_INPUT", fmt.parse_json, raw)
        self.assertEqual(fmt.parse_json(b'"\\ud83d\\ude00"'), "😀")
        self.assert_code("INVALID_INPUT", fmt.parse_json, bytearray(b"{}"))
        for value in (float("nan"), float("inf"), "\ud800", {"\ud800": 1}):
            self.assert_code("INVALID_INPUT", fmt.encode, value)

    def test_cycles_custom_types_and_shared_acyclic_values(self):
        cycle = []
        cycle.append(cycle)
        self.assert_code("INVALID_INPUT", fmt.encode, cycle)
        self.assert_code("INVALID_INPUT", fmt.encode, (1, 2))
        self.assert_code("INVALID_INPUT", fmt.encode, {1: 2})
        child = {"x": 1}
        self.assertEqual(fmt.parse_json(fmt.encode([child, child])), [child, child])

    def test_diagnostic_is_bounded_and_does_not_echo_unknown_field(self):
        marker = fmt.make_marker(ID, SHA)
        marker["private " * 1000] = 0
        error = self.assert_code("RESOURCE_LIMIT", fmt.validate_marker, marker)
        self.assertLessEqual(len(error.message.encode()), 1024)
        self.assertNotIn("private", error.message)

    def test_large_diagnostic_truncates_before_utf8_allocation(self):
        huge = "😀" * 1_000_000
        tracemalloc.start()
        try:
            error = RecoveryError("IO_ERROR", huge, "report", "published")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(error.message, "😀" * 256)
        self.assertLess(peak, 128 * 1024)
        self.assertEqual(error.to_envelope("create")["publication_state"], "published")
        # Preserve exact historical UTF-8 replacement/truncation, including a
        # split multibyte character at byte 1024 and invalid Unicode scalars.
        for message in ("x" * 1023 + "中" + "tail", "\ud800" * 1023 + "😀", "中\udc00" * 700):
            expected = message.encode("utf-8", "replace")[:1024].decode("utf-8", "ignore")
            self.assertEqual(RecoveryError("IO_ERROR", message).message, expected)

    def test_lowercase_identity_and_digest_lengths(self):
        for validator, good in ((fmt.validate_snapshot_id, ID), (fmt.validate_sha256, SHA),
                                (fmt.validate_source_commit, COMMIT)):
            self.assertEqual(validator(good), good)
            for bad in (good[:-1], good + "0", "A" * len(good), "../" + good, None):
                self.assert_code("INVALID_INPUT", validator, bad)

    def test_manifest_and_marker_roundtrip_exact_fields(self):
        manifest = self.manifest()
        self.assertEqual(fmt.validate_manifest(fmt.parse_json(fmt.encode(manifest)), ID), manifest)
        marker = fmt.make_marker(ID, hashlib.sha256(fmt.encode(manifest)).hexdigest())
        self.assertEqual(fmt.validate_marker(fmt.parse_json(fmt.encode(marker)), ID), marker)
        for validator, value in ((fmt.validate_manifest, manifest), (fmt.validate_marker, marker)):
            self.assert_code("INTEGRITY_FAILURE", validator, value, "f" * 32)
            self.assert_code("INVALID_INPUT", validator, dict(value, extra=True))
            missing = dict(value)
            missing.pop("format")
            self.assert_code("INVALID_INPUT", validator, missing)
            unsupported = dict(value, format="next/v2")
            self.assert_code("UNSUPPORTED_FORMAT", validator, unsupported)

    def test_manifest_numeric_fields_reject_bool_float_and_exponent(self):
        manifest = self.manifest()
        for value in (True, 1.0, 1e0, -1):
            bad = copy.deepcopy(manifest)
            bad["database"]["byte_length"] = value
            self.assert_code("INVALID_INPUT", fmt.validate_manifest, bad)
            bad = copy.deepcopy(manifest)
            bad["summary"]["counts"]["artifacts"] = value
            self.assert_code("INVALID_INPUT", fmt.validate_manifest, bad)
        for value in (True, 1.0):
            self.assert_code("INVALID_INPUT", fmt.validate_manifest,
                             dict(manifest, schema_version=value))
        raw = fmt.encode(manifest).replace(b'"byte_length":40960', b'"byte_length":4.096e4')
        self.assert_code("INVALID_INPUT", fmt.validate_manifest, fmt.parse_json(raw))

    def test_manifest_unknown_nested_fields_versions_and_database_name(self):
        for nested in ("producer", "database", "summary"):
            bad = self.manifest()
            bad[nested]["extra"] = "x"
            self.assert_code("INVALID_INPUT", fmt.validate_manifest, bad)
        for key, value in (("journal_mode", "wal"), ("text_encoding", "UTF-16le"),
                           ("schema_version", 2)):
            self.assert_code("UNSUPPORTED_FORMAT", fmt.validate_manifest,
                             dict(self.manifest(), **{key: value}))
        bad = self.manifest()
        bad["database"]["name"] = "../ledger.sqlite"
        self.assert_code("INVALID_INPUT", fmt.validate_manifest, bad)

    def test_exact_utc_timestamp_calendar_and_ascii_version(self):
        manifest = self.manifest()
        for good in ("2000-02-29T23:59:59Z", "2026-09-20T00:00:00Z"):
            fmt.validate_manifest(dict(manifest, created_at=good))
        for bad in ("2025-02-29T00:00:00Z", "2026-09-20T24:00:00Z",
                    "2026-09-20t00:00:00z", "2026-09-20T00:00:00+00:00",
                    "2026-09-20T00:00:00.1Z", "0000-01-01T00:00:00Z"):
            self.assert_code("INVALID_INPUT", fmt.validate_manifest, dict(manifest, created_at=bad))
        for version in ("", "中", "x" * 65):
            bad = copy.deepcopy(manifest)
            bad["producer"]["package_version"] = version
            self.assert_code("INVALID_INPUT", fmt.validate_manifest, bad)

    def test_summary_counts_file_and_blob_relationships(self):
        summary = empty_summary()
        fmt.validate_summary(summary, 4096)
        summary["counts"]["artifacts"] = fmt.MAX_RECORD_ROWS
        fmt.validate_summary(summary, 4096)
        summary["counts"]["idempotency_records"] = 1
        self.assert_code("RESOURCE_LIMIT", fmt.validate_summary, summary)
        summary = empty_summary()
        summary["counts"]["blobs"] = 1
        self.assert_code("INTEGRITY_FAILURE", fmt.validate_summary, summary)
        summary["verified_blob_count"] = 1
        summary["verified_byte_length"] = fmt.MAX_BLOB
        fmt.validate_summary(summary)
        self.assert_code("INTEGRITY_FAILURE", fmt.validate_summary, summary, 4096)
        summary["verified_byte_length"] += 1
        self.assert_code("INTEGRITY_FAILURE", fmt.validate_summary, summary)
        summary = empty_summary()
        summary["verified_byte_length"] = 1
        self.assert_code("INTEGRITY_FAILURE", fmt.validate_summary, summary)

    def test_database_byte_limit_and_positive_length(self):
        manifest = self.manifest()
        manifest["database"]["byte_length"] = fmt.MAX_DATABASE
        fmt.validate_manifest(manifest)
        manifest["database"]["byte_length"] += 1
        self.assert_code("RESOURCE_LIMIT", fmt.validate_manifest, manifest)
        manifest["database"]["byte_length"] = 0
        self.assert_code("INVALID_INPUT", fmt.validate_manifest, manifest)

    def test_configuration_copy_is_owned_and_exactly_typed(self):
        original = config()
        captured = fmt.capture_storage_config(original)
        original["archive_root"] = "/elsewhere"
        self.assertEqual(captured["archive_root"], "/mnt/test/snapshots")
        self.assertIsNot(captured, original)
        class CustomDict(dict):
            pass
        class CustomStr(str):
            pass
        self.assert_code("INVALID_INPUT", fmt.capture_storage_config, CustomDict(config()))
        for value in (Path("/mnt/test"), [], {}, None, 123, CustomStr("value")):
            self.assert_code("INVALID_INPUT", fmt.capture_storage_config,
                             dict(config(), mount_point=value))

    def test_storage_config_raw_and_canonical_budget_both_apply(self):
        value = config()
        missing = fmt.MAX_STORAGE_CONFIG - len(fmt.encode(value))
        value["archive_root"] += "x" * missing
        self.assertEqual(len(fmt.encode(value)), fmt.MAX_STORAGE_CONFIG)
        self.assertEqual(fmt.capture_storage_config(value), value)
        self.assert_code("RESOURCE_LIMIT", fmt.capture_storage_config,
                         dict(value, archive_root=value["archive_root"] + "x"))
        raw = fmt.encode(config())
        self.assert_code("RESOURCE_LIMIT", fmt.parse_json,
                         raw + b" " * fmt.MAX_STORAGE_CONFIG, fmt.MAX_STORAGE_CONFIG)

    def test_huge_config_value_is_rejected_before_utf8_or_json_materialization(self):
        value = dict(config(), archive_root="/mnt/test/" + "😀" * 1_000_000)
        original_string = fmt._string

        def bounded_string(item, stage):
            self.assertLessEqual(len(item), fmt.MAX_STORAGE_CONFIG)
            return original_string(item, stage)

        tracemalloc.start()
        try:
            with patch.object(fmt, "_string", side_effect=bounded_string):
                error = self.assert_code("RESOURCE_LIMIT", fmt.capture_storage_config, value)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(error.publication_state, "not_published")
        self.assertLess(peak, 128 * 1024)
        self.assertNotIn("😀", error.message)

    def test_config_absolute_containment_and_utf8_source_bytes(self):
        for bad in ("relative", "/mnt/test\x00/dir", "/mnt/testing", "/mnt/test/../other"):
            self.assert_code("INVALID_INPUT", fmt.capture_storage_config,
                             dict(config(), archive_root=bad))
        self.assert_code("INVALID_INPUT", fmt.capture_storage_config,
                         dict(config(), mount_source="中" * 342))
        fmt.capture_storage_config(dict(config(), mount_source="中" * 341 + "x"))
        self.assert_code("UNSUPPORTED_STORAGE", fmt.capture_storage_config,
                         dict(config(), fs_type="ext4"))

    def test_manifest_builder_owns_summary_and_keeps_reported_stage(self):
        summary = empty_summary()
        with patch.object(fmt, "_package_version", return_value="0.2.0a1"):
            value = fmt.make_manifest(ID, COMMIT, 4096, SHA, summary)
        summary["counts"]["blobs"] = 200
        self.assertEqual(value["summary"]["counts"]["blobs"], 0)
        error = self.assert_code("INVALID_INPUT", fmt.parse_json, b"no", stage="verify")
        self.assertEqual(error.stage, "verify")

    def test_package_version_uses_matching_install_metadata(self):
        distribution = Mock()
        distribution.locate_file.return_value = Path(fmt.__file__)
        distribution.version = "0.2.0a1"
        with patch.object(fmt.metadata, "distribution", return_value=distribution):
            self.assertEqual(fmt._package_version(), "0.2.0a1")

    def test_source_fallback_ignores_unrelated_installed_version(self):
        distribution = Mock()
        distribution.locate_file.return_value = Path("/unrelated/install/snapshot_format.py")
        distribution.version = "999.0"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "src" / "infra_artifact_ledger" / "snapshot_format.py"
            module.parent.mkdir(parents=True)
            module.write_text("", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                '[project]\nname="infra-artifact-ledger"\nversion="0.2.0a1"\n', encoding="utf-8")
            with patch.object(fmt, "__file__", str(module)), patch.object(
                    fmt.metadata, "distribution", return_value=distribution):
                self.assertEqual(fmt._package_version(), "0.2.0a1")
            with patch.object(fmt, "__file__", str(root / "package" / "snapshot_format.py")), patch.object(
                    fmt.metadata, "distribution", side_effect=metadata.PackageNotFoundError):
                self.assert_code("IO_ERROR", fmt._package_version)


if __name__ == "__main__":
    unittest.main()
