"""Independent review regressions for publication, format and cleanup boundaries."""

from pathlib import Path
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from infra_artifact_ledger import sqlite_store
from acceptance_helpers import ARTIFACT, SCOPE, create, encode


class StorageBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / "ledger.sqlite"

    def tearDown(self):
        self.directory.cleanup()

    def assert_error(self, code, callback):
        with self.assertRaises(LedgerError) as failure:
            callback()
        self.assertEqual((failure.exception.code, failure.exception.commit_state),
                         (code, "not_applicable"))
        return failure.exception

    def assert_no_staging(self):
        self.assertFalse(any(path.name.startswith(".artifact-ledger-init-")
                             for path in self.root.iterdir()))

    def unrelated_database(self, path):
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.execute("INSERT INTO unrelated VALUES ('keep')")
            connection.commit()
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        finally:
            connection.close()

    def test_competing_target_before_publication_is_untouched(self):
        real_link = os.link
        captured = {}

        def competitor(source, target):
            # Initialization has already built its own complete database, but
            # a different file reaches the public target before publication.
            self.unrelated_database(target)
            captured["bytes"] = Path(target).read_bytes()
            return real_link(source, target)

        with patch.object(sqlite_store.os, "link", side_effect=competitor):
            self.assert_error("IDENTITY_CONFLICT", lambda: initialize(self.path))
        self.assertEqual(self.path.read_bytes(), captured["bytes"])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                             [("unrelated",)])
        self.assert_no_staging()

    def test_existing_dangling_symlink_is_never_replaced(self):
        self.path.symlink_to(self.root / "missing.sqlite")
        self.assert_error("IDENTITY_CONFLICT", lambda: initialize(self.path))
        self.assertTrue(self.path.is_symlink())
        self.assertFalse((self.root / "missing.sqlite").exists())
        self.assert_no_staging()

    def test_existing_sqlite_sidecars_reserve_an_absent_database_target(self):
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(sidecar=suffix):
                sidecar = Path(str(self.path) + suffix)
                sidecar.write_bytes(b"existing SQLite recovery data")
                self.assert_error("IDENTITY_CONFLICT", lambda: initialize(self.path))
                self.assertFalse(self.path.exists())
                self.assertEqual(sidecar.read_bytes(), b"existing SQLite recovery data")
                sidecar.unlink()
        self.assert_no_staging()

    def test_sidecar_appearing_during_build_is_rejected_before_publication(self):
        commit = sqlite_store.SQLiteStore.commit
        sidecar = Path(str(self.path) + "-wal")

        def commit_then_sidecar(store):
            commit(store)
            sidecar.write_bytes(b"recovery file arrived during initialization")

        with patch.object(sqlite_store.SQLiteStore, "commit", commit_then_sidecar):
            self.assert_error("IDENTITY_CONFLICT", lambda: initialize(self.path))
        self.assertFalse(self.path.exists())
        self.assertEqual(sidecar.read_bytes(), b"recovery file arrived during initialization")
        self.assert_no_staging()

    def test_real_hot_journal_is_preserved_without_attempting_recovery(self):
        donor = self.root / "donor.sqlite"
        with initialize(donor) as ledger:
            ledger.execute(encode(create()))
        connection = sqlite3.connect(donor, isolation_level=None)
        try:
            connection.execute("PRAGMA cache_size=1")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE records SET data=data || zeroblob(100000)")
            hot_journal = Path(str(donor) + "-journal").read_bytes()
            self.assertEqual(hot_journal[:8], bytes.fromhex("d9d505f920a163d7"))
            sidecar = Path(str(self.path) + "-journal")
            sidecar.write_bytes(hot_journal)
        finally:
            connection.execute("ROLLBACK")
            connection.close()
        self.assert_error("IDENTITY_CONFLICT", lambda: initialize(self.path))
        self.assertFalse(self.path.exists())
        self.assertEqual(sidecar.read_bytes(), hot_journal)
        self.assert_no_staging()

    def test_legitimate_writer_journal_after_publication_is_not_removed(self):
        sync = sqlite_store._fsync_directory
        ready, release = threading.Event(), threading.Event()
        results, threads = [], []

        def write():
            try:
                with open_ledger(self.path) as ledger:
                    commit = ledger._store.commit

                    def pause_before_commit():
                        ready.set()
                        if not release.wait(5):
                            raise TimeoutError("initializer did not release the writer")
                        commit()

                    with patch.object(ledger._store, "commit", side_effect=pause_before_commit):
                        results.append(ledger.execute(encode(create())))
            except BaseException as error:
                results.append(error)
                ready.set()

        def start_writer(directory):
            sync(directory)
            thread = threading.Thread(target=write)
            threads.append(thread)
            thread.start()
            self.assertTrue(ready.wait(5))
            self.assertTrue(Path(str(self.path) + "-journal").exists())

        try:
            with patch.object(sqlite_store, "_fsync_directory", side_effect=start_writer):
                with initialize(self.path) as ledger:
                    self.assertTrue(Path(str(self.path) + "-journal").exists())
                    self.assertEqual(ledger.verify()["counts"]["artifacts"], 0)
        finally:
            release.set()
            for thread in threads:
                thread.join(5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], dict)
        self.assertEqual(results[0]["status"], "COMMITTED")

    def test_build_failure_does_not_publish_partial_database(self):
        with patch.object(sqlite_store.SQLiteStore, "commit", side_effect=OSError("commit failure")):
            self.assert_error("IO_ERROR", lambda: initialize(self.path))
        self.assertFalse(self.path.exists())
        self.assert_no_staging()

    def test_directory_sync_failure_preserves_complete_published_database(self):
        with patch.object(sqlite_store, "_fsync_directory", side_effect=OSError("directory sync failure")):
            self.assert_error("IO_ERROR", lambda: initialize(self.path))
        self.assertTrue(self.path.exists())
        with open_ledger(self.path) as ledger:
            self.assertEqual(ledger.verify()["counts"]["artifacts"], 0)
            self.assertEqual(ledger.execute(encode(create()))["status"], "COMMITTED")
        self.assert_no_staging()

    def test_reopen_failure_preserves_complete_published_database(self):
        real_connect = sqlite3.connect

        def fail_public_reopen(database, *args, **kwargs):
            if str(database) == self.path.as_uri() + "?mode=rw":
                raise OSError("reopen failure")
            return real_connect(database, *args, **kwargs)

        with patch.object(sqlite_store.sqlite3, "connect", side_effect=fail_public_reopen):
            self.assert_error("IO_ERROR", lambda: initialize(self.path))
        with open_ledger(self.path) as ledger:
            self.assertEqual(ledger.verify()["counts"]["artifacts"], 0)
        self.assert_no_staging()

    def test_replacement_after_publication_is_not_opened_or_deleted(self):
        replacement = self.root / "replacement.sqlite"
        self.unrelated_database(replacement)
        before = replacement.read_bytes()

        def replace_after_publish(directory):
            os.replace(replacement, self.path)

        with patch.object(sqlite_store, "_fsync_directory", side_effect=replace_after_publish):
            self.assert_error("IO_ERROR", lambda: initialize(self.path))
        self.assertEqual(self.path.read_bytes(), before)
        self.assert_no_staging()

    def test_cleanup_failure_does_not_replace_publication_conflict(self):
        real_link = os.link
        cleanup = tempfile.TemporaryDirectory.cleanup

        def competitor(source, target):
            Path(target).write_bytes(b"other owner's file")
            return real_link(source, target)

        def cleanup_then_fail(temporary):
            cleanup(temporary)
            raise OSError("cleanup failure")

        with patch.object(sqlite_store.os, "link", side_effect=competitor), \
                patch.object(tempfile.TemporaryDirectory, "cleanup", cleanup_then_fail):
            self.assert_error("IDENTITY_CONFLICT", lambda: initialize(self.path))
        self.assertEqual(self.path.read_bytes(), b"other owner's file")
        self.assert_no_staging()

    def test_cleanup_failure_after_publication_preserves_complete_target(self):
        cleanup = tempfile.TemporaryDirectory.cleanup

        def cleanup_then_fail(temporary):
            cleanup(temporary)
            raise OSError("cleanup failure")

        with patch.object(tempfile.TemporaryDirectory, "cleanup", cleanup_then_fail):
            self.assert_error("IO_ERROR", lambda: initialize(self.path))
        with open_ledger(self.path) as ledger:
            self.assertEqual(ledger.verify()["counts"]["artifacts"], 0)
        self.assert_no_staging()

    def test_connection_close_failure_does_not_replace_original_format_error(self):
        initialize(self.path).close()
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE VIEW unexpected AS SELECT * FROM records")
        real_connect = sqlite3.connect

        class CloseFailureConnection(sqlite3.Connection):
            def close(self):
                super().close()
                raise OSError("close failure")

        def connect(database, *args, **kwargs):
            kwargs["factory"] = CloseFailureConnection
            return real_connect(database, *args, **kwargs)

        with patch.object(sqlite_store.sqlite3, "connect", side_effect=connect):
            self.assert_error("UNSUPPORTED_VERSION", lambda: open_ledger(self.path))

    def test_stage_close_failure_does_not_replace_original_build_error(self):
        real_connect = sqlite3.connect

        class CloseFailureConnection(sqlite3.Connection):
            def close(self):
                super().close()
                raise OSError("close failure")

        def connect(database, *args, **kwargs):
            kwargs["factory"] = CloseFailureConnection
            return real_connect(database, *args, **kwargs)

        with patch.object(sqlite_store.sqlite3, "connect", side_effect=connect), \
                patch.object(sqlite_store.SQLiteStore, "commit",
                             side_effect=LedgerError("INTEGRITY_FAILURE", "build failure")):
            self.assert_error("INTEGRITY_FAILURE", lambda: initialize(self.path))
        self.assertFalse(self.path.exists())
        self.assert_no_staging()

    def test_foreign_schema_objects_and_missing_constraints_are_rejected(self):
        mutations = {
            "trigger": ["CREATE TRIGGER omit_operations BEFORE INSERT ON operations BEGIN SELECT RAISE(IGNORE); END"],
            "view": ["CREATE VIEW artifact_view AS SELECT * FROM records"],
            "missing_index": ["DROP INDEX refs_target"],
            "missing_pk": ["DROP TABLE records",
                           "CREATE TABLE records (id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL)",
                           "CREATE INDEX records_kind ON records(kind,id)"],
            "missing_fk": ["DROP TABLE payloads",
                           "CREATE TABLE payloads (blob_ref TEXT PRIMARY KEY, data BLOB NOT NULL)"],
            "missing_not_null": ["DROP TABLE payloads",
                                 "CREATE TABLE payloads (blob_ref TEXT PRIMARY KEY REFERENCES records(id), data BLOB)"],
        }
        for name, statements in mutations.items():
            with self.subTest(mutation=name):
                path = self.root / (name + ".sqlite")
                initialize(path).close()
                with sqlite3.connect(path) as connection:
                    for statement in statements:
                        connection.execute(statement)
                before = path.read_bytes()
                self.assert_error("UNSUPPORTED_VERSION", lambda: open_ledger(path))
                self.assertEqual(path.read_bytes(), before)

    def test_context_cleanup_preserves_committed_failure_and_identity(self):
        ledger = initialize(self.path)
        close = ledger._store.close
        try:
            with patch.object(ledger, "_success", side_effect=OSError("response preparation failure")), \
                    patch.object(ledger._store, "close", side_effect=OSError("close failure")):
                with self.assertRaises(LedgerError) as failure:
                    with ledger:
                        ledger.execute(encode(create()))
            error = failure.exception
            self.assertEqual((error.code, error.commit_state), ("IO_ERROR", "committed"))
            self.assertEqual(error.details["result_ref"], ARTIFACT)
            self.assertEqual(error.details["idempotency_scope_ref"], SCOPE)
            self.assertEqual(error.details["operation_kind"], "create_artifact")
            self.assertEqual(error.details["idempotency_key"], "create-quarterly")
            self.assertIn("recorded_at", error.details)
            self.assertTrue(error.__notes__)
        finally:
            close()
        with open_ledger(self.path) as reopened:
            self.assertEqual(reopened.verify()["counts"]["artifacts"], 1)

    def test_context_cleanup_preserves_callers_exception(self):
        ledger = initialize(self.path)
        close = ledger._store.close
        original = ValueError("caller failure")
        try:
            with patch.object(ledger._store, "close", side_effect=OSError("close failure")):
                with self.assertRaises(ValueError) as failure:
                    with ledger:
                        raise original
            self.assertIs(failure.exception, original)
            self.assertTrue(original.__notes__)
        finally:
            close()

    def test_context_without_prior_exception_still_reports_close_failure(self):
        ledger = initialize(self.path)
        close = ledger._store.close
        try:
            with patch.object(ledger._store, "close", side_effect=OSError("close failure")):
                def operation():
                    with ledger:
                        pass
                self.assert_error("IO_ERROR", operation)
        finally:
            close()


if __name__ == "__main__":
    unittest.main()
