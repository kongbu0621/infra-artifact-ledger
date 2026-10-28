"""Installed A -> B -> fresh A, using only public API/CLI and synthetic data.

Run with --a-python /isolated/a/bin/python --b-python /isolated/b/bin/python
--workdir NEW_DIRECTORY. No existing business installation or database is used.
"""
import argparse
import base64
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
        with public.initialize(a_db) as ledger:
            ledger.execute(wire(create()))
            ledger.execute(wire(append('original', [], original)),
                           payloads={identity: raw for _, identity, raw in original})
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
        assert result['replayed'] is False
        with public.open(b_db) as ledger:
            assert ledger.get_history('artifact:cross') == json.loads((root / 'original-history.json').read_bytes())
            for _, identity, raw in original:
                assert ledger.read_blob(identity) == raw
            for old in (create(), append('original', [], original)):
                replay = ledger.execute(wire(old), **({'payloads': {identity: raw for _, identity, raw in original}}
                    if old['operation_kind'] == 'append_version' else {}))
                assert replay['replayed'] is True
            new_contents = [('new/文件', 'blob:b-new', b'B independently appended\n'), original[0]]
            new = append('b-new', ['version:original'], new_contents)
            # Reused bytes remain part of the closure, with no need to retransmit.
            new['body']['blobs'] = [item for item in new['body']['blobs'] if item['blob_ref'] == 'blob:b-new']
            ledger.execute(wire(new), payloads={'blob:b-new': b'B independently appended\n'})
            replay = ledger.execute(wire(imported), package=(root / 'a.package').read_bytes(),
                                    descriptor=(root / 'a.descriptor').read_bytes())
            assert replay['replayed'] is True and replay['recorded_at'] == result['recorded_at']
            bundle = ledger.export_bundle()
            (root / 'b.package').write_bytes(bundle['package_utf8'])
            (root / 'b.descriptor').write_bytes(bundle['descriptor_utf8'])
            (root / 'b-metadata.json').write_bytes(wire(portable_metadata(bundle)))
            (root / 'b-history.json').write_bytes(wire(ledger.get_history('artifact:cross')))
            assert ledger.verify()['counts']['versions'] == 2
    elif mode == 'reopen-b':
        # A different process with no source checkout available reads persisted B bytes.
        with public.open(b_db) as ledger:
            assert ledger.read_blob('blob:b-new') == b'B independently appended\n'
            assert ledger.get_history('artifact:cross') == json.loads((root / 'b-history.json').read_bytes())
            assert ledger.verify()['counts']['import_receipts'] == 1
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
            ledger.execute(wire(request('import_bundle', dict(import_receipt_id='receipt:final-a'), 'import-b')),
                           package=(root / 'b.package').read_bytes(), descriptor=(root / 'b.descriptor').read_bytes())
            assert ledger.get_history('artifact:cross') == json.loads((root / 'b-history.json').read_bytes())
            assert ledger.read_blob('blob:b-new') == b'B independently appended\n'
            for _, identity, raw in original:
                assert ledger.read_blob(identity) == raw
            actual = portable_metadata(ledger.export_bundle())
            # Every imported immutable record/op is retained, with exactly one new
            # receipt and successful operation for this import.
            expected_meta = json.loads((root / 'b-metadata.json').read_bytes())
            for name, values in expected_meta.items():
                if name not in ('import_receipts', 'idempotency_records'):
                    assert actual[name] == values, name
                else:
                    assert len(actual[name]) == len(values) + 1
                    assert all(value in actual[name] for value in values), name
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
