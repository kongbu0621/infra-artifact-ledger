"""Optional, context-local checkpoints for long internal verification loops."""

from contextlib import contextmanager
from contextvars import ContextVar


_callback = ContextVar("ledger_verification_checkpoint", default=None)


class CheckpointInterrupted(Exception):
    """Carry an internal caller's interruption through A1 error mapping."""

    def __init__(self, original):
        super().__init__("Internal verification checkpoint interrupted.")
        self.original = original


def checkpoint():
    callback = _callback.get()
    if callback is not None:
        try:
            callback()
        except Exception as error:
            raise CheckpointInterrupted(error) from error


@contextmanager
def checkpoint_scope(callback):
    token = _callback.set(callback)
    try:
        yield
    except CheckpointInterrupted as error:
        raise error.original from error
    finally:
        _callback.reset(token)
