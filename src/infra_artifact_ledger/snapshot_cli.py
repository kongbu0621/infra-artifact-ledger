"""Strict command-line transport for the A2 recovery API.

Configuration files are bounded and read once.  Publication is owned by the
recovery API; a response-channel failure never rewrites its observed state.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import stat
import sys

from .snapshot_common import EXIT_CODES, RecoveryError, operation_budget
from .snapshot_format import capture_storage_config, parse_json


MAX_CONFIG = 16 * 1024
MAX_RESPONSE = 64 * 1024
_READ_ONLY = frozenset({"verify", "check_restore"})
_COMMANDS = {
    "create": ("db", "output-root", "snapshot-id", "source-commit"),
    "publish": ("snapshot", "storage-config", "expected-manifest-sha256", "scratch-parent"),
    "verify": ("snapshot", "expected-manifest-sha256", "scratch-parent"),
    "restore": ("snapshot", "target-dir", "expected-manifest-sha256", "scratch-parent"),
    "check-restore": ("target-dir", "expected-database-sha256", "scratch-parent"),
}


def _initial_state(operation):
    return "not_applicable" if operation in _READ_ONLY else "not_published"


class _ArgumentFailure(RecoveryError):
    def __init__(self, operation, message):
        super().__init__("INVALID_INPUT", message, stage="validate",
                         publication_state=_initial_state(operation))
        self.operation = operation


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args, operation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.operation = operation
        self.parsed_operation = None

    def parse_known_args(self, args=None, namespace=None):
        self.parsed_operation = None
        parsed, remaining = super().parse_known_args(args, namespace)
        self.parsed_operation = getattr(parsed, "operation", None)
        return parsed, remaining

    def argument_error(self, message):
        raise _ArgumentFailure(self.operation or self.parsed_operation, message)

    def error(self, message):
        # argparse diagnostics can contain arbitrary paths, payload-like tokens
        # or secrets.  None of that is needed to explain invalid arguments.
        self.argument_error("Invalid snapshot command arguments; check the command and its required options.")


class _Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        seen = getattr(namespace, "_seen", set())
        if self.dest in seen:
            parser.argument_error("An option was supplied more than once.")
        seen.add(self.dest)
        namespace._seen = seen
        setattr(namespace, self.dest, values)


def _parser():
    parser = _Parser(prog="artifact-ledger snapshot", allow_abbrev=False,
                     description="Create, save, verify and restore bounded Ledger snapshots.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command, required in _COMMANDS.items():
        operation = command.replace("-", "_")
        child = commands.add_parser(command, allow_abbrev=False, operation=operation)
        child.set_defaults(operation=operation)
        for name in required:
            child.add_argument("--" + name, required=True, action=_Once)
        if command in {"verify", "restore"}:
            child.add_argument("--storage-config", action=_Once)
    return parser


def _read_config(path, budget):
    """Capture one bounded regular file; subsequent stages never reopen it."""
    if type(path) is not str or not path or "\0" in path:
        raise RecoveryError("INVALID_INPUT", "Storage config needs a nonempty path without NUL.")
    try:
        os.fsencode(path)
        budget.check("validate")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        primary = None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise RecoveryError("INVALID_INPUT", "Storage config must be a regular file.")
            if info.st_size > MAX_CONFIG:
                raise RecoveryError("RESOURCE_LIMIT", "Storage config exceeds its serialized byte limit.")
            # Own the raw descriptor until close; wrapping it in fdopen can
            # fail before ownership transfers. Short reads are not EOF, and
            # growth after fstat must still respect the serialized limit.
            raw = bytearray()
            while len(raw) <= MAX_CONFIG:
                budget.check("validate")
                block = os.read(descriptor, MAX_CONFIG + 1 - len(raw))
                if not block:
                    break
                raw.extend(block)
                if len(raw) > MAX_CONFIG:
                    raise RecoveryError("RESOURCE_LIMIT", "Storage config exceeds its serialized byte limit.")
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                os.close(descriptor)
            except OSError:
                if primary is None:
                    raise
                primary.add_note("Storage config close also failed; the earlier failure is retained.")
        budget.check("validate")
        value = parse_json(bytes(raw), limit=MAX_CONFIG, stage="validate")
        budget.check("validate")
        return value
    except FileNotFoundError as error:
        raise RecoveryError("NOT_FOUND", "Storage config file was not found.") from error
    except (ValueError, UnicodeError) as error:
        raise RecoveryError("INVALID_INPUT", "Storage config path is invalid.") from error
    except OSError as error:
        raise RecoveryError("IO_ERROR", "Could not read storage config file.") from error


def _arguments(args, budget):
    kwargs = {name.replace("-", "_"): getattr(args, name.replace("-", "_"))
              for name in _COMMANDS[args.command]}
    if args.command in {"verify", "restore"}:
        kwargs["storage_config"] = args.storage_config
    if kwargs.get("storage_config") is not None:
        # An explicitly supplied FILE must contain a valid configuration. In
        # particular JSON null must not become the API's omitted-config None
        # sentinel and silently select local storage for verify/restore.
        kwargs["storage_config"] = capture_storage_config(
            _read_config(kwargs["storage_config"], budget))
    return kwargs


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8", "strict") + b"\n"


def _error_response(error, operation):
    # RecoveryError carries bounded diagnostics.  The fallback is also useful
    # if a future backend violates that internal guarantee.
    envelope = error.to_envelope(operation)
    try:
        encoded = _encode(envelope)
        if len(encoded) <= MAX_RESPONSE and len(envelope["error"]["message"].encode("utf-8")) <= 1024:
            return encoded
    except (TypeError, ValueError, UnicodeError, MemoryError):
        pass
    envelope["error"]["message"] = "The operation failed; diagnostic details could not be reported."
    return _encode(envelope)


def _write_channel(stream, value):
    """Write without leaving our bytes for a failing interpreter-exit flush."""
    if stream is None:
        raise OSError("Output channel is unavailable")
    buffer = getattr(stream, "buffer", None)
    raw = getattr(buffer, "raw", buffer)
    if type(buffer) in (io.FileIO, io.BufferedWriter, io.BufferedRandom) and type(raw) is io.FileIO:
        # Preserve a caller's pending output and stream ownership. Only known
        # file buffers are bypassed; custom streams keep their write contract.
        getattr(stream, "flush", buffer.flush)()
        remaining = memoryview(value if isinstance(value, bytes) else value.encode("utf-8"))
        while remaining:
            written = os.write(raw.fileno(), remaining)
            if written <= 0:
                raise OSError("Incomplete output write")
            remaining = remaining[written:]
        return
    output = buffer if isinstance(value, bytes) else stream
    if output is None:
        raise OSError("Binary output channel is unavailable")
    if output.write(value) != len(value):
        raise OSError("Incomplete output write")
    output.flush()


def _write_response(encoded):
    try:
        _write_channel(sys.stdout, encoded)
        return True
    except (OSError, ValueError):
        # A partially written JSON line cannot safely be followed by another
        # envelope.  Keep the caller's original identity/hash/path for checks.
        try:
            _write_channel(sys.stderr, "artifact-ledger: response channel failed; retain the original snapshot identity, digest and target path.\n")
        except (OSError, ValueError):
            pass
        return False


def main(argv=None):
    """Return A2 exit codes and at most one bounded JSON line (help is text)."""
    operation = None
    state = "not_published"
    api_started = False
    exit_code = 0
    try:
        with operation_budget() as budget:
            budget.check("validate")
            args = _parser().parse_args(argv)
            operation = args.operation
            state = _initial_state(operation)
            kwargs = _arguments(args, budget)
            budget.check("validate")
            from . import recovery

            function = getattr(recovery, operation)
            api_started = True
            # An unforeseen exception during an API call cannot prove that no
            # public object was created.  The API's RecoveryError is authoritative.
            state = "not_applicable" if operation in _READ_ONLY else "unknown"
            result = function(**kwargs)
            state = result["publication_state"]
            try:
                encoded = _encode(result)
            except (TypeError, ValueError, UnicodeError, MemoryError) as error:
                raise RecoveryError("IO_ERROR", "The operation result could not be encoded.",
                                    stage="report", publication_state=state) from error
            if len(encoded) > MAX_RESPONSE:
                raise RecoveryError("RESOURCE_LIMIT", "The complete response exceeds its byte limit.",
                                    stage="report", publication_state=state)
    except _ArgumentFailure as error:
        operation = error.operation
        encoded = _error_response(error, operation)
        exit_code = EXIT_CODES[error.code]
    except RecoveryError as error:
        # Config/argument processing occurs before the API has side effects.
        # Read-only operations remain not_applicable even for those failures.
        if not api_started and operation in _READ_ONLY:
            error = RecoveryError(error.code, error.message, stage=error.stage,
                                  publication_state="not_applicable")
        encoded = _error_response(error, operation)
        exit_code = EXIT_CODES[error.code]
    except Exception:
        code = "PUBLICATION_UNKNOWN" if state == "unknown" else "IO_ERROR"
        error = RecoveryError(code, "Snapshot command processing failed.",
                              stage="report" if api_started else "validate", publication_state=state)
        encoded = _error_response(error, operation)
        exit_code = EXIT_CODES[code]
    if not _write_response(encoded):
        return EXIT_CODES["IO_ERROR"]
    return exit_code
