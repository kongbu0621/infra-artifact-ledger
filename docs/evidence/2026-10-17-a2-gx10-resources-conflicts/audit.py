"""Offline stdlib audit of GX10 resource measurements and NAS-input conflicts.

Large fixtures are retained on GX10, not included in the portable readback.
Their separate stdlib inspection binds complete bytes and SQL/payload equality;
this audit validates its boundary observations, receipts, and retained manifest.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

SOURCE='d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b'
WHEEL='b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293'
REFERENCES={
 'A':('022d0498cd03b59342da205d7e665b5ba0c3bfa420d536dc2dc1708e064fa91b','e23882f3ab67468bce3709caecf1c3b68bad0dc47290ef57700bb717bced3504'),
 'B':('44a32fa97c31ab38c03235d442ef552327203dc5cb8b6e45f6bff3d55ccbcc45','b34700105f8864f31d2b70483919172f97d9a8840bbdc1830463e737445434b9')}

def require(value,message):
    if not value:raise RuntimeError(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(root,name):return json.loads((root/name).read_text())
def database(path):
    c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        require(c.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'SQLite integrity')
        require(not c.execute('PRAGMA foreign_key_check').fetchall(),'Foreign keys')
        return {t:sorted(c.execute('SELECT * FROM '+t).fetchall(),key=repr) for t in ('ledger_format','records','refs','operations','payloads')}
    finally:c.close()

def audit(root):
    index=read(root,'readback-index.json');by_name={v['name']:v for v in index['files']}
    require(len(by_name)==len(index['files'])==187,'Portable evidence file count')
    require(sum(v['size'] for v in by_name.values())==index['total_bytes'],'Transport total')
    actual={str(p.relative_to(root)) for prefix in ('run','input-A','input-B') for p in (root/prefix).rglob('*') if p.is_file() and not p.is_symlink()}
    require(actual==set(by_name),'Portable file membership')
    for name,v in by_name.items():
        path=root/name
        require(not Path(name).is_absolute() and '..' not in Path(name).parts and not path.is_symlink(),'Unsafe file path')
        raw=path.read_bytes();require(len(raw)==v['size'] and sha(raw)==v['sha256'],'Transport digest')
    require(index['links']==[{'name':'run/conflicts/existing/dangling','target':'missing-synthetic-target'}],'Link index')
    require(os.readlink(root/'run/conflicts/existing/dangling')=='missing-synthetic-target','Original dangling target')
    plan=read(root,'run/plan.json');started=read(root,'run/resources-started.json');report=read(root,'run/resources-report.json')
    require(plan['source_commit']==SOURCE and plan['wheel_sha256']==WHEEL,'Fixed implementation identity')
    require(started['runtime']['wheel_sha256']==WHEEL and started['runtime']['installed_package_wheel_bytes']=='MATCH','Installed package identity')
    require(started['mount']['fs_type']=='ext4' and report['runtime_unchanged'] and not report['NAS_operations'],'Actual local runtime scope')
    for name in ('resources','conflicts','support'):
        require(sha((root/'run'/f'{name}.py').read_bytes())==plan[name+'_sha256'],'Executed worker identity')
    for name,expected in plan['test_sources'].items():require(sha((root/'run/tests'/name).read_bytes())==expected,'Original test identity')
    inspection=read(root,'run/independent-resource-inspection.json')
    require(inspection['status']=='PASS' and not inspection['product_imports'] and inspection['database_objects_inspected']==38,'Independent inspection')
    fixture_index={v['name']:v for v in inspection['files']}
    require(len(fixture_index)==len(inspection['files'])==122 and sum(v['size'] for v in fixture_index.values())==inspection['total_bytes'],'Remote resource membership')
    omitted={v['name']:v for v in index['omitted_large_fixtures']}
    require(len(omitted)==len(index['omitted_large_fixtures'])==61,'Retained fixture membership')
    for name,v in omitted.items():
        require(name.startswith('run/resources/') and '/fixtures/' in name and v['retained_on_GX10'],'Unexpected omitted evidence')
        original=fixture_index[name.removeprefix('run/')]
        require(all(v[k]==original[k] for k in ('size','sha256')),'Retained fixture digest differs')
    for name,v in fixture_index.items():
        if 'run/'+name not in omitted:
            require(all(by_name['run/'+name][k]==v[k] for k in ('size','sha256')),'Inspection/control bytes differ')
    require(report['status']=='PASS' and set(report['cases'])==set(plan['resource_cases'])==set(inspection['cases']),'Resource completion set')
    metrics={};total_calls=0;valid=0;rejected=0
    boundaries={'pages':('size',[1073737728,1073741824,1073750016]),
                'metadata_bytes':('metadata_bytes',[16777215,16777216,16777217]),
                'metadata_rows':('metadata_rows',[49999,50000,50001]),
                'reference_rows':('refs_rows',[249999,250000,250001]),
                'reference_bytes':('refs_bytes',[33554431,33554432,33554433])}
    for case in plan['resource_cases']:
        base='run/resources/'+case
        receipt=read(root,base+'/receipt.json');measurement=read(root,'run/'+case+'-time.json');result=read(root,'run/'+case+'-result.json')
        require(receipt['status']=='PASS' and receipt['tests_run']==1 and receipt['skipped']==receipt['errors']==receipt['failures']==0,'Resource unittest outcome')
        require(result['exit']==measurement['exit']==0 and not result['timed_out'],'Measured process exit')
        require(0<measurement['peak_rss_kib']<=8*1024**2 and 0<measurement['wall_seconds']<900,'Measurement range')
        require(measurement['peak_rss_kib']>=receipt['process_peak_rss_kib'],'GNU time/worker RSS consistency')
        require(report['cases'][case]==read(root,'run/'+case+'-completed.json')=={'process':result,'measurement':measurement,'receipt':receipt},'Measurement receipt binding')
        require('Ran 1 test' in (root/('run/'+case+'.stderr')).read_text() and (root/('run/'+case+'.stderr')).read_text().rstrip().endswith('OK'),'Original unittest stderr')
        module=plan['resource_cases'][case].split('.')[0]+'.py';original=(root/'run/tests'/module).read_text()
        if case in ('pages','payload'):
            transformed=original.replace('with mock.patch.object(storage, "_mounts", return_value=(synthetic,)):','with deployed_filesystem(self.root):')
        else:
            transformed=original.replace('        classification = patch.object(storage, "_classify", return_value=None)\n        classification.start()\n        self.addCleanup(classification.stop)','        assert_deployed(self.root)')
        transformed=transformed.replace('"LOGIC_ONLY"','"GX10_INSTALLED_EXT4"')
        require((root/base/'executed-test.py').read_text()==transformed and 'patch.object(' not in transformed,'Remaining classification mock or extra test alteration')
        require(receipt['executed_test_sha256']==sha(transformed.encode()) and receipt['source_test_sha256']==plan['test_sources'][module],'Adapted test binding')
        require(receipt['bindings'] and all(b['mount']['fs_type']=='ext4' for b in receipt['bindings']),'Real ext4 check missing')
        calls=[read(root,str(p.relative_to(root))) for p in sorted((root/base).glob('api-*.json'))]
        require(len(calls)==receipt['api_calls']==(4 if case=='payload' else 9),'Actual API call count')
        total_calls+=len(calls)
        points=inspection['cases'][case]
        if case=='payload':
            require(len(points)==1 and points[0]['source_info']['payload_bytes']==402653185 and points[0]['source_info']['size']==403103744,'Real large-payload boundary')
        else:
            field,values=boundaries[case]
            require([v['source_info'][field] for v in points]==values,'Actual legal limit-minus/equal/plus observations')
        creates=[c for c in calls if c['operation']=='create']
        require(len(creates)==len(points),'Create/inspection membership')
        for number,(point,call) in enumerate(zip(points,creates)):
            expected='OK' if case=='payload' or number<2 else 'ERROR'
            require(point['status']==call['envelope']['status']==expected,'Boundary outcome')
            require(call['arguments']['db']==plan['run_dir']+'/'+point['source'],'Source fixture binding')
            src=point['source_info'];f=fixture_index[point['source']]
            require(all(src[k]==f[k] for k in ('size','sha256')),'Independent source manifest')
            if expected=='ERROR':
                require(point['error']==call['envelope']['error']['code']=='RESOURCE_LIMIT' and call['envelope']['publication_state']=='not_published','Limit rejection semantics')
                rejected+=1
            else:
                valid+=1;data=call['envelope']['data']
                require(point['full_tables_and_payloads_equal'] and point['database_sha256']==data['database_sha256'],'Full state inspection')
                for key in ('snapshot','target'):
                    require(fixture_index[point[key]+'/ledger.sqlite']['sha256']==point['database_sha256'],'Retained restore/snapshot bytes')
        metrics[case]={'wall_seconds':measurement['wall_seconds'],'peak_rss_kib':measurement['peak_rss_kib']}
    require(total_calls==49 and valid==11 and rejected==5,'Resource coverage totals')

    conflict=read(root,'run/conflicts-report.json');cstart=read(root,'run/conflicts-started.json')
    require(conflict['status']=='PASS' and conflict['existing_cases']==5 and conflict['races_completed']==2 and conflict['restore_calls']==9,'Conflict completion')
    require(conflict['NAS_inputs_unchanged'] and not conflict['NAS_written'] and cstart['inputs']==read(root,'run/conflicts-inputs-after.json'),'Original NAS preservation')
    require(cstart['local_mount']['fs_type']=='ext4' and all(v['fs_type']=='cifs' for v in cstart['mounts'].values()),'Conflict storage binding')
    for key,(manifest_hash,db_hash) in REFERENCES.items():
        p=root/('input-'+key)
        require(sha((p/'manifest.json').read_bytes())==manifest_hash and sha((p/'ledger.sqlite').read_bytes())==db_hash,'Independent NAS reference')
        config=read(root,'run/'+key+'-storage.json');mount=index['current_mount']['filesystems'][0]
        require(mount['fstype']=='cifs' and mount['source']==config['mount_source'] and mount['target']==config['mount_point'] and mount['fsroot']==config['mount_root'],'Current NAS mount')
        for name,expected in plan['inputs'][key]['files'].items():
            require({'size':(p/name).stat().st_size,'sha256':sha((p/name).read_bytes())}==expected,'NAS input bytes changed')
    def outcome(label,exit_code,error=None):
        r=read(root,'run/'+label+'-result.json')
        require(r['exit']==exit_code and not r['timed_out'] and r['stderr']==(root/('run/'+label+'.stderr')).read_text()=='','Conflict process exit/diagnostic')
        response=json.loads((root/('run/'+label+'.stdout')).read_text());require(response==r['response'],'Original process response')
        env=response.get('envelope',response)
        require(env['status']==('ERROR' if error else 'OK'),'Conflict result status')
        if error:require(env['error']['code']==error and env['publication_state']=='not_applicable','Reject-overwrite outcome')
        return r
    def unchanged(prefix,before,after):
        require(before==after,'Target modified during refusal/check')
        base=root/prefix
        names={'.'}|{str(p.relative_to(base)) for p in base.rglob('*')}
        require(names==set(before),'Target members changed')
        for name,v in before.items():
            p=base/name
            if v['kind']=='file':require(p.is_file() and p.stat().st_size==v['size'] and sha(p.read_bytes())==v['sha256'],'Target bytes changed')
            elif v['kind']=='link':require(p.is_symlink() and os.readlink(p)==v['target'],'Target link changed')
            else:require(p.is_dir(),'Target directory changed')
    require(set(conflict['existing'])=={'empty','nonempty','dangling','sidecar','regular'},'Existing-target case set')
    for mode,item in conflict['existing'].items():
        result=outcome('existing-'+mode,4,'TARGET_EXISTS')
        require(result==item['restore'] and item==read(root,'run/existing-'+mode+'-checked.json'),'Existing target receipts')
        unchanged('run/conflicts/existing/'+mode,item['before'],item['after'])
    winners={}
    for group,keys in [('same_input',('A','A')),('different_inputs',('A','B'))]:
        item=conflict['races'][group];winner=item['winner'];require(winner in (0,1),'Winner index')
        require(item==read(root,'run/'+group+'-checked.json') and item['winner_input']==keys[winner],'Winner binding')
        pids=[]
        for number in (0,1):
            label=group+'-'+str(number);result=outcome(label,0 if number==winner else 4,None if number==winner else 'TARGET_EXISTS')
            require(result==item['results'][number],'Contender receipt differs')
            ready=read(root,'run/'+label+'.ready');job=read(root,'run/'+label+'-job.json')
            require(ready['pid']==result['pid'] and ready['uid']==1000 and ready['target']==job['arguments']['target_dir']==plan['run_dir']+'/conflicts/'+group,'Rendezvous identity')
            require(job['input']==keys[number] and job['arguments']['snapshot']==plan['inputs'][keys[number]]['snapshot'],'Contender input')
            pids.append(result['pid'])
        require(len(set(pids))==2 and read(root,'run/'+group+'.release')=={'both_ready':True,'pids':pids},'Concurrent release proof')
        require(outcome(group+'-check',0)==item['check'],'Winner independent check')
        unchanged('run/conflicts/'+group,item['before_check'],item['after_check'])
        db=root/'run/conflicts'/group/'ledger.sqlite';original=root/('input-'+keys[winner])/'ledger.sqlite'
        require(db.read_bytes()==original.read_bytes() and database(db)==database(original),'Winning DB or complete state differs')
        require(set(item['before_check'])=={'.','ledger.sqlite'} and item['before_check']['ledger.sqlite']['nlink']==1,'Complete winner without temporary links')
        winners[group]=keys[winner]
    return {'status':'PASS','source_commit':SOURCE,'wheel_sha256':WHEEL,'portable_files':187,'portable_bytes':index['total_bytes'],
            'resource_tests':6,'API_calls':49,'legal_boundary_points':16,'full_lifecycles':11,'limit_rejections':5,'measurements':metrics,
            'retained_fixture_files':61,'retained_fixture_bytes':sum(v['size'] for v in omitted.values()),'independently_inspected_databases':38,
            'existing_target_refusals':5,'races':2,'race_winners':winners,'NAS_inputs_unchanged':True,'a2_overall':'PARTIAL',
            'resources_report_sha256':sha((root/'run/resources-report.json').read_bytes()),
            'conflicts_report_sha256':sha((root/'run/conflicts-report.json').read_bytes())}

if __name__=='__main__':print(json.dumps(audit(Path(sys.argv[1])),indent=2,sort_keys=True))
