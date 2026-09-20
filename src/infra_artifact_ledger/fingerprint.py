"""Deterministic encoding for the contract's finite JSON shapes.

This is deliberately not a general RFC 8785 implementation. The manifest shape
contains only ASCII object keys and string values, for which this encoding is
also the required RFC 8785 representation.
"""

import hashlib
import json

from .errors import LedgerError
from .records import CONTRACT_VERSION


def canonical_bytes(obj):
    """Encode declared JSON shapes without normalization, whitespace or LF."""
    # Validate iteratively, so accidental cycles/deep objects cannot crash the
    # recursive stdlib encoder. Object keys in all declared shapes are ASCII.
    stack = [(obj, 0, frozenset())]
    while stack:
        item, depth, ancestors = stack.pop()
        if isinstance(item, (dict, list)):
            if depth >= 16 or id(item) in ancestors:
                raise LedgerError("RESOURCE_LIMIT", "Canonical JSON container depth exceeds 16 or is cyclic.")
            parents = ancestors | {id(item)}
            if isinstance(item, dict):
                if any(type(key) is not str or not key.isascii() for key in item):
                    raise LedgerError("INVALID_INPUT", "Canonical object keys must be ASCII strings.")
                stack.extend((value, depth + 1, parents) for value in item.values())
            else:
                stack.extend((value, depth + 1, parents) for value in item)
        elif type(item) is str:
            if any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise LedgerError("INVALID_INPUT", "Canonical strings cannot contain lone surrogates.")
        elif type(item) not in (int, bool, type(None)):
            raise LedgerError("INVALID_INPUT", "Canonical values must use declared JSON types; floats are not supported.")
    try:
        return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise LedgerError("INVALID_INPUT", "Cannot encode the declared JSON shape.") from error


def digest_bytes(data):
    if type(data) is not bytes:
        raise LedgerError("INVALID_INPUT", "Digest input must be immutable bytes.")
    return {"algorithm": "sha256", "value": hashlib.sha256(data).hexdigest()}


def request_fingerprint(request, bundle=None):
    body = request["body"]
    if request["operation_kind"] == "import_bundle":
        if bundle is None:
            raise LedgerError("INVALID_INPUT", "Import fingerprint requires parsed bundle metadata.")
        body = {"import_receipt_id": body["import_receipt_id"], "bundle": bundle}
    envelope = {
        "contract_version": request.get("contract_version", CONTRACT_VERSION),
        "idempotency_scope_ref": request["idempotency_scope_ref"],
        "operation_kind": request["operation_kind"],
        "body": body,
    }
    return digest_bytes(canonical_bytes(envelope))


def manifest_digest(entries):
    return digest_bytes(canonical_bytes({"entries": entries}))
