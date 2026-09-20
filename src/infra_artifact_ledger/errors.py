"""Stable public failures; raw storage exceptions are not part of the API."""

EXIT_CODES = {
    "INVALID_INPUT": 2, "UNSUPPORTED_VERSION": 2, "UNSUPPORTED_PROFILE": 2,
    "RESOURCE_LIMIT": 2, "NOT_FOUND": 3, "IDENTITY_CONFLICT": 4,
    "IDEMPOTENCY_CONFLICT": 4, "INTEGRITY_FAILURE": 5, "BUSY": 6,
    "DURABILITY_UNKNOWN": 7, "IO_ERROR": 1, "INTERNAL_ERROR": 1,
}


class LedgerError(Exception):
    """A failure with the explicitly known state of the relevant commit."""

    def __init__(self, code, message, commit_state="not_committed", details=None):
        if code not in EXIT_CODES:
            raise ValueError("Unknown LedgerError code")
        if commit_state not in {"not_committed", "unknown", "committed", "not_applicable"}:
            raise ValueError("Unknown commit state")
        if not isinstance(message, str) or (details is not None and not isinstance(details, dict)):
            raise TypeError("message must be a string and details must be an object")
        super().__init__(message)
        self.code = code
        self.message = message
        self.commit_state = commit_state
        self.details = details

    def to_envelope(self):
        result = {"status": "ERROR", "code": self.code, "message": self.message,
                  "commit_state": self.commit_state}
        if self.details is not None:
            result["details"] = self.details
        return result
