"""Opt-in installed A1 string length acceptance through public API and CLI.

Run with the selected installation's python -I -B, --implementation a|b and
--workdir NEW_DIRECTORY. Adjacent JSON/transport/cross-contract helpers are
required. All databases and inputs are synthetic; actual public limits apply.
"""
import argparse
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import platform
import sqlite3
import sys
import time

spec = importlib.util.spec_from_file_location('json_probe',
    Path(__file__).with_name('json_resource_probe.py'))
j = importlib.util.module_from_spec(spec)
spec.loader.exec_module(j)
h, public, f, cross = j.h, j.public, j.f, j.cross


def axes():
    """Limits and field placements come from INTERFACE_PROFILE sections 2/5."""
    result = []

    def add(name, kind, minimum, maximum, *paths, unicode=True):
        result.append(dict(name=name, kind=kind, minimum=minimum, maximum=maximum,
                           paths=paths, unicode=unicode))

    for name, kind, paths in (
        ('artifact_id', 'create_artifact', ('body/artifact/artifact_id',)),
        ('version_id', 'append_version', ('body/version/version_id',
                                        'body/provenance_links/0/subject_version_ref')),
        ('content_root_ref', 'append_version', ('body/version/content_root_ref',
                                              'body/content_roots/0/content_root_ref')),
        ('blob_ref', 'append_version', ('body/blobs/0/blob_ref',
                                      'body/manifests/0/entries/0/blob_ref')),
        ('manifest_ref', 'append_version', ('body/manifests/0/manifest_ref',
                                          'body/content_roots/0/manifest_ref')),
        ('provenance_id', 'append_version', ('body/provenance_links/0/provenance_id',)),
        ('import_receipt_id', 'import_bundle', ('body/import_receipt_id',)),
    ):
        add(name, kind, 3, 256, *paths, unicode=False)
    add('namespace_ref', 'create_artifact', 1, 512, 'body/artifact/namespace_ref')
    for name in ('external_source_refs', 'capture_refs', 'handling_policy_refs'):
        add(name, 'append_version', 1, 512, 'body/version/' + name + '/0')
    add('principal_ref', 'append_version', 1, 512, 'body/version/principal_ref')
    add('provenance_object_ref', 'append_version', 1, 512, 'body/provenance_links/0/object/ref')
    for group, kind, prefix in (
        ('type', 'create_artifact', 'body/artifact/type_ref/'),
        ('relation', 'append_version', 'body/provenance_links/0/relation_ref/'),
    ):
        for field, maximum in (('namespace', 128), (group + '_id', 128), (group + '_version', 64)):
            add(group + '_' + field, kind, 1, maximum, prefix + field)
    add('entry_key', 'append_version', 1, 512, 'body/manifests/0/entries/0/entry_key')
    for kind in ('create_artifact', 'append_version', 'import_bundle'):
        add(kind + '_scope', kind, 1, 512, 'idempotency_scope_ref')
        add(kind + '_key', kind, 1, 256, 'idempotency_key')
    assert len(result) == 26 and len({a['name'] for a in result}) == 26
    return result


def set_field(value, path, text):
    parts = path.split('/')
    for part in parts[:-1]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    value[int(parts[-1]) if isinstance(value, list) else parts[-1]] = text


def fixture(axis, text):
    kind = axis['kind']
    if kind == 'create_artifact':
        value = f.create()
    elif kind == 'import_bundle':
        value = f.import_request()
    else:
        value = f.append(raw=b'string-boundary\x00\xff')
        body = value['body']
        body['version'].update(capture_refs=['capture:original'], handling_policy_refs=['policy:original'])
        body['content_roots'] = [dict(content_root_ref='root:one', kind='manifest', manifest_ref='manifest:one')]
        body['manifests'] = [dict(manifest_ref='manifest:one',
                                entries=[dict(entry_key='logical-entry', blob_ref='blob:one')])]
        body['provenance_links'] = [dict(provenance_id='provenance:one', subject_version_ref='version:one',
            relation_ref=dict(namespace='test', relation_id='derived', relation_version='1'),
            object=dict(kind='external_source', ref='source:original'))]
    for path in axis['paths']:
        set_field(value, path, text)
    kwargs = {}
    if kind == 'append_version':
        manifest = value['body']['manifests'][0]
        # Recompute the legitimate digest so an invalid entry length is the only
        # selected violation, rather than an unrelated integrity failure.
        manifest['digest'] = f.sha(f.wire(dict(entries=manifest['entries'])))
        kwargs['payloads'] = {value['body']['blobs'][0]['blob_ref']: b'string-boundary\x00\xff'}
    elif kind == 'import_bundle':
        package = f.wire(dict(transport_version='0.1.0', profile=f.PROFILE,
                             metadata=h.entry(f.wire(h.empty_metadata())), payloads=[]))
        kwargs = dict(package=package, descriptor=h.description(package))
    return value, kwargs


def sample(length, alphabet):
    pattern = 'x' if alphabet == 'ascii' else '\U0001f600e\u0301'
    text = (pattern * ((length + len(pattern) - 1) // len(pattern)))[:length]
    assert len(text) == length
    return text


def invoke(root, mode, db, value, kwargs, stage):
    raw = f.wire(value)
    request_path = root / (stage + '.request.json')
    request_path.write_bytes(raw)
    if mode == 'api':
        with public.open(db) as ledger:
            try:
                response = ledger.execute(raw, **kwargs)
            except public.LedgerError as exc:
                response = dict(status='ERROR', code=exc.code, commit_state=exc.commit_state)
                (root / (stage + '.response.json')).write_bytes(f.wire(response))
                return response
    else:
        args = ['write', '--db', db, '--request', request_path]
        if 'payloads' in kwargs:
            mapping = []
            for index, (ref, raw_blob) in enumerate(kwargs['payloads'].items()):
                blob_path = root / (stage + '-' + str(index) + '.blob')
                blob_path.write_bytes(raw_blob)
                mapping.append(dict(blob_ref=ref, input_path=str(blob_path)))
            mapping_path = root / (stage + '.payload-map.json')
            mapping_path.write_bytes(f.wire(mapping))
            args.extend(['--payload-map', mapping_path])
        if 'package' in kwargs:
            for name in ('package', 'descriptor'):
                path = root / (stage + '.' + name)
                path.write_bytes(kwargs[name])
                args.extend(['--' + name, path])
        exit_code, response = h.cli(root, *args)
        assert exit_code == (2 if response.get('status') == 'ERROR' else 0), response
    (root / (stage + '.response.json')).write_bytes(f.wire(response))
    return response


def check(root, axis, alphabet, length, mode):
    root.mkdir()
    db = root / 'ledger.sqlite'
    seed = f.create(key='seed') if axis['kind'] == 'append_version' else f.create('artifact:sentinel', 'seed')
    with public.initialize(db) as ledger:
        before = j.assert_created(ledger, h.empty_metadata(), seed, ledger.execute(f.wire(seed)))
    original_digest = h.file_digest(db)
    text = sample(length, alphabet)
    value, kwargs = fixture(axis, text)
    valid = axis['minimum'] <= length <= axis['maximum']
    response = invoke(root, mode, db, value, kwargs, 'boundary')
    if not valid:
        assert (response.get('status'), response.get('code'), response.get('commit_state')) == (
            'ERROR', 'INVALID_INPUT', 'not_committed'), response
        assert h.file_digest(db) == original_digest, 'rejection changed database bytes'
        with public.open(db) as ledger:
            cross.assert_metadata(ledger, before)
        # Correct only this field and its dependent links/digest. Preserve all
        # other request identity fields; no fabricated retry with a different key.
        corrected_length = min(axis['maximum'], max(axis['minimum'], length))
        value, kwargs = fixture(axis, sample(corrected_length, alphabet))
        response = invoke(root, mode, db, value, kwargs, 'corrected')
    with public.open(db) as ledger:
        if axis['kind'] == 'create_artifact':
            expected = j.assert_created(ledger, before, value, response)
        elif axis['kind'] == 'append_version':
            expected = cross.assert_append_committed(ledger, before, value, response)
        else:
            expected, _ = h.expected_import(before, h.empty_metadata(), value, response)
            cross.assert_metadata(ledger, expected)
    (root / 'expected-metadata.json').write_bytes(f.wire(expected))
    committed_digest = h.file_digest(db)
    replay = invoke(root, mode, db, value, kwargs, 'replay')
    operation = expected['idempotency_records'][-1]
    cross.assert_response(replay, operation, True)
    with public.open(db) as ledger:
        cross.assert_metadata(ledger, expected)
    assert h.file_digest(db) == committed_digest, 'replay changed database bytes'
    return dict(axis=axis['name'], alphabet=alphabet, length=length, utf8_bytes=len(text.encode()),
                mode=mode, expected_code='COMMITTED' if valid else 'INVALID_INPUT',
                corrected=not valid, status='PASS')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--implementation', choices=('a', 'b'), required=True)
    parser.add_argument('--workdir', type=Path, required=True)
    args = parser.parse_args()
    assert sys.flags.isolated and not sys.flags.optimize and sys.dont_write_bytecode
    module = Path(public.__file__).resolve().parent
    assert Path(sys.prefix).resolve() in module.parents and 'site-packages' in module.parts
    dist = 'infra-artifact-ledger' + ('-b' if args.implementation == 'b' else '')
    other = 'infra-artifact-ledger' + ('' if args.implementation == 'b' else '-b')
    version = importlib.metadata.version(dist)
    try:
        importlib.metadata.distribution(other)
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise AssertionError('A/B environments must remain separate')
    args.workdir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = dict(implementation=args.implementation, distribution=dist, version=version,
        module=str(module), python=platform.python_version(), sqlite=sqlite3.sqlite_version,
        runtime_sha256={p.name: h.file_digest(p) for p in sorted(module.glob('*.py'))},
        axes=axes(), checks=[])
    try:
        for axis in result['axes']:
            lo, hi = axis['minimum'], axis['maximum']
            for alphabet in (('ascii', 'unicode') if axis['unicode'] else ('ascii',)):
                for length in (lo-1, lo, lo+1, hi-1, hi, hi+1):
                    for mode in ('api', 'cli'):
                        name = '-'.join((axis['name'], alphabet, str(length), mode))
                        result['current_case'] = name
                        result['checks'].append(check(args.workdir / name, axis, alphabet, length, mode))
            (args.workdir / 'progress.json').write_text(json.dumps(result, indent=2) + '\n')
            print(axis['name'], 'PASS', len(result['checks']), flush=True)
        result.pop('current_case', None)
        result['status'] = 'PASS'
    except BaseException as exc:
        result.update(status='FAILED', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (args.workdir / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
