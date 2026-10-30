"""Opt-in installed public JSON limits using real files and responses.

Use the selected installation's python -I -B, with --implementation and a new
--workdir. Requires transport_resource_probe.py and its adjacent test helpers.
All fixtures are synthetic; no implementation constants or private APIs change.
"""
import argparse
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import platform
import resource
import sqlite3
import sys
import time

spec = importlib.util.spec_from_file_location('transport_probe',
    Path(__file__).with_name('transport_resource_probe.py'))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
public, f, cross = h.public, h.f, h.cross
LIMIT = 8 * 1048576


def error(call, code, state):
    try:
        call()
    except public.LedgerError as exc:
        assert (exc.code, exc.commit_state) == (code, state)
    else:
        raise AssertionError('expected public rejection')


def cli_error(root, state, *args, code='RESOURCE_LIMIT'):
    exit_code, response = h.cli(root, *args)
    assert exit_code == 2 and response['status'] == 'ERROR', response
    assert (response['code'], response['commit_state']) == (code, state), response


def documents(root):
    results = []
    for kind in ('write', 'operation', 'payload_map'):
        for size in (LIMIT-1, LIMIT, LIMIT+1):
            case = root / (kind + '-' + str(size))
            case.mkdir()
            db = case/'ledger.sqlite'
            with public.initialize(db) as ledger:
                create = f.create()
                created = ledger.execute(f.wire(create))
                prior_operation = ledger.get_operation('demo:scope', 'create_artifact', 'create')
            request = f.create('artifact:second', 'second') if kind == 'write' else f.append(raw=b'')
            if kind == 'operation':
                value = {k: create[k] for k in ('idempotency_scope_ref', 'operation_kind', 'idempotency_key')}
                command = ('operation', '--db', db, '--request', case/'input.json')
                state = 'not_applicable'
            elif kind == 'payload_map':
                (case/'blob').write_bytes(b'')
                (case/'request.json').write_bytes(f.wire(request))
                value = [dict(blob_ref='blob:one', input_path=str(case/'blob'))]
                command = ('write', '--db', db, '--request', case/'request.json', '--payload-map', case/'input.json')
                state = 'not_committed'
            else:
                value = request
                command = ('write', '--db', db, '--request', case/'input.json')
                state = 'not_committed'
            raw = h.padded(f.wire(value), size)
            (case/'input.json').write_bytes(raw)
            before = h.file_digest(db)
            if size > LIMIT:
                if kind == 'write':
                    with public.open(db) as ledger:
                        error(lambda: ledger.execute(raw), 'RESOURCE_LIMIT', state)
                cli_error(case, state, *command)
                assert h.file_digest(db) == before
                # Continue the same request/query after correcting the file.
                (case/'input.json').write_bytes(f.wire(value))
            exit_code, response = h.cli(case, *command)
            assert exit_code == 0, response
            with public.open(db) as ledger:
                assert f.wire(ledger.get_operation('demo:scope','create_artifact','create')) == f.wire(prior_operation)
                if kind == 'operation':
                    assert f.wire(response) == f.wire(dict(status='OK',commit_state='not_applicable',data=prior_operation))
                    assert h.file_digest(db) == before
                else:
                    operation = cross.operation_for(request, response['result_ref'], response['recorded_at'])
                    cross.assert_response(response, operation, False)
                    assert f.wire(ledger.get_operation('demo:scope',request['operation_kind'],request['idempotency_key'])['idempotency_record']) == f.wire(operation)
                    kwargs = {'payloads': {'blob:one': b''}} if kind == 'payload_map' else {}
                    cross.assert_response(ledger.execute(f.wire(request), **kwargs), operation, True)
                    if kind == 'payload_map':
                        assert ledger.read_blob('blob:one') == b''
                    ledger.verify()
            results.append(dict(axis=kind, bytes=size, status='PASS'))
    return results


def structural(root):
    db = root/'structural.sqlite'
    results = []
    with public.initialize(db) as ledger:
        ledger.execute(f.wire(f.create()))
        before = h.file_digest(db)
        for kind, limits in (('depth',(15,16,17)),('nodes',(499999,500000,500001))):
            for limit in limits:
                raw = b'['*limit+b'0'+b']'*limit if kind == 'depth' else b'['+b'0,'*(limit-2)+b'0]'
                code = 'RESOURCE_LIMIT' if limit == limits[-1] else 'INVALID_INPUT'
                error(lambda: ledger.execute(raw), code, 'not_committed')
                path = root/(kind+'-'+str(limit)+'.json')
                path.write_bytes(raw)
                cli_error(root, 'not_committed', 'write', '--db', db, '--request', path, code=code)
                assert h.file_digest(db) == before
                results.append(dict(axis=kind, measured=limit, expected_code=code, status='PASS'))
        # Limits below/equal accept JSON allocation, not the invalid array request shape.
        value = f.create('artifact:after-structural','after')
        assert ledger.execute(f.wire(value))['replayed'] is False
    return results


def refs_for_bytes(added):
    count, remainder = divmod(added+1,515)
    refs = [('ref:'+str(i).zfill(8)+':'+'x'*499) for i in range(count)]
    if remainder:
        if remainder < 4:
            refs[-1] = refs[-1][:-(4-remainder)]
            remainder = 4
        refs.append('q'*(remainder-3))
    assert all(1 <= len(ref) <= 512 for ref in refs)
    assert len(f.wire(refs))-2 == added
    return refs


def responses(root):
    results = []
    for size in (LIMIT-1,LIMIT,LIMIT+1):
        case = root/('response-'+str(size))
        case.mkdir()
        db = case/'ledger.sqlite'
        with public.initialize(db) as ledger:
            request = f.create()
            created = ledger.execute(f.wire(request))
            artifact = dict(request['body']['artifact'],recorded_at=created['recorded_at'])
            first = f.append(raw=b'')
            first['body']['version']['external_source_refs'] = refs_for_bytes(4*1048576)
            committed = ledger.execute(f.wire(first),payloads={'blob:one':b''})
            first_record = dict(first['body']['version'],recorded_at=committed['recorded_at'])
            second = f.append('version:two',raw=b'',parents=['version:one'],key='second')
            second['body'].update(content_roots=[],blobs=[])
            future = dict(second['body']['version'],recorded_at=committed['recorded_at'])
            history = dict(artifact=artifact,versions=[first_record,future],provenance_links=[])
            envelope = dict(status='OK',commit_state='not_applicable',data=history)
            future['external_source_refs'] = []
            refs = refs_for_bytes(size-len(f.wire(envelope))-1)
            second['body']['version']['external_source_refs'] = refs
            committed = ledger.execute(f.wire(second),payloads={})
            future.update(external_source_refs=refs,recorded_at=committed['recorded_at'])
            assert len(f.wire(envelope))+1 == size
            before = h.file_digest(db)
            if size <= LIMIT:
                assert f.wire(ledger.get_history('artifact:report')) == f.wire(history)
                exit_code, response = h.cli(case,'history','--db',db,'--artifact-id','artifact:report')
                assert exit_code == 0 and f.wire(response) == f.wire(envelope)
            else:
                error(lambda: ledger.get_history('artifact:report'), 'RESOURCE_LIMIT','not_applicable')
                cli_error(case,'not_applicable','history','--db',db,'--artifact-id','artifact:report')
            assert h.file_digest(db) == before
            operation = cross.operation_for(second,'version:two',committed['recorded_at'])
            cross.assert_response(ledger.execute(f.wire(second),payloads={}),operation,True)
            assert ledger.read_blob('blob:one') == b''
            assert ledger.verify()['counts']['versions'] == 2
            assert h.file_digest(db) == before
            (case/'expected-response.json').write_bytes(f.wire(envelope)+b'\n')
        results.append(dict(axis='response',bytes=size,status='PASS'))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--implementation',choices=('a','b'),required=True)
    parser.add_argument('--workdir',type=Path,required=True)
    args = parser.parse_args()
    assert sys.flags.isolated and not sys.flags.optimize
    module = Path(public.__file__).resolve().parent
    assert Path(sys.prefix).resolve() in module.parents and 'site-packages' in module.parts
    dist = 'infra-artifact-ledger'+('-b' if args.implementation == 'b' else '')
    other = 'infra-artifact-ledger'+('' if args.implementation == 'b' else '-b')
    version = importlib.metadata.version(dist)
    try:
        importlib.metadata.distribution(other)
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise AssertionError('A/B environments must be separate')
    args.workdir.mkdir(parents=True,exist_ok=False)
    started = time.monotonic()
    result = dict(implementation=args.implementation,distribution=dist,version=version,
        module=str(module),python=platform.python_version(),sqlite=sqlite3.sqlite_version,
        runtime_sha256={p.name:h.file_digest(p) for p in sorted(module.glob('*.py'))},checks=[])
    for function in (documents,structural,responses):
        result['checks'].extend(function(args.workdir))
        (args.workdir/'progress.json').write_text(json.dumps(result,indent=2)+'\n')
        print(function.__name__,'PASS',flush=True)
    result.update(status='PASS',elapsed_seconds=round(time.monotonic()-started,3),
        parent_max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        child_max_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    (args.workdir/'summary.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__ == '__main__':
    main()
