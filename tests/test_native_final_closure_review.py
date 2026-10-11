"""Independent local native final-preservation seam controls; never SDK delete.

The fixture's retained n4/day5 raw artifacts are unchanged. Only its synthetic
provider job pin and pinned transfer-config field are made internally coherent.
Actual Live.closeout/ACK closure/archive/full decode/census/guard code executes.
Policy selection, entry checks, local exchange and final provider deletion are
explicit doubles. No external persistence/provider/cost authority is asserted.
"""
from contextlib import ExitStack
import copy,hashlib,json,os,shutil,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research_tools'),str(ROOT/'tests')]
from research_tools import compute_only_remote as remote
from research_tools import compute_only_lifecycle as l, compute_only_control as c, compute_only_archive as a
from dams_sim.storage import digest,source_hash,file_digest
HERE=ROOT.parent
subject=(ROOT/'tests/test_paired_terminal_envelope.py').read_text()
# Independent fixture adaptation only; candidate/runtime source is untouched.
subject=subject.replace("'small_enterprise_profile':{'schema':h.PAIR_SCHEMA,'arm_profiles':[]}}", "'small_enterprise_profile':{'schema':h.PAIR_SCHEMA,'arm_profiles':[]}}\n  self.runtime['transfer_config_sha256']=digest(c.blob({'stage_id':'engineering-pair','spool_dir':str(root/'spool'),'ack_dir':str(root/'acks'),'expected_source_sha256':source_hash(),'max_spool_bytes':32*2**20,'max_transfer_bytes':64*2**20,'deadline_utc':'2030-01-01T00:00:00Z'}))")
subject=subject.replace("provider_identity_sha256='4'*64", "provider_identity_sha256=digest(c.blob({'vm':{'id':'engineering-instance'},'disk':{'id':'engineering-disk'}}))")
ns={'__name__':'independent_engineering_fixture','__file__':str(ROOT/'tests/test_paired_terminal_envelope.py')};exec(compile(subject,ns['__file__'],'exec'),ns)
Fixture=ns['Fixture'];FIXTURE=ns['FIXTURE'];h=ns['h'];OBS=[]
class Controls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=HERE);self.root=Path(self.temp.name);self.fix=Fixture(self.root);self.stack=ExitStack()
 def tearDown(self):self.stack.close();self.fix.close();self.temp.cleanup()
 def closure(self,wrong_arm=False):
  manifests=self.fix.collect_cases()
  for generation in manifests:
   ack=c.read(self.fix.collector.state,generation+'.ACK.json')[0]
   (self.root/'acks'/(generation+'.json')).write_bytes(ack)
  with self.fix.spool._locked():self.fix.spool._reconcile_acks()
  transfer=(self.root/'transfer.json').read_bytes();(self.root/'transfer-config.json').write_bytes(transfer)
  (self.root/'compute-runtime.json').write_bytes(c.blob(self.fix.runtime))
  snapshot={'schema':'dams-compute-live-poll-v1','source_sha256':source_hash(),'runtime_sha256':digest(c.blob(self.fix.runtime)),
   **remote.spool_snapshot({'stage_id':'engineering-pair','source_sha256':source_hash(),'deadline_utc':'2030-01-01T00:00:00Z'},self.root),'science_complete':False}
  self.assertEqual(len(snapshot['sealed_generations']),2);self.assertTrue(all(v['ack_present'] for v in snapshot['sealed_generations']))
  roster=self.fix.roster()
  for arm in h.SELECTED_ARMS:
   for p in sorted((FIXTURE/arm).rglob('*')):
    if p.is_file() and 'cases' not in p.relative_to(FIXTURE/arm).parts:
     name=arm+'/'+str(p.relative_to(FIXTURE/arm));self.fix.extra_paths[name]=p
  roster=self.fix.roster()
  if wrong_arm:
   f=copy.deepcopy(roster['files'][0]);f['path']=f['path'].replace('startup-existing/','startup-founding/',1);roster['files'].append(f)
  raw=c.blob(roster);live=object.__new__(l.Live);live.stack=self.stack;live.collector=self.fix.collector;live.runtime=self.fix.runtime
  live.vm={'id':'engineering-instance'};live.disk={'id':'engineering-disk'};live.check=lambda:None;live.load_runtime=lambda:None
  live.o={'max_terminal_files':8192,'max_terminal_bytes':32*2**30};live.poll=lambda:copy.deepcopy(snapshot)
  dirs=[]
  for name in ('terminal1','terminal2'):
   path=self.root/name;path.mkdir();dirs.append(self.stack.enter_context(l._directory(path)))
  live.terminal_copies=dirs;events=[];deletes=[]
  live.event=lambda name,value:events.append((name,value)) or value
  live.ledger=types.SimpleNamespace(event=lambda *args,**kwargs:events.append(('ledger',kwargs)))
  live.s={'id':'engineering-pair'}
  def read_range(f,offset,length):
   name=f['path']
   if name in self.fix.extra_paths:p=self.fix.extra_paths[name]
   else:
    parts=name.split('/');p=self.fix.raw_roots[parts[2]]/'/'.join(parts[4:])
   with p.open('rb') as stream:stream.seek(offset);return stream.read(length)
  def exchange(action,input_raw=b'',bound=None):
   if action=='terminal-roster':return raw
   self.assertEqual(action,'terminal-read');v=json.loads(input_raw);self.assertEqual(bound,v['length']);return read_range(v['file'],v['offset'],v['length'])
  live.exchange=exchange
  def delete(*,preservation_gate):
   preservation_gate();deletes.append(True)
   return {'provider_called':False,'provider_absence_verified':False,'new_paid_authority':False,'mock_provider_boundary_only':True,'science_complete':False}
  live.delete=delete
  return live,events,deletes,snapshot
 def test_full_original_pair_native_closeout_preservation_gate_reaches_only_mock_provider_boundary(self):
  live,events,deletes,snapshot=self.closure()
  with self.fix.policy():result=live.closeout()
  self.assertEqual(deletes,[True]);self.assertFalse(result['cleanup']['provider_called']);self.assertFalse(result['science_complete'])
  self.assertEqual(result['census']['assigned_cases'],2);self.assertEqual(set(result['census']['classifications'].values()),{'complete'})
  self.assertTrue(result['terminal']['all_terminal_bytes_full_decode_verified']);self.assertEqual(len(snapshot['sealed_generations']),2)
  OBS.append({'control':'native_closed_pair_final_preservation_seam','accepted_case_groups':2,'original_raw13_per_arm':True,'both_native_ACKs_reconciled':True,'native_spool_snapshot':True,'native_Live_closeout':True,'native_preservation_callback_rerun':True,'mock_delete_boundary_calls':len(deletes),'provider_calls':0,'provider_absence_verified':False,'external_persistence':False,'formal_worlds_added':0})
 def test_cross_arm_closed_census_failure_never_reaches_delete_boundary(self):
  live,events,deletes,snapshot=self.closure(wrong_arm=True)
  with self.fix.policy(),self.assertRaisesRegex(ValueError,'undeclared or cross-arm'):live.closeout()
  self.assertEqual(deletes,[]);self.assertTrue(any(name=='ledger' for name,_ in events))
  self.assertTrue((self.root/'state/terminal-archive-index.json').exists())
  OBS.append({'control':'native_closed_pair_cross_arm_refusal','mock_delete_boundary_calls':0,'complete_terminal_evidence_retained':True,'provider_calls':0,'formal_worlds_added':0})
if __name__=='__main__':unittest.main(verbosity=2)
