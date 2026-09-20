"""Strict input, record and graph validation for bounded-local-v0.1."""

from collections import deque
from calendar import monthrange
import json
import math
import re

from .errors import LedgerError
from .fingerprint import digest_bytes, manifest_digest
from .records import CONTRACT_VERSION, KINDS, PROFILE, SCHEMA_ID, THEORY_BASELINE

MAX_JSON = 8 * 1024 * 1024
MAX_BLOB = 64 * 1024 * 1024
MAX_PAYLOAD = 256 * 1024 * 1024
MAX_PACKAGE = 384 * 1024 * 1024
MAX_DESCRIPTOR = 4096
MAX_DEPTH = 16
MAX_NODES = 500000
MAX_INTEGER = 9007199254740991
OPERATIONS = {"create_artifact", "append_version", "import_bundle"}
_STABLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TIME = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.[0-9]+)?(?:[Zz]|([+-])([0-9]{2}):([0-9]{2}))\Z"
)
_BARE_TOKEN = re.compile(r'[^\s\[\]{},:]+')


def _fail(location, reason, code="INVALID_INPUT"):
    raise LedgerError(code, f"{location}: {reason}", details={"location": location})


def _resource(location, reason):
    _fail(location, reason, "RESOURCE_LIMIT")


def _unicode(value, location):
    if type(value) is not str:
        _fail(location, "must be a string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        _fail(location, "must contain valid Unicode without lone surrogates")


def _string(value, location, minimum, maximum):
    _unicode(value, location)
    if not minimum <= len(value) <= maximum:
        _fail(location, f"string length must be {minimum}..{maximum}")


def validate_stable_id(value, label="StableId"):
    _unicode(value, label)
    if not _STABLE_ID.fullmatch(value):
        _fail(label, "must be an ASCII StableId of length 3..256")


def _opaque(value, location):
    _string(value, location, 1, 512)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail(location, "opaque references cannot contain control characters")


def _object(value, required, optional=(), location="object"):
    if type(value) is not dict:
        _fail(location, "must be an object")
    if any(type(key) is not str for key in value):
        _fail(location, "object keys must be strings")
    required = set(required)
    if required - value.keys():
        _fail(location, "missing fields: " + ", ".join(sorted(required - value.keys())))
    if value.keys() - required - set(optional):
        _fail(location, "contains undeclared fields")


def _array(value, location, validator=None, unique=False, nonempty=False):
    if type(value) is not list:
        _fail(location, "must be an array")
    if nonempty and not value:
        _fail(location, "must be nonempty")
    if validator:
        for index, item in enumerate(value):
            validator(item, f"{location}[{index}]")
    if unique and len(set(value)) != len(value):
        _fail(location, "duplicate values are not permitted")


def _integer(value, location, maximum=MAX_INTEGER):
    if type(value) is not int or value < 0:
        _fail(location, "must be a nonnegative integer token (not float, exponent or boolean)")
    if value > MAX_INTEGER:
        _resource(location, "exceeds the maximum exact integer")
    if value > maximum:
        _resource(location, f"exceeds the limit of {maximum}")


def _digest(value, location):
    _object(value, ("algorithm", "value"), location=location)
    if value["algorithm"] != "sha256":
        _fail(location, "digest algorithm must be sha256")
    _unicode(value["value"], location + ".value")
    if not _DIGEST.fullmatch(value["value"]):
        _fail(location, "digest value must contain 64 lowercase hexadecimal characters")


def _time(value, location):
    _unicode(value, location)
    match = _TIME.fullmatch(value)
    if not match:
        _fail(location, "must be an RFC 3339 date-time with a timezone")
    parts = match.groups()
    year, month, day, hour, minute, second = map(int, parts[:6])
    if not (1 <= month <= 12 and 1 <= day <= monthrange(year, month)[1]
            and hour <= 23 and minute <= 59 and second <= 60):
        _fail(location, "contains an invalid RFC 3339 date or time")
    offset = 0
    if parts[6]:
        offset_hour, offset_minute = int(parts[7]), int(parts[8])
        if offset_hour > 23 or offset_minute > 59:
            _fail(location, "contains an invalid RFC 3339 timezone offset")
        offset = (offset_hour * 60 + offset_minute) * (1 if parts[6] == "+" else -1)
    if second == 60:
        # RFC 3339 section 5.7: a leap second has to occur at the end of a
        # UTC month, shifted by the numeric offset. This checks the timestamp
        # structure, not the historical truth of an IERS announcement.
        day_shift, utc_minute = divmod(hour * 60 + minute - offset, 24 * 60)
        utc_day, utc_month, utc_year = day + day_shift, month, year
        if utc_day < 1:
            utc_month -= 1
            if utc_month < 1:
                utc_month, utc_year = 12, utc_year - 1
            utc_day = monthrange(utc_year, utc_month)[1]
        elif utc_day > monthrange(year, month)[1]:
            utc_day, utc_month = 1, utc_month + 1
            if utc_month > 12:
                utc_month, utc_year = 1, utc_year + 1
        if utc_minute != 1439 or utc_day != monthrange(utc_year, utc_month)[1]:
            _fail(location, "leap-second syntax must be at the end of a UTC month")


def _typed(value, location, relation=False):
    name = "relation" if relation else "type"
    fields = ("namespace", name + "_id", name + "_version")
    _object(value, fields, location=location)
    for key, limit in zip(fields, (128, 128, 64)):
        _string(value[key], location + "." + key, 1, limit)


def _version(value, expected, location, code="UNSUPPORTED_VERSION"):
    _unicode(value, location)
    if value != expected:
        _fail(location, "unsupported value", code)


def _scan_limits(text, label):
    """Bound containers/nodes before the recursive JSON decoder allocates them.

    Strings are skipped with str.find, keeping large base64 strings inexpensive.
    This scanner only enforces resource limits; json.loads validates all syntax.
    Object keys are not value nodes. A root container has depth one.
    """
    depth = nodes = index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \r\n\t,:":
            index += 1
            continue
        if char in "{[":
            depth += 1
            nodes += 1
            if depth > MAX_DEPTH:
                _resource(label, f"container depth exceeds {MAX_DEPTH}")
            index += 1
        elif char in "}]":
            depth -= 1
            index += 1
        elif char == '"':
            start = index + 1
            while True:
                end = text.find('"', start)
                if end < 0:
                    return  # The decoder supplies the syntax error.
                slash = end - 1
                while slash >= index and text[slash] == "\\":
                    slash -= 1
                if (end - slash - 1) % 2 == 0:
                    break
                start = end + 1
            index = end + 1
            following = index
            while following < length and text[following] in " \t\r\n":
                following += 1
            if following == length or text[following] != ":":
                nodes += 1
        else:
            match = _BARE_TOKEN.match(text, index)
            if not match:
                return
            index = match.end()
            nodes += 1
        if nodes > MAX_NODES:
            _resource(label, f"JSON node count exceeds {MAX_NODES}")


def parse_json(raw, limit=MAX_JSON, label="JSON"):
    if type(raw) is not bytes:
        _fail(label, "input must be immutable UTF-8 bytes")
    if len(raw) > limit:
        _resource(label, f"serialized byte length exceeds {limit}")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail(label, "UTF-8 BOM is not permitted")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        _fail(label, "input must be valid UTF-8")
    _scan_limits(text, label)

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail(label, "duplicate object key")
            result[key] = value
        return result

    def constant(value):
        _fail(label, "non-finite numbers are not permitted")

    def floating(value):
        result = float(value)
        if not math.isfinite(result):
            _fail(label, "non-finite numbers are not permitted")
        return result

    def integer(value):
        # Declared numeric fields cannot use larger values. Bound conversion
        # before Python's configurable integer-string digit limit is reached.
        if len(value.lstrip("-")) > 16:
            _resource(label, "integer token exceeds the exact-integer range")
        return int(value)

    try:
        result = json.loads(text, object_pairs_hook=object_pairs,
                            parse_constant=constant, parse_float=floating,
                            parse_int=integer)
    except (ValueError, RecursionError) as error:
        _fail(label, "invalid JSON syntax: " + str(error)[:160])
    pending = [result]
    while pending:
        item = pending.pop()
        if type(item) is str:
            _unicode(item, label)
        elif type(item) is dict:
            for key in item:
                _unicode(key, label)
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    return result


def validate_record(kind, record, *, new=False):
    """Validate one immutable record; new timed records omit recorded_at."""
    location = kind
    if kind == "artifact":
        required, optional = {"artifact_id", "namespace_ref", "type_ref", "recorded_at"}, set()
    elif kind == "version":
        required = {"version_id", "artifact_id", "content_root_ref", "parent_version_refs",
                    "external_source_refs", "capture_refs", "handling_policy_refs", "recorded_at"}
        optional = {"principal_ref"}
    elif kind == "content_root":
        if type(record) is not dict or record.get("kind") not in ("blob", "manifest"):
            _fail(location, "kind must be blob or manifest")
        required, optional = {"content_root_ref", "kind", record["kind"] + "_ref"}, set()
    elif kind == "blob":
        required, optional = {"blob_ref", "digest", "byte_length", "payload_availability"}, set()
    elif kind == "manifest":
        required, optional = {"manifest_ref", "digest", "entries"}, set()
    elif kind == "provenance_link":
        required = {"provenance_id", "subject_version_ref", "relation_ref", "object", "recorded_at"}
        optional = set()
    elif kind == "import_receipt":
        required, optional = {"import_receipt_id", "imported_artifact_refs", "recorded_at"}, set()
    elif kind == "idempotency_record":
        required = {"idempotency_scope_ref", "operation_kind", "idempotency_key",
                    "request_fingerprint", "result_ref", "recorded_at"}
        optional = set()
    else:
        _fail("kind", "unknown record kind")
    if new:
        required.discard("recorded_at")
    _object(record, required, optional, location)
    if "recorded_at" in required:
        _time(record["recorded_at"], location + ".recorded_at")
    if kind in KINDS:
        validate_stable_id(record[KINDS[kind][1]], location + "." + KINDS[kind][1])
    if kind == "artifact":
        _opaque(record["namespace_ref"], location + ".namespace_ref")
        _typed(record["type_ref"], location + ".type_ref")
    elif kind == "version":
        for field in ("artifact_id", "content_root_ref"):
            validate_stable_id(record[field], location + "." + field)
        _array(record["parent_version_refs"], location + ".parent_version_refs", validate_stable_id, True)
        for field in ("external_source_refs", "capture_refs", "handling_policy_refs"):
            _array(record[field], location + "." + field, _opaque, True)
        if "principal_ref" in record:
            _opaque(record["principal_ref"], location + ".principal_ref")
    elif kind == "content_root":
        validate_stable_id(record[record["kind"] + "_ref"], location + ".target")
    elif kind == "blob":
        _digest(record["digest"], location + ".digest")
        _integer(record["byte_length"], location + ".byte_length", MAX_BLOB)
        if record["payload_availability"] == "erased":
            _fail(location, "erased payloads are not supported by this profile", "UNSUPPORTED_PROFILE")
        if record["payload_availability"] != "available":
            _fail(location, "payload_availability must be available or erased")
    elif kind == "manifest":
        _digest(record["digest"], location + ".digest")
        _array(record["entries"], location + ".entries", nonempty=True)
        keys = set()
        for entry in record["entries"]:
            _object(entry, ("entry_key", "blob_ref"), location="manifest.entry")
            _string(entry["entry_key"], "manifest.entry.entry_key", 1, 512)
            validate_stable_id(entry["blob_ref"], "manifest.entry.blob_ref")
            if entry["entry_key"] in keys:
                _fail(location, "duplicate entry_key")
            keys.add(entry["entry_key"])
        if record["digest"] != manifest_digest(record["entries"]):
            _fail(location, "Manifest entries digest mismatch", "INTEGRITY_FAILURE")
    elif kind == "provenance_link":
        validate_stable_id(record["subject_version_ref"], location + ".subject_version_ref")
        _typed(record["relation_ref"], location + ".relation_ref", relation=True)
        _object(record["object"], ("kind", "ref"), location=location + ".object")
        if record["object"]["kind"] not in ("artifact_version", "external_source", "capture",
                                              "principal", "policy", "runtime_ref", "other"):
            _fail(location, "unsupported provenance object kind")
        _opaque(record["object"]["ref"], location + ".object.ref")
    elif kind == "import_receipt":
        _array(record["imported_artifact_refs"], location + ".imported_artifact_refs", validate_stable_id, True)
    elif kind == "idempotency_record":
        _operation_fields(record, location)
        _digest(record["request_fingerprint"], location + ".request_fingerprint")
        validate_stable_id(record["result_ref"], location + ".result_ref")
    return record


def _operation_fields(obj, location):
    _opaque(obj["idempotency_scope_ref"], location + ".idempotency_scope_ref")
    _string(obj["idempotency_key"], location + ".idempotency_key", 1, 256)
    if type(obj["operation_kind"]) is not str or obj["operation_kind"] not in OPERATIONS:
        _fail(location, "unknown operation_kind")


def validate_operation_query(obj):
    _object(obj, ("idempotency_scope_ref", "operation_kind", "idempotency_key"), location="operation")
    _operation_fields(obj, "operation")
    return obj


def validate_request(request):
    _object(request, ("profile", "contract_version", "operation_kind", "idempotency_scope_ref",
                      "idempotency_key", "body"), ("request_fingerprint",), "request")
    _version(request["profile"], PROFILE, "request.profile", "UNSUPPORTED_PROFILE")
    _version(request["contract_version"], CONTRACT_VERSION, "request.contract_version")
    _operation_fields(request, "request")
    if "request_fingerprint" in request:
        _digest(request["request_fingerprint"], "request.request_fingerprint")
    body = request["body"]
    if request["operation_kind"] == "create_artifact":
        _object(body, ("artifact",), location="body")
        validate_record("artifact", body["artifact"], new=True)
    elif request["operation_kind"] == "append_version":
        _object(body, ("version", "content_roots", "blobs", "manifests", "provenance_links"), location="body")
        validate_record("version", body["version"], new=True)
        for kind in ("content_root", "blob", "manifest", "provenance_link"):
            collection, _ = KINDS[kind]
            _array(body[collection], "body." + collection)
            for record in body[collection]:
                validate_record(kind, record, new=kind == "provenance_link")
                if kind == "provenance_link" and record["subject_version_ref"] != body["version"]["version_id"]:
                    _fail("body.provenance_links", "new provenance must belong to the new version", "INTEGRITY_FAILURE")
    else:
        _object(body, ("import_receipt_id",), location="body")
        validate_stable_id(body["import_receipt_id"], "body.import_receipt_id")
    return request


def _content_refs(roots, manifests, version):
    try:
        root = roots[version["content_root_ref"]]
        if root["kind"] == "blob":
            return {root["blob_ref"]}
        return {entry["blob_ref"] for entry in manifests[root["manifest_ref"]]["entries"]}
    except KeyError:
        _fail("version.content_root_ref", "content reference does not resolve", "INTEGRITY_FAILURE")


def content_blob_refs(metadata, version):
    roots = {record["content_root_ref"]: record for record in metadata["content_roots"]}
    manifests = {record["manifest_ref"]: record for record in metadata["manifests"]}
    return _content_refs(roots, manifests, version)


def validate_metadata(metadata, *, check_closure=True):
    required = {"contract_version", "contract_status", "schema_id", "theory_baseline", "idempotency_records"}
    required.update(collection for collection, _ in KINDS.values() if collection != "import_receipts")
    _object(metadata, required, ("import_receipts",), "metadata")
    _version(metadata["contract_version"], CONTRACT_VERSION, "metadata.contract_version")
    _version(metadata["contract_status"], "candidate", "metadata.contract_status")
    _version(metadata["schema_id"], SCHEMA_ID, "metadata.schema_id")
    _object(metadata["theory_baseline"], ("repository", "commit"), location="metadata.theory_baseline")
    for field, value in THEORY_BASELINE.items():
        _version(metadata["theory_baseline"][field], value, "metadata.theory_baseline." + field)
    owned = {}
    by_kind = {}
    for kind, (collection, id_field) in KINDS.items():
        records = metadata.get(collection, [])
        _array(records, collection)
        by_kind[kind] = {}
        for record in records:
            validate_record(kind, record)
            identity = record[id_field]
            if identity in owned:
                _fail(collection, "duplicate owned ID across record collections", "IDENTITY_CONFLICT")
            owned[identity] = kind
            by_kind[kind][identity] = record
    _array(metadata["idempotency_records"], "idempotency_records")
    operations = set()
    for record in metadata["idempotency_records"]:
        validate_record("idempotency_record", record)
        triple = tuple(record[field] for field in ("idempotency_scope_ref", "operation_kind", "idempotency_key"))
        if triple in operations:
            _fail("idempotency_records", "duplicate idempotency identity", "IDEMPOTENCY_CONFLICT")
        operations.add(triple)
    if not check_closure:
        return metadata

    def reference(kind, identity, location):
        if identity not in by_kind[kind]:
            _fail(location, f"reference does not resolve to {kind}", "INTEGRITY_FAILURE")
        return by_kind[kind][identity]

    used_roots, used_manifests, used_blobs = set(), set(), set()
    indegree = {}
    children = {identity: [] for identity in by_kind["version"]}
    for identity, record in by_kind["version"].items():
        reference("artifact", record["artifact_id"], "version.artifact_id")
        reference("content_root", record["content_root_ref"], "version.content_root_ref")
        used_roots.add(record["content_root_ref"])
        indegree[identity] = len(record["parent_version_refs"])
        for parent_ref in record["parent_version_refs"]:
            parent = reference("version", parent_ref, "version.parent_version_refs")
            if parent["artifact_id"] != record["artifact_id"]:
                _fail("version.parent_version_refs", "parent belongs to another Artifact", "INTEGRITY_FAILURE")
            children[parent_ref].append(identity)
    queue = deque(identity for identity, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        identity = queue.popleft()
        visited += 1
        for child in children[identity]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(indegree):
        _fail("versions", "version parent graph contains a cycle", "INTEGRITY_FAILURE")
    for record in by_kind["content_root"].values():
        kind = record["kind"]
        target = record[kind + "_ref"]
        reference(kind, target, "content_root." + kind + "_ref")
        (used_blobs if kind == "blob" else used_manifests).add(target)
    for record in by_kind["manifest"].values():
        for entry in record["entries"]:
            reference("blob", entry["blob_ref"], "manifest.entry.blob_ref")
            used_blobs.add(entry["blob_ref"])
    for kind, used in (("content_root", used_roots), ("manifest", used_manifests), ("blob", used_blobs)):
        if set(by_kind[kind]) != used:
            _fail(KINDS[kind][0], "contains unreachable content records", "INTEGRITY_FAILURE")
    for record in by_kind["version"].values():
        refs = _content_refs(by_kind["content_root"], by_kind["manifest"], record)
        if sum(by_kind["blob"][ref]["byte_length"] for ref in refs) > MAX_PAYLOAD:
            _resource("version.content_root_ref", f"Version content closure exceeds {MAX_PAYLOAD}")
    for record in by_kind["provenance_link"].values():
        reference("version", record["subject_version_ref"], "provenance_link.subject_version_ref")
        if record["object"]["kind"] == "artifact_version":
            reference("version", record["object"]["ref"], "provenance_link.object.ref")
    for record in by_kind["import_receipt"].values():
        for identity in record["imported_artifact_refs"]:
            reference("artifact", identity, "import_receipt.imported_artifact_refs")
    result_kinds = {"create_artifact": "artifact", "append_version": "version", "import_bundle": "import_receipt"}
    for record in metadata["idempotency_records"]:
        reference(result_kinds[record["operation_kind"]], record["result_ref"], "idempotency_record.result_ref")
    return metadata


def check_payload(blob, data):
    validate_record("blob", blob)
    if type(data) is not bytes:
        _fail("payload", "content must be immutable bytes")
    if len(data) > MAX_BLOB:
        _resource("payload", f"Blob content exceeds {MAX_BLOB}")
    if len(data) != blob["byte_length"] or digest_bytes(data) != blob["digest"]:
        _fail("payload." + blob["blob_ref"], "content length or digest mismatch", "INTEGRITY_FAILURE")
