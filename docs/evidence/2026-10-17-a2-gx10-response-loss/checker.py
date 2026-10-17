"""GX10 installed CLI response-loss exercise; only a new synthetic local run.

No NAS access, storage mocks, old fixture writes, cleanup, or business retries.
The initial create response is deliberately unavailable: its later manifest
digest is a self-consistency candidate, never a pre-call trusted digest.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import stat
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
    with Path(path).open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    plan = json.loads(Path(sys.argv[1]).read_text())
    root = Path(plan["run_dir"])
    require(root.resolve() == Path(__file__).resolve().parent, "Run binding mismatch")
    require(digest(__file__) == plan["helper_sha256"], "Helper changed")
    helper = Path(plan["source_dir"]) / "tools/acceptance/a2_nas_exercise.py"
    require(digest(helper) == plan["nas_helper_sha256"], "Fixture helper changed")
    helpers = runpy.run_path(str(helper), run_name="fixed_acceptance_helpers")
    runtime = helpers["installed_identity"](plan["wheel"])
    require(runtime["wheel_sha256"] == plan["wheel_sha256"], "Wheel changed")
    from infra_artifact_ledger.snapshot_storage import open_directory
    from infra_artifact_ledger.snapshot_common import Budget
    from dataclasses import asdict
    with open_directory(root, budget=Budget()) as directory:
        directory.check()
        mount = asdict(directory.mount)
    require(not (root / "started.json").exists(), "Existing attempt must be reconciled first")
    save(root / "started.json", {"run_id": plan["run_id"], "pid": os.getpid(),
         "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
         "runtime": runtime, "mount": mount, "scope": "synthetic local response loss"})
    report = {"status": "RUNNING", "scope": "installed_local_cli_response_loss",
              "source_commit": plan["source_commit"], "run_id": plan["run_id"],
              "runtime": runtime, "mount": mount, "commands": [],
              "nas_executed": False, "business_replayed": False,
              "historical_call_success_proven_by_readback": False}
    stage = "fixture"
    try:
        for name in ("snapshots", "scratch"):
            (root / name).mkdir(mode=0o700)
        source, donor = root / "source.sqlite", root / "donor.sqlite"
        expected = helpers["populate"](source, donor)
        save(root / "expected.json", expected)
        source_hash, donor_hash = digest(source), digest(donor)
        executable = Path(sys.executable).parent / "artifact-ledger"
        environment = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        counts = {}

        def cli(operation, *, drop_response=False, **kwargs):
            counts[operation] = counts.get(operation, 0) + 1
            require(counts[operation] == 1, "No repeated operation in this exercise")
            label = f"{len(report['commands']) + 1:02d}-{operation}"
            argv = [sys.executable, "-I", "-B", str(executable), "snapshot", operation]
            for key, value in kwargs.items():
                argv.extend(["--" + key.replace("_", "-"), str(value)])
            record = {"operation": operation, "argv": argv, "stdout_reader_closed_before_spawn": drop_response}
            save(root / (label + "-request.json"), record)
            read_fd = write_fd = None
            stdout = None
            process = None
            started = time.monotonic()
            try:
                if drop_response:
                    read_fd, write_fd = os.pipe()
                    os.close(read_fd)
                    read_fd = None
                    output = write_fd
                else:
                    stdout = (root / (label + ".stdout")).open("xb")
                    output = stdout
                with (root / (label + ".stderr")).open("xb") as error:
                    process = subprocess.Popen(argv, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                                               stdout=output, stderr=error, start_new_session=True)
                    if write_fd is not None:
                        os.close(write_fd)
                        write_fd = None
                    try:
                        code = process.wait(timeout=360)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
                        record["timed_out"] = True
                        raise
            finally:
                if write_fd is not None:
                    os.close(write_fd)
                if read_fd is not None:
                    os.close(read_fd)
                if stdout is not None:
                    stdout.close()
                record.update(pid=None if process is None else process.pid,
                              exit=None if process is None else process.returncode,
                              elapsed_seconds=time.monotonic() - started)
                save(root / (label + "-result.json"), record)
                report["commands"].append(record)
            diagnostic = (root / (label + ".stderr")).read_bytes()
            if drop_response:
                require(code == 9 and b"response channel failed" in diagnostic, "Expected actual response loss")
                return None
            require(code == 0 and diagnostic == b"", "Readback CLI failed")
            raw = (root / (label + ".stdout")).read_bytes()
            require(len(raw) <= 64 * 1024 and raw.count(b"\n") == 1, "Invalid CLI envelope")
            result = json.loads(raw)
            require(result["status"] == "OK" and result["publication_state"] == "not_applicable", "Read-only result differs")
            return result

        stage = "create_response_loss"
        snapshot_id = plan["snapshot_id"]
        snapshot = root / "snapshots" / snapshot_id
        cli("create", drop_response=True, db=source, output_root=root / "snapshots",
            snapshot_id=snapshot_id, source_commit=plan["source_commit"])
        stage = "create_self_consistency_readback"
        require(snapshot.is_dir() and not snapshot.is_symlink(), "Original snapshot directory absent")
        require({p.name for p in snapshot.iterdir()} == {"ledger.sqlite", "manifest.json", "COMMITTED.json"}, "Snapshot members differ")
        manifest_file = snapshot / "manifest.json"
        require(stat.S_ISREG(manifest_file.lstat().st_mode) and manifest_file.stat().st_size <= 64 * 1024,
                "Manifest is not a bounded ordinary file")
        raw = manifest_file.read_bytes()
        manifest = json.loads(raw)
        require(manifest["snapshot_id"] == snapshot_id and manifest["producer"]["source_commit"] == plan["source_commit"],
                "Original create identity differs")
        candidate = hashlib.sha256(raw).hexdigest()
        before_verify = {p.name: digest(p) for p in snapshot.iterdir()}
        verified = cli("verify", snapshot=snapshot, expected_manifest_sha256=candidate, scratch_parent=root / "scratch")
        require(verified["data"]["summary"] == expected["summary"], "Verified summary differs")
        require(helpers["database_image"](snapshot / "ledger.sqlite") == expected["image"], "Snapshot rows/payloads differ")
        require(before_verify == {p.name: digest(p) for p in snapshot.iterdir()}, "Readback modified snapshot")
        reference = {"snapshot_id": snapshot_id, "manifest_sha256": candidate,
                     "database_sha256": digest(snapshot / "ledger.sqlite"),
                     "manifest_digest_origin": "derived_after_lost_create_response_and_current_self_consistency_verified",
                     "pre_create_manifest_digest_available": False, "exact_historical_T0_attested": False}
        save(root / "reference-after-readback.json", reference)
        report["create_readback"] = reference
        stage = "restore_response_loss"
        target = root / "restored"
        cli("restore", drop_response=True, snapshot=snapshot, target_dir=target,
            expected_manifest_sha256=reference["manifest_sha256"], scratch_parent=root / "scratch")
        stage = "original_target_readback"
        before = digest(target / "ledger.sqlite")
        checked = cli("check-restore", target_dir=target, expected_database_sha256=reference["database_sha256"],
                      scratch_parent=root / "scratch")
        require(checked["data"]["summary"] == expected["summary"], "Restored summary differs")
        require(before == reference["database_sha256"] == digest(target / "ledger.sqlite"), "Restored bytes changed")
        require(helpers["database_image"](target / "ledger.sqlite") == expected["image"], "Restored rows/payloads differ")
        require(digest(source) == source_hash and digest(donor) == donor_hash, "Original synthetic fixtures changed")
        require(before_verify == {p.name: digest(p) for p in snapshot.iterdir()}, "Original snapshot changed")
        report.update(status="PASS", operation_counts=counts, fixture_summary=expected["summary"],
                      all_sql_rows_and_payloads_match=True, original_synthetic_inputs_unchanged=True,
                      restore_readback_unchanged=True, create_and_restore_reexecuted=False,
                      limitation="Current readback does not attest historical completion or NAS/physical durability; no NAS publish exercised")
    except BaseException as error:
        report.update(status="FAIL", failed_stage=stage, error_type=type(error).__name__, error=str(error))
        raise
    finally:
        save(root / "report.json", report)
        print(json.dumps({k: report[k] for k in ("status", "scope", "run_id", "nas_executed", "business_replayed")}), flush=True)


if __name__ == "__main__":
    main()
