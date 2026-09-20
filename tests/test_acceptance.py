"""P2–P5 public behaviour, real process/lock failures and durable-state checks."""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger

try:
    from .acceptance_helpers import (ARTIFACT, REPORT_V1, REPORT_V2, SCOPE, append,
                                     create, digest, encode, import_kwargs, import_request)
except ImportError:
    from acceptance_helpers import (ARTIFACT, REPORT_V1, REPORT_V2, SCOPE, append,
                                    create, digest, encode, import_kwargs, import_request)


class AcceptanceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ledger-acceptance-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "source.sqlite"
        self.ledger = initialize(self.database)
        self.addCleanup(self.ledger.close)

    def assert_error(self, code, state, function, *args, **kwargs):
        with self.assertRaises(LedgerError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.commit_state, state)
        return caught.exception

    def counts(self, ledger=None):
        return (ledger or self.ledger).verify()["counts"]

    def seed(self, both=True):
        created = self.ledger.execute(encode(create()))
        v1, p1 = append()
        first = self.ledger.execute(encode(v1), payloads=p1)
        if not both:
            return created, first
        v2, p2 = append("v2", REPORT_V2, ["version:quarterly-v1"], provenance=True)
        second = self.ledger.execute(encode(v2), payloads=p2)
        return created, first, second

    def fresh(self, name="destination"):
        result = initialize(self.root / f"{name}.sqlite")
        self.addCleanup(result.close)
        return result

    def worker(self, mode="write", req=None, payloads=None, barrier=None, marker=None):
        number = len(list(self.root.glob("request-*.json")))
        request_path = self.root / f"request-{number}.json"
        request_path.write_bytes(encode(req or create()))
        payload_path = "-"
        if payloads is not None:
            locations = {}
            for index, (key, data) in enumerate(payloads.items()):
                location = self.root / f"input-{number}-{index}.bin"
                location.write_bytes(data)
                locations[key] = str(location)
            payload_path = self.root / f"payload-{number}.json"
            payload_path.write_bytes(encode(locations))
        env = os.environ.copy()
        source = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [source, env.get("PYTHONPATH")]))
        args = [sys.executable, str(Path(__file__).with_name("acceptance_helpers.py")),
                mode, str(self.database), str(request_path), str(payload_path),
                str(barrier or "-"), str(marker or "-")]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.addCleanup(self.finish_child, process)
        return process

    @staticmethod
    def finish_child(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

    def response(self, process):
        stdout, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
        self.assertEqual(stderr, b"")
        self.assertTrue(stdout.endswith(b"\n"))
        self.assertEqual(stdout.count(b"\n"), 1)
        return json.loads(stdout)


class PublicLifecycleTests(AcceptanceCase):
    def test_report_v1_v2_exact_content_history_replay_and_portable_copy(self):
        _, _, result = self.seed()
        history = self.ledger.get_history(ARTIFACT)
        self.assertEqual([v["version_id"] for v in history["versions"]],
                         ["version:quarterly-v1", "version:quarterly-v2"])
        self.assertEqual(history["versions"][1]["parent_version_refs"], ["version:quarterly-v1"])
        self.assertEqual(history["provenance_links"][0]["object"]["ref"], "version:quarterly-v1")
        self.assertEqual(self.ledger.read_blob("blob:quarterly-v1"), REPORT_V1)
        self.assertEqual(self.ledger.read_blob("blob:quarterly-v2"), REPORT_V2)
        self.assertEqual(self.ledger.verify(), {"counts": {
            "artifacts": 1, "versions": 2, "content_roots": 2, "blobs": 2,
            "manifests": 0, "provenance_links": 1, "idempotency_records": 3,
            "import_receipts": 0}, "verified_blob_count": 2, "verified_byte_length": 40})
        req, payloads = append("v2", REPORT_V2, ["version:quarterly-v1"], provenance=True)
        replay = self.ledger.execute(encode(req), payloads=payloads)
        self.assertEqual(replay, {**result, "replayed": True})
        original_operation = self.ledger.get_operation(SCOPE, "append_version", "append-v2")
        bundle, destination = self.ledger.export_bundle(), self.fresh()
        receipt = destination.execute(encode(import_request()), **import_kwargs(bundle))
        self.assertFalse(receipt["replayed"])
        self.assertEqual(destination.get_history(ARTIFACT), history)
        self.assertEqual(destination.get_operation(SCOPE, "append_version", "append-v2"), original_operation)
        self.assertEqual(destination.read_blob("blob:quarterly-v1"), REPORT_V1)
        self.assertEqual(destination.read_blob("blob:quarterly-v2"), REPORT_V2)
        self.assertEqual(destination.get_record("import_receipt", "receipt:copy")["imported_artifact_refs"], [ARTIFACT])
        self.assertEqual(self.counts(destination)["idempotency_records"], 4)
        self.assertEqual(self.counts(destination)["import_receipts"], 1)
        self.assertEqual(destination.execute(encode(import_request()), **import_kwargs(bundle)),
                         {**receipt, "replayed": True})
        self.assertEqual(self.counts(destination)["idempotency_records"], 4)

    def test_new_processes_write_then_restart_and_read_exact_content(self):
        self.ledger.close()
        self.assertFalse(self.response(self.worker(req=create()))["replayed"])
        for suffix, content, parents, provenance in [
            ("v1", REPORT_V1, [], False), ("v2", REPORT_V2, ["version:quarterly-v1"], True),
        ]:
            req, payloads = append(suffix, content, parents, provenance=provenance)
            self.assertFalse(self.response(self.worker(req=req, payloads=payloads))["replayed"])
        inspected = self.response(self.worker("inspect"))
        self.assertEqual(base64.b64decode(inspected["v1"]), REPORT_V1)
        self.assertEqual(base64.b64decode(inspected["v2"]), REPORT_V2)
        self.assertEqual(inspected["verification"]["verified_byte_length"], 40)
        self.assertEqual(inspected["history"]["provenance_links"][0]["object"]["ref"], "version:quarterly-v1")

    def test_two_ledgers_and_initialization_do_not_overwrite_or_mix(self):
        self.seed()
        isolated = self.fresh("independent")
        self.assertEqual(self.counts(isolated)["artifacts"], 0)
        self.assert_error("NOT_FOUND", "not_applicable", isolated.get_record, "artifact", ARTIFACT)
        before = self.database.read_bytes()
        with self.assertRaises(LedgerError):
            initialize(self.database)
        self.assertEqual(self.database.read_bytes(), before)
        missing = self.root / "not-created.sqlite"
        with self.assertRaises(LedgerError):
            open_ledger(missing)
        self.assertFalse(missing.exists())

    def test_same_key_different_input_preserves_original_record_and_operation(self):
        result = self.ledger.execute(encode(create()))
        before = self.ledger.get_operation(SCOPE, "create_artifact", "create-quarterly")
        changed = create()
        changed["body"]["artifact"]["type_ref"]["type_version"] = "2"
        self.assert_error("IDEMPOTENCY_CONFLICT", "not_committed", self.ledger.execute, encode(changed))
        self.assertEqual(self.ledger.get_operation(SCOPE, "create_artifact", "create-quarterly"), before)
        self.assertEqual(self.ledger.execute(encode(create())), {**result, "replayed": True})
        self.assertEqual(self.counts()["idempotency_records"], 1)

    def test_context_exit_does_not_rollback_earlier_successes_or_swallow_error(self):
        self.ledger.close()
        with self.assertRaises(LedgerError):
            with open_ledger(self.database) as handle:
                handle.execute(encode(create()))
                req, payloads = append()
                handle.execute(encode(req), payloads=payloads)
                handle.execute(encode(create(key="different-key")))
        with open_ledger(self.database) as reopened:
            self.assertEqual(reopened.read_blob("blob:quarterly-v1"), REPORT_V1)
            self.assertEqual(reopened.verify()["counts"]["idempotency_records"], 2)

    def test_argument_none_empty_mapping_and_new_zero_byte_blob_are_distinct(self):
        self.assert_error("INVALID_INPUT", "not_committed", self.ledger.execute,
                          encode(create()), payloads={})
        self.ledger.execute(encode(create()), payloads=None, package=None, descriptor=None)
        req, payloads = append("empty", b"")
        self.assert_error("INVALID_INPUT", "not_committed", self.ledger.execute, encode(req))
        with self.assertRaises(LedgerError):
            self.ledger.execute(encode(req), payloads={})
        self.assertEqual(self.counts()["versions"], 0)
        self.ledger.execute(encode(req), payloads=payloads)
        self.assertEqual(self.ledger.read_blob("blob:quarterly-empty"), b"")
        reused, _ = append("reuse-empty", b"")
        reused["body"]["version"]["content_root_ref"] = "root:quarterly-empty"
        reused["body"]["content_roots"] = reused["body"]["blobs"] = []
        self.ledger.execute(encode(reused), payloads={})
        self.assertEqual(self.counts()["versions"], 2)
        self.assertEqual(self.counts()["blobs"], 1)

    def test_explicit_branch_and_multiple_parent_merge_preserve_order(self):
        self.seed(False)
        for suffix in ("left", "right"):
            req, payloads = append(suffix, suffix.encode(), ["version:quarterly-v1"])
            self.ledger.execute(encode(req), payloads=payloads)
        parents = ["version:quarterly-right", "version:quarterly-left"]
        req, payloads = append("merged", b"caller supplied merged content", parents)
        self.ledger.execute(encode(req), payloads=payloads)
        merged = self.ledger.get_record("version", "version:quarterly-merged")
        self.assertEqual(merged["parent_version_refs"], parents)
        self.assertEqual(self.ledger.read_blob("blob:quarterly-merged"), b"caller supplied merged content")
        self.assertNotIn("current", self.ledger.get_history(ARTIFACT))
        self.assertEqual(self.counts()["versions"], 4)

    def test_cross_type_owned_id_collisions_cover_all_seven_record_kinds(self):
        self.seed()
        manifest_request, payloads = append("manifest", b"manifest bytes")
        entries = [{"entry_key": "report", "blob_ref": "blob:quarterly-manifest"}]
        canonical = json.dumps({"entries": entries}, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode()
        manifest_request["body"]["manifests"] = [{
            "manifest_ref": "manifest:quarterly", "entries": entries, "digest": digest(canonical)}]
        manifest_request["body"]["content_roots"] = [{
            "content_root_ref": "root:quarterly-manifest", "kind": "manifest", "manifest_ref": "manifest:quarterly"}]
        self.ledger.execute(encode(manifest_request), payloads=payloads)
        empty = self.fresh("empty").export_bundle()
        self.ledger.execute(encode(import_request("empty")), **import_kwargs(empty))
        before = self.counts()
        for index, occupied in enumerate([ARTIFACT, "version:quarterly-v1", "root:quarterly-v1",
                                         "blob:quarterly-v1", "manifest:quarterly",
                                         "provenance:quarterly-v2", "receipt:empty"]):
            with self.subTest(occupied=occupied):
                self.assert_error("IDENTITY_CONFLICT", "not_committed", self.ledger.execute,
                                  encode(create(occupied, f"collision-{index}")))
                self.assertEqual(self.counts(), before)
        req, payloads = append("cross-version", b"wrong owned type")
        req["body"]["version"]["version_id"] = ARTIFACT
        self.assert_error("IDENTITY_CONFLICT", "not_committed", self.ledger.execute,
                          encode(req), payloads=payloads)

    def test_corrupt_payload_leaves_metadata_query_success_but_reads_and_replay_fail(self):
        _, _, result = self.seed()
        original_history = self.ledger.get_history(ARTIFACT)
        original_operation = self.ledger.get_operation(SCOPE, "append_version", "append-v2")
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE payloads SET data=? WHERE blob_ref=?", (b"damaged", "blob:quarterly-v2"))
        self.assertEqual(self.ledger.get_history(ARTIFACT), original_history)
        self.assertEqual(self.ledger.get_operation(SCOPE, "append_version", "append-v2"), original_operation)
        self.assert_error("INTEGRITY_FAILURE", "not_applicable", self.ledger.read_blob, "blob:quarterly-v2")
        verification = self.assert_error("INTEGRITY_FAILURE", "not_applicable", self.ledger.verify)
        self.assertEqual(set(verification.details), {"failures", "truncated"})
        self.assertTrue(verification.details["failures"])
        req, payloads = append("v2", REPORT_V2, ["version:quarterly-v1"], provenance=True)
        error = self.assert_error("INTEGRITY_FAILURE", "committed", self.ledger.execute,
                                  encode(req), payloads=payloads)
        self.assertEqual(error.details["result_ref"], result["result_ref"])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT data FROM payloads WHERE blob_ref=?",
                                                 ("blob:quarterly-v2",)).fetchone()[0], b"damaged")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 3)


class ImportHistoryTests(AcceptanceCase):
    def test_import_history_subset_superset_and_other_branch_are_atomic_conflicts(self):
        self.seed(False)
        older = self.ledger.export_bundle()
        target = self.fresh()
        target.execute(encode(import_request("old")), **import_kwargs(older))
        req, payloads = append("v2", REPORT_V2, ["version:quarterly-v1"], provenance=True)
        self.ledger.execute(encode(req), payloads=payloads)
        newer = self.ledger.export_bundle()
        before = self.counts(target)
        self.assert_error("IDENTITY_CONFLICT", "not_committed", target.execute,
                          encode(import_request("super")), **import_kwargs(newer))
        self.assertEqual(self.counts(target), before)
        before = self.counts()
        self.assert_error("IDENTITY_CONFLICT", "not_committed", self.ledger.execute,
                          encode(import_request("sub")), **import_kwargs(older))
        self.assertEqual(self.counts(), before)
        branch, branch_payload = append("branch", b"different branch", ["version:quarterly-v1"])
        target.execute(encode(branch), payloads=branch_payload)
        before = self.counts(target)
        self.assert_error("IDENTITY_CONFLICT", "not_committed", target.execute,
                          encode(import_request("branch")), **import_kwargs(newer))
        self.assertEqual(self.counts(target), before)

    def test_successful_import_replays_after_target_grows_but_new_request_conflicts(self):
        self.seed()
        bundle, target = self.ledger.export_bundle(), self.fresh()
        result = target.execute(encode(import_request()), **import_kwargs(bundle))
        req, payloads = append("v3", b"new target version", ["version:quarterly-v2"])
        target.execute(encode(req), payloads=payloads)
        before = self.counts(target)
        self.assertEqual(target.execute(encode(import_request()), **import_kwargs(bundle)),
                         {**result, "replayed": True})
        self.assertEqual(self.counts(target), before)
        self.assert_error("IDENTITY_CONFLICT", "not_committed", target.execute,
                          encode(import_request("fresh")), **import_kwargs(bundle))
        self.assertEqual(self.counts(target), before)
        self.assertEqual(target.read_blob("blob:quarterly-v3"), b"new target version")

    def test_identical_complete_history_new_import_adds_only_receipt_and_success(self):
        self.seed()
        bundle, target = self.ledger.export_bundle(), self.fresh()
        target.execute(encode(import_request()), **import_kwargs(bundle))
        before = self.counts(target)
        target.execute(encode(import_request("again")), **import_kwargs(bundle))
        after = self.counts(target)
        self.assertEqual(after, {**before, "import_receipts": before["import_receipts"] + 1,
                                "idempotency_records": before["idempotency_records"] + 1})
        self.assertEqual(target.get_history(ARTIFACT), self.ledger.get_history(ARTIFACT))

    def test_failure_mid_import_leaves_no_partial_records_payloads_or_receipt(self):
        self.seed()
        bundle, target = self.ledger.export_bundle(), self.fresh()
        before = target.verify()
        execute = target._store.execute
        inserts = 0

        def fail_after_inserts(sql, parameters=()):
            nonlocal inserts
            result = execute(sql, parameters)
            if "INSERT" in sql.upper() and "RECORDS" in sql.upper():
                inserts += 1
                if inserts == 3:
                    raise OSError("injected write failure after partial import")
            return result

        with patch.object(target._store, "execute", side_effect=fail_after_inserts):
            self.assert_error("IO_ERROR", "not_committed", target.execute,
                              encode(import_request()), **import_kwargs(bundle))
        self.assertEqual(inserts, 3, "fault must actually hit partially written input")
        self.assertEqual(target.verify(), before)
        self.assertFalse(target.execute(encode(import_request()), **import_kwargs(bundle))["replayed"])

    def test_import_replay_detects_corrupt_target_without_repairing_original_success(self):
        self.seed()
        bundle, target = self.ledger.export_bundle(), self.fresh()
        result = target.execute(encode(import_request()), **import_kwargs(bundle))
        with sqlite3.connect(self.root / "destination.sqlite") as connection:
            connection.execute("UPDATE payloads SET data=? WHERE blob_ref=?", (b"broken", "blob:quarterly-v1"))
        operation = target.get_operation("acceptance:copy", "import_bundle", "import-copy")
        self.assertEqual(operation["result"]["import_receipt_id"], result["result_ref"])
        error = self.assert_error("INTEGRITY_FAILURE", "committed", target.execute,
                                  encode(import_request()), **import_kwargs(bundle))
        self.assertEqual(error.details["result_ref"], result["result_ref"])
        with sqlite3.connect(self.root / "destination.sqlite") as connection:
            self.assertEqual(connection.execute("SELECT data FROM payloads WHERE blob_ref=?",
                                                 ("blob:quarterly-v1",)).fetchone()[0], b"broken")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 4)


class TransactionFailureTests(AcceptanceCase):
    def test_commit_response_preparation_failure_preserves_known_committed_identity(self):
        with patch.object(self.ledger, "_success", side_effect=RuntimeError("response preparation failed")):
            error = self.assert_error("INTERNAL_ERROR", "committed", self.ledger.execute, encode(create()))
        operation = self.ledger.get_operation(SCOPE, "create_artifact", "create-quarterly")
        expected = {"result_ref": ARTIFACT, "idempotency_scope_ref": SCOPE,
                    "operation_kind": "create_artifact", "idempotency_key": "create-quarterly",
                    "recorded_at": operation["idempotency_record"]["recorded_at"]}
        for key, value in expected.items():
            self.assertEqual(error.details[key], value)
        self.assertTrue(self.ledger.execute(encode(create()))["replayed"])
        self.assertEqual(self.counts()["idempotency_records"], 1)

    def test_commit_call_failure_is_unknown_even_if_storage_actually_committed(self):
        actual_commit = self.ledger._store.commit

        def commit_then_fail():
            actual_commit()
            raise OSError("commit result was lost")

        with patch.object(self.ledger._store, "commit", side_effect=commit_then_fail):
            self.assert_error("DURABILITY_UNKNOWN", "unknown", self.ledger.execute, encode(create()))
        self.ledger.close()
        with open_ledger(self.database) as reopened:
            self.assertTrue(reopened.execute(encode(create()))["replayed"])
            self.assertEqual(reopened.verify()["counts"]["idempotency_records"], 1)

    def test_commit_call_failure_with_confirmed_rollback_is_not_committed(self):
        with patch.object(self.ledger._store, "commit", side_effect=OSError("commit could not be observed")):
            self.assert_error("IO_ERROR", "not_committed", self.ledger.execute, encode(create()))
        self.ledger.close()
        with open_ledger(self.database) as reopened:
            self.assertEqual(reopened.verify()["counts"]["artifacts"], 0)
            self.assertFalse(reopened.execute(encode(create()))["replayed"])

    def test_rollback_failure_is_unknown_then_reopen_proves_no_partial_write(self):
        execute = self.ledger._store.execute
        hit = []

        def fail_after_record(sql, parameters=()):
            result = execute(sql, parameters)
            if "INSERT" in sql.upper() and "RECORDS" in sql.upper():
                hit.append(True)
                raise OSError("injected after record write")
            return result

        with patch.object(self.ledger._store, "execute", side_effect=fail_after_record), \
             patch.object(self.ledger._store, "rollback", side_effect=OSError("rollback unavailable")):
            self.assert_error("DURABILITY_UNKNOWN", "unknown", self.ledger.execute, encode(create()))
        self.assertTrue(hit)
        self.ledger.close()
        with open_ledger(self.database) as reopened:
            self.assertEqual(reopened.verify()["counts"]["artifacts"], 0)
            self.assertFalse(reopened.execute(encode(create()))["replayed"])

    def test_real_begin_lock_timeout_returns_busy_without_changes(self):
        blocker = sqlite3.connect(self.database, isolation_level=None)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        try:
            self.assert_error("BUSY", "not_committed", self.ledger.execute, encode(create()))
        finally:
            blocker.rollback()
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 4.5)
        self.assertLess(elapsed, 12)
        self.assertEqual(self.counts()["artifacts"], 0)
        self.assertFalse(self.ledger.execute(encode(create()))["replayed"])

    def test_real_commit_lock_timeout_rolls_back_before_reporting_busy(self):
        blocker = sqlite3.connect(self.database, isolation_level=None)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN")
        blocker.execute("SELECT COUNT(*) FROM records").fetchone()
        commit, reached = self.ledger._store.commit, []

        def observe_commit():
            reached.append(True)
            commit()

        started = time.monotonic()
        try:
            with patch.object(self.ledger._store, "commit", side_effect=observe_commit):
                self.assert_error("BUSY", "not_committed", self.ledger.execute, encode(create()))
        finally:
            blocker.rollback()
        self.assertEqual(reached, [True], "writer must reach actual COMMIT lock conflict")
        self.assertGreaterEqual(time.monotonic() - started, 4.5)
        self.assertFalse(self.ledger._store.connection.in_transaction)
        self.assertEqual(self.counts()["artifacts"], 0)
        self.assertFalse(self.ledger.execute(encode(create()))["replayed"])

    def test_commit_busy_with_failed_rollback_reports_unknown(self):
        blocker = sqlite3.connect(self.database, isolation_level=None)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN")
        blocker.execute("SELECT COUNT(*) FROM records").fetchone()
        try:
            with patch.object(self.ledger._store, "rollback", side_effect=OSError("rollback failed")):
                self.assert_error("DURABILITY_UNKNOWN", "unknown", self.ledger.execute, encode(create()))
        finally:
            blocker.rollback()
            self.ledger.close()
        with open_ledger(self.database) as reopened:
            self.assertEqual(reopened.verify()["counts"]["artifacts"], 0)

    def test_real_process_kill_during_write_before_commit_and_after_commit(self):
        self.ledger.execute(encode(create()))
        self.ledger.close()
        for mode in ("pause_during_payload", "pause_before_commit", "pause_after_commit"):
            with self.subTest(boundary=mode):
                req, payloads = append(mode, mode.encode())
                marker = self.root / f"{mode}.marker"
                process = self.worker(mode, req, payloads, marker=marker)
                deadline = time.monotonic() + 10
                while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not marker.exists():
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                    self.fail(f"fault marker never reached: {stdout!r} {stderr!r}")
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
                self.assertLess(process.returncode, 0)
                self.assertEqual(stdout, b"", "no success response may precede tested boundary")
                with open_ledger(self.database) as reopened:
                    durable = mode == "pause_after_commit"
                    if durable:
                        self.assertEqual(reopened.read_blob(f"blob:quarterly-{mode}"), mode.encode())
                    else:
                        self.assert_error("NOT_FOUND", "not_applicable", reopened.get_operation,
                                          SCOPE, "append_version", f"append-{mode}")
                    retried = reopened.execute(encode(req), payloads=payloads)
                    self.assertEqual(retried["replayed"], durable)
                    reopened.verify()
        with open_ledger(self.database) as reopened:
            self.assertEqual(reopened.verify()["counts"]["versions"], 3)
            self.assertEqual(reopened.verify()["counts"]["idempotency_records"], 4)

    def test_concurrent_identical_requests_converge_and_conflicting_requests_have_one_winner(self):
        self.ledger.close()
        barrier = self.root / "same-start"
        processes = [self.worker(req=create(), barrier=barrier) for _ in range(4)]
        barrier.write_text("go", encoding="ascii")
        outcomes = [self.response(process) for process in processes]
        self.assertEqual(sum(item.get("replayed") is False for item in outcomes), 1)
        self.assertEqual(sum(item.get("replayed") is True for item in outcomes), 3)
        self.assertEqual(len({item["recorded_at"] for item in outcomes}), 1)
        with open_ledger(self.database) as reopened:
            self.assertEqual(reopened.verify()["counts"]["idempotency_records"], 1)
        barrier = self.root / "conflict-start"
        first = create("artifact:competing", "competing")
        second = deepcopy(first)
        second["body"]["artifact"]["type_ref"]["type_version"] = "2"
        processes = [self.worker(req=value, barrier=barrier) for value in (first, second)]
        barrier.write_text("go", encoding="ascii")
        outcomes = [self.response(process) for process in processes]
        self.assertEqual(sum(item["status"] == "COMMITTED" for item in outcomes), 1)
        self.assertEqual(sum(item.get("code") == "IDEMPOTENCY_CONFLICT" for item in outcomes), 1)
        with open_ledger(self.database) as reopened:
            self.assertEqual(reopened.verify()["counts"]["artifacts"], 2)
            self.assertEqual(reopened.verify()["counts"]["idempotency_records"], 2)

    def test_concurrent_distinct_keys_cannot_claim_the_same_owned_identity(self):
        self.ledger.close()
        barrier = self.root / "identity-start"
        processes = [self.worker(req=create(key=key), barrier=barrier) for key in ("first", "second")]
        barrier.write_text("go", encoding="ascii")
        outcomes = [self.response(process) for process in processes]
        self.assertEqual(sum(item["status"] == "COMMITTED" for item in outcomes), 1)
        self.assertEqual(sum(item.get("code") == "IDENTITY_CONFLICT" for item in outcomes), 1)
        with open_ledger(self.database) as reopened:
            self.assertEqual(reopened.verify()["counts"]["artifacts"], 1)
            self.assertEqual(reopened.verify()["counts"]["idempotency_records"], 1)


if __name__ == "__main__":
    unittest.main()
