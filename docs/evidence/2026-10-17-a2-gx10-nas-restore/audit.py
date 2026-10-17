"""Offline stdlib audit of NAS-input restore evidence; no product execution.

Usage: python3 audit.py PRIVATE_ARCHIVE
Hard-link identities come from retained remote lstat and readback records;
local copied evidence files do not retain the original filesystem link count.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

MANIFEST = '022d0498cd03b59342da205d7e665b5ba0c3bfa420d536dc2dc1708e064fa91b'
DATABASE = 'e23882f3ab67468bce3709caecf1c3b68bad0dc47290ef57700bb717bced3504'
SOURCE = 'd4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b'
WHEEL = 'b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293'

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(root, name):
    return json.loads((root/name).read_text())

def database(path):
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        require(conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'SQLite integrity')
        require(not conn.execute('PRAGMA foreign_key_check').fetchall(), 'SQLite foreign keys')
        return {t:sorted(conn.execute('SELECT * FROM '+t).fetchall(),key=repr)
                for t in ('ledger_format','records','refs','operations','payloads')}
    finally:
        conn.close()

def audit(root):
    index = read(root,'readback-index.json')
    by_name = {x['name']:x for x in index['files']}
    require(len(by_name) == len(index['files']) == 219, 'Evidence file membership')
    require(sum(x['size'] for x in index['files']) == index['total_bytes'], 'Evidence total bytes')
    actual = {str(p.relative_to(root)) for base in ('run','snapshot') for p in (root/base).rglob('*')
              if p.is_file() and not p.is_symlink()}
    require(actual == set(by_name), 'Extra/missing evidence file')
    for name,item in by_name.items():
        path = root/name
        require(not Path(name).is_absolute() and '..' not in Path(name).parts and not path.is_symlink(), 'Unsafe evidence path')
        raw = path.read_bytes()
        require(len(raw) == item['size'] and sha(raw) == item['sha256'], 'Transport digest')
    links = {str(p.relative_to(root)):os.readlink(p) for p in (root/'run').rglob('*') if p.is_symlink()}
    require(len(index['links']) == len(links) == 4 and links == {x['name']:x['target'] for x in index['links']}, 'Link transport')
    require(set(links.values()) == {'missing-synthetic-target'}, 'Unsafe link target')
    plan, report, started = (read(root,'run/'+n+'.json') for n in ('plan','report','started'))
    require(plan['source_commit'] == SOURCE and plan['wheel_sha256'] == WHEEL, 'Source/wheel identity')
    require(started['runtime']['wheel_sha256'] == WHEEL and
            started['runtime']['installed_package_wheel_bytes'] == 'MATCH', 'Installed runtime identity')
    for name in ('checker','support'):
        require(sha((root/'run'/f'{name}.py').read_bytes()) == plan[name+'_sha256'], 'Executed script identity')
    require(report['status'] == 'PASS' and report['restore_count'] == 15 and report['check_fixture_count'] == 7, 'Report completion')
    require(all(report[k] for k in ('NAS_input_unchanged','mount_unchanged','runtime_unchanged')) and
            not report['NAS_written'] and not report['prior_business_replayed'], 'Scope assertions')
    config = read(root,'run/storage-config.json')
    mount = index['current_mount']['filesystems']
    require(len(mount) == 1 and mount[0]['fstype'] == started['mount']['fs_type'] == config['fs_type'] == 'cifs', 'CIFS identity')
    require(mount[0]['source'] == config['mount_source'] and mount[0]['target'] == config['mount_point'] and
            mount[0]['fsroot'] == config['mount_root'] and index['namespace'] == started['namespace'], 'Mount binding')
    require(started['local_mount']['fs_type'] == 'ext4' and Path(plan['snapshot']).parent == Path(config['archive_root']), 'Local/NAS scope')
    baseline = (root/'snapshot/ledger.sqlite').read_bytes()
    manifest = read(root,'snapshot/manifest.json');marker = read(root,'snapshot/COMMITTED.json')
    require(sha((root/'snapshot/manifest.json').read_bytes()) == MANIFEST == plan['manifest_sha256'] == marker['manifest_sha256'], 'Independent manifest reference')
    require(sha(baseline) == DATABASE == plan['database_sha256'] == manifest['database']['sha256'], 'Independent database reference')
    require(len(baseline) == manifest['database']['byte_length'] == 49152, 'Database length')
    require(manifest['snapshot_id'] == marker['snapshot_id'] == Path(plan['snapshot']).name, 'Snapshot identity')
    require(read(root,'run/source-before.json') == read(root,'run/source-after.json'), 'Original NAS metadata changed')
    for name,value in plan['snapshot_expected'].items():
        require({'size':(root/'snapshot'/name).stat().st_size,'sha256':sha((root/'snapshot'/name).read_bytes())} == value, 'Prior T15 bytes changed')
    source = database(root/'snapshot/ledger.sqlite')
    require({k:len(v) for k,v in source.items()} == {'ledger_format':1,'records':16,'refs':21,'operations':7,'payloads':3}, 'SQL row counts')

    def outcome(label, code, error=None, state='not_applicable', lost=False):
        result = read(root,'run/'+label+'-result.json')
        require(result['exit'] == code and not result['timed_out'], 'Process exit/timeout')
        stderr = (root/('run/'+label+'.stderr')).read_text()
        require(stderr == result['stderr'], 'Original stderr differs')
        if lost:
            require(code == 9 and 'response channel failed' in stderr and 'response' not in result and
                    not (root/('run/'+label+'.stdout')).exists(), 'Actual lost-response receipt')
            require(read(root,'run/'+label+'-request.json')['drop_stdout'], 'Response channel not dropped')
            return result
        require(stderr == '', 'Unexpected diagnostic')
        raw = (root/('run/'+label+'.stdout')).read_bytes()
        if code == -9:
            require(raw == b'' and result['response'] is None, 'Killed process has fabricated response')
            return result
        response = json.loads(raw)
        require(response == result['response'], 'Original stdout differs')
        env = response.get('envelope',response)
        require(env['status'] == ('ERROR' if error else 'OK') and env['publication_state'] == state, 'Semantic outcome')
        if error:
            require(env['error']['code'] == error, 'Semantic error')
        else:
            require(env['data']['summary'] == manifest['summary'], 'Verified summary differs')
        return result

    def check_inventory(prefix, record):
        target = root/prefix
        if record == {'absent':True}:
            require(not os.path.lexists(target), 'Absent target appeared')
            return
        entries = record['entries']
        names = {'.'} | {str(p.relative_to(target)) for p in target.rglob('*')}
        require(names == set(entries), 'Target members differ')
        for name,item in entries.items():
            p = target/name
            if item['kind'] == 'directory':
                require(p.is_dir() and not p.is_symlink(), 'Directory changed')
            elif item['kind'] == 'link':
                require(p.is_symlink() and os.readlink(p) == item['target'], 'Sidecar link changed')
            else:
                require(p.is_file() and not p.is_symlink(), 'File kind changed')
                require(p.stat().st_size == item['size'] and sha(p.read_bytes()) == item['sha256'], 'Target contents changed')
                remote = by_name[str(p.relative_to(root))]
                require(all(remote[k] == item[k] for k in ('dev','ino','nlink')), 'Retained remote inode/link metadata differs')

    unpublished = {'nas_read','partial_copy','verify_corrupt','staged_fsync','link_enospc','link_eacces'}
    unknown = {'link_eio_before','link_eio_after','cleanup','target_sync','parent_sync'}
    killed = {'kill_before','kill_after'}
    complete = {'success_order','link_eio_after','cleanup','target_sync','parent_sync','kill_after','response_loss'}
    modes = unpublished | unknown | killed | {'success_order','response_loss'}
    require(set(report['restores']) == modes, 'Restore case set')
    observed = {}
    for mode in sorted(modes):
        error = 'PUBLICATION_UNKNOWN' if mode in unknown else 'INTEGRITY_FAILURE' if mode == 'verify_corrupt' else 'IO_ERROR' if mode in unpublished else None
        state = 'unknown' if mode in unknown else 'not_published' if mode in unpublished else 'published'
        code = -9 if mode in killed else 6 if mode == 'verify_corrupt' else 0 if mode == 'success_order' else 9
        result = outcome(mode,code,error,state,mode == 'response_loss')
        checked = outcome(mode+'-check',0 if mode in complete else 5,None if mode in complete else 'NOT_FOUND')
        argv = read(root,'run/'+mode+'-check-request.json')['argv']
        require(argv[argv.index('--target-dir')+1] == plan['run_dir']+'/targets/'+mode and
                argv[argv.index('--expected-database-sha256')+1] == DATABASE, 'Check input binding')
        item = report['restores'][mode]
        require(item == read(root,'run/'+mode+'-checked.json') and item['restore'] == result and item['check'] == checked, 'Report/raw receipt mismatch')
        require(item['before'] == item['after'], 'Readonly target changed')
        prefix = 'run/targets/'+mode
        check_inventory(prefix,item['before'])
        job = read(root,'run/'+mode+'-job.json')['arguments']
        require(job['snapshot'] == plan['snapshot'] and job['expected_manifest_sha256'] == MANIFEST and
                job['storage_config'] == config and job['target_dir'] == plan['run_dir']+'/targets/'+mode, 'Restore input binding')
        entries = item['before'].get('entries',{})
        files = {n:v for n,v in entries.items() if v['kind'] == 'file'}
        has_temp = mode in {'cleanup','kill_before','kill_after'}
        require(('ledger.sqlite' in files) == (mode in complete), 'Public database presence')
        require(len(files) == int(mode in complete)+int(has_temp), 'Unexpected remaining database')
        require(len(entries) == (0 if mode == 'nas_read' else 1+int(mode in complete)+2*int(has_temp)), 'Unexpected target remnants')
        for n,v in files.items():
            p = root/prefix/n
            require(p.read_bytes() == baseline and database(p) == source, 'Restored bytes/SQL/payloads differ')
            require(v['nlink'] == (2 if mode in {'cleanup','kill_after'} else 1), 'Original hard-link count')
        if mode in {'cleanup','kill_after'}:
            require(len({(v['dev'],v['ino']) for v in files.values()}) == 1, 'Two names are not the same original inode')
        scratch = root/'run/scratch'/mode
        scratch_files = list(scratch.rglob('ledger.sqlite'))
        require(len(scratch_files) == int(mode in killed), 'Unexpected scratch remnant')
        for p in scratch_files:
            require(p.read_bytes() == baseline, 'Interrupted scratch bytes differ')
        if mode != 'response_loss':
            hooks = result['response']['hooks'] if mode not in killed else read(root,'run/'+mode+'.ready')['hooks']
            require(hooks['fault_triggered'] == (mode != 'success_order') and hooks['link_completed'] == (mode in complete), 'Actual fault/link phase')
            require(hooks['final_sync_members'] == ([['ledger.sqlite']] if mode in {'success_order','target_sync','parent_sync'} else []), 'Cleanup-before-sync ordering')
            event = hooks.get('event',{})
            if mode in killed:
                ready = read(root,'run/'+mode+'.ready')
                require(ready['pid'] == result['pid'] and ready['uid'] == 1000, 'Killed process identity')
                require(event == {'kind':'crash_checkpoint','phase':'before_actual_link' if mode == 'kill_before' else 'after_actual_link'}, 'Kill checkpoint')
            elif mode == 'verify_corrupt':
                require(event == {'kind':'staged_byte_change','offset':0,'before':baseline[0],'after':baseline[0]^1}, 'Staged corruption evidence')
            elif mode != 'success_order':
                expected_errno = 28 if mode in {'partial_copy','link_enospc'} else 13 if mode in {'link_eacces','cleanup'} else 5
                require(event['errno'] == expected_errno, 'Injected errno differs')
                if mode == 'partial_copy':require(event['written'] == 128, 'Actual copied prefix')
                if mode == 'nas_read':require(event['actual_bytes_read'] == len(baseline), 'Actual NAS read length')
        observed[mode] = {'exit':code,'state':None if mode in killed or mode == 'response_loss' else state,
                          'current_check':'OK' if mode in complete else 'NOT_FOUND'}

    fixtures = {s+'_'+k for s in ('journal','wal','shm') for k in ('file','link')} | {'unrelated_link'}
    require(set(report['check_fixtures']) == fixtures, 'Sidecar fixture set')
    for mode in sorted(fixtures):
        good = mode == 'unrelated_link'
        checked = outcome(mode+'-check',0 if good else 6,None if good else 'INTEGRITY_FAILURE')
        argv = read(root,'run/'+mode+'-check-request.json')['argv']
        require(argv[argv.index('--target-dir')+1] == plan['run_dir']+'/check-fixtures/'+mode and
                argv[argv.index('--expected-database-sha256')+1] == DATABASE, 'Sidecar check input binding')
        item = report['check_fixtures'][mode]
        require(item == read(root,'run/'+mode+'-checked.json') and item['check'] == checked and item['before'] == item['after'], 'Sidecar check changed original')
        prefix = 'run/check-fixtures/'+mode
        check_inventory(prefix,item['before'])
        suffix,kind = mode.split('_')
        sidecar = 'unrelated' if good else 'ledger.sqlite-'+suffix
        require(set(item['before']['entries']) == {'.','ledger.sqlite',sidecar}, 'Sidecar fixture members')
        require((root/prefix/'ledger.sqlite').read_bytes() == baseline, 'Sidecar fixture DB changed')
        if kind == 'link':
            require(os.readlink(root/prefix/sidecar) == 'missing-synthetic-target' and not (root/prefix/sidecar).exists(), 'Expected dangling link')
        else:
            require((root/prefix/sidecar).read_bytes() == b'synthetic sidecar sentinel\n', 'Sidecar sentinel changed')
    return {'status':'PASS','files_verified':len(by_name),'bytes_verified':index['total_bytes'],'symlinks_preserved':4,
            'restore_cases':observed,'check_fixtures':7,'restore_current_OK':7,'restore_current_NOT_FOUND':8,
            'unknown_current_complete':4,'unknown_current_absent':1,'original_NAS_unchanged':True,
            'double_link_targets_preserved':2,'target_temp_directories_preserved':3,'scratch_databases_preserved':2,
            'source_commit':SOURCE,'wheel_sha256':WHEEL,'report_sha256':sha((root/'run/report.json').read_bytes()),
            'a2_overall':'PARTIAL','no_historical_sync_or_cleanup_claim':True}

if __name__ == '__main__':
    print(json.dumps(audit(Path(sys.argv[1])),indent=2,sort_keys=True))
