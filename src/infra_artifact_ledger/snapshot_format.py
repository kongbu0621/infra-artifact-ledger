"""Bounded A2 JSON formats; hashes always cover the original serialized bytes."""

from datetime import datetime, timezone
from importlib import metadata
import json
import math
from pathlib import Path
import posixpath
import re
import tomllib

from .snapshot_common import RecoveryError, operation_budget

MAX_DATABASE = 1024 * 1024 * 1024
MAX_MANIFEST = 64 * 1024
MAX_MARKER = 4 * 1024
MAX_STORAGE_CONFIG = 16 * 1024
MAX_RESPONSE = 64 * 1024
MAX_RECORD_ROWS = 50_000
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_REF_ROWS = 250_000
MAX_REF_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_NODES = 1024
MAX_DEPTH = 8
COPY_CHUNK = 1024 * 1024
BACKUP_PAGES = 256
MAX_BLOB = 64 * 1024 * 1024

SNAPSHOT_FORMAT = "infra-artifact-ledger-snapshot/v1"
MARKER_FORMAT = "infra-artifact-ledger-snapshot-commit/v1"
STORAGE_FORMAT = "infra-artifact-ledger-storage/v1"
COUNTS = (
    "artifacts", "versions", "content_roots", "blobs", "manifests",
    "provenance_links", "import_receipts", "idempotency_records",
)
_TOKEN = re.compile(r'[^\s\[\]{},:]+')


def _fail(message, stage, code="INVALID_INPUT"):
    raise RecoveryError(code, message, stage=stage)


def _string(value, stage):
    if type(value) is not str:
        _fail("A string is required.", stage)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        _fail("Strings must contain valid Unicode without lone surrogates.", stage)
    return value


def _walk(value, limit, stage, budget):
    """Check Python input iteratively, before recursive JSON encoding.

    Value nodes include containers and scalars; object keys are not nodes.
    Shared acyclic containers are allowed, cycles are not.
    """
    active = set()
    stack = [(value, 0, False)]
    nodes = 0
    while stack:
        budget.check(stage)
        item, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(item))
            continue
        nodes += 1
        if nodes > MAX_NODES:
            _fail("A2 JSON node count exceeds 1024.", stage, "RESOURCE_LIMIT")
        kind = type(item)
        if kind in (dict, list):
            if depth + 1 > MAX_DEPTH:
                _fail("A2 JSON container depth exceeds 8.", stage, "RESOURCE_LIMIT")
            if id(item) in active:
                _fail("Cyclic values are not JSON.", stage)
            active.add(id(item))
            stack.append((item, depth, True))
            # Reject wide objects without materializing their children first.
            if len(item) > MAX_NODES - nodes:
                _fail("A2 JSON node count exceeds 1024.", stage, "RESOURCE_LIMIT")
            if kind is dict:
                for key, child in item.items():
                    if type(key) is str and len(key) > limit:
                        _fail("A2 JSON byte budget exceeded.", stage, "RESOURCE_LIMIT")
                    _string(key, stage)
                    stack.append((child, depth + 1, False))
            else:
                stack.extend((child, depth + 1, False) for child in item)
        elif kind is str:
            if len(item) > limit:
                _fail("A2 JSON byte budget exceeded.", stage, "RESOURCE_LIMIT")
            _string(item, stage)
        elif kind is float:
            if not math.isfinite(item):
                _fail("Non-finite JSON numbers are not permitted.", stage)
        elif kind not in (int, bool, type(None)):
            _fail("Only built-in JSON value types are accepted.", stage)


def encode(value, limit=MAX_MANIFEST, *, stage="validate"):
    """Return deterministic strict UTF-8 JSON without LF, subject to A2 limits."""
    with operation_budget() as budget:
        return _encode(value, limit, stage, budget)


def _encode(value, limit, stage, budget):
    _walk(value, limit, stage, budget)
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
    chunks = []
    size = 0
    try:
        for chunk in encoder.iterencode(value):
            budget.check(stage)
            raw = chunk.encode("utf-8", errors="strict")
            size += len(raw)
            if size > limit:
                _fail("A2 JSON byte budget exceeded.", stage, "RESOURCE_LIMIT")
            chunks.append(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("Value cannot be encoded as strict A2 JSON.", stage)
    return b"".join(chunks)


def _scan(text, stage, budget):
    """Bound containers/nodes before json.loads allocates a recursive graph."""
    index = depth = nodes = 0
    while index < len(text):
        budget.check(stage)
        char = text[index]
        if char in " \r\n\t,:":
            index += 1
            continue
        if char in "[{":
            nodes += 1
            depth += 1
            if depth > MAX_DEPTH:
                _fail("A2 JSON container depth exceeds 8.", stage, "RESOURCE_LIMIT")
            index += 1
        elif char in "]}":
            depth -= 1
            index += 1
        elif char == '"':
            start = index + 1
            while True:
                end = text.find('"', start)
                if end < 0:
                    return  # The JSON decoder reports invalid syntax.
                slash = end - 1
                while slash >= index and text[slash] == "\\":
                    slash -= 1
                if (end - slash - 1) % 2 == 0:
                    break
                start = end + 1
            index = end + 1
            following = index
            while following < len(text) and text[following] in " \r\n\t":
                following += 1
            if following == len(text) or text[following] != ":":
                nodes += 1
        else:
            token = _TOKEN.match(text, index)
            if token is None:
                return
            index = token.end()
            nodes += 1
        if nodes > MAX_NODES:
            _fail("A2 JSON node count exceeds 1024.", stage, "RESOURCE_LIMIT")


def parse_json(raw, limit=MAX_MANIFEST, *, stage="validate"):
    """Parse original bytes, refusing duplicate keys, BOM and non-finite numbers."""
    with operation_budget() as budget:
        return _parse_json(raw, limit, stage, budget)


def _parse_json(raw, limit, stage, budget):
    if type(raw) is not bytes:
        _fail("A2 JSON input must be immutable UTF-8 bytes.", stage)
    if len(raw) > limit:
        _fail("A2 JSON byte budget exceeded.", stage, "RESOURCE_LIMIT")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("UTF-8 BOM is not permitted.", stage)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        _fail("A2 JSON must be valid UTF-8.", stage)
    _scan(text, stage, budget)

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                _fail("Duplicate JSON object keys are not permitted.", stage)
            result[key] = item
        return result

    def constant(_):
        _fail("Non-finite JSON numbers are not permitted.", stage)

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, RecursionError):
        _fail("Invalid A2 JSON syntax or numeric value.", stage)
    _walk(value, limit, stage, budget)
    return value


def _hex(value, count, name, stage):
    if type(value) is not str or len(value) != count or not re.fullmatch("[0-9a-f]{" + str(count) + "}", value):
        _fail(name + " must be lowercase hexadecimal with the prescribed length.", stage)
    return value


def validate_snapshot_id(value, *, stage="validate"):
    return _hex(value, 32, "snapshot_id", stage)


def validate_sha256(value, *, stage="validate"):
    return _hex(value, 64, "SHA-256", stage)


def validate_source_commit(value, *, stage="validate"):
    return _hex(value, 40, "source_commit", stage)


def _object(value, fields, stage):
    if type(value) is not dict:
        _fail("An ordinary JSON object with string keys is required.", stage)
    if len(value) != len(fields):
        _fail("Object fields do not match the A2 format.", stage)
    if any(type(key) is not str for key in value):
        _fail("An ordinary JSON object with string keys is required.", stage)
    if value.keys() != set(fields):
        _fail("Object fields do not match the A2 format.", stage)


def _fixed(value, expected, stage):
    if type(value) is not type(expected):
        _fail("A format discriminator has the wrong type.", stage)
    if value != expected:
        _fail("The A2 format or profile is not supported.", stage, "UNSUPPORTED_FORMAT")


def _integer(value, maximum, stage, minimum=0):
    if type(value) is not int or value < minimum:
        _fail("An integer token in the declared range is required; bool and float are invalid.", stage)
    if value > maximum:
        _fail("A2 count or byte budget exceeded.", stage, "RESOURCE_LIMIT")


def validate_summary(value, database_size=MAX_DATABASE, *, stage="validate"):
    _object(value, ("counts", "verified_blob_count", "verified_byte_length"), stage)
    counts = value["counts"]
    _object(counts, COUNTS, stage)
    for count in counts.values():
        _integer(count, MAX_RECORD_ROWS, stage)
    if sum(counts.values()) > MAX_RECORD_ROWS:
        _fail("A2 records plus operations exceed 50000 rows.", stage, "RESOURCE_LIMIT")
    _integer(value["verified_blob_count"], MAX_RECORD_ROWS, stage)
    _integer(value["verified_byte_length"], MAX_DATABASE, stage)
    if value["verified_blob_count"] != counts["blobs"]:
        _fail("Verified Blob count must equal the complete Blob count.", stage, "INTEGRITY_FAILURE")
    if value["verified_byte_length"] > min(database_size, counts["blobs"] * MAX_BLOB):
        _fail("Verified Blob bytes are inconsistent with the file or Blob counts.", stage, "INTEGRITY_FAILURE")
    return value


def validate_manifest(value, snapshot_id=None, *, stage="validate"):
    encode(value, MAX_MANIFEST, stage=stage)
    _object(value, ("format", "snapshot_id", "created_at", "backend", "schema_version",
                    "data_profile", "journal_mode", "text_encoding", "producer",
                    "database", "summary"), stage)
    for name, expected in (("format", SNAPSHOT_FORMAT), ("backend", "sqlite"),
                           ("schema_version", 1), ("data_profile", "bounded-local-v0.1"),
                           ("journal_mode", "delete"), ("text_encoding", "UTF-8")):
        _fixed(value[name], expected, stage)
    validate_snapshot_id(value["snapshot_id"], stage=stage)
    if snapshot_id is not None:
        validate_snapshot_id(snapshot_id, stage=stage)
        if value["snapshot_id"] != snapshot_id:
            _fail("Manifest snapshot_id does not match its directory.", stage, "INTEGRITY_FAILURE")
    timestamp = _string(value["created_at"], stage)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", timestamp):
        _fail("created_at must be a UTC YYYY-MM-DDTHH:MM:SSZ timestamp.", stage)
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail("created_at contains an invalid date or time.", stage)
    producer = value["producer"]
    _object(producer, ("package_version", "source_commit"), stage)
    version = _string(producer["package_version"], stage)
    if not (1 <= len(version) <= 64 and version.isascii()):
        _fail("package_version must contain 1 to 64 ASCII characters.", stage)
    validate_source_commit(producer["source_commit"], stage=stage)
    database = value["database"]
    _object(database, ("name", "byte_length", "sha256"), stage)
    if type(database["name"]) is not str or database["name"] != "ledger.sqlite":
        _fail("The database member must be ledger.sqlite.", stage)
    _integer(database["byte_length"], MAX_DATABASE, stage, minimum=1)
    validate_sha256(database["sha256"], stage=stage)
    validate_summary(value["summary"], database["byte_length"], stage=stage)
    return value


def validate_marker(value, snapshot_id=None, *, stage="validate"):
    encode(value, MAX_MARKER, stage=stage)
    _object(value, ("format", "snapshot_id", "manifest_sha256"), stage)
    _fixed(value["format"], MARKER_FORMAT, stage)
    validate_snapshot_id(value["snapshot_id"], stage=stage)
    validate_sha256(value["manifest_sha256"], stage=stage)
    if snapshot_id is not None:
        validate_snapshot_id(snapshot_id, stage=stage)
        if value["snapshot_id"] != snapshot_id:
            _fail("Marker snapshot_id does not match its directory.", stage, "INTEGRITY_FAILURE")
    return value


def capture_storage_config(config, *, stage="validate"):
    """Capture once; no custom mappings or caller-owned nested values survive."""
    if type(config) is not dict:
        _fail("storage_config must be an ordinary built-in dict.", stage)
    if len(config) != 8:
        _fail("Storage configuration fields do not match the A2 format.", stage)
    try:
        owned = config.copy()
    except RuntimeError:
        _fail("storage_config changed while being captured.", stage)
    _object(owned, ("format", "profile", "storage_ref", "mount_point", "mount_root",
                    "mount_source", "fs_type", "archive_root"), stage)
    for key, value in owned.items():
        if type(key) is not str or type(value) is not str:
            _fail("Storage configuration keys and values must be built-in strings.", stage)
    encode(owned, MAX_STORAGE_CONFIG, stage=stage)
    _fixed(owned["format"], STORAGE_FORMAT, stage)
    _fixed(owned["profile"], "mounted-posix-v1", stage)
    reference = owned["storage_ref"]
    if not (1 <= len(reference) <= 128 and reference.isascii()):
        _fail("storage_ref must contain 1 to 128 ASCII characters.", stage)
    if not (1 <= len(owned["mount_source"].encode("utf-8")) <= 1024):
        _fail("mount_source must contain 1 to 1024 UTF-8 bytes.", stage)
    if owned["fs_type"] not in ("nfs", "nfs4", "cifs"):
        _fail("This mounted storage type is not supported.", stage, "UNSUPPORTED_STORAGE")
    for key in ("mount_point", "mount_root", "archive_root"):
        path = owned[key]
        if not path.startswith("/") or "\x00" in path:
            _fail("Storage paths must be absolute and contain no NUL.", stage)
    point = posixpath.normpath(owned["mount_point"])
    root = posixpath.normpath(owned["archive_root"])
    if posixpath.commonpath((point, root)) != point:
        _fail("archive_root must lie within mount_point.", stage)
    return owned


def _package_version():
    """Use metadata for this installed module, or its actual source pyproject."""
    module = Path(__file__).resolve()
    try:
        distribution = metadata.distribution("infra-artifact-ledger")
    except metadata.PackageNotFoundError:
        distribution = None
    if distribution is not None:
        installed = distribution.locate_file("infra_artifact_ledger/snapshot_format.py")
        if Path(installed).resolve() == module:
            return distribution.version
    # Only the conventional source layout is a valid fallback. An unrelated
    # installed distribution cannot label this checkout with its own version.
    if module.parent.parent.name == "src":
        project = module.parent.parent.parent / "pyproject.toml"
        try:
            data = tomllib.loads(project.read_text(encoding="utf-8"))["project"]
            if data["name"] == "infra-artifact-ledger":
                return data["version"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    raise RecoveryError("IO_ERROR", "The running package version could not be identified.",
                        stage="snapshot")


def make_manifest(snapshot_id, source_commit, database_size, database_sha256, summary):
    # Round-trip gives the new document its own nested summary, with A2 limits.
    owned_summary = parse_json(encode(summary, stage="snapshot"), stage="snapshot")
    result = {
        "format": SNAPSHOT_FORMAT, "snapshot_id": snapshot_id,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "backend": "sqlite", "schema_version": 1, "data_profile": "bounded-local-v0.1",
        "journal_mode": "delete", "text_encoding": "UTF-8",
        "producer": {"package_version": _package_version(), "source_commit": source_commit},
        "database": {"name": "ledger.sqlite", "byte_length": database_size,
                     "sha256": database_sha256},
        "summary": owned_summary,
    }
    return validate_manifest(result, stage="snapshot")


def make_marker(snapshot_id, manifest_sha256):
    return validate_marker({"format": MARKER_FORMAT, "snapshot_id": snapshot_id,
                            "manifest_sha256": manifest_sha256})
