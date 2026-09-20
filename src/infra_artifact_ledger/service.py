"""Public operations and their transaction, identity and content guarantees."""

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import re
import sqlite3

from .errors import LedgerError
from .fingerprint import canonical_bytes, request_fingerprint
from .portable import decode_bundle, encode_bundle
from .records import KINDS
from .sqlite_store import SQLiteStore, reference_rows
from .validation import (MAX_BLOB, MAX_JSON, MAX_PAYLOAD, check_payload,
                         content_blob_refs, parse_json, validate_metadata,
                         validate_operation_query, validate_record, validate_request)


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}\Z")
_RESULT_KIND = {"create_artifact": "artifact", "append_version": "version", "import_bundle": "import_receipt"}
_MAX_COUNT = 9007199254740991


class _RejectedReplayInput(LedgerError):
    """Invalid transport input is rejected without disputing an older commit."""


def _identity(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise LedgerError("INVALID_INPUT", "Expected a StableId string.")


def _error(error, state):
    if isinstance(error, LedgerError):
        return LedgerError(error.code, error.message, state, error.details)
    if isinstance(error, sqlite3.Error):
        code = getattr(error, "sqlite_errorcode", 0) & 255
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return LedgerError("BUSY", "The local Ledger is busy; retry the same request later.", state)
        if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CONSTRAINT):
            return LedgerError("INTEGRITY_FAILURE", "Stored Ledger integrity validation failed.", state)
        return LedgerError("IO_ERROR", "SQLite could not complete the local Ledger operation.", state)
    if isinstance(error, OSError):
        return LedgerError("IO_ERROR", "Local Ledger I/O failed.", state)
    return LedgerError("INTERNAL_ERROR", "The Ledger operation failed internally.", state)


def _tuple(record):
    return record["idempotency_scope_ref"], record["operation_kind"], record["idempotency_key"]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _record_map(metadata):
    return {record[field]: (kind, record) for kind, (collection, field) in KINDS.items()
            for record in metadata.get(collection, [])}


def _response_size(data):
    envelope = {"status": "OK", "commit_state": "not_applicable", "data": data}
    if len(canonical_bytes(envelope)) + 1 > MAX_JSON:
        raise LedgerError("RESOURCE_LIMIT", "Ordinary response exceeds the 8 MiB JSON limit.",
                          details={"limit": "response_json_bytes"})
    return data


class Ledger:
    """A thread-bound local handle; each execute is its own transaction."""

    def __init__(self, store):
        self._store = store

    def __enter__(self):
        if self._store.closed:
            raise LedgerError("IO_ERROR", "Ledger handle is closed.", "not_applicable")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def close(self):
        try:
            self._store.close()
        except Exception as error:
            raise _error(error, "not_applicable") from error

    @contextmanager
    def _read(self):
        try:
            self._store.begin()
            yield
            self._store.rollback()
        except Exception as error:
            try:
                if self._store.connection.in_transaction:
                    self._store.rollback()
            except Exception:
                pass
            raise _error(error, "not_applicable") from error

    def _metadata(self):
        metadata = self._store.metadata()
        try:
            validate_metadata(metadata)
        except LedgerError as error:
            raise LedgerError("INTEGRITY_FAILURE", "Stored metadata is invalid: " + error.message,
                              details=error.details) from error
        return metadata

    def _record(self, kind, identity):
        row = self._store.record(identity)
        if row is None or row[0] != kind:
            raise LedgerError("NOT_FOUND", "The requested typed record does not exist.")
        try:
            validate_record(kind, row[1])
            if row[1][KINDS[kind][1]] != identity:
                raise LedgerError("INTEGRITY_FAILURE", "Stored identity differs from its record index.")
        except LedgerError as error:
            raise LedgerError("INTEGRITY_FAILURE", "Stored record is invalid: " + error.message) from error
        return row[1]

    def get_record(self, kind, id):
        with self._read():
            if type(kind) is not str or kind not in KINDS:
                raise LedgerError("INVALID_INPUT", "Unknown record kind.")
            _identity(id)
            return _response_size(self._record(kind, id))

    def get_history(self, artifact_id):
        with self._read():
            _identity(artifact_id)
            artifact = self._record("artifact", artifact_id)
            metadata = self._metadata()
            versions = [record for record in metadata["versions"] if record["artifact_id"] == artifact_id]
            version_ids = {record["version_id"] for record in versions}
            provenance = [record for record in metadata["provenance_links"]
                          if record["subject_version_ref"] in version_ids]
            return _response_size({"artifact": artifact, "versions": versions, "provenance_links": provenance})

    def get_operation(self, scope, kind, key):
        with self._read():
            query = {"idempotency_scope_ref": scope, "operation_kind": kind, "idempotency_key": key}
            validate_operation_query(query)
            record = self._store.operation(scope, kind, key)
            if record is None:
                raise LedgerError("NOT_FOUND", "No successful operation with this identity was found.")
            # Metadata integrity is checked, but deliberately no Blob is read.
            self._metadata()
            if _tuple(record) != (scope, kind, key):
                raise LedgerError("INTEGRITY_FAILURE", "Operation identity differs from its stored index.")
            result = self._record(_RESULT_KIND[kind], record["result_ref"])
            return _response_size({"idempotency_record": record, "result": result})

    def _blob(self, record):
        try:
            data = self._store.payload(record["blob_ref"], MAX_BLOB)
            check_payload(record, data)
            return data
        except LedgerError as error:
            raise LedgerError("INTEGRITY_FAILURE", "Persisted Blob is not intact: " + error.message,
                              details={"blob_ref": record["blob_ref"]}) from error

    def read_blob(self, blob_ref):
        with self._read():
            _identity(blob_ref)
            return self._blob(self._record("blob", blob_ref))

    def _verify_indexes(self, metadata):
        expected = {row for kind, (collection, _) in KINDS.items()
                    for record in metadata.get(collection, []) for row in reference_rows(kind, record)}
        if expected != set(self._store.execute("SELECT source_id,field,target_id FROM refs")):
            raise LedgerError("INTEGRITY_FAILURE", "Stored reference indexes differ from immutable records.")
        expected_records = {(identity, kind) for identity, (kind, _) in _record_map(metadata).items()}
        if expected_records != set(self._store.execute("SELECT id,kind FROM records")):
            raise LedgerError("INTEGRITY_FAILURE", "Stored owned identity indexes differ from immutable records.")
        expected_ops = {(*_tuple(record), record["result_ref"]) for record in metadata["idempotency_records"]}
        if expected_ops != set(self._store.execute("SELECT scope,kind,key,result_ref FROM operations")):
            raise LedgerError("INTEGRITY_FAILURE", "Stored operation indexes differ from immutable records.")
        if {record["blob_ref"] for record in metadata["blobs"]} != {
            row[0] for row in self._store.execute("SELECT blob_ref FROM payloads")
        }:
            raise LedgerError("INTEGRITY_FAILURE", "Stored payload coverage differs from Blob records.")

    def verify(self):
        with self._read():
            failures = []
            failure_count = 0

            def fail(location, reason):
                nonlocal failure_count
                failure_count += 1
                if len(failures) < 100:
                    failures.append({"location": location, "reason": reason})

            try:
                metadata = self._store.metadata()
            except Exception as error:
                fail("metadata", str(error))
                raise LedgerError("INTEGRITY_FAILURE", "Ledger verification failed.",
                                  details={"failures": failures, "truncated": False}) from error
            try:
                validate_metadata(metadata)
            except LedgerError as error:
                fail("metadata", error.message)
            try:
                self._verify_indexes(metadata)
            except (LedgerError, KeyError, TypeError) as error:
                fail("indexes", str(error))
            verified_count = verified_length = 0
            for record in metadata["blobs"]:
                location = str(record.get("blob_ref", "blob"))
                try:
                    validate_record("blob", record)
                    data = self._blob(record)
                    verified_count += 1
                    verified_length += len(data)
                    del data
                except (LedgerError, KeyError, TypeError) as error:
                    fail(location, str(error))
            if failure_count:
                raise LedgerError("INTEGRITY_FAILURE", "Ledger verification failed.",
                                  details={"failures": failures, "truncated": failure_count > 100})
            collections = [collection for collection, _ in KINDS.values()] + ["idempotency_records"]
            counts = {collection: len(metadata[collection]) for collection in collections}
            if max(*counts.values(), verified_length, verified_count) > _MAX_COUNT:
                raise LedgerError("RESOURCE_LIMIT", "Verification count exceeds the exact integer range.")
            return _response_size({"counts": counts, "verified_blob_count": verified_count,
                                   "verified_byte_length": verified_length})

    def export_bundle(self):
        with self._read():
            metadata = self._metadata()
            self._verify_indexes(metadata)
            # Enforce limits before collecting the payload bytes. A large
            # Ledger remains usable even when a whole export no longer fits.
            if len(canonical_bytes(metadata)) > MAX_JSON:
                raise LedgerError("RESOURCE_LIMIT", "Complete metadata exceeds the export JSON limit.")
            if sum(record["byte_length"] for record in metadata["blobs"]) > MAX_PAYLOAD:
                raise LedgerError("RESOURCE_LIMIT", "Complete Ledger payload exceeds the export limit.")
            payloads = {record["blob_ref"]: self._blob(record) for record in metadata["blobs"]}
            return encode_bundle(metadata, payloads)

    @staticmethod
    def _success(operation_record, replayed=False):
        return {"status": "COMMITTED", "commit_state": "committed",
                "operation_kind": operation_record["operation_kind"],
                "result_ref": operation_record["result_ref"], "recorded_at": operation_record["recorded_at"],
                "replayed": replayed}

    @staticmethod
    def _committed_details(record):
        return {name: record[name] for name in ("result_ref", "idempotency_scope_ref", "operation_kind",
                                                "idempotency_key", "recorded_at")}

    def execute(self, request_utf8, *, payloads=None, package=None, descriptor=None):
        """Validate immutable inputs, then recheck and commit one operation."""
        operation_record = None
        began = committing = committed = False
        try:
            request = validate_request(parse_json(request_utf8))
            kind = request["operation_kind"]
            metadata = None
            supplied = {}
            if kind == "create_artifact":
                if payloads is not None or package is not None or descriptor is not None:
                    raise LedgerError("INVALID_INPUT", "create_artifact does not accept transport content arguments.")
            elif kind == "append_version":
                if not isinstance(payloads, Mapping) or package is not None or descriptor is not None:
                    raise LedgerError("INVALID_INPUT", "append_version requires only a payloads mapping.")
                # Snapshot mapping entries immediately; bytes themselves are
                # immutable and these exact objects will be inserted.
                supplied = dict(payloads.items())
                total = 0
                for ref, data in supplied.items():
                    _identity(ref)
                    if type(data) is not bytes:
                        raise LedgerError("INVALID_INPUT", "Payload values must be immutable bytes.")
                    if len(data) > MAX_BLOB:
                        raise LedgerError("RESOURCE_LIMIT", "One payload exceeds the Blob size limit.")
                    total += len(data)
                    if total > MAX_PAYLOAD:
                        raise LedgerError("RESOURCE_LIMIT", "Supplied payloads exceed the per-call limit.")
                announced = {record["blob_ref"]: record for record in request["body"]["blobs"]}
                for ref, data in supplied.items():
                    if ref in announced:
                        check_payload(announced[ref], data)
            else:
                if payloads is not None or type(package) is not bytes or type(descriptor) is not bytes:
                    raise LedgerError("INVALID_INPUT", "import_bundle requires package and descriptor bytes only.")
                metadata, supplied = decode_bundle(package, descriptor)
            fingerprint = request_fingerprint(request, bundle=metadata)
            if "request_fingerprint" in request and request["request_fingerprint"] != fingerprint:
                raise LedgerError("INVALID_INPUT", "Supplied request_fingerprint does not match the request.")
            if self._store.closed:
                raise LedgerError("IO_ERROR", "Ledger handle is closed.")
            # Include a failure immediately after BEGIN in rollback handling.
            began = True
            self._store.begin(write=True)
            previous = self._store.operation(*_tuple(request))
            if previous is not None:
                try:
                    validate_record("idempotency_record", previous)
                    if _tuple(previous) != _tuple(request):
                        raise LedgerError("INTEGRITY_FAILURE", "Stored operation identity differs from its index.")
                except LedgerError as error:
                    raise LedgerError("INTEGRITY_FAILURE", "Stored successful operation is invalid: " + error.message) from error
                if previous["request_fingerprint"] != fingerprint:
                    raise LedgerError("IDEMPOTENCY_CONFLICT", "The idempotency identity is already bound to a different request.")
                operation_record = previous
                # The prior commit is known even if replay validation finds
                # later content corruption or releasing the lock fails.
                committed = True
                self._check_replay(request, metadata, supplied, previous)
                self._store.rollback()
                began = False
                return self._success(previous, replayed=True)
            current = self._metadata()
            self._verify_indexes(current)
            recorded_at = _now()
            if kind == "create_artifact":
                additions, new_payloads, historical_ops, result_ref = self._create(request, current, recorded_at)
            elif kind == "append_version":
                additions, new_payloads, historical_ops, result_ref = self._append(request, current, supplied, recorded_at)
            else:
                additions, new_payloads, historical_ops, result_ref = self._import(request, current, metadata, supplied, recorded_at)
            operation_record = {"idempotency_scope_ref": request["idempotency_scope_ref"],
                                "operation_kind": kind, "idempotency_key": request["idempotency_key"],
                                "request_fingerprint": fingerprint, "result_ref": result_ref,
                                "recorded_at": recorded_at}
            for record_kind, record in additions:
                self._store.insert_record(record_kind, record)
            # Record all owned IDs before adding foreign-key references.
            for record_kind, record in additions:
                self._store.insert_references(record_kind, record)
            for blob_ref, data in new_payloads.items():
                self._store.insert_payload(blob_ref, data)
            for historical_record in historical_ops:
                self._store.insert_operation(historical_record)
            self._store.insert_operation(operation_record)
            committing = True
            self._store.commit()
            committed = True
            return self._success(operation_record)
        except Exception as error:
            if isinstance(error, _RejectedReplayInput):
                committed = False
            if committed:
                try:
                    if self._store.connection.in_transaction:
                        self._store.rollback()
                except Exception:
                    pass
                public = _error(error, "committed")
                # A known commit never becomes BUSY/not_committed/unknown.
                if public.code not in ("INTEGRITY_FAILURE", "IO_ERROR", "INTERNAL_ERROR"):
                    public = LedgerError("INTERNAL_ERROR", public.message, "committed", public.details)
                public.details = {**(public.details or {}), **self._committed_details(operation_record)}
                raise public from error
            if began:
                try:
                    active = self._store.connection.in_transaction
                    if not active and committing:
                        raise RuntimeError("COMMIT outcome cannot be inferred from an inactive transaction")
                    if active:
                        self._store.rollback()
                    if self._store.connection.in_transaction:
                        raise RuntimeError("Rollback did not end the transaction")
                except Exception as rollback_error:
                    raise LedgerError("DURABILITY_UNKNOWN", "Commit or rollback could not be confirmed; query or retry the original identity.",
                                      "unknown") from rollback_error
            raise _error(error, "not_committed") from error

    def _check_replay(self, request, incoming, supplied, previous):
        current = self._metadata()
        self._verify_indexes(current)
        kind = request["operation_kind"]
        result = self._record(_RESULT_KIND[kind], previous["result_ref"])
        if _tuple(previous) != _tuple(request):
            raise LedgerError("INTEGRITY_FAILURE", "Stored idempotency identity does not match its index.")

        def require_record(record_kind, original, *, without_time=False):
            field = KINDS[record_kind][1]
            actual = self._record(record_kind, original[field])
            if without_time:
                actual = {key: value for key, value in actual.items() if key != "recorded_at"}
            if actual != original:
                raise LedgerError("INTEGRITY_FAILURE", "Committed metadata differs from the original successful request.")

        if kind == "create_artifact":
            if previous["result_ref"] != request["body"]["artifact"]["artifact_id"]:
                raise LedgerError("INTEGRITY_FAILURE", "Success result differs from the original Artifact identity.")
            require_record("artifact", request["body"]["artifact"], without_time=True)
            refs = set()
        elif kind == "append_version":
            if previous["result_ref"] != request["body"]["version"]["version_id"]:
                raise LedgerError("INTEGRITY_FAILURE", "Success result differs from the original Version identity.")
            require_record("version", request["body"]["version"], without_time=True)
            for record_kind in ("content_root", "blob", "manifest", "provenance_link"):
                for original in request["body"][KINDS[record_kind][0]]:
                    require_record(record_kind, original, without_time=record_kind == "provenance_link")
            refs = content_blob_refs(current, result)
        else:
            if previous["result_ref"] != request["body"]["import_receipt_id"] or result["imported_artifact_refs"] != sorted(
                record["artifact_id"] for record in incoming["artifacts"]
            ):
                raise LedgerError("INTEGRITY_FAILURE", "Success Receipt differs from the original import.")
            for record_kind, (collection, _) in KINDS.items():
                for original in incoming.get(collection, []):
                    require_record(record_kind, original)
            for original in incoming["idempotency_records"]:
                if self._store.operation(*_tuple(original)) != original:
                    raise LedgerError("INTEGRITY_FAILURE", "Imported operation history differs from the original package.")
            refs = {record["blob_ref"] for record in incoming["blobs"]}
        if not supplied.keys() <= refs:
            raise _RejectedReplayInput("INVALID_INPUT", "Payload references are outside the replayed content closure.")
        for ref in refs:
            record = self._record("blob", ref)
            if ref in supplied:
                try:
                    check_payload(record, supplied[ref])
                except LedgerError as error:
                    raise _RejectedReplayInput(error.code, error.message, details=error.details) from error
            self._blob(record)

    def _create(self, request, current, timestamp):
        record = {**request["body"]["artifact"], "recorded_at": timestamp}
        if record["artifact_id"] in _record_map(current):
            raise LedgerError("IDENTITY_CONFLICT", "Artifact ID is already owned.")
        return [("artifact", record)], {}, [], record["artifact_id"]

    def _append(self, request, current, supplied, timestamp):
        body = request["body"]
        version = {**body["version"], "recorded_at": timestamp}
        existing = _record_map(current)
        artifact = existing.get(version["artifact_id"])
        if artifact is None or artifact[0] != "artifact":
            raise LedgerError("NOT_FOUND", "The Version's Artifact does not exist.")
        additions = [("version", version)]
        for record_kind in ("content_root", "blob", "manifest", "provenance_link"):
            collection, _ = KINDS[record_kind]
            for item in body[collection]:
                record = {**item, "recorded_at": timestamp} if record_kind == "provenance_link" else item
                if record_kind == "provenance_link" and record["subject_version_ref"] != version["version_id"]:
                    raise LedgerError("INVALID_INPUT", "Append provenance must have the new Version as its subject.")
                additions.append((record_kind, record))
        accepted = []
        seen = set()
        for record_kind, record in additions:
            identity = record[KINDS[record_kind][1]]
            if identity in seen:
                raise LedgerError("INVALID_INPUT", "Owned ID is repeated within the append request.")
            seen.add(identity)
            previous = existing.get(identity)
            if previous is not None:
                if record_kind in ("version", "provenance_link") or previous != (record_kind, record):
                    raise LedgerError("IDENTITY_CONFLICT", "Owned ID has a different immutable record or cannot be reused.")
            else:
                accepted.append((record_kind, record))
        combined = {name: list(value) if isinstance(value, list) else value for name, value in current.items()}
        for record_kind, record in accepted:
            combined[KINDS[record_kind][0]].append(record)
        validate_metadata(combined)
        refs = content_blob_refs(combined, version)
        all_records = _record_map(combined)
        root = all_records[version["content_root_ref"]][1]
        for record in body["content_roots"]:
            if record["content_root_ref"] != version["content_root_ref"]:
                raise LedgerError("INVALID_INPUT", "Append includes an unrelated ContentRoot.")
        for record in body["manifests"]:
            if root["kind"] != "manifest" or record["manifest_ref"] != root["manifest_ref"]:
                raise LedgerError("INVALID_INPUT", "Append includes an unrelated Manifest.")
        if any(record["blob_ref"] not in refs for record in body["blobs"]) or not supplied.keys() <= refs:
            raise LedgerError("INVALID_INPUT", "Append includes content outside the new Version closure.")
        if sum(all_records[ref][1]["byte_length"] for ref in refs) > MAX_PAYLOAD:
            raise LedgerError("RESOURCE_LIMIT", "Version content closure exceeds 256 MiB.")
        new_payloads = {}
        for ref in refs:
            record = all_records[ref][1]
            if ref in supplied:
                check_payload(record, supplied[ref])
            if ref in existing:
                self._blob(record)
            elif ref in supplied:
                new_payloads[ref] = supplied[ref]
            else:
                raise LedgerError("INVALID_INPUT", "Every new Blob requires its immutable payload bytes.", details={"blob_ref": ref})
        return accepted, new_payloads, [], version["version_id"]

    @staticmethod
    def _history(metadata, artifact_id):
        versions = {record["version_id"]: record for record in metadata["versions"]
                    if record["artifact_id"] == artifact_id}
        provenance = {record["provenance_id"]: record for record in metadata["provenance_links"]
                      if record["subject_version_ref"] in versions}
        return versions, provenance

    def _import(self, request, current, incoming, supplied, timestamp):
        existing = _record_map(current)
        input_records = _record_map(incoming)
        receipt_id = request["body"]["import_receipt_id"]
        if receipt_id in existing or receipt_id in input_records:
            raise LedgerError("IDENTITY_CONFLICT", "A new ImportReceipt must use an unowned ID.")
        for artifact in incoming["artifacts"]:
            identity = artifact["artifact_id"]
            if identity in existing:
                if existing[identity] != ("artifact", artifact) or self._history(current, identity) != self._history(incoming, identity):
                    raise LedgerError("IDENTITY_CONFLICT", "Repeated Artifact requires exactly the same complete history.")
        additions = []
        for identity, (kind, record) in input_records.items():
            if identity in existing:
                if existing[identity] != (kind, record):
                    raise LedgerError("IDENTITY_CONFLICT", "Imported owned identity differs from its stored immutable record.")
            else:
                additions.append((kind, record))
        operations = {_tuple(record): record for record in current["idempotency_records"]}
        historical_ops = []
        for record in incoming["idempotency_records"]:
            identity = _tuple(record)
            if identity == _tuple(request):
                raise LedgerError("IDEMPOTENCY_CONFLICT", "Import identity is already used inside the incoming history.")
            if identity in operations:
                if operations[identity] != record:
                    raise LedgerError("IDEMPOTENCY_CONFLICT", "Imported successful operation differs from stored history.")
            else:
                historical_ops.append(record)
        new_payloads = {}
        for ref, data in supplied.items():
            if ref in existing:
                # Same immutable records still require reading the actual
                # persisted bytes. Import never repairs damaged old content.
                persisted = self._blob(existing[ref][1])
                if persisted != data:
                    raise LedgerError("INTEGRITY_FAILURE", "Imported and persisted Blob bytes disagree.")
            else:
                new_payloads[ref] = data
        receipt = {"import_receipt_id": receipt_id,
                   "imported_artifact_refs": sorted(record["artifact_id"] for record in incoming["artifacts"]),
                   "recorded_at": timestamp}
        additions.append(("import_receipt", receipt))
        combined = {name: list(value) if isinstance(value, list) else value for name, value in current.items()}
        for kind, record in additions:
            combined[KINDS[kind][0]].append(record)
        combined["idempotency_records"].extend(historical_ops)
        validate_metadata(combined)
        return additions, new_payloads, historical_ops, receipt_id


def initialize(path):
    """Create a new local Ledger without replacing any existing path."""
    try:
        return Ledger(SQLiteStore.connect(path, create=True))
    except Exception as error:
        raise _error(error, "not_applicable") from error


def open(path):
    """Open an existing, exactly compatible Ledger without migration."""
    try:
        return Ledger(SQLiteStore.connect(path))
    except Exception as error:
        raise _error(error, "not_applicable") from error
