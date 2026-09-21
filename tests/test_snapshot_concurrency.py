"""LOGIC_ONLY: competing recovery calls in two independent interpreters.

The only filesystem capability substitution admits the sandbox classification;
mount/fd identities, SQLite, files, exclusive mkdir, hard links and sync remain
real. A test-only rendezvous holds both callers immediately before their final
exclusive-directory syscall, after each caller has validated/prepared its data.
This is concurrency evidence, not deployed local/NAS filesystem certification.
"""

import hashlib
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

from infra_artifact_ledger import initialize, open as open_ledger
from infra_artifact_ledger import recovery
from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import EXIT_CODES

try:
    from .acceptance_helpers import ARTIFACT, append, create, encode
except ImportError:
    from acceptance_helpers import ARTIFACT, append, create, encode


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(directory):
    return {entry.name: entry.read_bytes() for entry in directory.iterdir()}


def table_rows(database):
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        return {name: sorted(connection.execute("SELECT * FROM " + name).fetchall(), key=repr)
                for name in ("ledger_format", "records", "payloads", "operations", "refs")}
    finally:
        connection.close()


def worker(operation, serialized, control_path, token, target_name):
    """Standalone process entry used only by this test module."""
    arguments = json.loads(serialized)
    control = Path(control_path)
    original_mkdir = storage.Directory.mkdir
    reached = False

    def rendezvous(directory, name):
        nonlocal reached
        if name == target_name:
            if reached:
                raise AssertionError("Final target mkdir was attempted more than once")
            reached = True
            # Separate files provide proof that both independent interpreters
            # reached the operation's exclusive-ownership boundary.
            preparing = control / (token + ".preparing")
            with preparing.open("x", encoding="ascii") as stream:
                stream.write(str(os.getpid()))
            preparing.rename(control / (token + ".ready"))
            deadline = time.monotonic() + 30
            while not (control / "release").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Parent did not release both competing calls")
                time.sleep(0.005)
        return original_mkdir(directory, name)

    with patch.object(storage, "_classify", return_value=None), \
            patch.object(storage.Directory, "mkdir", rendezvous):
        try:
            result = getattr(recovery, operation)(**arguments)
            exit_code = 0
        except recovery.RecoveryError as error:
            result = error.to_envelope(operation)
            exit_code = EXIT_CODES[error.code]
    if not reached:
        raise AssertionError("Call did not reach the concurrency rendezvous")
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    return exit_code


class SnapshotConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a2-concurrency-logic-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "snapshots"
        self.scratch = self.root / "scratch"
        self.control = self.root / "control"
        for directory in (self.output, self.scratch, self.control):
            directory.mkdir()
        classification = patch.object(storage, "_classify", return_value=None)
        classification.start()
        self.addCleanup(classification.stop)
        self.database = self.make_source("source", b"independent source content\n")
        self.old_snapshot = recovery.create(db=self.database, output_root=self.output,
                                            snapshot_id="9" * 32, source_commit="9" * 40)
        self.old_path = Path(self.old_snapshot["data"]["snapshot_path"])
        self.old_bytes = files(self.old_path)

    def make_source(self, name, payload):
        database = self.root / (name + ".sqlite")
        with initialize(database) as ledger:
            ledger.execute(encode(create()))
            request, payloads = append(payload=payload)
            ledger.execute(encode(request), payloads=payloads)
        return database

    @staticmethod
    def finish(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

    def compete(self, operation, arguments, target_name):
        # Follow the package being tested: checkout src or installed wheel's
        # site-packages. An installed test must never switch back to checkout.
        active_package_root = str(Path(recovery.__file__).resolve().parents[1])
        environment = os.environ.copy()
        environment["PYTHONPATH"] = active_package_root
        children = []
        for index, kwargs in enumerate(arguments):
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--worker", operation,
                 json.dumps(kwargs), str(self.control), str(index), target_name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
            )
            self.addCleanup(self.finish, child)
            children.append(child)
        deadline = time.monotonic() + 30
        ready = [self.control / f"{index}.ready" for index in range(2)]
        while not all(path.exists() for path in ready):
            for child in children:
                if child.poll() is not None:
                    stdout, stderr = child.communicate(timeout=5)
                    self.fail(f"Competing process exited before rendezvous: {stdout!r} {stderr!r}")
            if time.monotonic() >= deadline:
                self.fail("Both independent callers did not reach the exclusive mkdir boundary")
            time.sleep(0.01)
        pids = {int(path.read_text(encoding="ascii")) for path in ready}
        self.assertEqual(len(pids), 2)
        self.assertNotIn(os.getpid(), pids)
        self.assertTrue(all(child.poll() is None for child in children))
        (self.control / "release").write_text("both-ready", encoding="ascii")
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            self.assertEqual(stderr, b"")
            self.assertEqual(stdout.count(b"\n"), 1)
            result = json.loads(stdout)
            self.assertEqual(child.returncode, 0 if result["status"] == "OK" else 4)
            results.append(result)
        successful = [index for index, result in enumerate(results) if result["status"] == "OK"]
        self.assertEqual(len(successful), 1, results)
        winner = successful[0]
        self.assertEqual(results[winner]["publication_state"], "published")
        loser = results[1 - winner]
        self.assertEqual(loser["error"]["code"], "TARGET_EXISTS")
        self.assertEqual(loser["publication_state"], "not_applicable")
        return winner, results

    def assert_snapshot(self, path, data):
        self.assertEqual({entry.name for entry in path.iterdir()},
                         {"ledger.sqlite", "manifest.json", "COMMITTED.json"})
        for entry in path.iterdir():
            self.assertTrue(entry.is_file())
            self.assertFalse(entry.is_symlink())
        self.assertEqual((path / "ledger.sqlite").stat().st_nlink, 1)
        self.assertEqual(digest(path / "ledger.sqlite"), data["database_sha256"])
        self.assertEqual(digest(path / "manifest.json"), data["manifest_sha256"])
        manifest = json.loads((path / "manifest.json").read_bytes())
        marker = json.loads((path / "COMMITTED.json").read_bytes())
        self.assertEqual(manifest["database"]["sha256"], data["database_sha256"])
        self.assertEqual(manifest["database"]["byte_length"], (path / "ledger.sqlite").stat().st_size)
        self.assertEqual(marker["manifest_sha256"], data["manifest_sha256"])
        self.assertEqual(marker["snapshot_id"], path.name)
        expected_marker = json.dumps({"format": "infra-artifact-ledger-snapshot-commit/v1",
                                      "snapshot_id": path.name,
                                      "manifest_sha256": data["manifest_sha256"]},
                                     sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(digest(path / "COMMITTED.json"), hashlib.sha256(expected_marker).hexdigest())
        verified = recovery.verify(snapshot=path, expected_manifest_sha256=data["manifest_sha256"],
                                   scratch_parent=self.scratch)
        self.assertEqual(verified["data"]["summary"], data["summary"])

    def test_two_independent_create_calls_same_id_have_one_complete_winner(self):
        identity = "a" * 32
        before_source = self.database.read_bytes()
        source_rows = table_rows(self.database)
        arguments = [dict(db=str(self.database), output_root=str(self.output),
                          snapshot_id=identity, source_commit=commit * 40) for commit in ("1", "2")]
        winner, results = self.compete("create", arguments, identity)
        generation = self.output / identity
        data = results[winner]["data"]
        self.assert_snapshot(generation, data)
        manifest = json.loads((generation / "manifest.json").read_bytes())
        self.assertEqual(manifest["producer"]["source_commit"], arguments[winner]["source_commit"])
        self.assertEqual(table_rows(generation / "ledger.sqlite"), source_rows)
        self.assertEqual(self.database.read_bytes(), before_source)
        self.assertEqual(files(self.old_path), self.old_bytes)
        self.assertEqual({entry.name for entry in self.output.iterdir()}, {identity, self.old_path.name})
        self.assertEqual(list(self.scratch.iterdir()), [])
        # A later loser cannot rewrite the already completed winning generation.
        before_winner = files(generation)
        with self.assertRaises(recovery.RecoveryError) as caught:
            recovery.create(**arguments[1 - winner])
        self.assertEqual(caught.exception.code, "TARGET_EXISTS")
        self.assertEqual(caught.exception.publication_state, "not_applicable")
        self.assertEqual(files(generation), before_winner)

    def test_two_independent_restore_calls_same_target_keep_exact_winner_state(self):
        alternate = self.make_source("alternate", b"different contents from the second snapshot\n")
        second = recovery.create(db=alternate, output_root=self.output,
                                 snapshot_id="8" * 32, source_commit="8" * 40)
        inputs = (self.old_snapshot, second)
        input_paths = [Path(result["data"]["snapshot_path"]) for result in inputs]
        input_bytes = [files(path) for path in input_paths]
        source_bytes = [self.database.read_bytes(), alternate.read_bytes()]
        rows = [table_rows(path / "ledger.sqlite") for path in input_paths]
        target = self.root / "restored"
        unrelated = self.root / "old-restore"
        unrelated.mkdir()
        (unrelated / "ledger.sqlite").write_bytes(b"existing unrelated object")
        unrelated_before = files(unrelated)
        arguments = [dict(snapshot=str(path), target_dir=str(target), scratch_parent=str(self.scratch),
                          expected_manifest_sha256=result["data"]["manifest_sha256"])
                     for path, result in zip(input_paths, inputs)]
        winner, results = self.compete("restore", arguments, target.name)
        winner_data = inputs[winner]["data"]
        self.assertEqual({entry.name for entry in target.iterdir()}, {"ledger.sqlite"})
        self.assertEqual((target / "ledger.sqlite").stat().st_nlink, 1)
        self.assertEqual((target / "ledger.sqlite").read_bytes(), input_bytes[winner]["ledger.sqlite"])
        self.assertEqual(digest(target / "ledger.sqlite"), winner_data["database_sha256"])
        self.assertEqual(results[winner]["data"]["database_sha256"], winner_data["database_sha256"])
        self.assertEqual(table_rows(target / "ledger.sqlite"), rows[winner])
        checked = recovery.check_restore(target_dir=target,
                                         expected_database_sha256=winner_data["database_sha256"],
                                         scratch_parent=self.scratch)
        self.assertEqual(checked["data"]["summary"], winner_data["summary"])
        with open_ledger(target / "ledger.sqlite") as ledger:
            self.assertEqual(ledger.get_history(ARTIFACT)["versions"][0]["version_id"], "version:quarterly-v1")
            self.assertEqual(ledger.read_blob("blob:quarterly-v1"),
                             b"independent source content\n" if winner == 0
                             else b"different contents from the second snapshot\n")
        for path, before, result in zip(input_paths, input_bytes, inputs):
            self.assertEqual(files(path), before)
            self.assert_snapshot(path, result["data"])
        self.assertEqual([self.database.read_bytes(), alternate.read_bytes()], source_bytes)
        self.assertEqual(files(unrelated), unrelated_before)
        self.assertEqual(list(self.scratch.iterdir()), [])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        raise SystemExit(worker(*sys.argv[2:]))
    unittest.main()
