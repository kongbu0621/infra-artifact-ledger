"""Synthetic acceptance inputs and a real, independently started API consumer.

Fault modes patch internal storage seams only in the child test process. They
are not installed CLI flags or runtime environment configuration.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import time

PROFILE = "bounded-local-v0.1"
SCOPE = "acceptance:reports"
ARTIFACT = "artifact:quarterly-001"
REPORT_V1 = b"Quarterly report v1\n"
REPORT_V2 = b"Quarterly report v2\n"


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(value):
    return {"algorithm": "sha256", "value": hashlib.sha256(value).hexdigest()}


def request(kind, body, key, scope=SCOPE):
    return {
        "profile": PROFILE, "contract_version": "0.1.0",
        "operation_kind": kind, "idempotency_scope_ref": scope,
        "idempotency_key": key, "body": body,
    }


def create(artifact=ARTIFACT, key="create-quarterly", scope=SCOPE):
    return request("create_artifact", {"artifact": {
        "artifact_id": artifact, "namespace_ref": "acceptance:reports",
        "type_ref": {"namespace": "demo", "type_id": "report", "type_version": "1"},
    }}, key, scope)


def append(suffix="v1", payload=REPORT_V1, parents=(), artifact=ARTIFACT,
           provenance=False, key=None):
    version_id, root_id, blob_id = (f"{kind}:quarterly-{suffix}" for kind in ("version", "root", "blob"))
    body = {
        "version": {
            "version_id": version_id, "artifact_id": artifact,
            "content_root_ref": root_id, "parent_version_refs": list(parents),
            "external_source_refs": [], "capture_refs": [], "handling_policy_refs": [],
        },
        "content_roots": [{"content_root_ref": root_id, "kind": "blob", "blob_ref": blob_id}],
        "blobs": [{"blob_ref": blob_id, "digest": digest(payload),
                   "byte_length": len(payload), "payload_availability": "available"}],
        "manifests": [], "provenance_links": [],
    }
    if provenance:
        body["provenance_links"].append({
            "provenance_id": f"provenance:quarterly-{suffix}",
            "subject_version_ref": version_id,
            "relation_ref": {"namespace": "demo", "relation_id": "derived_from", "relation_version": "1"},
            "object": {"kind": "artifact_version", "ref": parents[0]},
        })
    return request("append_version", body, key or f"append-{suffix}"), {blob_id: payload}


def import_request(suffix="copy", key=None, scope="acceptance:copy"):
    return request("import_bundle", {"import_receipt_id": f"receipt:{suffix}"}, key or f"import-{suffix}", scope)


def import_kwargs(bundle):
    return {"package": bundle["package_utf8"], "descriptor": bundle["descriptor_utf8"]}


def repackage(metadata, payloads):
    """Independent transport encoder for malformed-input acceptance fixtures."""
    metadata_bytes = encode(metadata)
    package = encode({
        "transport_version": "0.1.0", "profile": PROFILE,
        "metadata": {"encoding": "base64", "data": base64.b64encode(metadata_bytes).decode("ascii"),
                     "byte_length": len(metadata_bytes), "digest": digest(metadata_bytes)},
        "payloads": [{"blob_ref": key, "encoding": "base64",
                      "data": base64.b64encode(data).decode("ascii"),
                      "byte_length": len(data), "digest": digest(data)} for key, data in payloads.items()],
    })
    descriptor = encode({"transport_version": "0.1.0", "package_byte_length": len(package),
                         "package_digest": digest(package)})
    return {"package_utf8": package, "descriptor_utf8": descriptor}


def worker():
    from infra_artifact_ledger import LedgerError, open as open_ledger
    mode, database, request_path, payload_path, gate_path, marker_path = sys.argv[1:]
    if gate_path != "-":
        deadline = time.monotonic() + 15
        while not Path(gate_path).exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("parent did not release start barrier")
            time.sleep(0.01)
    with open_ledger(database) as ledger:
        if mode == "inspect":
            result = {
                "history": ledger.get_history(ARTIFACT), "verification": ledger.verify(),
                "v1": base64.b64encode(ledger.read_blob("blob:quarterly-v1")).decode("ascii"),
                "v2": base64.b64encode(ledger.read_blob("blob:quarterly-v2")).decode("ascii"),
            }
        else:
            if mode in {"pause_before_commit", "pause_after_commit", "pause_during_payload"}:
                original_commit, original_execute = ledger._store.commit, ledger._store.execute

                def pause():
                    Path(marker_path).write_text("ready", encoding="ascii")
                    time.sleep(30)  # Parent kills the process; this is only a failure deadline.
                    raise TimeoutError("parent did not terminate paused child")

                def commit():
                    if mode == "pause_before_commit":
                        pause()
                    original_commit()
                    if mode == "pause_after_commit":
                        pause()

                def execute(sql, parameters=()):
                    result = original_execute(sql, parameters)
                    if mode == "pause_during_payload" and "INSERT" in sql.upper() and "PAYLOADS" in sql.upper():
                        pause()
                    return result

                ledger._store.commit, ledger._store.execute = commit, execute
            kwargs = {}
            if payload_path != "-":
                locations = json.loads(Path(payload_path).read_text(encoding="utf-8"))
                kwargs["payloads"] = {key: Path(location).read_bytes() for key, location in locations.items()}
            try:
                result = ledger.execute(Path(request_path).read_bytes(), **kwargs)
            except LedgerError as error:
                result = {"status": "ERROR", "code": error.code, "commit_state": error.commit_state}
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    worker()
