"""Public-contract checks with independently constructed synthetic inputs.

Private SQLite access below is solely deliberate corruption/fault injection.
No implementation A helper or implementation B validator is used as an oracle.
"""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger

PROFILE = 'bounded-local-v0.1'


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def sha(raw):
    return dict(algorithm='sha256', value=hashlib.sha256(raw).hexdigest())


def request(kind, body, key='first', scope='demo:scope'):
    return dict(profile=PROFILE, contract_version='0.1.0', operation_kind=kind,
                idempotency_scope_ref=scope, idempotency_key=key, body=body)


def create(artifact='artifact:report', key='create'):
    return request('create_artifact', dict(artifact=dict(artifact_id=artifact,
        namespace_ref='demo:reports', type_ref=dict(namespace='demo', type_id='report', type_version='1'))), key)


def append(version='version:one', blob='blob:one', root='root:one', raw=b'original\x00\xff', parents=None, key='append'):
    return request('append_version', dict(version=dict(version_id=version, artifact_id='artifact:report',
        content_root_ref=root, parent_version_refs=parents or [], external_source_refs=['external:original'],
        capture_refs=[], handling_policy_refs=[], principal_ref='principal:owner'),
        content_roots=[dict(content_root_ref=root, kind='blob', blob_ref=blob)],
        blobs=[dict(blob_ref=blob, digest=sha(raw), byte_length=len(raw), payload_availability='available')],
        manifests=[], provenance_links=[]), key)


def import_request(receipt='receipt:one', key='import'):
    return request('import_bundle', dict(import_receipt_id=receipt), key)


def repackage(bundle, change_metadata=None, change_package=None):
    package = json.loads(bundle['package_utf8'])
    if change_metadata:
        metadata = json.loads(base64.b64decode(package['metadata']['data']))
        change_metadata(metadata)
        raw = wire(metadata)
        package['metadata'] = dict(encoding='base64', data=base64.b64encode(raw).decode(),
                                   byte_length=len(raw), digest=sha(raw))
    if change_package:
        change_package(package)
    raw = wire(package)
    return dict(package=raw, descriptor=wire(dict(transport_version='0.1.0',
                package_byte_length=len(raw), package_digest=sha(raw))))


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'b.sqlite'
        self.ledger = initialize(self.db)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.ledger.close)

    def assert_error(self, code, call, state=None):
        with self.assertRaises(LedgerError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        if state:
            self.assertEqual(caught.exception.commit_state, state)
        return caught.exception

    def seed(self):
        self.ledger.execute(wire(create()))
        self.ledger.execute(wire(append()), payloads={'blob:one': b'original\x00\xff'})

    def test_published_fingerprint_vector_and_control_key(self):
        value = create(key='key\x00\n非ASCII')
        result = self.ledger.execute(wire(value))
        op = self.ledger.get_operation('demo:scope', 'create_artifact', value['idempotency_key'])
        self.assertEqual(op['idempotency_record']['request_fingerprint']['value'],
                         '9cf1e431202cf2afcbacdacc3d0917bc5a77b409129911f107d902d134cbf8b8')
        again = self.ledger.execute(wire(value))
        self.assertTrue(again.pop('replayed'))
        result.pop('replayed')
        self.assertEqual(again, result)
        self.assertEqual(self.ledger.verify()['counts']['idempotency_records'], 1)

    def test_different_key_and_same_key_conflicts(self):
        self.ledger.execute(wire(create()))
        self.assert_error('IDENTITY_CONFLICT', lambda: self.ledger.execute(wire(create(key='new'))), 'not_committed')
        self.assert_error('IDEMPOTENCY_CONFLICT', lambda: self.ledger.execute(wire(create('artifact:other'))), 'not_committed')
        self.assertEqual(self.ledger.verify()['counts']['artifacts'], 1)

    def test_strict_json_rejections_leave_empty_database(self):
        value = wire(create())
        cases = [b'\xef\xbb\xbf' + value, value + b'x', b'{"body":{},"body":{}}',
                 b'NaN', b'Infinity', b'1.0', b'1e0', b'"\\ud800"', b'"\xff"',
                 b'[' * 17 + b'0' + b']' * 17]
        for raw in cases:
            with self.subTest(raw=raw[:25]):
                with self.assertRaises(LedgerError):
                    self.ledger.execute(raw)
        for bad in ({**create(), 'unknown': 1}, {**create(), 'contract_version': '2'},
                    {**create(), 'profile': 'other'}, {**create(), 'operation_kind': []}):
            with self.subTest(bad=bad):
                with self.assertRaises(LedgerError) as caught:
                    self.ledger.execute(wire(bad))
                self.assertNotEqual(caught.exception.code, 'INTERNAL_ERROR')
        self.assertEqual(self.ledger.verify()['counts']['artifacts'], 0)

    def test_new_empty_blob_requires_explicit_bytes(self):
        self.ledger.execute(wire(create()))
        raw = wire(append(raw=b''))
        self.assert_error('INTEGRITY_FAILURE', lambda: self.ledger.execute(raw, payloads={}), 'not_committed')
        self.ledger.execute(raw, payloads={'blob:one': b''})
        self.assertEqual(self.ledger.read_blob('blob:one'), b'')

    def test_invalid_argument_combinations(self):
        cases = [(wire(create()), dict(payloads={})), (wire(append()), {}),
                 (wire(import_request()), dict(package=b'', descriptor=None)),
                 (bytearray(wire(create())), {})]
        for raw, kwargs in cases:
            self.assert_error('INVALID_INPUT', lambda: self.ledger.execute(raw, **kwargs), 'not_committed')
        self.assertEqual(self.ledger.verify()['counts']['artifacts'], 0)

    def test_auxiliary_identity_and_missing_parent_are_atomic(self):
        self.ledger.execute(wire(create()))
        value = append(root='artifact:report')
        self.assert_error('IDENTITY_CONFLICT', lambda: self.ledger.execute(wire(value), payloads={'blob:one': b'original\x00\xff'}))
        value = append(parents=['version:missing'])
        self.assert_error('INTEGRITY_FAILURE', lambda: self.ledger.execute(wire(value), payloads={'blob:one': b'original\x00\xff'}))
        self.assertEqual(self.ledger.verify()['counts']['blobs'], 0)
        self.assertEqual(self.ledger.verify()['counts']['idempotency_records'], 1)

    def test_manifest_logical_keys_and_content_identity(self):
        self.ledger.execute(wire(create()))
        value = append(raw=b'')
        body = value['body']
        body['content_roots'][0] = dict(content_root_ref='root:one', kind='manifest', manifest_ref='manifest:one')
        entries = [dict(entry_key='报告/é.txt', blob_ref='blob:abc'),
                   dict(entry_key='../not-a-path', blob_ref='blob:empty')]
        body['manifests'] = [dict(manifest_ref='manifest:one', entries=entries,
            digest=dict(algorithm='sha256', value='1bdde4c4e5c733a4ea8b879a410dc036b86b1da192a9799522cc62ba456ec253'))]
        body['blobs'] = [dict(blob_ref=name, digest=sha(b''), byte_length=0, payload_availability='available')
                         for name in ('blob:abc', 'blob:empty')]
        self.ledger.execute(wire(value), payloads={'blob:abc': b'', 'blob:empty': b''})
        self.assertEqual(self.ledger.get_record('manifest', 'manifest:one')['entries'], entries)
        self.assertEqual(self.ledger.verify()['verified_blob_count'], 2)
        self.assertFalse((self.root.parent / 'not-a-path').exists())

    def test_reuse_empty_payload_map_and_explicit_merge(self):
        self.seed()
        for name in ('left', 'right'):
            value = append(version='version:' + name, parents=['version:one'], key=name)
            value['body'].update(content_roots=[], blobs=[])
            self.ledger.execute(wire(value), payloads={})
        value = append(version='version:merge', parents=['version:right', 'version:left'], key='merge')
        value['body'].update(content_roots=[], blobs=[])
        self.ledger.execute(wire(value), payloads={})
        self.assertEqual(self.ledger.get_record('version', 'version:merge')['parent_version_refs'],
                         ['version:right', 'version:left'])
        self.assertEqual(self.ledger.verify()['counts']['blobs'], 1)

    def test_bad_retransmitted_reused_blob_is_rejected_input(self):
        self.seed()
        value = append(version='version:reused', parents=['version:one'], key='reuse')
        value['body'].update(content_roots=[], blobs=[])
        self.ledger.execute(wire(value), payloads={})
        self.assert_error('INTEGRITY_FAILURE', lambda: self.ledger.execute(wire(value),
                          payloads={'blob:one': b'bad-input'}), 'not_committed')
        self.assertEqual(self.ledger.read_blob('blob:one'), b'original\x00\xff')
        self.assertTrue(self.ledger.execute(wire(value), payloads={})['replayed'])

    def test_corruption_does_not_erase_success_or_repair_on_replay(self):
        self.seed()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE b_objects SET content=? WHERE owned_id='blob:one'", (b'corrupt',))
        op = self.ledger.get_operation('demo:scope', 'append_version', 'append')
        self.assertEqual(op['result']['version_id'], 'version:one')
        self.assert_error('INTEGRITY_FAILURE', lambda: self.ledger.read_blob('blob:one'), 'not_applicable')
        error = self.assert_error('INTEGRITY_FAILURE', self.ledger.verify, 'not_applicable')
        self.assertEqual(set(error.details), {'failures', 'truncated'})
        error = self.assert_error('INTEGRITY_FAILURE', lambda: self.ledger.execute(wire(append()),
                        payloads={'blob:one': b'original\x00\xff'}), 'committed')
        self.assertEqual(error.details['result_ref'], 'version:one')
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT content FROM b_objects WHERE owned_id='blob:one'").fetchone()[0], b'corrupt')

    def test_import_replay_after_append_and_new_key_history_conflict(self):
        self.seed()
        bundle = self.ledger.export_bundle()
        with initialize(self.root / 'target.sqlite') as target:
            arguments = dict(package=bundle['package_utf8'], descriptor=bundle['descriptor_utf8'])
            result = target.execute(wire(import_request()), **arguments)
            self.assertEqual(target.get_history('artifact:report'), self.ledger.get_history('artifact:report'))
            self.assertEqual(target.read_blob('blob:one'), b'original\x00\xff')
            target.execute(wire(append('version:two', 'blob:two', 'root:two', b'new', ['version:one'], 'new')),
                           payloads={'blob:two': b'new'})
            repeat = target.execute(wire(import_request()), **arguments)
            self.assertEqual(repeat['recorded_at'], result['recorded_at'])
            self.assertTrue(repeat['replayed'])
            self.assert_error('IDENTITY_CONFLICT', lambda: target.execute(wire(import_request('receipt:two', 'new-key')), **arguments))
            self.assertEqual(target.verify()['counts']['import_receipts'], 1)

    def test_import_validation_late_failure_no_partial_records(self):
        self.seed()
        bundle = self.ledger.export_bundle()
        mutations = [lambda p: p['payloads'].clear(),
                     lambda p: p['payloads'].append(copy.deepcopy(p['payloads'][0])),
                     lambda p: p['payloads'][0].update(data='YQ=='),
                     lambda p: p['metadata'].update(data=p['metadata']['data'] + '\n')]
        with initialize(self.root / 'target.sqlite') as target:
            for mutate in mutations:
                arguments = repackage(bundle, change_package=mutate)
                with self.assertRaises(LedgerError):
                    target.execute(wire(import_request()), **arguments)
                self.assertEqual(target.verify()['counts']['artifacts'], 0)

    def test_import_erased_receipt_collision_and_historical_key_conflict(self):
        self.seed()
        bundle = self.ledger.export_bundle()
        with initialize(self.root / 'target.sqlite') as target:
            erased = repackage(bundle, lambda m: m['blobs'][0].update(payload_availability='erased'))
            self.assert_error('UNSUPPORTED_PROFILE', lambda: target.execute(wire(import_request()), **erased))
            arguments = dict(package=bundle['package_utf8'], descriptor=bundle['descriptor_utf8'])
            self.assert_error('IDENTITY_CONFLICT', lambda: target.execute(wire(import_request('artifact:report')), **arguments))
            target.execute(wire(create('artifact:other')))
            self.assert_error('IDEMPOTENCY_CONFLICT', lambda: target.execute(wire(import_request()), **arguments))
            self.assertEqual(target.verify()['counts']['artifacts'], 1)

    def test_current_import_key_cannot_claim_imported_history(self):
        self.seed()
        bundle = self.ledger.export_bundle()
        with initialize(self.root / 'middle.sqlite') as middle:
            middle.execute(wire(import_request()), package=bundle['package_utf8'], descriptor=bundle['descriptor_utf8'])
            second = middle.export_bundle()
        with initialize(self.root / 'target.sqlite') as target:
            self.assert_error('IDEMPOTENCY_CONFLICT', lambda: target.execute(wire(import_request('receipt:new')),
                package=second['package_utf8'], descriptor=second['descriptor_utf8']))
            self.assertEqual(target.verify()['counts']['artifacts'], 0)

    def test_empty_export_import(self):
        bundle = self.ledger.export_bundle()
        result = self.ledger.execute(wire(import_request()), package=bundle['package_utf8'], descriptor=bundle['descriptor_utf8'])
        self.assertEqual(self.ledger.get_record('import_receipt', result['result_ref'])['imported_artifact_refs'], [])
        self.assertEqual(self.ledger.verify()['counts']['import_receipts'], 1)

    def test_read_descriptor_mismatch_and_unrelated_content(self):
        self.seed()
        bundle = self.ledger.export_bundle()
        self.assert_error('INTEGRITY_FAILURE', lambda: self.ledger.execute(wire(import_request()),
             package=bundle['package_utf8'] + b' ', descriptor=bundle['descriptor_utf8']))
        value = append('version:two', key='new')
        value['body']['blobs'].append(dict(blob_ref='blob:unused', digest=sha(b''), byte_length=0, payload_availability='available'))
        self.assert_error('INVALID_INPUT', lambda: self.ledger.execute(wire(value), payloads={'blob:unused': b''}))
        self.assertEqual(self.ledger.verify()['counts']['versions'], 1)

    def test_close_reopen_and_failed_later_write_keeps_prior_commit(self):
        with initialize(self.root / 'context.sqlite') as handle:
            handle.execute(wire(create()))
            self.assert_error('IDENTITY_CONFLICT', lambda: handle.execute(wire(create(key='new'))))
        with open_ledger(self.root / 'context.sqlite') as handle:
            self.assertEqual(handle.get_record('artifact', 'artifact:report')['artifact_id'], 'artifact:report')

    def test_exclusive_init_and_missing_open(self):
        self.assert_error('IDENTITY_CONFLICT', lambda: initialize(self.db))
        missing = self.root / 'missing.sqlite'
        self.assert_error('NOT_FOUND', lambda: open_ledger(missing))
        self.assertFalse(missing.exists())
        missing.symlink_to(self.root / 'absent')
        self.assert_error('IDENTITY_CONFLICT', lambda: initialize(missing))
        self.assertTrue(missing.is_symlink())

    def test_foreign_database_rejected_without_change(self):
        foreign = self.root / 'foreign.sqlite'
        with sqlite3.connect(foreign) as db:
            db.execute('CREATE TABLE original (id TEXT)')
        before = foreign.read_bytes()
        self.assert_error('UNSUPPORTED_VERSION', lambda: open_ledger(foreign))
        self.assertEqual(foreign.read_bytes(), before)

    def test_competing_writer_busy_and_retry(self):
        lock = sqlite3.connect(self.db, isolation_level=None)
        self.addCleanup(lock.close)
        lock.execute('BEGIN IMMEDIATE')
        self.assert_error('BUSY', lambda: self.ledger.execute(wire(create())), 'not_committed')
        lock.execute('ROLLBACK')
        self.assertFalse(self.ledger.execute(wire(create()))['replayed'])

    def test_commit_busy_rolls_back_then_retries_same_identity(self):
        lock = sqlite3.connect(self.db, isolation_level=None)
        self.addCleanup(lock.close)
        lock.execute('BEGIN')
        lock.execute('SELECT * FROM b_objects').fetchall()
        self.assert_error('BUSY', lambda: self.ledger.execute(wire(create())), 'not_committed')
        lock.execute('ROLLBACK')
        self.assertEqual(self.ledger.verify()['counts']['artifacts'], 0)
        self.ledger.execute(wire(create()))
        self.assertTrue(self.ledger.execute(wire(create()))['replayed'])

    def cli(self, *args, stdout=subprocess.PIPE):
        isolation = ['-I'] if sys.flags.isolated else []
        return subprocess.run([sys.executable, *isolation, '-m', 'infra_artifact_ledger', *args],
                              stdout=stdout, stderr=subprocess.PIPE, check=False)

    def test_cli_control_key_roundtrip_and_no_overwrite(self):
        request_file = self.root / 'request.json'
        query_file = self.root / 'query.json'
        value = create(key='cli\x00key')
        request_file.write_bytes(wire(value))
        query_file.write_bytes(wire({key: value[key] for key in ('idempotency_scope_ref', 'operation_kind', 'idempotency_key')}))
        result = self.cli('write', '--db', str(self.db), '--request', str(request_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['replayed'])
        result = self.cli('operation', '--db', str(self.db), '--request', str(query_file))
        self.assertEqual(json.loads(result.stdout)['data']['idempotency_record']['idempotency_key'], 'cli\x00key')
        result = self.cli('write', '--db', str(self.db), '--request', str(request_file))
        self.assertTrue(json.loads(result.stdout)['replayed'])
        package, descriptor = self.root / 'package', self.root / 'descriptor'
        package.write_bytes(b'keep')
        result = self.cli('export', '--db', str(self.db), '--package', str(package), '--descriptor', str(descriptor))
        self.assertEqual(result.returncode, 4)
        self.assertEqual(package.read_bytes(), b'keep')
        self.assertFalse(descriptor.exists())

    def test_two_processes_same_request_have_one_durable_result(self):
        path = self.root/'same-request.json'
        path.write_bytes(wire(create()))
        isolation = ['-I'] if sys.flags.isolated else []
        command = [sys.executable, *isolation, '-m', 'infra_artifact_ledger', 'write',
                   '--db', str(self.db), '--request', str(path)]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        codes, responses = [], []
        for process in processes:
            output, error = process.communicate(timeout=30)
            codes.append(process.returncode)
            responses.append(json.loads(output))
            self.assertFalse(error)
        self.assertIn(0, codes)
        self.assertTrue(all(code in (0, 6) for code in codes), (codes, responses))
        self.assertEqual(sum(response.get('replayed') is False for response in responses if response['status']=='COMMITTED'), 1)
        self.assertEqual(self.ledger.verify()['counts']['idempotency_records'], 1)
        self.assertTrue(self.ledger.execute(wire(create()))['replayed'])

    def test_response_loss_preserves_queryable_commit(self):
        path = self.root / 'request.json'
        path.write_bytes(wire(create()))
        read_fd, write_fd = os.pipe()
        os.close(read_fd)
        try:
            result = self.cli('write', '--db', str(self.db), '--request', str(path), stdout=write_fd)
        finally:
            os.close(write_fd)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.ledger.get_operation('demo:scope', 'create_artifact', 'create')['result']['artifact_id'], 'artifact:report')
        self.assertEqual(self.ledger.verify()['counts']['idempotency_records'], 1)


if __name__ == '__main__':
    unittest.main()
