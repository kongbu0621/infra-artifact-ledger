"""Bounded, complete transport bundles; payloads are data and never executed."""

import base64
import binascii
import io
import json
import re
from collections.abc import Mapping

from .errors import LedgerError
from .fingerprint import canonical_bytes, digest_bytes
from .records import KINDS, PROFILE, TRANSPORT_VERSION
from .validation import (
    MAX_BLOB,
    MAX_DESCRIPTOR,
    MAX_INTEGER,
    MAX_JSON,
    MAX_PACKAGE,
    MAX_PAYLOAD,
    check_payload,
    parse_json,
    validate_metadata,
)


def _fail(code, message):
    raise LedgerError(code, message)


def _fields(value, fields, label):
    if type(value) is not dict or set(value) != set(fields):
        _fail("INVALID_INPUT", f"{label}: expected exactly {', '.join(fields)}.")


def _length(value, limit, label):
    if type(value) is not int or value < 0:
        _fail("INVALID_INPUT", f"{label}: expected a nonnegative safe integer.")
    if value > MAX_INTEGER or value > limit:
        _fail("RESOURCE_LIMIT", f"{label}: exceeds {limit} bytes.")


def _digest(value, label):
    _fields(value, ("algorithm", "value"), label)
    if value["algorithm"] != "sha256" or type(value["value"]) is not str:
        _fail("INVALID_INPUT", f"{label}: expected a sha256 digest.")
    if re.fullmatch(r"[0-9a-f]{64}", value["value"]) is None:
        _fail("INVALID_INPUT", f"{label}: expected 64 lowercase hexadecimal digits.")


def _version(value, expected, label, code="UNSUPPORTED_VERSION"):
    if type(value) is not str:
        _fail("INVALID_INPUT", f"{label}: expected a string.")
    if value != expected:
        _fail(code, f"{label}: unsupported value.")


def _raw(value, limit, label):
    if type(value) is not bytes:
        _fail("INVALID_INPUT", f"{label}: expected immutable bytes.")
    if len(value) > limit:
        _fail("RESOURCE_LIMIT", f"{label}: exceeds {limit} serialized bytes.")


def _write(output, chunk, limit, label):
    if output.tell() + len(chunk) > limit:
        _fail("RESOURCE_LIMIT", f"{label}: exceeds {limit} serialized bytes.")
    output.write(chunk)


def _json_bytes(value, limit, label):
    """Bound allocation of the serialized result instead of checking it afterward."""
    output = io.BytesIO()
    encoder = json.JSONEncoder(
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    try:
        for piece in encoder.iterencode(value):
            _write(output, piece.encode("utf-8"), limit, label)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise LedgerError("INVALID_INPUT", f"{label}: cannot encode strict JSON.") from exc
    return output.getvalue()


def _content_entry(raw):
    return {
        "encoding": "base64",
        "data": base64.b64encode(raw).decode("ascii"),
        "byte_length": len(raw),
        "digest": digest_bytes(raw),
    }


def _content_header(entry, limit, label, *, payload=False):
    fields = ("encoding", "data", "byte_length", "digest")
    _fields(entry, (("blob_ref",) + fields) if payload else fields, label)
    if entry["encoding"] != "base64":
        _fail("INVALID_INPUT", f"{label}.encoding: expected base64.")
    _length(entry["byte_length"], limit, f"{label}.byte_length")
    _digest(entry["digest"], f"{label}.digest")
    data = entry["data"]
    if type(data) is not str or not data.isascii():
        _fail("INVALID_INPUT", f"{label}.data: expected ASCII Base64 text.")
    # Check the maximum possible decoded size before Base64 allocates bytes.
    if len(data) % 4:
        _fail("INVALID_INPUT", f"{label}.data: invalid Base64 length.")
    padding = 2 if data.endswith("==") else int(data.endswith("="))
    decoded_length = len(data) // 4 * 3 - padding
    if decoded_length > limit:
        _fail("RESOURCE_LIMIT", f"{label}.data: exceeds {limit} decoded bytes.")
    if re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", data) is None:
        _fail("INVALID_INPUT", f"{label}.data: invalid Base64 alphabet or padding.")
    if decoded_length != entry["byte_length"]:
        _fail("INTEGRITY_FAILURE", f"{label}: Base64 and declared length differ.")
    return decoded_length


def _decode_content(entry, label):
    try:
        decoded = base64.b64decode(entry["data"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LedgerError("INVALID_INPUT", f"{label}.data: invalid Base64.") from exc
    if base64.b64encode(decoded).decode("ascii") != entry["data"]:
        _fail("INVALID_INPUT", f"{label}.data: noncanonical Base64.")
    if len(decoded) != entry["byte_length"] or digest_bytes(decoded) != entry["digest"]:
        _fail("INTEGRITY_FAILURE", f"{label}: content length or digest mismatch.")
    return decoded


def encode_bundle(metadata, payloads):
    """Export a complete validated state with stable record collection ordering.

    The caller supplies one consistent store view. Record-internal arrays retain
    their order. Sorting this *export* must never alter an import request's input
    metadata, whose original array order and optional fields affect its fingerprint.
    """
    validate_metadata(metadata)
    if not isinstance(payloads, Mapping):
        _fail("INVALID_INPUT", "payloads: expected a BlobRef-to-bytes mapping.")
    payloads = dict(payloads.items())
    blob_records = {blob["blob_ref"]: blob for blob in metadata["blobs"]}
    if set(payloads) != set(blob_records):
        _fail("INTEGRITY_FAILURE", "payloads: missing or extra BlobRef.")
    total = 0
    for blob_ref, raw in payloads.items():
        check_payload(blob_records[blob_ref], raw)
        total += len(raw)
        if total > MAX_PAYLOAD:
            _fail("RESOURCE_LIMIT", "payloads: total exceeds 256 MiB.")

    # Copy only the collections that are sorted; never mutate the caller's state.
    ordered = dict(metadata)
    for collection, identifier in KINDS.values():
        ordered[collection] = sorted(metadata.get(collection, []), key=lambda r: r[identifier])
    ordered["idempotency_records"] = sorted(
        metadata["idempotency_records"],
        key=lambda r: (r["idempotency_scope_ref"], r["operation_kind"], r["idempotency_key"]),
    )
    metadata_raw = _json_bytes(ordered, MAX_JSON, "metadata")
    # The parser also enforces the profile's depth and node limits on exports.
    parse_json(metadata_raw, limit=MAX_JSON, label="metadata")

    # Stream one encoded payload at a time into a bounded buffer. Keeping a list
    # of all Base64 strings as well as the package would double its large portion.
    output = io.BytesIO()
    prefix = (
        b'{"transport_version":' + canonical_bytes(TRANSPORT_VERSION)
        + b',"profile":' + canonical_bytes(PROFILE) + b',"metadata":'
    )
    _write(output, prefix, MAX_PACKAGE, "package")
    _write(output, _json_bytes(_content_entry(metadata_raw), MAX_PACKAGE, "metadata entry"), MAX_PACKAGE, "package")
    _write(output, b',"payloads":[', MAX_PACKAGE, "package")
    for index, blob_ref in enumerate(sorted(payloads)):
        if index:
            _write(output, b",", MAX_PACKAGE, "package")
        entry = {"blob_ref": blob_ref, **_content_entry(payloads[blob_ref])}
        _write(output, _json_bytes(entry, MAX_PACKAGE, "payload entry"), MAX_PACKAGE, "package")
    _write(output, b"]}", MAX_PACKAGE, "package")
    package = output.getvalue()
    descriptor = canonical_bytes({
        "transport_version": TRANSPORT_VERSION,
        "package_byte_length": len(package),
        "package_digest": digest_bytes(package),
    })
    _raw(descriptor, MAX_DESCRIPTOR, "descriptor")
    return {"package_utf8": package, "descriptor_utf8": descriptor}


def decode_bundle(package, descriptor):
    """Validate all transport and content layers, retaining parsed metadata shape."""
    _raw(package, MAX_PACKAGE, "package")
    _raw(descriptor, MAX_DESCRIPTOR, "descriptor")
    description = parse_json(descriptor, limit=MAX_DESCRIPTOR, label="descriptor")
    _fields(description, ("transport_version", "package_byte_length", "package_digest"), "descriptor")
    _version(description["transport_version"], TRANSPORT_VERSION, "descriptor.transport_version")
    _length(description["package_byte_length"], MAX_PACKAGE, "descriptor.package_byte_length")
    _digest(description["package_digest"], "descriptor.package_digest")
    if (description["package_byte_length"] != len(package)
            or description["package_digest"] != digest_bytes(package)):
        _fail("INTEGRITY_FAILURE", "descriptor: package length or digest mismatch.")

    envelope = parse_json(package, limit=MAX_PACKAGE, label="package")
    _fields(envelope, ("transport_version", "profile", "metadata", "payloads"), "package")
    _version(envelope["transport_version"], TRANSPORT_VERSION, "package.transport_version")
    _version(envelope["profile"], PROFILE, "package.profile", "UNSUPPORTED_PROFILE")
    _content_header(envelope["metadata"], MAX_JSON, "metadata")
    metadata_raw = _decode_content(envelope["metadata"], "metadata")
    metadata = parse_json(metadata_raw, limit=MAX_JSON, label="metadata")
    validate_metadata(metadata)

    entries = envelope["payloads"]
    if type(entries) is not list:
        _fail("INVALID_INPUT", "package.payloads: expected an array.")
    blob_records = {blob["blob_ref"]: blob for blob in metadata["blobs"]}
    seen = set()
    total = 0
    # Preflight *all* claimed and Base64-derived sizes before allocating payloads.
    for index, entry in enumerate(entries):
        label = f"payloads[{index}]"
        size = _content_header(entry, MAX_BLOB, label, payload=True)
        blob_ref = entry["blob_ref"]
        if type(blob_ref) is not str:
            _fail("INVALID_INPUT", f"{label}.blob_ref: expected a string.")
        if blob_ref in seen:
            _fail("INVALID_INPUT", f"{label}: duplicate BlobRef.")
        seen.add(blob_ref)
        if blob_ref not in blob_records:
            _fail("INTEGRITY_FAILURE", f"{label}: extra BlobRef.")
        record = blob_records[blob_ref]
        if entry["byte_length"] != record["byte_length"] or entry["digest"] != record["digest"]:
            _fail("INTEGRITY_FAILURE", f"{label}: transport and Blob metadata differ.")
        total += size
        if total > MAX_PAYLOAD:
            _fail("RESOURCE_LIMIT", "payloads: total exceeds 256 MiB.")
    if seen != set(blob_records):
        _fail("INTEGRITY_FAILURE", "payloads: missing BlobRef.")

    payloads = {}
    for index, entry in enumerate(entries):
        raw = _decode_content(entry, f"payloads[{index}]")
        check_payload(blob_records[entry["blob_ref"]], raw)
        payloads[entry["blob_ref"]] = raw
    return metadata, payloads
