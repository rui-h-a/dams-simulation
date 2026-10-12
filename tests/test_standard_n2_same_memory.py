"""Portable pure operational N2 capability checks; no Model/SQL/provider/auth."""
import ast,copy,sys,types,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'research_tools')];sys.dont_write_bytecode=True
from research_tools import cloud_control as cc,cloud_worker as cw,compute_only_worker as worker
SHAPES={'n2-highmem-2':(2,16),'n2-highmem-4':(4,32),'n2-standard-4':(4,16)}
def fixture(machine='n2-standard-4'):
 cpu,mem=SHAPES[machine];region='us-central1';zone=region+'-a'
 c={'authorization_id':'SYNTHETIC-NO-AUTHORITY','source_commit':'a'*40,'region':region,'zones':[zone],'subnet':'synthetic-only','network_mode':'internal-offline','image_id':'7','price_snapshot':{'machines':{machine:{'vcpus':cpu,'memory_gib':mem,'zone':zone}}}}
 s={'id':'synthetic-shape','purchase_mode':'STANDARD','machine_type':machine,'disk_type':'pd-standard','architecture_declaration':'official-n2-x86_64','expected_guest':{'architecture':'x86_64','vcpus':cpu,'memory_gib_min':str(mem-1),'memory_gib_max':str(mem)},'quota':{'metric':'N2_CPUS','info_id':'N2-CPUS-per-project-region','dimensions':{'region':region}}}
 r={k:s[k]for k in ('purchase_mode','machine_type','expected_guest')}
 d={'machine_type':machine,'guest_scheduling':{'preemptible':'FALSE','automatic_restart':'FALSE','on_host_maintenance':'TERMINATE'},'guest_architecture':'x86_64','guest_cpu_count':cpu,'guest_cpu_affinity':list(range(cpu)),'guest_memory_total_bytes':int((mem-.5)*2**30),'guest_memory_total_source':'proc-meminfo','guest_boot_disk_metadata_interface':'SCSI','guest_root_block':{'interface':'SCSI'},'instance_id':'SYNTHETIC','zone':zone,'boot_disk_device_name':'SYNTHETIC','guest_os_release_sha256':'b'*64,'guest_root_filesystem_bytes':550*2**30}
 return c,s,r,d
class Controls(unittest.TestCase):
 def test_three_native_catalog_and_guest_shapes(self):
  for machine in SHAPES:
   c,s,r,d=fixture(machine);before=copy.deepcopy((c,s,r,d));caps=cc.stage_capabilities(c,s);self.assertEqual(caps['disk_interface'],'SCSI');self.assertEqual(caps['quota']['metric'],'N2_CPUS');self.assertTrue(cw.verify_guest(r,d)['hardware_frozen_verified']);self.assertEqual((c,s,r,d),before)
 def test_wrong_catalog_cpu_ram_zone_rejected(self):
  for k,v in [('vcpus',2),('memory_gib',32),('zone','us-east1-a')]:
   c,s,_,_=fixture();c['price_snapshot']['machines']['n2-standard-4'][k]=v
   with self.assertRaises(cc.GuardError):cc.stage_capabilities(c,s)
 def test_guest_wrong_cpu_ram_catalog_units_rejected(self):
  for k,v in [('guest_cpu_count',2),('guest_cpu_affinity',[0,1]),('guest_memory_total_bytes',32*2**30),('guest_memory_total_bytes',14*2**30),('guest_architecture','arm64')]:
   _,_,r,d=fixture();d[k]=v
   with self.assertRaises(cw.GuardError):cw.verify_guest(r,d)
  _,_,r,d=fixture();r['expected_guest']['memory_gib_max']='32'
  with self.assertRaises(cw.GuardError):cw.verify_guest(r,d)
 def test_disk_and_purchase_mode_cannot_fallback(self):
  for mode in ('SPOT','OTHER',None):
   c,s,r,d=fixture();s['purchase_mode']=mode;r['purchase_mode']=mode
   with self.assertRaises(cc.GuardError):cc.stage_capabilities(c,s)
   with self.assertRaises(cw.GuardError):cw.verify_guest(r,d)
  c,s,r,d=fixture();s['disk_type']='hyperdisk-balanced'
  with self.assertRaises(cc.GuardError):cc.stage_capabilities(c,s)
  for key in ('guest_boot_disk_metadata_interface','guest_root_block'):
   _,_,r,d=fixture();d[key]='NVME' if key!='guest_root_block' else {'interface':'NVME'}
   with self.assertRaises(cw.GuardError):cw.verify_guest(r,d)
 def test_original_compute_worker_native_consumer(self):
  _,_,r,d=fixture();r['expected_identity']={k:d[k]for k in ('instance_id','zone','boot_disk_device_name','guest_os_release_sha256')};r['expected_identity']['boot_disk_bytes_min']=d['guest_root_filesystem_bytes'];r['provider_identity_sha256']='c'*64;self.assertTrue(worker.verify_identity(r,d)['hardware_frozen_verified'])
  d['guest_cpu_count']=2
  with self.assertRaises(worker.GuardError):worker.verify_identity(r,d)
 def test_original_vm_validation_catalog_consumer_exact(self):
  c,s,_,_=fixture();e={'instance_name':'SYNTHETIC','termination_utc':'2030-01-01T00:00:00.001Z'}
  v={'name':e['instance_name'],'labels':cc.labels(c,s),'machineType':'zones/us-central1-a/machineTypes/n2-standard-4','zone':'zones/us-central1-a','scheduling':{'provisioningModel':'STANDARD','instanceTerminationAction':'DELETE','terminationTime':e['termination_utc'],'automaticRestart':False,'onHostMaintenance':'TERMINATE'},'serviceAccounts':[],'networkInterfaces':[{'nicType':'GVNIC','subnetwork':c['subnet']}],'metadata':{'items':[{'key':k,'value':value}for k,value in {'dams-source-commit':c['source_commit'],'dams-source-image-id':c['image_id'],'enable-oslogin':'TRUE','block-project-ssh-keys':'TRUE'}.items()]},'disks':[{'boot':True,'autoDelete':True,'interface':'SCSI'}]}
  self.assertIs(cc.validate_compute_only_instance(c,s,e,v),v);v['disks'][0]['interface']='NVME'
  with self.assertRaises(cc.GuardError):cc.validate_compute_only_instance(c,s,e,v)
 def test_reverse_AST_shows_only_capability_functions_changed(self):
  for filename,name in [('cloud_control.py','stage_capabilities'),('cloud_worker.py','verify_guest')]:
   text=(ROOT/'research_tools'/filename).read_text();oldtext=text.replace(', "n2-standard-4": (4, 16)','').replace(', "n2-standard-4"','').replace(", 'n2-standard-4': 16",'').replace(", 'n2-standard-4'",'');old={n.name:ast.dump(n,include_attributes=False)for n in ast.parse(oldtext).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))};new={n.name:ast.dump(n,include_attributes=False)for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))};self.assertEqual([n for n in old if old[n]!=new[n]],[name])
if __name__=='__main__':unittest.main(verbosity=2)
