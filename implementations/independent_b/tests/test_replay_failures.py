"""A known original commit survives replay failures; invalid input stays rejected.

Connection/response faults are injected only in fresh synthetic databases.
The outcome oracle uses public records, complete exports and actual Blob bytes.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from infra_artifact_ledger import ledger as implementation
from cross_implementation import assert_metadata, assert_replay, portable_metadata
from test_failures import ConnectionFault
from test_public_contract import append, create, import_request, wire
from test_read_errors import database_error


class ReplayFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.sqlite'
        self.ledger = initialize(self.path)
        self.addCleanup(lambda: self.ledger.close())
        reuse = append(version='version:reuse', key='reuse', parents=['version:one'])
        reuse['body']['content_roots'] = []
        reuse['body']['blobs'] = []
        self.calls = [(create(), {}),
                      (append(), {'payloads': {'blob:one': b'original\x00\xff'}}),
                      (reuse, {'payloads': {}})]
        for request, kwargs in self.calls:
            self.ledger.execute(wire(request), **kwargs)
        bundle = self.ledger.export_bundle()
        imported = (import_request(), {'package': bundle['package_utf8'],
                                      'descriptor': bundle['descriptor_utf8']})
        self.ledger.execute(wire(imported[0]), **imported[1])
        self.calls.append(imported)
        self.expected = portable_metadata(self.ledger.export_bundle())
        self.before = self.path.read_bytes()

    @contextmanager
    def connection_fault(self, **kwargs):
        real = self.ledger._db
        self.ledger._db = ConnectionFault(real, 'ROLLBACK', **kwargs)
        try:
            yield
        finally:
            self.ledger._db = real
            # Persistent cleanup failure requires closing the handle. Read back
            # before replay so later calls cannot conceal an unwanted change.
            self.ledger.close()
            self.ledger = open_ledger(self.path)
            self.assert_preserved()

    def assert_preserved(self):
        self.assertEqual(self.path.read_bytes(), self.before)
        assert_metadata(self.ledger, self.expected)
        self.assertIs(type(self.ledger.read_blob('blob:one')), bytes)
        self.assertEqual(self.ledger.read_blob('blob:one'), b'original\x00\xff')
        for request, arguments in self.calls:
            assert_replay(self.ledger, request, arguments, self.expected)

    def assert_committed(self, error, request, code):
        self.assertEqual((error.code, error.commit_state), (code, 'committed'))
        operation, = [item for item in self.expected['idempotency_records']
                      if all(item[key] == request[key] for key in (
                          'idempotency_scope_ref', 'operation_kind', 'idempotency_key'))]
        self.assertEqual(error.details, {key: operation[key] for key in (
            'result_ref', 'idempotency_scope_ref', 'operation_kind',
            'idempotency_key', 'recorded_at')})

    def test_replay_rollback_failure_retains_original_commit(self):
        for request, kwargs in self.calls:
            for fault in ({}, {'after': True}, {'rollback_fails': True}):
                with self.subTest(kind=request['operation_kind'], key=request['idempotency_key'], fault=fault):
                    with self.connection_fault(**fault):
                        with self.assertRaises(LedgerError) as caught:
                            self.ledger.execute(wire(request), **kwargs)
                        self.assert_committed(caught.exception, request, 'IO_ERROR')

    def test_replay_response_failure_retains_original_commit(self):
        for request, kwargs in self.calls:
            with self.subTest(kind=request['operation_kind'], key=request['idempotency_key']):
                with self.connection_fault(after=True):
                    # Disable the rollback injection to reach response handling.
                    self.ledger._db.armed = False
                    with patch.object(implementation, '_response', side_effect=RuntimeError('response failure')):
                        with self.assertRaises(LedgerError) as caught:
                            self.ledger.execute(wire(request), **kwargs)
                    self.assert_committed(caught.exception, request, 'INTERNAL_ERROR')

    def test_replay_content_failure_survives_secondary_cleanup_failure(self):
        for request, kwargs in self.calls[1:]:
            for code in ('IO_ERROR', 'INTEGRITY_FAILURE'):
                for ended in (False, True):
                    with self.subTest(kind=request['operation_kind'], key=request['idempotency_key'], code=code, ended=ended):
                        with self.connection_fault(rollback_fails=True):
                            real = self.ledger._db.real
                            primary = (database_error(sqlite3.SQLITE_IOERR_READ) if code == 'IO_ERROR'
                                       else LedgerError('INTEGRITY_FAILURE', 'injected content corruption'))
                            def fail_read(identity):
                                if ended:
                                    real.execute('ROLLBACK')
                                raise primary
                            with patch.object(self.ledger, '_blob', side_effect=fail_read):
                                with self.assertRaises(LedgerError) as caught:
                                    self.ledger.execute(wire(request), **kwargs)
                            self.assert_committed(caught.exception, request, code)
                            if code == 'INTEGRITY_FAILURE':
                                self.assertIs(caught.exception, primary)

    def test_replay_interruption_is_not_replaced_by_cleanup_failure(self):
        request, kwargs = self.calls[1]
        with self.connection_fault(rollback_fails=True):
            primary = KeyboardInterrupt('injected replay interruption')
            with patch.object(self.ledger, '_blob', side_effect=primary):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    self.ledger.execute(wire(request), **kwargs)
            self.assertIs(caught.exception, primary)

    def test_cli_replay_failure_reports_committed_identity_and_exit_one(self):
        request, _ = self.calls[0]
        request_path = self.path.parent / 'request.json'
        request_path.write_bytes(wire(request))
        code = """
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[3])
from test_failures import ConnectionFault
from infra_artifact_ledger import cli, ledger as implementation
print(implementation.__file__, file=sys.stderr)
opened = cli.open_ledger
def open_fault(path):
    result = opened(path)
    result._db = ConnectionFault(result._db, 'ROLLBACK', rollback_fails=True)
    return result
with patch.object(cli, 'open_ledger', side_effect=open_fault):
    raise SystemExit(cli.main(['write', '--db', sys.argv[1], '--request', sys.argv[2]]))
"""
        result = subprocess.run([sys.executable, *(['-I'] if sys.flags.isolated else []),
            '-B', '-c', code, str(self.path), str(request_path), str(Path(__file__).parent)],
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr.decode().strip(), implementation.__file__)
        envelope = json.loads(result.stdout)
        self.assertEqual(set(envelope), {'status', 'code', 'message', 'commit_state', 'details'})
        self.assertEqual(envelope['status'], 'ERROR')
        self.assert_committed(LedgerError(envelope['code'], envelope['message'],
            envelope['commit_state'], envelope['details']), request, 'IO_ERROR')
        self.assert_preserved()

    def test_invalid_replay_input_does_not_claim_the_original_commit(self):
        reused, _ = self.calls[2]
        cases = [
            (create('artifact:different'), {}, 'IDEMPOTENCY_CONFLICT'),
            (append(), {'payloads': {'blob:extra': b'x'}}, 'INVALID_INPUT'),
            (append(), {'payloads': {'blob:one': b'wrong'}}, 'INTEGRITY_FAILURE'),
            (reused, {'payloads': {'blob:one': b'wrong'}}, 'INTEGRITY_FAILURE'),
        ]
        for request, kwargs, code in cases:
            with self.subTest(key=request['idempotency_key'], code=code):
                # Ordinary rollback succeeds here; this is a rejected input,
                # distinct from valid replay with a stored-content/read fault.
                with self.connection_fault():
                    self.ledger._db.armed = False
                    with self.assertRaises(LedgerError) as caught:
                        self.ledger.execute(wire(request), **kwargs)
                    self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                     (code, 'not_committed'))
                    self.assertIsNone(caught.exception.details)


if __name__ == '__main__':
    unittest.main()
