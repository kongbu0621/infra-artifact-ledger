"""Independent B: closed A1 wire shapes, encoding and graph validation.

Implemented from docs/INTERFACE_PROFILE.md; no imports from implementation A.
"""
import datetime
import hashlib
import json
import re

PROFILE = 'bounded-local-v0.1'
VERSION = '0.1.0'
MIB = 1048576
DOCUMENT = 8 * MIB
BLOB = 64 * MIB
PAYLOAD = 256 * MIB
PACKAGE = 384 * MIB
SAFE_INT = 9007199254740991
MARKERS = {
    'contract_version': VERSION, 'contract_status': 'candidate',
    'schema_id': 'urn:code-driver-theory:artifact-ledger:v0.1:candidate',
    'theory_baseline': {'repository': 'kongbu0621/code-driver-theory',
                        'commit': '9e2bc8217f05d5b604e24fa56dad2525f35983f1'},
}
KINDS = {
    'artifact': ('artifacts', 'artifact_id'),
    'version': ('versions', 'version_id'),
    'content_root': ('content_roots', 'content_root_ref'),
    'blob': ('blobs', 'blob_ref'),
    'manifest': ('manifests', 'manifest_ref'),
    'provenance_link': ('provenance_links', 'provenance_id'),
    'import_receipt': ('import_receipts', 'import_receipt_id'),
}
RESULT = {'create_artifact': 'artifact', 'append_version': 'version',
          'import_bundle': 'import_receipt'}


class LedgerError(Exception):
    def __init__(self, code, message, commit_state='not_applicable', details=None):
        super().__init__(message)
        self.code, self.message, self.commit_state = code, message, commit_state
        self.details = details

    def envelope(self):
        result = dict(status='ERROR', code=self.code, message=self.message,
                      commit_state=self.commit_state)
        if self.details is not None:
            result['details'] = self.details
        return result


def fail(code, location):
    raise LedgerError(code, location)


def require(condition, location, code='INVALID_INPUT'):
    if not condition:
        fail(code, location)


def bounded(size, maximum, location):
    require(size <= maximum, location, 'RESOURCE_LIMIT')


def fields(value, required, optional=()):
    required, optional = set(required), set(optional)
    require(type(value) is dict and required <= value.keys()
            and value.keys() <= required | optional, 'object fields')


def string(value, minimum=0, maximum=None):
    require(type(value) is str, 'string type')
    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), 'Unicode scalar')
    require(len(value) >= minimum and (maximum is None or len(value) <= maximum),
            'string length')


def stable(value):
    string(value, 3, 256)
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*', value) is not None, 'owned ID')


def opaque(value):
    string(value, 1, 512)
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value), 'opaque reference')


def integer(value):
    require(type(value) is int and 0 <= value <= SAFE_INT, 'safe integer')


def digest(raw):
    return {'algorithm': 'sha256', 'value': hashlib.sha256(raw).hexdigest()}


def digest_shape(value):
    fields(value, ('algorithm', 'value'))
    require(value['algorithm'] == 'sha256' and type(value['value']) is str
            and re.fullmatch('[0-9a-f]{64}', value['value']) is not None, 'digest')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _preflight(text):
    # Bound parser nesting and value allocation before json.loads. Keys do not
    # count as scalar nodes; the JSON decoder remains the grammar authority.
    depth = nodes = i = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            i += 1
            while i < len(text):
                if text[i] == '\\':
                    i += 2
                elif text[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
            j = i
            while j < len(text) and text[j] in ' \t\r\n':
                j += 1
            if j == len(text) or text[j] != ':':
                nodes += 1
            bounded(nodes, 500000, 'JSON nodes')
            continue
        if c in '{[':
            depth += 1
            nodes += 1
            bounded(depth, 16, 'JSON depth')
        elif c in '}]':
            depth -= 1
        elif c in '-0123456789tfn':
            nodes += 1
            while i + 1 < len(text) and text[i + 1] not in ' \t\r\n,]}:':
                i += 1
        bounded(nodes, 500000, 'JSON nodes')
        i += 1


def parse(raw, maximum=DOCUMENT):
    require(type(raw) is bytes, 'immutable UTF-8 bytes required')
    bounded(len(raw), maximum, 'serialized JSON bytes')
    try:
        text = raw.decode('utf-8')
        _preflight(text)
        def pairs(items):
            result = {}
            for key, value in items:
                require(key not in result, 'duplicate JSON key')
                string(key)
                result[key] = value
            return result
        def number(token):
            # All numeric fields in this closed profile are safe integers.
            require(len(token) <= 17, 'safe integer token')
            value = int(token)
            integer(value)
            return value
        def forbidden(token):
            fail('INVALID_INPUT', 'non-integer JSON number')
        value = json.loads(text, object_pairs_hook=pairs, parse_int=number,
                           parse_float=forbidden, parse_constant=forbidden)
        pending = [value]
        while pending:
            item = pending.pop()
            if type(item) is str:
                string(item)
            elif type(item) is dict:
                pending.extend(item.values())
            elif type(item) is list:
                pending.extend(item)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise LedgerError('INVALID_INPUT', 'invalid strict UTF-8 JSON') from exc


def timestamp(value):
    string(value)
    require(re.fullmatch(r'\d{4}-\d\d-\d\d[Tt]\d\d:\d\d:\d\d(?:\.\d+)?(?:[Zz]|[+-]\d\d:\d\d)', value)
            is not None, 'RFC3339 time')
    try:
        datetime.datetime.fromisoformat(value.replace('z', 'Z').replace('Z', '+00:00'))
    except ValueError as exc:
        raise LedgerError('INVALID_INPUT', 'invalid calendar time') from exc
    if value[-1] not in 'zZ':
        require(int(value[-5:-3]) < 24 and int(value[-2:]) < 60, 'timezone offset')


def unique(values, validator):
    require(type(values) is list, 'array type')
    for item in values:
        validator(item)
    require(len(set(values)) == len(values), 'duplicate array member')


def qualified(value, noun):
    fields(value, ('namespace', noun + '_id', noun + '_version'))
    for name, maximum in (('namespace', 128), (noun + '_id', 128), (noun + '_version', 64)):
        string(value[name], 1, maximum)


def record(kind, value, *, new=False):
    require(kind in KINDS, 'record kind')
    id_key = KINDS[kind][1]
    base = {id_key}
    timed = kind in ('artifact', 'version', 'provenance_link', 'import_receipt')
    if timed and not new:
        base.add('recorded_at')
    shapes = {
        'artifact': {'namespace_ref', 'type_ref'},
        'version': {'artifact_id', 'content_root_ref', 'parent_version_refs',
                    'external_source_refs', 'capture_refs', 'handling_policy_refs'},
        'blob': {'digest', 'byte_length', 'payload_availability'},
        'manifest': {'digest', 'entries'},
        'provenance_link': {'subject_version_ref', 'relation_ref', 'object'},
        'import_receipt': {'imported_artifact_refs'},
    }
    if kind == 'content_root':
        require(type(value) is dict and value.get('kind') in ('blob', 'manifest'), 'content kind')
        shapes[kind] = {'kind', value['kind'] + '_ref'}
    fields(value, base | shapes[kind], {'principal_ref'} if kind == 'version' else ())
    stable(value[id_key])
    if timed and not new:
        timestamp(value['recorded_at'])
    if kind == 'artifact':
        opaque(value['namespace_ref'])
        qualified(value['type_ref'], 'type')
    elif kind == 'version':
        stable(value['artifact_id'])
        stable(value['content_root_ref'])
        unique(value['parent_version_refs'], stable)
        for name in ('external_source_refs', 'capture_refs', 'handling_policy_refs'):
            unique(value[name], opaque)
        if 'principal_ref' in value:
            opaque(value['principal_ref'])
    elif kind == 'content_root':
        stable(value[value['kind'] + '_ref'])
    elif kind == 'blob':
        digest_shape(value['digest'])
        integer(value['byte_length'])
        bounded(value['byte_length'], BLOB, 'blob bytes')
        require(value['payload_availability'] in ('available', 'erased'), 'availability')
        require(value['payload_availability'] == 'available', 'erased blob', 'UNSUPPORTED_PROFILE')
    elif kind == 'manifest':
        digest_shape(value['digest'])
        require(type(value['entries']) is list and len(value['entries']) > 0, 'manifest entries')
        keys = set()
        for entry in value['entries']:
            fields(entry, ('entry_key', 'blob_ref'))
            string(entry['entry_key'], 1, 512)
            stable(entry['blob_ref'])
            require(entry['entry_key'] not in keys, 'duplicate entry key')
            keys.add(entry['entry_key'])
        require(digest(encode({'entries': value['entries']})) == value['digest'],
                'manifest digest', 'INTEGRITY_FAILURE')
    elif kind == 'provenance_link':
        stable(value['subject_version_ref'])
        qualified(value['relation_ref'], 'relation')
        fields(value['object'], ('kind', 'ref'))
        require(value['object']['kind'] in ('artifact_version', 'external_source', 'capture',
                'principal', 'policy', 'runtime_ref', 'other'), 'provenance object kind')
        opaque(value['object']['ref'])
    elif kind == 'import_receipt':
        unique(value['imported_artifact_refs'], stable)


def operation_identity(value):
    opaque(value['idempotency_scope_ref'])
    require(type(value['operation_kind']) is str and value['operation_kind'] in RESULT, 'operation kind')
    string(value['idempotency_key'], 1, 256)
    return tuple(value[name] for name in ('idempotency_scope_ref', 'operation_kind', 'idempotency_key'))


def operation(value):
    fields(value, ('idempotency_scope_ref', 'operation_kind', 'idempotency_key',
                   'request_fingerprint', 'result_ref', 'recorded_at'))
    operation_identity(value)
    digest_shape(value['request_fingerprint'])
    stable(value['result_ref'])
    timestamp(value['recorded_at'])


def empty_metadata():
    return {**MARKERS, **{collection: [] for collection, _ in KINDS.values()},
            'idempotency_records': []}


def metadata(value):
    fields(value, set(MARKERS) | {name for name, _ in KINDS.values() if name != 'import_receipts'}
           | {'idempotency_records'}, {'import_receipts'})
    for key, expected in MARKERS.items():
        require(value[key] == expected, key, 'UNSUPPORTED_VERSION')
    objects = {}
    for kind, (collection, id_key) in KINDS.items():
        require(type(value.get(collection, [])) is list, collection)
        for item in value.get(collection, []):
            record(kind, item)
            identity = item[id_key]
            require(identity not in objects, 'duplicate owned ID', 'IDENTITY_CONFLICT')
            objects[identity] = (kind, item)
    operations = {}
    require(type(value['idempotency_records']) is list, 'idempotency records')
    for item in value['idempotency_records']:
        operation(item)
        triple = operation_identity(item)
        require(triple not in operations, 'duplicate operation', 'IDEMPOTENCY_CONFLICT')
        operations[triple] = item
    graph(objects, operations)
    return objects, operations


def graph(objects, operations):
    def target(identity, kind):
        require(identity in objects and objects[identity][0] == kind,
                'unresolved ' + kind + ': ' + identity, 'INTEGRITY_FAILURE')
        return objects[identity][1]
    used = set()
    indegrees, children = {}, {}
    for identity, (kind, value) in objects.items():
        if kind == 'version':
            target(value['artifact_id'], 'artifact')
            target(value['content_root_ref'], 'content_root')
            used.add(value['content_root_ref'])
            indegrees[identity] = len(value['parent_version_refs'])
            for parent in value['parent_version_refs']:
                require(target(parent, 'version')['artifact_id'] == value['artifact_id'],
                        'cross artifact parent', 'INTEGRITY_FAILURE')
                children.setdefault(parent, []).append(identity)
        elif kind == 'content_root':
            ref = value[value['kind'] + '_ref']
            target(ref, value['kind'])
            used.add(ref)
        elif kind == 'manifest':
            for entry in value['entries']:
                target(entry['blob_ref'], 'blob')
                used.add(entry['blob_ref'])
        elif kind == 'provenance_link':
            target(value['subject_version_ref'], 'version')
            if value['object']['kind'] == 'artifact_version':
                target(value['object']['ref'], 'version')
        elif kind == 'import_receipt':
            for ref in value['imported_artifact_refs']:
                target(ref, 'artifact')
    ready = [key for key, count in indegrees.items() if count == 0]
    visited = 0
    while ready:
        parent = ready.pop()
        visited += 1
        for child in children.get(parent, ()):
            indegrees[child] -= 1
            if indegrees[child] == 0:
                ready.append(child)
    require(visited == len(indegrees), 'parent cycle', 'INTEGRITY_FAILURE')
    for identity, (kind, _) in objects.items():
        if kind in ('content_root', 'manifest', 'blob'):
            require(identity in used, 'unreachable content: ' + identity, 'INTEGRITY_FAILURE')
    for item in operations.values():
        target(item['result_ref'], RESULT[item['operation_kind']])


def closure(objects, root_id):
    require(root_id in objects and objects[root_id][0] == 'content_root',
            'missing content root', 'INTEGRITY_FAILURE')
    root = objects[root_id][1]
    target_id = root[root['kind'] + '_ref']
    require(target_id in objects and objects[target_id][0] == root['kind'],
            'missing root target', 'INTEGRITY_FAILURE')
    identities = {root_id, target_id}
    blobs = {target_id}
    if root['kind'] == 'manifest':
        blobs = {entry['blob_ref'] for entry in objects[target_id][1]['entries']}
        identities |= blobs
    for blob_id in blobs:
        require(blob_id in objects and objects[blob_id][0] == 'blob',
                'missing blob', 'INTEGRITY_FAILURE')
    return identities, blobs


def check_bytes(record_value, raw):
    require(type(raw) is bytes and len(raw) == record_value['byte_length']
            and digest(raw) == record_value['digest'], 'blob bytes do not match', 'INTEGRITY_FAILURE')


def success_data(data):
    raw = encode({'status': 'OK', 'commit_state': 'not_applicable', 'data': data})
    bounded(len(raw) + 1, DOCUMENT, 'response bytes')
    # Valid separate writes can accumulate into an over-limit history. Apply
    # the same structural limits to the complete response, not only inputs.
    _preflight(raw.decode('utf-8'))
    return data
