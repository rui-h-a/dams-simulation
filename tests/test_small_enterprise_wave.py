from pathlib import Path
import importlib.util,json,sys,tempfile,unittest,hashlib,types
from unittest.mock import patch
ROOT=Path(__file__).absolute().parents[1]
sys.path.insert(0,str(ROOT))
from research_tools import small_enterprise as m
# The immutable scientific parent is needed for the differential controls.
import os,subprocess
PARENT="30a604e776ecf1c5c0196e6ed44f6b93ac1312b5"
repo=os.environ.get("DAMS_SCIENTIFIC_PARENT_REPO",str(ROOT))
raw=subprocess.run(["git","-C",repo,"show",PARENT+":research_tools/small_enterprise.py"],capture_output=True,check=True,timeout=10).stdout
assert hashlib.sha256(raw).hexdigest()=="2732dc6ff3d73e9848c022522e02a744ca0e77b775d472227585e8229784494a"
old=types.ModuleType("unchanged_previous_small_entry")
exec(compile(raw,"immutable_parent_small_enterprise.py","exec"),old.__dict__)
LIMITS=m.RuntimeLimits.from_dict(json.loads((ROOT/"docs/frozen-studies/small-enterprise-five-year/n300-wave-runtime.json").read_bytes()))


class WaveControls(unittest.TestCase):
 def setup_pair(self,width=1,*,free=(97*m.GIB,97*m.GIB),fail_raw=None,closed=True,wrong_manifest=False,cached=False,invalid_path=False):
  scratch=tempfile.TemporaryDirectory(dir=ROOT,prefix='controls-');self.addCleanup(scratch.cleanup)
  root=Path(scratch.name);_,decl=m.spec(300);roster=m.rows(300,42000,LIMITS)
  pair=root/'pilot/world-42000';pair.mkdir(parents=True)
  calls=[];refs=[]
  for row in roster:
   rel='cases/'+row['case_id']+'/attempt-000'
   p=pair/rel;p.mkdir(parents=True);b=b'fixed simulated producer manifest\n';(p/'manifest.json').write_bytes(b)
   # A later failed directory must never override Scheduler's selected receipt.
   (p.parent/'attempt-001').mkdir()
   refs.append({'attempt':rel,'manifest_sha256':hashlib.sha256(b).hexdigest()})
  if wrong_manifest:refs[0]['manifest_sha256']='0'*64
  if invalid_path:refs[0]['attempt']='../outside/attempt-000'
  class FakeScheduler:
   def __init__(self,p,l,d):
    self.owned_workers_absent=True;self.l=l
    assert l.max_output_bytes==16*m.GIB and l.batch_max_output_bytes==32*m.GIB+256*2**20
    assert l.min_free_disk_bytes==80*m.GIB
   def run(self,configs):
    i=sum(len(x[1]) for x in calls if x[0]=='producer');f=free[min(i,len(free)-1)]
    if not cached and f < self.l.min_free_disk_bytes + len(configs)*16*m.GIB:
     calls.append(('refusal',[c.regime for c in configs]));raise RuntimeError('real-capacity-refusal')
    calls.append(('producer',[c.regime for c in configs]));self.owned_workers_absent=closed
    return [refs[roster.index(next(r for r in roster if r['config']==c.to_dict()))] for c in configs]
  class Reader:
   def __init__(self,*args):self.count=0
   def validate_case(self,row,attempt,**kw):
    calls.append(('raw',[row['config']['regime']]))
    assert attempt.name=='attempt-000'
    if fail_raw==row['config']['regime']:raise ValueError('full-raw-gate-refused')
    self.count+=1
    return {'case_id':row['case_id'],'tags':row['tags'],'attempt':attempt,'state_semantic_sha256':'fixed','summary':{},'config':m.Config.from_dict(row['config'])}
   def result(self):return {'unique_complete_cases':self.count}
  patches=[patch.object(m,'guard',lambda _:None),patch.object(m,'Scheduler',FakeScheduler),patch.object(m,'CheckedLongitudinalCases',Reader),patch.object(m,'pair_effects',lambda *a:([],[])),patch.object(m.shutil,'disk_usage',lambda _ :types.SimpleNamespace(free=free[0]))]
  for p in patches:p.start();self.addCleanup(p.stop)
  self.calls=calls;self.root=root
  return lambda:m.execute_pair(root,300,42000,'pilot',LIMITS,decl,'fixed-entry',producer_wave_width=width)
 def test_one_wave_full_caps_and_raw_before_second(self):
  f=self.setup_pair();f();self.assertEqual([x[0] for x in self.calls],['producer','raw','producer','raw'])
  self.assertTrue((self.root/'pilot/world-42000/paired-result.json').exists())
 def test_second_refusal_preserves_first_no_pair(self):
  f=self.setup_pair(free=(97*m.GIB,95*m.GIB))
  with self.assertRaisesRegex(RuntimeError,'capacity'):f()
  self.assertEqual([x[0] for x in self.calls],['producer','raw','refusal'])
  self.assertFalse((self.root/'pilot/world-42000/paired-result.json').exists())
  self.assertTrue(any((self.root/'pilot/world-42000/cases').glob('*/attempt-000/manifest.json')))
 def test_raw_refusal_prevents_second(self):
  f=self.setup_pair(fail_raw='hierarchy')
  with self.assertRaisesRegex(ValueError,'raw'):f()
  self.assertEqual([x[0] for x in self.calls],['producer','raw'])
 def test_ownership_unclosed_prevents_raw(self):
  f=self.setup_pair(closed=False)
  with self.assertRaisesRegex(RuntimeError,'ownership'):f()
  self.assertEqual([x[0] for x in self.calls],['producer'])
 def test_exact_selected_manifest_required(self):
  f=self.setup_pair(wrong_manifest=True)
  with self.assertRaisesRegex(ValueError,'manifest'):f()
  self.assertEqual([x[0] for x in self.calls],['producer'])
 def test_selected_attempt_must_belong_to_case(self):
  f=self.setup_pair(invalid_path=True)
  with self.assertRaisesRegex(ValueError,'outside'):f()
 def test_newer_failed_history_ignored_by_reference(self):
  f=self.setup_pair();f();self.assertEqual(sum(x[0]=='raw' for x in self.calls),2)
 def test_cached_no_producer_work_does_not_need_new_cap(self):
  f=self.setup_pair(free=(79*m.GIB,),cached=True);f()
  self.assertTrue((self.root/'pilot/world-42000/paired-result.json').exists())
 def test_parallel_admission_unchanged(self):
  f=self.setup_pair(2,free=(111*m.GIB,))
  with self.assertRaisesRegex(RuntimeError,'both'):f()
  self.assertEqual(self.calls,[])
 def test_parallel_single_scheduler_run(self):
  f=self.setup_pair(2,free=(113*m.GIB,));f()
  self.assertEqual([x[0] for x in self.calls],['producer','raw','raw'])
 def test_invalid_wave_types(self):
  for v in [True,0,3,1.0]:
   with self.assertRaisesRegex(ValueError,'width'):
    m.execute_pair(Path('/unused'),300,42000,'pilot',LIMITS,{},'unused',producer_wave_width=v)
 def test_config_casehash_source_spec_precision_unchanged(self):
  self.assertEqual(m.CORE,old.CORE);self.assertEqual(m.DRIVER,old.DRIVER)
  self.assertEqual(m.source_hash(),m.CORE);self.assertEqual(m.driver_hash(),m.DRIVER)
  for n in [30,120,300]:
   self.assertEqual(m.spec(n)[1],old.spec(n)[1])
   self.assertEqual(m.rows(n,42000,LIMITS),old.rows(n,42000,LIMITS))
if __name__=='__main__':unittest.main()
