"""Explicit synthetic A2 NAS exercise; never mounts or changes NAS services.

Run with the installed wheel's isolated interpreter (``python -I``). Required
inputs are an existing local acceptance parent, the private storage config,
the reviewed source commit, and that exact wheel. A new random run directory
retains private evidence. Only its proven-owned ``disposable`` subtree is
removed, after an independent NAS verification; NAS snapshots are retained.
This entry point is an acceptance tool, not an installed product command.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import secrets
import selectors
import sqlite3
import stat
import subprocess
import sys
import time
import zipfile


class ExerciseError(Exception):
    """An intentionally path-free public diagnostic."""


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ExerciseError(message)


def identity(info):
    return info.st_dev, info.st_ino


def file_stamp(info):
    return (*identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def mount_id(fd):
    with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as source:
        values = [line.partition(":")[2].strip() for line in source if line.startswith("mnt_id:")]
    require(len(values) == 1, "Missing descriptor mount identity.")
    return int(values[0])


def open_directory(path):
    path = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for name in path.parts[1:]:
            next_fd = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


class OwnedRun:
    """Delete permission only for one subtree exclusively created by this run."""

    def __init__(self, parent):
        self.run_id = "a2-nas-" + secrets.token_hex(16)
        self.path = Path(os.path.abspath(parent)) / self.run_id
        self.fd = None
        parent_fd = open_directory(parent)
        try:
            os.mkdir(self.run_id, 0o700, dir_fd=parent_fd)
            self.fd = os.open(self.run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=parent_fd)
            self.root_identity = identity(os.fstat(self.fd))
            self.mount = mount_id(self.fd)
            self.marker = encode({"format": "a2-nas-synthetic-run/v1", "run_id": self.run_id,
                                  "nonce": secrets.token_hex(32)})
            self.write_new("OWNED.json", self.marker)
            os.mkdir("disposable", 0o700, dir_fd=self.fd)
            self.disposable_identity = identity(os.stat("disposable", dir_fd=self.fd,
                                                        follow_symlinks=False))
            os.mkdir("evidence", 0o700, dir_fd=self.fd)
            os.fsync(self.fd)
            os.fsync(parent_fd)
        except BaseException:
            self.close()
            raise
        finally:
            os.close(parent_fd)

    def close(self):
        if self.fd is not None:
            descriptor = self.fd
            self.fd = None
            os.close(descriptor)

    def write_new(self, name, raw):
        require("/" not in name and name not in {"", ".", ".."}, "Invalid evidence name.")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                     dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb", closefd=False) as output:
                output.write(raw)
                output.flush()
                os.fsync(fd)
        finally:
            os.close(fd)

    def check(self):
        require(self.fd is not None, "Run directory is closed.")
        current = open_directory(self.path)
        try:
            require(identity(os.fstat(current)) == self.root_identity
                    and mount_id(current) == self.mount, "Run directory binding changed.")
        finally:
            os.close(current)
        marker_fd = os.open("OWNED.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                            dir_fd=self.fd)
        try:
            info = os.fstat(marker_fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_size == len(self.marker), "Run ownership marker changed.")
            require(os.read(marker_fd, len(self.marker) + 1) == self.marker,
                    "Run ownership marker changed.")
        finally:
            os.close(marker_fd)

    def _inspect_disposable(self, expected_paths, action):
        """Hold checked descriptors while inventorying only known generated names."""
        self.check()
        require(identity(os.stat("disposable", dir_fd=self.fd, follow_symlinks=False))
                == self.disposable_identity, "Disposable directory binding changed.")
        descriptors, entries, inventory = [], [], {}
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        root = os.open("disposable", flags | os.O_DIRECTORY, dir_fd=self.fd)
        descriptors.append(root)
        try:
            require(identity(os.fstat(root)) == self.disposable_identity,
                    "Disposable directory binding changed.")

            def inspect(fd, relative, depth):
                require(depth <= 8 and len(descriptors) <= 128, "Unexpected synthetic tree size.")
                require(mount_id(fd) == self.mount, "Cleanup refuses a mount boundary.")
                for name in sorted(os.listdir(fd)):
                    require(len(descriptors) <= 128, "Unexpected synthetic tree size.")
                    entry_path = relative + name
                    require(entry_path in expected_paths, "Disposable tree contains an unowned entry.")
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    require(info.st_dev == self.root_identity[0], "Cleanup refuses another device.")
                    directory = stat.S_ISDIR(info.st_mode)
                    require(directory or stat.S_ISREG(info.st_mode), "Cleanup refuses links or special files.")
                    child = os.open(name, flags | (os.O_DIRECTORY if directory else 0), dir_fd=fd)
                    descriptors.append(child)
                    before = os.fstat(child)
                    require(identity(before) == identity(info) and mount_id(child) == self.mount,
                            "Cleanup entry binding changed.")
                    record = {"identity": list(identity(info)), "directory": directory, "mount_id": self.mount}
                    if directory:
                        inspect(child, entry_path + "/", depth + 1)
                    else:
                        require(before.st_nlink == 1, "Cleanup refuses hardlinked files.")
                        hasher = hashlib.sha256()
                        while block := os.read(child, 1024 * 1024):
                            hasher.update(block)
                        after = os.fstat(child)
                        require(file_stamp(before) == file_stamp(after), "Synthetic file changed while being inventoried.")
                        record.update(stamp=list(file_stamp(after)), sha256=hasher.hexdigest())
                    inventory[entry_path] = record
                    entries.append((fd, name, child, entry_path, directory))

            inspect(root, "", 0)
            require(set(inventory) == set(expected_paths), "Disposable tree is missing a generated entry.")
            self.check()
            return action(inventory, entries)
        finally:
            pending, first_error = sys.exc_info()[1], None
            for fd in reversed(descriptors):
                try:
                    os.close(fd)
                except OSError as error:
                    if first_error is None:
                        first_error = error
            if first_error is not None:
                if pending is not None:
                    pending.add_note("One acceptance inventory descriptor could not be closed.")
                else:
                    raise first_error

    def freeze_disposable(self, expected_paths):
        """Freeze the explicitly named fixture after close, before NAS actions.

        This is an internal call with tool-generated paths, never a CLI input or
        permission to adopt arbitrary files found by traversal. The caller must
        retain exclusive control throughout the exercise and deletion.
        """
        require(not hasattr(self, "disposable_inventory"), "Disposable inventory is already frozen.")
        expected_paths = frozenset(expected_paths)
        require(bool(expected_paths) and all(type(name) is str and name and not name.startswith("/")
                and all(part not in {"", ".", ".."} for part in name.split("/")) for name in expected_paths),
                "Invalid generated inventory path.")
        self.disposable_inventory = self._inspect_disposable(expected_paths, lambda inventory, _: inventory)
        self.write_new("disposable-ownership.json", encode(self.disposable_inventory))
        os.fsync(self.fd)

    def remove_disposable(self):
        """Delete only the frozen generated tree; any addition/change stops first."""
        require(hasattr(self, "disposable_inventory"), "No frozen disposable ownership inventory exists.")

        def remove(inventory, entries):
            require(inventory == self.disposable_inventory, "Generated file identity or bytes changed after freezing.")
            for fd, name, child, entry_path, directory in entries:
                current = os.stat(name, dir_fd=fd, follow_symlinks=False)
                expected = inventory[entry_path]
                require(list(identity(current)) == expected["identity"] and mount_id(child) == self.mount
                        and not stat.S_ISLNK(current.st_mode), "Cleanup entry changed after inspection.")
                if directory:
                    require(stat.S_ISDIR(current.st_mode), "Cleanup directory changed its type.")
                    os.rmdir(name, dir_fd=fd)
                else:
                    require(stat.S_ISREG(current.st_mode) and list(file_stamp(current)) == expected["stamp"],
                            "Cleanup file changed after inspection.")
                    os.unlink(name, dir_fd=fd)
            require(identity(os.stat("disposable", dir_fd=self.fd, follow_symlinks=False))
                    == self.disposable_identity, "Disposable directory binding changed.")
            os.rmdir("disposable", dir_fd=self.fd)
            os.fsync(self.fd)
            require(not os.path.lexists(self.path / "disposable"), "Synthetic loss was not complete.")

        self._inspect_disposable(self.disposable_inventory, remove)


def request(kind, body, key):
    return {"profile": "bounded-local-v0.1", "contract_version": "0.1.0",
            "operation_kind": kind, "idempotency_scope_ref": "acceptance:nas",
            "idempotency_key": key, "body": body}


def create_request(name):
    return request("create_artifact", {"artifact": {
        "artifact_id": "artifact:" + name, "namespace_ref": "acceptance:nas",
        "type_ref": {"namespace": "acceptance", "type_id": "synthetic", "type_version": "1"},
    }}, "create-" + name)


def populate(source, donor):
    """Self-contained synthetic branch/manifest/provenance/import fixture."""
    import infra_artifact_ledger as library
    with library.initialize(donor) as ledger:
        ledger.execute(encode(create_request("nas-seed")))
        for label, parents in (("base", []), ("left", ["base"]), ("right", ["base"])):
            payload = ("仅用于 NAS 恢复验收：" + label + "\n").encode()
            blob = "blob:nas-" + label
            body = {"version": {"version_id": "version:nas-" + label,
                    "artifact_id": "artifact:nas-seed", "content_root_ref": "root:nas-" + label,
                    "parent_version_refs": ["version:nas-" + p for p in parents],
                    "external_source_refs": [], "capture_refs": [], "handling_policy_refs": []},
                    "content_roots": [{"content_root_ref": "root:nas-" + label,
                                       "kind": "blob", "blob_ref": blob}],
                    "blobs": [{"blob_ref": blob, "digest": {"algorithm": "sha256", "value": digest(payload)},
                               "byte_length": len(payload), "payload_availability": "available"}],
                    "manifests": [], "provenance_links": []}
            if parents:
                body["provenance_links"] = [{"provenance_id": "provenance:nas-" + label,
                    "subject_version_ref": "version:nas-" + label,
                    "relation_ref": {"namespace": "acceptance", "relation_id": "derived_from", "relation_version": "1"},
                    "object": {"kind": "artifact_version", "ref": "version:nas-base"}}]
            if label == "left":
                entries = [{"entry_key": "branch", "blob_ref": blob}]
                body["content_roots"] = [{"content_root_ref": "root:nas-left", "kind": "manifest",
                                         "manifest_ref": "manifest:nas-left"}]
                body["manifests"] = [{"manifest_ref": "manifest:nas-left", "entries": entries,
                    "digest": {"algorithm": "sha256", "value": digest(encode({"entries": entries}))}}]
            ledger.execute(encode(request("append_version", body, "append-" + label)), payloads={blob: payload})
        bundle = ledger.export_bundle()
    with library.initialize(source) as ledger:
        ledger.execute(encode(request("import_bundle", {"import_receipt_id": "receipt:nas-seed"}, "import-seed")),
                       package=bundle["package_utf8"], descriptor=bundle["descriptor_utf8"])
        replay_request = create_request("nas-local")
        replay_result = ledger.execute(encode(replay_request))
        summary = ledger.verify()
    return {"summary": summary, "replay_request": replay_request, "replay_result": replay_result,
            "image": database_image(source)}


def database_image(database):
    """Independent SQL-row and every-payload hashes, with no recoverable payload."""
    result = {"rows": {}, "payloads": {}}
    with closing(sqlite3.connect(Path(database).absolute().as_uri() + "?mode=ro", uri=True)) as connection:
        for table, ordering in (("ledger_format", "version,profile"), ("records", "id"),
                                ("operations", "scope,kind,key"), ("refs", "source_id,field,target_id")):
            rows = [list(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {ordering}")]
            result["rows"][table] = {"count": len(rows), "sha256": digest(encode(rows))}
        for key, data in connection.execute("SELECT blob_ref,data FROM payloads ORDER BY blob_ref"):
            require(type(data) is bytes, "Synthetic payload is not binary.")
            result["payloads"][key] = {"length": len(data), "sha256": digest(data)}
    return result


def verify_wheel(wheel, package_directory):
    """Hash and inspect one fixed wheel descriptor, never reopen its pathname."""
    wheel = Path(wheel)
    fd = os.open(wheel, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with stream as source:
        before = os.fstat(source.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= 64 * 1024 * 1024,
                "An ordinary bounded wheel file is required.")
        hasher = hashlib.sha256()
        while block := source.read(1024 * 1024):
            hasher.update(block)
        source.seek(0)
        with zipfile.ZipFile(source) as archive:
            members = [item for item in archive.infolist()
                       if item.filename.startswith("infra_artifact_ledger/") and not item.is_dir()]
            require(bool(members) and len(members) == len({item.filename for item in members}),
                    "Wheel package members are invalid.")
            require(sum(item.file_size for item in members) <= 16 * 1024 * 1024,
                    "Wheel package members exceed the acceptance budget.")
            installed_names = {"infra_artifact_ledger/" + str(path.relative_to(package_directory))
                               for path in package_directory.rglob("*") if path.is_file()
                               and "__pycache__" not in path.relative_to(package_directory).parts}
            require({item.filename for item in members} == installed_names,
                    "Installed package members differ from supplied wheel.")
            for item in members:
                parts = Path(item.filename).parts
                require(".." not in parts and not Path(item.filename).is_absolute(), "Wheel member path is invalid.")
                path = package_directory.joinpath(*parts[1:])
                require(not any(candidate.is_symlink() for candidate in (path, *path.parents)
                                if candidate == package_directory or package_directory in candidate.parents),
                        "Installed package member is linked.")
                installed_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    installed_stream = os.fdopen(installed_fd, "rb")
                except BaseException:
                    os.close(installed_fd)
                    raise
                with installed_stream as installed, archive.open(item) as archived:
                    initial = os.fstat(installed.fileno())
                    require(stat.S_ISREG(initial.st_mode) and initial.st_size == item.file_size,
                            "Installed package member length differs from supplied wheel.")
                    while True:
                        left, right = installed.read(1024 * 1024), archived.read(1024 * 1024)
                        require(left == right, "Installed package bytes differ from supplied wheel.")
                        if not left:
                            break
                    require(file_stamp(initial) == file_stamp(os.fstat(installed.fileno()))
                            == file_stamp(os.stat(path, follow_symlinks=False)),
                            "Installed package changed during wheel comparison.")
        require(file_stamp(before) == file_stamp(os.fstat(source.fileno()))
                == file_stamp(os.stat(wheel, follow_symlinks=False)), "Wheel binding or bytes changed during inspection.")
    return hasher.hexdigest()


def installed_identity(wheel):
    import infra_artifact_ledger as library
    require(bool(sys.flags.isolated), "Run with the installed environment's python -I.")
    module = Path(library.__file__).resolve()
    prefix = Path(sys.prefix).resolve()
    require(sys.prefix != sys.base_prefix and prefix in module.parents,
            "A dedicated installed-wheel virtual environment is required.")
    require(Path(__file__).resolve().parents[2] not in module.parents,
            "Acceptance must not import the source checkout.")
    wheel_hash = verify_wheel(wheel, module.parent)
    return {"module_path": str(module), "package_version": importlib.metadata.version("infra-artifact-ledger"),
            "wheel_sha256": wheel_hash, "installed_package_wheel_bytes": "MATCH",
            "git_source_identity": "NOT_ATTESTED; source_commit is caller asserted",
            "python": sys.version, "executable": sys.executable,
            "sqlite": sqlite3.sqlite_version, "platform": platform.platform(), "machine": platform.machine()}


def child_cli(argv, cwd, calls=None):
    """Independent installed interpreter; bound both streams and elapsed wait."""
    command = [sys.executable, "-I", "-m", "infra_artifact_ledger", "snapshot", *argv]
    started = time.monotonic()
    process = subprocess.Popen(command,
                               cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + 360
    try:
        with selectors.DefaultSelector() as selector:
            for label, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, label)
            while selector.get_map():
                require(time.monotonic() < deadline, "Independent recovery process exceeded its wait budget.")
                for key, _ in selector.select(min(1.0, max(0.0, deadline - time.monotonic()))):
                    block = os.read(key.fileobj.fileno(), 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        output[key.data].extend(block)
                        require(len(output[key.data]) <= 65536, "Independent recovery output exceeded its bound.")
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        raw = bytes(output["stdout"])
        require(process.returncode == 0 and not output["stderr"], "Independent recovery command failed.")
        require(raw.endswith(b"\n") and raw.count(b"\n") == 1, "Independent recovery response is malformed.")
        response = json.loads(raw)
        operation = argv[0].replace("-", "_")
        require(type(response) is dict and set(response) == {
            "protocol", "status", "operation", "publication_state", "data"}
            and response["protocol"] == "infra-artifact-ledger-recovery/v1"
            and response["status"] == "OK" and response["operation"] == operation
            and response["publication_state"] == ("not_applicable" if operation in {"verify", "check_restore"} else "published")
            and type(response["data"]) is dict, "Independent recovery command returned a mismatched envelope.")
        return response
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()
        if calls is not None:
            record = {"argv": command, "exit_code": process.returncode,
                      "elapsed_seconds": round(time.monotonic() - started, 3),
                      "stdout_sha256": digest(bytes(output["stdout"])),
                      "stderr_sha256": digest(bytes(output["stderr"]))}
            try:
                record["response"] = json.loads(output["stdout"])
            except (ValueError, UnicodeError):
                record["response"] = None
            calls.append(record)


def concurrent_directory_probe(archive):
    """Two independent processes contend for one new synthetic NAS directory."""
    name = "mkdir-race-" + secrets.token_hex(16)
    command = """import json,os,sys
sys.stdin.buffer.read(1)
try:
    os.mkdir(sys.argv[1],0o700)
except FileExistsError:
    print(json.dumps({'result':'exists'}))
else:
    s=os.stat(sys.argv[1],follow_symlinks=False)
    print(json.dumps({'result':'created','identity':[s.st_dev,s.st_ino]}))
"""
    archive.check()
    processes = []
    try:
        for _ in range(2):
            processes.append(subprocess.Popen([sys.executable, "-I", "-c", command, str(archive.path / name)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        for process in processes:
            process.stdin.write(b"x")
            process.stdin.close()
            process.stdin = None
        outcomes = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            require(process.returncode == 0 and not stderr and len(stdout) <= 1024,
                    "Concurrent NAS directory probe failed.")
            outcomes.append(json.loads(stdout))
        require(sorted(item["result"] for item in outcomes) == ["created", "exists"],
                "NAS mkdir did not admit exactly one creator.")
        expected = next(item["identity"] for item in outcomes if item["result"] == "created")
        archive.check()
        info = os.stat(name, dir_fd=archive.fd, follow_symlinks=False)
        require(list(identity(info)) == expected and stat.S_ISDIR(info.st_mode),
                "NAS probe directory binding changed.")
        os.rmdir(name, dir_fd=archive.fd)
        archive.fsync()
        return {"processes": 2, "created": 1, "already_exists": 1}
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()


def exercise(args):
    from infra_artifact_ledger import open as open_ledger
    from infra_artifact_ledger import recovery
    from infra_artifact_ledger.snapshot_common import Budget
    from infra_artifact_ledger.snapshot_format import capture_storage_config, parse_json, MAX_STORAGE_CONFIG
    from infra_artifact_ledger import snapshot_storage as storage
    require(re.fullmatch(r"[0-9a-f]{40}", args.source_commit) is not None, "Expected a complete source commit.")
    runtime = installed_identity(args.wheel)
    config_fd = os.open(args.storage_config, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(stat.S_ISREG(os.fstat(config_fd).st_mode), "Storage configuration must be an ordinary file.")
        raw = os.read(config_fd, MAX_STORAGE_CONFIG + 1)
    finally:
        os.close(config_fd)
    config = capture_storage_config(parse_json(raw, MAX_STORAGE_CONFIG))
    # No owned directory is created until both actual endpoint classes match.
    with storage.open_directory(args.local_parent, budget=Budget()) as local:
        local.check()
        with storage.open_directory(config["archive_root"], budget=Budget(), storage_config=config) as nas:
            nas.check()
            endpoints = {"local": asdict(local.mount), "nas": asdict(nas.mount)}
    run = OwnedRun(args.local_parent)
    report = {"format": "infra-artifact-ledger-a2-nas-exercise/v1", "run_id": run.run_id,
              "status": "RUNNING", "source_commit": args.source_commit, "runtime": runtime,
              "source_commit_evidence": "CALLER_ASSERTED_NOT_GIT_ATTESTED",
              "endpoints": endpoints, "storage_config": config, "stages": {}, "commands": [],
              "coverage": "synthetic NAS roundtrip only; not all T01-T15 or server power-loss testing"}
    stage = "synthetic_fixture"
    pending_error = None
    try:
        work = run.path / "disposable"
        for name in ("snapshots", "scratch"):
            (work / name).mkdir(mode=0o700)
        source, donor = work / "source.sqlite", work / "donor.sqlite"
        stage = "synthetic_fixture"
        expected = populate(source, donor)
        run.write_new("expected.json", encode(expected))
        report["stages"][stage] = expected["summary"]
        stage = "create"
        snapshot_id = secrets.token_hex(16)
        created = recovery.create(db=source, output_root=work / "snapshots", snapshot_id=snapshot_id,
                                  source_commit=args.source_commit)
        manifest_hash = created["data"]["manifest_sha256"]
        database_hash = created["data"]["database_sha256"]
        require(created["data"]["snapshot_id"] == snapshot_id
                and created["data"]["summary"] == expected["summary"], "Local snapshot result differs from fixture.")
        report["stages"][stage] = created
        run.write_new("snapshot-reference.json", encode({"snapshot_id": snapshot_id,
                      "manifest_sha256": manifest_hash, "database_sha256": database_hash}))
        run.freeze_disposable({"source.sqlite", "donor.sqlite", "snapshots", "scratch",
                               "snapshots/" + snapshot_id,
                               *("snapshots/" + snapshot_id + "/" + name
                                 for name in ("ledger.sqlite", "manifest.json", "COMMITTED.json"))})
        stage = "storage_preflight"
        # Snapshot root and preflight stay in a new NAS subtree; never remove it.
        with storage.open_directory(config["archive_root"], budget=Budget(), storage_config=config) as nas:
            with nas.mkdir(run.run_id) as archive:
                archive.fsync()
                nas.fsync()
                private_config = dict(config, archive_root=str(archive.path))
                report["stages"][stage] = storage.preflight(archive, Budget())
                report["stages"]["concurrent_directory_probe"] = concurrent_directory_probe(archive)
                report["stages"]["mount_loss_or_disconnect"] = "NOT_RUN; see separate fault-injection evidence"
        run.write_new("storage-config.json", encode(private_config))
        stage = "publish"
        published = recovery.publish(snapshot=work / "snapshots" / snapshot_id, storage_config=private_config,
                                     expected_manifest_sha256=manifest_hash, scratch_parent=work / "scratch")
        reference = {"snapshot_id": snapshot_id, "manifest_sha256": manifest_hash,
                     "database_sha256": database_hash, "summary": expected["summary"]}
        require(all(published["data"].get(key) == value for key, value in reference.items()),
                "NAS publication result does not match the independently saved reference.")
        report["stages"][stage] = published
        nas_snapshot = str(Path(private_config["archive_root"]) / snapshot_id)
        stage = "independent_nas_verify"
        verified = child_cli(["verify", "--snapshot", nas_snapshot, "--storage-config", str(run.path / "storage-config.json"),
                              "--expected-manifest-sha256", manifest_hash, "--scratch-parent", str(work / "scratch")], run.path, report["commands"])
        require(all(verified["data"].get(key) == value for key, value in reference.items()),
                "NAS verification result does not match the independently saved reference.")
        report["stages"][stage] = verified
        stage = "synthetic_local_loss"
        require(database_image(source) == expected["image"], "Source changed before simulated local loss.")
        run.remove_disposable()
        report["stages"][stage] = {"owned_disposable_removed": True,
                                   "source_and_donor_and_snapshot_and_scratch_removed": True}
        # New process now has only NAS + hashes/request expectation, no local DB.
        (run.path / "restore-scratch").mkdir(mode=0o700)
        target = run.path / "restored"
        stage = "independent_nas_restore"
        restored = child_cli(["restore", "--snapshot", nas_snapshot, "--storage-config", str(run.path / "storage-config.json"),
                             "--target-dir", str(target), "--expected-manifest-sha256", manifest_hash,
                             "--scratch-parent", str(run.path / "restore-scratch")], run.path, report["commands"])
        require(all(restored["data"].get(key) == value for key, value in reference.items()),
                "NAS restore result does not match the independently saved reference.")
        report["stages"][stage] = restored
        stage = "check_restore"
        checked = recovery.check_restore(target_dir=target, expected_database_sha256=database_hash,
                                         scratch_parent=run.path / "restore-scratch")
        report["stages"][stage] = checked
        stage = "exact_rows_payloads_replay_new_write"
        database = target / "ledger.sqlite"
        require(database_image(database) == expected["image"], "Restored SQL rows or Blob bytes differ.")
        with open_ledger(database) as ledger:
            replay = ledger.execute(encode(expected["replay_request"]))
            require(replay == dict(expected["replay_result"], replayed=True), "Original request replay differs.")
            require(ledger.verify() == expected["summary"], "Replay changed the restored state.")
            require(database_image(database) == expected["image"], "Replay changed persisted rows or payloads.")
            new_result = ledger.execute(encode(create_request("nas-after-restore")))
            require(not new_result["replayed"], "New synthetic request did not create a new operation.")
            after = ledger.verify()
            counts = dict(expected["summary"]["counts"])
            counts["artifacts"] += 1
            counts["idempotency_records"] += 1
            require(after == dict(expected["summary"], counts=counts), "New synthetic request has unexpected effects.")
        report["stages"][stage] = {"all_rows_and_blobs_equal": True, "replay_preserved_state": True,
                                   "new_synthetic_write": "PASS"}
        report["status"] = "PASS"
    except Exception as error:
        pending_error = error
        report["status"] = "FAILED"
        report["failure"] = {"stage": stage, "type": type(error).__name__,
                             "code": getattr(error, "code", None), "publication_state": getattr(error, "publication_state", None)}
        error.acceptance_run_id = run.run_id
        raise
    finally:
        try:
            run.check()
            run.write_new("report.json", encode(report))
            os.fsync(run.fd)
            if pending_error is not None:
                pending_error.acceptance_evidence_saved = True
        except Exception as evidence_error:
            if pending_error is None:
                evidence_error.acceptance_run_id = run.run_id
                evidence_error.acceptance_evidence_saved = False
                raise
            pending_error.acceptance_evidence_saved = False
            pending_error.add_note("The owned acceptance report could not be durably recorded.")
        finally:
            active_error = sys.exc_info()[1]
            try:
                run.close()
            except Exception as close_error:
                if active_error is not None:
                    active_error.add_note("The acceptance run directory descriptor could not be closed.")
                else:
                    # The report was already durably written. Keep that
                    # evidence locatable when descriptor cleanup alone fails.
                    close_error.acceptance_run_id = run.run_id
                    close_error.acceptance_evidence_saved = True
                    raise
    return {"status": "PASS", "run_id": run.run_id, "evidence": "report.json",
            "scope": "synthetic_nas_roundtrip", "server_power_loss": "NOT_RUN"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-parent", type=Path, required=True)
    parser.add_argument("--storage-config", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = exercise(args)
    except Exception as error:
        # Private paths/config remain only in the explicitly owned local report.
        result = {"status": "FAILED", "type": type(error).__name__,
                  "code": getattr(error, "code", None), "run_id": getattr(error, "acceptance_run_id", None),
                  "evidence_saved": getattr(error, "acceptance_evidence_saved", False),
                  "scope": "synthetic_nas_roundtrip"}
        print(encode(result).decode())
        return 1
    print(encode(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
