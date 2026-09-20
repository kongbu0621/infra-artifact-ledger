"""Read failures keep their classification and metadata keeps its row binding."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize
from acceptance_helpers import ARTIFACT, SCOPE, append, create, encode


class ReadIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "ledger.sqlite"
        self.ledger = initialize(self.path)
        self.ledger.execute(encode(create()))

    def tearDown(self):
        self.ledger.close()
        self.directory.cleanup()

    def assert_error(self, code, callback):
        with self.assertRaises(LedgerError) as failure:
            callback()
        self.assertEqual((failure.exception.code, failure.exception.commit_state),
                         (code, "not_applicable"))
        self.assertFalse(self.ledger._store.connection.in_transaction)
        return failure.exception

    def test_verify_real_exclusive_lock_is_busy_and_does_not_claim_corruption(self):
        before = self.ledger.verify()
        blocker = sqlite3.connect(self.path, isolation_level=None)
        try:
            blocker.execute("BEGIN EXCLUSIVE")
            self.assert_error("BUSY", self.ledger.verify)
        finally:
            blocker.rollback()
            blocker.close()
        self.assertEqual(self.ledger.verify(), before)

    def test_verify_metadata_read_failures_keep_sqlite_classification(self):
        for sqlite_code, expected in ((sqlite3.SQLITE_BUSY, "BUSY"),
                                      (sqlite3.SQLITE_LOCKED, "BUSY"),
                                      (sqlite3.SQLITE_IOERR_READ, "IO_ERROR"),
                                      (sqlite3.SQLITE_CORRUPT, "INTEGRITY_FAILURE")):
            error = sqlite3.OperationalError("injected metadata read failure")
            error.sqlite_errorcode = sqlite_code
            with self.subTest(sqlite_code=sqlite_code), \
                    patch.object(self.ledger._store, "metadata", side_effect=error):
                public = self.assert_error(expected, self.ledger.verify)
            if expected == "INTEGRITY_FAILURE":
                self.assertEqual(set(public.details), {"failures", "truncated"})
                self.assertEqual(public.details["failures"][0]["location"], "metadata")
                self.assertFalse(public.details["truncated"])

    def test_verify_corrupt_json_still_has_integrity_failure_details(self):
        self.ledger._store.execute("UPDATE records SET data='{' WHERE id=?", (ARTIFACT,))
        error = self.assert_error("INTEGRITY_FAILURE", self.ledger.verify)
        self.assertEqual(set(error.details), {"failures", "truncated"})
        self.assertFalse(error.details["truncated"])

    def check_verify_later_read_errors(self, seam, location):
        request, payloads = append("v1", b"report")
        self.ledger.execute(encode(request), payloads=payloads)
        for sqlite_code, expected in ((sqlite3.SQLITE_BUSY, "BUSY"),
                                      (sqlite3.SQLITE_IOERR_READ, "IO_ERROR"),
                                      (sqlite3.SQLITE_CORRUPT, "INTEGRITY_FAILURE")):
            error = sqlite3.DatabaseError("injected later read failure")
            error.sqlite_errorcode = sqlite_code
            with self.subTest(sqlite_code=sqlite_code), \
                    patch.object(self.ledger, seam, side_effect=error):
                public = self.assert_error(expected, self.ledger.verify)
            if expected == "INTEGRITY_FAILURE":
                self.assertIsInstance(public.details, dict)
                self.assertEqual(set(public.details), {"failures", "truncated"})
                self.assertEqual(len(public.details["failures"]), 1)
                self.assertEqual(public.details["failures"][0]["location"], location)
                self.assertFalse(public.details["truncated"])
        for code in ("BUSY", "IO_ERROR"):
            with self.subTest(public_error=code), patch.object(
                self.ledger, seam, side_effect=LedgerError(code, "injected public read failure")
            ):
                self.assert_error(code, self.ledger.verify)

    def test_verify_index_read_errors_keep_classification_and_failure_shape(self):
        self.check_verify_later_read_errors("_verify_indexes", "indexes")

    def test_verify_payload_read_errors_keep_classification_and_failure_shape(self):
        self.check_verify_later_read_errors("_blob", "blob:quarterly-v1")

    def test_verify_invalid_blob_metadata_remains_a_stored_integrity_failure(self):
        request, payloads = append("v1", b"report")
        self.ledger.execute(encode(request), payloads=payloads)
        record = dict(request["body"]["blobs"][0], byte_length=-1)
        self.ledger._store.execute("UPDATE records SET data=? WHERE id=?",
                                   (encode(record).decode("utf-8"), record["blob_ref"]))
        error = self.assert_error("INTEGRITY_FAILURE", self.ledger.verify)
        self.assertEqual(set(error.details), {"failures", "truncated"})
        self.assertEqual({failure["location"] for failure in error.details["failures"]},
                         {"metadata", "blob:quarterly-v1"})

    def test_history_rejects_permuted_version_bodies_instead_of_wrong_order(self):
        for suffix in ("first", "second"):
            request, payloads = append(suffix, b"report")
            self.ledger.execute(encode(request), payloads=payloads)
        rows = list(self.ledger._store.execute(
            "SELECT id,data FROM records WHERE kind='version' ORDER BY id"
        ))
        self.ledger._store.begin(write=True)
        for index, (identity, _) in enumerate(rows):
            self.ledger._store.execute("UPDATE records SET data=? WHERE id=?",
                                       (rows[1-index][1], identity))
        self.ledger._store.commit()
        self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.get_history(ARTIFACT))

    def test_operation_query_rejects_result_index_disagreement(self):
        self.ledger.execute(encode(create("artifact:other", "create-other")))
        self.ledger._store.execute(
            "UPDATE operations SET result_ref='artifact:other' WHERE key='create-quarterly'"
        )
        self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.get_operation(
            SCOPE, "create_artifact", "create-quarterly"
        ))

    def test_metadata_queries_remain_available_when_only_payload_is_corrupt(self):
        request, payloads = append("v1", b"report")
        self.ledger.execute(encode(request), payloads=payloads)
        expected_history = self.ledger.get_history(ARTIFACT)
        expected_operation = self.ledger.get_operation(SCOPE, "append_version", "append-v1")
        self.ledger._store.execute("UPDATE payloads SET data=?", (b"broken",))
        self.assertEqual(self.ledger.get_history(ARTIFACT), expected_history)
        self.assertEqual(self.ledger.get_record("version", "version:quarterly-v1"),
                         expected_operation["result"])
        self.assertEqual(self.ledger.get_operation(SCOPE, "append_version", "append-v1"),
                         expected_operation)
        self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.read_blob("blob:quarterly-v1"))

    def test_invalid_utf8_sql_text_is_stored_corruption_not_io_failure(self):
        self.ledger._store.execute("UPDATE records SET data=CAST(x'ff' AS TEXT) WHERE id=?", (ARTIFACT,))
        for operation in (lambda: self.ledger.get_record("artifact", ARTIFACT),
                          lambda: self.ledger.get_history(ARTIFACT),
                          self.ledger.verify, self.ledger.export_bundle):
            with self.subTest(operation=operation):
                self.assert_error("INTEGRITY_FAILURE", operation)

    def test_nonbinary_payload_is_rejected_before_fetching_its_data(self):
        request, payloads = append("v1", b"report")
        self.ledger.execute(encode(request), payloads=payloads)
        for expression in ("CAST(x'00ffff' AS TEXT)", "17", "1.25"):
            self.ledger._store.execute("UPDATE payloads SET data=" + expression)
            statements = []
            self.ledger._store.connection.set_trace_callback(statements.append)
            try:
                with self.subTest(expression=expression):
                    self.assert_error("INTEGRITY_FAILURE", lambda: self.ledger.read_blob("blob:quarterly-v1"))
                    error = self.assert_error("INTEGRITY_FAILURE", self.ledger.verify)
                    self.assertEqual(set(error.details), {"failures", "truncated"})
            finally:
                self.ledger._store.connection.set_trace_callback(None)
            self.assertFalse(any(statement.startswith("SELECT data FROM payloads")
                                 for statement in statements),
                             "Nonbinary payloads must be rejected before fetching their contents")


if __name__ == "__main__":
    unittest.main()
