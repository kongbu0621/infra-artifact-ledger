"""Internal A2 failures and cumulative operation deadlines.

No implicit settings or machine paths are consulted. Nested CLI/library work
shares one deadline; unrelated calls and threads retain independent budgets.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import sys
import time

PROTOCOL = "infra-artifact-ledger-recovery/v1"
EXIT_CODES = {
    "INVALID_INPUT": 2,
    "UNSUPPORTED_FORMAT": 3, "UNSUPPORTED_STORAGE": 3,
    "TARGET_EXISTS": 4,
    "NOT_FOUND": 5, "INCOMPLETE_SNAPSHOT": 5,
    "INTEGRITY_FAILURE": 6,
    "RESOURCE_LIMIT": 7,
    "BUSY": 8, "TIMEOUT": 8,
    "IO_ERROR": 9, "PUBLICATION_UNKNOWN": 9,
}
STAGES = {"validate", "snapshot", "copy", "verify", "publish", "sync", "report"}
PUBLICATION_STATES = {"not_published", "published", "unknown", "not_applicable"}


class RecoveryError(Exception):
    """A bounded recovery failure distinct from an A1 transaction failure."""

    def __init__(self, code, message, stage="validate", publication_state="not_published"):
        if code not in EXIT_CODES or stage not in STAGES or publication_state not in PUBLICATION_STATES:
            raise ValueError("Invalid recovery error classification.")
        if type(message) is not str:
            raise TypeError("Recovery error message must be a string.")
        # Every code point produces at least one byte (including surrogate
        # replacement). Slice first so an oversized diagnostic never requires
        # an equally oversized temporary UTF-8 allocation merely to truncate it.
        message = message[:1024].encode("utf-8", "replace")[:1024].decode("utf-8", "ignore")
        super().__init__(message)
        self.code, self.message = code, message
        self.stage, self.publication_state = stage, publication_state

    def to_envelope(self, operation=None):
        return {"protocol": PROTOCOL, "status": "ERROR", "operation": operation,
                "publication_state": self.publication_state,
                "error": {"code": self.code, "message": self.message, "stage": self.stage}}


class Budget:
    """300 seconds between cooperative checkpoints, not an OS I/O hard timeout."""

    def __init__(self):
        self.deadline = time.monotonic() + 300.0

    def check(self, stage="validate"):
        if time.monotonic() >= self.deadline:
            raise RecoveryError("TIMEOUT", "Recovery operation checkpoint deadline exceeded.", stage)

    def remaining(self, stage="validate"):
        self.check(stage)
        return max(0.0, self.deadline - time.monotonic())


_budget = ContextVar("artifact_ledger_recovery_budget", default=None)


@contextmanager
def operation_budget():
    current = _budget.get()
    if current is not None:
        yield current
        return
    current = Budget()
    token = _budget.set(current)
    try:
        yield current
    finally:
        _budget.reset(token)


def path(value):
    """A1-compatible host path validation without resolving symlinks."""
    try:
        raw = os.fspath(value)
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ValueError("Invalid path.")
        os.fsencode(raw)
        # Keep '..' until the original components have been checked with
        # descriptor-relative O_NOFOLLOW opens. Lexical abspath/normpath here
        # would erase a symlink (or missing/non-directory entry) before '..'.
        result = Path(raw).absolute()
        # Linux resolves exactly two leading slashes to the same root as one.
        # pathlib preserves that POSIX implementation-defined spelling, which
        # would otherwise break mount lookup and containment comparisons. Keep
        # every remaining component, especially '..', for the no-follow walk.
        if sys.platform.startswith("linux") and result.anchor == "//":
            result = Path("/", *result.parts[1:])
        return result
    except (TypeError, ValueError, UnicodeError) as error:
        raise RecoveryError("INVALID_INPUT", "A valid nonempty host path is required.") from error
