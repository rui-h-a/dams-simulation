"""Native terminal controls using retained synthetic engineering raw only.
No Model construction, SDK, credentials, paid resources or formal samples.
Policy/Entry/Ledger interfaces are explicit test doubles where needed; Archive,
Collector, codec, filesystem, raw reader and snapshot validation remain original.
"""
import copy
from contextlib import ExitStack
from datetime import datetime,timezone,timedelta
import hashlib,json,os,shutil,types
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'source' if (ROOT/'source/research_tools').is_dir() else ROOT
sys.path[:0]=[str(SOURCE),str(SOURCE/'research_tools')]
sys.dont_write_bytecode=True
from research_tools import compute_only_archive as a,compute_only_control as c,compute_only_lifecycle as l
from research_tools import small_enterprise_cloud as h
from dams_sim.config import Config
from dams_sim.storage import canonical,digest,source_hash,file_digest
from dams_sim.longitudinal_pipeline import driver_hash
from dams_sim.transfer_spool import TransferSpool,_codec,MAX_METADATA_BYTES
from dams_sim._committed_pages import Directory,identity
from research_tools.validate_longitudinal import CheckedLongitudinalCases
FIXTURE=ROOT/'tests/fixtures/engineering_pair'

class Fixture:
 def __init__(self,root):
  self.root=root;self.stack=ExitStack();self.rows=[];self.raw_roots={};self.extra_paths={}
  self.runtime={'schema':'ENGINEERING_RUNTIME_ONLY','runtime_limits':{'batch_max_output_bytes':32*2**30,'batch_max_output_files':32798},
   'small_enterprise_profile':{'schema':h.PAIR_SCHEMA,'arm_profiles':[]}}
  declaration=json.loads((FIXTURE/'startup-existing/small-operation.json').read_bytes())['full_confirmation_pair_assignment']['spec']
  for arm in h.SELECTED_ARMS:
   attempt=next((FIXTURE/arm/'cases').glob('*/attempt-000'));m=json.loads((attempt/'manifest.json').read_bytes());cid=m['scientific_case_id']
   row={'case_id':cid,'config':m['config'],'tags':{'parent_case_id':None,'arm_id':arm,'world':m['config']['world']}}
   self.rows.append(row);self.raw_roots[cid]=attempt
   self.runtime['small_enterprise_profile']['arm_profiles'].append({'selected_arm':arm,'selected_case_id':cid})
  assignment={'schema':'dams-compute-only-assignment-v1','source_sha256':source_hash(),'pipeline_driver_sha256':driver_hash(),
   'spec_sha256':digest(canonical(declaration)),'spec':declaration,'inventory_sha256':digest(canonical(self.rows)),'cases':self.rows}
  self.assignment_raw=c.blob(assignment);codec=_codec();deps={n:digest((Path(codec.__file__).parent/n).read_bytes()) for n in ('cloud_control.py','cloud_archive.py')}
  for name in ('state','cache','copy1','copy2','lease','backup1','backup2','spool','acks'): (root/name).mkdir()
  config={'schema':c.COLLECTOR_SCHEMA,'stage_id':'engineering-pair',**{k:assignment[k] for k in ('source_sha256','pipeline_driver_sha256','spec_sha256','inventory_sha256')},
   'assignment_sha256':digest(self.assignment_raw),'codec_source_sha256':deps['cloud_archive.py'],'codec_dependencies_sha256':deps,
   'collector_source_sha256':c.COLLECTOR_IMPORT_SHA256,'deadline_utc':'2030-01-01T00:00:00Z','max_fetch_requests':65536,
   'max_fetch_bytes':512*2**20,'max_group_raw_bytes':16*2**20,'min_free_bytes':0,'state_dir':str(root/'state'),
   'cache_dir':str(root/'cache'),'copy_dirs':[str(root/'copy1'),str(root/'copy2')]}
  raw=c.blob(config);self.admission=root/'admission.json';self.admission.write_bytes(raw)
  self.collector=c.Collector(self.admission,admission_sha256=digest(raw),assignment_raw=self.assignment_raw)
  self.stack.callback(self.collector.close)
  job={k:config[k] for k in a.JOB_FIELDS-{'runtime_sha256','provider_identity_sha256'}}
  job.update(runtime_sha256=digest(c.blob(self.runtime)),provider_identity_sha256='4'*64)
  self.backends=[]
  for index in (1,2):
   bid='engineering-backup'+str(index);directory=root/('backup'+str(index))
   enrollment={'schema':'dams-compute-persistent-backup-enrollment-v1','job_sha256':digest(canonical(job)),
    'backup_id':bid,'domain':'same-device-engineering-only','locator':str(directory),'kind':'local-fixture'}
   er=c.blob(enrollment);binding={k:enrollment[k] for k in ('backup_id','domain','locator')};binding['enrollment_sha256']=digest(er)
   backend=a.DirectoryBackend(directory,binding,er);self.backends.append(backend);self.stack.callback(backend.close)
  profile={'schema':a.PROFILE_SCHEMA,'mode':'local-fixture','collector_binding_sha256':digest(canonical(config)),
   'helper_sha256':a.IMPORT_SHA,'job':job,'lease_dir':str(root/'lease'),'max_lease_raw_bytes':16*2**20,
   'max_archive_encoded_bytes':128*2**20,'backup_bindings':[b.binding for b in self.backends],'prior_cases':{},
   'max_operation_seconds':5400,'max_rss_bytes':2**30}
  pr=c.blob(profile);p=root/'archive-profile.json';p.write_bytes(pr)
  self.archive=a.ArchiveClosure(self.collector,{'path':str(p),'sha256':digest(pr)},self.backends);self.collector.archive=self.archive
  sr=c.blob({'stage_id':config['stage_id'],'spool_dir':str(root/'spool'),'ack_dir':str(root/'acks'),
   'expected_source_sha256':source_hash(),'max_spool_bytes':32*2**20,'max_transfer_bytes':64*2**20,'deadline_utc':'2030-01-01T00:00:00Z'})
  p=root/'transfer.json';p.write_bytes(sr);self.spool=TransferSpool(p,expected_source_sha256=source_hash())
  self.policy=lambda:patch.multiple(h,validate_pair_profile=lambda profile,limits:profile,
    assignment=lambda profile,limits:{'cases':self.rows})
 def close(self):self.stack.close()
 def collect_cases(self):
  manifests={}
  for row in self.rows:
   attempt=self.raw_roots[row['case_id']];meta=json.loads((attempt/'manifest.json').read_bytes())
   self.spool.publish_final(attempt,meta)
   group=next(p for p in (self.root/'spool/groups').iterdir() if json.loads((p/'manifest.json').read_bytes())['case_id']==row['case_id'])
   mr=(group/'manifest.json').read_bytes();sr=(group/'seal.json').read_bytes();m=json.loads(mr)
   tickets=[self.collector.reserve_metadata_attempt(len(x)) for x in (mr,sr)]
   def fetch(part,chunk):
    import io
    return io.BytesIO((group/part['part_directory']/ (f"{chunk['offset']:020d}.z")).read_bytes())
   # Original full13 byte decode plus scientific raw gate, no Model constructors.
   self.collector.collect(mr,sr,fetch,metadata_tickets=tickets)
   manifests[m['generation']]=m
  return manifests
 def roster(self):
  files=[]
  for row in self.rows:
   prefix=row['tags']['arm_id']+'/cases/'+row['case_id']+'/attempt-000/'
   for p in sorted(self.raw_roots[row['case_id']].iterdir()):
    if p.is_file():files.append({'path':prefix+p.name,'bytes':p.stat().st_size,'sha256':file_digest(p),'identity':list(identity(p.stat()))})
  for name,p in sorted(self.extra_paths.items()):
   files.append({'path':name,'bytes':p.stat().st_size,'sha256':file_digest(p),'identity':list(identity(p.stat()))})
  return {'schema':'dams-compute-terminal-roster-v1','files':files,'source_sha256':source_hash(),
   'runtime_sha256':digest(c.blob(self.runtime)),'science_complete':False,'case_payloads_require_original_collector':True,
   'terminal':{'owned_pipeline_group_verification':{'owned_pipeline_group_absent':True}}}
 def read_range(self,f,offset,length):
  parts=f['path'].split('/');cid=parts[2];name='/'.join(parts[4:])
  path=self.extra_paths.get(f['path'],self.raw_roots[cid]/name)
  with path.open('rb') as stream:stream.seek(offset);return stream.read(length)

def native_live_fixture(fix, *, terminal_bytes=32*2**30):
 root=fix.root;state=root/'live-events';state.mkdir()
 for name in ('terminal1','terminal2','heads','keys'): (root/name).mkdir()
 key=root/'keys/fixture-key';key.write_bytes(b'ENGINEERING_NOT_A_PRIVATE_KEY')
 key.with_name(key.name+'.pub').write_bytes(b'ENGINEERING_NOT_AN_SSH_PUBLIC_KEY')
 minimum=c.blob(fix.collector.head);head=root/'minimum.json';head.write_bytes(minimum)
 ending=datetime(2030,1,1,tzinfo=timezone.utc)
 config={'paid_actions_authorized':True,'authorization_id':'engineering-only',
  'global_deadline_utc':'2030-01-01T00:00:00Z','budget_cap_usd':'100','reserve_usd':'20','prior_spend_usd':'0'}
 stage={'id':'engineering-pair','small_enterprise_profile':fix.runtime['small_enterprise_profile'],
  'runtime_limits':fix.runtime['runtime_limits'],'expected_guest':{'architecture':'x86_64','vcpus':4,'memory_gib_min':'30','memory_gib_max':'32'},
  **{k:fix.collector.config[k] for k in ('assignment_sha256','spec_sha256','inventory_sha256')}}
 held={'intent_sha256':'2'*64,'reserved_usd':'1','termination_utc':config['global_deadline_utc']}
 ledger={**{k:config[k] for k in ('authorization_id','prior_spend_usd')},'deadline_utc':config['global_deadline_utc'],
  'cap_usd':'100','reserve_usd':'20','entries':{stage['id']:held}}
 lp=root/'ledger.json';lp.write_bytes(c.blob(ledger))
 approval={'schema':'dams-root-compute-live-approval-v1','authorization_id':config['authorization_id'],
  'stage_id':stage['id'],**held,'source_manifest_sha256':'1'*64,'paid_actions_authorized':True}
 ap=root/'approval.json';ap.write_bytes(c.blob(approval));assignment=root/'assignment.json';assignment.write_bytes(fix.assignment_raw)
 options={k:None for k in l.FIELDS};options.update(schema=l.SCHEMA,phase='launch',ledger_path=str(lp),
  approval={'path':str(ap),'sha256':digest(ap.read_bytes())},collector_admission={'path':str(fix.admission),'sha256':digest(fix.admission.read_bytes())},
  assignment={'path':str(assignment),'sha256':digest(fix.assignment_raw)},minimum_head={'path':str(head),'sha256':digest(minimum)},
  head_receipt_dir=str(root/'heads'),ssh_key_file=str(key),network_tag='engineering-only',terminal_copy_dirs=[str(root/'terminal1'),str(root/'terminal2')],
  max_terminal_files=8192,max_terminal_bytes=terminal_bytes,max_command_seconds=300,max_stdout_bytes=65536,overhead_bytes=65536)
 def reference(ref):
  raw=Path(ref['path']).read_bytes();assert digest(raw)==ref['sha256'];return raw
 entry=types.SimpleNamespace(c=config,stage=stage,options={},manifest_sha='1'*64,termination=ending,
  reference=reference,check=lambda **kw:None,state=types.SimpleNamespace(path=state))
 # Root financial and entry interfaces are declared fixtures. Native Live,
 # directory validation and the reopened original Collector execute fully.
 ledger_double=types.SimpleNamespace(reserve=lambda s:held)
 with fix.policy(),patch.object(l.cc,'stage_cost',return_value=l.cc.money('0')),patch.object(l.cc,'Ledger',return_value=ledger_double):
  live=l.Live(entry,options,g=types.SimpleNamespace(run=lambda *a,**k:(_ for _ in ()).throw(AssertionError('no SDK'))))
 return live

class Controls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=ROOT.parent if SOURCE==ROOT else ROOT);self.root=Path(self.temp.name);self.fix=Fixture(self.root)
 def tearDown(self):self.fix.close();self.temp.cleanup()
 def test_live_constructor_native_finite_envelope_and_mismatch(self):
  live=native_live_fixture(self.fix)
  try:self.assertEqual(live.o['max_terminal_files'],8192);self.assertEqual(live.collector.head,self.fix.collector.head)
  finally:live.close()
  # A separate native fixture root avoids deliberately reusing owned dirs.
  other=self.root/'other';other.mkdir();fix=Fixture(other)
  try:
   with self.assertRaisesRegex(ValueError,'exact whole bytes'):
    native_live_fixture(fix,terminal_bytes=16*2**30)
  finally:fix.close()
 def test_paired_complete_census_two_children_are_authoritative(self):
  manifests=self.fix.collect_cases();roster=self.fix.roster()
  # Add the actual retained control/census bytes of BOTH engineering children.
  for arm in h.SELECTED_ARMS:
   for p in sorted((FIXTURE/arm).rglob('*')):
    if p.is_file() and 'cases' not in p.relative_to(FIXTURE/arm).parts:
     name=arm+'/'+str(p.relative_to(FIXTURE/arm));roster['files'].append({'path':name,'bytes':p.stat().st_size,'sha256':file_digest(p),'identity':list(identity(p.stat()))})
  roots=[self.root/'census1',self.root/'census2']
  for root in roots:
   root.mkdir()
   for f in roster['files']:
    if a.terminal_case_tree(f['path']):continue
    source=FIXTURE/f['path'];target=root/f['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
   (root/'terminal-roster.json').write_bytes(c.blob(roster))
  live=l.Live.__new__(l.Live);live.stack=ExitStack();live.collector=self.fix.collector;live.runtime=self.fix.runtime;live.check=lambda:None
  live.terminal_copies=[live.stack.enter_context(l._directory(root)) for root in roots]
  try:
   with self.fix.policy():result,guard=live._preservation_census(manifests)
   self.assertEqual(result['assigned_cases'],2);self.assertEqual(set(result['classifications'].values()),{'complete'});guard()
   bad=copy.deepcopy(roster);f=copy.deepcopy(roster['files'][0]);f['path']=f['path'].replace(self.fix.rows[0]['case_id'],'f'*64);bad['files'].append(f)
   for root in roots:(root/'terminal-roster.json').write_bytes(c.blob(bad))
   with self.fix.policy(),self.assertRaisesRegex(ValueError,'undeclared or cross-arm'):
    live._preservation_census(manifests)
  finally:live.stack.close()
 def test_native_full13_both_arm_archive_decode_and_matching(self):
  manifests=self.fix.collect_cases();roster=self.fix.roster();raw=c.blob(roster)
  self.assertEqual(len(roster['files']),26);self.assertLess(len(raw),65536)
  with self.fix.policy():
   self.fix.archive.terminal_archive(raw,self.fix.read_range,runtime=self.fix.runtime)
   result=self.fix.archive.terminal_guard();self.assertTrue(result['all_terminal_bytes_full_decode_verified'])
   self.assertFalse(result['external_persistence'])
   prefixes=a.terminal_case_prefixes(self.fix.collector,self.fix.runtime)
  files={f['path']:f for f in roster['files']}
  for m in manifests.values():
   self.assertTrue(self.fix.archive.native_roster_matches(m,files,case_prefix=prefixes[m['case_id']]))
   self.assertFalse(self.fix.archive.native_roster_matches(m,files))
 def test_paired_assignment_runtime_and_wrong_arm_rejected(self):
  roster=self.fix.roster();bad=copy.deepcopy(self.fix.runtime);bad['schema']='changed'
  with self.fix.policy(),self.assertRaisesRegex(ValueError,'runtime pin differs'):
   self.fix.archive.terminal_archive(c.blob(roster),lambda *_:self.fail('no range'),runtime=bad)
  with self.fix.policy():
   prefixes=a.terminal_case_prefixes(self.fix.collector,self.fix.runtime)
  for row in self.fix.rows:self.assertEqual(prefixes[row['case_id']],row['tags']['arm_id']+'/cases/'+row['case_id']+'/')
  bad=copy.deepcopy(self.fix.runtime);bad['small_enterprise_profile']['arm_profiles'][0]['selected_arm']='startup-founding'
  with self.fix.policy(),self.assertRaisesRegex(ValueError,'arm/case identity differs'):
   a.terminal_case_prefixes(self.fix.collector,bad)
 def test_metadata_rejected_before_any_range_or_publish(self):
  roster=self.fix.roster();roster['files'][0]['bytes']=2**63-1
  with patch.object(self.fix.archive,'publish',side_effect=AssertionError('no publish')) as publish:
   with self.assertRaisesRegex(ValueError,'chunk metadata cannot fit'):
    self.fix.archive.terminal_archive(c.blob(roster),lambda *_:self.fail('no range'))
   self.assertEqual(publish.call_count,0)
  raw=c.blob({**self.fix.roster(),'padding':'x'*MAX_METADATA_BYTES})
  with self.assertRaisesRegex(ValueError,'roster metadata bound'):
   self.fix.archive.terminal_archive(raw,lambda *_:self.fail('no range'))
 def test_native_terminal_quota_and_group_quota_retained(self):
  from dams_sim.transfer_spool import MAX_FILES
  self.assertEqual(MAX_FILES,8192);self.assertEqual(MAX_METADATA_BYTES,8*2**20)
  roster=self.fix.roster();roster['files']=[roster['files'][0]]*8193
  with self.assertRaisesRegex(ValueError,'file cap differs'):
   self.fix.archive.terminal_archive(c.blob(roster),lambda *_:self.fail('no range'))
 def test_native_large_forensic_case_pull_preserves_every_byte(self):
  manifests=self.fix.collect_cases()
  # Additional 33MiB forensic file is synthetic, deliberately NOT scientific
  # final-state data; original retained full13 and checkpoints remain unchanged.
  row=self.fix.rows[0];name=row['tags']['arm_id']+'/cases/'+row['case_id']+'/attempt-000/unvalidated-large.sqlite'
  large=self.root/'engineering-large-33MiB';size=33*2**20
  with large.open('wb') as stream:stream.truncate(size)
  self.fix.extra_paths[name]=large;roster=self.fix.roster();roster_raw=c.blob(roster)
  live=l.Live.__new__(l.Live);live.stack=ExitStack();live.collector=self.fix.collector;live.runtime=self.fix.runtime
  live.check=lambda:None;live.load_runtime=lambda:None;live.o={'max_terminal_files':8192,'max_terminal_bytes':32*2**30}
  live.event=lambda name,result:result
  roots=[self.root/'native-terminal1',self.root/'native-terminal2']
  for root in roots:root.mkdir()
  live.terminal_copies=[live.stack.enter_context(l._directory(root)) for root in roots]
  actions=[]
  def exchange(action,input_raw=b'',bound=None):
   actions.append(action)
   if action=='terminal-roster':return roster_raw
   self.assertEqual(action,'terminal-read');value=json.loads(input_raw)
   self.assertEqual(bound,value['length'])
   return self.fix.read_range(value['file'],value['offset'],value['length'])
  live.exchange=exchange
  try:
   with self.fix.policy():
    result=live._pull_archive_terminal();live._terminal_copy_guard()
    self.assertTrue(result['all_terminal_bytes_full_decode_verified'])
    self.assertFalse(result['science_complete'])
    index=json.loads(c.read(self.fix.collector.state,'terminal-archive-index.json')[0])
    stored=next(f for f in index['files'] if f['path']==name)
    self.assertEqual(stored['bytes'],size);self.assertEqual(stored['sha256'],file_digest(large))
    self.assertEqual(stored['scientific_status'],'unvalidated-terminal-evidence')
    self.assertGreater(actions.count('terminal-read'),0)
    prefixes=a.terminal_case_prefixes(self.fix.collector,self.fix.runtime)
    original=next(m for m in manifests.values() if m['case_id']==row['case_id'])
    self.assertFalse(self.fix.archive.native_roster_matches(original,{f['path']:f for f in roster['files']},case_prefix=prefixes[row['case_id']]))
    for root in roots:
     self.assertFalse((root/name).exists())
     self.assertEqual((root/'terminal-roster.json').read_bytes(),roster_raw)
  finally:live.stack.close()
 def test_exchange_8mib_bound_rejected_before_reserve_or_dispatch(self):
  live=l.Live.__new__(l.Live);live.host_path='engineering-known-hosts'
  helper=(SOURCE/'research_tools/compute_only_remote.py').read_bytes()
  live._context=lambda:{'remote_helper_sha256':digest(helper),'zone':'engineering-only','deadline_utc':'2030-01-01T00:00:00Z'}
  live.entry=types.SimpleNamespace(root=types.SimpleNamespace(path=SOURCE))
  live.e={'instance_name':'engineering-only'}
  live.c={'project':'engineering-only','gcloud_configuration':'engineering-only','gcloud_account':'engineering-only'}
  live.o={'max_stdout_bytes':65536,'overhead_bytes':65536,'ssh_key_file':'engineering-only'}
  live.collector=self.fix.collector
  with patch.object(live.collector,'reserve_metadata_attempt',side_effect=AssertionError('reserve forbidden')) as reserve:
   with self.assertRaisesRegex(ValueError,'complete metadata reservation'):
    live.exchange('terminal-roster',bound=MAX_METADATA_BYTES)
   self.assertEqual(reserve.call_count,0)
 def test_truncated_range_refused_without_terminal_acceptance(self):
  roster=self.fix.roster()
  with self.fix.policy(),self.assertRaisesRegex(ValueError,'range truncated/oversized'):
   self.fix.archive.terminal_archive(c.blob(roster),lambda f,o,n:self.fix.read_range(f,o,n)[:-1],runtime=self.fix.runtime)
  self.assertFalse((self.root/'state/terminal-archive-index.json').exists())
 def test_large_case_paths_are_case_evidence_not_32mib_control(self):
  for row in self.fix.rows:
   name=row['tags']['arm_id']+'/cases/'+row['case_id']+'/attempt-000/final_state.sqlite'
   self.assertTrue(a.terminal_case_tree(name))
  self.assertTrue(a.terminal_case_tree('startup-existing/cases/unknown/attempt-000/raw.sqlite'))
  self.assertFalse(a.terminal_case_tree('startup-existing/confirmation-operation/case_census.json'))

if __name__=='__main__':unittest.main(verbosity=2)
