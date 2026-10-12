"""Pure source-bound N2 profile delta controls: no Model/SQL/provider imports."""
import ast
import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[1]
BASELINE = SOURCE/'tests/fixtures/n2_capability'

def extract(path, names):
    tree = ast.parse(path.read_bytes(), filename=str(path))
    nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    env = dict(datetime=datetime, timezone=timezone, Decimal=Decimal,
               InvalidOperation=InvalidOperation, hashlib=hashlib, json=json, re=re)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), env)
    return env

C = extract(SOURCE/'research_tools/cloud_control.py', ('GuardError', 'stage_capabilities',
    'stage_vm_rate', 'stage_termination_action', '_create_command', '_validate_instance',
    'money', 'utc', 'labels', 'digest'))
W = extract(SOURCE/'research_tools/cloud_worker.py', ('verify_guest',))
W['GuardError'] = C['GuardError']
ERROR = C['GuardError']


def fixtures(cpu=2, disk='pd-balanced'):
    machine = 'n2-highmem-' + str(cpu)
    memory = cpu*8
    c = {'region':'asia-east1', 'zones':['asia-east1-b'], 'execution_mode':'compute-only-iap',
         'price_snapshot':{'machines':{machine:{'vcpus':cpu,'memory_gib':memory,'zone':'asia-east1-b'}},
                           'standard_vm_usd_per_hour':{machine:str(Decimal('.036602')*cpu+Decimal('.004906')*memory)}},
         'global_deadline_utc':'2026-10-15T08:54:46.960873Z', 'project':'fixture-project',
         'image':'fixture-image','image_id':'123','subnet':'fixture-subnet','network_mode':'internal-offline',
         'source_commit':'a'*40,'authorization_id':'fixture-authority','service_account':'fixture-sa'}
    expected={'architecture':'x86_64','vcpus':cpu,'memory_gib_min':memory-1,'memory_gib_max':memory}
    s={'id':'fixture-n2-stage','machine_type':machine,'purchase_mode':'STANDARD','disk_type':disk,
       'disk_gib':50,'architecture_declaration':'official-n2-x86_64',
       'quota':{'metric':'N2_CPUS','info_id':'N2-CPUS-per-project-region','dimensions':{'region':c['region']}},
       'expected_guest':expected}
    r={'machine_type':machine,'purchase_mode':'STANDARD','expected_guest':copy.deepcopy(expected)}
    m={'machine_type':machine,'guest_scheduling':{'preemptible':'FALSE','automatic_restart':'FALSE','on_host_maintenance':'TERMINATE'},
       'guest_architecture':'x86_64','guest_cpu_count':cpu,'guest_cpu_affinity':list(range(cpu)),
       'guest_memory_total_bytes':int((Decimal(memory)-Decimal('.5'))*2**30),'guest_memory_total_source':'fixture/proc/meminfo',
       'guest_boot_disk_metadata_interface':'SCSI','guest_root_block':{'interface':'SCSI'}}
    e={'instance_name':'fixture-n2','termination_utc':'2026-10-12T12:00:00Z'}
    vm={'name':e['instance_name'],'labels':C['labels'](c,s),'machineType':'fixture/'+machine,'zone':'fixture/asia-east1-b',
        'scheduling':{'provisioningModel':'STANDARD','instanceTerminationAction':'DELETE','terminationTime':e['termination_utc'],
                      'automaticRestart':False,'onHostMaintenance':'TERMINATE'},'serviceAccounts':[],
        'networkInterfaces':[{'nicType':'GVNIC','subnetwork':'fixture/fixture-subnet'}],
        'metadata':{'items':[{'key':k,'value':v} for k,v in {'dams-source-commit':c['source_commit'],'dams-source-image-id':c['image_id'],
                    'enable-oslogin':'TRUE','block-project-ssh-keys':'TRUE'}.items()]},
        'disks':[{'boot':True,'autoDelete':True,'interface':'SCSI'}]}
    return c,s,r,m,e,vm

class DeltaControls(unittest.TestCase):
    def test_01_valid_n2_two_cpu_and_both_existing_disks(self):
        for disk in ('pd-balanced','pd-ssd'):
            with self.subTest(disk=disk):
                c,s,r,m,e,vm=fixtures(disk=disk)
                caps=C['stage_capabilities'](c,s)
                self.assertEqual(caps,{'purchase_mode':'STANDARD','disk_type':disk,'disk_interface':'SCSI','quota':s['quota']})
                self.assertTrue(W['verify_guest'](r,m)['hardware_frozen_verified'])
    def test_02_old_n2_four_profile_unchanged(self):
        c,s,r,m,e,vm=fixtures(4)
        oldC=extract(BASELINE/'cloud_control.py.txt',('GuardError','money','stage_capabilities'))
        oldW=extract(BASELINE/'cloud_worker.py.txt',('verify_guest',));oldW['GuardError']=ERROR
        self.assertEqual(C['stage_capabilities'](c,s),oldC['stage_capabilities'](c,s))
        self.assertEqual(W['verify_guest'](r,m),oldW['verify_guest'](r,m))
    def test_03_old_two_cpu_refusal_is_preserved_history(self):
        c,s,*_=fixtures();old=extract(BASELINE/'cloud_control.py.txt',('GuardError','money','stage_capabilities'))
        with self.assertRaises(old['GuardError']):old['stage_capabilities'](c,s)
    def test_04_wrong_catalog_shape(self):
        for field,val in (('vcpus',4),('vcpus',True),('memory_gib',32),('zone','us-central1-a'),('architecture','ARM64')):
            with self.subTest(field=field,value=val):
                c,s,*_=fixtures();c['price_snapshot']['machines'][s['machine_type']][field]=val
                with self.assertRaises(ERROR):C['stage_capabilities'](c,s)
    def test_05_wrong_or_missing_quota(self):
        for quota in (None,{'metric':'PREEMPTIBLE_CPUS'}, {'metric':'N2_CPUS','info_id':'N2-CPUS-per-project-region','dimensions':{'region':'us-central1'}}):
            with self.subTest(quota=quota):
                c,s,*_=fixtures()
                if quota is None:del s['quota']
                else:s['quota']=quota
                with self.assertRaises(ERROR):C['stage_capabilities'](c,s)
    def test_06_no_spot_or_other_n2_size_fallback(self):
        for machine,mode in (('n2-highmem-2','SPOT'),('n2-highmem-8','STANDARD'),('n2-highcpu-2','STANDARD')):
            c,s,*_=fixtures();s.update(machine_type=machine,purchase_mode=mode)
            with self.assertRaises(ERROR):C['stage_capabilities'](c,s)
    def test_07_disk_and_parallel_contract_refusal(self):
        for field,value in (('disk_type','hyperdisk-balanced'),('disk_iops',3000),('node_count',2),('node_count',True)):
            c,s,*_=fixtures();s[field]=value
            with self.assertRaises(ERROR):C['stage_capabilities'](c,s)
    def test_08_frozen_guest_fields_cannot_launder_4_cpu_ram(self):
        for field,value in (('vcpus',4),('vcpus',True),('memory_gib_max',32),('memory_gib_min',16),('architecture','aarch64')):
            c,s,*_=fixtures();s['expected_guest'][field]=value
            with self.assertRaises(ERROR):C['stage_capabilities'](c,s)
    def test_09_real_guest_cpu_ram_affinity_or_interface_refused(self):
        for field,value in (('machine_type','n2-highmem-4'),('guest_cpu_count',4),('guest_cpu_count',True),('guest_cpu_affinity',[0]),
                            ('guest_memory_total_bytes',32*2**30),('guest_boot_disk_metadata_interface','NVME'),('guest_root_block',{'interface':'NVME'})):
            c,s,r,m,*_=fixtures();m[field]=value
            with self.assertRaises(ERROR):W['verify_guest'](r,m)
    def test_10_nominal_ram_exactness_not_only_measured_range(self):
        c,s,r,m,*_=fixtures();r['expected_guest']['memory_gib_max']=32
        with self.assertRaises(ERROR):W['verify_guest'](r,m)
    def test_11_pure_create_argv_uses_exact_n2_profile_without_dispatch(self):
        c,s,r,m,e,vm=fixtures();argv=C['_create_command'](c,s,e,'asia-east1-b','fixture-startup',compute_only=True)
        for x in ('--machine-type=n2-highmem-2','--provisioning-model=STANDARD','--boot-disk-interface=SCSI',
                  '--boot-disk-type=pd-balanced','--no-service-account','--no-scopes','--instance-termination-action=DELETE',
                  '--termination-time='+e['termination_utc']):self.assertIn(x,argv)
        self.assertFalse(any(x.startswith('--service-account=') or x.startswith('--scopes=') for x in argv))
        legacy=C['_create_command'](c,s,e,'asia-east1-b','fixture-startup',compute_only=False)
        self.assertIn('--service-account=fixture-sa',legacy)
        self.assertIn('--scopes=https://www.googleapis.com/auth/devstorage.read_write',legacy)
    def test_12_pure_instance_readback_same_interface_and_mode(self):
        c,s,r,m,e,vm=fixtures();self.assertIs(C['_validate_instance'](c,s,e,vm,compute_only=True),vm)
        for field,value in (('machineType','fixture/n2-highmem-4'),):
            bad=copy.deepcopy(vm);bad[field]=value
            with self.assertRaises(ERROR):C['_validate_instance'](c,s,e,bad,compute_only=True)
        bad=copy.deepcopy(vm);bad['disks'][0]['interface']='NVME'
        with self.assertRaises(ERROR):C['_validate_instance'](c,s,e,bad,compute_only=True)
    def test_13_checked_standard_rate_required_no_spot_price_fallback(self):
        c,s,*_=fixtures();self.assertEqual(C['stage_vm_rate'](c,s),Decimal('.151700'))
        self.assertEqual(C['stage_vm_rate'](*fixtures(4)[:2]),Decimal('.303400'))
        del c['price_snapshot']['standard_vm_usd_per_hour'];c['price_snapshot']['spot_vm_usd_per_hour']={s['machine_type']:'.001'}
        with self.assertRaises(ERROR):C['stage_vm_rate'](c,s)
        self.assertEqual(Decimal('.1517')*48,Decimal('7.2816'))
    def test_15_explicit_N2_standard_disk_keeps_interface_and_existing_price_guard(self):
        for cpu in (2,4):
            c,s,r,m,e,vm=fixtures(cpu,disk='pd-standard')
            self.assertEqual(C['stage_capabilities'](c,s)['disk_interface'],'SCSI')
            argv=C['_create_command'](c,s,e,'asia-east1-b','fixture-startup',compute_only=True)
            self.assertIn('--boot-disk-type=pd-standard',argv)
            self.assertTrue(W['verify_guest'](r,m)['hardware_frozen_verified'])
            self.assertIs(C['_validate_instance'](c,s,e,vm,compute_only=True),vm)
        c,s,*_=fixtures(disk='pd-standard');s['disk_type']='local-ssd'
        with self.assertRaises(ERROR):C['stage_capabilities'](c,s)

    def test_14_module_AST_only_two_existing_functions_changed(self):
        for file,allowed in (('cloud_control.py',{'stage_capabilities'}),('cloud_worker.py',{'verify_guest'})):
            a=ast.parse((BASELINE/(file+'.txt')).read_bytes());b=ast.parse((SOURCE/'research_tools'/file).read_bytes())
            def rest(tree):return ast.dump(ast.Module(body=[n for n in tree.body if not isinstance(n,ast.FunctionDef) or n.name not in allowed],type_ignores=[]),include_attributes=False)
            self.assertEqual(rest(a),rest(b))
        before=json.loads((BASELINE/'source-sha256.json').read_bytes())['files']
        for name,e in before.items():
            if name in ('research_tools/cloud_control.py','research_tools/cloud_worker.py'):continue
            self.assertEqual(hashlib.sha256((SOURCE/name).read_bytes()).hexdigest(),e['sha256'],name)

if __name__=='__main__':unittest.main(verbosity=2)
