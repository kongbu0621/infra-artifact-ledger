"""Installed A -> B -> fresh A, using only public API/CLI and synthetic data.

Run with --a-python /isolated/a/bin/python --b-python /isolated/b/bin/python
--workdir NEW_DIRECTORY. No existing business installation or database is used.
"""
import argparse
import base64
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys


def wire(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def sha(raw):
    return dict(algorithm='sha256', value=hashlib.sha256(raw).hexdigest())


def request(kind, body, key):
    return dict(profile='bounded-local-v0.1', contract_version='0.1.0',
                operation_kind=kind, idempotency_scope_ref='cross:scope',
                idempotency_key=key, body=body)


def create():
    return request('create_artifact', dict(artifact=dict(artifact_id='artifact:cross',
        namespace_ref='namespace:synthetic', type_ref=dict(namespace='test', type_id='report', type_version='1'))), 'create\x00original')


def append(version, parent, contents):
    root, manifest = 'root:' + version, 'manifest:' + version
    entries = [dict(entry_key=name, blob_ref=identity) for name, identity, _ in contents]
    return request('append_version', dict(version=dict(version_id='version:' + version,
        artifact_id='artifact:cross', content_root_ref=root, parent_version_refs=parent,
        external_source_refs=['external:source'], capture_refs=['capture:original'],
        handling_policy_refs=['policy:retain']),
        content_roots=[dict(content_root_ref=root, kind='manifest', manifest_ref=manifest)],
        manifests=[dict(manifest_ref=manifest, entries=entries, digest=sha(wire(dict(entries=entries))))],
        blobs=[dict(blob_ref=identity, byte_length=len(raw), digest=sha(raw), payload_availability='available')
               for _, identity, raw in contents],
        provenance_links=[dict(provenance_id='provenance:' + version,
            subject_version_ref='version:' + version,
            relation_ref=dict(namespace='test', relation_id='derived', relation_version='1'),
            object=dict(kind='external_source', ref='source:opaque'))]), 'append:' + version)


def call_cli(*args):
    result = subprocess.run([sys.executable, '-I', '-m', 'infra_artifact_ledger', *map(str, args)],
                            capture_output=True, check=False)
    if result.returncode:
        raise AssertionError((result.returncode, result.stdout.decode(), result.stderr.decode()))
    return json.loads(result.stdout)


def portable_metadata(bundle):
    return json.loads(base64.b64decode(json.loads(bundle['package_utf8'])['metadata']['data']))


COLLECTIONS = (
    ('artifact', 'artifacts', 'artifact_id'),
    ('version', 'versions', 'version_id'),
    ('content_root', 'content_roots', 'content_root_ref'),
    ('blob', 'blobs', 'blob_ref'),
    ('manifest', 'manifests', 'manifest_ref'),
    ('provenance_link', 'provenance_links', 'provenance_id'),
    ('import_receipt', 'import_receipts', 'import_receipt_id'),
)


def operation_for(value, result_ref, recorded_at, bundle=None):
    # Compute the expected fingerprint from the request/input, never the output.
    body = value['body'] if bundle is None else dict(
        import_receipt_id=value['body']['import_receipt_id'], bundle=bundle)
    operation = {key: value[key] for key in
                 ('idempotency_scope_ref', 'operation_kind', 'idempotency_key')}
    operation.update(result_ref=result_ref, recorded_at=recorded_at,
        request_fingerprint=sha(wire(dict(contract_version=value['contract_version'],
            idempotency_scope_ref=value['idempotency_scope_ref'],
            operation_kind=value['operation_kind'], body=body))))
    return operation


def assert_response(response, operation, replayed):
    assert response == dict(status='COMMITTED', commit_state='committed',
        operation_kind=operation['operation_kind'], result_ref=operation['result_ref'],
        recorded_at=operation['recorded_at'], replayed=replayed)
    assert response['replayed'] is replayed


def assert_metadata(ledger, expected):
    def normalized(metadata):
        metadata = copy.deepcopy(metadata)
        metadata.setdefault('import_receipts', [])
        # Top-level records are sets; nested parent/manifest order is meaningful.
        for name in [item[1] for item in COLLECTIONS] + ['idempotency_records']:
            metadata[name] = sorted(metadata[name], key=wire)
        return metadata
    expected = normalized(expected)
    # JSON distinguishes integer counts/lengths from floats and booleans;
    # Python container equality alone would accept True == 1 == 1.0.
    assert wire(normalized(portable_metadata(ledger.export_bundle()))) == wire(expected)
    records = {}
    for kind, collection, identity in COLLECTIONS:
        for record in expected[collection]:
            records[record[identity]] = record
            assert wire(ledger.get_record(kind, record[identity])) == wire(record)
    for operation in expected['idempotency_records']:
        assert wire(ledger.get_operation(operation['idempotency_scope_ref'],
            operation['operation_kind'], operation['idempotency_key'])) == wire(dict(
                idempotency_record=operation, result=records[operation['result_ref']]))
    assert wire(ledger.verify()) == wire(dict(
        counts={key: len(value) for key, value in expected.items() if isinstance(value, list)},
        verified_blob_count=len(expected['blobs']),
        verified_byte_length=sum(item['byte_length'] for item in expected['blobs'])))


def assert_import_preserved(ledger, incoming, value, response):
    receipt = dict(import_receipt_id=value['body']['import_receipt_id'],
        recorded_at=response['recorded_at'],
        imported_artifact_refs=sorted(item['artifact_id'] for item in incoming['artifacts']))
    operation = operation_for(value, receipt['import_receipt_id'], receipt['recorded_at'], incoming)
    assert_response(response, operation, False)
    expected = copy.deepcopy(incoming)
    expected.setdefault('import_receipts', []).append(receipt)
    expected['idempotency_records'].append(operation)
    assert_metadata(ledger, expected)
    return expected


def assert_replay(ledger, value, kwargs, expected):
    operation, = [item for item in expected['idempotency_records'] if all(
        item[key] == value[key] for key in ('idempotency_scope_ref', 'operation_kind', 'idempotency_key'))]
    response = ledger.execute(wire(value), **kwargs)
    assert_response(response, operation, True)
    # Preserve the original request/package, but compare committed state rather
    # than requiring byte-identical export JSON wrapping across separate reads.
    assert_metadata(ledger, expected)
    for record in expected['blobs']:
        raw = ledger.read_blob(record['blob_ref'])
        assert type(raw) is bytes
        assert len(raw) == record['byte_length'] and sha(raw) == record['digest']


def assert_append_committed(ledger, before, value, response):
    # This scenario supplies new records and references an already imported Blob.
    expected = copy.deepcopy(before)
    when = response['recorded_at']
    version = {**value['body']['version'], 'recorded_at': when}
    expected['versions'].append(version)
    for kind, collection, identity in COLLECTIONS[2:-1]:
        for record in value['body'][collection]:
            assert all(old[identity] != record[identity] for old in before[collection])
            expected[collection].append({**record, 'recorded_at': when}
                                       if kind == 'provenance_link' else copy.deepcopy(record))
    operation = operation_for(value, version['version_id'], when)
    expected['idempotency_records'].append(operation)
    assert_response(response, operation, False)
    assert_metadata(ledger, expected)
    return expected


def worker(mode, root):
    import infra_artifact_ledger as public
    # -I plus separate environments excludes repository paths and PYTHONPATH.
    module_path = Path(public.__file__).resolve()
    assert Path(sys.prefix).resolve() in module_path.parents
    assert 'site-packages' in module_path.parts
    distributions = {dist.metadata['Name'] for dist in importlib.metadata.distributions()}
    expected = 'infra-artifact-ledger-b' if mode in ('consume-b', 'reopen-b') else 'infra-artifact-ledger'
    forbidden = 'infra-artifact-ledger' if expected.endswith('-b') else 'infra-artifact-ledger-b'
    assert expected in distributions and forbidden not in distributions
    a_db, b_db, final_db = (root / name for name in ('a.sqlite', 'b.sqlite', 'a-fresh.sqlite'))
    original = [('报告/é.txt', 'blob:original', b'Original\x00\xff\n'),
                ('../logical-only', 'blob:empty', b''),
                ('e\u0301', 'blob:same-bytes-other-id', b'Original\x00\xff\n')]
    if mode == 'seed-a':
        # Include branching/merge history, reused bytes, an empty Artifact and
        # an actual successful import in A before exporting to B.
        with public.initialize(root / 'a-source.sqlite') as ledger:
            ledger.execute(wire(create()))
            empty = create()
            empty['body']['artifact']['artifact_id'] = 'artifact:empty'
            empty['idempotency_key'] = 'create:empty'
            ledger.execute(wire(empty))
            ledger.execute(wire(append('original', [], original)),
                           payloads={identity: raw for _, identity, raw in original})
            for version, parents in [('left', ['version:original']),
                                     ('right', ['version:original']),
                                     ('merge', ['version:right', 'version:left'])]:
                ledger.execute(wire(append(version, parents, original)), payloads={})
            source = ledger.export_bundle()
            (root / 'source.package').write_bytes(source['package_utf8'])
            (root / 'source.descriptor').write_bytes(source['descriptor_utf8'])
        original_import = request('import_bundle', dict(import_receipt_id='receipt:a-original'), 'import-source')
        (root / 'import-a-original.json').write_bytes(wire(original_import))
        with public.initialize(a_db) as ledger:
            result = ledger.execute(wire(original_import), package=source['package_utf8'],
                                    descriptor=source['descriptor_utf8'])
            assert_import_preserved(ledger, portable_metadata(source), original_import, result)
            (root / 'import-a-result.json').write_bytes(wire(result))
            bundle = ledger.export_bundle()
            (root / 'a-metadata.json').write_bytes(wire(portable_metadata(bundle)))
            (root / 'original-history.json').write_bytes(wire(ledger.get_history('artifact:cross')))
            assert ledger.verify()['verified_blob_count'] == 3
        # Exercise installed CLI export, then consume those exact files in B.
        call_cli('export', '--db', a_db, '--package', root / 'a.package', '--descriptor', root / 'a.descriptor')
    elif mode == 'consume-b':
        before = sha(a_db.read_bytes())
        try:
            public.open(a_db)
        except public.LedgerError:
            pass
        else:
            raise AssertionError('B opened A physical format')
        assert sha(a_db.read_bytes()) == before
        imported = request('import_bundle', dict(import_receipt_id='receipt:b-import'), 'import-a')
        (root / 'import-b.json').write_bytes(wire(imported))
        call_cli('init', '--db', b_db)
        result = call_cli('write', '--db', b_db, '--request', root / 'import-b.json',
                         '--package', root / 'a.package', '--descriptor', root / 'a.descriptor')
        with public.open(b_db) as ledger:
            expected_meta = assert_import_preserved(ledger,
                json.loads((root / 'a-metadata.json').read_bytes()), imported, result)
            assert ledger.get_history('artifact:cross') == json.loads((root / 'original-history.json').read_bytes())
            assert ledger.get_history('artifact:empty')['versions'] == []
            for _, identity, raw in original:
                assert ledger.read_blob(identity) == raw
            for old in (create(), append('original', [], original)):
                assert_replay(ledger, old, {'payloads': {identity: raw for _, identity, raw in original}}
                    if old['operation_kind'] == 'append_version' else {}, expected_meta)
            def replay_original_import():
                assert_replay(ledger, json.loads((root / 'import-a-original.json').read_bytes()),
                    dict(package=(root / 'source.package').read_bytes(),
                         descriptor=(root / 'source.descriptor').read_bytes()), expected_meta)
            replay_original_import()
            new_contents = [('new/文件', 'blob:b-new', b'B independently appended\n'), original[0]]
            new = append('b-new', ['version:merge'], new_contents)
            # Reused bytes remain part of the closure, with no need to retransmit.
            new['body']['blobs'] = [item for item in new['body']['blobs'] if item['blob_ref'] == 'blob:b-new']
            response = ledger.execute(wire(new), payloads={'blob:b-new': b'B independently appended\n'})
            expected_meta = assert_append_committed(ledger, expected_meta, new, response)
            replay_original_import()
            assert_replay(ledger, imported, dict(package=(root / 'a.package').read_bytes(),
                descriptor=(root / 'a.descriptor').read_bytes()), expected_meta)
            bundle = ledger.export_bundle()
            (root / 'b.package').write_bytes(bundle['package_utf8'])
            (root / 'b.descriptor').write_bytes(bundle['descriptor_utf8'])
            # The checked output's actual array order participates in the next
            # import fingerprint, even though record-set comparison ignores it.
            (root / 'b-metadata.json').write_bytes(wire(portable_metadata(bundle)))
            (root / 'b-history.json').write_bytes(wire(ledger.get_history('artifact:cross')))
            assert ledger.verify()['counts']['versions'] == 5
    elif mode == 'reopen-b':
        # A different process with no source checkout available reads persisted B bytes.
        with public.open(b_db) as ledger:
            assert ledger.read_blob('blob:b-new') == b'B independently appended\n'
            assert ledger.get_history('artifact:cross') == json.loads((root / 'b-history.json').read_bytes())
            assert_metadata(ledger, json.loads((root / 'b-metadata.json').read_bytes()))
    else:
        before = sha(b_db.read_bytes())
        try:
            public.open(b_db)
        except public.LedgerError:
            pass
        else:
            raise AssertionError('A opened B physical format')
        assert sha(b_db.read_bytes()) == before
        with public.initialize(final_db) as ledger:
            final_import = request('import_bundle', dict(import_receipt_id='receipt:final-a'), 'import-b')
            result = ledger.execute(wire(final_import), package=(root / 'b.package').read_bytes(),
                                    descriptor=(root / 'b.descriptor').read_bytes())
            assert ledger.get_history('artifact:cross') == json.loads((root / 'b-history.json').read_bytes())
            assert ledger.read_blob('blob:b-new') == b'B independently appended\n'
            for _, identity, raw in original:
                assert ledger.read_blob(identity) == raw
            expected_meta = json.loads((root / 'b-metadata.json').read_bytes())
            assert_import_preserved(ledger, expected_meta, final_import, result)
            assert ledger.verify()['verified_blob_count'] == 4
    return dict(step=mode, status='PASS', python=platform.python_version(),
                distribution=expected, installed_module=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--a-python')
    parser.add_argument('--b-python')
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--worker', choices=('seed-a', 'consume-b', 'reopen-b', 'consume-a'))
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.workdir), sort_keys=True))
        return
    args.workdir.mkdir(parents=True, exist_ok=False)
    rows = []
    for mode, interpreter in [('seed-a', args.a_python), ('consume-b', args.b_python),
                               ('reopen-b', args.b_python), ('consume-a', args.a_python)]:
        result = subprocess.run([interpreter, '-I', str(Path(__file__).resolve()),
                                 '--worker', mode, '--workdir', str(args.workdir)],
                                capture_output=True, check=False)
        (args.workdir / (mode + '.stdout')).write_bytes(result.stdout)
        (args.workdir / (mode + '.stderr')).write_bytes(result.stderr)
        if result.returncode:
            raise RuntimeError(mode + ': ' + result.stderr.decode())
        rows.append(json.loads(result.stdout))
    (args.workdir / 'summary.json').write_bytes(wire(rows) + b'\n')
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
