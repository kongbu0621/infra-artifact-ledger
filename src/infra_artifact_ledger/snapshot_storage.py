"""Linux fd-bound storage for the A2 snapshot profile.

The caller owns each newly created directory.  A successful system call records
filesystem acknowledgement, not a guarantee about remote server power loss.
Read operations never run an implicit capability probe or change their input.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Iterator

from .snapshot_common import Budget, RecoveryError, path

MAX_DATABASE_BYTES = 1_073_741_824
COPY_CHUNK = 1_048_576
LOCAL_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs"})
NETWORK_FILESYSTEMS = frozenset({"nfs", "nfs4", "cifs"})
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_UNSUPPORTED_ERRNOS = {errno.ENOSYS, errno.EOPNOTSUPP, errno.EXDEV, errno.EINVAL}


def _error(code: str, message: str, stage: str = "validate") -> RecoveryError:
    return RecoveryError(code, message, stage=stage)


def _io_error(exc: OSError, stage: str = "validate") -> RecoveryError:
    if exc.errno in _UNSUPPORTED_ERRNOS:
        return _error("UNSUPPORTED_STORAGE", "Required filesystem operation is unavailable", stage)
    if exc.errno == errno.ENOENT:
        return _error("NOT_FOUND", "Required filesystem object is missing", stage)
    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        return _error("UNSUPPORTED_STORAGE", "Directory path must not contain symbolic links", stage)
    return _error("IO_ERROR", "Filesystem operation failed", stage)


def _close_preserving_error(close, primary: BaseException | None = None) -> None:
    """Close once; keep an operation failure ahead of a secondary close error."""
    try:
        close()
    except OSError:
        if primary is None:
            raise
        primary.add_note("A filesystem close also failed; the earlier failure is retained.")


def _name(value: str) -> str:
    if type(value) is not str or not value or value in {".", ".."} or "/" in value or "\x00" in value:
        raise _error("INVALID_INPUT", "Expected a single filesystem entry name")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise _error("INVALID_INPUT", "Filesystem entry name must be UTF-8") from exc
    return value


def _absolute(value) -> Path:
    return Path(path(value))


def _unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


@dataclass(frozen=True)
class Mount:
    mount_id: int
    parent_id: int
    device: str
    root: str
    point: str
    fs_type: str
    source: str


def parse_mountinfo(raw: str) -> tuple[Mount, ...]:
    """Parse the kernel mountinfo format, including escaped path fields."""
    result = []
    try:
        # Kernel records/fields use ASCII LF/space. Unicode whitespace may
        # legitimately occur inside an unescaped UTF-8 mount path or source.
        for line in raw.split("\n"):
            if not line:
                continue
            before, after = line.split(" - ", 1)
            fields, tail = before.split(" "), after.split(" ")
            if len(fields) < 6 or len(tail) < 3:
                raise ValueError("invalid field count")
            mount = Mount(int(fields[0]), int(fields[1]), fields[2],
                          _unescape(fields[3]), _unescape(fields[4]),
                          tail[0], _unescape(tail[1]))
            if mount.mount_id < 1 or not mount.point.startswith("/") or not mount.root.startswith("/"):
                raise ValueError("invalid mount identity")
            result.append(mount)
        if not result or len({m.mount_id for m in result}) != len(result):
            raise ValueError("missing or duplicate mount identity")
    except (ValueError, TypeError) as exc:
        raise _error("UNSUPPORTED_STORAGE", "Linux mount metadata is invalid") from exc
    return tuple(result)


def _mounts() -> tuple[Mount, ...]:
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="strict", newline="") as source:
            return parse_mountinfo(source.read())
    except (OSError, UnicodeError) as exc:
        raise _error("UNSUPPORTED_STORAGE", "Linux mount metadata is unavailable") from exc


def _fd_mount_id(fd: int) -> int:
    try:
        with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as source:
            entries = [line.split(":", 1)[1].strip() for line in source if line.startswith("mnt_id:")]
        if len(entries) != 1:
            raise ValueError("missing mount ID")
        return int(entries[0])
    except (OSError, ValueError, UnicodeError) as exc:
        raise _error("UNSUPPORTED_STORAGE", "Directory mount binding is unavailable") from exc


def _under(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _mount_for(target: Path, fd_mount_id: int) -> Mount:
    candidates = [m for m in _mounts() if _under(target, Path(m.point))]
    if not candidates:
        raise _error("UNSUPPORTED_STORAGE", "Path has no matching mount")
    depth = max(len(Path(m.point).parts) for m in candidates)
    candidates = [m for m in candidates if len(Path(m.point).parts) == depth]
    matches = [m for m in candidates if m.mount_id == fd_mount_id]
    if len(matches) != 1:
        raise _error("UNSUPPORTED_STORAGE", "Path and open descriptor have different mounts")
    return matches[0]


def _classify(target: Path, mount: Mount, config: dict | None) -> None:
    if config is None:
        if mount.fs_type not in LOCAL_FILESYSTEMS:
            raise _error("UNSUPPORTED_STORAGE", "Path is not on a supported local filesystem")
        return
    # The public entry point already validates and owns this fixed configuration.
    expected = (config["mount_point"], config["mount_root"], config["fs_type"], config["mount_source"])
    actual = (mount.point, mount.root, mount.fs_type, mount.source)
    if mount.fs_type not in NETWORK_FILESYSTEMS or actual != expected:
        raise _error("UNSUPPORTED_STORAGE", "Mounted storage does not match its fixed configuration")
    # Configured paths have the same component semantics as host paths: do not
    # erase an intervening symlink/missing component with lexical '..' cleanup.
    archive_path = _absolute(config["archive_root"])
    candidate = Path(os.path.normpath(archive_path))
    # Lexical checks may reject an impossible containment relationship, but
    # cannot accept one without the full original component walk below.
    if not _under(candidate, Path(config["mount_point"])):
        raise _error("UNSUPPORTED_STORAGE", "Archive root is outside the configured mount")
    if not _under(target, candidate):
        raise _error("UNSUPPORTED_STORAGE", "Path is outside the configured archive root")
    archive_fd = _walk_directory(archive_path)
    completed = False
    try:
        archive_path = candidate
        if _fd_mount_id(archive_fd) != mount.mount_id or _mount_for(archive_path, mount.mount_id) != mount:
            raise _error("UNSUPPORTED_STORAGE", "Archive root and target have different mounts")
        if not _under(target, archive_path):
            raise _error("UNSUPPORTED_STORAGE", "Path is outside the configured archive root")
        if not _under(archive_path, Path(config["mount_point"])):
            raise _error("UNSUPPORTED_STORAGE", "Archive root is outside the configured mount")
        completed = True
    finally:
        _close_preserving_error(lambda: os.close(archive_fd),
                                None if completed else sys.exception())


def _walk_directory(target: Path) -> int:
    """Open every component relative to its parent; never resolve a symlink."""
    descriptor = None
    try:
        descriptor = os.open("/", _DIR_FLAGS)
        for component in target.parts[1:]:
            if component in {"", "."}:
                raise _error("INVALID_INPUT", "Directory path contains an invalid component")
            # A real '..' open preserves filesystem traversal semantics. Every
            # preceding component has already been opened with O_NOFOLLOW.
            child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            previous, descriptor = descriptor, child
            # close() can release the descriptor and still report an error.
            # Transfer ownership first so cleanup never closes the old number
            # twice and never loses the newly opened child descriptor.
            os.close(previous)
        result, descriptor = descriptor, None
        return result
    except OSError as exc:
        raise _io_error(exc) from exc
    finally:
        if descriptor is not None:
            # Success transferred ownership and set descriptor to None.
            _close_preserving_error(lambda: os.close(descriptor), sys.exception())


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _version(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class Directory:
    """An open directory bound to its path, mount, and optional parent."""

    def __init__(self, fd: int, target: Path, budget: Budget, mount: Mount,
                 config: dict | None, parent: Directory | None = None,
                 *, walk_path: Path | None = None):
        self.fd, self.path, self.budget = fd, target, budget
        self._walk_path = target if walk_path is None else walk_path
        self.mount, self.storage_config, self.parent = mount, config, parent
        self.identity = _identity(os.fstat(fd))
        self._closed = False

    def __enter__(self) -> Directory:
        return self

    def __exit__(self, error_type, error, traceback):
        _close_preserving_error(self.close, error)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self.fd)

    def check(self, stage: str = "validate") -> None:
        self.budget.check(stage)
        if self._closed:
            raise _error("IO_ERROR", "Directory descriptor is closed", stage)
        if self.parent is not None:
            self.parent.check(stage)
        current = None
        completed = False
        try:
            if _identity(os.fstat(self.fd)) != self.identity:
                raise _error("IO_ERROR", "Directory descriptor identity changed", stage)
            current = _walk_directory(self._walk_path)
            if _identity(os.fstat(current)) != self.identity:
                raise _error("IO_ERROR", "Directory path binding changed", stage)
            if _fd_mount_id(current) != self.mount.mount_id or _fd_mount_id(self.fd) != self.mount.mount_id:
                raise _error("UNSUPPORTED_STORAGE", "Directory mount binding changed", stage)
            mount = _mount_for(self.path, self.mount.mount_id)
            if mount != self.mount:
                raise _error("UNSUPPORTED_STORAGE", "Mounted endpoint changed", stage)
            _classify(self.path, mount, self.storage_config)
            completed = True
        except RecoveryError as exc:
            # Helpers also serve initial validation and default to that stage.
            # This check belongs to its caller's current operation stage.
            exc.stage = stage
            raise
        except OSError as exc:
            raise _io_error(exc, stage) from exc
        finally:
            if current is not None:
                _close_preserving_error(lambda: os.close(current),
                                        None if completed else sys.exception())
        self.budget.check(stage)

    def mkdir(self, name: str) -> Directory:
        name = _name(name)
        self.check()
        fd = None
        try:
            os.mkdir(name, mode=0o700, dir_fd=self.fd)
            fd = os.open(name, _DIR_FLAGS, dir_fd=self.fd)
            child_path = self.path / name
            mount = _mount_for(child_path, _fd_mount_id(fd))
            if mount != self.mount:
                raise _error("UNSUPPORTED_STORAGE", "New directory crossed a mount boundary")
            child = Directory(fd, child_path, self.budget, mount, self.storage_config, self)
            fd = None
            try:
                child.check()
            except BaseException as exc:
                _close_preserving_error(child.close, exc)
                raise
            return child
        except FileExistsError as exc:
            raise RecoveryError("TARGET_EXISTS", "Target already exists", publication_state="not_applicable") from exc
        except OSError as exc:
            raise _io_error(exc) from exc
        finally:
            if fd is not None:
                _close_preserving_error(lambda: os.close(fd), sys.exception())

    def open_file(self, name: str, *, single_link: bool = False) -> int:
        name = _name(name)
        self.check()
        fd = None
        try:
            before = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise _error("INTEGRITY_FAILURE", "Snapshot member is not a regular file")
            fd = os.open(name, _FILE_FLAGS, dir_fd=self.fd)
            after = os.fstat(fd)
            if not stat.S_ISREG(after.st_mode) or _identity(before) != _identity(after):
                raise _error("INTEGRITY_FAILURE", "Snapshot member binding changed")
            if single_link and after.st_nlink != 1:
                raise _error("INTEGRITY_FAILURE", "Database file must have one link")
            if _fd_mount_id(fd) != self.mount.mount_id:
                raise _error("UNSUPPORTED_STORAGE", "Snapshot file crossed a mount boundary")
            self.check()
            result, fd = fd, None
            return result
        except OSError as exc:
            raise _io_error(exc) from exc
        finally:
            if fd is not None:
                _close_preserving_error(lambda: os.close(fd), sys.exception())

    def members(self, *, limit: int | None = None) -> set[str]:
        self.check()
        try:
            result = set()
            with os.scandir(self.fd) as entries:
                for entry in entries:
                    self.budget.check("validate")
                    result.add(entry.name)
                    if limit is not None and len(result) > limit:
                        raise _error("INTEGRITY_FAILURE", "Directory contains extra members")
        except OSError as exc:
            raise _io_error(exc) from exc
        self.check()
        return result

    def fsync(self) -> None:
        self.check("sync")
        try:
            os.fsync(self.fd)
        except OSError as exc:
            raise _io_error(exc, "sync") from exc
        self.check("sync")


def open_directory(value, *, budget: Budget, storage_config: dict | None = None) -> Directory:
    target = _absolute(value)
    budget.check("validate")
    fd = _walk_directory(target)
    original_walk = target
    try:
        # Only the successful full walk permits lexical canonicalization for
        # mount lookup, later binding checks and cross-directory comparisons.
        target = Path(os.path.normpath(target))
        mount = _mount_for(target, _fd_mount_id(fd))
        _classify(target, mount, storage_config)
        directory = Directory(fd, target, budget, mount, storage_config, walk_path=original_walk)
        directory.check()
        return directory
    except BaseException as exc:
        _close_preserving_error(lambda: os.close(fd), exc)
        raise


def _remove_owned_contents(directory: Directory) -> None:
    """Clean only an owned temporary directory, with no link traversal."""
    directory.check()
    for name in directory.members():
        directory.budget.check("validate")
        info = os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            fd = os.open(name, _DIR_FLAGS, dir_fd=directory.fd)
            try:
                if _identity(os.fstat(fd)) != _identity(info):
                    raise _error("IO_ERROR", "Temporary directory binding changed before cleanup")
                if _fd_mount_id(fd) != directory.mount.mount_id:
                    raise _error("UNSUPPORTED_STORAGE", "Temporary cleanup crossed a mount boundary")
                with Directory(fd, directory.path / name, directory.budget, directory.mount,
                               directory.storage_config, directory) as child:
                    fd = None
                    _remove_owned_contents(child)
                directory.check()
                if _identity(os.stat(name, dir_fd=directory.fd, follow_symlinks=False)) != _identity(info):
                    raise _error("IO_ERROR", "Temporary directory binding changed")
                os.rmdir(name, dir_fd=directory.fd)
            finally:
                if fd is not None:
                    _close_preserving_error(lambda: os.close(fd), sys.exception())
        else:
            os.unlink(name, dir_fd=directory.fd)


@contextmanager
def temporary_directory(parent: Directory, budget: Budget) -> Iterator[Directory]:
    budget.check("validate")
    # The exclusive mkdir is the ownership proof; an existing name is never used.
    temporary = parent.mkdir(".snapshot-tmp-" + secrets.token_hex(16))
    name, identity = temporary.path.name, temporary.identity
    primary = None
    try:
        yield temporary
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_completed = False
        try:
            _remove_owned_contents(temporary)
            parent.check()
            if _identity(os.stat(name, dir_fd=parent.fd, follow_symlinks=False)) != identity:
                raise _error("IO_ERROR", "Temporary directory binding changed")
            os.rmdir(name, dir_fd=parent.fd)
            cleanup_completed = True
        except (OSError, RecoveryError) as exc:
            if primary is None:
                if isinstance(exc, OSError):
                    raise _io_error(exc) from exc
                raise
        finally:
            # An enclosing caller's handled exception is not our failure.
            # Consult interpreter exception state only when this cleanup did
            # fail; otherwise use solely the explicit exception from the body.
            closing_primary = primary if cleanup_completed or primary is not None else sys.exception()
            _close_preserving_error(temporary.close, closing_primary)


def _exclusive_file(directory: Directory, name: str) -> int:
    directory.check("copy")
    try:
        return os.open(_name(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                       0o600, dir_fd=directory.fd)
    except FileExistsError as exc:
        raise RecoveryError("TARGET_EXISTS", "Target file already exists", stage="copy",
                            publication_state="not_applicable") from exc
    except OSError as exc:
        raise _io_error(exc, "copy") from exc


def _write_all(fd: int, value: bytes, budget: Budget) -> None:
    view = memoryview(value)
    while view:
        budget.check("copy")
        written = os.write(fd, view)
        if written <= 0:
            raise _error("IO_ERROR", "Filesystem returned an incomplete write", "copy")
        view = view[written:]


def _sync_file(fd: int, budget: Budget) -> None:
    budget.check("sync")
    try:
        os.fsync(fd)
    except OSError as exc:
        raise _io_error(exc, "sync") from exc


def copy_file(src_fd: int, dest: Directory, name: str, budget: Budget, *,
              expected_size: int | None = None, expected_hash: str | None = None) -> dict:
    budget.check("copy")
    descriptor = None
    try:
        before = os.fstat(src_fd)
        if not stat.S_ISREG(before.st_mode):
            raise _error("INTEGRITY_FAILURE", "Copy source is not a regular file", "copy")
        if before.st_size > MAX_DATABASE_BYTES:
            raise _error("RESOURCE_LIMIT", "Database file exceeds the snapshot budget", "copy")
        if expected_size is not None and before.st_size != expected_size:
            raise _error("INTEGRITY_FAILURE", "Copy source length does not match", "copy")
        descriptor = _exclusive_file(dest, name)
        os.lseek(src_fd, 0, os.SEEK_SET)
        digest, total = hashlib.sha256(), 0
        while True:
            budget.check("copy")
            dest.check("copy")
            block = os.read(src_fd, COPY_CHUNK)
            if not block:
                break
            total += len(block)
            if total > MAX_DATABASE_BYTES:
                raise _error("RESOURCE_LIMIT", "Database file exceeds the snapshot budget", "copy")
            if total > before.st_size or (expected_size is not None and total > expected_size):
                raise _error("INTEGRITY_FAILURE", "Copy source length changed", "copy")
            digest.update(block)
            _write_all(descriptor, block, budget)
        after = os.fstat(src_fd)
        if _version(before) != _version(after) or total != before.st_size:
            raise _error("INTEGRITY_FAILURE", "Copy source changed while reading", "copy")
        result = {"byte_length": total, "sha256": digest.hexdigest()}
        if expected_hash is not None and result["sha256"] != expected_hash:
            raise _error("INTEGRITY_FAILURE", "Copy source digest does not match", "copy")
        _sync_file(descriptor, budget)
        published_stat = os.fstat(descriptor)
        finished, descriptor = descriptor, None
        os.close(finished)
        dest.check("copy")
        current = os.stat(name, dir_fd=dest.fd, follow_symlinks=False)
        if _identity(current) != _identity(published_stat) or current.st_size != total:
            raise _error("INTEGRITY_FAILURE", "Copy target binding changed", "copy")
        return result
    except OSError as exc:
        raise _io_error(exc, "copy") from exc
    finally:
        if descriptor is not None:
            _close_preserving_error(lambda: os.close(descriptor), sys.exception())


def write_file(dest: Directory, name: str, raw: bytes, budget: Budget) -> None:
    budget.check("copy")
    fd = _exclusive_file(dest, name)
    completed = False
    try:
        _write_all(fd, raw, budget)
        _sync_file(fd, budget)
        dest.check("copy")
        completed = True
    except OSError as exc:
        raise _io_error(exc, "copy") from exc
    finally:
        _close_preserving_error(lambda: os.close(fd), None if completed else sys.exception())


def read_file(directory: Directory, name: str, limit: int, budget: Budget) -> bytes:
    fd = directory.open_file(name)
    completed = False
    try:
        before = os.fstat(fd)
        if before.st_size > limit:
            raise _error("RESOURCE_LIMIT", "Snapshot member exceeds its byte budget", "verify")
        chunks, total = [], 0
        while True:
            budget.check("verify")
            block = os.read(fd, min(COPY_CHUNK, limit + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > limit:
                raise _error("RESOURCE_LIMIT", "Snapshot member exceeds its byte budget", "verify")
        if _version(before) != _version(os.fstat(fd)) or total != before.st_size:
            raise _error("INTEGRITY_FAILURE", "Snapshot member changed while reading", "verify")
        directory.check("verify")
        if _identity(os.stat(name, dir_fd=directory.fd, follow_symlinks=False)) != _identity(before):
            raise _error("INTEGRITY_FAILURE", "Snapshot member path binding changed", "verify")
        result = b"".join(chunks)
        completed = True
        return result
    except OSError as exc:
        raise _io_error(exc, "verify") from exc
    finally:
        _close_preserving_error(lambda: os.close(fd), None if completed else sys.exception())


def preflight(parent: Directory, budget: Budget) -> dict:
    """Explicit synthetic probe; never called by a read-only operation.

    This proves the tested calls on this binding only.  Deployment acceptance
    must additionally exercise competing publishers and isolated mount loss.
    """
    with temporary_directory(parent, budget) as probe:
        raw = b"infra-artifact-ledger mounted-posix-v1 synthetic probe\n"
        write_file(probe, "original", raw, budget)
        try:
            write_file(probe, "original", b"must not overwrite", budget)
        except RecoveryError as exc:
            if exc.code != "TARGET_EXISTS":
                raise
        else:
            raise _error("UNSUPPORTED_STORAGE", "Exclusive file creation is unavailable")
        try:
            os.link("original", "linked", src_dir_fd=probe.fd, dst_dir_fd=probe.fd, follow_symlinks=False)
            try:
                os.link("original", "linked", src_dir_fd=probe.fd, dst_dir_fd=probe.fd, follow_symlinks=False)
            except FileExistsError:
                pass
            else:
                raise _error("UNSUPPORTED_STORAGE", "Hard links do not preserve existing targets")
        except OSError as exc:
            raise _io_error(exc, "publish") from exc
        probe.fsync()
        parent.fsync()
        if read_file(probe, "original", len(raw), budget) != raw or read_file(probe, "linked", len(raw), budget) != raw:
            raise _error("INTEGRITY_FAILURE", "Storage probe bytes did not survive reopening")
        if _identity(os.stat("original", dir_fd=probe.fd)) != _identity(os.stat("linked", dir_fd=probe.fd)):
            raise _error("UNSUPPORTED_STORAGE", "Storage probe hard link identity differs")
        probe.check()
    parent.fsync()
    return {"profile": "mounted-posix-v1", "mount_id": parent.mount.mount_id,
            "fs_type": parent.mount.fs_type}
