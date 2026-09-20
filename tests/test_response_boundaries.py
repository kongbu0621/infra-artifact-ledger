"""Explicit public library/CLI response limits at 499,999/500,000/500,001 nodes.

Run separately: PYTHONPATH=src python tests/test_response_boundaries.py -v
Normal discovery skips this resource suite. Each fixture uses only three public
writes with individually legal requests; no database editing, limit patching or
large payload bytes are involved. The ordinary response stays below 8 MiB, so
these cases isolate the independent JSON node limit as a ledger accumulates.
"""
from __future__ import annotations

import gc
import json
from pathlib import Path
import platform
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from infra_artifact_ledger import LedgerError, initialize
from infra_artifact_ledger.validation import MAX_JSON, MAX_NODES, parse_json

try:
    from .acceptance_helpers import ARTIFACT, append, create, encode
except ImportError:
    from acceptance_helpers import ARTIFACT, append, create, encode


EXPLICIT_RESOURCE_RUN = __name__ == "__main__"


def node_count(value):
    """Independent structural count: object keys are not JSON value nodes."""
    pending = [value]
    count = 0
    while pending:
        item = pending.pop()
        count += 1
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return count


def success_envelope(data):
    return {"status": "OK", "commit_state": "not_applicable", "data": data}


@unittest.skipUnless(
    EXPLICIT_RESOURCE_RUN,
    "run tests/test_response_boundaries.py explicitly for actual response node limits",
)
class ActualResponseBoundaryTests(unittest.TestCase):
    def test_public_history_envelope_nodes_499999_500000_500001(self):
        self.assertEqual(MAX_NODES, 500000)
        self.assertEqual(MAX_JSON, 8 * 1024 * 1024)
        for target_nodes in (499999, 500000, 500001):
            started = time.monotonic()
            with self.subTest(response_nodes=target_nodes), tempfile.TemporaryDirectory(
                prefix="ledger-response-nodes-"
            ) as directory:
                database = Path(directory) / "ledger.sqlite"
                with initialize(database) as ledger:
                    ledger.execute(encode(create()))
                    first, first_payloads = append("nodes-first", b"")
                    first["body"]["version"]["external_source_refs"] = [
                        str(index) for index in range(250000)
                    ]
                    first_raw = encode(first)
                    self.assertLess(len(first_raw), MAX_JSON)
                    self.assertLess(node_count(parse_json(first_raw)), MAX_NODES)
                    ledger.execute(first_raw, payloads=first_payloads)

                    artifact = ledger.get_record("artifact", ARTIFACT)
                    first_record = ledger.get_record("version", "version:quarterly-nodes-first")
                    second, second_payloads = append("nodes-second", b"")
                    # Timestamp content changes neither scalar node count nor
                    # the comparison; the actual second record is read below.
                    predicted_second = {
                        **second["body"]["version"], "recorded_at": first_record["recorded_at"]
                    }
                    predicted = success_envelope({
                        "artifact": artifact,
                        "versions": [first_record, predicted_second],
                        "provenance_links": [],
                    })
                    additional_nodes = target_nodes - node_count(predicted)
                    second["body"]["version"]["external_source_refs"] = [
                        str(index) for index in range(additional_nodes)
                    ]
                    second_raw = encode(second)
                    self.assertLess(len(second_raw), MAX_JSON)
                    self.assertLess(node_count(parse_json(second_raw)), MAX_NODES)
                    ledger.execute(second_raw, payloads=second_payloads)
                    second_record = ledger.get_record("version", "version:quarterly-nodes-second")
                    expected = {
                        "artifact": artifact,
                        "versions": [first_record, second_record],
                        "provenance_links": [],
                    }
                    expected_envelope = success_envelope(expected)
                    expected_raw = encode(expected_envelope) + b"\n"
                    self.assertEqual(node_count(expected_envelope), target_nodes)
                    self.assertLess(len(expected_raw), MAX_JSON)

                    before = ledger.verify()
                    if target_nodes <= MAX_NODES:
                        self.assertEqual(ledger.get_history(ARTIFACT), expected)
                        self.assertEqual(parse_json(expected_raw), expected_envelope)
                    else:
                        with self.assertRaises(LedgerError) as caught:
                            ledger.get_history(ARTIFACT)
                        self.assertEqual(caught.exception.code, "RESOURCE_LIMIT")
                        self.assertEqual(caught.exception.commit_state, "not_applicable")

                    result = subprocess.run(
                        [sys.executable, "-m", "infra_artifact_ledger", "history",
                         "--db", str(database), "--artifact-id", ARTIFACT],
                        capture_output=True, check=False, timeout=60,
                    )
                    self.assertEqual(result.stderr, b"")
                    response = parse_json(result.stdout)
                    if target_nodes <= MAX_NODES:
                        self.assertEqual(result.returncode, 0)
                        self.assertEqual(response, expected_envelope)
                        self.assertEqual(node_count(response), target_nodes)
                    else:
                        self.assertEqual(result.returncode, 2)
                        self.assertEqual(response["status"], "ERROR")
                        self.assertEqual(response["code"], "RESOURCE_LIMIT")
                        self.assertEqual(response["commit_state"], "not_applicable")
                    self.assertEqual(ledger.verify(), before)
                    self.assertEqual(before["counts"]["versions"], 2)
                print(json.dumps({
                    "case": self._testMethodName, "response_nodes": target_nodes,
                    "response_bytes": len(expected_raw),
                    "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                    "platform": platform.platform(),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                }), flush=True)
                gc.collect()


if __name__ == "__main__":
    unittest.main()
