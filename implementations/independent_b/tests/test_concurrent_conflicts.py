"""A4-09: actual processes competing through the public write interface.

Both connections open before a pipe barrier releases them. No internal SQL,
clock, lock or transaction is patched. After both processes exit, reopen and
compare the entire committed state against the seed plus the winning request.
Set LEDGER_CONCURRENCY_EVIDENCE to an existing directory to retain new fixtures.
"""
import base64
import copy
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import infra_artifact_ledger as public


def worker(database, inputs):
    value = json.loads(Path(inputs).read_bytes())
    kwargs = {key: base64.b64decode(raw, validate=True)
              for key, raw in value['arguments'].items() if key != 'payloads'}
    if 'payloads' in value['arguments']:
        kwargs['payloads'] = {key: base64.b64decode(raw, validate=True)
                              for key, raw in value['arguments']['payloads'].items()}
    with public.open(database) as ledger:
        print(json.dumps(dict(pid=os.getpid(), module=public.__file__,
                              isolated=bool(sys.flags.isolated))), flush=True)
        if sys.stdin.readline() != 'go\n':
            raise RuntimeError('missing release from parent')
        try:
            response = ledger.execute(base64.b64decode(value['request'], validate=True), **kwargs)
        except public.LedgerError as exc:
            response = dict(status='ERROR', code=exc.code, message=exc.message,
                            commit_state=exc.commit_state)
            if exc.details is not None:
                response['details'] = exc.details
        print(json.dumps(response), flush=True)


class ConcurrentConflictTests(unittest.TestCase):
    def setUp(self):
        # Test fixtures/oracles are separate from the installed public module.
        from cross_implementation import (assert_metadata, assert_response,
            operation_for, portable_metadata, wire, append as rich_append)
        from test_public_contract import create, import_request
        self.wire, self.create, self.import_request = wire, create, import_request
        self.rich_append = rich_append
        self.metadata, self.operation_for = portable_metadata, operation_for
        self.assert_metadata, self.assert_response = assert_metadata, assert_response
        evidence = os.environ.get('LEDGER_CONCURRENCY_EVIDENCE')
        if evidence:
            self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=evidence))
        else:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name)

    def append_call(self, name, artifact='artifact:report'):
        payloads = {'blob:' + name: b'synthetic\x00\xff:' + name.encode(),
                    'empty:' + name: b''}
        request = self.rich_append(name, [], [
            ('报告/e\u0301.txt', 'blob:' + name, payloads['blob:' + name]),
            ('../logical-only', 'empty:' + name, b'')])
        request['body']['version']['artifact_id'] = artifact
        return request, dict(payloads=payloads)

    def source_bundle(self, name):
        with public.initialize(self.root / (name + '.sqlite')) as source:
            source.execute(self.wire(self.create('artifact:' + name, key='create:' + name)))
            request, kwargs = self.append_call(name, 'artifact:' + name)
            source.execute(self.wire(request), **kwargs)
            empty = public.initialize(self.root / ('empty-' + name + '.sqlite'))
            with empty:
                bundle = empty.export_bundle()
            source.execute(self.wire(self.import_request('receipt:' + name, key='empty:' + name)),
                           package=bundle['package_utf8'], descriptor=bundle['descriptor_utf8'])
            return source.export_bundle()

    def inputs(self, call):
        request, kwargs = call
        encode = lambda raw: base64.b64encode(raw).decode('ascii')
        arguments = {key: encode(raw) for key, raw in kwargs.items() if key != 'payloads'}
        if 'payloads' in kwargs:
            arguments['payloads'] = {key: encode(raw) for key, raw in kwargs['payloads'].items()}
        return dict(request=encode(self.wire(request)), arguments=arguments)

    def race(self, database, calls, directory, reverse):
        processes, ready, outputs = [], [], []
        try:
            for index, call in enumerate(calls):
                inputs = directory / f'input-{index}.json'
                inputs.write_bytes(self.wire(self.inputs(call)))
                isolation = ['-I'] if sys.flags.isolated else []
                processes.append(subprocess.Popen([sys.executable, *isolation, __file__,
                    '--worker', str(database), str(inputs)], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            for process in processes:
                self.assertTrue(select.select([process.stdout], [], [], 15)[0], 'worker readiness timeout')
                line = process.stdout.readline()
                self.assertTrue(line, 'worker exited before opening the database')
                item = json.loads(line)
                self.assertEqual(item, dict(pid=process.pid, module=public.__file__,
                                            isolated=bool(sys.flags.isolated)))
                ready.append(item)
            self.assertEqual(len({item['pid'] for item in ready}), 2)
            for process in processes[::-1] if reverse else processes:
                process.stdin.write(b'go\n')
                process.stdin.flush()
            for index, process in enumerate(processes):
                stdout, stderr = process.communicate(timeout=30)
                (directory / f'worker-{index}.stdout').write_bytes(stdout)
                (directory / f'worker-{index}.stderr').write_bytes(stderr)
                self.assertEqual(process.returncode, 0, (stdout, stderr))
                self.assertEqual(stderr, b'')
                outputs.append(json.loads(stdout))
            (directory / 'race.json').write_bytes(self.wire(dict(ready=ready, responses=outputs)))
            return outputs
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    def assert_error(self, error, code):
        self.assertEqual(error.code, code)
        self.assertEqual(error.commit_state, 'not_committed')

    def expected_state(self, before, call, response):
        value, kwargs = call
        expected = copy.deepcopy(before)
        body, kind, when = value['body'], value['operation_kind'], response['recorded_at']
        incoming = None
        if kind == 'create_artifact':
            result = body['artifact']['artifact_id']
            expected['artifacts'].append({**body['artifact'], 'recorded_at': when})
        elif kind == 'append_version':
            result = body['version']['version_id']
            expected['versions'].append({**body['version'], 'recorded_at': when})
            for collection in ('blobs', 'manifests', 'content_roots', 'provenance_links'):
                expected[collection].extend({**item, 'recorded_at': when}
                    if collection == 'provenance_links' else copy.deepcopy(item) for item in body[collection])
        else:
            result = body['import_receipt_id']
            incoming = self.metadata(dict(package_utf8=kwargs['package'], descriptor_utf8=kwargs['descriptor']))
            for collection, records in incoming.items():
                if isinstance(records, list):
                    expected[collection].extend(copy.deepcopy(records))
            expected['import_receipts'].append(dict(import_receipt_id=result, recorded_at=when,
                imported_artifact_refs=sorted(item['artifact_id'] for item in incoming['artifacts'])))
        operation = self.operation_for(value, result, when, incoming)
        expected['idempotency_records'].append(operation)
        self.assert_response(response, operation, False)
        return expected, operation

    def assert_state(self, ledger, expected, payloads):
        self.assert_metadata(ledger, expected)
        for artifact in expected['artifacts']:
            versions = sorted((item for item in expected['versions']
                if item['artifact_id'] == artifact['artifact_id']), key=lambda item: item['version_id'])
            version_ids = {item['version_id'] for item in versions}
            provenance = sorted((item for item in expected['provenance_links']
                if item['subject_version_ref'] in version_ids), key=lambda item: item['provenance_id'])
            self.assertEqual(self.wire(ledger.get_history(artifact['artifact_id'])),
                self.wire(dict(artifact=artifact, versions=versions, provenance_links=provenance)))
        for identity, raw in payloads.items():
            actual = ledger.read_blob(identity)
            self.assertIs(type(actual), bytes, identity)
            self.assertEqual(actual, raw)

    def exercise(self, calls, conflict):
        for number in range(3):
            with self.subTest(round=number):
                directory = self.root / str(number)
                directory.mkdir()
                database = directory / 'target.sqlite'
                with public.initialize(database) as seed:
                    seed.execute(self.wire(self.create()))
                    seed_request, seed_kwargs = self.append_call('retained')
                    seed.execute(self.wire(seed_request), **seed_kwargs)
                    before = self.metadata(seed.export_bundle())
                responses = self.race(database, calls, directory, reverse=bool(number % 2))
                winners = [index for index, result in enumerate(responses) if result['status'] == 'COMMITTED']
                self.assertEqual(len(winners), 1, responses)
                winner = winners[0]
                loser = 1 - winner
                rejected = responses[loser]
                self.assertEqual(rejected['status'], 'ERROR')
                self.assertEqual(rejected['commit_state'], 'not_committed')
                self.assertIn(rejected['code'], (conflict, 'BUSY'))
                expected, operation = self.expected_state(before, calls[winner], responses[winner])
                payloads = dict(seed_kwargs['payloads'])
                kwargs = calls[winner][1]
                payloads.update(kwargs.get('payloads', {}))
                if 'package' in kwargs:
                    payloads.update({item['blob_ref']: base64.b64decode(item['data'], validate=True)
                                     for item in json.loads(kwargs['package'])['payloads']})
                # Both old writers have exited: no in-flight call can commit later.
                with public.open(database) as ledger:
                    self.assert_state(ledger, expected, payloads)
                    value, kwargs = calls[loser]
                    if conflict == 'IDENTITY_CONFLICT':
                        with self.assertRaises(public.LedgerError) as caught:
                            ledger.get_operation(value['idempotency_scope_ref'],
                                                 value['operation_kind'], value['idempotency_key'])
                        self.assertEqual(caught.exception.code, 'NOT_FOUND')
                    # A BUSY call is retried once only, after the durable winner is known.
                    with self.assertRaises(public.LedgerError) as caught:
                        ledger.execute(self.wire(value), **kwargs)
                    self.assert_error(caught.exception, conflict)
                    self.assert_state(ledger, expected, payloads)
                    value, kwargs = calls[winner]
                    self.assert_response(ledger.execute(self.wire(value), **kwargs), operation, True)
                    self.assert_state(ledger, expected, payloads)
                    (directory / 'expected.json').write_bytes(self.wire(expected))
                    (directory / 'verified.json').write_bytes(self.wire(ledger.verify()))

    def test_create_same_key_different_request(self):
        self.exercise([(self.create('artifact:left', key='race'), {}),
                       (self.create('artifact:right', key='race'), {})], 'IDEMPOTENCY_CONFLICT')

    def test_readback_rejects_bytes_like_values(self):
        with public.initialize(self.root / 'readback.sqlite') as ledger:
            ledger.execute(self.wire(self.create()))
            request, kwargs = self.append_call('readback')
            ledger.execute(self.wire(request), **kwargs)
            expected = self.metadata(ledger.export_bundle())
            self.assert_state(ledger, expected, kwargs['payloads'])
            read_blob = ledger.read_blob
            for convert in (bytearray, memoryview):
                with self.subTest(return_type=convert.__name__):
                    with patch.object(ledger, 'read_blob',
                                      side_effect=lambda ref: convert(read_blob(ref))):
                        with self.assertRaises(AssertionError):
                            self.assert_state(ledger, expected, kwargs['payloads'])
            self.assert_state(ledger, expected, kwargs['payloads'])

    def test_create_same_id_different_keys(self):
        self.exercise([(self.create('artifact:new', key='left'), {}),
                       (self.create('artifact:new', key='right'), {})], 'IDENTITY_CONFLICT')

    def test_append_same_key_different_request(self):
        calls = [self.append_call(name) for name in ('left', 'right')]
        for request, _ in calls:
            request['idempotency_key'] = 'race'
        self.exercise(calls, 'IDEMPOTENCY_CONFLICT')

    def test_append_same_id_different_keys(self):
        calls = [self.append_call(name) for name in ('left', 'right')]
        for request, _ in calls:
            request['body']['version']['version_id'] = 'version:shared'
            request['body']['provenance_links'][0]['subject_version_ref'] = 'version:shared'
        self.exercise(calls, 'IDENTITY_CONFLICT')

    def import_calls(self, same_key):
        calls = []
        for name in ('left', 'right'):
            bundle = self.source_bundle(name)
            calls.append((self.import_request('receipt:shared', key='race' if same_key else name),
                dict(package=bundle['package_utf8'], descriptor=bundle['descriptor_utf8'])))
        return calls

    def test_import_same_key_different_package(self):
        self.exercise(self.import_calls(True), 'IDEMPOTENCY_CONFLICT')

    def test_import_same_receipt_different_keys(self):
        self.exercise(self.import_calls(False), 'IDENTITY_CONFLICT')

    def test_create_and_append_share_owned_id_space(self):
        append_call = self.append_call('shared')
        self.exercise([(self.create('version:shared', key='race-create'), {}), append_call], 'IDENTITY_CONFLICT')


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        worker(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
