"""Two final acceptance gaps, in fresh owned fixtures; no classification mocks."""
# Assertions in this retained acceptance runner include observable calls.
# Refuse optimized Python before package imports, plan reads or filesystem work.
if not __debug__:
 raise SystemExit("Acceptance requires assertions; run without -O or -OO.")

import hashlib,io,json,os,platform,sqlite3,subprocess,sys,zipfile
from pathlib import Path
from infra_artifact_ledger import initialize,open as open_ledger,LedgerError,recovery
from infra_artifact_ledger import snapshot_format as fmt,snapshot_storage as storage
from infra_artifact_ledger.sqlite_store import _SCHEMA,APPLICATION_ID
from infra_artifact_ledger.snapshot_common import Budget

def sha(raw):return hashlib.sha256(raw).hexdigest()
def wheel_modules(wheel_path,expected_sha256,package):
 """Compare module bytes and wheel digest from one bounded in-memory image."""
 with Path(wheel_path).open('rb') as source:raw=source.read(64*1024*1024+1)
 if len(raw)>64*1024*1024 or sha(raw)!=expected_sha256:
  raise ValueError('Wheel size or SHA-256 does not match the plan.')
 prefix='infra_artifact_ledger/'
 with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
  members=[item for item in wheel.infolist() if item.filename.startswith(prefix) and item.filename.endswith('.py')]
  names={item.filename for item in members}
  installed={prefix+p.relative_to(package).as_posix() for p in package.rglob('*.py') if '__pycache__' not in p.relative_to(package).parts}
  if len(members)!=17 or len(names)!=17 or names!=installed:
   raise ValueError('Wheel and installed Python module membership differ or contain duplicates.')
  if sum(item.file_size for item in members)>16*1024*1024:
   raise ValueError('Wheel Python modules exceed the acceptance budget.')
  modules={}
  for item in members:
   path=package/item.filename[len(prefix):]
   if path.is_symlink() or not path.is_file() or path.stat().st_size!=item.file_size:
    raise ValueError('Installed Python module type or size differs from wheel.')
   content=wheel.read(item)
   if path.read_bytes()!=content:raise ValueError('Installed Python module bytes differ from wheel.')
   modules[item.filename]=sha(content)
 return modules
def save_bytes(p,raw):
 with p.open('xb') as f:
  f.write(raw);f.flush();os.fsync(f.fileno())
def dump(p,v):
 with p.open('x') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
def inventory(root):
 return {str(p.relative_to(root)):{'size':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in root.rglob('*') if p.is_file()}
def run(plan):
 root=Path(plan['run_dir']);out=root/'fixtures-v2';out.mkdir(mode=0o700)
 receipts=out/'receipts';receipts.mkdir(mode=0o700)
 package=Path(recovery.__file__).parent
 modules=wheel_modules(plan['wheel'],plan['wheel_sha256'],package)
 helper=(root/'acceptance_helpers.py').read_bytes();assert sha(helper)==plan['helper_sha256']
 ns={'__name__':'closure_fixture_helpers'};exec(compile(helper,'acceptance_helpers.py','exec'),ns)
 config=json.loads(Path(plan['storage_config']).read_text());config=fmt.capture_storage_config(config)
 archive=Path(config['archive_root'])
 with storage.open_directory(archive,budget=Budget(),storage_config=config) as destination:
  destination.check('verify');binding=destination.identity;before_names=sorted(os.listdir(archive))
  for name in ('snapshots','scratch','targets','create-output'): (out/name).mkdir()
  utf16=out/'utf16.sqlite'
  c=sqlite3.connect(utf16)
  c.execute("PRAGMA encoding='UTF-16le'")
  for s in _SCHEMA:c.execute(s)
  c.execute("INSERT INTO ledger_format VALUES(1,'bounded-local-v0.1')")
  c.execute(f'PRAGMA application_id={APPLICATION_ID}');c.execute('PRAGMA user_version=1');c.commit();c.close()
  with open_ledger(utf16) as ledger: summary=ledger.verify()
  db= utf16.read_bytes(); assert int.from_bytes(db[56:60],'big')==2
  identity=plan['snapshot_id'];generation=out/'snapshots'/identity;generation.mkdir()
  save_bytes(generation/'ledger.sqlite',db)
  manifest=fmt.make_manifest(identity,plan['source_commit'],len(db),sha(db),summary)
  assert manifest['text_encoding']=='UTF-8'
  raw=fmt.encode(manifest,fmt.MAX_MANIFEST);mh=sha(raw)
  save_bytes(generation/'manifest.json',raw)
  save_bytes(generation/'COMMITTED.json',fmt.encode(fmt.make_marker(identity,mh),fmt.MAX_MARKER))
  check_target=out/'targets'/'utf16-existing';check_target.mkdir();save_bytes(check_target/'ledger.sqlite',db)
  protected=inventory(generation);source_before=sha(db)
  common={'snapshot':str(generation),'expected_manifest_sha256':mh,'scratch_parent':str(out/'scratch')}
  calls={
   'create':{'db':str(utf16),'output_root':str(out/'create-output'),'snapshot_id':identity,'source_commit':plan['source_commit']},
   'publish':dict(common,storage_config=config), 'verify':common,
   'restore':dict(common,target_dir=str(out/'targets'/'api-invalid')),
   'check_restore':{'target_dir':str(check_target),'expected_database_sha256':sha(db),'scratch_parent':str(out/'scratch')}}
  records=[];cli=Path(sys.executable).parent/'artifact-ledger'
  for api,kwargs in calls.items():
   try:getattr(recovery,api)(**kwargs)
   except recovery.RecoveryError as e:
    expected='not_applicable' if api in ('verify','check_restore') else 'not_published'
    assert (e.code,e.publication_state)==('UNSUPPORTED_FORMAT',expected),(api,e.code,e.publication_state)
    records.append({'route':'API','operation':api,'code':e.code,'state':e.publication_state,'stage':e.stage})
   else:raise AssertionError('UTF16 unexpectedly accepted: '+api)
   args=[str(cli),'snapshot',api.replace('_','-')]
   ck=dict(kwargs)
   if api=='restore':ck['target_dir']=str(out/'targets'/'cli-invalid')
   for k,v in ck.items():args+=['--'+k.replace('_','-'),plan['storage_config'] if k=='storage_config' else str(v)]
   cp=subprocess.run([sys.executable,'-I','-B',*args],capture_output=True,timeout=30)
   save_bytes(receipts/(api+'-stdout.json'),cp.stdout);save_bytes(receipts/(api+'-stderr.txt'),cp.stderr)
   answer=json.loads(cp.stdout);assert cp.returncode==3 and cp.stderr==b'',(api,cp.returncode)
   assert answer['error']['code']=='UNSUPPORTED_FORMAT' and answer['publication_state']==expected,answer
   records.append({'route':'CLI','operation':api,'exit':cp.returncode,'code':answer['error']['code'],'state':answer['publication_state']})
  assert inventory(generation)==protected and sha(utf16.read_bytes())==source_before
  assert (check_target/'ledger.sqlite').read_bytes()==db
  assert sorted(p.name for p in (out/'targets').iterdir())==['utf16-existing']
  assert not list((out/'create-output').iterdir()) and not list((out/'scratch').iterdir())
  destination.check('verify');assert destination.identity==binding and sorted(os.listdir(archive))==before_names
  nas_no_new_generation=True
 # New rich-enough fixture for same-key conflict after actual restoration.
 source=out/'source.sqlite';requests=[(ns['create'](),{}),ns['append']()];results=[]
 with initialize(source) as ledger:
  for req,payloads in requests:results.append(ledger.execute(ns['encode'](req),**({'payloads':payloads} if req['operation_kind']!='create_artifact' else {})))
 source_hash_before=sha(source.read_bytes())
 created=recovery.create(db=source,output_root=out/'snapshots',snapshot_id=plan['valid_snapshot_id'],source_commit=plan['source_commit'])
 restored=recovery.restore(snapshot=created['data']['snapshot_path'],expected_manifest_sha256=created['data']['manifest_sha256'],scratch_parent=out/'scratch',target_dir=out/'targets'/'restored')
 target=Path(restored['data']['database_path']);state_before=target.read_bytes()
 with open_ledger(target) as ledger:
  baseline=ledger.verify();oldops=[ledger.get_operation(ns['SCOPE'],r['operation_kind'],r['idempotency_key']) for r,_ in requests]
  for (req,payloads),original in zip(requests,results):assert ledger.execute(ns['encode'](req),**({'payloads':payloads} if req['operation_kind']!='create_artifact' else {}))==dict(original,replayed=True)
  conflicts=[(ns['create']('artifact:different',key=requests[0][0]['idempotency_key']),{}),ns['append']('changed',b'changed',key=requests[1][0]['idempotency_key'])]
  for req,payloads in conflicts:
   try:ledger.execute(ns['encode'](req),**({'payloads':payloads} if req['operation_kind']!='create_artifact' else {}))
   except LedgerError as e:assert (e.code,e.commit_state)==('IDEMPOTENCY_CONFLICT','not_committed'),(e.code,e.commit_state)
   else:raise AssertionError('Conflicting old key accepted')
   assert ledger.verify()==baseline
  assert [ledger.get_operation(ns['SCOPE'],r['operation_kind'],r['idempotency_key']) for r,_ in requests]==oldops
 assert target.read_bytes()==state_before
 before_new=inventory(out/'targets'/'restored')
 with open_ledger(target) as ledger:
  req,payloads=ns['append']('continuation',b'new content',parents=['version:quarterly-v1']);ledger.execute(ns['encode'](req),**({'payloads':payloads} if req['operation_kind']!='create_artifact' else {}))
  after=ledger.verify();assert after['counts']['versions']==baseline['counts']['versions']+1 and after['counts']['idempotency_records']==baseline['counts']['idempotency_records']+1
  assert after['counts']['import_receipts']==baseline['counts']['import_receipts']==0
 assert sha(source.read_bytes())==source_hash_before
 assert state_before==Path(created['data']['snapshot_path'],'ledger.sqlite').read_bytes()
 report={'status':'PASS','source_commit':plan['source_commit'],'wheel_sha256':plan['wheel_sha256'],'modules':modules,'runtime':{'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'machine':platform.machine()},'utf16':{'A1_schema_verified':True,'actual_header_encoding':2,'manifest_claim':'UTF-8','all_inputs_unchanged':True,'no_invalid_target_created':True,'NAS_binding_and_names_unchanged':nas_no_new_generation,'cases':records},'restored_idempotency':{'old_key_replays':2,'different_input_conflicts':2,'conflict_code':'IDEMPOTENCY_CONFLICT','physical_bytes_and_old_operations_unchanged':True,'before':baseline,'after_new_version':after,'before_new_write':before_new},'storage_classification_mocked':False,'product_modified':False,'public_publish_calls':2,'NAS_generation_published':False,'scope':'Real installed API/CLI, fresh ext4 fixtures; publish rejects invalid local input before any NAS generation. Existing NAS root binding and entries checked.'}
 dump(receipts/'report.json',report);dump(receipts/'file-index.json',inventory(root));print(json.dumps({'status':'PASS','utf16_routes':10,'conflicting_keys':2,'old_key_replays':2}))
if __name__=='__main__':run(json.loads(Path(sys.argv[1]).read_text()))
