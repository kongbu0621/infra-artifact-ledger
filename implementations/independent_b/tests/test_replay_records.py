"""Replay must revalidate retained records against the original request/package.

Only fresh synthetic copies are altered. Private SQL is used solely for fault
injection into either independent format; outcomes use the public contract.
"""
from contextlib import contextmanager
import copy
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from infra_artifact_ledger import LedgerError, initialize, open as open_ledger
from cross_implementation import assert_replay, portable_metadata
import test_import_conflicts as fixtures
from test_public_contract import append, import_request, sha, wire


class ReplayRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline = self.root / 'baseline.sqlite'
        self.counter = 0
        with initialize(self.baseline) as ledger:
            self.empty = ledger.export_bundle()
            self.calls = fixtures.ImportConflictTests.seed(self, ledger, 'source')
            fixtures.ImportConflictTests.seed(self, ledger, 'alternate')
            bundle = ledger.export_bundle()
            self.imported = (import_request(), fixtures.arguments(bundle))
            ledger.execute(wire(self.imported[0]), **self.imported[1])
            self.expected = portable_metadata(ledger.export_bundle())

    @contextmanager
    def isolated_case(self):
        self.counter += 1
        path = self.root / ('case-%d.sqlite' % self.counter)
        shutil.copyfile(self.baseline, path)
        with open_ledger(path) as ledger:
            yield path, ledger

    def change_record(self, path, identity, value, content=None):
        with sqlite3.connect(path) as db:
            is_b = db.execute("SELECT name FROM sqlite_master WHERE name='b_objects'").fetchone()
            if is_b:
                db.execute('UPDATE b_objects SET document=? WHERE owned_id=?', (wire(value), identity))
                if content is not None:
                    db.execute('UPDATE b_objects SET content=? WHERE owned_id=?', (content, identity))
            else:
                db.execute('UPDATE records SET data=? WHERE id=?', (wire(value).decode(), identity))
                if content is not None:
                    db.execute('UPDATE payloads SET data=? WHERE blob_ref=?', (content, identity))

    def change_operation(self, path, value):
        args = tuple(value[key] for key in ('idempotency_scope_ref', 'operation_kind', 'idempotency_key'))
        with sqlite3.connect(path) as db:
            is_b = db.execute("SELECT name FROM sqlite_master WHERE name='b_operations'").fetchone()
            if is_b:
                db.execute('UPDATE b_operations SET document=? WHERE scope=? AND kind=? AND request_key=?',
                           (wire(value), *args))
            else:
                db.execute('UPDATE operations SET data=?, result_ref=? WHERE scope=? AND kind=? AND key=?',
                           (wire(value).decode(), value['result_ref'], *args))

    def operation(self, ledger, request):
        return ledger.get_operation(*(request[key] for key in (
            'idempotency_scope_ref', 'operation_kind', 'idempotency_key')))['idempotency_record']

    def assert_replay_rejected(self, path, ledger, call, operation=None):
        request, kwargs = call
        if operation is None:
            operation, = [item for item in self.expected['idempotency_records']
                          if all(item[key] == request[key] for key in (
                              'idempotency_scope_ref', 'operation_kind', 'idempotency_key'))]
        before = path.read_bytes()
        def check(handle):
            with self.assertRaises(LedgerError) as caught:
                handle.execute(wire(request), **kwargs)
            error = caught.exception
            self.assertEqual((error.code, error.commit_state), ('INTEGRITY_FAILURE', 'committed'))
            for key in ('result_ref', 'idempotency_scope_ref', 'operation_kind',
                        'idempotency_key', 'recorded_at'):
                self.assertEqual(error.details[key], operation[key])
            # A replay detects damage; it must neither repair it nor add a receipt.
            self.assertEqual(path.read_bytes(), before)
        check(ledger)
        with open_ledger(path) as reopened:
            check(reopened)

    def changed_record(self, ledger, kind):
        identity = 'source:' + kind
        value = copy.deepcopy(ledger.get_record(kind, identity))
        content = None
        if kind == 'artifact':
            value['namespace_ref'] = 'changed:namespace'
        elif kind == 'version':
            value['principal_ref'] = 'changed:principal'
        elif kind == 'content_root':
            value['manifest_ref'] = 'alternate:manifest'
        elif kind == 'manifest':
            value['entries'][0]['entry_key'] = 'changed-key'
            value['digest'] = sha(wire(dict(entries=value['entries'])))
        elif kind == 'blob':
            content = b'changed synthetic bytes'
            value.update(byte_length=len(content), digest=sha(content))
        elif kind == 'provenance_link':
            value['relation_ref']['relation_id'] = 'changed'
        else:
            value['recorded_at'] = fixtures.OTHER_TIME
        return identity, value, content

    def test_create_and_append_replay_check_original_metadata(self):
        for kind in fixtures.KINDS[:-1]:
            with self.subTest(kind=kind), self.isolated_case() as (path, ledger):
                self.change_record(path, *self.changed_record(ledger, kind))
                self.assert_replay_rejected(path, ledger, self.calls[0 if kind == 'artifact' else 1])

    def test_import_replay_checks_all_imported_record_types(self):
        cases = [(kind, False) for kind in fixtures.KINDS]
        cases += [(kind, True) for kind in ('artifact', 'version', 'provenance_link')]
        for kind, change_time in cases:
            with self.subTest(kind=kind, time=change_time), self.isolated_case() as (path, ledger):
                if change_time:
                    identity = 'source:' + kind
                    record = ledger.get_record(kind, identity)
                    record['recorded_at'] = fixtures.OTHER_TIME
                    self.change_record(path, identity, record)
                else:
                    self.change_record(path, *self.changed_record(ledger, kind))
                self.assert_replay_rejected(path, ledger, self.imported)

    def test_import_replay_checks_complete_historical_operations(self):
        for call in self.calls:
            for field in ('request_fingerprint', 'recorded_at', 'result_ref'):
                with self.subTest(kind=call[0]['operation_kind'], field=field), self.isolated_case() as (path, ledger):
                    operation = self.operation(ledger, call[0])
                    if field == 'request_fingerprint':
                        operation[field] = sha(b'changed fingerprint')
                    elif field == 'recorded_at':
                        operation[field] = fixtures.OTHER_TIME
                    else:
                        operation[field] = operation[field].replace('source:', 'alternate:')
                    self.change_operation(path, operation)
                    self.assert_replay_rejected(path, ledger, self.imported)

    def test_replay_checks_result_identity_and_current_receipt(self):
        for call, alternate in [(self.calls[0], 'alternate:artifact'),
                                (self.calls[1], 'alternate:version'),
                                (self.imported, 'alternate:import_receipt')]:
            with self.subTest(kind=call[0]['operation_kind']), self.isolated_case() as (path, ledger):
                operation = self.operation(ledger, call[0])
                operation['result_ref'] = alternate
                self.change_operation(path, operation)
                self.assert_replay_rejected(path, ledger, call, operation)
        with self.subTest(receipt='artifact coverage'), self.isolated_case() as (path, ledger):
            receipt = ledger.get_record('import_receipt', 'receipt:one')
            receipt['imported_artifact_refs'] = []
            self.change_record(path, 'receipt:one', receipt)
            self.assert_replay_rejected(path, ledger, self.imported)

    def test_invalid_stored_metadata_keeps_the_known_original_commit(self):
        with self.subTest(record='artifact'), self.isolated_case() as (path, ledger):
            value = ledger.get_record('artifact', 'source:artifact')
            del value['namespace_ref']
            self.change_record(path, 'source:artifact', value)
            self.assert_replay_rejected(path, ledger, self.calls[0])
        with self.subTest(record='historical operation'), self.isolated_case() as (path, ledger):
            value = self.operation(ledger, self.calls[0][0])
            value['request_fingerprint'] = 'invalid'
            self.change_operation(path, value)
            self.assert_replay_rejected(path, ledger, self.imported)

    def test_valid_later_append_keeps_original_import_replayable(self):
        with self.isolated_case() as (_, ledger):
            request = append(version='version:later', key='later')
            request['body']['version']['artifact_id'] = 'source:artifact'
            request['body']['version']['parent_version_refs'] = ['source:version']
            ledger.execute(wire(request), payloads={'blob:one': b'original\x00\xff'})
            expected = portable_metadata(ledger.export_bundle())
            for request, kwargs in [*self.calls, self.imported]:
                assert_replay(ledger, request, kwargs, expected)


if __name__ == '__main__':
    unittest.main()
