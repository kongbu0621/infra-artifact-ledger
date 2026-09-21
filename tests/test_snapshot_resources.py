"""Opt-in A2 acceptance at the actual, unmodified resource constants.

Run: A2_RESOURCE_TESTS=1 PYTHONPATH=src python -m unittest discover -s tests -p test_snapshot_resources.py -v

Byte/row preflights intentionally use schema-correct but semantically malformed
rows to isolate independently unreachable boundaries. Passing a preflight does
not declare those rows valid Ledger records. The large lifecycle uses public A1
writes and the public A2 APIs with real SQLite and synthetic mount classification;
it is LOGIC_ONLY evidence, never an actual storage profile or NAS certification.
"""
from __future__ import annotations

from contextlib import contextmanager
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from infra_artifact_ledger import recovery
from infra_artifact_ledger import snapshot_sqlite as snapshot
from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError

try:
    from .acceptance_helpers import append, create, encode
except ImportError:
    from acceptance_helpers import append, create, encode

MiB = 1024 * 1024
ENABLED = os.environ.get("A2_RESOURCE_TESTS") == "1"
# Independent enumeration of every approved schema-1 business TEXT column.
TEXT_COLUMNS = {
    "ledger_format": ("profile",), "records": ("id", "kind", "data"),
    "payloads": ("blob_ref",),
    "operations": ("scope", "kind", "key", "data", "result_ref"),
    "refs": ("source_id", "field", "target_id"),
}


def utf8_text(byte_length: int, *, chinese: bool = True) -> str:
    """Make exactly N UTF-8 bytes without allocating an N-element object list."""
    if chinese:
        count, remainder = divmod(byte_length, 3)
        return "中" * count + "x" * remainder
    return "x" * byte_length


@unittest.skipUnless(ENABLED, "set A2_RESOURCE_TESTS=1 for actual A2 resource limits")
class ActualSnapshotResourceTests(unittest.TestCase):
    def setUp(self):
        self.started = time.monotonic()
        self.temp = tempfile.TemporaryDirectory(prefix="ledger-a2-resource-")
        self.root = Path(self.temp.name)
        self.counter = 0
        self.observed = []
        self.assertEqual(snapshot.MAX_DATABASE, 1_073_741_824)
        self.assertEqual(snapshot.MAX_METADATA_ROWS, 50_000)
        self.assertEqual(snapshot.MAX_METADATA_BYTES, 16 * MiB)
        self.assertEqual(snapshot.MAX_REFS_ROWS, 250_000)
        self.assertEqual(snapshot.MAX_REFS_BYTES, 32 * MiB)
        self.assertEqual(snapshot.MAX_TEXT_BYTES, 64 * MiB)

    def tearDown(self):
        self.temp.cleanup()
        gc.collect()
        print(json.dumps({
            "case": self._testMethodName, "evidence_scope": "LOGIC_ONLY",
            "actual_constants": True, "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version, "platform": platform.platform(),
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "observed": self.observed,
        }, sort_keys=True), flush=True)

    @contextmanager
    def database(self):
        self.counter += 1
        target = self.root / f"case-{self.counter}.sqlite"
        with initialize(target):
            pass
        connection = sqlite3.connect(target)
        try:
            yield connection, target
        finally:
            connection.close()
            target.unlink(missing_ok=True)
            for suffix in ("-journal", "-wal", "-shm"):
                Path(str(target) + suffix).unlink(missing_ok=True)
            gc.collect()

    def limited(self, callback):
        with self.assertRaises(RecoveryError) as caught:
            callback()
        self.assertEqual(caught.exception.code, "RESOURCE_LIMIT")
        self.assertEqual(caught.exception.publication_state, "not_published")

    def preflight(self, connection):
        connection.commit()
        return snapshot._format_and_budget(connection, Budget(), "verify")

    def test_database_one_gib_minus_equal_plus_one_file_preflight(self):
        target = self.root / "sparse-boundary.sqlite"
        # File-stat limit: byte ±1 values cannot all be valid SQLite page sizes.
        # The zero-filled sparse files are not claimed to be valid databases.
        for length in (1_073_741_823, 1_073_741_824, 1_073_741_825):
            with open(target, "wb") as stream:
                stream.truncate(length)
            self.assertEqual(target.stat().st_size, length)
            if length <= 1_073_741_824:
                snapshot._source_binding(target, Budget())
            else:
                self.limited(lambda: snapshot._source_binding(target, Budget()))
                self.limited(lambda: snapshot.validate_database(target, Budget()))
            self.observed.append({"file_bytes": length, "layer": "source_stat_preflight",
                                  "result": "PASS" if length <= 1_073_741_824 else "RESOURCE_LIMIT"})

    def test_legal_sqlite_adjacent_pages_at_one_gib_lifecycle(self):
        # Allocate pages through SQLite, then drop only synthetic padding tables.
        # Remaining free-list pages are valid SQLite state, survive Online Backup,
        # and count toward the actual sealed file limit. The separate large
        # payload lifecycle tests live business bytes rather than free pages.
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            mount_id = storage._fd_mount_id(descriptor)
        finally:
            os.close(descriptor)
        synthetic = storage.Mount(mount_id, 1, "0:1", "/", "/", "ext4", "explicit-logic-test")
        # SQLite skips the page containing offset 0x40000000 (its lock bytes).
        # Thus the first SQL-allocated page above 1 GiB is +8192 at page size
        # 4096, not +4096: https://www.sqlite.org/fileformat.html#the_lock_byte_page
        for difference in (-1, 0, 2):
            with self.subTest(adjacent_page=difference), tempfile.TemporaryDirectory(dir=self.root) as case:
                case_root = Path(case)
                source = case_root / "source.sqlite"
                seed_payload = b"actual page-boundary ledger content"
                with initialize(source) as ledger:
                    ledger.execute(encode(create()))
                    request, supplied = append("page-boundary", seed_payload)
                    ledger.execute(encode(request), payloads=supplied)
                    expected_summary = ledger.verify()
                with sqlite3.connect(source) as connection:
                    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                    self.assertEqual(page_size, 4096)
                    self.assertEqual(connection.execute("PRAGMA auto_vacuum").fetchone()[0], 0)
                    target_pages = 1_073_741_824 // page_size + difference
                    target_bytes = target_pages * page_size
                    # Each one-row padding table has an existing root page.
                    # Overflow pages contain page_size-4 bytes. Keeping the first
                    # value at 512 MiB also avoids SQLite's single-value 1e9 cap.
                    connection.execute("CREATE TABLE a2_resource_padding_large (data BLOB)")
                    connection.execute("INSERT INTO a2_resource_padding_large VALUES (zeroblob(?))", (512 * MiB,))
                    connection.execute("CREATE TABLE a2_resource_padding_tail (data BLOB)")
                    connection.execute("INSERT INTO a2_resource_padding_tail VALUES (zeroblob(1))")
                    before_tail = connection.execute("PRAGMA page_count").fetchone()[0]
                    lock_byte_page = 0x40000000 // page_size + 1
                    skipped_lock_page = int(before_tail < lock_byte_page <= target_pages)
                    remaining = target_pages - before_tail - skipped_lock_page
                    self.assertGreater(remaining, 0)
                    connection.execute("UPDATE a2_resource_padding_tail SET data=zeroblob(?)",
                                       (remaining * (page_size - 4),))
                    self.assertEqual(connection.execute("PRAGMA page_count").fetchone()[0], target_pages)
                    connection.commit()
                    connection.execute("DROP TABLE a2_resource_padding_tail")
                    connection.execute("DROP TABLE a2_resource_padding_large")
                    connection.commit()
                    self.assertEqual(connection.execute("PRAGMA page_count").fetchone()[0], target_pages)
                    free_pages = connection.execute("PRAGMA freelist_count").fetchone()[0]
                    self.assertGreater(free_pages, 0)
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
                    self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(source.stat().st_size, target_bytes)
                with open_ledger(source) as ledger:
                    self.assertEqual(ledger.verify(), expected_summary)
                    self.assertEqual(ledger.read_blob("blob:quarterly-page-boundary"), seed_payload)
                archives, scratch = case_root / "archives", case_root / "scratch"
                archives.mkdir()
                scratch.mkdir()
                case_started = time.monotonic()
                with mock.patch.object(storage, "_mounts", return_value=(synthetic,)):
                    if difference > 0:
                        self.limited(lambda: recovery.create(db=source, output_root=archives,
                                                            snapshot_id="c" * 32, source_commit="d" * 40))
                        self.assertEqual(list(archives.iterdir()), [])
                        self.assertEqual(source.stat().st_size, target_bytes)
                        with open_ledger(source) as ledger:
                            self.assertEqual(ledger.verify(), expected_summary)
                        result = "RESOURCE_LIMIT"
                    else:
                        created = recovery.create(db=source, output_root=archives,
                                                  snapshot_id="c" * 32, source_commit="d" * 40)
                        snapshot_path = Path(created["data"]["snapshot_path"])
                        manifest_hash = created["data"]["manifest_sha256"]
                        self.assertEqual((snapshot_path / "ledger.sqlite").stat().st_size, target_bytes)
                        verified = recovery.verify(snapshot=snapshot_path, expected_manifest_sha256=manifest_hash,
                                                   scratch_parent=scratch)
                        self.assertEqual(verified["data"]["summary"], expected_summary)
                        restored = recovery.restore(snapshot=snapshot_path, target_dir=case_root / "restored",
                                                    expected_manifest_sha256=manifest_hash, scratch_parent=scratch)
                        restored_database = Path(restored["data"]["database_path"])
                        self.assertEqual(restored_database.stat().st_size, target_bytes)
                        self.assertEqual(restored["data"]["summary"], expected_summary)
                        checked = recovery.check_restore(target_dir=case_root / "restored", scratch_parent=scratch,
                                                         expected_database_sha256=created["data"]["database_sha256"])
                        self.assertEqual(checked["data"]["summary"], expected_summary)
                        with open_ledger(restored_database) as ledger:
                            self.assertEqual(ledger.verify(), expected_summary)
                            self.assertEqual(ledger.read_blob("blob:quarterly-page-boundary"), seed_payload)
                        result = "PASS"
                observation = {"page_size": page_size, "page_count": target_pages,
                               "freelist_pages": free_pages, "database_bytes": target_bytes,
                               "skipped_lock_byte_page": lock_byte_page if skipped_lock_page else None,
                               "sqlite_integrity": "ok", "a1_semantics": "PASS", "result": result,
                               "a2_elapsed_seconds": round(time.monotonic() - case_started, 3),
                               "evidence_scope": "LOGIC_ONLY"}
                self.observed.append(observation)
                print(json.dumps({"adjacent_page_boundary": observation}, sort_keys=True), flush=True)

    def test_metadata_utf8_16mib_minus_equal_plus_one_across_two_tables(self):
        for chinese in (False, True):
            for length in (16 * MiB - 1, 16 * MiB, 16 * MiB + 1):
                with self.subTest(chinese=chinese, byte_length=length), self.database() as (connection, _):
                    first = length // 2
                    connection.execute("INSERT INTO records VALUES (?,?,?)",
                                       ("record:resource", "artifact", utf8_text(first, chinese=chinese)))
                    connection.execute("INSERT INTO operations VALUES (?,?,?,?,?)",
                                       ("resource", "create_artifact", "key", utf8_text(length - first, chinese=chinese), "record:resource"))
                    actual = sum(connection.execute(
                        f"SELECT sum(length(CAST(data AS BLOB))) FROM {table}").fetchone()[0]
                                 for table in ("records", "operations"))
                    self.assertEqual(actual, length)
                    if chinese:
                        character_count = sum(connection.execute(
                            f"SELECT sum(length(data)) FROM {table}").fetchone()[0]
                                              for table in ("records", "operations"))
                        self.assertLess(character_count, length)
                    if length <= 16 * MiB:
                        self.preflight(connection)
                    else:
                        self.limited(lambda: self.preflight(connection))
                    self.observed.append({"metadata_utf8_bytes": actual, "chinese": chinese,
                                          "layer": "format_and_budget"})

    def test_reference_utf8_32mib_minus_equal_plus_one(self):
        for length in (32 * MiB - 1, 32 * MiB, 32 * MiB + 1):
            with self.subTest(byte_length=length), self.database() as (connection, _):
                connection.execute("INSERT INTO refs VALUES (?,?,?)", ("s", utf8_text(length - 2), "t"))
                actual = connection.execute(
                    "SELECT sum(length(CAST(source_id AS BLOB))+length(CAST(field AS BLOB))+"
                    "length(CAST(target_id AS BLOB))) FROM refs").fetchone()[0]
                self.assertEqual(actual, length)
                if length <= 32 * MiB:
                    self.preflight(connection)
                else:
                    self.limited(lambda: self.preflight(connection))
                self.observed.append({"refs_utf8_bytes": actual, "layer": "format_and_budget"})

    def test_all_text_utf8_64mib_minus_equal_plus_one_includes_index_keys(self):
        # A giant operation scope isolates total TEXT from the tighter data and
        # reference caps; semantic identifier validation would reject it later.
        for length in (64 * MiB - 1, 64 * MiB, 64 * MiB + 1):
            with self.subTest(byte_length=length), self.database() as (connection, _):
                connection.execute("INSERT INTO records VALUES ('r','artifact','{}')")
                connection.execute("INSERT INTO operations VALUES ('','create_artifact','k','{}','r')")
                fixed = 0
                for table, columns in TEXT_COLUMNS.items():
                    lengths = "+".join(f"length(CAST({column} AS BLOB))" for column in columns)
                    fixed += connection.execute(f"SELECT coalesce(sum({lengths}),0) FROM {table}").fetchone()[0]
                connection.execute("UPDATE operations SET scope=?", (utf8_text(length - fixed),))
                actual = 0
                for table, columns in TEXT_COLUMNS.items():
                    lengths = "+".join(f"length(CAST({column} AS BLOB))" for column in columns)
                    actual += connection.execute(f"SELECT coalesce(sum({lengths}),0) FROM {table}").fetchone()[0]
                self.assertEqual(actual, length)
                if length <= 64 * MiB:
                    self.preflight(connection)
                else:
                    self.limited(lambda: self.preflight(connection))
                self.observed.append({"all_text_utf8_bytes": actual, "layer": "format_and_budget"})

    def test_metadata_and_reference_actual_row_limits(self):
        with self.database() as (connection, _):
            # Include operations in the combined metadata count.
            connection.executemany("INSERT INTO records VALUES (?,?,?)",
                                   ((f"r:{i:06d}", "artifact", "{}") for i in range(49_998)))
            connection.execute("INSERT INTO operations VALUES ('s','create_artifact','k','{}','r:000000')")
            for count in (49_999, 50_000, 50_001):
                if count > 49_999:
                    connection.execute("INSERT INTO records VALUES (?,?,?)", (f"extra:{count}", "artifact", "{}"))
                actual = connection.execute("SELECT (SELECT count(*) FROM records)+(SELECT count(*) FROM operations)").fetchone()[0]
                self.assertEqual(actual, count)
                if count <= 50_000:
                    self.preflight(connection)
                else:
                    self.limited(lambda: self.preflight(connection))
                self.observed.append({"combined_metadata_rows": actual})
        with self.database() as (connection, _):
            connection.executemany("INSERT INTO refs VALUES (?,?,?)",
                                   (("s", f"field:{i:06d}", "t") for i in range(249_999)))
            for count in (249_999, 250_000, 250_001):
                if count > 249_999:
                    connection.execute("INSERT INTO refs VALUES (?,?,?)", ("s", f"field:{count}", "t"))
                self.assertEqual(connection.execute("SELECT count(*) FROM refs").fetchone()[0], count)
                if count <= 250_000:
                    self.preflight(connection)
                else:
                    self.limited(lambda: self.preflight(connection))
                self.observed.append({"refs_rows": count})

    def test_legal_lifecycle_exceeds_portable_payload_and_package_limits(self):
        source = self.root / "source.sqlite"
        archives, scratch = self.root / "archives", self.root / "scratch"
        archives.mkdir()
        scratch.mkdir()
        payload = b"a" * (64 * MiB)
        expected = {}
        first_request = None
        with initialize(source) as ledger:
            ledger.execute(encode(create()))
            for index in range(7):
                body = payload if index < 6 else b"z"
                request, supplied = append(f"large-{index}", body)
                ledger.execute(encode(request), payloads=supplied)
                expected[f"blob:quarterly-large-{index}"] = (len(body), hashlib.sha256(body).hexdigest())
                if first_request is None:
                    first_request = request
            summary = ledger.verify()
            with self.assertRaises(LedgerError) as caught:
                ledger.export_bundle()
            self.assertEqual(caught.exception.code, "RESOURCE_LIMIT")
            self.assertEqual(caught.exception.commit_state, "not_applicable")
        del supplied, body, payload
        gc.collect()
        self.assertEqual(summary["verified_byte_length"], 384 * MiB + 1)
        self.assertGreater(source.stat().st_size, 384 * MiB)
        self.assertLess(source.stat().st_size, 1_073_741_824)
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            mount_id = storage._fd_mount_id(descriptor)
        finally:
            os.close(descriptor)
        synthetic = storage.Mount(mount_id, 1, "0:1", "/", "/", "ext4", "explicit-logic-test")
        # ONLY the mount classification is simulated. SQLite, fsync, fd binding,
        # copy/hash/semantic checks and all byte limits execute their real code.
        with mock.patch.object(storage, "_mounts", return_value=(synthetic,)):
            created = recovery.create(db=source, output_root=archives, snapshot_id="a" * 32,
                                      source_commit="b" * 40)
            manifest_hash = created["data"]["manifest_sha256"]
            snapshot_path = created["data"]["snapshot_path"]
            verified = recovery.verify(snapshot=snapshot_path, expected_manifest_sha256=manifest_hash,
                                       scratch_parent=scratch)
            self.assertEqual(verified["data"]["summary"], summary)
            restored = recovery.restore(snapshot=snapshot_path, target_dir=self.root / "restored",
                                        expected_manifest_sha256=manifest_hash, scratch_parent=scratch)
            self.assertEqual(restored["data"]["summary"], summary)
            self.assertEqual(set((self.root / "restored").iterdir()), {self.root / "restored" / "ledger.sqlite"})
            checked = recovery.check_restore(target_dir=self.root / "restored", scratch_parent=scratch,
                                              expected_database_sha256=created["data"]["database_sha256"])
            self.assertEqual(checked["data"]["summary"], summary)
        with open_ledger(self.root / "restored" / "ledger.sqlite") as ledger:
            for reference, (length, digest) in expected.items():
                actual = ledger.read_blob(reference)
                self.assertEqual(len(actual), length)
                self.assertEqual(hashlib.sha256(actual).hexdigest(), digest)
                del actual
            replay = ledger.execute(encode(first_request), payloads={"blob:quarterly-large-0": b"a" * (64 * MiB)})
            self.assertTrue(replay["replayed"])
            self.assertEqual(ledger.verify(), summary)
        self.observed.append({"verified_payload_bytes": summary["verified_byte_length"],
                              "source_database_bytes": source.stat().st_size,
                              "snapshot_database_bytes": (Path(snapshot_path) / "ledger.sqlite").stat().st_size,
                              "a1_portable_export": "RESOURCE_LIMIT",
                              "lifecycle": ["create", "verify", "restore", "check_restore", "read_all_blobs", "idempotency_replay"]})


if __name__ == "__main__":
    unittest.main()
