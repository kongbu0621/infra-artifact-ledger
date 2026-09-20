"""SQL indexes must remain paired with the immutable JSON in their own row."""

from pathlib import Path
import tempfile
import unittest

from infra_artifact_ledger import LedgerError, initialize
from acceptance_helpers import SCOPE, create, encode


class IndexBindingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.ledger = initialize(Path(self.directory.name) / "ledger.sqlite")
        self.identities = ["artifact:first", "artifact:second"]
        self.keys = ["create-first", "create-second"]
        for identity, key in zip(self.identities, self.keys):
            self.ledger.execute(encode(create(identity, key)))

    def tearDown(self):
        self.ledger.close()
        self.directory.cleanup()

    def swap_bodies(self, table):
        column = "id" if table == "records" else "key"
        identities = self.identities if table == "records" else self.keys
        original = [self.ledger._store.execute(
            f"SELECT data FROM {table} WHERE {column}=?", (identity,)
        ).fetchone()[0] for identity in identities]
        self.ledger._store.begin(write=True)
        for index, identity in enumerate(identities):
            self.ledger._store.execute(
                f"UPDATE {table} SET data=? WHERE {column}=?", (original[1-index], identity)
            )
        self.ledger._store.commit()

    def snapshot(self):
        return {
            table: self.ledger._store.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("records", "operations", "refs", "payloads")
        }

    def assert_rejected(self, callback, state):
        before = self.snapshot()
        with self.assertRaises(LedgerError) as failure:
            callback()
        self.assertEqual((failure.exception.code, failure.exception.commit_state),
                         ("INTEGRITY_FAILURE", state))
        self.assertEqual(self.snapshot(), before, "Rejected operation must not repair or otherwise change data")
        self.assertFalse(self.ledger._store.connection.in_transaction)
        return failure.exception

    def check_verify_export_and_write(self, table):
        self.swap_bodies(table)
        failure = self.assert_rejected(self.ledger.verify, "not_applicable")
        self.assertEqual(set(failure.details), {"failures", "truncated"})
        self.assertFalse(failure.details["truncated"])
        self.assert_rejected(self.ledger.export_bundle, "not_applicable")
        self.assert_rejected(
            lambda: self.ledger.execute(encode(create("artifact:third", "create-third"))),
            "not_committed",
        )

    def test_record_body_permutation_is_rejected_by_verify_export_and_new_write(self):
        self.check_verify_export_and_write("records")
        self.assert_rejected(lambda: self.ledger.get_record("artifact", self.identities[0]), "not_applicable")

    def test_operation_body_permutation_is_rejected_by_verify_export_and_new_write(self):
        self.check_verify_export_and_write("operations")
        self.assert_rejected(
            lambda: self.ledger.get_operation(SCOPE, "create_artifact", self.keys[0]), "not_applicable"
        )

    def test_record_body_permutation_rejects_known_replay_as_committed_corruption(self):
        self.swap_bodies("records")
        failure = self.assert_rejected(
            lambda: self.ledger.execute(encode(create(self.identities[0], self.keys[0]))), "committed"
        )
        self.assertEqual(failure.details["result_ref"], self.identities[0])

    def test_correct_rows_remain_readable_verifiable_and_exportable(self):
        before = self.snapshot()
        self.assertEqual(self.ledger.verify()["counts"]["artifacts"], 2)
        self.assertEqual(set(self.ledger.export_bundle()), {"package_utf8", "descriptor_utf8"})
        for identity, key in zip(self.identities, self.keys):
            result = self.ledger.get_operation(SCOPE, "create_artifact", key)
            self.assertEqual(result["idempotency_record"]["result_ref"], identity)
            self.assertEqual(result["result"]["artifact_id"], identity)
            replay = self.ledger.execute(encode(create(identity, key)))
            self.assertEqual(replay["result_ref"], identity)
            self.assertTrue(replay["replayed"])
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
