"""Import conflict/atomicity checks against the published A1 contract.

Only connection proxies use private storage, solely to inject failures. Expected
records, payloads, receipts and operation identities come from public APIs.
"""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from test_failures import ConnectionFault
from test_public_contract import append, create, import_request, repackage, sha, wire


KINDS = ('artifact', 'version', 'content_root', 'blob', 'manifest',
         'provenance_link', 'import_receipt')
COLLECTIONS = (
    ('artifact', 'artifacts', 'artifact_id'),
    ('version', 'versions', 'version_id'),
    ('content_root', 'content_roots', 'content_root_ref'),
    ('blob', 'blobs', 'blob_ref'),
    ('manifest', 'manifests', 'manifest_ref'),
    ('provenance_link', 'provenance_links', 'provenance_id'),
    ('import_receipt', 'import_receipts', 'import_receipt_id'),
)
OTHER_TIME = '2000-01-01T00:00:00.000000Z'


def arguments(bundle):
    return dict(package=bundle['package_utf8'], descriptor=bundle['descriptor_utf8'])


def metadata(bundle):
    return json.loads(base64.b64decode(json.loads(bundle['package_utf8'])['metadata']['data']))


class InsertFault:
    """Fail after the selected real INSERT, while another connection observes."""
    def __init__(self, real, fail_at, observe):
        self.real, self.fail_at, self.observe = real, fail_at, observe
        self.inserts = 0

    @property
    def in_transaction(self):
        return self.real.in_transaction

    def execute(self, sql, *args):
        result = self.real.execute(sql, *args)
        if sql.startswith('INSERT INTO '):
            self.inserts += 1
            if self.inserts == self.fail_at:
                self.observe()
                raise OSError('injected import INSERT failure')
        return result

    def close(self):
        self.real.close()


class ImportConflictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with initialize(self.root / 'empty.sqlite') as empty:
            self.empty = empty.export_bundle()
        self.source = initialize(self.root / 'source.sqlite')
        self.addCleanup(self.source.close)
        self.originals = self.seed(self.source, 'source')
        self.seed(self.source, 'alternate')
        self.bundle = self.source.export_bundle()
        self.path = self.root / 'target.sqlite'
        self.target = initialize(self.path)
        self.addCleanup(self.target.close)
        self.seed(self.target, 'retained')

    def seed(self, ledger, prefix):
        """Two instances provide valid alternative result refs for all 3 kinds."""
        ids = {kind: prefix + ':' + kind for kind in KINDS}
        raw = b'synthetic\x00\xff:' + prefix.encode()
        first = create(ids['artifact'], key=prefix + ':create')
        second = append(ids['version'], ids['blob'], ids['content_root'],
                        raw, key=prefix + ':append')
        body = second['body']
        body['version']['artifact_id'] = ids['artifact']
        body['content_roots'] = [dict(content_root_ref=ids['content_root'],
            kind='manifest', manifest_ref=ids['manifest'])]
        entries = [dict(entry_key='report', blob_ref=ids['blob'])]
        body['manifests'] = [dict(manifest_ref=ids['manifest'], entries=entries,
                                digest=sha(wire(dict(entries=entries))))]
        body['provenance_links'] = [dict(provenance_id=ids['provenance_link'],
            subject_version_ref=ids['version'], relation_ref=dict(namespace='demo',
            relation_id='derived', relation_version='1'),
            object=dict(kind='external_source', ref='external:original'))]
        third = import_request(ids['import_receipt'], prefix + ':empty')
        calls = [(first, {}), (second, dict(payloads={ids['blob']: raw})),
                 (third, arguments(self.empty))]
        for request, kwargs in calls:
            ledger.execute(wire(request), **kwargs)
        return calls

    def snapshot(self, ledger):
        return ledger.export_bundle(), ledger.verify()

    def assert_bundle_preserved(self, ledger, bundle):
        """Compare every public record and actual byte, not just row counts."""
        expected = metadata(bundle)
        records = {}
        for kind, collection, identity in COLLECTIONS:
            for record in expected[collection]:
                records[record[identity]] = record
                self.assertEqual(ledger.get_record(kind, record[identity]), record)
        for artifact in expected['artifacts']:
            versions = sorted((item for item in expected['versions']
                               if item['artifact_id'] == artifact['artifact_id']),
                              key=lambda item: item['version_id'])
            version_ids = {item['version_id'] for item in versions}
            provenance = sorted((item for item in expected['provenance_links']
                                 if item['subject_version_ref'] in version_ids),
                                key=lambda item: item['provenance_id'])
            self.assertEqual(ledger.get_history(artifact['artifact_id']),
                             dict(artifact=artifact, versions=versions, provenance_links=provenance))
        for operation in expected['idempotency_records']:
            self.assertEqual(self.operation(ledger, operation),
                             dict(idempotency_record=operation, result=records[operation['result_ref']]))
        for payload in json.loads(bundle['package_utf8'])['payloads']:
            self.assertEqual(ledger.read_blob(payload['blob_ref']),
                             base64.b64decode(payload['data'], validate=True))

    def operation(self, ledger, request):
        return ledger.get_operation(request['idempotency_scope_ref'],
            request['operation_kind'], request['idempotency_key'])

    def assert_import_committed(self, ledger, retained, request, bundle, response=None):
        """Check the exact import delta against the request and pre-import data."""
        incoming, expected = metadata(bundle), metadata(retained)
        observed = self.operation(ledger, request)
        when = observed['idempotency_record']['recorded_at']
        receipt = dict(import_receipt_id=request['body']['import_receipt_id'],
                       recorded_at=when, imported_artifact_refs=sorted(
                           item['artifact_id'] for item in incoming['artifacts']))
        current = {key: request[key] for key in
                   ('idempotency_scope_ref', 'operation_kind', 'idempotency_key')}
        current.update(result_ref=receipt['import_receipt_id'], recorded_at=when,
            request_fingerprint=sha(wire(dict(contract_version=request['contract_version'],
                idempotency_scope_ref=request['idempotency_scope_ref'],
                operation_kind=request['operation_kind'],
                body=dict(import_receipt_id=receipt['import_receipt_id'], bundle=incoming)))))
        self.assertEqual(observed, dict(idempotency_record=current, result=receipt))
        self.assertEqual(ledger.get_record('import_receipt', receipt['import_receipt_id']), receipt)
        if response is not None:
            self.assertEqual(response, dict(status='COMMITTED', commit_state='committed',
                operation_kind='import_bundle', result_ref=receipt['import_receipt_id'],
                recorded_at=when, replayed=False))
            self.assertIs(response['replayed'], False)
        for _, collection, identity in COLLECTIONS:
            records = {item[identity]: item for item in expected[collection]}
            for item in incoming[collection]:
                if item[identity] in records:
                    self.assertEqual(records[item[identity]], item)
                records[item[identity]] = item
            if collection == 'import_receipts':
                self.assertNotIn(receipt['import_receipt_id'], records)
                records[receipt['import_receipt_id']] = receipt
            expected[collection] = [records[key] for key in sorted(records)]
        def triple(op):
            return op['idempotency_scope_ref'], op['operation_kind'], op['idempotency_key']
        operations = {triple(item): item for item in expected['idempotency_records']}
        for item in incoming['idempotency_records']:
            if triple(item) in operations:
                self.assertEqual(operations[triple(item)], item)
            operations[triple(item)] = item
        self.assertNotIn(triple(current), operations)
        operations[triple(current)] = current
        expected['idempotency_records'] = [operations[key] for key in sorted(operations)]
        self.assertEqual(metadata(ledger.export_bundle()), expected)
        self.assertEqual(ledger.verify(), dict(
            counts={key: len(value) for key, value in expected.items() if isinstance(value, list)},
            verified_blob_count=len(expected['blobs']),
            verified_byte_length=sum(item['byte_length'] for item in expected['blobs'])))
        self.assert_bundle_preserved(ledger, retained)
        self.assert_bundle_preserved(ledger, bundle)

    def assert_rejected(self, code, request, kwargs):
        before = self.snapshot(self.target)
        physical_before = self.path.read_bytes()
        with self.assertRaises(LedgerError) as caught:
            self.target.execute(wire(request), **kwargs)
        self.assertEqual((caught.exception.code, caught.exception.commit_state),
                         (code, 'not_committed'))
        self.assertEqual(self.snapshot(self.target), before)
        self.assertEqual(self.path.read_bytes(), physical_before)
        with open_ledger(self.path) as reopened:
            self.assertEqual(self.snapshot(reopened), before)
            self.assertEqual(reopened.read_blob('retained:blob'), b'synthetic\x00\xff:retained')

    def import_source(self):
        return self.target.execute(wire(import_request()), **arguments(self.bundle))

    def test_each_historical_operation_requires_entire_record_equality(self):
        self.import_source()
        result_kinds = dict(create_artifact='artifact', append_version='version',
                            import_bundle='import_receipt')
        for request, kwargs in self.originals:
            kind, key = request['operation_kind'], request['idempotency_key']
            original = self.operation(self.target, request)
            mutations = dict(request_fingerprint=sha(b'different request'),
                recorded_at=OTHER_TIME, result_ref='alternate:' + result_kinds[kind])
            for field, value in mutations.items():
                with self.subTest(kind=kind, field=field):
                    self.assertNotEqual(original['idempotency_record'][field], value)
                    def change(meta):
                        op = next(op for op in meta['idempotency_records']
                                  if op['idempotency_key'] == key)
                        op[field] = value
                    changed = repackage(self.bundle, change)
                    self.assert_rejected('IDEMPOTENCY_CONFLICT',
                                         import_request('receipt:new', 'new'), changed)
                    self.assertEqual(self.operation(self.target, request), original)
                    replay = self.target.execute(wire(request), **kwargs)
                    self.assertTrue(replay['replayed'])
                    self.assertEqual(replay['result_ref'], original['idempotency_record']['result_ref'])
                    self.assertEqual(replay['recorded_at'], original['idempotency_record']['recorded_at'])

    def test_duplicate_historical_triples_and_current_triple_collision(self):
        for request, _ in self.originals:
            for changed in (False, True):
                with self.subTest(kind=request['operation_kind'], changed=changed):
                    def duplicate(meta):
                        op = copy.deepcopy(next(op for op in meta['idempotency_records']
                            if op['idempotency_key'] == request['idempotency_key']))
                        if changed:
                            op['recorded_at'] = OTHER_TIME
                        meta['idempotency_records'].append(op)
                    self.assert_rejected('IDEMPOTENCY_CONFLICT', import_request(),
                                         repackage(self.bundle, duplicate))
        # Same current triple as a historical import must not claim its result.
        self.assert_rejected('IDEMPOTENCY_CONFLICT',
            import_request('receipt:new', 'source:empty'), arguments(self.bundle))
        self.import_source()  # Rejections did not reserve the original key/receipt.

    def test_new_receipt_cannot_collide_with_any_owned_type(self):
        for prefix in ('source', 'retained'):
            for kind in KINDS:
                with self.subTest(location=prefix, kind=kind):
                    self.assert_rejected('IDENTITY_CONFLICT',
                        import_request(prefix + ':' + kind), arguments(self.bundle))
        self.import_source()
        self.assert_rejected('IDENTITY_CONFLICT', import_request(key='another-key'),
                             arguments(self.bundle))
        before = self.snapshot(self.target)
        self.assertTrue(self.import_source()['replayed'])
        self.assertEqual(self.snapshot(self.target), before)

    def test_same_key_with_different_kind_or_scope_is_not_a_collision(self):
        for field, value in (('idempotency_key', 'source:create'),
                             ('idempotency_scope_ref', 'another:scope')):
            with self.subTest(field=field):
                request = import_request('receipt:' + field, 'source:empty')
                request[field] = value
                retained = self.target.export_bundle()
                result = self.target.execute(wire(request), **arguments(self.bundle))
                self.assert_import_committed(self.target, retained, request, self.bundle, result)
                before = self.snapshot(self.target)
                self.assertEqual(self.target.execute(wire(request), **arguments(self.bundle)),
                                 {**result, 'replayed': True})
                self.assertEqual(self.snapshot(self.target), before)

    def test_historical_receipt_record_conflicts_preserve_original(self):
        self.import_source()
        for field, value in (('recorded_at', OTHER_TIME),
                             ('imported_artifact_refs', ['source:artifact'])):
            with self.subTest(field=field):
                def change(meta):
                    receipt = next(item for item in meta['import_receipts']
                                   if item['import_receipt_id'] == 'source:import_receipt')
                    receipt[field] = value
                self.assert_rejected('IDENTITY_CONFLICT',
                    import_request('receipt:new', 'new'), repackage(self.bundle, change))

    def test_identical_history_converges_and_replays_without_duplicate_rows(self):
        first = self.import_source()
        retained = self.target.export_bundle()
        def reverse_collections(meta):
            for value in meta.values():
                if isinstance(value, list):
                    value.reverse()
        reordered = repackage(self.bundle, reverse_collections)
        # History sets converge, but the original request fingerprint preserves order.
        self.assert_rejected('IDEMPOTENCY_CONFLICT', import_request(), reordered)
        request = import_request('receipt:next', 'next')
        result = self.target.execute(wire(request), **reordered)
        reordered_bundle = dict(package_utf8=reordered['package'], descriptor_utf8=reordered['descriptor'])
        self.assert_import_committed(self.target, retained, request, reordered_bundle, result)
        before_replay = self.snapshot(self.target)
        self.assertEqual(self.import_source(), {**first, 'replayed': True})
        self.assertEqual(self.target.execute(wire(request), **reordered), {**result, 'replayed': True})
        # Transport whitespace is outside the semantic request fingerprint.
        package = json.dumps(json.loads(reordered['package']), indent=2).encode()
        descriptor = json.loads(reordered['descriptor'])
        descriptor.update(package_byte_length=len(package), package_digest=sha(package))
        self.assertEqual(self.target.execute(wire(request), package=package,
                         descriptor=wire(descriptor)), {**result, 'replayed': True})
        self.assertEqual(self.snapshot(self.target), before_replay)
        self.assert_rejected('IDEMPOTENCY_CONFLICT', request, arguments(self.bundle))
        self.assertEqual(self.source.export_bundle(), self.bundle)

    def test_late_invalid_record_reference_or_payload_preserves_target(self):
        cases = [
            ('INVALID_INPUT', repackage(self.bundle,
                lambda m: m['idempotency_records'][-1].update(unexpected=True))),
            ('INTEGRITY_FAILURE', repackage(self.bundle,
                lambda m: m['idempotency_records'][-1].update(result_ref='missing:result'))),
            ('INTEGRITY_FAILURE', repackage(self.bundle,
                lambda m: m['import_receipts'][-1].update(imported_artifact_refs=['missing:artifact']))),
            ('INTEGRITY_FAILURE', repackage(self.bundle, change_package=
                lambda p: p['payloads'][-1].update(data=base64.b64encode(b'bad').decode()))),
        ]
        for index, (code, kwargs) in enumerate(cases):
            with self.subTest(code=code, index=index):
                self.assert_rejected(code, import_request(), kwargs)
        self.import_source()

    def test_failure_after_each_insert_rolls_back_all_import_rows(self):
        meta = metadata(self.bundle)
        # All incoming records plus the new receipt and current operation.
        count = sum(len(value) for value in meta.values() if isinstance(value, list)) + 2
        self.assertEqual(count, 22)  # 14 owned + 6 historical + 2 current.
        for fail_at in range(1, count + 1):
            with self.subTest(insert=fail_at):
                path = self.root / ('fault-%d.sqlite' % fail_at)
                with initialize(path) as target:
                    self.seed(target, 'retained')
                    before = self.snapshot(target)
                    with open_ledger(path) as observer:
                        observed = []
                        def observe():
                            state = self.snapshot(observer)
                            observed.append(state)
                        fault = InsertFault(target._db, fail_at, observe)
                        target._db = fault
                        with self.assertRaises(LedgerError) as caught:
                            target.execute(wire(import_request()), **arguments(self.bundle))
                        self.assertEqual((caught.exception.code, caught.exception.commit_state),
                                         ('IO_ERROR', 'not_committed'))
                        self.assertEqual(fault.inserts, fail_at)
                        self.assertEqual(observed, [before])
                        self.assertEqual(self.snapshot(target), before)
                        self.assertEqual(self.snapshot(observer), before)
                    with open_ledger(path) as reopened:
                        self.assertEqual(self.snapshot(reopened), before)
                        result = reopened.execute(wire(import_request()), **arguments(self.bundle))
                        self.assert_import_committed(reopened, before[0], import_request(), self.bundle, result)
                        committed = self.snapshot(reopened)
                        self.assertEqual(reopened.execute(wire(import_request()), **arguments(self.bundle)),
                                         {**result, 'replayed': True})
                        self.assertEqual(self.snapshot(reopened), committed)

    def test_import_unknown_outcomes_require_readback_before_retry(self):
        before = self.snapshot(self.target)
        self.target._db = ConnectionFault(self.target._db, 'INSERT INTO b_operations',
                                          after=True, rollback_fails=True)
        with self.assertRaises(LedgerError) as caught:
            self.import_source()
        self.assertEqual((caught.exception.code, caught.exception.commit_state),
                         ('DURABILITY_UNKNOWN', 'unknown'))
        self.target.close()
        with open_ledger(self.path) as target:
            self.assertEqual(self.snapshot(target), before)
            # COMMIT really succeeds; only acknowledgement is lost.
            target._db = ConnectionFault(target._db, 'COMMIT', after=True)
            with self.assertRaises(LedgerError) as caught:
                target.execute(wire(import_request()), **arguments(self.bundle))
            self.assertEqual((caught.exception.code, caught.exception.commit_state),
                             ('DURABILITY_UNKNOWN', 'unknown'))
        with open_ledger(self.path) as target:
            original = self.operation(target, import_request())['idempotency_record']
            self.assert_import_committed(target, before[0], import_request(), self.bundle)
            committed = self.snapshot(target)
            replay = target.execute(wire(import_request()), **arguments(self.bundle))
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['result_ref'], original['result_ref'])
            self.assertEqual(replay['recorded_at'], original['recorded_at'])
            self.assertEqual(self.snapshot(target), committed)


if __name__ == '__main__':
    unittest.main()
