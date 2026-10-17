"""Offline, read-only audit of the private GX10/CIFS fault evidence archive.

Usage: python3 audit.py PRIVATE_ARCHIVE
Uses only stdlib, never imports Ledger, connects to NAS, or repeats operations.
Transport hashes alone are insufficient: compare independent inputs, complete
SQLite rows/payloads, partial prefixes, process results and namespace bindings.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(root, name):
    return json.loads((root / name).read_text())


def files(path):
    return {str(p.relative_to(path)): {'size': p.stat().st_size, 'sha256': sha(p.read_bytes())}
            for p in sorted(path.rglob('*')) if p.is_file()}


def database(path):
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        require(conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'SQLite integrity')
        require(not conn.execute('PRAGMA foreign_key_check').fetchall(), 'SQLite foreign keys')
        return {name: sorted(conn.execute('SELECT * FROM ' + name).fetchall(), key=repr)
                for name in ('ledger_format', 'records', 'refs', 'operations', 'payloads')}
    finally:
        conn.close()


def audit(root):
    index = read(root, 'readback-index.json')
    names = [f['name'] for f in index['files']]
    require(len(names) == len(set(names)) == 138, 'Evidence membership count')
    actual = {str(p.relative_to(root)) for base in ('run', 'nas', 'preserved') for p in (root/base).rglob('*') if p.is_file()}
    require(actual == set(names), 'Evidence membership differs')
    for item in index['files']:
        path = root/item['name']
        require(path.is_relative_to(root) and '..' not in path.parts and not path.is_symlink(), 'Unsafe evidence path')
        raw = path.read_bytes()
        require(len(raw) == item['size'] and sha(raw) == item['sha256'], 'Transport hash mismatch')
    plan = read(root, 'run/plan.json')
    repair = read(root, 'run/repair-plan.json')
    require(sha((root/'run/checker.py').read_bytes()) == plan['checker_sha256'], 'Executed checker binding')
    require(sha((root/'run/namespace_check.py').read_bytes()) == plan['namespace_checker_sha256'], 'Initial supervisor binding')
    require(sha((root/'run/checker-v2.py').read_bytes()) == repair['checker_sha256'], 'Fixed checker binding')
    require(sha((root/'run/namespace-reconcile.py').read_bytes()) == repair['namespace_checker_sha256'], 'Continuation binding')
    require(plan['source_commit'] == 'd4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b', 'Fixed source')
    require(plan['wheel_sha256'] == 'b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293', 'Fixed wheel')
    started = read(root, 'run/started.json')
    require(started['runtime']['installed_package_wheel_bytes'] == 'MATCH' and started['runtime']['wheel_sha256'] == plan['wheel_sha256'], 'Runtime binding')
    sid = plan['snapshot_id']
    refs = read(root, 'run/references-before-publish.json')
    ordinary = read(root, 'run/ordinary-report.json')
    final = read(root, 'run/final-report.json')
    require(ordinary['status'] == final['status'] == 'PASS', 'Execution completion')

    def outcome(label, status, state, code=None):
        result = read(root, 'run/' + label + '-result.json')
        require(not result['timed_out'] and result['stderr'] == '', 'Command exit evidence')
        output = read(root, 'run/' + label + '.stdout')
        require(result['response'] == output, 'Retained stdout differs from result')
        require((root/('run/' + label + '.stderr')).read_bytes() == b'', 'Retained stderr differs')
        envelope = output.get('envelope', output)
        require(envelope['status'] == status and envelope['publication_state'] == state, 'Wrong semantic state')
        require(result['exit'] == (0 if code is None else {'TARGET_EXISTS':4, 'IO_ERROR':9,
                'PUBLICATION_UNKNOWN':9, 'INCOMPLETE_SNAPSHOT':5, 'UNSUPPORTED_STORAGE':3}[code]), 'Wrong exit')
        if code:
            require(envelope['error']['code'] == code, 'Wrong semantic error')
        return result

    sources = {'a': database(root/'run/source.sqlite'), 'b': database(root/'run/variant.sqlite')}
    require(sources['a'] != sources['b'], 'Contention candidates must differ')
    for candidate in ('a', 'b'):
        snap = root/'run'/('snapshots-' + candidate)/sid
        manifest_raw = (snap/'manifest.json').read_bytes()
        manifest = json.loads(manifest_raw)
        marker = read(snap, 'COMMITTED.json')
        require(manifest['snapshot_id'] == marker['snapshot_id'] == refs[candidate]['snapshot_id'] == sid, 'Snapshot ID')
        require(sha(manifest_raw) == refs[candidate]['manifest_sha256'] == marker['manifest_sha256'], 'Manifest reference')
        db = (snap/'ledger.sqlite').read_bytes()
        require(sha(db) == refs[candidate]['database_sha256'] == manifest['database']['sha256'], 'Database reference')
        require(len(db) == manifest['database']['byte_length'], 'Database size')
        require(database(snap/'ledger.sqlite') == sources[candidate], 'Snapshot rows/payloads differ')
    lost = read(root, 'run/response_loss-result.json')
    require(lost['exit'] == 9 and 'response channel failed' in lost['stderr'], 'Lost stdout result')
    require(read(root, 'run/response_loss-request.json')['drop_stdout'], 'Lost stdout injection')
    require((root/'run/response_loss.stderr').read_text() == lost['stderr'], 'Lost stderr differs')
    require(not (root/'run/response_loss.stdout').exists(), 'Unexpected recovered response')

    results = {c: read(root, 'run/contender-' + c + '-result.json') for c in ('a', 'b')}
    winners = [c for c,r in results.items() if r['exit'] == 0]
    require(len(winners) == 1, 'Must have exactly one winner')
    winner = winners[0]
    pids = []
    for candidate in ('a','b'):
        is_winner = candidate == winner
        result = outcome('contender-' + candidate, 'OK' if is_winner else 'ERROR',
                         'published' if is_winner else 'not_applicable', None if is_winner else 'TARGET_EXISTS')
        ready = read(root, 'run/contender-' + candidate + '.ready')
        require(ready['pid'] == result['pid'] and result['response']['hooks']['rendezvous_reached'], 'Rendezvous evidence')
        pids.append(ready['pid'])
    release = read(root, 'run/contenders.release')
    require(len(set(pids)) == 2 and release == {'both_ready':True,'pids':pids}, 'Independent competing processes')

    complete = {'response_loss':'a','contenders':winner,'final_sync':'a','link_reply_lost':'a','mount_after_marker':'a'}
    for case, candidate in complete.items():
        target = root/'nas'/case/sid
        require(files(target) == files(root/'run'/('snapshots-' + candidate)/sid), 'NAS candidate bytes differ')
        require(database(target/'ledger.sqlite') == sources[candidate], 'NAS rows/payloads differ')
        label = 'mount-reconcile' if case == 'mount_after_marker' else case + '-verify'
        outcome(label, 'OK', 'not_applicable')
    for case in ('final_sync', 'link_reply_lost', 'mount_after_marker'):
        result = outcome(case, 'ERROR', 'unknown', 'PUBLICATION_UNKNOWN')
        require(result['response']['hooks']['marker_link_completed'] and result['response']['hooks']['fault_triggered'], 'Post-marker fault missing')
    for case, member in [('partial_database','ledger.sqlite'),('partial_manifest','manifest.json')]:
        result = outcome(case, 'ERROR', 'not_published', 'IO_ERROR')
        count = result['response']['hooks']['partial_bytes_written']
        target = root/'nas'/case/sid
        require(count == 128 and not (target/'COMMITTED.json').exists(), 'Partial state')
        require((target/member).read_bytes() == (root/'run/snapshots-a'/sid/member).read_bytes()[:count], 'Partial bytes differ')
        require(not result['response']['hooks']['marker_link_completed'], 'Premature marker')
        outcome(case+'-verify', 'ERROR', 'not_applicable', 'INCOMPLETE_SNAPSHOT')
    outcome('incomplete', 'ERROR', 'not_applicable', 'TARGET_EXISTS')
    require(files(root/'nas/incomplete'/sid) == ordinary['cases']['incomplete']['before'], 'Old incomplete directory overwritten')

    old = read(root, 'run/namespace-report.json')
    resumed = read(root, 'run/namespace-completion-report.json')
    require(old['status'] == 'FAIL' and old['error_type'] == 'ModuleNotFoundError', 'Historical controller failure lost')
    require(resumed['status'] == 'PASS' and resumed['original_failure_sha256'] == sha((root/'run/namespace-report.json').read_bytes()), 'Failure reconciliation')
    ready, release = (read(root,'run/mount_after_marker.'+suffix) for suffix in ('ready','release'))
    require(ready['namespace'] == release['namespace'] == old['isolated_namespace'] != old['original_namespace'], 'Actual isolated loss checkpoint')
    require(ready['uid'] == 1000 and ready['marker_link_completed'] and release['mount_removed'], 'Actual marker then mount loss')
    require(resumed['isolated_namespace'] != resumed['original_namespace'] == started['namespace'] == index['namespace'], 'Resume isolation')
    for record in (old,resumed):
        require(record['private_unmount_completed'] and record['inherited_mount']['fstype'] == 'cifs'
                and record['inherited_mount']['propagation'] == record['replacement_mount']['propagation'] == 'private'
                and record['replacement_mount']['fstype'] == 'ext4', 'Mount loss isolation evidence')
    outcome('mount_absent', 'ERROR', 'not_published', 'UNSUPPORTED_STORAGE')
    require(not files(root/'run/shadow'), 'Fallback wrote local file')
    config = read(root,'run/mount_after_marker-storage.json')
    current = index['current_mount']['filesystems']
    require(len(current) == 1, 'Global mount count')
    for expected, actual in [('mount_point','target'),('mount_source','source'),('fs_type','fstype'),('mount_root','fsroot')]:
        require(config[expected] == current[0][actual], 'Global mount binding changed')
    require('vers=3.1.1' in current[0]['options'].split(','), 'SMB dialect')
    require(files(root/'preserved') == read(root,'run/preserved-before.json'), 'Previously successful generation changed')
    require(sha((root/'preserved/ledger.sqlite').read_bytes()) == 'e23882f3ab67468bce3709caecf1c3b68bad0dc47290ef57700bb717bced3504', 'Prior independent reference')
    require(sha((root/'preserved/manifest.json').read_bytes()) == '022d0498cd03b59342da205d7e665b5ba0c3bfa420d536dc2dc1708e064fa91b', 'Prior manifest reference')
    temporary = [p for p in (root/'nas/mount_after_marker').iterdir() if p.name.startswith('.snapshot-tmp-')]
    require(len(temporary) == 1 and set(files(temporary[0])) == {'COMMITTED.json'}, 'Preserved cleanup remainder')
    require((temporary[0]/'COMMITTED.json').read_bytes() == (root/'nas/mount_after_marker'/sid/'COMMITTED.json').read_bytes(), 'Remainder marker binding')
    return {'status':'PASS','files':len(names),'all_transport_hashes_match':True,
            'complete_nas_generations_checked':len(complete),'all_rows_and_payloads_match':True,
            'contention_winner':winner,'partial_files_checked':2,'old_generation_unchanged':True,
            'global_mount_unchanged':True,'no_local_fallback_files':True,
            'original_controller_failure_preserved':True,'marker_temporary_directory_retained':True,
            'audit_sha256':sha(Path(__file__).read_bytes()),
            'limitation':'Current bytes and retained execution evidence; not physical network loss or historical final sync.'}


if __name__ == '__main__':
    print(json.dumps(audit(Path(sys.argv[1])), ensure_ascii=False, indent=2))
