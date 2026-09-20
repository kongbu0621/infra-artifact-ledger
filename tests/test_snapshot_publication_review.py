"""LOGIC_ONLY evidence for final read-back and a wholly lost create response.

Public recovery APIs execute their real SQLite, copy, sync and publication
paths. Only storage classification is mocked to admit isolated test directories;
the results do not certify the enclosing filesystem, a NAS or power loss.
The adoption helper below demonstrates an external caller's recovery procedure,
not a new public API or proof that an earlier create completed successfully.
"""
import errno
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize, recovery
from infra_artifact_ledger import snapshot_format as fmt
from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget

try:
    from .acceptance_helpers import append, create, encode
except ImportError:
    from acceptance_helpers import append, create, encode


SOURCE_COMMIT = "a" * 40
SNAPSHOT_ID = "b" * 32
_LOST_RESPONSE_CHILD = r"""
import os, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from infra_artifact_ledger import recovery, snapshot_storage as storage
def stop_before_marker(*args, **kwargs):
    os._exit(74)
with patch.object(storage, '_classify', return_value=None):
    if sys.argv[6] == 'incomplete':
        with patch.object(recovery.os, 'link', stop_before_marker):
            recovery.create(db=sys.argv[2], output_root=sys.argv[3],
                            snapshot_id=sys.argv[4], source_commit=sys.argv[5])
        raise AssertionError('marker publication was not reached')
    recovery.create(db=sys.argv[2], output_root=sys.argv[3],
                    snapshot_id=sys.argv[4], source_commit=sys.argv[5])
# Create has really completed, but its entire returned envelope is lost. No
# stdout, side-channel manifest digest or serialized API result is emitted.
os._exit(0)
"""


def generation_image(directory):
    result = {}
    for entry in sorted(directory.iterdir()):
        info = entry.lstat()
        result[entry.name] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            hashlib.sha256(entry.read_bytes()).hexdigest() if entry.is_file() else None,
        )
    return result


class SnapshotPublicationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a2-publication-review-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output, self.scratch = (self.root / name for name in ("snapshots", "scratch"))
        for directory in (self.output, self.scratch):
            directory.mkdir()
        self.output_identity = (self.output.stat().st_dev, self.output.stat().st_ino)
        self.generation = self.output / SNAPSHOT_ID
        classification = patch.object(storage, "_classify", return_value=None)
        classification.start()
        self.addCleanup(classification.stop)
        self.source = self.root / "source.sqlite"
        with initialize(self.source) as ledger:
            ledger.execute(encode(create()))
            request, payloads = append("readback", b"synthetic preserved content\x00\xff")
            ledger.execute(encode(request), payloads=payloads)
            self.summary = ledger.verify()
        self.source_bytes = self.source.read_bytes()
        self.saved_reference = None

    def assert_error(self, code, state, callback):
        with self.assertRaises(recovery.RecoveryError) as caught:
            callback()
        self.assertEqual((caught.exception.code, caught.exception.publication_state), (code, state))
        return caught.exception

    def final_readback_failure(self, member, mode):
        actual_link, actual_read = os.link, os.read
        published = False
        fault_reached = False
        expected_manifest = None

        def link_then_fault(*args, **kwargs):
            nonlocal published, fault_reached, expected_manifest
            actual_link(*args, **kwargs)
            published = True
            expected_manifest = hashlib.sha256((self.generation / "manifest.json").read_bytes()).hexdigest()
            if mode == "hash":
                if member == "manifest.json":
                    # Still valid JSON, but these bytes no longer match the
                    # independently captured marker/publisher expectation.
                    with (self.generation / member).open("ab") as stream:
                        stream.write(b" ")
                else:
                    with (self.generation / member).open("r+b") as stream:
                        stream.seek(-1, os.SEEK_END)
                        original = stream.read(1)
                        stream.seek(-1, os.SEEK_END)
                        stream.write(bytes([original[0] ^ 1]))
                fault_reached = True

        def fail_final_member_read(fd, length):
            nonlocal fault_reached
            if published and mode == "io" and Path(os.readlink(f"/proc/self/fd/{fd}")) == self.generation / member:
                fault_reached = True
                raise OSError(errno.EIO, "injected final member read failure")
            return actual_read(fd, length)

        with patch.object(recovery.os, "link", side_effect=link_then_fault), \
                patch.object(recovery.os, "read", side_effect=fail_final_member_read):
            self.assert_error("PUBLICATION_UNKNOWN", "unknown", lambda: recovery.create(
                db=self.source, output_root=self.output, snapshot_id=SNAPSHOT_ID, source_commit=SOURCE_COMMIT))
        self.assertTrue(published)
        self.assertTrue(fault_reached)
        self.assertIsNotNone(expected_manifest)
        self.assertEqual({entry.name for entry in self.generation.iterdir()},
                         {"ledger.sqlite", "manifest.json", "COMMITTED.json"})
        before_check = generation_image(self.generation)
        kwargs = dict(snapshot=self.generation, expected_manifest_sha256=expected_manifest,
                      scratch_parent=self.scratch)
        if mode == "io":
            checked = recovery.verify(**kwargs)
            self.assertEqual((checked["status"], checked["publication_state"]), ("OK", "not_applicable"))
            self.assertEqual(checked["data"]["summary"], self.summary)
        else:
            self.assert_error("INTEGRITY_FAILURE", "not_applicable", lambda: recovery.verify(**kwargs))
        self.assertEqual(generation_image(self.generation), before_check)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def test_database_final_read_io_failure_after_marker_preserves_unknown_generation(self):
        self.final_readback_failure("ledger.sqlite", "io")

    def test_manifest_final_read_io_failure_after_marker_preserves_unknown_generation(self):
        self.final_readback_failure("manifest.json", "io")

    def test_database_final_hash_failure_after_marker_preserves_corrupt_generation(self):
        self.final_readback_failure("ledger.sqlite", "hash")

    def test_manifest_final_hash_failure_after_marker_preserves_corrupt_generation(self):
        self.final_readback_failure("manifest.json", "hash")

    def lose_child_response(self, mode="complete"):
        # The caller owns this newly created output root, verified target
        # absence before this one child starts, and has no other publisher.
        self.assertFalse(os.path.lexists(self.generation))
        package_root = Path(recovery.__file__).resolve().parents[1]
        run = subprocess.run(
            [sys.executable, "-I", "-c", _LOST_RESPONSE_CHILD, str(package_root), str(self.source),
             str(self.output), SNAPSHOT_ID, SOURCE_COMMIT, mode],
            capture_output=True, timeout=30, cwd=self.root)
        self.assertEqual((run.returncode, run.stdout, run.stderr),
                         (74 if mode == "incomplete" else 0, b"", b""))
        self.assertIsNone(self.saved_reference)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def inspect_owned_lost_create(self, expected_source_commit, *, ownership_confirmed=True):
        # Caller procedure only: do not infer ownership or a past success from
        # the child's exit code, a directory's existence or a digest read now.
        if not ownership_confirmed or self.output_identity != (
                self.output.stat().st_dev, self.output.stat().st_ino):
            raise ValueError("This generation is not confirmed as this caller's exclusive operation.")
        with storage.open_directory(self.generation, budget=Budget()) as directory:
            raw = storage.read_file(directory, "manifest.json", 64 * 1024, Budget())
        manifest = fmt.validate_manifest(fmt.parse_json(raw, 64 * 1024), SNAPSHOT_ID)
        if manifest["producer"]["source_commit"] != expected_source_commit:
            raise ValueError("Create source_commit does not match the original invocation.")
        candidate_hash = hashlib.sha256(raw).hexdigest()
        checked = recovery.verify(snapshot=self.generation, expected_manifest_sha256=candidate_hash,
                                   scratch_parent=self.scratch)
        # Persist only after ownership, ID, source and complete self-consistency
        # checks. This digest did not exist independently before the operation.
        self.saved_reference = {
            "snapshot_id": SNAPSHOT_ID,
            "manifest_sha256": candidate_hash,
            "database_sha256": checked["data"]["database_sha256"],
            "evidence": "current_owned_generation_self_consistency",
            "matched_previously_saved_digest": False,
            "historical_create_success_confirmed": False,
        }
        return checked

    def test_complete_create_with_wholly_lost_response_can_only_establish_current_self_consistency(self):
        self.lose_child_response()
        before = generation_image(self.generation)
        checked = self.inspect_owned_lost_create(SOURCE_COMMIT)
        self.assertEqual((checked["status"], checked["publication_state"]), ("OK", "not_applicable"))
        self.assertEqual(checked["data"]["summary"], self.summary)
        self.assertEqual(self.saved_reference["evidence"], "current_owned_generation_self_consistency")
        self.assertFalse(self.saved_reference["matched_previously_saved_digest"])
        self.assertFalse(self.saved_reference["historical_create_success_confirmed"])
        self.assertEqual(generation_image(self.generation), before)

    def test_incomplete_lost_create_is_not_adopted_and_is_not_retried(self):
        self.lose_child_response("incomplete")
        self.assertFalse((self.generation / "COMMITTED.json").exists())
        before = generation_image(self.generation)
        self.assert_error("INCOMPLETE_SNAPSHOT", "not_applicable",
                          lambda: self.inspect_owned_lost_create(SOURCE_COMMIT))
        self.assertIsNone(self.saved_reference)
        self.assertEqual(generation_image(self.generation), before)

    def test_lost_create_source_commit_mismatch_is_not_adopted(self):
        self.lose_child_response()
        before = generation_image(self.generation)
        with self.assertRaisesRegex(ValueError, "source_commit"):
            self.inspect_owned_lost_create("e" * 40)
        self.assertIsNone(self.saved_reference)
        self.assertEqual(generation_image(self.generation), before)

    def test_unconfirmed_generation_ownership_is_not_adopted(self):
        self.lose_child_response()
        before = generation_image(self.generation)
        with self.assertRaisesRegex(ValueError, "exclusive operation"):
            self.inspect_owned_lost_create(SOURCE_COMMIT, ownership_confirmed=False)
        self.assertIsNone(self.saved_reference)
        self.assertEqual(generation_image(self.generation), before)

    def test_lost_create_manifest_over_64kib_is_not_adopted(self):
        self.lose_child_response()
        manifest = self.generation / "manifest.json"
        with manifest.open("ab") as stream:
            stream.write(b" " * (64 * 1024 + 1 - manifest.stat().st_size))
        before = generation_image(self.generation)
        with self.assertRaises(recovery.RecoveryError) as caught:
            self.inspect_owned_lost_create(SOURCE_COMMIT)
        self.assertEqual(caught.exception.code, "RESOURCE_LIMIT")
        self.assertIsNone(self.saved_reference)
        self.assertEqual(generation_image(self.generation), before)


if __name__ == "__main__":
    unittest.main()
