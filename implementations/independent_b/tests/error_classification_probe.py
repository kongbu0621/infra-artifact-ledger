"""Opt-in A4-07 acceptance using an existing A or B installation.

Run python -I -B with --implementation a|b and --workdir NEW_DIRECTORY.
Every rejection starts with valid synthetic input and one selected violation.
Outer transport hashes are renewed when testing an inner layer. Only public
API/CLI calls are used; adjacent public test fixtures supply independent oracles.
"""
import argparse
import base64
import copy
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import platform
import re
import sqlite3
import sys
import time

spec = importlib.util.spec_from_file_location('json_probe',
    Path(__file__).with_name('json_resource_probe.py'))
j = importlib.util.module_from_spec(spec)
spec.loader.exec_module(j)
h, public, f, cross = j.h, j.public, j.f, j.cross
EXITS = dict(INVALID_INPUT=2, UNSUPPORTED_VERSION=2, UNSUPPORTED_PROFILE=2,
             INTEGRITY_FAILURE=5, IO_ERROR=1)
BLOB = b'a\x00\xffz'


def append():
    value = f.append(raw=BLOB)
    body = value['body']
    body['content_roots'] = [dict(content_root_ref='root:one', kind='manifest', manifest_ref='manifest:one')]
    entries = [dict(entry_key='report', blob_ref='blob:one')]
    body['manifests'] = [dict(manifest_ref='manifest:one', entries=entries,
                              digest=f.sha(f.wire(dict(entries=entries))))]
    return value


def fixture(kind):
    if kind == 'create':
        return dict(request=f.wire(f.create('artifact:new', 'new')), kwargs={})
    value = append()
    if kind == 'append':
        return dict(request=f.wire(value), kwargs=dict(payloads={'blob:one': BLOB}))
    meta = h.empty_metadata()
    when = '2026-10-30T00:00:00Z'
    meta['artifacts'] = [{**f.create('artifact:incoming')['body']['artifact'], 'recorded_at': when}]
    meta['versions'] = [{**value['body']['version'], 'artifact_id': 'artifact:incoming', 'recorded_at': when}]
    for key in ('content_roots', 'blobs', 'manifests', 'provenance_links'):
        meta[key] = value['body'][key]
    # Valid trailing JSON whitespace ensures Base64 padding for pad-bit cases.
    raw_meta = f.wire(meta)
    raw_meta += b' ' * ((1 - len(raw_meta)) % 3)
    package = f.wire(dict(transport_version='0.1.0', profile=f.PROFILE,
        metadata=h.entry(raw_meta), payloads=[dict(blob_ref='blob:one', **h.entry(BLOB))]))
    return dict(request=f.wire(f.import_request()),
                kwargs=dict(package=package, descriptor=h.description(package)))


def layer_bytes(value, layer):
    if layer == 'request':
        return value['request']
    if layer == 'metadata':
        return base64.b64decode(json.loads(value['kwargs']['package'])['metadata']['data'], validate=True)
    return value['kwargs'][layer]


def replace_layer(value, layer, raw):
    if layer == 'request':
        value['request'] = raw
    elif layer == 'metadata':
        package = json.loads(value['kwargs']['package'])
        package['metadata'] = h.entry(raw)
        replace_layer(value, 'package', f.wire(package))
    elif layer == 'package':
        value['kwargs'].update(package=raw, descriptor=h.description(raw))
    else:
        value['kwargs'][layer] = raw


def edit(value, layer, change):
    obj = json.loads(layer_bytes(value, layer))
    change(obj)
    replace_layer(value, layer, f.wire(obj))


def invalid_json(raw, fault):
    if fault == 'bom':
        return b'\xef\xbb\xbf' + raw
    if fault == 'trailing':
        return raw + b'x'
    if fault == 'duplicate-key':
        key = next(iter(json.loads(raw)))
        item = f.wire({key: json.loads(raw)[key]})[1:-1]
        return b'{' + item + b',' + raw[1:]
    # Replace one existing string value, preserving the surrounding JSON shape.
    match = re.search(rb':"(?:[^"\\]|\\.)*"', raw)
    assert match is not None
    token = {'utf8': b'"\xff"', 'surrogate': b'"\\ud800"',
             'nan': b'NaN', 'infinity': b'Infinity'}[fault]
    return raw[:match.start()+1] + token + raw[match.end():]


def pad_bits(text):
    assert text.endswith('=')
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    index = len(text.rstrip('=')) - 1
    changed = text[:index] + alphabet[alphabet.index(text[index]) + 1] + text[index+1:]
    assert base64.b64decode(changed, validate=True) == base64.b64decode(text, validate=True)
    assert changed != text
    return changed


def cases():
    result = []

    def add(name, kind, code, mutate, modes=('api', 'cli')):
        result.append(dict(name=name, kind=kind, code=code, mutate=mutate, modes=modes))

    for layer in ('request', 'descriptor', 'package', 'metadata'):
        kind = 'create' if layer == 'request' else 'import'
        for fault in ('bom', 'trailing', 'duplicate-key', 'utf8', 'surrogate', 'nan', 'infinity'):
            add(layer+'-'+fault, kind, 'INVALID_INPUT',
                lambda v, layer=layer, fault=fault: replace_layer(v, layer, invalid_json(layer_bytes(v, layer), fault)))
        add(layer+'-unknown-field', kind, 'INVALID_INPUT',
            lambda v, layer=layer: edit(v, layer, lambda obj: obj.update(unknown=1)))

    for layer, field, code in (
        ('request', 'contract_version', 'UNSUPPORTED_VERSION'),
        ('request', 'profile', 'UNSUPPORTED_PROFILE'),
        ('descriptor', 'transport_version', 'UNSUPPORTED_VERSION'),
        ('package', 'transport_version', 'UNSUPPORTED_VERSION'),
        ('package', 'profile', 'UNSUPPORTED_PROFILE'),
        ('metadata', 'contract_version', 'UNSUPPORTED_VERSION'),
        ('metadata', 'contract_status', 'UNSUPPORTED_VERSION'),
        ('metadata', 'schema_id', 'UNSUPPORTED_VERSION'),
    ):
        add(layer+'-'+field, 'create' if layer == 'request' else 'import', code,
            lambda v, layer=layer, field=field: edit(v, layer, lambda obj: obj.update({field: 'unsupported'})))

    for kind, layer in (('append', 'request'), ('import', 'metadata')):
        def body(obj, layer=layer):
            return obj['body'] if layer == 'request' else obj
        add(kind+'-manifest-digest', kind, 'INTEGRITY_FAILURE',
            lambda v, layer=layer, body=body: edit(v, layer,
                lambda obj: body(obj)['manifests'][0].update(digest=f.sha(b'wrong'))))
        def erased(v, layer=layer, body=body):
            edit(v, layer, lambda obj: body(obj)['blobs'][0].update(payload_availability='erased'))
            if layer == 'metadata':
                edit(v, 'package', lambda obj: obj.update(payloads=[]))
            else:
                v['kwargs']['payloads'] = {}
        add(kind+'-erased', kind, 'UNSUPPORTED_PROFILE', erased)
        for token in (b'4.0', b'4e0', b'true', b'-1'):
            def bad_integer(v, layer=layer, token=token):
                raw = layer_bytes(v, layer)
                assert raw.count(b'"byte_length":4') == 1
                replace_layer(v, layer, raw.replace(b'"byte_length":4', b'"byte_length":'+token))
            add(kind+'-integer-'+token.decode(), kind, 'INVALID_INPUT', bad_integer)

    for field in ('package_byte_length', 'package_digest'):
        add('descriptor-'+field, 'import', 'INTEGRITY_FAILURE',
            lambda v, field=field: edit(v, 'descriptor', lambda obj: obj.update({field:
                obj[field]+1 if field.endswith('length') else f.sha(b'wrong')})))
    for entry in ('metadata', 'payload'):
        def selected(obj, entry=entry):
            return obj['metadata'] if entry == 'metadata' else obj['payloads'][0]
        for field in ('byte_length', 'digest'):
            add(entry+'-'+field, 'import', 'INTEGRITY_FAILURE',
                lambda v, field=field, selected=selected: edit(v, 'package', lambda obj:
                    selected(obj).update({field: selected(obj)[field]+1 if field == 'byte_length' else f.sha(b'wrong')})))
        for fault in ('alphabet', 'whitespace', 'padding', 'pad-bits'):
            def bad_base64(v, selected=selected, fault=fault):
                def change(obj):
                    item = selected(obj)
                    text = item['data']
                    item['data'] = {'alphabet': lambda: '!'+text[1:],
                        'whitespace': lambda: ' '+text[1:], 'padding': lambda: text[:-1],
                        'pad-bits': lambda: pad_bits(text)}[fault]()
                edit(v, 'package', change)
            add(entry+'-base64-'+fault, 'import', 'INVALID_INPUT', bad_base64)

    add('missing-payload', 'import', 'INTEGRITY_FAILURE',
        lambda v: edit(v, 'package', lambda obj: obj.update(payloads=[])))
    add('duplicate-payload', 'import', 'INVALID_INPUT',
        lambda v: edit(v, 'package', lambda obj: obj['payloads'].append(copy.deepcopy(obj['payloads'][0]))))
    add('extra-payload', 'import', 'INTEGRITY_FAILURE',
        lambda v: edit(v, 'package', lambda obj: obj['payloads'].append(dict(blob_ref='blob:extra', **h.entry(b'extra')))))
    # A self-consistent transport entry whose bytes differ from Blob metadata.
    add('payload-versus-blob', 'import', 'INTEGRITY_FAILURE',
        lambda v: edit(v, 'package', lambda obj: obj['payloads'][0].update(h.entry(b'new!'))))
    for field in ('package', 'descriptor'):
        add('missing-argument-'+field, 'import', 'INVALID_INPUT', lambda v, field=field: v['kwargs'].pop(field))
    add('missing-argument-payloads', 'append', 'INVALID_INPUT', lambda v: v['kwargs'].pop('payloads'))
    for field, kind in (('request', 'create'), ('package', 'import'), ('descriptor', 'import'),
                        ('payload-map', 'append'), ('blob', 'append')):
        add('missing-file-'+field, kind, 'IO_ERROR', lambda v, field=field: v.update(missing_file=field), ('cli',))
    assert len({item['name'] for item in result}) == len(result)
    return result


def invoke(root, mode, db, value, stage):
    folder = root/stage
    folder.mkdir()
    if mode == 'api':
        (folder/'input.json').write_bytes(value['request'])
        with public.open(db) as ledger:
            try:
                response = ledger.execute(value['request'], **value['kwargs'])
            except public.LedgerError as exc:
                response = dict(status='ERROR', code=exc.code, message=exc.message, commit_state=exc.commit_state)
                if exc.details is not None:
                    response['details'] = exc.details
        exit_code = None
    else:
        def put(name, raw):
            path = folder/name
            if value.get('missing_file') != name:
                path.write_bytes(raw)
            return path
        args = ['write', '--db', db, '--request', put('request', value['request'])]
        kwargs = value['kwargs']
        if 'payloads' in kwargs:
            mapping = [dict(blob_ref=ref, input_path=str(put('blob', raw))) for ref, raw in kwargs['payloads'].items()]
            assert len(mapping) <= 1
            args.extend(['--payload-map', put('payload-map', f.wire(mapping))])
        for name in ('package', 'descriptor'):
            if name in kwargs:
                args.extend(['--'+name, put(name, kwargs[name])])
        exit_code, response = h.cli(folder, *args)
    (folder/'response.json').write_bytes(f.wire(response))
    return exit_code, response


def assert_error(response, code, exit_code, mode):
    assert {'status', 'code', 'message', 'commit_state'} <= response.keys() <= {
        'status', 'code', 'message', 'commit_state', 'details'}, response
    assert (response['status'], response['code'], response['commit_state']) == ('ERROR', code, 'not_committed'), response
    assert type(response['message']) is str
    assert 'details' not in response or type(response['details']) is dict
    if mode == 'cli':
        assert exit_code == EXITS[code], (exit_code, response)


def check(root, case, mode):
    root.mkdir()
    db = root/'ledger.sqlite'
    with public.initialize(db) as ledger:
        seed = f.create(key='seed-create')
        before = j.assert_created(ledger, h.empty_metadata(), seed, ledger.execute(f.wire(seed)))
        seed = f.append('version:sentinel', 'blob:sentinel', 'root:sentinel', b'preserve\x00\xff', key='seed-append')
        before = cross.assert_append_committed(ledger, before, seed,
            ledger.execute(f.wire(seed), payloads={'blob:sentinel': b'preserve\x00\xff'}))
    original = fixture(case['kind'])
    changed = copy.deepcopy(original)
    case['mutate'](changed)
    assert changed != original, 'fault must actually change the input'
    prior = h.file_digest(db)
    exit_code, response = invoke(root, mode, db, changed, 'rejected')
    assert_error(response, case['code'], exit_code, mode)
    assert h.file_digest(db) == prior, 'rejection changed database bytes'
    with public.open(db) as ledger:
        cross.assert_metadata(ledger, before)
    # Restore just the selected violation using the original valid input, same
    # identity/key, same target. Success is the positive control for this case.
    exit_code, response = invoke(root, mode, db, original, 'corrected')
    assert mode != 'cli' or exit_code == 0, response
    request = json.loads(original['request'])
    with public.open(db) as ledger:
        if case['kind'] == 'create':
            expected = j.assert_created(ledger, before, request, response)
        elif case['kind'] == 'append':
            expected = cross.assert_append_committed(ledger, before, request, response)
        else:
            incoming = json.loads(layer_bytes(original, 'metadata'))
            expected, _ = h.expected_import(before, incoming, request, response)
            cross.assert_metadata(ledger, expected)
    committed = h.file_digest(db)
    exit_code, response = invoke(root, mode, db, original, 'replayed')
    assert mode != 'cli' or exit_code == 0, response
    cross.assert_response(response, expected['idempotency_records'][-1], True)
    with public.open(db) as ledger:
        cross.assert_metadata(ledger, expected)
    assert h.file_digest(db) == committed, 'replay changed database bytes'
    (root/'expected-metadata.json').write_bytes(f.wire(expected))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--implementation', choices=('a', 'b'), required=True)
    parser.add_argument('--workdir', type=Path, required=True)
    args = parser.parse_args()
    if not sys.flags.isolated or sys.flags.optimize or not sys.dont_write_bytecode:
        parser.error('use an installed interpreter with -I -B and without -O/-OO')
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
    report = dict(implementation=args.implementation, distribution=dist, version=version,
        python=platform.python_version(), sqlite=sqlite3.sqlite_version, module=str(module),
        runtime_sha256={p.name: h.file_digest(p) for p in sorted(module.glob('*.py'))}, checks=[])
    for case in cases():
        for mode in case['modes']:
            result = dict(case=case['name'], mode=mode, expected_code=case['code'])
            try:
                check(args.workdir/(case['name']+'-'+mode), case, mode)
            except Exception as exc:
                result.update(status='FAILED', error_type=type(exc).__name__, error=str(exc))
                print(case['name'], mode, 'FAILED', str(exc), flush=True)
            else:
                result['status'] = 'PASS'
            report['checks'].append(result)
    report.update(status='PASS' if all(item['status'] == 'PASS' for item in report['checks']) else 'FAILED',
                  elapsed_seconds=round(time.monotonic()-started, 3))
    (args.workdir/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    print(report['status'], len(report['checks']), 'cases', flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
