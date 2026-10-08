from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'research_tools'))
from cloud_cleanup import verify_archives,closeout,deletion_commands,inventory_commands,verify_retained_cloud_objects
from cloud_control import GuardError,digest,stamp
from tests.test_cloud_control import fixture

class CloudCleanupTests(unittest.TestCase):
    def test_authorization_flags_require_true_booleans_before_sdk(self):
        c=fixture();c.update(dedicated_project=True,paid_actions_authorized=True,abort_cleanup_authorized=True)
        for field in ('dedicated_project','paid_actions_authorized'):
            for value in ('false','true',1,False):
                with patch('cloud_cleanup.Gcloud') as sdk:
                    with self.assertRaises(GuardError):closeout({**c,field:value},'/unused',{},True)
                    sdk.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            roots=[Path(tmp)/n for n in ('a','b')]
            for p in roots:p.mkdir();(p/'partial').write_bytes(b'partial')
            receipt={'closure_mode':'abort','archive_roots':[str(p) for p in roots],
                     'files_sha256':{'partial':hashlib.sha256(b'partial').hexdigest()},'cloud_object_files':{}}
            for value in ('false','true',1,False):
                with self.assertRaises(GuardError):verify_archives(receipt,{**c,'abort_cleanup_authorized':value})

    def test_quiescence_fence_and_late_generation_never_delete_unarchived_bytes(self):
        import base64,io
        from cloud_control import execute
        c=fixture();c.update(dedicated_project=True,paid_actions_authorized=True,abort_cleanup_authorized=True)
        payload=b'archived checkpoint';md5=base64.b64encode(hashlib.md5(payload).digest()).decode()
        class Store:
            objects=[{'name':'checkpoint','generation':'1','size':str(len(payload)),'md5Hash':md5}]
            def __init__(self,*args,**kwargs):pass
            def request(self,url):return io.BytesIO(json.dumps({'items':self.objects}).encode())
            def charge_transfer(self,n):pass
        class API:
            active=True;calls=[]
            def __init__(self,*args):pass
            def run(self,args):
                self.calls.append(args)
                if 'projects' in args and 'describe' in args and 'billing' not in args:
                    data={'projectId':c['project'],'labels':{'dams-task':digest(c['authorization_id'])[:24]},'lifecycleState':'ACTIVE'}
                elif 'instances' in args and 'list' in args:
                    data=[{'name':'guest','selfLink':'https://x/projects/'+c['project']+'/zones/us-central1-a/instances/guest','zone':'x/us-central1-a'}] if self.active else []
                elif 'buckets' in args and 'list' in args:data=[{'name':'fixture'}]
                elif 'rm' in args and 'gs://fixture/checkpoint#1' in args:
                    Store.objects=[{'name':'late-checkpoint','generation':'2','size':'4','md5Hash':base64.b64encode(hashlib.md5(b'late').digest()).decode()}];data=[]
                elif 'rm' in args and 'gs://fixture' in args:
                    return {'exit':1,'stdout':'[]','stderr':'bucket is not empty'}
                else:data=[]
                return {'exit':0,'stdout':json.dumps(data),'stderr':''}
        with tempfile.TemporaryDirectory() as tmp,patch('cloud_cleanup.Gcloud',API),patch('cloud_worker.Store',Store):
            roots=[Path(tmp)/n for n in ('a','b')]
            for p in roots:p.mkdir();(p/'checkpoint').write_bytes(payload)
            receipt={'closure_mode':'abort','archive_roots':[str(p) for p in roots],
                     'files_sha256':{'checkpoint':hashlib.sha256(payload).hexdigest()},
                     'cloud_object_files':{'gs://fixture/checkpoint#1':'checkpoint'}}
            folder=Path(tmp)/'logs'
            with self.assertRaisesRegex(GuardError,'confirmed VM and disk absence'):closeout(c,folder,receipt,True)
            self.assertFalse(any('delete' in x or 'rm' in x or 'unlink' in x for x in API.calls))
            self.assertTrue((folder/'closeout-started.json').is_file())
            with patch('cloud_control.Gcloud') as sdk:
                with self.assertRaisesRegex(GuardError,'closeout has started'):execute(c,folder)
                sdk.assert_not_called()
            API.active=False;API.calls=[]
            with self.assertRaisesRegex(GuardError,'absent from both verified archives'):closeout(c,folder,receipt,True)
            self.assertFalse(any(('rm' in x and '--recursive' in x) or 'unlink' in x or ('projects' in x and 'delete' in x) for x in API.calls))
            self.assertEqual(Store.objects[0]['name'],'late-checkpoint')

    def test_uncertain_bucket_deletion_cannot_close_project_and_reconnect_accepts_archived_absence(self):
        import base64,io
        c=fixture();c.update(dedicated_project=True,paid_actions_authorized=True,abort_cleanup_authorized=True)
        payload=b'archived';md5=base64.b64encode(hashlib.md5(payload).digest()).decode()
        class Store:
            def __init__(self,*args,**kwargs):pass
            def request(self,url):return io.BytesIO(json.dumps({'items':[{'name':'checkpoint','generation':'1','size':str(len(payload)),'md5Hash':md5}]}).encode())
            def charge_transfer(self,n):pass
        class API:
            bucket_exists=True;calls=[];unlinked=False;shutdown=False
            def __init__(self,*args):pass
            def run(self,args):
                self.calls.append(args)
                if 'unlink' in args:self.unlinked=True;data={}
                elif 'projects' in args and 'delete' in args:self.shutdown=True;data={}
                elif 'projects' in args and 'describe' in args and 'billing' not in args:
                    data={'projectId':c['project'],'labels':{'dams-task':digest(c['authorization_id'])[:24]},'lifecycleState':'DELETE_REQUESTED' if self.shutdown else 'ACTIVE'}
                elif 'billing' in args:data={'billingEnabled':not self.unlinked,'billingAccountName':'' if self.unlinked else 'fixture'}
                elif 'buckets' in args and 'list' in args:data=[{'name':'fixture'}] if self.bucket_exists else []
                elif 'rm' in args:return {'exit':None,'stdout':'[]','stderr':'response timeout; retained object still archived'}
                else:data=[]
                return {'exit':0,'stdout':json.dumps(data),'stderr':''}
        with tempfile.TemporaryDirectory() as tmp,patch('cloud_cleanup.Gcloud',API),patch('cloud_worker.Store',Store):
            roots=[Path(tmp)/n for n in ('a','b')]
            for p in roots:p.mkdir();(p/'checkpoint').write_bytes(payload)
            receipt={'closure_mode':'abort','archive_roots':[str(p) for p in roots],
                     'files_sha256':{'checkpoint':hashlib.sha256(payload).hexdigest()},
                     'cloud_object_files':{'gs://fixture/checkpoint#1':'checkpoint'}}
            folder=Path(tmp)/'logs'
            with self.assertRaisesRegex(GuardError,'bucket deletion remains unconfirmed'):closeout(c,folder,receipt,True)
            self.assertFalse(any('unlink' in x or ('projects' in x and 'delete' in x) for x in API.calls))
            API.bucket_exists=False;API.calls=[]
            result=closeout(c,folder,receipt,True)
            self.assertEqual(result['status'],'billing-disabled-project-shutdown-verified')
            self.assertFalse(result['science_complete'])

    def test_explicit_abort_keeps_science_incomplete_and_requires_actual_object_roster(self):
        c=fixture();c.update(abort_cleanup_authorized=True,dedicated_project=True,paid_actions_authorized=True)
        with tempfile.TemporaryDirectory() as tmp:
            roots=[Path(tmp)/n for n in ('a','b')]
            for p in roots:p.mkdir();(p/'partial-output').write_bytes(b'actual partial')
            receipt={'closure_mode':'abort','archive_roots':[str(p) for p in roots],
                     'files_sha256':{'partial-output':hashlib.sha256(b'actual partial').hexdigest()},'cloud_object_files':{}}
            verified=verify_archives(receipt,c)
            self.assertFalse(verified['science_complete']);self.assertEqual(verified['closure_mode'],'abort')
            verify_retained_cloud_objects(c,Path(tmp)/'logs',receipt,[])
            # A declared nonempty cloud roster cannot be hidden by providing
            # two matching arbitrary files or an empty observed bucket list.
            receipt['cloud_object_files']={'gs://fixture/actual#1':'partial-output'}
            with self.assertRaises(GuardError):verify_retained_cloud_objects(c,Path(tmp)/'logs',receipt,[])
            c['abort_cleanup_authorized']=False
            with self.assertRaises(GuardError):verify_archives(receipt,c)

    def test_abort_provider_generation_is_bound_to_actual_archive_bytes(self):
        import base64,io
        c=fixture();payload=b'actual partial checkpoint';md5=base64.b64encode(hashlib.md5(payload).digest()).decode()
        class Store:
            def __init__(self,*args,**kwargs):pass
            def request(self,url):return io.BytesIO(json.dumps({'items':[{'name':'tasks/partial','generation':'7','size':str(len(payload)),'md5Hash':md5}]}).encode())
            def charge_transfer(self,n):pass
        with tempfile.TemporaryDirectory() as tmp,patch('cloud_worker.Store',Store):
            root=Path(tmp);(root/'partial').write_bytes(payload)
            receipt={'archive_roots':[str(root)],'files_sha256':{'partial':hashlib.sha256(payload).hexdigest()},
                     'cloud_object_files':{'gs://fixture/tasks/partial#7':'partial'}}
            report=verify_retained_cloud_objects(c,root/'logs',receipt,[{'name':'fixture'}])
            self.assertEqual(report['object_generations'],1)
            (root/'partial').write_bytes(b'corruption')
            with self.assertRaises(GuardError):verify_retained_cloud_objects(c,root/'logs',receipt,[{'name':'fixture'}])

    def test_two_complete_copies_before_any_cloud_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots=[Path(tmp)/n for n in ('a','b')]
            for p in roots:p.mkdir();(p/'state').write_bytes(b'original')
            receipt={'archive_roots':[str(p) for p in roots],'files_sha256':{'state':hashlib.sha256(b'original').hexdigest()}}
            with self.assertRaises(GuardError):verify_archives(receipt)
            receipt['pipeline_roots']=[{'path':'.','spec':'validation','scale':120,'stage_id':'test-stage'}]
            with patch('research_tools.validate_pipeline.validate_pipeline_output',return_value={'unique_complete_cases':1,'spec_sha256':'fixture','case_origins_sha256':'fixture'}) as validation:
                self.assertEqual(verify_archives(receipt)['copies'],2);self.assertEqual(validation.call_count,2)
            (roots[1]/'state').write_bytes(b'changed')
            with patch('cloud_cleanup.Gcloud') as g:
                c=fixture();c.update(dedicated_project=True,paid_actions_authorized=True)
                with self.assertRaises(GuardError):closeout(c,Path(tmp)/'logs',receipt,True)
                g.assert_not_called()

    def test_deadline_does_not_block_final_cleanup_or_touch_shared_account(self):
        c=fixture();c.update(dedicated_project=True,paid_actions_authorized=True,global_deadline_utc=stamp(datetime.now(timezone.utc)-timedelta(days=1)))
        class API:
            calls=[];unlinked=False;shutdown=False
            def __init__(self,*args):pass
            def run(self,args):
                self.calls.append(args)
                if 'unlink' in args:self.unlinked=True;data={}
                elif 'delete' in args:self.shutdown=True;data={}
                elif 'projects' in args and 'describe' in args and 'billing' not in args:
                    data={'projectId':c['project'],'labels':{'dams-task':digest(c['authorization_id'])[:24]},'lifecycleState':'DELETE_REQUESTED' if self.shutdown else 'ACTIVE'}
                elif 'billing' in args:data={'billingEnabled':not self.unlinked,'billingAccountName':'' if self.unlinked else 'fixture-only'}
                else:data=[]
                return {'exit':0,'stdout':json.dumps(data),'stderr':''}
        with tempfile.TemporaryDirectory() as tmp,patch('cloud_cleanup.Gcloud',API):
            roots=[Path(tmp)/n for n in ('a','b')]
            for p in roots:p.mkdir();(p/'state').write_bytes(b'ok')
            receipt={'archive_roots':[str(p) for p in roots],'files_sha256':{'state':hashlib.sha256(b'ok').hexdigest()},'cloud_object_files':{},'pipeline_roots':[{'path':'.','spec':'validation','scale':120,'stage_id':'test-stage'}]}
            with patch('research_tools.validate_pipeline.validate_pipeline_output',return_value={'unique_complete_cases':1,'spec_sha256':'fixture','case_origins_sha256':'fixture'}):
                result=closeout(c,Path(tmp)/'logs',receipt,True)
            self.assertFalse(result['billing_enabled']);self.assertEqual(result['project_state'],'DELETE_REQUESTED')
            self.assertTrue(any('unlink' in a for a in API.calls));self.assertFalse(any('accounts' in a for a in API.calls))

    def test_explicit_resource_delete_plan_rejects_other_project(self):
        c=fixture();obs={k:{'result':[]} for k in inventory_commands(c)}
        obs['disks']['result']=[{'name':'fixture-disk','selfLink':'https://x/projects/fictional-research/zones/us-central1-a/disks/fixture-disk','zone':'x/us-central1-a'}]
        commands=deletion_commands(c,obs)
        self.assertEqual(len(commands),1);self.assertIn('--zone=us-central1-a',commands[0])
        obs['disks']['result'][0]['selfLink']='https://x/projects/other-project/zones/us-central1-a/disks/fixture-disk'
        with self.assertRaises(GuardError):deletion_commands(c,obs)

if __name__=='__main__':unittest.main()
