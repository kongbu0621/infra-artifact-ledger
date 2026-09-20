"""Run the published report tutorial against an isolated installed wheel.

Run using the consumer venv's Python with -I. This deliberately does not add
the repository's src directory to sys.path or implement any Ledger behavior.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    text = (repo / "docs/REUSE_EXAMPLE.md").read_text(encoding="utf-8")
    inputs = {}
    for match in re.finditer(r"(?:保存为|查询保存为) `([^`]+\.json)`[^\n]*\n\n```json\n(.*?)\n```", text, re.S):
        inputs[match[1]] = json.loads(match[2])
    required = {"create-report.json", "append-v1.json", "append-v2.json",
                "payload-v1.json", "payload-v2.json", "query-v2.json", "import-report.json"}
    assert set(inputs) == required, set(inputs)
    import infra_artifact_ledger as library

    module_path = Path(library.__file__).resolve()
    assert repo not in module_path.parents, module_path
    assert Path(sys.prefix).resolve() in module_path.parents, module_path
    executable = Path(sys.executable).parent / "artifact-ledger"
    calls = []
    with tempfile.TemporaryDirectory(prefix="ledger-installed-consumer-") as temp:
        work = Path(temp)
        for name, obj in inputs.items():
            (work / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        for number in (1, 2):
            (work / f"report-v{number}.txt").write_bytes(f"Quarterly report v{number}\n".encode())

        def cli(*argv: str, expected: int = 0) -> dict:
            result = subprocess.run([str(executable), *argv], cwd=work,
                                    capture_output=True, text=True, check=False)
            assert result.returncode == expected, (argv, result.returncode, result.stdout, result.stderr)
            assert result.stdout.endswith("\n") and len(result.stdout.splitlines()) == 1, result.stdout
            output = json.loads(result.stdout)
            assert output["status"] == ("ERROR" if expected else "COMMITTED" if argv[0] == "write" else "OK")
            calls.append({"argv": list(argv), "exit": result.returncode, "status": output["status"]})
            return output

        cli("init", "--db", "source.sqlite")
        cli("write", "--db", "source.sqlite", "--request", "create-report.json")
        for number in (1, 2):
            response = cli("write", "--db", "source.sqlite", "--request", f"append-v{number}.json",
                           "--payload-map", f"payload-v{number}.json")
            assert not response["replayed"]
        original = response
        for number in (1, 2):
            version = cli("get", "--db", "source.sqlite", "--kind", "version",
                          "--id", f"version:quarterly-001-v{number}")["data"]
            root = cli("get", "--db", "source.sqlite", "--kind", "content_root",
                       "--id", version["content_root_ref"])["data"]
            cli("read-blob", "--db", "source.sqlite", "--blob-ref", root["blob_ref"],
                "--output", f"read-v{number}.txt")
            assert (work / f"read-v{number}.txt").read_bytes() == (work / f"report-v{number}.txt").read_bytes()
        history = cli("history", "--db", "source.sqlite", "--artifact-id", "artifact:quarterly-001")["data"]
        source = cli("verify", "--db", "source.sqlite")["data"]
        assert source["counts"] == dict(artifacts=1, versions=2, content_roots=2, blobs=2,
                                       manifests=0, provenance_links=1, idempotency_records=3, import_receipts=0)
        assert source["verified_byte_length"] == 40 and source["verified_blob_count"] == 2
        replay = cli("write", "--db", "source.sqlite", "--request", "append-v2.json", "--payload-map", "payload-v2.json")
        assert replay == dict(original, replayed=True)
        conflict = copy.deepcopy(inputs["create-report.json"])
        conflict["body"]["artifact"]["type_ref"]["type_version"] = "2"
        (work / "conflict.json").write_text(json.dumps(conflict), encoding="utf-8")
        rejected = cli("write", "--db", "source.sqlite", "--request", "conflict.json", expected=4)
        assert rejected["code"] == "IDEMPOTENCY_CONFLICT"
        query = cli("operation", "--db", "source.sqlite", "--request", "query-v2.json")["data"]
        request = inputs["append-v2.json"]
        semantic = {key: request[key] for key in ("contract_version", "idempotency_scope_ref", "operation_kind", "body")}
        digest = hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        assert query["idempotency_record"]["request_fingerprint"] == {"algorithm": "sha256", "value": digest}
        cli("export", "--db", "source.sqlite", "--package", "package.json", "--descriptor", "descriptor.json")
        cli("init", "--db", "destination.sqlite")
        import_args = ("write", "--db", "destination.sqlite", "--request", "import-report.json",
                       "--package", "package.json", "--descriptor", "descriptor.json")
        receipt = cli(*import_args)
        assert cli(*import_args) == dict(receipt, replayed=True)
        assert cli("history", "--db", "destination.sqlite", "--artifact-id", "artifact:quarterly-001")["data"] == history
        assert cli("operation", "--db", "destination.sqlite", "--request", "query-v2.json")["data"] == query
        imported = cli("get", "--db", "destination.sqlite", "--kind", "import_receipt", "--id", receipt["result_ref"])["data"]
        assert imported["imported_artifact_refs"] == ["artifact:quarterly-001"]
        for number in (1, 2):
            cli("read-blob", "--db", "destination.sqlite", "--blob-ref", f"blob:quarterly-001-v{number}",
                "--output", f"copied-v{number}.txt")
            assert (work / f"copied-v{number}.txt").read_bytes() == (work / f"report-v{number}.txt").read_bytes()
        target = cli("verify", "--db", "destination.sqlite")["data"]
        assert target["counts"] == dict(source["counts"], idempotency_records=4, import_receipts=1)
        assert target["verified_byte_length"] == 40
        # Run the actual library snippet from USAGE, in its separate database.
        usage = (repo / "docs/USAGE.md").read_text(encoding="utf-8")
        snippet = re.search(r"```python\n(.*?)\n```", usage, re.S)[1]
        script = work / "library_consumer.py"
        script.write_text(snippet, encoding="utf-8")
        run = subprocess.run([sys.executable, "-I", str(script)], cwd=work,
                             capture_output=True, text=True, check=False)
        assert run.returncode == 0, (run.stdout, run.stderr)
        with library.open(work / "python-source.sqlite") as ledger:
            assert ledger.verify() == source
        print(json.dumps({"result": "PASS", "python": sys.version.split()[0],
                          "sqlite": sqlite3.sqlite_version,
                          "package_version": importlib.metadata.version("infra-artifact-ledger"),
                          "module_path": str(module_path), "cli_calls": calls,
                          "library_guide": "PASS", "source_counts": source["counts"],
                          "target_counts": target["counts"], "verified_bytes": 40}, ensure_ascii=False))


if __name__ == "__main__":
    main()
