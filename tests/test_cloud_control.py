"""No test creates cloud resources; verify guards and real local CLI failures."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research_tools"))
from cloud_control import (GuardError, Ledger, Gcloud, checked_config, create_command, stage_cost, stamp,
                           package_source, preflight_quotas, reconcile_stage, validate_instance,
                           verify_boot_disk, preflight_environment, labels)


def fixture():
    now = datetime.now(timezone.utc)
    return {"version": 1, "authorization_id": "test-authorization", "project": "fictional-research", "dedicated_project":True,
            "region": "us-central1", "zones": ["us-central1-a", "us-central1-b"], "bucket": "fictional-bucket",
            "service_account": "worker@fictional-research.iam.gserviceaccount.com", "subnet": "fictional-subnet",
            "image": "https://compute.googleapis.com/compute/v1/projects/debian-cloud/global/images/debian-fixed",
            "image_id":"12345", "network_mode":"internal-offline", "image_dependencies_preinstalled":True,
            "gcloud_configuration":"test-config", "gcloud_account":"test@example.invalid",
            "source_commit": "a" * 40, "global_deadline_utc": stamp(now + timedelta(hours=4)),
            "budget_cap_usd": "7", "reserve_usd": "2", "prior_spend_usd": "0.1", "outbound_access_verified": True,
            "price_snapshot": {"region": "us-central1", "currency": "USD", "checked_utc": stamp(now),
                "sources": ["https://cloud.google.com/example-test-fixture"], "spot_vm_usd_per_hour": {"c4d-highmem-96": "2"},
                "hyperdisk_gib_hour": "0.0001", "gcs_gib_hour": "0.0001", "egress_gib": "0.12", "gcs_class_a_per_1000":"0.005"},
            "stages": [{"id": "test-stage", "machine_type": "c4d-highmem-96", "max_seconds": 1800,
                        "spec": "validation", "scale": 120, "runtime_limits": {"version": 1},
                        "max_result_gib": 1, "max_egress_gib": 1, "storage_operations_usd_upper": "0.01", "other_usd_upper": "0.01"}]}


class CloudControlTests(unittest.TestCase):
    def test_bootstrap_refuses_tracked_and_untracked_source_but_allows_ignored_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); checkout=base/'checkout';checkout.mkdir()
            (checkout/'run.sh').write_text('#!/bin/bash\necho verified-entry\n')
            (checkout/'.gitignore').write_text('runs/\n.venv/\n')
            def git(*args):
                return subprocess.run(['git','-C',str(checkout),*args],check=True,capture_output=True,text=True).stdout.strip()
            git('init','-q');git('add','run.sh','.gitignore')
            git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture')
            commit=git('rev-parse','HEAD');install=base/'dams-simulation'/commit;install.parent.mkdir()
            checkout.rename(install)
            env={**os.environ,'XDG_DATA_HOME':str(base)}
            command=['/bin/bash',str(ROOT/'bootstrap.sh'),'--commit',commit]
            (install/'runs').mkdir();(install/'runs'/'ignored-result.json').write_text('{}')
            clean=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(clean.returncode,0,clean.stderr);self.assertIn('verified-entry',clean.stdout)
            (install/'shadow_module.py').write_text('raise RuntimeError("untracked source")\n')
            untracked=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(untracked.returncode,2);self.assertIn('untracked source',untracked.stderr)
            self.assertNotIn('verified-entry',untracked.stdout);self.assertTrue((install/'shadow_module.py').exists())
            (install/'shadow_module.py').unlink();(install/'run.sh').write_text('#!/bin/bash\necho modified-entry\n')
            modified=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(modified.returncode,2);self.assertNotIn('modified-entry',modified.stdout)

    def test_entire_future_plan_source_and_reservations_are_immutable(self):
        c=fixture();c['stages'].append({**c['stages'][0],'id':'stage-two'})
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(Path(tmp)/'ledger.json',c);ledger.reserve(c['stages'][0]);ledger.settle('test-stage',True,True,True)
            import copy
            for change in ('source','future'):
                mutated=copy.deepcopy(c)
                if change=='source':mutated['source_commit']='b'*40
                else:mutated['stages'][1]['scale']=1000
                with self.assertRaises(GuardError):Ledger(ledger.path,mutated).reserve(mutated['stages'][1])
        for field in ('disk_retention_hours','storage_retention_hours'):
            invalid=fixture();invalid['stages'][0][field]=0
            with self.assertRaises(GuardError):checked_config(invalid)
        from cloud_control import execute
        with patch('cloud_control.Gcloud') as sdk:
            with self.assertRaises(GuardError):execute(fixture(),'/unused-test-only')
            sdk.assert_not_called()

    def test_failed_stage_cannot_settle_or_escalate(self):
        c=fixture();c['stages'].append({**c['stages'][0], 'id':'stage-two'})
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(Path(tmp)/'ledger.json',c);ledger.reserve(c['stages'][0])
            ledger.event('test-stage',state='uncertain',failure='interrupted')
            for absent,archived,science in [(True,False,False),(True,True,False),(False,True,True)]:
                with self.assertRaises(GuardError): ledger.settle('test-stage',absent,archived,science)
            self.assertEqual(json.loads(ledger.path.read_text())['entries']['test-stage']['state'],'uncertain')
            with self.assertRaises(GuardError): ledger.reserve(c['stages'][1])

    def test_named_account_and_monotone_private_command_log(self):
        c=fixture()
        with tempfile.TemporaryDirectory() as tmp,patch('cloud_control.subprocess.run') as run:
            run.return_value=subprocess.CompletedProcess([],0,'[]','')
            g=Gcloud(c,tmp);g.run(['gcloud','compute','instances','list','--project='+c['project']])
            g=Gcloud(c,tmp);g.run(['gcloud','compute','disks','list','--project='+c['project']])
            for call in run.call_args_list:
                self.assertIn('--configuration=test-config',call.args[0]);self.assertIn('--account=test@example.invalid',call.args[0])
            self.assertEqual(len(list(Path(tmp).glob('command-*.json'))),2)

    def test_pending_and_zero_quota_are_not_grants(self):
        c=fixture();c['price_snapshot']['machines']={'c4d-highmem-96':{'vcpus':96}}
        class API:
            def __init__(self,spot):self.spot=spot;self.calls=[]
            def run(self,args):
                self.calls.append(args)
                if 'project-info' in args:return {'exit':0,'stdout':json.dumps({'quotas':[{'metric':'CPUS_ALL_REGIONS','limit':384,'usage':0}]})}
                spot=any('PREEMPTIBLE' in a for a in args)
                dimensions={'region':'us-central1'} if spot else {'region':'us-central1','vm_family':'C4D'}
                return {'exit':0,'stdout':json.dumps({'reconciling':True,'preferredValue':384,'dimensionsInfos':[{'dimensions':dimensions,'details':{'value':self.spot if spot else 384}}]})}
        zero=API(0)
        with self.assertRaises(GuardError):preflight_quotas(zero,c)
        self.assertFalse(any('create' in a or 'cp' in a for a in zero.calls))
        preflight_quotas(API(384),c)
        # A small standard C4D quota must not block an approved separate Spot
        # pool. Conversely, ample standard quota cannot replace empty Spot.
        approved=API(384);preflight_quotas(approved,c)
        self.assertFalse(any(any('CPUS-PER-VM-FAMILY' in x for x in a) for a in approved.calls))

    def test_ambiguous_creation_and_recovery_keep_expiry_and_attempts(self):
        c=fixture();s=c['stages'][0]
        class API:
            def __init__(self,operations,disks=[]):self.operations=operations;self.disks=disks
            def inventory(self):return []
            def run(self,args):return {'exit':0,'stdout':json.dumps(self.operations if 'operations' in args else self.disks)}
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(Path(tmp)/'ledger.json',c);e=ledger.reserve(s)
            e=ledger.event(s['id'],state='uncertain',attempts=1)
            for ops in ([],[{'status':'PENDING'}]):
                with self.assertRaises(GuardError):reconcile_stage(API(ops),c,s,e,ledger)
            with self.assertRaises(GuardError):reconcile_stage(API([{'status':'DONE'}],[{'name':e['instance_name']}]),c,s,e,ledger)
            reconcile_stage(API([{'status':'DONE'}]),c,s,e,ledger)
            saved=ledger.reserve(s)
            self.assertEqual(saved['termination_utc'],e['termination_utc']);self.assertEqual(saved['attempts'],1)

    def test_existing_vm_credentials_network_and_disk_are_bound(self):
        c=fixture();s=c['stages'][0];e={'instance_name':'dams-test','termination_utc':c['global_deadline_utc']}
        vm={'name':e['instance_name'],'labels':labels(c,s),'machineType':'x/'+s['machine_type'],
            'scheduling':{'provisioningModel':'SPOT','instanceTerminationAction':'DELETE','terminationTime':e['termination_utc']},
            'serviceAccounts':[{'email':c['service_account'],'scopes':['https://www.googleapis.com/auth/devstorage.read_write']}],
            'networkInterfaces':[{'nicType':'GVNIC','subnetwork':'x/'+c['subnet']}],
            'metadata':{'items':[{'key':k,'value':v} for k,v in {'dams-source-commit':c['source_commit'],'dams-source-image-id':c['image_id'],'enable-oslogin':'TRUE','block-project-ssh-keys':'TRUE'}.items()]},
            'disks':[{'boot':True,'autoDelete':True,'source':'x/dams-test'}],'zone':'x/us-central1-a'}
        validate_instance(c,s,e,vm)
        for key,value in [('serviceAccounts',[]),('networkInterfaces',[{'nicType':'VIRTIO_NET','subnetwork':'x/fictional-subnet'}]),('disks',[])]:
            with self.assertRaises(GuardError):validate_instance(c,s,e,{**vm,key:value})
        class API:
            def run(self,args):return {'exit':0,'stdout':json.dumps({'sourceImageId':'999','sourceImage':c['image'],'type':'x/hyperdisk-balanced','sizeGb':50,'provisionedIops':3000,'provisionedThroughput':140})}
        with self.assertRaises(GuardError):verify_boot_disk(API(),c,s,vm)

    def test_actual_firewall_api_inventory_not_boolean(self):
        c=fixture();c['network_mode']='iap-ephemeral-ip';c['restricted_firewall_verified']=True
        network='https://compute.googleapis.com/compute/v1/projects/fictional-research/global/networks/research'
        rules=[]
        for direction,allow,ranges,ports in [('INGRESS',True,['35.235.240.0/20'],['22']),('EGRESS',True,['0.0.0.0/0'],['443']),('INGRESS',False,['0.0.0.0/0'],None),('EGRESS',False,['0.0.0.0/0'],None)]:
            rule={'network':network,'targetServiceAccounts':[c['service_account']],'direction':direction,'priority':1000 if allow else 2000}
            rule['sourceRanges' if direction=='INGRESS' else 'destinationRanges']=ranges
            rule['allowed' if allow else 'denied']=[{'IPProtocol':'tcp','ports':ports}] if allow else [{'IPProtocol':'all'}]
            rules.append(rule)
        class API:
            def run(self,args):
                if 'get-iam-policy' in args:
                    d={'bindings':[{'role':'roles/storage.objectUser','members':['serviceAccount:'+c['service_account']]}]} if 'buckets' in args else {'bindings':[]}
                elif 'buckets' in args:d={'name':c['bucket'],'projectNumber':'1','location':'US-CENTRAL1','storageClass':'STANDARD','iamConfiguration':{'uniformBucketLevelAccess':{'enabled':True},'publicAccessPrevention':'enforced'},'softDeletePolicy':{'retentionDurationSeconds':'0'}}
                elif 'projects' in args:d={'projectId':c['project'],'projectNumber':'1','labels':{'dams-task':labels(c,c['stages'][0])['dams-task']}}
                elif 'images' in args:d={'id':c['image_id'],'selfLink':c['image'],'status':'READY','architecture':'X86_64'}
                elif 'subnets' in args:d={'selfLink':'x/subnet','region':'x/us-central1','privateIpGoogleAccess':True,'network':network}
                else:d=rules
                return {'exit':0,'stdout':json.dumps(d)}
        self.assertEqual(preflight_environment(API(),c)['firewall_rules'],4)
        rules[0]['allowed'][0]['ports']=['22','3389']
        with self.assertRaises(GuardError):preflight_environment(API(),c)

    def test_cost_and_all_stage_reserve(self):
        c = fixture()
        self.assertEqual(stage_cost(c, c['stages'][0]), Decimal('1.39136160'))
        checked_config(c)
        c['stages'] = [{**c['stages'][0], 'id': f'stage-{i}'} for i in range(4)]
        with self.assertRaises(GuardError): checked_config(c)

    def test_global_deadline_and_margin_not_reset(self):
        c = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp)/'ledger.json', c)
            first = ledger.reserve(c['stages'][0])
            self.assertEqual(first, ledger.reserve(c['stages'][0]))
            modified = {**c, 'global_deadline_utc': stamp(datetime.now(timezone.utc)+timedelta(days=1))}
            with self.assertRaises(GuardError): Ledger(Path(tmp)/'ledger.json',modified).reserve(c['stages'][0])
            with self.assertRaises(GuardError): ledger.reserve({**c['stages'][0], 'scale':1000})
            with self.assertRaises(GuardError): ledger.reserve({**c['stages'][0], 'id':'stage-two'})
            with self.assertRaises(GuardError): ledger.settle('test-stage',False,False)
            with self.assertRaises(GuardError): ledger.settle('test-stage',True,False)
            with self.assertRaises(GuardError): ledger.settle('test-stage',True,True)
            ledger.settle('test-stage',True,True,science_verified=True)
            saved=json.loads((Path(tmp)/'ledger.json').read_text())
            self.assertEqual(saved['entries']['test-stage']['charged_usd_upper'], first['reserved_usd'])

    def test_money_price_and_expiry_refusals(self):
        for key,value in [('budget_cap_usd','NaN'),('cost_margin','0.5'),('outbound_access_verified',False)]:
            c=fixture();c[key]=value
            with self.assertRaises(GuardError): checked_config(c)
        c=fixture();c['price_snapshot']['checked_utc']=stamp(datetime.now(timezone.utc)-timedelta(days=2))
        with self.assertRaises(GuardError): checked_config(c)
        c=fixture();c['global_deadline_utc']=stamp(datetime.now(timezone.utc)-timedelta(seconds=1))
        with self.assertRaises(GuardError): checked_config(c)

    def test_real_cli_help_and_no_paid_default(self):
        r=subprocess.run(['/bin/bash','run.sh','--help'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(r.returncode,0);self.assertIn('bounded local validation',r.stdout)
        r=subprocess.run(['/bin/bash','run.sh','--scale','nan'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(r.returncode,2)
        r=subprocess.run(['/bin/bash','bootstrap.sh'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(r.returncode,2);self.assertIn('full published release commit',r.stderr)

    def test_absolute_spot_delete_hyperdisk_restricted_command(self):
        c=fixture();s=c['stages'][0]
        cmd=create_command(c,s,{'instance_name':'dams-test','termination_utc':c['global_deadline_utc']},c['zones'][0],'/tmp/startup')
        self.assertIn('--provisioning-model=SPOT',cmd)
        self.assertIn('--instance-termination-action=DELETE',cmd)
        self.assertIn('--termination-time='+c['global_deadline_utc'],cmd)
        self.assertIn('--boot-disk-provisioned-iops=3000',cmd)
        self.assertIn('--boot-disk-provisioned-throughput=140',cmd)
        self.assertIn('--network-interface=subnet=fictional-subnet,nic-type=GVNIC,no-address',cmd)
        self.assertFalse(any('key-file' in x or 'image-family' in x or 'tier-1' in x for x in cmd))

    def test_fixed_clean_allowlist_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)/'repo';repo.mkdir()
            for name in ('run.sh','uv.lock','pyproject.toml','private.txt'): (repo/name).write_text(name)
            for args in (['init','-q'],['add','.'],['-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture']):
                subprocess.run(['git',*args],cwd=repo,check=True)
            commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
            result=package_source(repo,commit,Path(tmp)/'source.tar')
            self.assertEqual(result['files'],3)
            self.assertNotIn('private.txt',json.loads((Path(tmp)/'source.manifest.json').read_text())['source_files_sha256'])
            (repo/'run.sh').write_text('changed')
            with self.assertRaises(GuardError): package_source(repo,commit,Path(tmp)/'changed.tar')


if __name__ == '__main__': unittest.main()
