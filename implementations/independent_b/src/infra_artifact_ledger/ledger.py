"""Independent B A1 operations over a private object-row SQLite format."""
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import sqlite3

from . import contract as c
from . import storage, transport


def _read(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        try:
            with self._view():
                return method(self, *args, **kwargs)
        except c.LedgerError as exc:
            exc.commit_state = 'not_applicable'
            raise
        except sqlite3.Error as exc:
            raise c.LedgerError(storage.read_error_code(exc), 'database read failed') from exc
        except (OSError, ValueError) as exc:
            raise c.LedgerError('IO_ERROR', 'read failed') from exc
    return call


def _busy(exc):
    return (getattr(exc, 'sqlite_errorcode', 0) & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _response(op, replayed):
    return dict(status='COMMITTED', commit_state='committed',
                operation_kind=op['operation_kind'], result_ref=op['result_ref'],
                recorded_at=op['recorded_at'], replayed=replayed)


def _identity_details(op):
    return {key: op[key] for key in ('result_ref', 'idempotency_scope_ref',
            'operation_kind', 'idempotency_key', 'recorded_at')}


class Ledger:
    def __init__(self, connection):
        self._db = connection

    def close(self):
        try:
            self._db.close()
        except sqlite3.Error as exc:
            raise c.LedgerError('IO_ERROR', 'database close failed') from exc

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.close()
        except Exception:
            # Preserve the operation's error, especially committed/unknown
            # outcomes. A standalone close failure must still reach the caller.
            if kind is None:
                raise

    @contextmanager
    def _view(self):
        self._db.execute('BEGIN')
        try:
            yield
        except BaseException:
            try:
                self._db.execute('ROLLBACK')
            except Exception:
                # A read's primary error must survive failed cleanup, including
                # when the storage layer has already ended the transaction.
                pass
            raise
        else:
            self._db.execute('ROLLBACK')

    def _objects(self):
        objects = {}
        for identity, kind, document in self._db.execute('SELECT owned_id, kind, document FROM b_objects'):
            try:
                value = c.parse(document)
                c.record(kind, value)
                c.require(value[c.KINDS[kind][1]] == identity, 'stored identity mismatch', 'INTEGRITY_FAILURE')
                objects[identity] = (kind, value)
            except c.LedgerError as exc:
                raise c.LedgerError('INTEGRITY_FAILURE', 'invalid stored object: ' + str(identity)) from exc
        return objects

    def _operations(self):
        operations = {}
        for scope, kind, key, document in self._db.execute('SELECT scope, kind, request_key, document FROM b_operations'):
            try:
                value = c.parse(document)
                c.operation(value)
                triple = c.operation_identity(value)
                c.require(triple == (scope, kind, key), 'stored operation identity', 'INTEGRITY_FAILURE')
                operations[triple] = value
            except c.LedgerError as exc:
                raise c.LedgerError('INTEGRITY_FAILURE', 'invalid stored operation') from exc
        return operations

    def _record(self, kind, identity):
        c.require(type(kind) is str and kind in c.KINDS, 'record kind')
        c.stable(identity)
        row = self._db.execute('SELECT kind, document FROM b_objects WHERE owned_id=?', (identity,)).fetchone()
        c.require(row is not None and row[0] == kind, 'record not found', 'NOT_FOUND')
        try:
            result = c.parse(row[1])
            c.record(kind, result)
            c.require(result[c.KINDS[kind][1]] == identity, 'stored object identity', 'INTEGRITY_FAILURE')
            return result
        except c.LedgerError as exc:
            raise c.LedgerError('INTEGRITY_FAILURE', 'invalid stored record') from exc

    def _blob(self, identity):
        record = self._record('blob', identity)
        size, data_type = self._db.execute('SELECT length(content), typeof(content) FROM b_objects WHERE owned_id=?', (identity,)).fetchone()
        c.require(data_type == 'blob' and size == record['byte_length'],
                  'stored payload type or length', 'INTEGRITY_FAILURE')
        raw = self._db.execute('SELECT content FROM b_objects WHERE owned_id=?', (identity,)).fetchone()[0]
        c.check_bytes(record, raw)
        return raw

    @_read
    def get_record(self, kind, id):
        return c.success_data(self._record(kind, id))

    @_read
    def get_history(self, artifact_id):
        artifact = self._record('artifact', artifact_id)
        objects = self._objects()
        return c.success_data(_history(objects, artifact_id, artifact))

    @_read
    def get_operation(self, scope, kind, key):
        query = dict(idempotency_scope_ref=scope, operation_kind=kind, idempotency_key=key)
        triple = c.operation_identity(query)
        row = self._db.execute('SELECT document FROM b_operations WHERE scope=? AND kind=? AND request_key=?', triple).fetchone()
        c.require(row is not None, 'operation not found', 'NOT_FOUND')
        try:
            op = c.parse(row[0])
            c.operation(op)
            c.require(c.operation_identity(op) == triple, 'stored operation identity', 'INTEGRITY_FAILURE')
        except c.LedgerError as exc:
            raise c.LedgerError('INTEGRITY_FAILURE', 'invalid stored operation') from exc
        return c.success_data(dict(idempotency_record=op, result=self._record(c.RESULT[kind], op['result_ref'])))

    @_read
    def read_blob(self, blob_ref):
        return self._blob(blob_ref)

    def _verify(self):
        failures = []
        count = 0
        def note(location, reason):
            nonlocal count
            count += 1
            if len(failures) < 100:
                failures.append(dict(location=location, reason=reason))
        objects, operations = {}, {}
        try:
            objects = self._objects()
            operations = self._operations()
            c.graph(objects, operations)
        except c.LedgerError as exc:
            note('metadata', exc.message)
        # Always visit every stored blob, including after the report fills up.
        verified_count = verified_bytes = 0
        for identity, in self._db.execute("SELECT owned_id FROM b_objects WHERE kind='blob'"):
            try:
                raw = self._blob(identity)
                verified_count += 1
                verified_bytes += len(raw)
                c.bounded(verified_bytes, c.SAFE_INT, 'verified bytes')
            except c.LedgerError as exc:
                note(str(identity), exc.message)
        if failures:
            raise c.LedgerError('INTEGRITY_FAILURE', 'ledger verification failed',
                               details=dict(failures=failures, truncated=count > 100))
        counts = {collection: sum(kind == expected for kind, _ in objects.values())
                  for expected, (collection, _) in c.KINDS.items()}
        counts['idempotency_records'] = len(operations)
        return dict(counts=counts, verified_blob_count=verified_count, verified_byte_length=verified_bytes)

    @_read
    def verify(self):
        return c.success_data(self._verify())

    @_read
    def export_bundle(self):
        self._verify()
        objects, operations = self._objects(), self._operations()
        meta = c.empty_metadata()
        for identity in sorted(objects):
            kind, value = objects[identity]
            meta[c.KINDS[kind][0]].append(value)
        meta['idempotency_records'] = [operations[key] for key in sorted(operations)]
        return transport.pack(meta, self._blob)

    def execute(self, request_utf8, *, payloads=None, package=None, descriptor=None):
        started = attempted_commit = committed = False
        original = None
        replay_content_failed = False
        try:
            request = c.parse(request_utf8)
            c.fields(request, ('profile', 'contract_version', 'operation_kind',
                               'idempotency_scope_ref', 'idempotency_key', 'body'), {'request_fingerprint'})
            c.require(request['profile'] == c.PROFILE, 'profile', 'UNSUPPORTED_PROFILE')
            c.require(request['contract_version'] == c.VERSION, 'contract version', 'UNSUPPORTED_VERSION')
            triple = c.operation_identity(request)
            kind = request['operation_kind']
            body = request['body']
            incoming, incoming_ops, frozen = {}, {}, {}
            if kind == 'create_artifact':
                c.require(payloads is None and package is None and descriptor is None, 'create arguments')
                c.fields(body, ('artifact',))
                c.record('artifact', body['artifact'], new=True)
            elif kind == 'append_version':
                c.require(isinstance(payloads, Mapping) and package is None and descriptor is None, 'append arguments')
                c.fields(body, ('version', 'content_roots', 'blobs', 'manifests', 'provenance_links'))
                c.record('version', body['version'], new=True)
                total = 0
                for identity, raw in payloads.items():
                    c.stable(identity)
                    c.require(type(raw) is bytes and identity not in frozen, 'immutable payload bytes')
                    c.bounded(len(raw), c.BLOB, 'blob bytes')
                    total += len(raw)
                    c.bounded(total, c.PAYLOAD, 'input payload bytes')
                    frozen[identity] = raw
                for object_kind in ('content_root', 'blob', 'manifest', 'provenance_link'):
                    collection, id_key = c.KINDS[object_kind]
                    c.require(type(body[collection]) is list, collection)
                    for item in body[collection]:
                        c.record(object_kind, item, new=object_kind == 'provenance_link')
                        c.require(item[id_key] not in incoming, 'duplicate append ID', 'IDENTITY_CONFLICT')
                        incoming[item[id_key]] = (object_kind, item)
                c.require(body['version']['version_id'] not in incoming, 'version ID collision', 'IDENTITY_CONFLICT')
                for identity, raw in frozen.items():
                    if identity in incoming:
                        c.require(incoming[identity][0] == 'blob', 'payload identity type')
                        c.check_bytes(incoming[identity][1], raw)
            else:
                c.require(payloads is None and package is not None and descriptor is not None, 'import arguments')
                c.fields(body, ('import_receipt_id',))
                c.stable(body['import_receipt_id'])
                meta, incoming, incoming_ops, frozen = transport.unpack(package, descriptor)
                body = dict(import_receipt_id=body['import_receipt_id'], bundle=meta)
            fingerprint = c.digest(c.encode(dict(contract_version=c.VERSION,
                idempotency_scope_ref=request['idempotency_scope_ref'], operation_kind=kind, body=body)))
            if 'request_fingerprint' in request:
                c.digest_shape(request['request_fingerprint'])
                c.require(request['request_fingerprint'] == fingerprint, 'request fingerprint', 'INTEGRITY_FAILURE')
            self._db.execute('BEGIN IMMEDIATE')
            started = True
            objects, operations = self._objects(), self._operations()
            c.graph(objects, operations)
            if triple in operations:
                original = operations[triple]
                c.require(original['request_fingerprint'] == fingerprint,
                          'key bound to another request', 'IDEMPOTENCY_CONFLICT')
                # A successful receipt replays before complete-history comparison:
                # the destination may legitimately have appended since import.
                if kind == 'append_version':
                    _, blob_ids = c.closure(objects, body['version']['content_root_ref'])
                elif kind == 'import_bundle':
                    blob_ids = set(frozen)
                else:
                    blob_ids = set()
                c.require(frozen.keys() <= blob_ids, 'extra payload')
                for identity in blob_ids:
                    if identity in frozen:
                        c.check_bytes(objects[identity][1], frozen[identity])
                    try:
                        self._blob(identity)
                    except c.LedgerError as failure:
                        replay_content_failed = failure.code == 'INTEGRITY_FAILURE'
                        raise
                self._db.execute('ROLLBACK')
                started = False
                return _response(original, True)
            original = None
            now = _now()
            additions, history_ops = {}, {}
            if kind == 'create_artifact':
                item = {**body['artifact'], 'recorded_at': now}
                result_id = item['artifact_id']
                c.require(result_id not in objects, 'artifact ID exists', 'IDENTITY_CONFLICT')
                additions[result_id] = ('artifact', item)
            elif kind == 'append_version':
                item = {**body['version'], 'recorded_at': now}
                result_id = item['version_id']
                c.require(result_id not in objects, 'version ID exists', 'IDENTITY_CONFLICT')
                c.require(item['artifact_id'] in objects and objects[item['artifact_id']][0] == 'artifact',
                          'artifact not found', 'NOT_FOUND')
                additions[result_id] = ('version', item)
                for identity, (object_kind, value) in incoming.items():
                    if object_kind == 'provenance_link':
                        c.require(identity not in objects, 'provenance ID exists', 'IDENTITY_CONFLICT')
                        c.require(value['subject_version_ref'] == result_id, 'provenance subject')
                        value = {**value, 'recorded_at': now}
                    self._converge(objects, identity, object_kind, value)
                    additions[identity] = (object_kind, value)
                combined = {**objects, **additions}
                used, blob_ids = c.closure(combined, item['content_root_ref'])
                c.require(all(identity in used for identity, (k, _) in incoming.items()
                              if k != 'provenance_link'), 'unrelated content')
                c.require(frozen.keys() <= blob_ids, 'extra payload')
                c.bounded(sum(combined[identity][1]['byte_length'] for identity in blob_ids),
                          c.PAYLOAD, 'version closure bytes')
                for identity in blob_ids:
                    if identity in objects:
                        self._blob(identity)
                    else:
                        c.require(identity in frozen, 'new blob bytes missing', 'INTEGRITY_FAILURE')
                    if identity in frozen:
                        c.check_bytes(combined[identity][1], frozen[identity])
            else:
                result_id = body['import_receipt_id']
                c.require(result_id not in objects and result_id not in incoming,
                          'receipt ID exists', 'IDENTITY_CONFLICT')
                c.require(triple not in incoming_ops, 'import claims historical key', 'IDEMPOTENCY_CONFLICT')
                for identity, (object_kind, item) in incoming.items():
                    self._converge(objects, identity, object_kind, item)
                    if object_kind == 'artifact' and identity in objects:
                        c.require(_history(objects, identity, item) == _history(incoming, identity, item),
                                  'complete artifact history differs', 'IDENTITY_CONFLICT')
                    if object_kind == 'blob' and identity in objects:
                        self._blob(identity)
                for key, value in incoming_ops.items():
                    c.require(key not in operations or operations[key] == value,
                              'historical operation differs', 'IDEMPOTENCY_CONFLICT')
                additions.update(incoming)
                history_ops.update(incoming_ops)
                receipt = dict(import_receipt_id=result_id, recorded_at=now,
                               imported_artifact_refs=sorted(identity for identity, (k, _) in incoming.items() if k == 'artifact'))
                additions[result_id] = ('import_receipt', receipt)
            op = {name: request[name] for name in ('idempotency_scope_ref', 'operation_kind', 'idempotency_key')}
            op.update(request_fingerprint=fingerprint, result_ref=result_id, recorded_at=now)
            c.graph({**objects, **additions}, {**operations, **history_ops, triple: op})
            for identity, (object_kind, item) in additions.items():
                if identity not in objects:
                    self._db.execute('INSERT INTO b_objects VALUES (?,?,?,?)',
                        (identity, object_kind, c.encode(item), frozen.get(identity) if object_kind == 'blob' else None))
            for key, item in {**history_ops, triple: op}.items():
                if key not in operations:
                    self._db.execute('INSERT INTO b_operations VALUES (?,?,?,?)', (*key, c.encode(item)))
            response = _response(op, False)
            attempted_commit = True
            self._db.execute('COMMIT')
            committed = True
            started = False
            return response
        except BaseException as exc:
            state = 'not_committed'
            details = None
            if committed:
                state, details = 'committed', _identity_details(op)
            elif started:
                try:
                    if not self._db.in_transaction:
                        raise RuntimeError('transaction outcome unavailable')
                    self._db.execute('ROLLBACK')
                    if self._db.in_transaction:
                        raise RuntimeError('rollback incomplete')
                except Exception as rollback_error:
                    raise c.LedgerError('DURABILITY_UNKNOWN', 'cannot confirm transaction rollback', 'unknown') from rollback_error
            elif attempted_commit:
                raise c.LedgerError('DURABILITY_UNKNOWN', 'commit outcome unavailable', 'unknown') from exc
            if not isinstance(exc, Exception):
                raise
            if original is not None and replay_content_failed:
                state, details = 'committed', _identity_details(original)
            if isinstance(exc, c.LedgerError):
                exc.commit_state = state
                if details is not None:
                    exc.details = details
                raise
            if isinstance(exc, sqlite3.Error):
                code = 'BUSY' if _busy(exc) and state == 'not_committed' else 'IO_ERROR'
            else:
                code = 'IO_ERROR' if isinstance(exc, OSError) else 'INTERNAL_ERROR'
            raise c.LedgerError(code, 'ledger operation failed', state, details) from exc

    @staticmethod
    def _converge(objects, identity, kind, value):
        c.require(identity not in objects or objects[identity] == (kind, value),
                  'owned ID has different immutable record', 'IDENTITY_CONFLICT')


def _history(objects, artifact_id, artifact):
    versions = [value for identity, (kind, value) in sorted(objects.items())
                if kind == 'version' and value['artifact_id'] == artifact_id]
    ids = {value['version_id'] for value in versions}
    provenance = [value for identity, (kind, value) in sorted(objects.items())
                  if kind == 'provenance_link' and value['subject_version_ref'] in ids]
    return dict(artifact=artifact, versions=versions, provenance_links=provenance)


def initialize(path):
    return Ledger(storage.initialize_connection(path))


def open(path):
    return Ledger(storage.open_connection(path))
