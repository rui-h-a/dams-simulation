"""Actual native Ledger/strict VM validator on synthetic metadata only.
No Model, SQL, SDK, authentication, provider request or real ledger mutation.
"""
import ast,copy,importlib.util,json,tempfile,unittest,types
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
O=Path(__file__).resolve().parents[1]
CONTROL=O/'research_tools/cloud_control.py'
NORMALIZATION_LINE='            expiry = expiry.replace(microsecond=(expiry.microsecond // 1000) * 1000)\n'
NEW_SOURCE=CONTROL.read_text()
assert NEW_SOURCE.count(NORMALIZATION_LINE)==1
PRE_FLOOR_SOURCE=NEW_SOURCE.replace(NORMALIZATION_LINE,'')

def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
old=types.ModuleType('synthetic_prefloor_control');old.__file__=str(CONTROL)
exec(compile(PRE_FLOOR_SOURCE,'synthetic_prefloor_control','exec'),old.__dict__)
new=load('candidate_ms_control',CONTROL)

def config(now,micro=187908):
 stage={'id':'synthetic-expiry','max_seconds':190800,'machine_type':'n2-highmem-2','purchase_mode':'STANDARD','disk_type':'pd-standard','architecture_declaration':'official-n2-x86_64','expected_guest':{'architecture':'x86_64','vcpus':2,'memory_gib_min':'15','memory_gib_max':'16'},'quota':{'metric':'N2_CPUS','info_id':'N2-CPUS-per-project-region','dimensions':{'region':'us-central1'}}}
 c={'authorization_id':'SYNTHETIC-NO-AUTHORITY','source_commit':'a'*40,'project':'synthetic-only','region':'us-central1','zones':['us-central1-a'],'bucket':'synthetic-only','service_account':'synthetic-only','subnet':'synthetic-only','image':'synthetic-only','image_id':'123','network_mode':'internal-offline','gcloud_configuration':'synthetic-only','gcloud_account':'synthetic-only','global_deadline_utc':(now+timedelta(hours=60)).isoformat(),'budget_cap_usd':'100','reserve_usd':'20','prior_spend_usd':'48.8','stages':[stage],'price_snapshot':{'machines':{'n2-highmem-2':{'vcpus':2,'memory_gib':16,'zone':'us-central1-a'}}}}
 return c,stage

def vm(mod,c,s,e,expiry):
 return {'name':e['instance_name'],'labels':mod.labels(c,s),'machineType':'zones/us-central1-a/machineTypes/n2-highmem-2','zone':'zones/us-central1-a','scheduling':{'provisioningModel':'STANDARD','instanceTerminationAction':'DELETE','terminationTime':expiry,'automaticRestart':False,'onHostMaintenance':'TERMINATE'},'serviceAccounts':[],'networkInterfaces':[{'nicType':'GVNIC','subnetwork':c['subnet']}],'metadata':{'items':[{'key':k,'value':v}for k,v in {'dams-source-commit':c['source_commit'],'dams-source-image-id':c['image_id'],'enable-oslogin':'TRUE','block-project-ssh-keys':'TRUE'}.items()]},'disks':[{'boot':True,'autoDelete':True,'interface':'SCSI'}]}

class Controls(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory(dir=O.parent);self.root=Path(self.tmp.name);self.now=datetime(2026,10,12,11,39,55,187908,tzinfo=timezone.utc)
 def tearDown(self):self.tmp.cleanup()
 def reserve(self,mod,name='ledger',now=None,c=None,s=None):
  now=now or self.now
  if c is None:c,s=config(now)
  with patch.object(mod,'stage_cost',return_value=Decimal('24.88957321694587')):return mod.Ledger(self.root/(name+'.json'),c).reserve(s,now=now)
 def test_synthetic_92_microsecond_rounding_refused_by_unchanged_exact_validator(self):
  c,s=config(self.now);entry=self.reserve(old,'old',c=c,s=s)
  self.assertEqual(entry['termination_utc'],'2026-10-14T16:39:55.187908Z')
  observed=vm(old,c,s,entry,'2026-10-14T16:39:55.188Z')
  for mod in (old,new):
   with self.assertRaisesRegex(mod.GuardError,'absolute time differs'):mod.validate_compute_only_instance(c,s,entry,observed)
 def test_future_floor_and_exact_native_validator_accept_no_tolerance(self):
  c,s=config(self.now);entry=self.reserve(new,c=c,s=s);self.assertEqual(entry['termination_utc'],'2026-10-14T16:39:55.187000Z')
  observed=vm(new,c,s,entry,'2026-10-14T16:39:55.187Z');self.assertIs(new.validate_compute_only_instance(c,s,entry,observed),observed)
  # A different future prospective clock floors to the actually observed .188
  # representation. These are synthetic new reservations, no live VM.
  now=self.now.replace(microsecond=188908);c,s=config(now);entry=self.reserve(new,'other-future',now=now,c=c,s=s);observed=vm(new,c,s,entry,'2026-10-14T16:39:55.188Z');self.assertIs(new.validate_compute_only_instance(c,s,entry,observed),observed)
  observed['scheduling']['terminationTime']='2026-10-14T16:39:55.188001Z'
  with self.assertRaises(new.GuardError):new.validate_compute_only_instance(c,s,entry,observed)
 def test_global_deadline_not_extended_and_full_stage_fit_still_strict(self):
  c,s=config(self.now);c['global_deadline_utc']=(self.now+timedelta(seconds=s['max_seconds'])).isoformat();entry=self.reserve(new,c=c,s=s)
  self.assertLessEqual(new.utc(entry['termination_utc']),new.utc(c['global_deadline_utc']))
  c['global_deadline_utc']=(self.now+timedelta(seconds=s['max_seconds'],microseconds=-1)).isoformat()
  with self.assertRaisesRegex(new.GuardError,'entire reserved stage'):self.reserve(new,'too-short',c=c,s=s)
 def test_existing_hold_attempts_and_clock_never_rewritten(self):
  c,s=config(self.now);entry=self.reserve(old,c=c,s=s)
  with patch.object(old,'stage_cost',return_value=Decimal('24.88957321694587')):old.Ledger(self.root/'ledger.json',c).event(s['id'],attempts=1,state='uncertain')
  before=(self.root/'ledger.json').read_bytes();resumed=self.reserve(new,c=c,s=s)
  self.assertEqual(resumed['termination_utc'],entry['termination_utc']);self.assertEqual(resumed['attempts'],1);self.assertEqual((self.root/'ledger.json').read_bytes(),before)
  c['budget_cap_usd']='101'
  with self.assertRaisesRegex(new.GuardError,'cannot be reset'):self.reserve(new,c=c,s=s)
 def test_full_source_reverse_ast_and_microsecond_range_floor(self):
  before=PRE_FLOOR_SOURCE;after=NEW_SOURCE;line=NORMALIZATION_LINE;self.assertEqual(ast.dump(ast.parse(after.replace(line,'')),include_attributes=False),ast.dump(ast.parse(before),include_attributes=False))
  for micros in (0,1,500,999,1000,187908,188000,999999):
   now=self.now.replace(microsecond=micros);entry=self.reserve(new,str(micros),now=now);expiry=new.utc(entry['termination_utc']);bound=now+timedelta(seconds=190800);self.assertEqual(expiry.microsecond%1000,0);self.assertGreaterEqual((bound-expiry).total_seconds(),0);self.assertLess((bound-expiry).total_seconds(),.001)
if __name__=='__main__':unittest.main(verbosity=2)
