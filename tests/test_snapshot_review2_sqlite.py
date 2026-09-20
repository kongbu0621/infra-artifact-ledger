"""Round-two source isolation regressions with real SQLite.

Only filesystem classification is mocked: LOGIC_ONLY, not storage acceptance.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import mock_open, patch

from infra_artifact_ledger import initialize, recovery
from infra_artifact_ledger import snapshot_sqlite as ss
from infra_artifact_ledger import snapshot_storage as storage
from infra_artifact_ledger.snapshot_common import Budget


class SnapshotSourceSidecarReviewTests(unittest.TestCase):
    def test_delete_header_with_existing_wal_is_rejected_before_source_sqlite_open(self):
        with tempfile.TemporaryDirectory(prefix="a2-review2-wal-") as temporary:
            root = Path(temporary)
            source = root / "source.sqlite"
            output = root / "output"
            output.mkdir()
            with initialize(source):
                pass
            # A leftover/malformed WAL is enough for SQLite's readonly open to
            # create a new SHM file, even though the database header is DELETE.
            self.assertEqual(source.read_bytes()[18:20], b"\x01\x01")
            Path(str(source) + "-wal").write_bytes(b"x" * 4096)
            before = {entry.name: entry.read_bytes()
                      for entry in root.iterdir() if entry.is_file()}

            with patch.object(storage, "_classify", return_value=None):
                with self.assertRaises(recovery.RecoveryError) as caught:
                    recovery.create(db=source, output_root=output,
                                    snapshot_id="2" * 32, source_commit="3" * 40)

            self.assertEqual(caught.exception.code, "UNSUPPORTED_FORMAT")
            self.assertEqual(caught.exception.publication_state, "not_published")
            after = {entry.name: entry.read_bytes()
                     for entry in root.iterdir() if entry.is_file()}
            self.assertEqual(set(before), set(after))
            self.assertEqual(before, after)
            self.assertEqual(list(output.iterdir()), [])

    def test_every_wal_or_shm_entry_is_refused_before_scratch_or_sqlite(self):
        for suffix in ("-wal", "-shm"):
            for kind in ("empty", "nonempty", "broken_symlink", "directory"):
                with self.subTest(suffix=suffix, kind=kind), \
                        tempfile.TemporaryDirectory(prefix="a2-review2-sidecar-") as temporary:
                    root = Path(temporary)
                    source, output = root / "source.sqlite", root / "output"
                    output.mkdir()
                    with initialize(source):
                        pass
                    before = source.read_bytes()
                    source_stat, parent_stat = source.stat(), root.stat()
                    sidecar = Path(str(source) + suffix)
                    if kind == "broken_symlink":
                        sidecar.symlink_to(root / "absent")
                    elif kind == "directory":
                        sidecar.mkdir()
                    else:
                        sidecar.write_bytes(b"" if kind == "empty" else b"x" * 4096)
                    sidecar_stat = sidecar.lstat()
                    with patch.object(storage, "_classify", return_value=None), \
                            patch.object(storage, "temporary_directory", side_effect=AssertionError("scratch created")), \
                            patch.object(ss, "_connect_readonly", side_effect=AssertionError("source SQLite opened")):
                        with self.assertRaises(recovery.RecoveryError) as caught:
                            recovery.create(db=source, output_root=output,
                                            snapshot_id="2" * 32, source_commit="3" * 40)
                    self.assertEqual((caught.exception.code, caught.exception.stage,
                                      caught.exception.publication_state),
                                     ("UNSUPPORTED_FORMAT", "validate", "not_published"))
                    self.assertEqual(before, source.read_bytes())
                    self.assertEqual(source_stat, source.stat())
                    self.assertEqual(parent_stat.st_ino, root.stat().st_ino)
                    self.assertEqual(sidecar_stat, sidecar.lstat())
                    self.assertEqual(list(output.iterdir()), [])

    def test_wal_observed_after_header_inspection_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="a2-review2-late-wal-") as temporary:
            source = Path(temporary) / "source.sqlite"
            with initialize(source):
                pass
            actual = ss._read_header

            def inspect(path, budget):
                result = actual(path, budget)
                Path(str(path) + "-wal").write_bytes(b"x" * 4096)
                return result

            with patch.object(ss, "_read_header", side_effect=inspect):
                with self.assertRaises(recovery.RecoveryError) as caught:
                    ss.preflight_source(source, Budget())
            self.assertEqual(caught.exception.code, "UNSUPPORTED_FORMAT")

    def test_wal_observed_after_completed_preflight_is_refused_before_open(self):
        with tempfile.TemporaryDirectory(prefix="a2-review2-late-wal-") as temporary:
            root = Path(temporary)
            source, target = root / "source.sqlite", root / "copy.sqlite"
            with initialize(source):
                pass
            budget = Budget()
            binding = ss.preflight_source(source, budget)
            Path(str(source) + "-wal").write_bytes(b"x" * 4096)
            with patch.object(ss, "_connect_readonly", side_effect=AssertionError("source SQLite opened")):
                with self.assertRaises(recovery.RecoveryError) as caught:
                    ss.snapshot_database(source, target, budget, source_binding=binding)
            self.assertEqual(caught.exception.code, "UNSUPPORTED_FORMAT")
            self.assertFalse(target.exists())

    def test_unicode_whitespace_mount_binding_change_is_detected(self):
        # A mountinfo parser model, not a real mount/profile acceptance test.
        for whitespace in ("\u00a0", "\u2028"):
            with self.subTest(whitespace=repr(whitespace)), \
                    tempfile.TemporaryDirectory(prefix="a2-review2-mount-") as temporary:
                directory = Path(temporary) / ("source" + whitespace + "mount")
                directory.mkdir()
                source = directory / "source.sqlite"
                with initialize(source):
                    pass

                def mounts(identity):
                    return ("901 1 0:1 / / rw - ext4 root rw\n"
                            f"{identity} 901 0:2 / {directory} rw - ext4 child rw\n")

                with patch.object(ss, "open", mock_open(read_data=mounts(902)), create=True):
                    binding = ss._source_binding(source, Budget())
                self.assertEqual(len(binding[1]), 2, "source mount record was lost")
                with patch.object(ss, "open", mock_open(read_data=mounts(903)), create=True):
                    with self.assertRaises(recovery.RecoveryError) as caught:
                        ss._assert_source_binding(source, binding, Budget())
                self.assertEqual(caught.exception.code, "IO_ERROR")


if __name__ == "__main__":
    unittest.main()
