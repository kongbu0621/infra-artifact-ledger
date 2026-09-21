"""SQLite persistence boundary, deliberately independent of consumer domains.

All SQL passes through ``execute`` and transaction boundaries through the
named methods. These ordinary internal boundaries also let the test suite
exercise failures without adding any environment-controlled product hooks.
"""

import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
from functools import lru_cache

from .errors import LedgerError
from ._checkpoints import checkpoint
from .fingerprint import canonical_bytes
from .records import KINDS, PROFILE, empty_metadata
from .validation import MAX_JSON, parse_json


SCHEMA_VERSION = 1
APPLICATION_ID = 0x49414C31

_SCHEMA = (
    "CREATE TABLE ledger_format (version INTEGER NOT NULL, profile TEXT NOT NULL)",
    "CREATE TABLE records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX records_kind ON records(kind, id)",
    "CREATE TABLE payloads (blob_ref TEXT PRIMARY KEY REFERENCES records(id), data BLOB NOT NULL)",
    "CREATE TABLE operations (scope TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, "
    "data TEXT NOT NULL, result_ref TEXT NOT NULL REFERENCES records(id), "
    "PRIMARY KEY(scope, kind, key))",
    "CREATE TABLE refs (source_id TEXT NOT NULL REFERENCES records(id), field TEXT NOT NULL, "
    "target_id TEXT NOT NULL REFERENCES records(id), PRIMARY KEY(source_id,field,target_id))",
    "CREATE INDEX refs_target ON refs(target_id, field)",
)


def _layout(connection):
    return tuple(connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ))


def _close_after_failure(connection, error):
    try:
        connection.close()
    except BaseException:
        BaseException.add_note(error, "SQLite connection cleanup also failed.")


@contextmanager
def _staging_directory(parent):
    temporary = tempfile.TemporaryDirectory(prefix=".artifact-ledger-init-", dir=parent)
    try:
        yield Path(temporary.name)
    except BaseException as error:
        try:
            temporary.cleanup()
        except BaseException:
            BaseException.add_note(error, "Private initialization directory cleanup also failed.")
        raise
    else:
        temporary.cleanup()


@lru_cache(maxsize=1)
def _expected_layout():
    # Use this SQLite build's own representation, including automatic indexes,
    # to avoid hard-coding differences between supported SQLite versions.
    connection = sqlite3.connect(":memory:")
    try:
        for statement in _SCHEMA:
            connection.execute(statement)
        result = _layout(connection)
    except BaseException as error:
        _close_after_failure(connection, error)
        raise
    else:
        connection.close()
    return result


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _same_file(path, expected):
    current = path.stat(follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise LedgerError("IO_ERROR", "The newly published database path was replaced.")


def _require_new_target(target):
    # Existing SQLite recovery files also occupy this name. Opening a newly
    # created main file beside an old hot journal could consume or remove that
    # unrelated recovery evidence. This initializer does not perform recovery.
    for candidate in (target, *(Path(str(target) + suffix) for suffix in ("-journal", "-wal", "-shm"))):
        if os.path.lexists(candidate):
            raise LedgerError("IDENTITY_CONFLICT", "Database target or SQLite sidecar already exists; it will not be overwritten.")


def reference_rows(kind, record):
    """Derive query indexes from immutable record text, with no second truth."""
    source = record[KINDS[kind][1]]
    targets = []
    if kind == "version":
        targets.extend((("artifact_id", record["artifact_id"]),
                        ("content_root_ref", record["content_root_ref"])))
        targets.extend((f"parent_version_refs.{i}", ref)
                       for i, ref in enumerate(record["parent_version_refs"]))
    elif kind == "content_root":
        field = "blob_ref" if record["kind"] == "blob" else "manifest_ref"
        targets.append((field, record[field]))
    elif kind == "manifest":
        targets.extend((f"entries.{i}", entry["blob_ref"])
                       for i, entry in enumerate(record["entries"]))
    elif kind == "provenance_link":
        targets.append(("subject_version_ref", record["subject_version_ref"]))
        if record["object"]["kind"] == "artifact_version":
            targets.append(("object.ref", record["object"]["ref"]))
    elif kind == "import_receipt":
        targets.extend((f"imported_artifact_refs.{i}", ref)
                       for i, ref in enumerate(record["imported_artifact_refs"]))
    return [(source, field, target) for field, target in targets]


def _path(path):
    try:
        value = os.fspath(path)
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("invalid path")
        # Match the CLI's host-path boundary before existence checks or
        # initialization can turn an unencodable name into an unrelated error.
        os.fsencode(value)
        return Path(value).absolute()
    except (TypeError, ValueError, UnicodeError) as error:
        raise LedgerError("INVALID_INPUT", "Database path must be a valid local path.") from error


def _sql_text(raw):
    # SQLite permits ill-formed UTF-8 in TEXT. Decode at the connection boundary
    # so its Python adapter cannot turn known corrupt text into a generic I/O
    # error before our record validation gets to see it.
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise LedgerError("INTEGRITY_FAILURE", "Stored SQL text is not valid UTF-8.") from error


class SQLiteStore:
    """One handle owns one thread-bound, explicitly controlled connection."""

    def __init__(self, connection):
        self.connection = connection
        self.connection.text_factory = _sql_text
        self.closed = False

    @classmethod
    def connect(cls, path, *, create=False):
        target = _path(path)
        if create:
            return cls._initialize(target)
        if not target.exists():
            raise LedgerError("NOT_FOUND", "Database path does not exist.")
        connection = None
        try:
            # URI mode=rw avoids implicitly creating a database after a race.
            connection = sqlite3.connect(target.as_uri() + "?mode=rw", uri=True,
                                         timeout=5.0, isolation_level=None)
            store = cls(connection)
            store.execute("PRAGMA foreign_keys=ON")
            store.execute("PRAGMA busy_timeout=5000")
            store._validate_format()
            # Do not silently change a caller's incompatible live DB mode.
            if store.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
                raise LedgerError("UNSUPPORTED_PROFILE", "Database journal mode is not the supported DELETE profile.")
            store.execute("PRAGMA synchronous=FULL")
            return store
        except BaseException as error:
            if connection is not None:
                _close_after_failure(connection, error)
            raise

    @classmethod
    def _initialize(cls, target):
        # Build in an owned directory on the same filesystem. No SQLite
        # initialization statement ever runs against the public target path.
        _require_new_target(target)
        with _staging_directory(target.parent) as directory:
            staged = directory / "ledger.sqlite"
            fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            connection = sqlite3.connect(staged.as_uri() + "?mode=rw", uri=True,
                                         timeout=5.0, isolation_level=None)
            try:
                store = cls(connection)
                store.execute("PRAGMA foreign_keys=ON")
                store.execute("PRAGMA journal_mode=DELETE")
                store.execute("PRAGMA synchronous=FULL")
                store.begin(write=True)
                for statement in _SCHEMA:
                    store.execute(statement)
                store.execute("INSERT INTO ledger_format VALUES (?, ?)", (SCHEMA_VERSION, PROFILE))
                store.execute(f"PRAGMA application_id={APPLICATION_ID}")
                store.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                store.commit()
            except BaseException as error:
                _close_after_failure(connection, error)
                raise
            else:
                connection.close()
            fd = os.open(staged, os.O_RDONLY)
            try:
                os.fsync(fd)
                published_stat = os.fstat(fd)
            finally:
                os.close(fd)
            _require_new_target(target)
            try:
                os.link(staged, target)
            except FileExistsError as error:
                raise LedgerError("IDENTITY_CONFLICT", "Database target already exists; it will not be overwritten.") from error
            # After publication the target may be complete even if fsync or
            # reopening fails. Never unlink it as error cleanup: another actor
            # may already have opened or replaced it.
            _fsync_directory(target.parent)
        _same_file(target, published_stat)
        result = cls.connect(target)
        try:
            _same_file(target, published_stat)
        except BaseException as error:
            _close_after_failure(result, error)
            raise
        return result

    def _validate_format(self):
        try:
            version = self.execute("PRAGMA user_version").fetchone()[0]
            application = self.execute("PRAGMA application_id").fetchone()[0]
            tables = {row[0] for row in self.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version != SCHEMA_VERSION or application != APPLICATION_ID or tables != {
                "ledger_format", "records", "payloads", "operations", "refs"
            }:
                raise LedgerError("UNSUPPORTED_VERSION", "Unknown or incompatible local Ledger format.")
            if self.execute("SELECT version, profile FROM ledger_format").fetchall() != [(SCHEMA_VERSION, PROFILE)]:
                raise LedgerError("UNSUPPORTED_VERSION", "Unknown or incompatible local Ledger format header.")
            required = {"records": {"id", "kind", "data"}, "payloads": {"blob_ref", "data"},
                        "operations": {"scope", "kind", "key", "data", "result_ref"},
                        "refs": {"source_id", "field", "target_id"}}
            for table, columns in required.items():
                if {row[1] for row in self.execute(f"PRAGMA table_info({table})")} != columns:
                    raise LedgerError("UNSUPPORTED_VERSION", "Unknown local Ledger table layout.")
            if _layout(self.connection) != _expected_layout():
                raise LedgerError("UNSUPPORTED_VERSION", "Unknown local Ledger schema, indexes or triggers.")
        except sqlite3.DatabaseError as error:
            code = getattr(error, "sqlite_errorcode", 0) & 255
            if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                raise LedgerError("INTEGRITY_FAILURE", "Cannot read the existing Ledger format.") from error
            # Lock contention and I/O failure do not establish corruption.
            # Preserve their SQLite codes for the public service error mapper.
            raise

    def execute(self, sql, parameters=()):
        return self.connection.execute(sql, parameters)

    def begin(self, write=False):
        self.execute("BEGIN IMMEDIATE" if write else "BEGIN")

    def commit(self):
        self.execute("COMMIT")

    def rollback(self):
        self.execute("ROLLBACK")

    def close(self):
        if not self.closed:
            self.connection.close()
            self.closed = True

    @staticmethod
    def _decode(text):
        try:
            if type(text) is not str:
                raise ValueError("record is not text")
            value = parse_json(text.encode("utf-8"), limit=MAX_JSON, label="stored record")
            if not isinstance(value, dict):
                raise ValueError("record is not an object")
            return value
        except (ValueError, TypeError, UnicodeError, LedgerError) as error:
            raise LedgerError("INTEGRITY_FAILURE", "Stored metadata is not a valid record.") from error

    def record(self, identity):
        row = self.execute("SELECT kind, data FROM records WHERE id=?", (identity,)).fetchone()
        return None if row is None else (row[0], self._decode(row[1]))

    def operation(self, scope, kind, key):
        row = self.execute("SELECT data FROM operations WHERE scope=? AND kind=? AND key=?",
                           (scope, kind, key)).fetchone()
        return None if row is None else self._decode(row[0])

    def payload(self, blob_ref, limit):
        info = self.execute("SELECT typeof(data),length(data) FROM payloads WHERE blob_ref=?", (blob_ref,)).fetchone()
        if info is None:
            raise LedgerError("INTEGRITY_FAILURE", "Persisted Blob bytes are missing.", details={"blob_ref": blob_ref})
        # length(TEXT) counts characters only up to its first NUL. Reject its
        # storage class before fetching any bytes or trusting that size bound.
        if info[0] != "blob":
            raise LedgerError("INTEGRITY_FAILURE", "Persisted Blob is not binary data.", details={"blob_ref": blob_ref})
        if info[1] > limit:
            raise LedgerError("INTEGRITY_FAILURE", "Persisted Blob bytes exceed the supported length.", details={"blob_ref": blob_ref})
        data = self.execute("SELECT data FROM payloads WHERE blob_ref=?", (blob_ref,)).fetchone()[0]
        if type(data) is not bytes:
            raise LedgerError("INTEGRITY_FAILURE", "Persisted Blob is not binary data.", details={"blob_ref": blob_ref})
        return data

    def metadata(self):
        metadata = empty_metadata()
        for identity, kind, raw in self.execute("SELECT id,kind,data FROM records ORDER BY id"):
            checkpoint()
            if kind not in KINDS:
                raise LedgerError("INTEGRITY_FAILURE", "Stored record has an unknown kind.")
            record = self._decode(raw)
            if record.get(KINDS[kind][1]) != identity:
                raise LedgerError("INTEGRITY_FAILURE", "Stored owned identity index differs from its immutable record.")
            metadata[KINDS[kind][0]].append(record)
        operations = []
        for scope, kind, key, result_ref, raw in self.execute(
            "SELECT scope,kind,key,result_ref,data FROM operations"
        ):
            checkpoint()
            record = self._decode(raw)
            if (scope, kind, key, result_ref) != (
                record.get("idempotency_scope_ref"), record.get("operation_kind"),
                record.get("idempotency_key"), record.get("result_ref")
            ):
                raise LedgerError("INTEGRITY_FAILURE", "Stored operation index differs from its immutable record.")
            operations.append(record)
        try:
            metadata["idempotency_records"] = sorted(operations, key=lambda item: (
                item["idempotency_scope_ref"], item["operation_kind"], item["idempotency_key"]))
        except (KeyError, TypeError) as error:
            raise LedgerError("INTEGRITY_FAILURE", "Stored operation metadata is invalid.") from error
        return metadata

    def insert_record(self, kind, record):
        self.execute("INSERT INTO records(id,kind,data) VALUES (?,?,?)", (
            record[KINDS[kind][1]], kind, canonical_bytes(record).decode("utf-8")))

    def insert_references(self, kind, record):
        for row in reference_rows(kind, record):
            self.execute("INSERT INTO refs(source_id,field,target_id) VALUES (?,?,?)", row)

    def insert_payload(self, blob_ref, data):
        self.execute("INSERT INTO payloads(blob_ref,data) VALUES (?,?)", (blob_ref, data))

    def insert_operation(self, record):
        self.execute("INSERT INTO operations(scope,kind,key,data,result_ref) VALUES (?,?,?,?,?)", (
            record["idempotency_scope_ref"], record["operation_kind"], record["idempotency_key"],
            canonical_bytes(record).decode("utf-8"), record["result_ref"]))
