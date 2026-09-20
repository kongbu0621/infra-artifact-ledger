"""Exercise an installed A2 wheel on an explicitly chosen local scratch root.

Run with the isolated consumer venv's Python -I.  No checkout imports, implicit
machine paths, storage mocks, mounts or production data are used.  Passing a
storage config opts into creating two fresh synthetic NAS generations.  This
smoke flow does not replace the separate NAS capability and loss exercise.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import uuid


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--storage-config", type=Path)
    args = parser.parse_args()

    import infra_artifact_ledger as library
    from infra_artifact_ledger import recovery
    from infra_artifact_ledger.snapshot_common import Budget
    from infra_artifact_ledger.snapshot_format import capture_storage_config, parse_json
    from infra_artifact_ledger.snapshot_storage import open_directory

    module_path = Path(library.__file__).resolve()
    assert sys.flags.isolated and sys.prefix != sys.base_prefix, "Use the consumer venv Python with -I."
    assert Path(sys.prefix).resolve() in module_path.parents, "Use an isolated installed wheel."
    executable = Path(sys.executable).parent / "artifact-ledger"
    assert executable.is_file(), "Installed CLI entry point is missing."
    assert len(args.source_commit) == 40 and all(c in "0123456789abcdef" for c in args.source_commit)
    scratch = args.scratch_parent.absolute()
    # Public APIs also enforce the profile.  This early check avoids placing
    # even synthetic fixtures on unsupported temporary/overlay filesystems.
    with open_directory(scratch, budget=Budget()):
        pass
    work = Path(tempfile.mkdtemp(prefix="a2-installed-", dir=scratch))
    output_root = work / "snapshots"
    output_root.mkdir()
    source = work / "source.sqlite"
    artifact = "artifact:installed-a2-report"
    payload = b"Synthetic installed recovery evidence.\n"
    prefix = {"profile": "bounded-local-v0.1", "contract_version": "0.1.0",
              "idempotency_scope_ref": "acceptance:installed-a2"}
    creation = dict(prefix, operation_kind="create_artifact", idempotency_key="create", body={
        "artifact": {"artifact_id": artifact, "namespace_ref": "acceptance:reports",
                     "type_ref": {"namespace": "demo", "type_id": "report", "type_version": "1"}}})
    append = dict(prefix, operation_kind="append_version", idempotency_key="append", body={
        "version": {"version_id": "version:installed-a2-v1", "artifact_id": artifact,
                    "content_root_ref": "root:installed-a2-v1", "parent_version_refs": [],
                    "external_source_refs": [], "capture_refs": [], "handling_policy_refs": []},
        "content_roots": [{"content_root_ref": "root:installed-a2-v1", "kind": "blob", "blob_ref": "blob:installed-a2-v1"}],
        "blobs": [{"blob_ref": "blob:installed-a2-v1", "byte_length": len(payload),
                   "digest": {"algorithm": "sha256", "value": hashlib.sha256(payload).hexdigest()},
                   "payload_availability": "available"}],
        "manifests": [], "provenance_links": []})
    with library.initialize(source) as ledger:
        ledger.execute(encode(creation))
        appended = ledger.execute(encode(append), payloads={"blob:installed-a2-v1": payload})
        original_summary = ledger.verify()
    original_bytes = source.read_bytes()

    config = None
    if args.storage_config is not None:
        with args.storage_config.open("rb") as stream:
            raw = stream.read(16 * 1024 + 1)
        config = capture_storage_config(parse_json(raw, limit=16 * 1024))

    calls = []

    def run_cli(command, **kwargs):
        # Execute the installed console entry with the same isolated interpreter;
        # a parent's -I does not remove PYTHONPATH/PYTHONHOME from child env.
        argv = [sys.executable, "-I", str(executable), "snapshot", command.replace("_", "-")]
        for key, value in kwargs.items():
            if value is not None:
                if key == "storage_config":
                    value = args.storage_config.absolute()
                argv.extend(["--" + key.replace("_", "-"), str(value)])
        run = subprocess.run(argv, capture_output=True, timeout=360, cwd=work)
        assert run.returncode == 0, (command, run.returncode, run.stdout, run.stderr)
        assert run.stderr == b"" and run.stdout.count(b"\n") == 1
        assert len(run.stdout) <= 64 * 1024
        return json.loads(run.stdout)

    for mode in ("api", "cli"):
        def invoke(command, **kwargs):
            response = getattr(recovery, command)(**kwargs) if mode == "api" else run_cli(command, **kwargs)
            assert response["operation"] == command and response["status"] == "OK"
            assert response["publication_state"] == ("not_applicable" if command in {"verify", "check_restore"} else "published")
            calls.append({"entry": mode, "operation": command, "status": "PASS"})
            return response["data"]

        created = invoke("create", db=str(source), output_root=str(output_root),
                         snapshot_id=uuid.uuid4().hex, source_commit=args.source_commit)
        snapshot = created["snapshot_path"]
        expected = created["manifest_sha256"]
        assert created["summary"] == original_summary
        source_config = None
        if config is not None:
            published = invoke("publish", snapshot=snapshot, storage_config=config,
                               expected_manifest_sha256=expected, scratch_parent=str(work))
            assert published["manifest_sha256"] == expected
            assert published["database_sha256"] == created["database_sha256"]
            snapshot, source_config = published["snapshot_path"], config
        verified = invoke("verify", snapshot=snapshot, expected_manifest_sha256=expected,
                          scratch_parent=str(work), storage_config=source_config)
        target = work / (mode + "-restored")
        restored = invoke("restore", snapshot=snapshot, target_dir=str(target),
                          expected_manifest_sha256=expected, scratch_parent=str(work), storage_config=source_config)
        checked = invoke("check_restore", target_dir=str(target), expected_database_sha256=created["database_sha256"],
                         scratch_parent=str(work))
        assert verified["summary"] == restored["summary"] == checked["summary"] == original_summary
        assert sorted(child.name for child in target.iterdir()) == ["ledger.sqlite"]
        # Only after check_restore, enable the synthetic restored database to
        # prove that physical restore preserved the existing idempotency record.
        with library.open(restored["database_path"]) as ledger:
            assert ledger.read_blob("blob:installed-a2-v1") == payload
            assert ledger.execute(encode(append), payloads={"blob:installed-a2-v1": payload}) == dict(appended, replayed=True)
            assert ledger.verify() == original_summary
    assert source.read_bytes() == original_bytes
    print(json.dumps({"result": "PASS", "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                      "platform": platform.platform(), "package_version": importlib.metadata.version("infra-artifact-ledger"),
                      "module_path": str(module_path), "source_commit": args.source_commit, "work_dir": str(work),
                      "calls": calls, "nas_publish_flow": "PASS" if config is not None else "NOT_RUN",
                      "nas_capability_acceptance": "NOT_ATTESTED", "nas_loss_exercise": "NOT_RUN"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
