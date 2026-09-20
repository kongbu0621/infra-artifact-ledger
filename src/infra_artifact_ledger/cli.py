"""File/argv transport for the same validated library operations.

Every operation result, including argument errors, is one JSON line on stdout.
Content files are explicitly named by the caller and published without replace.
"""

import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile

from .errors import EXIT_CODES, LedgerError
from .fingerprint import canonical_bytes, digest_bytes
from .service import initialize, open as open_ledger
from .validation import (
    MAX_BLOB, MAX_DESCRIPTOR, MAX_JSON, MAX_PACKAGE, MAX_PAYLOAD,
    parse_json, validate_operation_query, validate_request, validate_stable_id,
)


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args, operation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._operation = operation
        self._parsed_operation = None

    def parse_known_args(self, args=None, namespace=None):
        self._parsed_operation = None
        parsed, remaining = super().parse_known_args(args, namespace)
        # Root parse_args reports unknown options after its child has returned.
        # Keep that parsed command too; do not infer it from arbitrary argv text.
        self._parsed_operation = getattr(parsed, "command", None)
        return parsed, remaining

    def argument_error(self, message):
        operation = self._operation or self._parsed_operation
        state = "not_applicable" if operation is not None and operation != "write" else "not_committed"
        raise LedgerError("INVALID_INPUT", message, state)

    def error(self, message):
        # argparse can interpolate arbitrary caller values into its messages.
        # Keep diagnostics structured, bounded and free of input file contents.
        self.argument_error("Invalid command-line arguments; check the command and its required options.")


class _Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        seen = getattr(namespace, "_seen", set())
        if self.dest in seen:
            parser.argument_error("An option was supplied more than once.")
        seen.add(self.dest)
        namespace._seen = seen
        setattr(namespace, self.dest, values)


def _parser():
    parser = _Parser(prog="artifact-ledger", allow_abbrev=False,
                     description="Local artifact identity, version and content ledger.")
    commands = parser.add_subparsers(dest="command", required=True)
    options = {
        "init": ("db",),
        "write": ("db", "request"),
        "get": ("db", "kind", "id"),
        "history": ("db", "artifact-id"),
        "operation": ("db", "request"),
        "read-blob": ("db", "blob-ref", "output"),
        "verify": ("db",),
        "export": ("db", "package", "descriptor"),
    }
    for command, required in options.items():
        child = commands.add_parser(command, allow_abbrev=False, operation=command)
        for name in required:
            child.add_argument("--" + name, required=True, action=_Once)
        if command == "write":
            for name in ("payload-map", "package", "descriptor"):
                child.add_argument("--" + name, action=_Once)
    return parser


def _valid_path(value, label):
    if type(value) is not str or not value or "\0" in value:
        raise LedgerError("INVALID_INPUT", f"{label} must be a nonempty operating-system path without NUL.")
    try:
        os.fsencode(value)
    except (UnicodeError, ValueError) as error:
        raise LedgerError("INVALID_INPUT", f"{label} is not an operating-system path.") from error
    return value


def _check_output_paths(database, *outputs):
    """Reserve SQLite's sidecar names before opening or publishing any file."""
    try:
        database_paths = {Path(database).absolute(), Path(database).resolve()}
        reserved = {Path(str(path) + suffix).resolve()
                    for path in database_paths for suffix in ("-journal", "-wal", "-shm")}
        for output in outputs:
            if Path(output).resolve() in reserved:
                raise LedgerError("INVALID_INPUT", "Output path is reserved for the current SQLite database.")
    except RuntimeError as error:
        raise LedgerError("INVALID_INPUT", "Output or database path contains a symlink loop.") from error


def _read_file(path, limit, label):
    """Limit both known file size and actual bytes read before JSON decoding."""
    _valid_path(path, label)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise LedgerError("INVALID_INPUT", f"{label} must refer to a regular file.")
            if info.st_size > limit:
                raise LedgerError("RESOURCE_LIMIT", f"{label} exceeds its serialized byte limit.")
            # One bounded immutable snapshot is used for validation AND submit.
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise LedgerError("RESOURCE_LIMIT", f"{label} exceeds its serialized byte limit.")
            return data
    except (ValueError, UnicodeError) as error:
        raise LedgerError("INVALID_INPUT", f"{label} is not a readable operating-system path.") from error
    except OSError as error:
        raise LedgerError("IO_ERROR", f"Could not read {label}.") from error


def _payloads(path):
    entries = parse_json(_read_file(path, MAX_JSON, "payload-map"),
                         limit=MAX_JSON, label="payload-map")
    if type(entries) is not list:
        raise LedgerError("INVALID_INPUT", "payload-map must be an array.")
    seen = set()
    # Check the complete map before reading the first payload.
    for entry in entries:
        if type(entry) is not dict or set(entry) != {"blob_ref", "input_path"}:
            raise LedgerError("INVALID_INPUT", "payload-map entries require only blob_ref and input_path.")
        validate_stable_id(entry["blob_ref"], "payload-map.blob_ref")
        _valid_path(entry["input_path"], "payload-map.input_path")
        if entry["blob_ref"] in seen:
            raise LedgerError("INVALID_INPUT", "payload-map contains a duplicate blob_ref.")
        seen.add(entry["blob_ref"])
    result = {}
    total = 0
    for entry in entries:
        content = _read_file(entry["input_path"], min(MAX_BLOB, MAX_PAYLOAD - total), "payload")
        result[entry["blob_ref"]] = content
        total += len(content)
    return result


def _write_input(args):
    raw = _read_file(args.request, MAX_JSON, "request")
    request = parse_json(raw, limit=MAX_JSON, label="request")
    validate_request(request)
    kind = request.get("operation_kind")
    supplied = (args.payload_map is not None, args.package is not None, args.descriptor is not None)
    expected = {"create_artifact": (False, False, False),
                "append_version": (True, False, False),
                "import_bundle": (False, True, True)}
    if type(kind) is not str or kind not in expected:
        raise LedgerError("INVALID_INPUT", "request.operation_kind is not supported.")
    if supplied != expected[kind]:
        raise LedgerError("INVALID_INPUT", "Transport options do not match request.operation_kind.")
    transport = {}
    if kind == "append_version":
        transport["payloads"] = _payloads(args.payload_map)
    elif kind == "import_bundle":
        # The tiny descriptor is bounded separately; neither source is reopened.
        transport["descriptor"] = _read_file(args.descriptor, MAX_DESCRIPTOR, "descriptor")
        transport["package"] = _read_file(args.package, MAX_PACKAGE, "package")
    return raw, request, transport


def _publish(path, content):
    """Publish one fully written file with a kernel no-clobber operation.

    The temporary file lives in the target directory. link() atomically refuses
    every existing destination, including dangling symlinks, unlike replace().
    A publication followed by directory-fsync failure is honestly an IO_ERROR;
    the caller must inspect outputs, since the link may already exist.
    """
    _valid_path(path, "output")
    destination = Path(path)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".artifact-ledger-", dir=destination.parent)
        with os.fdopen(fd, "w+b") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            actual_digest = hashlib.sha256()
            actual_length = 0
            while chunk := stream.read(1024 * 1024):
                actual_digest.update(chunk)
                actual_length += len(chunk)
            if actual_length != len(content) or actual_digest.digest() != hashlib.sha256(content).digest():
                raise LedgerError("INTEGRITY_FAILURE", "Output verification failed before publication.", "not_applicable")
        os.link(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as error:
        raise LedgerError("IO_ERROR", "Output already exists; it was not replaced.", "not_applicable") from error
    except (ValueError, UnicodeError) as error:
        raise LedgerError("INVALID_INPUT", "Output is not an operating-system path.", "not_applicable") from error
    except OSError as error:
        raise LedgerError("IO_ERROR", "Output publication failed; inspect the explicitly named outputs before retrying.",
                          "not_applicable") from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                # Cleanup failure cannot turn a successfully published file into
                # an absent file. Temporary names never enter portable metadata.
                pass


def _ok(data):
    return {"status": "OK", "commit_state": "not_applicable", "data": data}


def _committed_error(error, result, request):
    return LedgerError("IO_ERROR" if isinstance(error, OSError) or
                       isinstance(error, LedgerError) and error.code == "IO_ERROR" else "INTERNAL_ERROR",
                       "The write committed, but response preparation or connection cleanup failed.", "committed",
                       {"result_ref": result["result_ref"],
                        "idempotency_scope_ref": request["idempotency_scope_ref"],
                        "operation_kind": result["operation_kind"],
                        "idempotency_key": request["idempotency_key"],
                        "recorded_at": result["recorded_at"]})


def _dispatch(args):
    handle = None
    result = None
    request = None
    write_started = False
    error = None
    try:
        _valid_path(args.db, "db")
        # All transport-option and file validation happens before opening a DB.
        if args.command == "write":
            raw, request, transport = _write_input(args)
        elif args.command == "operation":
            query = parse_json(_read_file(args.request, MAX_JSON, "operation request"),
                               limit=MAX_JSON, label="operation request")
            validate_operation_query(query)
        elif args.command == "export":
            _valid_path(args.package, "package output")
            _valid_path(args.descriptor, "descriptor output")
            _check_output_paths(args.db, args.package, args.descriptor)
            if os.path.abspath(args.package) == os.path.abspath(args.descriptor):
                raise LedgerError("INVALID_INPUT", "Package and descriptor outputs must be different paths.")
        elif args.command == "read-blob":
            _valid_path(args.output, "blob output")
            _check_output_paths(args.db, args.output)
        handle = initialize(args.db) if args.command == "init" else open_ledger(args.db)
        if args.command == "init":
            result = _ok({"profile": "bounded-local-v0.1", "storage_schema_version": 1})
        elif args.command == "write":
            write_started = True
            result = handle.execute(raw, **transport)
        elif args.command == "get":
            result = _ok(handle.get_record(args.kind, args.id))
        elif args.command == "history":
            result = _ok(handle.get_history(args.artifact_id))
        elif args.command == "operation":
            result = _ok(handle.get_operation(query["idempotency_scope_ref"],
                                              query["operation_kind"], query["idempotency_key"]))
        elif args.command == "read-blob":
            content = handle.read_blob(args.blob_ref)
            _publish(args.output, content)
            result = _ok({"blob_ref": args.blob_ref, "byte_length": len(content), "digest": digest_bytes(content)})
        elif args.command == "verify":
            result = _ok(handle.verify())
        elif args.command == "export":
            bundle = handle.export_bundle()
            descriptor = parse_json(bundle["descriptor_utf8"], limit=MAX_DESCRIPTOR, label="export descriptor")
            # Two outputs are intentionally not claimed to be one atomic publish.
            _publish(args.package, bundle["package_utf8"])
            _publish(args.descriptor, bundle["descriptor_utf8"])
            result = _ok({"descriptor": descriptor})
        encoded = canonical_bytes(result) + b"\n"
        if len(encoded) > MAX_JSON:
            raise LedgerError("RESOURCE_LIMIT", "Response exceeds the complete JSON envelope limit.")
    except Exception as caught:
        error = caught
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception as caught:
                if error is None:
                    error = caught
    if error is not None:
        if result is not None and result.get("status") == "COMMITTED":
            raise _committed_error(error, result, request) from error
        if isinstance(error, LedgerError):
            if args.command != "write":
                raise LedgerError(error.code, error.message, "not_applicable", error.details) from error
            if not write_started:
                # open() is a read/handle operation on its own, but failure to
                # open for this write proves that execute was never started.
                raise LedgerError(error.code, error.message, "not_committed", error.details) from error
            raise error
        if write_started:
            raise LedgerError("DURABILITY_UNKNOWN", "The write did not return a confirmed result; query its original identity.",
                              "unknown") from error
        raise LedgerError("IO_ERROR" if isinstance(error, OSError) else "INTERNAL_ERROR",
                          "The operation could not be completed.",
                          "not_committed" if args.command == "write" else "not_applicable") from error
    return encoded


def _encode_error(error):
    """Bound failure output too, preserving exact known commit identity."""
    envelope = error.to_envelope()
    try:
        encoded = canonical_bytes(envelope) + b"\n"
        if len(encoded) <= MAX_JSON:
            return encoded
    except (LedgerError, MemoryError):
        pass
    envelope["message"] = "The operation failed; diagnostic details exceeded the response limit."
    details = envelope.pop("details", None)
    if error.commit_state == "committed" and isinstance(details, dict):
        # These identity fields are already bounded by the validated contract.
        identity = {key: details[key] for key in ("result_ref", "idempotency_scope_ref",
                    "operation_kind", "idempotency_key", "recorded_at") if key in details}
        if identity:
            envelope["details"] = identity
    elif error.code == "INTEGRITY_FAILURE" and isinstance(details, dict) and "failures" in details:
        envelope["details"] = {"failures": [{"location": "verification",
                                             "reason": "Failure details exceed the response limit."}],
                               "truncated": True}
    return canonical_bytes(envelope) + b"\n"


def main(argv=None):
    """Return a stable exit code; no storage or file exception leaks as traceback."""
    exit_code = 0
    try:
        encoded = _dispatch(_parser().parse_args(argv))
    except LedgerError as error:
        encoded = _encode_error(error)
        exit_code = EXIT_CODES[error.code]
    except Exception:
        error = LedgerError("INTERNAL_ERROR", "Command-line processing failed before execution.")
        encoded = _encode_error(error)
        exit_code = 1
    try:
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except (OSError, ValueError):
        # A broken response channel may have a partial JSON line. Do not append a
        # second envelope or pretend a confirmed database write was rolled back.
        try:
            sys.stderr.write("artifact-ledger: response channel failed; query the original operation identity.\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
        return 1
    return exit_code
