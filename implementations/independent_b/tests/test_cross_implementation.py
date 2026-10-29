"""Reject false positives in the installed cross-implementation acceptance."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from infra_artifact_ledger import initialize
import cross_implementation as cross


class CrossAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with initialize(self.root / 'source.sqlite') as source:
            source.execute(cross.wire(cross.create()))
            other = cross.create()
            other['body']['artifact']['artifact_id'] = 'artifact:empty'
            other['idempotency_key'] = 'empty'
            source.execute(cross.wire(other))
            self.payload = [('file', 'blob:original', b'original')]
            source.execute(cross.wire(cross.append('original', [], self.payload)),
                           payloads={'blob:original': b'original'})
            self.bundle = source.export_bundle()
        self.incoming = cross.portable_metadata(self.bundle)
        self.value = cross.request('import_bundle', dict(import_receipt_id='receipt:target'), 'import')
        self.kwargs = dict(package=self.bundle['package_utf8'], descriptor=self.bundle['descriptor_utf8'])
        self.target = initialize(self.root / 'target.sqlite')
        self.addCleanup(self.target.close)
        self.response = self.target.execute(cross.wire(self.value), **self.kwargs)
        self.expected = cross.assert_import_preserved(self.target, self.incoming, self.value, self.response)

    def test_import_checks_exact_new_receipt_and_operation(self):
        actual = cross.portable_metadata(self.target.export_bundle())
        changes = [
            ('import_receipts', 'import_receipt_id', 'receipt:wrong'),
            ('import_receipts', 'recorded_at', '2000-01-01T00:00:00.000000Z'),
            ('import_receipts', 'imported_artifact_refs', []),
            ('idempotency_records', 'idempotency_scope_ref', 'wrong:scope'),
            ('idempotency_records', 'operation_kind', 'create_artifact'),
            ('idempotency_records', 'idempotency_key', 'wrong-key'),
            ('idempotency_records', 'request_fingerprint', cross.sha(b'wrong')),
            ('idempotency_records', 'result_ref', 'artifact:cross'),
            ('idempotency_records', 'recorded_at', '2000-01-01T00:00:00.000000Z'),
        ]
        for collection, field, value in changes:
            with self.subTest(collection=collection, field=field):
                corrupted = copy.deepcopy(actual)
                current, = [item for item in corrupted[collection] if
                    item.get('import_receipt_id', item.get('result_ref')) == 'receipt:target']
                current[field] = value
                with patch.object(cross, 'portable_metadata', return_value=corrupted):
                    with self.assertRaises(AssertionError):
                        cross.assert_import_preserved(self.target, self.incoming, self.value, self.response)

    def test_replay_requires_complete_response_and_real_boolean(self):
        cross.assert_replay(self.target, self.value, self.kwargs, self.expected)
        response = self.target.execute(cross.wire(self.value), **self.kwargs)
        for key, value in [('status', 'ERROR'), ('commit_state', 'unknown'),
                           ('operation_kind', 'create_artifact'), ('result_ref', 'receipt:wrong'),
                           ('recorded_at', '2000-01-01T00:00:00.000000Z'),
                           ('replayed', 1), ('replayed', False), ('unexpected', True)]:
            with self.subTest(key=key, value=value):
                with patch.object(self.target, 'execute', return_value={**response, key: value}):
                    with self.assertRaises(AssertionError):
                        cross.assert_replay(self.target, self.value, self.kwargs, self.expected)

    def test_append_compares_requested_delta_and_preserves_old_state(self):
        for field in ('valid', 'principal', 'parents', 'provenance'):
            with self.subTest(field=field), initialize(self.root / (field + '.sqlite')) as target:
                target.execute(cross.wire(self.value), **self.kwargs)
                before = cross.portable_metadata(target.export_bundle())
                value = cross.append('new', ['version:original'], [('new', 'blob:new', b'new')])
                altered = copy.deepcopy(value)
                if field == 'principal':
                    altered['body']['version']['principal_ref'] = 'principal:unrequested'
                elif field == 'parents':
                    altered['body']['version']['parent_version_refs'] = []
                elif field == 'provenance':
                    altered['body']['provenance_links'][0]['relation_ref']['relation_id'] = 'unrequested'
                response = target.execute(cross.wire(altered), payloads={'blob:new': b'new'})
                if field == 'valid':
                    expected = cross.assert_append_committed(target, before, value, response)
                    cross.assert_replay(target, value, dict(payloads={'blob:new': b'new'}), expected)
                else:
                    with self.assertRaises(AssertionError):
                        cross.assert_append_committed(target, before, value, response)

    def test_replay_accepts_equivalent_export_wrapping(self):
        original = self.target.export_bundle()
        package = json.dumps(json.loads(original['package_utf8']), indent=2,
                             ensure_ascii=False).encode()
        wrapped = dict(package_utf8=package, descriptor_utf8=cross.wire(dict(
            transport_version='0.1.0', package_byte_length=len(package),
            package_digest=cross.sha(package))))
        self.assertNotEqual(wrapped, original)
        # A real public import accepts this alternate wire representation.
        with initialize(self.root / 'wrapped.sqlite') as reader:
            value = cross.request('import_bundle', dict(import_receipt_id='receipt:wrapped'), 'wrapped')
            response = reader.execute(cross.wire(value), package=wrapped['package_utf8'],
                                      descriptor=wrapped['descriptor_utf8'])
            cross.assert_import_preserved(reader, cross.portable_metadata(original), value, response)
        calls = []
        execute = self.target.execute
        def replay(raw, **kwargs):
            calls.append((raw, kwargs))
            return execute(raw, **kwargs)
        with patch.object(self.target, 'execute', side_effect=replay), patch.object(
                self.target, 'export_bundle', side_effect=[original, wrapped, original, wrapped]):
            cross.assert_replay(self.target, self.value, self.kwargs, self.expected)
            cross.assert_replay(self.target, self.value, self.kwargs, self.expected)
        self.assertEqual(calls, [(cross.wire(self.value), self.kwargs)] * 2)

    def test_metadata_preserves_json_number_types(self):
        actual = cross.portable_metadata(self.target.export_bundle())
        corrupted = copy.deepcopy(actual)
        corrupted['blobs'][0]['byte_length'] = float(corrupted['blobs'][0]['byte_length'])
        with self.subTest(location='export'), patch.object(cross, 'portable_metadata', return_value=corrupted):
            with self.assertRaises(AssertionError):
                cross.assert_metadata(self.target, self.expected)
        read = self.target.get_record
        def wrong_record(kind, identity):
            record = read(kind, identity)
            if kind == 'blob':
                record = {**record, 'byte_length': float(record['byte_length'])}
            return record
        with self.subTest(location='record'), patch.object(self.target, 'get_record', side_effect=wrong_record):
            with self.assertRaises(AssertionError):
                cross.assert_metadata(self.target, self.expected)
        report = self.target.verify()
        for group, field, bad in [('counts', 'versions', True), ('counts', 'artifacts', 2.0),
                                 (None, 'verified_blob_count', True),
                                 (None, 'verified_byte_length', float(report['verified_byte_length']))]:
            with self.subTest(group=group, field=field):
                changed = copy.deepcopy(report)
                (changed if group is None else changed[group])[field] = bad
                with patch.object(self.target, 'verify', return_value=changed):
                    with self.assertRaises(AssertionError):
                        cross.assert_metadata(self.target, self.expected)

    def test_replay_rejects_changed_records_or_bytes(self):
        raw = self.payload[0][2]
        for changed in (b'X' + raw[1:], raw[:-1], raw + b'X', bytearray(raw)):
            with self.subTest(payload=changed), patch.object(self.target, 'read_blob', return_value=changed):
                with self.assertRaises(AssertionError):
                    cross.assert_replay(self.target, self.value, self.kwargs, self.expected)
        execute = self.target.execute
        def extra_record(raw, **kwargs):
            response = execute(raw, **kwargs)
            extra = cross.create()
            extra['body']['artifact']['artifact_id'] = 'artifact:unrequested'
            extra['idempotency_key'] = 'unrequested'
            execute(cross.wire(extra))
            return response
        with patch.object(self.target, 'execute', side_effect=extra_record):
            with self.assertRaises(AssertionError):
                cross.assert_replay(self.target, self.value, self.kwargs, self.expected)

    def test_import_fingerprint_keeps_original_record_array_order(self):
        incoming = copy.deepcopy(self.incoming)
        incoming['artifacts'].reverse()
        self.assertNotEqual(incoming['artifacts'], self.incoming['artifacts'])
        package = json.loads(self.bundle['package_utf8'])
        raw = cross.wire(incoming)
        package['metadata'] = dict(encoding='base64', data=base64.b64encode(raw).decode(),
                                   digest=cross.sha(raw), byte_length=len(raw))
        encoded = cross.wire(package)
        descriptor = cross.wire(dict(transport_version='0.1.0', package_byte_length=len(encoded),
                                     package_digest=cross.sha(encoded)))
        with initialize(self.root / 'reordered.sqlite') as target:
            response = target.execute(cross.wire(self.value), package=encoded, descriptor=descriptor)
            cross.assert_import_preserved(target, incoming, self.value, response)
            with self.assertRaises(AssertionError):
                cross.assert_import_preserved(target, self.incoming, self.value, response)


if __name__ == '__main__':
    unittest.main()
