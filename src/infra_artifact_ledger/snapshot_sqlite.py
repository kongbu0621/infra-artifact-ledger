"""A2 read-only source snapshots and full verification of private copies.

The file can also be executed by a fresh isolated Python interpreter for the
bounded header inspection. That entry runs before importing Ledger/SQLite:
opening and closing a raw source fd in its caller could drop POSIX locks owned
by that caller's existing SQLite connections.
"""

import json
import os
from pathlib import Path
import stat
import sys


def _header_result(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return {"ok": False, "code": "INVALID_INPUT"}
            raw = os.read(fd, 100)
            current = os.stat(path, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                return {"ok": False, "code": "IO_ERROR"}
        finally:
            os.close(fd)
        if len(raw) != 100 or raw[:16] != b"SQLite format 3\x00":
            return {"ok": False, "code": "INTEGRITY_FAILURE"}
        if raw[18:20] != b"\x01\x01":
            return {"ok": False, "code": "UNSUPPORTED_FORMAT"}
        return {"ok": True, "device": info.st_dev, "inode": info.st_ino}
    except FileNotFoundError:
        return {"ok": False, "code": "NOT_FOUND"}
    except OSError:
        return {"ok": False, "code": "IO_ERROR"}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--source-header":
        raise SystemExit(2)
    print(json.dumps(_header_result(sys.argv[2]), separators=(",", ":")))
    raise SystemExit(0)


from contextlib import contextmanager
import selectors
import sqlite3
import subprocess

from ._checkpoints import checkpoint_scope
from .errors import LedgerError
from .service import Ledger
from .snapshot_common import RecoveryError
from .sqlite_store import SQLiteStore, _expected_layout


MAX_DATABASE = 1_073_741_824
MAX_METADATA_ROWS = 50_000
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_REFS_ROWS = 250_000
MAX_REFS_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024 * 1024
BACKUP_PAGES = 256
_HEADER_OUTPUT_LIMIT = 2048
_TEXT_COLUMNS = {
    "ledger_format": ("profile",),
    "records": ("id", "kind", "data"),
    "payloads": ("blob_ref",),
    "operations": ("scope", "kind", "key", "data", "result_ref"),
    "refs": ("source_id", "field", "target_id"),
}


def _failure(code, message, stage):
    return RecoveryError(code, message, stage=stage)


def _close_connections(stage, *connections):
    pending = sys.exc_info()[1]
    failure = None
    for connection in connections:
        if connection is None:
            continue
        try:
            connection.close()
        except Exception as error:
            if pending is not None:
                BaseException.add_note(pending, "SQLite snapshot connection cleanup also failed.")
            elif failure is None:
                failure = error
    if failure is not None:
        raise _translate(failure, stage) from failure


def _translate(error, stage):
    if isinstance(error, RecoveryError):
        return error
    if isinstance(error, LedgerError):
        code = error.code
        if code in ("UNSUPPORTED_VERSION", "UNSUPPORTED_PROFILE"):
            code = "UNSUPPORTED_FORMAT"
        elif code not in ("BUSY", "IO_ERROR", "RESOURCE_LIMIT", "INTEGRITY_FAILURE", "NOT_FOUND"):
            code = "INTEGRITY_FAILURE"
        return _failure(code, "Ledger snapshot validation failed.", stage)
    if isinstance(error, sqlite3.Error):
        code = getattr(error, "sqlite_errorcode", 0) & 255
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return _failure("BUSY", "The source database is busy.", stage)
        if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CONSTRAINT):
            return _failure("INTEGRITY_FAILURE", "SQLite database integrity validation failed.", stage)
        # A readonly hot journal requires recovery; never perform it here.
        if code == sqlite3.SQLITE_READONLY:
            return _failure("INTEGRITY_FAILURE", "The source requires recovery outside the snapshot operation.", stage)
        if code == sqlite3.SQLITE_TOOBIG:
            return _failure("RESOURCE_LIMIT", "SQLite value exceeds the supported resource budget.", stage)
        if code == sqlite3.SQLITE_FULL:
            return _failure("IO_ERROR", "SQLite could not allocate snapshot storage.", stage)
        return _failure("IO_ERROR", "SQLite could not complete the snapshot operation.", stage)
    if isinstance(error, FileNotFoundError):
        return _failure("NOT_FOUND", "A database input does not exist.", stage)
    if isinstance(error, UnicodeError):
        return _failure("INTEGRITY_FAILURE", "Database text is not valid UTF-8.", stage)
    return _failure("IO_ERROR", "Database snapshot I/O failed.", stage)


def _mount_bindings(path):
    """Capture relevant mount records without opening the live database."""
    records = []
    with open("/proc/self/mountinfo", encoding="utf-8", errors="strict") as stream:
        for line in stream:
            fields = line.split()
            if len(fields) < 10:
                raise _failure("IO_ERROR", "Mount binding information is invalid.", "validate")
            point = fields[4]
            for encoded, decoded in (("\\040", " "), ("\\011", "\t"),
                                     ("\\012", "\n"), ("\\134", "\\")):
                point = point.replace(encoded, decoded)
            if path == Path(point) or Path(point) in path.parents:
                records.append(tuple(fields))
    if not records:
        raise _failure("UNSUPPORTED_STORAGE", "Source mount binding is unavailable.", "validate")
    return tuple(records)


def _source_binding(path, budget):
    budget.check("validate")
    entries = []
    for candidate in (*reversed(path.parents), path):
        budget.check("validate")
        info = candidate.stat(follow_symlinks=False)
        required = stat.S_ISREG if candidate == path else stat.S_ISDIR
        if not required(info.st_mode):
            raise _failure("INVALID_INPUT", "Source path must contain ordinary directories and a regular database.", "validate")
        if candidate == path and info.st_size > MAX_DATABASE:
            raise _failure("RESOURCE_LIMIT", "Database exceeds the 1 GiB snapshot limit.", "validate")
        entries.append((info.st_dev, info.st_ino))
    return tuple(entries), _mount_bindings(path)


def _assert_source_binding(path, expected, budget):
    try:
        current = _source_binding(path, budget)
    except RecoveryError as error:
        if error.code == "INVALID_INPUT":
            raise _failure("IO_ERROR", "Source path binding changed during snapshot.", "snapshot") from error
        raise
    except (FileNotFoundError, NotADirectoryError) as error:
        raise _failure("IO_ERROR", "Source path binding changed during snapshot.", "snapshot") from error
    if current != expected:
        raise _failure("IO_ERROR", "Source path or mount binding changed during snapshot.", "snapshot")


def _read_header(source, budget):
    """Inspect at most 100 source bytes, with bounded child output and cleanup."""
    budget.check("validate")
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).absolute()), "--source-header", str(source)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                budget.check("validate")
                for key, _ in selector.select(min(0.05, budget.remaining("validate"))):
                    chunk = os.read(key.fileobj.fileno(), _HEADER_OUTPUT_LIMIT + 1 - len(output))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > _HEADER_OUTPUT_LIMIT:
                            raise _failure("IO_ERROR", "Source inspection returned too much output.", "validate")
        try:
            returncode = process.wait(timeout=budget.remaining("validate"))
        except subprocess.TimeoutExpired as error:
            raise _failure("TIMEOUT", "Source inspection exceeded the operation deadline.", "validate") from error
        budget.check("validate")
        if returncode != 0:
            raise _failure("IO_ERROR", "Source inspection process did not complete.", "validate")
        try:
            def unique_fields(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("duplicate child result field")
                    value[key] = item
                return value

            result = json.loads(bytes(output).decode("ascii"), object_pairs_hook=unique_fields)
            if type(result) is not dict or type(result.get("ok")) is not bool:
                raise ValueError("invalid result")
            if result["ok"]:
                if set(result) != {"ok", "device", "inode"} or any(
                    type(result[key]) is not int or result[key] < 0 for key in ("device", "inode")
                ):
                    raise ValueError("invalid binding")
                return result["device"], result["inode"]
            if set(result) != {"ok", "code"} or result["code"] not in {
                "INVALID_INPUT", "IO_ERROR", "NOT_FOUND", "INTEGRITY_FAILURE", "UNSUPPORTED_FORMAT"
            }:
                raise ValueError("invalid failure")
        except (ValueError, UnicodeError, TypeError) as error:
            raise _failure("IO_ERROR", "Source inspection returned an invalid result.", "validate") from error
        raise _failure(result["code"], "Source header inspection rejected the database.", "validate")
    except OSError as error:
        raise _failure("IO_ERROR", "Could not start or read the source inspection process.", "validate") from error
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            process.wait()
            if process.stdout is not None:
                process.stdout.close()


@contextmanager
def _progress(connection, budget, stage):
    interruption = []

    def check():
        try:
            budget.check(stage)
        except RecoveryError as error:
            interruption.append(error)
            return 1
        return 0

    connection.set_progress_handler(check, 1000)
    try:
        yield
    except (sqlite3.Error, LedgerError) as error:
        if interruption:
            raise interruption[0] from error
        raise
    finally:
        connection.set_progress_handler(None, 0)


def _connect_readonly(path):
    # A live source must use normal SQLite locking, never immutable=1.
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                           isolation_level=None, timeout=0.0)


def _format_and_budget(connection, budget, stage):
    budget.check(stage)
    store = SQLiteStore(connection)
    if connection.execute("PRAGMA encoding").fetchone()[0] != "UTF-8":
        raise _failure("UNSUPPORTED_FORMAT", "Snapshot databases must use UTF-8.", stage)
    if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
        raise _failure("UNSUPPORTED_FORMAT", "Only DELETE journal databases are supported.", stage)
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    if page_size * page_count > MAX_DATABASE:
        raise _failure("RESOURCE_LIMIT", "Database exceeds the 1 GiB snapshot limit.", stage)
    # Bound schema and its singleton format record before A1's exact layout
    # validator decodes TEXT or uses fetchall. Arbitrary SQLite is not Ledger.
    schema_count, schema_bytes = connection.execute(
        "SELECT count(*), coalesce(sum(length(CAST(type AS BLOB)) + "
        "length(CAST(name AS BLOB)) + length(CAST(tbl_name AS BLOB)) + "
        "coalesce(length(CAST(sql AS BLOB)),0)),0) FROM sqlite_master"
    ).fetchone()
    if schema_count != len(_expected_layout()) or schema_bytes > 65536:
        raise _failure("UNSUPPORTED_FORMAT", "Unknown local Ledger schema.", stage)
    # Validate the exact layout before touching tables whose names may otherwise
    # resolve to a view. This comparison is now bounded to 64 KiB of schema.
    actual_layout = tuple(connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ))
    if actual_layout != _expected_layout():
        raise _failure("UNSUPPORTED_FORMAT", "Unknown local Ledger schema.", stage)
    rows = connection.execute(
        "SELECT typeof(version),typeof(profile),length(CAST(profile AS BLOB)) "
        "FROM ledger_format LIMIT 2"
    ).fetchall()
    if len(rows) != 1 or rows[0][0] != "integer" or rows[0][1] != "text" or rows[0][2] > 128:
        raise _failure("UNSUPPORTED_FORMAT", "Invalid local Ledger format header.", stage)
    store._validate_format()
    totals = {}
    text_bytes = 0
    for table, columns in _TEXT_COLUMNS.items():
        budget.check(stage)
        lengths = "+".join(f"length(CAST({column} AS BLOB))" for column in columns)
        invalid = " OR ".join(f"typeof({column}) != 'text'" for column in columns)
        count, size, bad = connection.execute(
            f"SELECT count(*), coalesce(sum({lengths}),0), coalesce(sum({invalid}),0) FROM {table}"
        ).fetchone()
        if bad:
            raise _failure("INTEGRITY_FAILURE", "Ledger text column has an invalid storage class.", stage)
        totals[table] = (count, size)
        text_bytes += size
    if totals["records"][0] + totals["operations"][0] > MAX_METADATA_ROWS:
        raise _failure("RESOURCE_LIMIT", "Snapshot metadata row limit exceeded.", stage)
    if totals["refs"][0] > MAX_REFS_ROWS or totals["refs"][1] > MAX_REFS_BYTES:
        raise _failure("RESOURCE_LIMIT", "Snapshot reference budget exceeded.", stage)
    if text_bytes > MAX_TEXT_BYTES:
        raise _failure("RESOURCE_LIMIT", "Snapshot total TEXT budget exceeded.", stage)
    data_bytes = sum(connection.execute(
        f"SELECT coalesce(sum(length(CAST(data AS BLOB))),0) FROM {table}"
    ).fetchone()[0] for table in ("records", "operations"))
    if data_bytes > MAX_METADATA_BYTES:
        raise _failure("RESOURCE_LIMIT", "Snapshot metadata byte limit exceeded.", stage)
    budget.check(stage)
    return page_size


def _no_sidecars(path, stage):
    for suffix in ("-journal", "-wal", "-shm"):
        if os.path.lexists(str(path) + suffix):
            raise _failure("INTEGRITY_FAILURE", "Private database has an unexpected SQLite sidecar.", stage)


def validate_database(path: Path, budget):
    """Fully verify an exclusively owned local private copy and return summary."""
    stage = "verify"
    connection = None
    try:
        budget.check(stage)
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise _failure("INTEGRITY_FAILURE", "Private database is not a regular file.", stage)
        if info.st_size > MAX_DATABASE:
            raise _failure("RESOURCE_LIMIT", "Database exceeds the 1 GiB snapshot limit.", stage)
        _no_sidecars(path, stage)
        header = _header_result(path)
        if not header["ok"]:
            raise _failure(header["code"], "Private database header is not supported.", stage)
        connection = _connect_readonly(path)
        with _progress(connection, budget, stage):
            connection.execute("BEGIN")
            _format_and_budget(connection, budget, stage)
            integrity = connection.execute("PRAGMA integrity_check").fetchmany(2)
            if integrity != [("ok",)]:
                raise _failure("INTEGRITY_FAILURE", "SQLite integrity_check rejected the database.", stage)
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise _failure("INTEGRITY_FAILURE", "SQLite foreign_key_check rejected the database.", stage)
            connection.execute("ROLLBACK")
            with checkpoint_scope(lambda: budget.check(stage)):
                summary = Ledger(SQLiteStore(connection)).verify()
            budget.check(stage)
        connection.close()
        connection = None
        _no_sidecars(path, stage)
        after = path.stat(follow_symlinks=False)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise _failure("INTEGRITY_FAILURE", "Private database changed during verification.", stage)
        return summary
    except Exception as error:
        raise _translate(error, stage) from error
    finally:
        _close_connections(stage, connection)


def preflight_source(source: Path, budget):
    """Check source without creating scratch files or opening source SQLite."""
    try:
        binding = _source_binding(source, budget)
        if _read_header(source, budget) != binding[0][-1]:
            raise _failure("IO_ERROR", "Source binding changed during header inspection.", "validate")
        _assert_source_binding(source, binding, budget)
        return binding
    except Exception as error:
        raise _translate(error, "validate") from error


def snapshot_database(source: Path, target: Path, budget, *, source_binding=None):
    """Create a new private SQLite backup, release source, then fully verify it."""
    stage = "snapshot"
    source_connection = target_connection = None
    try:
        binding = preflight_source(source, budget) if source_binding is None else source_binding
        _assert_source_binding(source, binding, budget)
        source_connection = _connect_readonly(source)
        _assert_source_binding(source, binding, budget)
        with _progress(source_connection, budget, stage):
            source_connection.execute("BEGIN")
            # A real schema read pins SQLite's snapshot. BEGIN alone does not.
            source_connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            page_size = _format_and_budget(source_connection, budget, stage)
            budget.check(stage)
            for name in (target, *(Path(str(target) + suffix) for suffix in ("-journal", "-wal", "-shm"))):
                if os.path.lexists(name):
                    raise _failure("TARGET_EXISTS", "Private snapshot target is already occupied.", stage)
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError as error:
                raise _failure("TARGET_EXISTS", "Private snapshot target is already occupied.", stage) from error
            os.close(fd)
            target_connection = sqlite3.connect(target.as_uri() + "?mode=rw", uri=True,
                                                isolation_level=None, timeout=0.0)

            def progress(status, remaining, total):
                budget.check(stage)
                if total * page_size > MAX_DATABASE:
                    raise _failure("RESOURCE_LIMIT", "Backup exceeds the 1 GiB snapshot limit.", stage)
                if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise _failure("BUSY", "SQLite backup is busy.", stage)

            source_connection.backup(target_connection, pages=BACKUP_PAGES, progress=progress, sleep=0.0)
            budget.check(stage)
        target_connection.close()
        target_connection = None
        _assert_source_binding(source, binding, budget)
        source_connection.close()
        source_connection = None
        # Source read locks have ended before expensive graph/blob verification.
        return validate_database(target, budget)
    except Exception as error:
        raise _translate(error, stage) from error
    finally:
        _close_connections(stage, target_connection, source_connection)
