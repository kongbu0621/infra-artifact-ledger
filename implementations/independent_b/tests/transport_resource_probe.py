"""Opt-in installed A1 transport limits through public API and real CLI.

Run each axis in its own process using the selected installation's python -I -B.
Uses a NEW local synthetic directory; retains inputs, databases and results.
No runtime constants or implementation internals are used or patched.
"""
import argparse
import base64
import copy
import gc
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import platform
import resource
import sqlite3
import subprocess
import sys
import time

import infra_artifact_ledger as public


def helper(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


f = helper('test_public_contract')
cross = helper('cross_implementation')
MiB = 1048576
LIMITS = {'package': 384 * MiB, 'descriptor': 4096,
          'metadata': 8 * MiB, 'payload': 256 * MiB}
COLLECTIONS = [item[1] for item in cross.COLLECTIONS] + ['idempotency_records']


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def empty_metadata():
    return dict(contract_version='0.1.0', contract_status='candidate',
                schema_id='urn:code-driver-theory:artifact-ledger:v0.1:candidate',
                theory_baseline=dict(repository='kongbu0621/code-driver-theory',
                    commit='9e2bc8217f05d5b604e24fa56dad2525f35983f1'),
                **{key: [] for key in COLLECTIONS})


def entry(raw):
    return dict(encoding='base64', data=base64.b64encode(raw).decode('ascii'),
                byte_length=len(raw), digest=f.sha(raw))


def description(raw):
    return f.wire(dict(transport_version='0.1.0', package_byte_length=len(raw),
                       package_digest=f.sha(raw)))


def padded(raw, length):
    assert len(raw) <= length
    return raw + b' ' * (length - len(raw))


def fixture(axis, length):
    """Independent, valid envelope; only the selected resource crosses its limit."""
    meta, payloads = empty_metadata(), []
    if axis == 'payload':
        when = '2026-10-30T00:00:00Z'
        meta['artifacts'].append({**f.create()['body']['artifact'], 'recorded_at': when})
        remaining, index = length, 0
        while remaining:
            size = min(64 * MiB, remaining)
            raw = bytes([97 + index]) * size
            body = f.append('version:' + str(index), 'blob:' + str(index),
                            'root:' + str(index), raw)['body']
            meta['versions'].append({**body['version'], 'recorded_at': when})
            meta['content_roots'].extend(body['content_roots'])
            meta['blobs'].extend(body['blobs'])
            payloads.append(dict(blob_ref='blob:' + str(index), **entry(raw)))
            remaining -= size
            index += 1
        del raw
    meta_raw = f.wire(meta)
    if axis == 'metadata':
        meta_raw = padded(meta_raw, length)
    package = f.wire(dict(transport_version='0.1.0', profile=f.PROFILE,
                          metadata=entry(meta_raw), payloads=payloads))
    if axis == 'package':
        package = padded(package, length)
    descriptor = description(package)
    if axis == 'descriptor':
        descriptor = padded(descriptor, length)
    measured = dict(package=len(package), descriptor=len(descriptor),
                    metadata=len(meta_raw), payload=sum(p['byte_length'] for p in payloads))
    assert measured[axis] == length
    assert all(measured[key] <= limit for key, limit in LIMITS.items() if key != axis)
    return meta, package, descriptor, measured


def cli(root, *args):
    started = time.monotonic()
    result = subprocess.run([sys.executable, '-I', '-B', '-m', 'infra_artifact_ledger',
                             *map(str, args)], cwd=root, capture_output=True, timeout=240)
    with (root / 'cli.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(args=list(map(str, args)), exit_code=result.returncode,
            stdout=result.stdout.decode(), stderr=result.stderr.decode(),
            seconds=round(time.monotonic() - started, 3))) + '\n')
    assert result.stderr == b'', result.stderr
    response = json.loads(result.stdout)
    assert result.stdout == f.wire(response) + b'\n'
    return result.returncode, response


def expected_import(before, incoming, request, response):
    receipt = dict(import_receipt_id=request['body']['import_receipt_id'],
        imported_artifact_refs=sorted(item['artifact_id'] for item in incoming['artifacts']),
        recorded_at=response['recorded_at'])
    operation = cross.operation_for(request, receipt['import_receipt_id'],
                                    receipt['recorded_at'], incoming)
    cross.assert_response(response, operation, False)
    expected = copy.deepcopy(before)
    for key in COLLECTIONS:
        expected[key].extend(copy.deepcopy(incoming[key]))
    expected['import_receipts'].append(receipt)
    expected['idempotency_records'].append(operation)
    return expected, operation


def check(root, mode, incoming, package, descriptor, accepted):
    root.mkdir()
    db = root / 'ledger.sqlite'
    request = f.import_request()
    raw_request = f.wire(request)
    (root / 'request.json').write_bytes(raw_request)
    with public.initialize(db) as ledger:
        ledger.execute(f.wire(f.create('artifact:sentinel', 'sentinel')))
        before = cross.portable_metadata(ledger.export_bundle())
    # CLI uses the same actual large inputs as the API; all invocations are -I.
    package_path, descriptor_path = root.parent/'package.json', root.parent/'descriptor.json'

    def invoke(raw, desc):
        if mode == 'api':
            with public.open(db) as ledger:
                return ledger.execute(raw_request, package=raw, descriptor=desc)
        code, response = cli(root, 'write', '--db', db, '--request', root/'request.json',
                             '--package', package_path, '--descriptor', descriptor_path)
        assert code == (0 if response['status'] == 'COMMITTED' else 2), response
        return response

    prior = file_digest(db)
    if not accepted:
        if mode == 'api':
            try:
                invoke(package, descriptor)
            except public.LedgerError as exc:
                assert (exc.code, exc.commit_state) == ('RESOURCE_LIMIT', 'not_committed')
            else:
                raise AssertionError('oversized import accepted')
        else:
            response = invoke(package, descriptor)
            assert (response['status'], response['code'], response['commit_state']) == (
                'ERROR', 'RESOURCE_LIMIT', 'not_committed'), response
        assert file_digest(db) == prior, 'refusal changed original database'
        with public.open(db) as ledger:
            cross.assert_metadata(ledger, before)
            try:
                ledger.get_operation(request['idempotency_scope_ref'], 'import_bundle', request['idempotency_key'])
            except public.LedgerError as exc:
                assert exc.code == 'NOT_FOUND'
            else:
                raise AssertionError('failed import recorded an operation')
        # Same identity can continue after rejection with a valid empty package.
        incoming, package, descriptor, _ = fixture('descriptor', 4096)
        if mode == 'cli':
            package_path, descriptor_path = root/'retry-package.json', root/'retry-descriptor.json'
            package_path.write_bytes(package)
            descriptor_path.write_bytes(descriptor)
    response = invoke(package, descriptor)
    expected, operation = expected_import(before, incoming, request, response)
    with public.open(db) as ledger:
        cross.assert_metadata(ledger, expected)
        expected_verify = ledger.verify()
    committed = file_digest(db)
    cross.assert_response(invoke(package, descriptor), operation, True)
    assert file_digest(db) == committed, 'replay changed database'
    # Independent new process verifies the committed contents after all handles close.
    code, reopened = cli(root, 'verify', '--db', db)
    assert code == 0 and f.wire(reopened) == f.wire(dict(
        status='OK', commit_state='not_applicable', data=expected_verify))
    (root/'expected-metadata.json').write_bytes(f.wire(expected))
    return dict(mode=mode, boundary_accepted=accepted, original_state_preserved_on_rejection=not accepted,
                continuation_and_replay='PASS', new_process_verify='PASS', database_sha256=committed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--axis', choices=LIMITS, required=True)
    parser.add_argument('--implementation', choices=('a', 'b'), required=True)
    parser.add_argument('--workdir', type=Path, required=True)
    args = parser.parse_args()
    if not sys.flags.isolated or sys.flags.optimize:
        parser.error('use an installed interpreter with -I and without -O')
    package_dir = Path(public.__file__).resolve().parent
    assert Path(sys.prefix).resolve() in package_dir.parents and 'site-packages' in package_dir.parts
    dist = 'infra-artifact-ledger' + ('-b' if args.implementation == 'b' else '')
    other = 'infra-artifact-ledger' + ('' if args.implementation == 'b' else '-b')
    version = importlib.metadata.version(dist)
    try:
        importlib.metadata.distribution(other)
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise AssertionError('A and B must not be installed together')
    args.workdir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = dict(implementation=args.implementation, axis=args.axis, distribution=dist, version=version,
        python=platform.python_version(), sqlite=sqlite3.sqlite_version, platform=platform.platform(),
        module=str(package_dir), runtime_sha256={p.name: file_digest(p) for p in sorted(package_dir.glob('*.py'))},
        cases=[])
    for offset in (-1, 0, 1):
        length = LIMITS[args.axis] + offset
        root = args.workdir / str(length)
        root.mkdir()
        then = time.monotonic()
        incoming, package, descriptor, measured = fixture(args.axis, length)
        (root/'package.json').write_bytes(package)
        (root/'descriptor.json').write_bytes(descriptor)
        item = dict(measured_bytes=measured, inputs_sha256=dict(package=f.sha(package)['value'],
            descriptor=f.sha(descriptor)['value']), checks=[])
        for mode in ('api', 'cli'):
            item['checks'].append(check(root/mode, mode, incoming, package, descriptor, offset <= 0))
        item['elapsed_seconds'] = round(time.monotonic() - then, 3)
        result['cases'].append(item)
        (args.workdir/'progress.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(dict(axis=args.axis, measured_bytes=length, status='PASS',
                              seconds=item['elapsed_seconds'])), flush=True)
        del incoming, package, descriptor
        gc.collect()
    result.update(status='PASS', elapsed_seconds=round(time.monotonic() - started, 3),
                  parent_max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  child_max_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    (args.workdir/'summary.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
