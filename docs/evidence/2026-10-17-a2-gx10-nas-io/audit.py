"""Offline stdlib audit of the private GX10/CIFS I/O evidence, without replay.

Usage: python3 audit.py PRIVATE_ARCHIVE
Compare original process outputs and checkpoint identities, exact remaining and
corrupted bytes, SQL rows/payloads, and prior independently retained hashes.
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
    return json.loads((root/name).read_text())


def files(root):
    return {str(p.relative_to(root)):{'size':p.stat().st_size,'sha256':sha(p.read_bytes())}
            for p in sorted(root.rglob('*')) if p.is_file()}


def inventory(root):
    result=files(root)
    result.update({str(p.relative_to(root)):{'directory':True} for p in root.rglob('*') if p.is_dir()})
    return result


def database(path):
    conn=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        require(conn.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'SQLite integrity')
        require(not conn.execute('PRAGMA foreign_key_check').fetchall(),'SQLite foreign keys')
        return {t:sorted(conn.execute('SELECT * FROM '+t).fetchall(),key=repr)
                for t in ('ledger_format','records','refs','operations','payloads')}
    finally:
        conn.close()


def audit(root):
    index=read(root,'readback-index.json')
    names=[x['name'] for x in index['files']]
    require(len(names)==len(set(names))==278,'Evidence membership count')
    require(sum(x['size'] for x in index['files'])==index['total_bytes'],'Evidence total size')
    actual={str(p.relative_to(root)) for base in ('run','nas','preserved-t15','preserved-faults')
            for p in (root/base).rglob('*') if p.is_file()}
    require(actual==set(names),'Evidence membership differs')
    for item in index['files']:
        p=root/item['name']
        require(p.is_relative_to(root) and '..' not in p.parts and not p.is_symlink(),'Unsafe evidence path')
        raw=p.read_bytes()
        require(len(raw)==item['size'] and sha(raw)==item['sha256'],'Transport hash mismatch')
    plan=read(root,'run/plan.json')
    report=read(root,'run/report.json')
    started=read(root,'run/started.json')
    ref=read(root,'run/reference-before-publish.json')
    sid=plan['snapshot_id']
    require(plan['source_commit']=='d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b','Source identity')
    require(plan['wheel_sha256']=='b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293','Wheel identity')
    require(started['runtime']['wheel_sha256']==plan['wheel_sha256'] and
            started['runtime']['installed_package_wheel_bytes']=='MATCH','Installation identity')
    for name,key in [('checker.py','checker_sha256'),('support.py','support_sha256')]:
        require(sha((root/'run'/name).read_bytes())==plan[key],'Executed script identity')
    require(report['status']=='PASS' and report['completed_cases']==18,'Execution completion')
    snapshot=root/'run/snapshots'/sid
    baseline={p.name:p.read_bytes() for p in snapshot.iterdir()}
    require(set(baseline)=={'ledger.sqlite','manifest.json','COMMITTED.json'},'Local snapshot membership')
    manifest=json.loads(baseline['manifest.json']);marker=json.loads(baseline['COMMITTED.json'])
    require(sid==ref['snapshot_id']==manifest['snapshot_id']==marker['snapshot_id'],'Snapshot identity')
    require(sha(baseline['manifest.json'])==ref['manifest_sha256']==marker['manifest_sha256'],'Pre-call manifest reference')
    require(sha(baseline['ledger.sqlite'])==ref['database_sha256']==manifest['database']['sha256'],'Database reference')
    require(len(baseline['ledger.sqlite'])==manifest['database']['byte_length'],'Database size')
    source=database(root/'run/source.sqlite')
    require(database(snapshot/'ledger.sqlite')==source,'Source/snapshot rows or payloads differ')

    incomplete={'zero_write','enospc_db','enospc_manifest','enospc_marker','edquot_db','enospc_db_sync',
                'eacces_db_open','eacces_marker_link','read_db_before','read_manifest_before','kill_before_marker'}
    corrupt={'corrupt_db_after','corrupt_manifest_after'}
    complete={'short_write','read_db_after','read_manifest_after','read_marker_after','kill_after_marker'}
    modes=incomplete|corrupt|complete
    require(set(report['cases'])==modes=={p.name for p in (root/'nas').iterdir()},'Scenario membership')
    unknown={'eacces_marker_link','read_db_after','read_manifest_after','read_marker_after'}|corrupt
    observed={}
    def outcome(label, code, state, error=None):
        result=read(root,'run/'+label+'-result.json')
        require(result['exit']==code and not result['timed_out'] and result['stderr']=='','Process exit/diagnostic')
        require((root/('run/'+label+'.stderr')).read_bytes()==b'','Raw stderr differs')
        raw=(root/('run/'+label+'.stdout')).read_bytes()
        if code==-9:
            require(raw==b'' and result['response'] is None,'Killed publisher must have no response')
            return result
        output=json.loads(raw)
        require(output==result['response'],'Raw response differs')
        envelope=output.get('envelope',output)
        require(envelope['status']==('ERROR' if error else 'OK') and envelope['publication_state']==state,'Semantic outcome')
        if error:
            require(envelope['error']['code']==error,'Semantic error')
        return result

    for mode in sorted(modes):
        killed=mode.startswith('kill_')
        error=None if mode=='short_write' or killed else 'PUBLICATION_UNKNOWN' if mode in unknown else 'IO_ERROR'
        state=None if killed else 'published' if mode=='short_write' else 'unknown' if mode in unknown else 'not_published'
        result=outcome(mode,-9 if killed else 0 if not error else 9,state,error)
        verify_error='INCOMPLETE_SNAPSHOT' if mode in incomplete else 'INTEGRITY_FAILURE' if mode in corrupt else None
        verified=outcome(mode+'-verify',5 if mode in incomplete else 6 if mode in corrupt else 0,'not_applicable',verify_error)
        require(result==report['cases'][mode]['publish'] and verified==report['cases'][mode]['verify'],'Report/command mismatch')
        require(read(root,'run/'+mode+'-checked.json')==report['cases'][mode],'Per-case completion record')
        case=root/'nas'/mode;generation=case/sid
        require(generation.is_dir(),'Expected exclusive generation directory')
        require(inventory(case)==report['cases'][mode]['before_verify'],'Readonly verification changed NAS bytes')
        hooks=result['response']['hooks'] if not killed else read(root,'run/'+mode+'.ready')['hooks']
        require(hooks['fault_triggered'],'Fault checkpoint missing')
        require(hooks['marker_link_completed']==(mode in complete|corrupt),'Actual marker-link phase')
        content={p.name:p.read_bytes() for p in generation.iterdir()}
        expected=dict(baseline)
        if mode in incomplete:
            expected.pop('COMMITTED.json')
        if mode=='eacces_db_open':
            expected={}
        elif mode in {'enospc_db','edquot_db','enospc_db_sync','zero_write'}:
            expected={'ledger.sqlite':baseline['ledger.sqlite'][:128 if mode=='zero_write' else
                       len(baseline['ledger.sqlite']) if mode=='enospc_db_sync' else 0]}
        elif mode=='enospc_manifest':
            expected['manifest.json']=baseline['manifest.json'][:128]
        elif mode in corrupt:
            event=hooks['event']
            member='ledger.sqlite' if mode=='corrupt_db_after' else 'manifest.json'
            offset=len(baseline[member])-1 if member=='ledger.sqlite' else 0
            require(event['kind']=='persistent_byte_change' and event['member']==member and event['offset']==offset,
                    'Corruption injection position')
            require(event['before']==baseline[member][offset] and event['after']==(event['before']^1),'Corruption injection value')
            changed=bytearray(expected[member]);changed[offset]^=1;expected[member]=bytes(changed)
        require(content==expected,'Exact remaining NAS bytes differ')
        leftovers=[p for p in case.iterdir() if p.name!=sid]
        require(len(leftovers)==(1 if killed else 0),'Unexpected NAS remainder')
        if killed:
            ready=read(root,'run/'+mode+'.ready')
            require(ready['pid']==result['pid'] and ready['uid']==1000,'Killed process identity')
            phase='before_actual_marker_link' if mode=='kill_before_marker' else 'after_actual_marker_link'
            require(hooks['event']=={'kind':'crash_checkpoint','phase':phase},'Killed process phase')
            require(leftovers[0].name.startswith('.snapshot-tmp-') and
                    {p.name:p.read_bytes() for p in leftovers[0].iterdir()}=={'COMMITTED.json':baseline['COMMITTED.json']},'Crash marker remainder')
        elif mode=='short_write':
            for member,raw in baseline.items():
                counters=hooks['writes'][member]
                require(counters=={'bytes':len(raw),'calls':(len(raw)+96)//97,'short_returns':(len(raw)-1)//97},
                        'Real short-write continuation count')
        elif mode.startswith(('enospc_','edquot_','eacces_')):
            event=hooks['event']
            number=28 if mode.startswith('enospc_') else 122 if mode.startswith('edquot_') else 13
            require(event['errno']==number,'Injected errno differs')
            kind='fsync_error' if mode=='enospc_db_sync' else 'open_error' if mode=='eacces_db_open' else 'link_error' if mode=='eacces_marker_link' else 'write_error'
            require(event['kind']==kind,'Fault syscall boundary differs')
            if mode=='enospc_marker':
                require(event['bytes_written']==32 and hooks['writes']['COMMITTED.json']['bytes']==32,'Partial marker-write record')
        elif mode.startswith('read_'):
            event=hooks['event'];member={'db':'ledger.sqlite','manifest':'manifest.json','marker':'COMMITTED.json'}[mode.split('_')[1]]
            require(event=={'actual_bytes_read':len(baseline[member]),'errno':5,'kind':'read_error_after_real_read','member':member},'Actual read boundary')
        elif mode=='zero_write':
            require(hooks['event']=={'bytes_written':128,'errno':None,'kind':'zero_write','member':'ledger.sqlite'},'Zero-write boundary')
        if mode in complete:
            require(database(generation/'ledger.sqlite')==source,'NAS full rows or payloads differ')
        observed[mode]={'publisher_exit':result['exit'],'publication_state':state,'error':error,
                        'verify':verify_error or 'OK','exact_files_checked':True}

    scratch=list((root/'run/scratch').glob('.snapshot-tmp-*'))
    require(len(scratch)==2 and all({p.name:p.read_bytes() for p in d.iterdir()}=={'ledger.sqlite':baseline['ledger.sqlite']} for d in scratch),
            'Retained crash scratch copies')
    before=read(root,'run/preserved-before.json')
    for name in ('t15','faults'):
        require(files(root/('preserved-'+name))==plan['preserved_expected'][name],'Prior independent evidence changed')
        require(inventory(root/('preserved-'+name))==before[name],'Prior evidence changed during run')
    mount=index['current_mount']['filesystems'];config=read(root,'run/short_write-storage.json')
    require(len(mount)==1,'Mount membership')
    for configured,actual in [('mount_point','target'),('mount_source','source'),('mount_root','fsroot'),('fs_type','fstype')]:
        require(config[configured]==mount[0][actual],'Global mount binding differs')
    require(mount[0]['fstype']=='cifs' and 'vers=3.1.1' in mount[0]['options'].split(','),'Actual CIFS dialect')
    return {'status':'PASS','readback_files':len(names),'bytes':index['total_bytes'],'scenarios':observed,
            'complete_NAS_generations_with_all_rows_and_payloads':len(complete),
            'two_precisely_corrupted_generations_rejected':True,'old_T15_and_fault_evidence_unchanged':True,
            'retained_NAS_marker_temporary_directories':2,'retained_local_scratch_databases':2,
            'global_mount_unchanged':True,'audit_sha256':sha(Path(__file__).read_bytes()),
            'limitations':'Controlled syscall errors and actual process kills; not actual NAS disk-full, server ACL/quota enforcement, network/power failure, or historical final-sync proof.'}


if __name__=='__main__':
    print(json.dumps(audit(Path(sys.argv[1])),ensure_ascii=False,indent=2))
