"""SQLite persistence boundary, deliberately independent of consumer domains.

All SQL passes through ``execute`` and transaction boundaries through the
named methods. These ordinary internal boundaries also let the test suite
exercise failures without adding any environment-controlled product hooks.
"""

import os
from pathlib import Path
import sqlite3

from .errors import LedgerError
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
        return Path(value).absolute()
    except (TypeError, ValueError, UnicodeError) as error:
        raise LedgerError("INVALID_INPUT", "Database path must be a valid local path.") from error


class SQLiteStore:
    """One handle owns one thread-bound, explicitly controlled connection."""

    def __init__(self, connection):
        self.connection = connection
        self.closed = False

    @classmethod
    def connect(cls, path, *, create=False):
        target = _path(path)
        created_stat = None
        if create:
            try:
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as error:
                raise LedgerError("IDENTITY_CONFLICT", "Database target already exists; it will not be overwritten.") from error
            try:
                created_stat = os.fstat(fd)
            finally:
                os.close(fd)
        elif not target.exists():
            raise LedgerError("NOT_FOUND", "Database path does not exist.")
        connection = None
        try:
            # URI mode=rw avoids implicitly creating a database after a race.
            connection = sqlite3.connect(target.as_uri() + "?mode=rw", uri=True,
                                         timeout=5.0, isolation_level=None)
            store = cls(connection)
            store.execute("PRAGMA foreign_keys=ON")
            store.execute("PRAGMA busy_timeout=5000")
            if create:
                store.execute("PRAGMA journal_mode=DELETE")
                store.execute("PRAGMA synchronous=FULL")
                store.begin(write=True)
                for statement in _SCHEMA:
                    store.execute(statement)
                store.execute("INSERT INTO ledger_format VALUES (?, ?)", (SCHEMA_VERSION, PROFILE))
                store.execute(f"PRAGMA application_id={APPLICATION_ID}")
                store.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                store.commit()
            else:
                store._validate_format()
                # Do not silently change a caller's incompatible live DB mode.
                if store.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
                    raise LedgerError("UNSUPPORTED_PROFILE", "Database journal mode is not the supported DELETE profile.")
                store.execute("PRAGMA synchronous=FULL")
            return store
        except BaseException:
            if connection is not None:
                connection.close()
            # A failed initialization must never remove another actor's file.
            if create and created_stat is not None:
                try:
                    current = target.stat()
                    if (current.st_dev, current.st_ino) == (created_stat.st_dev, created_stat.st_ino):
                        target.unlink()
                except OSError:
                    pass
            raise

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
        except sqlite3.DatabaseError as error:
            raise LedgerError("INTEGRITY_FAILURE", "Cannot read the existing Ledger format.") from error

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
        length = self.execute("SELECT length(data) FROM payloads WHERE blob_ref=?", (blob_ref,)).fetchone()
        if length is None:
            raise LedgerError("INTEGRITY_FAILURE", "Persisted Blob bytes are missing.", details={"blob_ref": blob_ref})
        if length[0] > limit:
            raise LedgerError("INTEGRITY_FAILURE", "Persisted Blob bytes exceed the supported length.", details={"blob_ref": blob_ref})
        data = self.execute("SELECT data FROM payloads WHERE blob_ref=?", (blob_ref,)).fetchone()[0]
        if type(data) is not bytes:
            raise LedgerError("INTEGRITY_FAILURE", "Persisted Blob is not binary data.", details={"blob_ref": blob_ref})
        return data

    def metadata(self):
        metadata = empty_metadata()
        for kind, raw in self.execute("SELECT kind, data FROM records ORDER BY id"):
            if kind not in KINDS:
                raise LedgerError("INTEGRITY_FAILURE", "Stored record has an unknown kind.")
            metadata[KINDS[kind][0]].append(self._decode(raw))
        operations = [self._decode(row[0]) for row in self.execute("SELECT data FROM operations")]
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
