"""Real SQLite source protection and private-copy validation for A2.

These exercise SQLite semantics on isolated temporary fixtures. They do not
attest the enclosing filesystem's durability or any NAS profile.
"""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, open as open_ledger
from infra_artifact_ledger import snapshot_sqlite as ss
from infra_artifact_ledger._checkpoints import checkpoint, checkpoint_scope
from infra_artifact_ledger.snapshot_common import Budget, RecoveryError
from infra_artifact_ledger.sqlite_store import _SCHEMA, APPLICATION_ID

from acceptance_helpers import append, create, encode


class SQLiteSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.sqlite"
        self.target = self.root / "copy.sqlite"
        with initialize(self.source) as ledger:
            ledger.execute(encode(create()))
            request, payloads = append("v1", b"snapshot content")
            ledger.execute(encode(request), payloads=payloads)
        self.budget = Budget()

    def tearDown(self):
        self.temporary.cleanup()

    def error(self, code, callback):
        with self.assertRaises(RecoveryError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.publication_state, "not_published")
        return caught.exception

    def snapshot(self):
        return ss.snapshot_database(self.source, self.target, self.budget)

    def members(self):
        return {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}

    def test_backup_is_full_state_and_source_unchanged(self):
        before = self.source.read_bytes()
        with open_ledger(self.source) as ledger:
            expected = ledger.verify()
        self.assertEqual(self.snapshot(), expected)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(ss.validate_database(self.target, Budget()), expected)
        with open_ledger(self.target) as ledger:
            self.assertEqual(ledger.read_blob("blob:quarterly-v1"), b"snapshot content")
            self.assertTrue(ledger.execute(encode(create()))["replayed"])
            self.assertEqual(ledger.verify(), expected)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["copy.sqlite", "source.sqlite"])

    def test_empty_ledger_snapshot(self):
        self.source.unlink()
        with initialize(self.source) as ledger:
            expected = ledger.verify()
        self.assertEqual(self.snapshot(), expected)
        self.assertEqual(expected["verified_blob_count"], 0)

    def test_source_sqlite_open_does_not_use_immutable_or_writable(self):
        calls = []
        actual = ss.sqlite3.connect

        def connect(database, **kwargs):
            if database.startswith(self.source.as_uri()):
                calls.append(database)
            return actual(database, **kwargs)

        with patch.object(ss.sqlite3, "connect", side_effect=connect):
            self.snapshot()
        self.assertEqual(calls, [self.source.as_uri() + "?mode=ro"])

    def test_existing_caller_transaction_keeps_reserved_lock(self):
        with open_ledger(self.source) as ledger:
            connection = ledger._store.connection
            record = ledger.get_record("artifact", "artifact:quarterly-001")
            record["artifact_id"] = "artifact:uncommitted"
            connection.execute("BEGIN IMMEDIATE")
            ledger._store.insert_record("artifact", record)
            self.assertEqual(self.snapshot()["counts"]["artifacts"], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM records WHERE kind='artifact'").fetchone()[0], 2)
            with open_ledger(self.target) as copied:
                self.assertIsNotNone(copied._store.record("artifact:quarterly-001"))
                self.assertIsNone(copied._store.record("artifact:uncommitted"))
            # A separately started interpreter observes the caller's reserved
            # lock after both the header helper and source connection closed.
            code = """
import sqlite3, sys
c = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)
try:
    c.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError as e:
    print(getattr(e, 'sqlite_errorcode', -1) & 255)
else:
    print('LOCK_LOST')
finally:
    c.close()
"""
            result = subprocess.run([sys.executable, "-I", "-c", code, str(self.source)],
                                    capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(result.stdout.strip(), str(sqlite3.SQLITE_BUSY))
            self.assertTrue(connection.in_transaction)
            connection.rollback()
            self.assertEqual(connection.execute("SELECT count(*) FROM records WHERE kind='artifact'").fetchone()[0], 1)
            self.assertIsNone(ledger._store.record("artifact:uncommitted"))

    def test_t0_excludes_later_commit_and_releases_source_before_validation(self):
        fixed = threading.Event()
        writer_started = threading.Event()
        writer_committed = threading.Event()
        failures = []
        source = self.source

        def writer():
            try:
                if not fixed.wait(10):
                    raise AssertionError("T0 was never reached")
                with open_ledger(source) as ledger:
                    writer_started.set()
                    ledger.execute(encode(create("artifact:after-t0", "after-t0")))
                writer_committed.set()
            except BaseException as error:
                failures.append(error)
                writer_started.set()

        thread = threading.Thread(target=writer)
        actual_budget = ss._format_and_budget
        actual_validate = ss.validate_database
        invoked = False

        def pin(connection, budget, stage):
            nonlocal invoked
            result = actual_budget(connection, budget, stage)
            if not invoked:
                invoked = True
                fixed.set()
                self.assertTrue(writer_started.wait(10))
                self.assertFalse(writer_committed.is_set())
            return result

        def validate(path, budget):
            self.assertTrue(writer_committed.wait(10), "source read lock survived into deep validation")
            return actual_validate(path, budget)

        thread.start()
        try:
            with patch.object(ss, "_format_and_budget", side_effect=pin), patch.object(ss, "validate_database", side_effect=validate):
                summary = self.snapshot()
        finally:
            fixed.set()
            thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(summary["counts"]["artifacts"], 1)
        with open_ledger(self.source) as ledger:
            self.assertEqual(ledger.verify()["counts"]["artifacts"], 2)

    def test_wal_without_sidecars_rejected_before_sqlite_open(self):
        with sqlite3.connect(self.source) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        connection.close()
        self.assertFalse(Path(str(self.source) + "-wal").exists())
        before = self.members()
        with patch.object(ss, "_connect_readonly", side_effect=AssertionError("SQLite must not open WAL")):
            self.error("UNSUPPORTED_FORMAT", self.snapshot)
        self.assertEqual(self.members(), before)

    def test_wal_existing_sidecars_are_not_modified(self):
        connection = sqlite3.connect(self.source, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("UPDATE records SET data=data")
            before = self.members()
            self.assertIn("source.sqlite-wal", before)
            self.assertIn("source.sqlite-shm", before)
            self.error("UNSUPPORTED_FORMAT", self.snapshot)
            self.assertEqual(self.members(), before)
        finally:
            connection.close()

    def test_hot_journal_is_not_recovered_or_removed(self):
        with open_ledger(self.source) as ledger:
            request, payloads = append("big", b"x" * (1024 * 1024))
            ledger.execute(encode(request), payloads=payloads)
        code = """
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute('PRAGMA cache_size=1')
c.execute('BEGIN IMMEDIATE')
c.execute('UPDATE payloads SET data=zeroblob(length(data))')
os._exit(0)
"""
        subprocess.run([sys.executable, "-I", "-c", code, str(self.source)], check=True, timeout=10)
        journal = Path(str(self.source) + "-journal")
        self.assertTrue(journal.exists())
        self.assertNotEqual(journal.read_bytes()[:8], b"\x00" * 8)
        before = self.members()
        self.error("INTEGRITY_FAILURE", self.snapshot)
        self.assertEqual(self.members(), before)

    def test_unknown_schema_and_utf16_refused_without_rewrite(self):
        with sqlite3.connect(self.source) as connection:
            connection.execute("PRAGMA user_version=99")
        before = self.source.read_bytes()
        self.error("UNSUPPORTED_FORMAT", self.snapshot)
        self.assertEqual(self.source.read_bytes(), before)
        self.source.unlink()
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("PRAGMA encoding='UTF-16le'")
            for statement in _SCHEMA:
                connection.execute(statement)
            connection.execute("INSERT INTO ledger_format VALUES(1, 'bounded-local-v0.1')")
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        finally:
            connection.close()
        before = self.source.read_bytes()
        self.error("UNSUPPORTED_FORMAT", self.snapshot)
        self.error("UNSUPPORTED_FORMAT", lambda: ss.validate_database(self.source, Budget()))
        self.assertEqual(self.source.read_bytes(), before)

    def test_short_bad_and_nonordinary_sources_never_open_sqlite(self):
        for raw in (b"", b"x" * 100, b"SQLite format 3\x00" + b"\x00" * 30):
            self.source.write_bytes(raw)
            with patch.object(ss, "_connect_readonly", side_effect=AssertionError("invalid source was opened")):
                self.error("INTEGRITY_FAILURE", self.snapshot)
            self.assertEqual(self.source.read_bytes(), raw)
        self.source.unlink()
        os.mkfifo(self.source)
        self.error("INVALID_INPUT", self.snapshot)
        self.source.unlink()
        self.source.symlink_to(self.target)
        self.error("INVALID_INPUT", self.snapshot)

    def test_subprocess_start_failure_has_no_fallback(self):
        with patch.object(ss.subprocess, "Popen", side_effect=OSError("cannot start")), patch.object(
            ss, "_connect_readonly", side_effect=AssertionError("fallback opened source")
        ):
            self.error("IO_ERROR", self.snapshot)
        self.assertFalse(self.target.exists())

    def test_child_abnormal_invalid_and_overlimit_output_reaped(self):
        original = ss.subprocess.Popen
        for code in ("raise SystemExit(3)", "print('invalid')", "print('x' * 3000)", "print('{}')"):
            children = []

            def child(*args, **kwargs):
                process = original([sys.executable, "-I", "-c", code], **kwargs)
                children.append(process)
                return process

            with self.subTest(code=code), patch.object(ss.subprocess, "Popen", side_effect=child), patch.object(
                ss, "_connect_readonly", side_effect=AssertionError("fallback opened source")
            ):
                self.error("IO_ERROR", self.snapshot)
            self.assertTrue(all(process.poll() is not None and process.stdout.closed for process in children))

    def test_child_deadline_terminates_and_reaps(self):
        original = ss.subprocess.Popen
        children = []

        def child(*args, **kwargs):
            process = original([sys.executable, "-I", "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(process)
            return process

        self.budget.deadline = time.monotonic() + 0.15
        with patch.object(ss.subprocess, "Popen", side_effect=child):
            self.error("TIMEOUT", self.snapshot)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)

    def test_detected_source_binding_change_refuses_to_open_replacement(self):
        original = ss._read_header

        def replace(path, budget):
            result = original(path, budget)
            path.rename(self.root / "original.sqlite")
            path.write_bytes(b"replacement")
            return result

        with patch.object(ss, "_read_header", side_effect=replace), patch.object(
            ss, "_connect_readonly", side_effect=AssertionError("replacement was opened")
        ):
            self.error("IO_ERROR", self.snapshot)
        self.assertEqual(self.source.read_bytes(), b"replacement")

    def test_source_preflight_creates_no_files_and_does_not_open_sqlite(self):
        before = self.members()
        with patch.object(ss, "_connect_readonly", side_effect=AssertionError("preflight opened SQLite")):
            binding = ss.preflight_source(self.source, self.budget)
        self.assertEqual(self.members(), before)
        with patch.object(ss, "_read_header", side_effect=AssertionError("preflight unnecessarily repeated")):
            summary = ss.snapshot_database(self.source, self.target, self.budget, source_binding=binding)
        self.assertEqual(summary["counts"]["artifacts"], 1)

    def test_supplied_preflight_binding_refuses_changed_source_before_sqlite(self):
        binding = ss.preflight_source(self.source, self.budget)
        self.source.rename(self.root / "original.sqlite")
        self.source.write_bytes(b"replacement")
        with patch.object(ss, "_connect_readonly", side_effect=AssertionError("replacement was opened")):
            self.error("IO_ERROR", lambda: ss.snapshot_database(
                self.source, self.target, self.budget, source_binding=binding,
            ))
        self.assertFalse(self.target.exists())
        self.assertEqual(self.source.read_bytes(), b"replacement")

    def test_source_busy_and_expired_budget_do_not_create_target(self):
        connection = sqlite3.connect(self.source, isolation_level=None)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            self.error("BUSY", self.snapshot)
        finally:
            connection.close()
        self.assertFalse(self.target.exists())
        self.budget.deadline = time.monotonic() - 1
        self.error("TIMEOUT", self.snapshot)

    def test_backup_failure_and_dynamic_growth_release_source_locks(self):
        original_connect = ss._connect_readonly
        for failure_mode, code in (("io", "IO_ERROR"), ("growth", "RESOURCE_LIMIT")):
            with self.subTest(mode=failure_mode):
                class FailingBackup(sqlite3.Connection):
                    def backup(inner, target, *, pages, progress, sleep):
                        if failure_mode == "growth":
                            progress(sqlite3.SQLITE_OK, 1, ss.MAX_DATABASE // 4096 + 1)
                        raise OSError("synthetic backup failure")

                def connect(path):
                    if path == self.source:
                        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                               isolation_level=None, timeout=0, factory=FailingBackup)
                    return original_connect(path)

                before = self.source.read_bytes()
                with patch.object(ss, "_connect_readonly", side_effect=connect):
                    self.error(code, self.snapshot)
                self.assertEqual(self.source.read_bytes(), before)
                # A different process can take EXCLUSIVE immediately: the
                # failure path has released the source read transaction.
                probe = subprocess.run([
                    sys.executable, "-I", "-c",
                    "import sqlite3,sys; c=sqlite3.connect(sys.argv[1],timeout=0); c.execute('BEGIN EXCLUSIVE'); c.close()",
                    str(self.source),
                ], capture_output=True, timeout=10)
                self.assertEqual(probe.returncode, 0, probe.stderr)
                if self.target.exists():
                    self.target.unlink()

    def test_private_validation_rejects_blob_fk_indexes_and_sidecars(self):
        self.snapshot()
        original = self.target.read_bytes()
        cases = (
            "UPDATE payloads SET data=x'00'",
            "UPDATE refs SET target_id='missing:record'",
            "DELETE FROM refs",
            "UPDATE operations SET key='changed:operation'",
        )
        for statement in cases:
            with self.subTest(statement=statement):
                self.target.write_bytes(original)
                with sqlite3.connect(self.target) as connection:
                    connection.execute(statement)
                connection.close()
                before = self.target.read_bytes()
                self.error("INTEGRITY_FAILURE", lambda: ss.validate_database(self.target, Budget()))
                self.assertEqual(self.target.read_bytes(), before)
        self.target.write_bytes(original)
        sidecar = Path(str(self.target) + "-journal")
        sidecar.symlink_to(self.root / "missing")
        self.error("INTEGRITY_FAILURE", lambda: ss.validate_database(self.target, Budget()))
        self.assertTrue(sidecar.is_symlink())

    def test_private_physical_corruption_is_rejected(self):
        self.snapshot()
        raw = bytearray(self.target.read_bytes())
        # Page 1's b-tree type, not merely the magic header.
        raw[100] = 0xFF
        self.target.write_bytes(raw)
        self.error("INTEGRITY_FAILURE", lambda: ss.validate_database(self.target, Budget()))

    def test_wrong_sql_storage_classes_and_invalid_utf8_are_integrity_failures(self):
        original = self.source.read_bytes()
        for statement in (
            "UPDATE records SET data=CAST(data AS BLOB) WHERE kind='artifact'",
            "UPDATE records SET kind=CAST(x'ff' AS TEXT) WHERE kind='artifact'",
            "UPDATE payloads SET data=CAST(data AS TEXT)",
            "UPDATE payloads SET blob_ref=CAST(blob_ref AS BLOB)",
            "UPDATE operations SET result_ref=CAST(result_ref AS BLOB)",
        ):
            with self.subTest(statement=statement):
                self.source.write_bytes(original)
                if self.target.exists():
                    self.target.unlink()
                with sqlite3.connect(self.source) as connection:
                    connection.execute(statement)
                connection.close()
                before = self.source.read_bytes()
                self.error("INTEGRITY_FAILURE", self.snapshot)
                self.assertEqual(self.source.read_bytes(), before)

    def test_target_and_sidecar_refuse_overwrite(self):
        for occupied in (self.target, Path(str(self.target) + "-journal")):
            occupied.write_bytes(b"preserve")
            self.error("TARGET_EXISTS", self.snapshot)
            self.assertEqual(occupied.read_bytes(), b"preserve")
            occupied.unlink()

    def test_python_graph_timeout_preserves_classification_and_scope(self):
        self.snapshot()
        count = 0

        def stop():
            nonlocal count
            count += 1
            raise RecoveryError("TIMEOUT", "synthetic deadline", "verify")

        with open_ledger(self.target) as ledger:
            with self.assertRaises(RecoveryError) as caught:
                with checkpoint_scope(stop):
                    ledger.verify()
            self.assertEqual(caught.exception.code, "TIMEOUT")
            self.assertFalse(ledger._store.connection.in_transaction)
            self.assertEqual(ledger.verify()["verified_blob_count"], 1)
        self.assertEqual(count, 1)
        checkpoint()  # Unrelated A1 work is back to the default no-op.

    def test_sqlite_progress_timeout_is_not_mapped_to_io_error(self):
        self.snapshot()
        original = ss._format_and_budget

        def expire(connection, budget, stage):
            original(connection, budget, stage)
            budget.deadline = time.monotonic() - 1
            connection.execute(
                "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n<10000) SELECT sum(n) FROM r"
            ).fetchone()

        with patch.object(ss, "_format_and_budget", side_effect=expire):
            self.error("TIMEOUT", lambda: ss.validate_database(self.target, Budget()))

    def test_metadata_bytes_count_utf8_and_limit_boundary(self):
        # This tests the pre-decoding budget layer. The deliberately synthetic
        # data is not presented as semantically valid Ledger metadata.
        with sqlite3.connect(self.source) as connection:
            connection.execute("DELETE FROM operations")
            connection.execute("DELETE FROM records")
            connection.execute("DELETE FROM payloads")
            connection.execute("DELETE FROM refs")
            limit = ss.MAX_METADATA_BYTES
            text = "中" * (limit // 3) + "x" * (limit % 3)
            connection.execute("INSERT INTO records VALUES('artifact:budget','artifact',?)", (text,))
        connection.close()
        connection = ss._connect_readonly(self.source)
        try:
            with ss._progress(connection, Budget(), "verify"):
                ss._format_and_budget(connection, Budget(), "verify")
        finally:
            connection.close()
        with sqlite3.connect(self.source) as connection:
            connection.execute("UPDATE records SET data=data || 'x'")
        connection.close()
        self.error("RESOURCE_LIMIT", self.snapshot)

    def test_metadata_row_limit_precedes_decoding(self):
        with sqlite3.connect(self.source) as connection:
            connection.execute("DELETE FROM operations")
            connection.execute("DELETE FROM records")
            connection.execute("DELETE FROM payloads")
            connection.execute("DELETE FROM refs")
            connection.executemany("INSERT INTO records VALUES(?,'artifact','{}')",
                                   ((f"artifact:{index}",) for index in range(ss.MAX_METADATA_ROWS)))
        connection.close()
        connection = ss._connect_readonly(self.source)
        try:
            ss._format_and_budget(connection, Budget(), "verify")
        finally:
            connection.close()
        with sqlite3.connect(self.source) as connection:
            connection.execute("INSERT INTO records VALUES('artifact:extra','artifact','{}')")
        connection.close()
        self.error("RESOURCE_LIMIT", self.snapshot)

    def test_database_file_over_limit_rejected_before_child(self):
        with self.source.open("r+b") as stream:
            stream.truncate(ss.MAX_DATABASE + 1)
        with patch.object(ss, "_read_header", side_effect=AssertionError("oversized source inspected")):
            self.error("RESOURCE_LIMIT", self.snapshot)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
