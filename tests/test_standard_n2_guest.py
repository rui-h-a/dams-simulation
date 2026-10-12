"""Pure native Standard N2 guest capability controls; no Model/SQL/provider/auth."""
from pathlib import Path
import ast,copy,importlib.util,sys,types,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research_tools')];sys.dont_write_bytecode=True
from research_tools import cloud_worker as cw,compute_only_worker as worker
SOURCE=(ROOT/'research_tools/cloud_worker.py').read_text()
def prefloor_hardware_source(text):
 return text.replace("('c4d-highmem-4', 'n2-highmem-2', 'n2-highmem-4',","('c4d-highmem-4', 'n2-highmem-4',").replace("'c4d-highmem-4': 31, 'n2-highmem-2': 16, 'n2-highmem-4': 32","'c4d-highmem-4': 31, 'n2-highmem-4': 32").replace("interface = 'SCSI' if machine in ('n2-highmem-2', 'n2-highmem-4') else 'NVME'","interface = 'SCSI' if machine == 'n2-highmem-4' else 'NVME'")

def fixture(cpu=2):
 memory=8*cpu;machine='n2-highmem-'+str(cpu)
 runtime={'purchase_mode':'STANDARD','machine_type':machine,'expected_guest':{'architecture':'x86_64','vcpus':cpu,'memory_gib_min':str(memory-1),'memory_gib_max':str(memory)}}
 d={'machine_type':machine,'guest_scheduling':{'preemptible':'FALSE','automatic_restart':'FALSE','on_host_maintenance':'TERMINATE'},'guest_architecture':'x86_64','guest_cpu_count':cpu,'guest_cpu_affinity':list(range(cpu)),'guest_memory_total_bytes':int((memory-.5)*2**30),'guest_memory_total_source':'proc-meminfo','guest_boot_disk_metadata_interface':'SCSI','guest_root_block':{'interface':'SCSI'},'instance_id':'SYNTHETIC-only','zone':'us-central1-a','boot_disk_device_name':'SYNTHETIC-root','guest_os_release_sha256':'a'*64,'guest_root_filesystem_bytes':550*2**30}
 return runtime,d
class Controls(unittest.TestCase):
 def test_n2_two_and_four_native_guest_verified(self):
  for cpu in (2,4):
   r,d=fixture(cpu);before=copy.deepcopy((r,d));v=cw.verify_guest(r,d);self.assertTrue(v['hardware_frozen_verified']);self.assertEqual(v['guest_cpu_count'],cpu);self.assertEqual((r,d),before)
 def test_original_n2_two_rejection_counterexample_preserved(self):
  m=types.ModuleType('synthetic_old_n2_guest');m.__file__=str(ROOT/'research_tools/cloud_worker.py');exec(compile(prefloor_hardware_source(SOURCE),'synthetic_old_n2_guest','exec'),m.__dict__);r,d=fixture()
  with self.assertRaisesRegex(m.GuardError,'unsupported or inconsistent'):m.verify_guest(r,d)
 def test_wrong_cpu_or_affinity_rejected(self):
  for k,v in [('guest_cpu_count',4),('guest_cpu_affinity',[0]),('guest_cpu_affinity',[0,0]),('guest_architecture','arm64')]:
   r,d=fixture();d[k]=v
   with self.assertRaises(cw.GuardError):cw.verify_guest(r,d)
 def test_wrong_ram_catalog_and_measured_units_rejected(self):
  for value in ('32','15','0','NaN'):
   r,d=fixture();r['expected_guest']['memory_gib_max']=value
   with self.assertRaises(cw.GuardError):cw.verify_guest(r,d)
  for value in (14*2**30,17*2**30,float(15.5*2**30)):
   r,d=fixture();d['guest_memory_total_bytes']=value
   with self.assertRaises(cw.GuardError):cw.verify_guest(r,d)
 def test_wrong_disk_metadata_or_root_rejected(self):
  for key in ('guest_boot_disk_metadata_interface','guest_root_block'):
   r,d=fixture();d[key]='NVME' if key!='guest_root_block' else {'interface':'NVME'}
   with self.assertRaises(cw.GuardError):cw.verify_guest(r,d)
 def test_no_purchase_mode_or_machine_fallback(self):
  for mode in ('SPOT','OTHER',None):
   r,d=fixture();r['purchase_mode']=mode
   with self.assertRaises(cw.GuardError):cw.verify_guest(r,d)
  r,d=fixture();d['machine_type']='n2-highmem-4'
  with self.assertRaises(cw.GuardError):cw.verify_guest(r,d)
 def test_original_compute_worker_consumes_same_native_guest(self):
  r,d=fixture();r['expected_identity']={k:d[k]for k in ('instance_id','zone','boot_disk_device_name','guest_os_release_sha256')};r['expected_identity']['boot_disk_bytes_min']=d['guest_root_filesystem_bytes'];r['provider_identity_sha256']='b'*64
  self.assertTrue(worker.verify_identity(r,d)['hardware_frozen_verified'])
  d['instance_id']='foreign'
  with self.assertRaises(worker.GuardError):worker.verify_identity(r,d)
 def test_all_other_functions_AST_unchanged(self):
  original=ast.parse(prefloor_hardware_source(SOURCE));candidate=ast.parse(SOURCE)
  before={n.name:ast.dump(n,include_attributes=False)for n in original.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))};after={n.name:ast.dump(n,include_attributes=False)for n in candidate.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))};self.assertEqual(set(before),set(after));self.assertEqual([n for n in before if before[n]!=after[n]],['verify_guest'])
if __name__=='__main__':unittest.main(verbosity=2)
