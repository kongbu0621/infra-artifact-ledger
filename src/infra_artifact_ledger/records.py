"""Constants of the public, bounded local ledger contract."""

PROFILE = "bounded-local-v0.1"
CONTRACT_VERSION = "0.1.0"
TRANSPORT_VERSION = "0.1.0"
SCHEMA_ID = "urn:code-driver-theory:artifact-ledger:v0.1:candidate"
THEORY_BASELINE = {
    "repository": "kongbu0621/code-driver-theory",
    "commit": "9e2bc8217f05d5b604e24fa56dad2525f35983f1",
}
KINDS = {
    "artifact": ("artifacts", "artifact_id"),
    "version": ("versions", "version_id"),
    "content_root": ("content_roots", "content_root_ref"),
    "blob": ("blobs", "blob_ref"),
    "manifest": ("manifests", "manifest_ref"),
    "provenance_link": ("provenance_links", "provenance_id"),
    "import_receipt": ("import_receipts", "import_receipt_id"),
}


def empty_metadata():
    """Return a fresh metadata document, including the optional receipts array."""
    return {
        "contract_version": CONTRACT_VERSION,
        "contract_status": "candidate",
        "schema_id": SCHEMA_ID,
        "theory_baseline": dict(THEORY_BASELINE),
        **{collection: [] for collection, _ in KINDS.values()},
        "idempotency_records": [],
    }
