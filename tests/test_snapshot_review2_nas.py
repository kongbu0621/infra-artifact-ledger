"""Local ownership regressions for the NAS harness; no real NAS operations."""
import errno
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


_TOOL = Path(__file__).resolve().parents[1] / "tools" / "acceptance" / "a2_nas_exercise.py"
_SPEC = importlib.util.spec_from_file_location("a2_nas_review2", _TOOL)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


def is_open(fd):
    try:
        os.fstat(fd)
        return True
    except OSError as error:
        if error.errno == errno.EBADF:
            return False
        raise


class NasDirectoryOwnershipReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)

    def test_walk_close_error_does_not_close_reused_fd_or_leak_child(self):
        real_open, real_close = os.open, os.close
        opened, closed, reused = [], [], []
        primary = OSError(errno.EIO, "synthetic close error after release")

        def track_open(*args, **kwargs):
            result = real_open(*args, **kwargs)
            opened.append(result)
            return result

        def release_then_fail(fd):
            real_close(fd)
            closed.append(fd)
            if len(closed) == 1:
                reused.append(real_open("/dev/null", os.O_RDONLY))
                self.assertEqual(reused[0], fd)
                raise primary

        try:
            with patch.object(tool.os, "open", side_effect=track_open), \
                    patch.object(tool.os, "close", side_effect=release_then_fail):
                with self.assertRaises(OSError) as caught:
                    tool.open_directory(self.parent)
            self.assertIs(caught.exception, primary)
            self.assertEqual(len(opened), 2)
            self.assertEqual(closed, opened)
            self.assertTrue(is_open(reused[0]), "A descriptor now owned elsewhere was closed twice")
            self.assertFalse(is_open(opened[1]), "The opened child descriptor leaked")
        finally:
            for fd in set(opened + reused):
                if is_open(fd):
                    real_close(fd)

    def test_walk_open_error_survives_cleanup_close_error(self):
        real_open, real_close = os.open, os.close
        primary = OSError(errno.EACCES, "synthetic component open error")
        opened = []

        def open_once(*args, **kwargs):
            if opened:
                raise primary
            result = real_open(*args, **kwargs)
            opened.append(result)
            return result

        def fail_close(fd):
            real_close(fd)
            raise OSError(errno.EIO, "synthetic cleanup close error")

        with patch.object(tool.os, "open", side_effect=open_once), \
                patch.object(tool.os, "close", side_effect=fail_close):
            with self.assertRaises(OSError) as caught:
                tool.open_directory(self.parent)
        self.assertIs(caught.exception, primary)
        self.assertFalse(is_open(opened[0]))

    def test_missing_component_before_dotdot_is_not_normalized_away(self):
        real = self.parent / "real"
        real.mkdir()
        returned = None
        try:
            with self.assertRaises(FileNotFoundError):
                returned = tool.open_directory(self.parent / "missing" / ".." / "real")
        finally:
            if returned is not None:
                os.close(returned)

    def test_symlink_before_dotdot_is_not_normalized_away(self):
        real = self.parent / "real"
        real.mkdir()
        (self.parent / "alias").symlink_to(real, target_is_directory=True)
        returned = None
        try:
            with self.assertRaises(OSError):
                returned = tool.open_directory(self.parent / "alias" / ".." / "real")
        finally:
            if returned is not None:
                os.close(returned)

    def test_parent_close_error_during_initialization_closes_run_fd(self):
        real_open_directory, real_close = tool.open_directory, os.close
        parent_fds = []
        primary = OSError(errno.EIO, "synthetic parent close error")
        instance = tool.OwnedRun.__new__(tool.OwnedRun)
        original_fd = []

        def remember_parent(*args, **kwargs):
            fd = real_open_directory(*args, **kwargs)
            parent_fds.append(fd)
            return fd

        def fail_parent(fd):
            real_close(fd)
            if parent_fds and fd == parent_fds[0]:
                original_fd.append(instance.fd)
                raise primary

        try:
            with patch.object(tool, "open_directory", side_effect=remember_parent), \
                    patch.object(tool.os, "close", side_effect=fail_parent):
                with self.assertRaises(OSError) as caught:
                    instance.__init__(self.parent)
            self.assertIs(caught.exception, primary)
            self.assertIsNone(instance.fd)
            self.assertFalse(is_open(original_fd[0]))
        finally:
            instance.close()

    def test_initialization_error_survives_parent_close_error(self):
        real_close = os.close
        primary = OSError(errno.EIO, "synthetic marker write error")
        instance = tool.OwnedRun.__new__(tool.OwnedRun)
        real_write = tool.OwnedRun.write_new
        fail_closes = False
        closed = []

        def fail_write(owner, name, raw):
            nonlocal fail_closes
            if name == "OWNED.json":
                fail_closes = True
                raise primary
            return real_write(owner, name, raw)

        def fail_close(fd):
            real_close(fd)
            if fail_closes:
                closed.append(fd)
                raise OSError(errno.EIO, "synthetic initialization cleanup error")

        with patch.object(tool.OwnedRun, "write_new", fail_write), \
                patch.object(tool.os, "close", side_effect=fail_close):
            with self.assertRaises(OSError) as caught:
                instance.__init__(self.parent)
        self.assertIs(caught.exception, primary)
        self.assertEqual(len(closed), 2)
        self.assertEqual(len(set(closed)), 2)
        self.assertTrue(all(not is_open(fd) for fd in closed))
        self.assertIsNone(instance.fd)

    def make_dotdot_run(self):
        prefix, real = self.parent / "prefix", self.parent / "real"
        prefix.mkdir()
        real.mkdir()
        run = tool.OwnedRun(prefix / ".." / "real")
        self.addCleanup(run.close)
        return run, prefix, real

    def test_dotdot_prefix_replaced_by_symlink_blocks_deletion(self):
        run, prefix, real = self.make_dotdot_run()
        source = real / run.run_id / "disposable" / "source.sqlite"
        source.write_bytes(b"synthetic")
        run.freeze_disposable({"source.sqlite"})
        prefix.rmdir()
        prefix.symlink_to(real, target_is_directory=True)
        with self.assertRaises((OSError, tool.ExerciseError)):
            run.remove_disposable()
        self.assertEqual(source.read_bytes(), b"synthetic")

    def test_dotdot_prefix_replaced_by_another_directory_blocks_deletion(self):
        run, prefix, real = self.make_dotdot_run()
        source = real / run.run_id / "disposable" / "source.sqlite"
        source.write_bytes(b"synthetic")
        run.freeze_disposable({"source.sqlite"})
        prefix.rename(self.parent / "original-prefix")
        prefix.mkdir()
        with self.assertRaises(tool.ExerciseError):
            run.remove_disposable()
        self.assertEqual(source.read_bytes(), b"synthetic")

    def test_unchanged_dotdot_path_keeps_normal_owned_cleanup(self):
        run, _, real = self.make_dotdot_run()
        source = real / run.run_id / "disposable" / "source.sqlite"
        source.write_bytes(b"synthetic")
        run.freeze_disposable({"source.sqlite"})
        run.remove_disposable()
        self.assertFalse(source.parent.exists())
        self.assertTrue((real / run.run_id / "OWNED.json").exists())

    def test_check_close_error_is_not_swallowed_by_callers_except_block(self):
        run = tool.OwnedRun(self.parent)
        self.addCleanup(run.close)
        real_open_directory, real_close = tool.open_directory, os.close
        returned, injected = [], []
        outer = ValueError("already handled caller error")
        close_error = OSError(errno.EIO, "synthetic checked directory close error")

        def remember_current(*args, **kwargs):
            fd = real_open_directory(*args, **kwargs)
            returned.append(fd)
            return fd

        def fail_current(fd):
            real_close(fd)
            if returned and fd == returned[0] and not injected:
                injected.append(fd)
                raise close_error

        try:
            raise outer
        except ValueError:
            with patch.object(tool, "open_directory", side_effect=remember_current), \
                    patch.object(tool.os, "close", side_effect=fail_current):
                with self.assertRaises(OSError) as caught:
                    run.check()
        self.assertIs(caught.exception, close_error)
        self.assertFalse(is_open(injected[0]))
        self.assertFalse(hasattr(outer, "__notes__"))

    def test_inventory_close_error_is_not_swallowed_by_callers_except_block(self):
        run = tool.OwnedRun(self.parent)
        self.addCleanup(run.close)
        (run.path / "disposable" / "source.sqlite").write_bytes(b"synthetic")
        real_close, closed = os.close, []
        close_error = OSError(errno.EIO, "synthetic inventory close error")
        outer = ValueError("already handled caller error")

        def fail_first(fd):
            real_close(fd)
            closed.append(fd)
            if len(closed) == 1:
                raise close_error

        close_patch = patch.object(tool.os, "close", side_effect=fail_first)

        def inspect_success(*_):
            close_patch.start()
            return {}

        try:
            try:
                raise outer
            except ValueError:
                with self.assertRaises(OSError) as caught:
                    run._inspect_disposable({"source.sqlite"}, inspect_success)
        finally:
            close_patch.stop()
        self.assertIs(caught.exception, close_error)
        self.assertEqual(len(closed), 2)
        self.assertTrue(all(not is_open(fd) for fd in closed))
        self.assertFalse(hasattr(outer, "__notes__"))

    def test_exercise_close_error_is_not_swallowed_by_callers_except_block(self):
        # Only cleanup/report orchestration: local classification and child
        # isolation are mocked. This is not real NAS or installed-wheel proof.
        from infra_artifact_ledger import recovery, snapshot_storage
        archive, local = self.parent / "mock-archive", self.parent / "mock-local"
        archive.mkdir()
        local.mkdir()
        config = self.parent / "mock-storage.json"
        config.write_bytes(tool.encode({"format": "infra-artifact-ledger-storage/v1",
            "profile": "mounted-posix-v1", "storage_ref": "synthetic", "mount_point": str(self.parent),
            "mount_root": "/", "mount_source": "synthetic:/archive", "fs_type": "nfs4",
            "archive_root": str(archive)}))
        args = SimpleNamespace(source_commit="a" * 40, wheel=self.parent / "unused.whl",
                               storage_config=config, local_parent=local)
        real_close = tool.OwnedRun.close
        close_error = OSError(errno.EIO, "synthetic final run close error")
        outer = ValueError("already handled caller error")

        def close_then_fail(run):
            real_close(run)
            raise close_error

        def inline(argv, cwd, records=None):
            options = {argv[i][2:].replace("-", "_"): argv[i + 1] for i in range(1, len(argv), 2)}
            options["storage_config"] = json.loads(Path(options["storage_config"]).read_text())
            return getattr(recovery, argv[0])(**options)

        try:
            raise outer
        except ValueError:
            with patch.object(tool, "installed_identity", return_value={"mode": "MOCK"}), \
                    patch.object(snapshot_storage, "_classify"), \
                    patch.object(tool, "child_cli", side_effect=inline), \
                    patch.object(tool.OwnedRun, "close", close_then_fail):
                with self.assertRaises(OSError) as caught:
                    tool.exercise(args)
        self.assertIs(caught.exception, close_error)
        self.assertTrue(caught.exception.acceptance_evidence_saved)
        report = json.loads((local / caught.exception.acceptance_run_id / "report.json").read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(hasattr(outer, "__notes__"))


if __name__ == "__main__":
    unittest.main()
