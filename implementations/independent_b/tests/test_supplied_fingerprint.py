"""Caller fingerprint assertions have the same API/CLI meaning in A and B."""
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from test_public_contract import append, create, import_request, sha, wire


class SuppliedFingerprintTests(unittest.TestCase):
    def request(self, kind, ledger, root):
        if kind == 'create_artifact':
            request, kwargs, bundle = create(), {}, None
        elif kind == 'append_version':
            ledger.execute(wire(create()))
            request, kwargs, bundle = append(), dict(payloads={'blob:one': b'original\x00\xff'}), None
        else:
            with initialize(root / 'empty.sqlite') as empty:
                exported = empty.export_bundle()
            bundle = json.loads(base64.b64decode(json.loads(exported['package_utf8'])['metadata']['data']))
            request, kwargs = import_request(), dict(package=exported['package_utf8'],
                                                     descriptor=exported['descriptor_utf8'])
        return request, kwargs, bundle

    def fingerprint(self, request, bundle):
        envelope = {key: request[key] for key in
                    ('contract_version', 'idempotency_scope_ref', 'operation_kind', 'body')}
        if request['operation_kind'] == 'import_bundle':
            envelope['body'] = dict(import_receipt_id=request['body']['import_receipt_id'], bundle=bundle)
        return sha(wire(envelope))

    def operation(self, ledger, request):
        return ledger.get_operation(request['idempotency_scope_ref'],
                                    request['operation_kind'], request['idempotency_key'])

    def reject(self, ledger, path, request, kwargs, code):
        before, physical = ledger.export_bundle(), path.read_bytes()
        with self.assertRaises(LedgerError) as caught:
            ledger.execute(wire(request), **kwargs)
        self.assertEqual((caught.exception.code, caught.exception.commit_state), (code, 'not_committed'))
        self.assertEqual(ledger.export_bundle(), before)
        self.assertEqual(path.read_bytes(), physical)

    def test_supplied_fingerprint_validation_precedes_commit_and_replay(self):
        for kind in ('create_artifact', 'append_version', 'import_bundle'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / 'ledger.sqlite'
                with initialize(path) as ledger:
                    request, kwargs, bundle = self.request(kind, ledger, root)
                    correct = {**request, 'request_fingerprint': self.fingerprint(request, bundle)}
                    wrong = {**request, 'request_fingerprint': sha(b'not this request')}
                    self.assertNotEqual(correct['request_fingerprint'], wrong['request_fingerprint'])
                    self.reject(ledger, path, wrong, kwargs, 'INVALID_INPUT')
                    with self.assertRaises(LedgerError) as caught:
                        self.operation(ledger, request)
                    self.assertEqual(caught.exception.code, 'NOT_FOUND')
                    result = ledger.execute(wire(correct), **kwargs)
                    self.assertIs(result['replayed'], False)
                    original = self.operation(ledger, request)
                    self.assertEqual(original['idempotency_record']['request_fingerprint'], correct['request_fingerprint'])
                    self.reject(ledger, path, wrong, kwargs, 'INVALID_INPUT')
                    changed = copy.deepcopy(request)
                    if kind == 'create_artifact':
                        changed['body']['artifact']['namespace_ref'] = 'demo:other'
                    elif kind == 'append_version':
                        changed['body']['version']['principal_ref'] = 'principal:other'
                    else:
                        changed['body']['import_receipt_id'] = 'receipt:other'
                    changed['request_fingerprint'] = self.fingerprint(changed, bundle)
                    self.reject(ledger, path, changed, kwargs, 'IDEMPOTENCY_CONFLICT')
                    self.assertEqual(self.operation(ledger, request), original)
                    before = ledger.export_bundle()
                with open_ledger(path) as ledger:
                    replay = ledger.execute(wire(correct), **kwargs)
                    self.assertEqual(replay, {**result, 'replayed': True})
                    self.assertIs(replay['replayed'], True)
                    self.assertEqual(ledger.export_bundle(), before)

    def test_cli_rejects_mismatched_fingerprint_with_exit_two(self):
        for kind in ('create_artifact', 'append_version', 'import_bundle'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / 'ledger.sqlite'
                with initialize(path) as ledger:
                    request, kwargs, bundle = self.request(kind, ledger, root)
                    request_file = root / 'request.json'
                    isolation = ['-I'] if sys.flags.isolated else []
                    command = [sys.executable, *isolation, '-B', '-m', 'infra_artifact_ledger',
                               'write', '--db', str(path), '--request', str(request_file)]
                    if 'payloads' in kwargs:
                        payload_file = root / 'payload'
                        payload_file.write_bytes(kwargs['payloads']['blob:one'])
                        mapping = root / 'payloads.json'
                        mapping.write_bytes(wire([dict(blob_ref='blob:one', input_path=str(payload_file))]))
                        command += ['--payload-map', str(mapping)]
                    if 'package' in kwargs:
                        for name in ('package', 'descriptor'):
                            (root / name).write_bytes(kwargs[name])
                            command += ['--' + name, str(root / name)]
                    correct = {**request, 'request_fingerprint': self.fingerprint(request, bundle)}
                    wrong = {**request, 'request_fingerprint': sha(b'not this request')}
                    for committed in (False, True):
                        before, physical = ledger.export_bundle(), path.read_bytes()
                        request_file.write_bytes(wire(wrong))
                        failed = subprocess.run(command, capture_output=True, timeout=20, check=False)
                        self.assertEqual(failed.returncode, 2, failed.stdout)
                        self.assertEqual(failed.stderr, b'')
                        error = json.loads(failed.stdout)
                        self.assertEqual((error['status'], error['code'], error['commit_state']),
                                         ('ERROR', 'INVALID_INPUT', 'not_committed'))
                        self.assertEqual(ledger.export_bundle(), before)
                        self.assertEqual(path.read_bytes(), physical)
                        request_file.write_bytes(wire(correct))
                        success = subprocess.run(command, capture_output=True, timeout=20, check=False)
                        self.assertEqual(success.returncode, 0, success.stdout)
                        response = json.loads(success.stdout)
                        self.assertEqual(response['status'], 'COMMITTED')
                        self.assertIs(response['replayed'], committed)
                        if committed:
                            self.assertEqual(response, {**first, 'replayed': True})
                            self.assertEqual(ledger.export_bundle(), before)
                        else:
                            first = response


if __name__ == '__main__':
    unittest.main()
