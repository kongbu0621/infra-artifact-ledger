"""Consistent local snapshots, mounted storage publication and fresh restore.

Public calls are synchronous and keyword-only. A successful read attests the
current bytes and ledger semantics; it cannot attest a past publisher's sync.
"""
from contextlib import contextmanager
import errno
import hashlib
import os
import stat

from .snapshot_common import PROTOCOL, RecoveryError, operation_budget, path
from . import snapshot_format as fmt
from . import snapshot_storage as storage
from .snapshot_sqlite import preflight_source, snapshot_database, validate_database

__all__ = ["create", "publish", "verify", "restore", "check_restore", "RecoveryError"]

_MEMBERS = {"ledger.sqlite", "manifest.json", "COMMITTED.json"}
_SIDECARS = {"ledger.sqlite-journal", "ledger.sqlite-wal", "ledger.sqlite-shm"}
_LOCAL_LINK_NO_EFFECT = {
    errno.EEXIST, errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENOTDIR,
    errno.EXDEV, errno.ENOSPC, errno.EROFS, errno.EMLINK, errno.ENAMETOOLONG,
    errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL,
}


class _Operation:
    def __init__(self, operation, budget):
        self.operation, self.budget = operation, budget
        self.stage = "validate"
        self.read_only = operation in {"verify", "check_restore"}
        self.attempted = False

    def checkpoint(self, stage):
        self.stage = stage
        self.budget.check(stage)

    def link(self, source, source_name, destination, destination_name, *, remote=False):
        self.checkpoint("publish")
        source.check("publish")
        destination.check("publish")
        # After this point even an exception can mean the remote link exists.
        self.attempted = True
        try:
            os.link(source_name, destination_name, src_dir_fd=source.fd,
                    dst_dir_fd=destination.fd, follow_symlinks=False)
        except OSError as error:
            # Local definite errors are distinguished from NFS retry ambiguity.
            if not remote and error.errno in _LOCAL_LINK_NO_EFFECT:
                self.attempted = False
                if error.errno == errno.EEXIST:
                    raise RecoveryError("TARGET_EXISTS", "Publication target already exists.",
                                        "publish", "not_applicable") from error
                if error.errno in {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL}:
                    raise RecoveryError("UNSUPPORTED_STORAGE", "Required hard-link publication is unavailable.",
                                        "publish") from error
            raise


def _execute(operation, callback):
    with operation_budget() as budget:
        context = _Operation(operation, budget)
        completed = False
        try:
            data = callback(context)
            # A returned callback has completed publication, synchronization,
            # final validation and owned cleanup. Reporting cannot undo that
            # observed result or turn it into an ambiguous publication.
            completed = True
            context.checkpoint("report")
            envelope = {"protocol": PROTOCOL, "status": "OK", "operation": operation,
                        "publication_state": "not_applicable" if context.read_only else "published",
                        "data": data}
            fmt.encode(envelope, fmt.MAX_RESPONSE - 1, stage="report")
            return envelope
        except Exception as error:
            if context.attempted and not completed:
                raise RecoveryError("PUBLICATION_UNKNOWN",
                                    "Publication was attempted; preserve the original target and verify it.",
                                    error.stage if isinstance(error, RecoveryError) else context.stage, "unknown") from error
            if isinstance(error, RecoveryError):
                if context.read_only:
                    error.publication_state = "not_applicable"
                elif completed:
                    error.publication_state = "published"
                raise
            code = "RESOURCE_LIMIT" if isinstance(error, MemoryError) else "IO_ERROR"
            if isinstance(error, FileNotFoundError):
                code = "NOT_FOUND"
            raise RecoveryError(code, "Recovery operation could not complete.", context.stage,
                                "not_applicable" if context.read_only else
                                "published" if completed else "not_published") from error


def _entry_exists(directory, name):
    try:
        os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _scratch_outside_input(scratch_path, input_path):
    if scratch_path == input_path or input_path in scratch_path.parents:
        raise RecoveryError("INVALID_INPUT", "Scratch directory must be outside the read-only input.")


def _reject_plain_overlap(destination, source):
    # A lexical early check is sufficient only without parent traversal.
    # After no-follow opening, all paths are checked again canonically.
    if ".." not in destination.parts and ".." not in source.parts:
        _scratch_outside_input(destination, source)


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _owned_file(fd):
    """Close once, preserving this operation's primary error if there is one."""
    primary = None
    try:
        yield fd
    except BaseException as error:
        primary = error
        raise
    finally:
        storage._close_preserving_error(lambda: os.close(fd), primary)


def _database_info(directory, budget):
    directory.check("verify")
    with _owned_file(directory.open_file("ledger.sqlite", single_link=True)) as fd:
        before = os.fstat(fd)
        if not 0 < before.st_size <= fmt.MAX_DATABASE:
            raise RecoveryError("RESOURCE_LIMIT", "Database file exceeds the snapshot budget.", "verify")
        digest, size = hashlib.sha256(), 0
        while True:
            budget.check("verify")
            chunk = os.read(fd, min(fmt.COPY_CHUNK, fmt.MAX_DATABASE + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > fmt.MAX_DATABASE:
                raise RecoveryError("RESOURCE_LIMIT", "Database file exceeds the snapshot budget.", "verify")
            digest.update(chunk)
        current = os.stat("ledger.sqlite", dir_fd=directory.fd, follow_symlinks=False)
        if size != before.st_size or _stamp(before) != _stamp(os.fstat(fd)) or _stamp(before) != _stamp(current):
            raise RecoveryError("INTEGRITY_FAILURE", "Database changed while being read.", "verify")
        directory.check("verify")
        return {"byte_length": size, "sha256": digest.hexdigest()}


def _manifest(directory, expected, budget):
    directory.check("verify")
    members = directory.members(limit=3)
    if not _MEMBERS <= members:
        raise RecoveryError("INCOMPLETE_SNAPSHOT", "Snapshot is missing a required member.", "verify")
    if members != _MEMBERS:
        raise RecoveryError("INTEGRITY_FAILURE", "Snapshot has unexpected members.", "verify")
    raw = storage.read_file(directory, "manifest.json", fmt.MAX_MANIFEST, budget)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RecoveryError("INTEGRITY_FAILURE", "Manifest does not match the expected digest.", "verify")
    manifest = fmt.validate_manifest(fmt.parse_json(raw, fmt.MAX_MANIFEST, stage="verify"),
                                     directory.path.name, stage="verify")
    marker_raw = storage.read_file(directory, "COMMITTED.json", fmt.MAX_MARKER, budget)
    marker = fmt.validate_marker(fmt.parse_json(marker_raw, fmt.MAX_MARKER, stage="verify"),
                                directory.path.name, stage="verify")
    if marker["manifest_sha256"] != expected:
        raise RecoveryError("INTEGRITY_FAILURE", "Completion marker does not bind this manifest.", "verify")
    return manifest, raw, marker_raw


@contextmanager
def _validated_snapshot(snapshot, expected, scratch, config, context, *, destination_path=None):
    """Hold source handles while validating the one private copied database."""
    context.checkpoint("verify")
    with storage.open_directory(snapshot, budget=context.budget, storage_config=config) as source:
        if config is not None and source.path.parent != path(os.path.abspath(config["archive_root"])):
            raise RecoveryError("INVALID_INPUT", "Snapshot must be a direct generation in archive_root.")
        _scratch_outside_input(scratch.path, source.path)
        if destination_path is not None:
            _scratch_outside_input(destination_path, source.path)
        if source.identity == scratch.identity:
            raise RecoveryError("INVALID_INPUT", "Scratch directory aliases the read-only snapshot.")
        manifest, raw, marker_raw = _manifest(source, expected, context.budget)
        with _owned_file(source.open_file("ledger.sqlite", single_link=True)) as fd:
            initial = os.fstat(fd)
            with storage.temporary_directory(scratch, context.budget) as private:
                context.checkpoint("copy")
                database = manifest["database"]
                storage.copy_file(fd, private, "ledger.sqlite", context.budget,
                                  expected_size=database["byte_length"], expected_hash=database["sha256"])
                context.checkpoint("verify")
                summary = validate_database(private.path / "ledger.sqlite", context.budget)
                if summary != manifest["summary"] or _database_info(private, context.budget) != {
                    "byte_length": database["byte_length"], "sha256": database["sha256"]
                }:
                    raise RecoveryError("INTEGRITY_FAILURE", "Snapshot database or summary does not match.", "verify")
                source.check("verify")
                current = os.stat("ledger.sqlite", dir_fd=source.fd, follow_symlinks=False)
                if _stamp(initial) != _stamp(os.fstat(fd)) or _stamp(initial) != _stamp(current):
                    raise RecoveryError("INTEGRITY_FAILURE", "Snapshot input changed during validation.", "verify")
                again, again_raw, again_marker = _manifest(source, expected, context.budget)
                if again_raw != raw or again_marker != marker_raw or again != manifest:
                    raise RecoveryError("INTEGRITY_FAILURE", "Snapshot metadata changed during validation.", "verify")
                # Consumers use the verified copy, never reopen the original DB.
                yield manifest, raw, marker_raw, private


def _copy_database(source, destination, expected, context):
    context.checkpoint("copy")
    with _owned_file(source.open_file("ledger.sqlite", single_link=True)) as fd:
        storage.copy_file(fd, destination, "ledger.sqlite", context.budget,
                          expected_size=expected["byte_length"], expected_hash=expected["sha256"])


def _seal_archive(parent, private, manifest, raw, marker_raw, context, *, remote=False):
    """Only the final marker link is a publication attempt."""
    parent.check("publish")
    with parent.mkdir(manifest["snapshot_id"]) as generation:
        _copy_database(private, generation, manifest["database"], context)
        storage.write_file(generation, "manifest.json", raw, context.budget)
        context.checkpoint("verify")
        if _database_info(generation, context.budget) != {
            "byte_length": manifest["database"]["byte_length"], "sha256": manifest["database"]["sha256"]
        } or storage.read_file(generation, "manifest.json", fmt.MAX_MANIFEST, context.budget) != raw:
            raise RecoveryError("INTEGRITY_FAILURE", "Archive copy failed its read-back check.", "verify")
        context.checkpoint("sync")
        generation.fsync()
        parent.fsync()
        with storage.temporary_directory(parent, context.budget) as marker_temp:
            storage.write_file(marker_temp, "COMMITTED.json", marker_raw, context.budget)
            parent.check("publish")
            context.link(marker_temp, "COMMITTED.json", generation, "COMMITTED.json", remote=remote)
        context.checkpoint("sync")
        generation.fsync()
        parent.fsync()
        digest = hashlib.sha256(raw).hexdigest()
        actual, actual_raw, actual_marker = _manifest(generation, digest, context.budget)
        if actual != manifest or actual_raw != raw or actual_marker != marker_raw or _database_info(generation, context.budget) != {
            "byte_length": manifest["database"]["byte_length"], "sha256": manifest["database"]["sha256"]
        }:
            raise RecoveryError("INTEGRITY_FAILURE", "Published snapshot changed during final validation.", "verify")
        generation.check("verify")
        parent.check("verify")
        return str(generation.path)


def _snapshot_data(manifest, raw):
    return {"snapshot_id": manifest["snapshot_id"], "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "database_sha256": manifest["database"]["sha256"], "summary": manifest["summary"]}


def create(*, db, output_root, snapshot_id, source_commit):
    """Create one sealed generation on a supported local filesystem."""
    def work(context):
        identity = fmt.validate_snapshot_id(snapshot_id)
        commit = fmt.validate_source_commit(source_commit)
        source_path, output_path = path(db), path(output_root)
        with storage.open_directory(source_path.parent, budget=context.budget) as source_parent, \
                storage.open_directory(output_path, budget=context.budget) as parent:
            if _entry_exists(parent, identity):
                raise RecoveryError("TARGET_EXISTS", "Snapshot generation already exists.", publication_state="not_applicable")
            context.checkpoint("snapshot")
            source_parent.check("snapshot")
            source_binding = preflight_source(source_path, context.budget)
            with storage.temporary_directory(parent, context.budget) as private:
                context.checkpoint("snapshot")
                source_parent.check("snapshot")
                summary = snapshot_database(source_path, private.path / "ledger.sqlite", context.budget,
                                            source_binding=source_binding)
                source_parent.check("snapshot")
                database = _database_info(private, context.budget)
                manifest = fmt.make_manifest(identity, commit, database["byte_length"], database["sha256"], summary)
                raw = fmt.encode(manifest, fmt.MAX_MANIFEST)
                marker_raw = fmt.encode(fmt.make_marker(identity, hashlib.sha256(raw).hexdigest()), fmt.MAX_MARKER)
                result_path = _seal_archive(parent, private, manifest, raw, marker_raw, context)
            data = _snapshot_data(manifest, raw)
            data.update(snapshot_path=result_path, durability_scope="filesystem_acknowledged")
            return data
    return _execute("create", work)


def publish(*, snapshot, storage_config, expected_manifest_sha256, scratch_parent):
    """Copy an already sealed local generation to an explicitly bound mount."""
    def work(context):
        config = fmt.capture_storage_config(storage_config)
        expected = fmt.validate_sha256(expected_manifest_sha256)
        source_path, scratch_path = path(snapshot), path(scratch_parent)
        _reject_plain_overlap(scratch_path, source_path)
        with storage.open_directory(scratch_path, budget=context.budget) as scratch, \
                storage.open_directory(config["archive_root"], budget=context.budget, storage_config=config) as destination:
            with _validated_snapshot(source_path, expected, scratch, None, context) as (manifest, raw, marker_raw, private):
                result_path = _seal_archive(destination, private, manifest, raw, marker_raw, context, remote=True)
            data = _snapshot_data(manifest, raw)
            data.update(snapshot_path=result_path, durability_scope="filesystem_acknowledged")
            return data
    return _execute("publish", work)


def verify(*, snapshot, expected_manifest_sha256, scratch_parent, storage_config=None):
    """Verify fixed archive bytes and complete ledger semantics on a local copy."""
    def work(context):
        config = None if storage_config is None else fmt.capture_storage_config(storage_config)
        expected = fmt.validate_sha256(expected_manifest_sha256)
        source_path, scratch_path = path(snapshot), path(scratch_parent)
        _reject_plain_overlap(scratch_path, source_path)
        with storage.open_directory(scratch_path, budget=context.budget) as scratch:
            with _validated_snapshot(source_path, expected, scratch, config, context) as (manifest, raw, _, private):
                return _snapshot_data(manifest, raw)
    return _execute("verify", work)


def restore(*, snapshot, target_dir, expected_manifest_sha256, scratch_parent, storage_config=None):
    """Restore into a wholly new directory without changing ledger identities."""
    def work(context):
        config = None if storage_config is None else fmt.capture_storage_config(storage_config)
        expected = fmt.validate_sha256(expected_manifest_sha256)
        source_path, target_path, scratch_path = path(snapshot), path(target_dir), path(scratch_parent)
        _reject_plain_overlap(scratch_path, source_path)
        _reject_plain_overlap(target_path, source_path)
        with storage.open_directory(scratch_path, budget=context.budget) as scratch, \
                storage.open_directory(target_path.parent, budget=context.budget) as parent:
            if _entry_exists(parent, target_path.name):
                raise RecoveryError("TARGET_EXISTS", "Restore target already exists.", publication_state="not_applicable")
            with _validated_snapshot(source_path, expected, scratch, config, context,
                                     destination_path=parent.path / target_path.name) as (manifest, raw, _, private):
                with parent.mkdir(target_path.name) as target:
                    with storage.temporary_directory(target, context.budget) as staged:
                        _copy_database(private, staged, manifest["database"], context)
                        context.checkpoint("verify")
                        if validate_database(staged.path / "ledger.sqlite", context.budget) != manifest["summary"]:
                            raise RecoveryError("INTEGRITY_FAILURE", "Restore verification summary differs.", "verify")
                        if _database_info(staged, context.budget) != {
                            "byte_length": manifest["database"]["byte_length"], "sha256": manifest["database"]["sha256"]
                        }:
                            raise RecoveryError("INTEGRITY_FAILURE", "Restore bytes changed during verification.", "verify")
                        with _owned_file(staged.open_file("ledger.sqlite", single_link=True)) as fd:
                            context.checkpoint("sync")
                            os.fsync(fd)
                            context.checkpoint("sync")
                        context.link(staged, "ledger.sqlite", target, "ledger.sqlite")
                    # Owned temporary link must be removed BEFORE final sync.
                    context.checkpoint("sync")
                    target.fsync()
                    parent.fsync()
                    if target.members(limit=1) != {"ledger.sqlite"} or _database_info(target, context.budget) != {
                        "byte_length": manifest["database"]["byte_length"], "sha256": manifest["database"]["sha256"]
                    }:
                        raise RecoveryError("INTEGRITY_FAILURE", "Restore target changed before completion.", "verify")
                    target.check("verify")
                    parent.check("verify")
            data = _snapshot_data(manifest, raw)
            data.update(database_path=str(target_path / "ledger.sqlite"), durability_scope="filesystem_acknowledged")
            return data
    return _execute("restore", work)


def check_restore(*, target_dir, expected_database_sha256, scratch_parent):
    """Read-only database check of an exclusively held, not-yet-used restore."""
    def work(context):
        expected = fmt.validate_sha256(expected_database_sha256)
        target_path, scratch_path = path(target_dir), path(scratch_parent)
        _reject_plain_overlap(scratch_path, target_path)
        with storage.open_directory(scratch_path, budget=context.budget) as scratch, \
                storage.open_directory(target_path, budget=context.budget) as target:
            _scratch_outside_input(scratch.path, target.path)
            if target.identity == scratch.identity:
                raise RecoveryError("INVALID_INPUT", "Scratch directory aliases the read-only target.")
            if any(_entry_exists(target, name) for name in _SIDECARS):
                raise RecoveryError("INTEGRITY_FAILURE", "Restore target contains SQLite sidecars.", "verify")
            with _owned_file(target.open_file("ledger.sqlite", single_link=False)) as fd:
                initial = os.fstat(fd)
                with storage.temporary_directory(scratch, context.budget) as private:
                    context.checkpoint("copy")
                    copied = storage.copy_file(fd, private, "ledger.sqlite", context.budget,
                                               expected_size=initial.st_size, expected_hash=expected)
                    context.checkpoint("verify")
                    summary = validate_database(private.path / "ledger.sqlite", context.budget)
                    if _database_info(private, context.budget) != copied:
                        raise RecoveryError("INTEGRITY_FAILURE", "Database bytes changed during validation.", "verify")
                    target.check("verify")
                    current = os.stat("ledger.sqlite", dir_fd=target.fd, follow_symlinks=False)
                    if _stamp(initial) != _stamp(current) or _stamp(initial) != _stamp(os.fstat(fd)) or any(_entry_exists(target, name) for name in _SIDECARS):
                        raise RecoveryError("INTEGRITY_FAILURE", "Restore target changed during validation.", "verify")
                return {"database_sha256": expected, "database_path": str(target_path / "ledger.sqlite"), "summary": summary}
    return _execute("check_restore", work)
