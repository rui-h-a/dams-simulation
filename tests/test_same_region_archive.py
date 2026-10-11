"""Offline counterexamples: real native CAS and real GCS adapter/codec checks.

No provider, billing, Model construction or formal result is generated here.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit
import zlib

HERE = Path(__file__).resolve().parents[1]
SOURCE = HERE
sys.path[:0] = [str(SOURCE), str(SOURCE/'research_tools')]
sys.dont_write_bytecode = True
from research_tools import same_region_archive as a
from research_tools import compute_only_persistent_backends as pb
from research_tools import persistent_backend_budget_meter as bm
from research_tools.compute_only_archive import ArchiveClosure, RECEIPT_SCHEMA
from dams_sim._committed_pages import Directory, identity
from dams_sim.transfer_spool import _codec


def root():
    return {'schema':a.ROOT_SCHEMA,'project':'test-research01','region':'us-central1',
        'bucket':'test-study-archive','prefix':'study/stage1','service_account':'test-worker@test-research01.iam.gserviceaccount.com',
        'source_manifest_sha256':'1'*64,'global_deadline_utc':'2030-01-01T00:00:00Z',
        'custody_epoch':'2'*64,'bucket_role':'projects/test-research01/roles/damsStudyArchive',
        'bucket_metadata_sha256':'3'*64,'bucket_iam_sha256':'4'*64,'project_iam_sha256':'5'*64,
        'granted_permissions':sorted(a.PERMISSIONS),'guest_project_roles':[],
        'root_verified':True,'expected_versioning':False,'expected_soft_delete_seconds':0}


class FakeMeter:
    def __init__(self):self.events=[];self.results=[]
    def before(self,event):self.events.append(copy.deepcopy(event));return len(self.events)
    def after(self,ticket,result):self.results.append((ticket,copy.deepcopy(result)))


class Response:
    def __init__(self,status,raw,headers=None):self.status=status;self.buffer=io.BytesIO(raw);self.headers=headers or {};self.closed=False
    def read(self,count):return self.buffer.read(count)
    def close(self):self.closed=True


class Google:
    def __init__(self):self.calls=[];self.objects={};self.next=100;self.statuses=[];self.truncate=False
    def request(self,method,path,body,headers,timeout):
        self.calls.append((method,path));query=parse_qs(urlsplit(path).query)
        if self.statuses:return Response(self.statuses.pop(0),b'{}')
        if path=='/storage/v1/b/test-study-archive':
            return Response(200,pb.blob({'name':'test-study-archive','location':'US-CENTRAL1','locationType':'region',
                'versioning':{'enabled':False},'iamConfiguration':{'uniformBucketLevelAccess':{'enabled':True},'publicAccessPrevention':'enforced'},
                'softDeletePolicy':{'retentionDurationSeconds':'0'}}))
        if '/iam' in path:raise AssertionError('guest must never request IAM')
        if method=='POST':
            assert query['ifGenerationMatch']==['0'];name=query['name'][0]
            if name in self.objects:return Response(412,b'{}')
            self.next+=1;self.objects[name]=(str(self.next),body)
        else:name=unquote(urlsplit(path).path.split('/o/',1)[1])
        if name not in self.objects:return Response(404,b'{}')
        gen,raw=self.objects[name]
        if 'generation' in query and query['generation']!=[gen]:return Response(412,b'{}')
        if query.get('alt')==['media']:
            return Response(200,raw[:-1] if self.truncate else raw,
                {'x-goog-generation':gen,'content-length':str(len(raw)),'content-encoding':'identity'})
        return Response(200,pb.blob({'bucket':'test-study-archive','name':name,'generation':gen,'size':str(len(raw)),
                                   'contentType':'application/octet-stream'}))


class BackendFixture:
    def __init__(self, directory):
        self.root=directory;self.google=Google();self.meter=FakeMeter();self.backends=[]
        self.job='6'*64
        for kind,bid in [('external-cas','local-study-disk'),('gcs','regional-gcs')]:
            state=directory/(bid+'-state');state.mkdir();configdir=directory/(bid+'-config');configdir.mkdir()
            cas=directory/'cas';cas.mkdir(exist_ok=True)
            domain='filesystem-device:'+str(cas.stat().st_dev) if kind=='external-cas' else 'provider:google-cloud-storage'
            locator=str(cas) if kind=='external-cas' else 'gs://test-study-archive/study/stage1'
            enrolled=pb.blob({'schema':'dams-compute-persistent-backup-enrollment-v1','job_sha256':self.job,
                             'backup_id':bid,'domain':domain,'locator':locator,'kind':'external-persistent'})
            binding={'backup_id':bid,'domain':domain,'locator':locator,'enrollment_sha256':pb.digest(enrolled)}
            conf={'schema':'dams-compute-persistent-adapter-v1','kind':kind,'binding':binding,
                'job_sha256':self.job,'source_sha256':pb.IMPORT_SHA,'state_root':str(state),
                'state_identity':list(identity(state.stat())[:2]),'max_object_bytes':1024**2,
                'timeout_seconds':5,'max_retries':0 if kind=='external-cas' else 2}
            if kind=='gcs':conf.update(bucket='test-study-archive',prefix='study/stage1',expected_location='US-CENTRAL1',
                expected_versioning=False,expected_soft_delete_seconds=0,expected_ubla=True,expected_public_access_prevention='enforced')
            else:conf.update(root=str(cas),root_identity=list(identity(cas.stat())[:2]),volume=str(directory),
                volume_identity=list(identity(directory.stat())[:2]),min_free_bytes=0)
            p=configdir/'config.json';raw=pb.blob(conf);p.write_bytes(raw)
            with patch.object(pb.os.path,'ismount',return_value=True):
                backend=(pb.ExternalCASBackend(p,pb.digest(raw),enrolled,self.job) if kind=='external-cas' else
                    a.WorkloadGCSBackend(p,pb.digest(raw),enrolled,self.job,root_admission=root(),
                        token_provider=lambda:'OFFLINE_TEST_CREDENTIAL',meter=self.meter,transport=self.google))
            if kind=='gcs':backend.admit()
            self.backends.append(backend)
    def close(self):
        for b in self.backends:b.close()


class Controls(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=HERE);self.path=Path(self.temp.name);self.fix=None
    def tearDown(self):
        if self.fix:self.fix.close()
        self.temp.cleanup()
    def enrollment(self,changes=None):
        value=root();value.update(changes or {});raw=pb.blob(value)
        return a.root_admission(raw,pb.digest(raw),project='test-research01',region='us-central1',
            source_manifest_sha256='1'*64,global_deadline_utc='2030-01-01T00:00:00Z')
    def test_exact_three_permissions_admitted(self):self.assertEqual(self.enrollment()['guest_project_roles'],[])
    def test_owner_and_project_grants_refused(self):
        for change in [{'guest_project_roles':['roles/owner']},{'granted_permissions':sorted(a.PERMISSIONS|{'storage.objects.delete'})},
                       {'bucket_role':'roles/storage.objectUser'},{'root_verified':False},{'region':'US'}]:
            with self.subTest(change=change),self.assertRaises(ValueError):self.enrollment(change)
    def test_root_source_and_sha_drift_refused(self):
        raw=pb.blob(root())
        with self.assertRaises(ValueError):a.root_admission(raw,'f'*64,project='test-research01',region='us-central1',source_manifest_sha256='1'*64,global_deadline_utc='2030-01-01T00:00:00Z')
        with self.assertRaises(ValueError):self.enrollment({'source_manifest_sha256':'a'*64})
    def test_legacy_no_sa_is_not_mutated(self):
        from research_tools import cloud_control as cc
        original=['gcloud','compute','instances','create','x','--no-service-account','--no-scopes','--instance-termination-action=STOP']
        with patch.object(cc,'create_compute_only_command',return_value=original):
            argv=a.create_command({'region':'us-central1','project':'test-research01'}, {},{},'us-central1-a','/tmp/source',startup_sha256='1'*64,admission=root())
        self.assertIn('--no-service-account',original);self.assertNotIn('--no-service-account',argv)
        self.assertEqual([x for x in argv if x.startswith('--scopes=')],['--scopes='+a.SCOPE]);self.assertIn('--instance-termination-action=STOP',argv)
    def test_overbroad_actual_vm_refused_before_base_checks(self):
        with self.assertRaises(ValueError):a.validate_instance({'region':'us-central1'}, {}, {},{'serviceAccounts':[{'email':root()['service_account'],'scopes':['https://www.googleapis.com/auth/cloud-platform']}]},root())
    def test_guest_no_iam_request(self):
        self.fix=BackendFixture(self.path);self.assertEqual(self.fix.backends[1].policy_receipt['guest_IAM_reads'],0)
        self.assertEqual(self.fix.google.calls,[('GET','/storage/v1/b/test-study-archive')])
    def test_create_collision_and_exact_full_restore(self):
        self.fix=BackendFixture(self.path);b=self.fix.backends[1];raw=b'original evidence\x00'+bytes(range(255))*400
        key='objects/'+pb.digest(raw)+'.z';b.publish(key,raw);b.publish(key,raw)
        stream=b.read(key);restored=b''
        try:
            while block:=stream.read(65536):restored+=block
        finally:stream.close()
        self.assertEqual(restored,raw);self.assertTrue(all('ifGenerationMatch=0' in path for method,path in self.fix.google.calls if method=='POST'))
        self.assertEqual(len(self.fix.meter.events),len(self.fix.meter.results))
    def test_truncation_not_accepted(self):
        self.fix=BackendFixture(self.path);b=self.fix.backends[1];raw=b'abc';key='objects/'+pb.digest(raw)+'.z'
        b.publish(key,raw);self.fix.google.truncate=True
        stream=b.read(key)
        with self.assertRaises(ValueError):
            try:
                while stream.read(65536):pass
            finally:stream.close()
    def test_transient_retries_metered_and_bounded(self):
        self.fix=BackendFixture(self.path);b=self.fix.backends[1];self.fix.google.statuses=[503,503,503,503]
        start=len(self.fix.meter.events)
        status,raw,_=b._full('GET','/storage/v1/b/test-study-archive',None,pb.MAX_POLICY,b._deadline())
        self.assertEqual(status,503);self.assertEqual(len(self.fix.meter.events)-start,3);self.assertEqual(len(self.fix.google.statuses),1)
    def test_collision_different_bytes_refused(self):
        self.fix=BackendFixture(self.path);b=self.fix.backends[1];key='a'*64+'/manifest.json'
        b.publish(key,b'123')
        with self.assertRaises(ValueError):b.publish(key,b'456')
    def test_metadata_missing_generation_invalidates_reuse(self):
        self.fix=BackendFixture(self.path);b=self.fix.backends[1];raw=b'123';key='objects/'+pb.digest(raw)+'.z';b.publish(key,raw)
        pin=b.custody(key,len(raw),pb.digest(raw));self.assertIn('generation',pin)
        self.fix.google.objects.clear()
        with self.assertRaises(ValueError):b.custody(key,len(raw),pb.digest(raw))
    def test_same_region_multiregion_policy_refused(self):
        self.fix=BackendFixture(self.path);b=self.fix.backends[1]
        class WrongRegion(Google):
            def request(inner,*args,**kwargs):return Response(200,pb.blob({'name':'test-study-archive','location':'US','locationType':'multi-region'}))
        b.transport=WrongRegion()
        with self.assertRaises(ValueError):b.admit()
    def test_token_memory_only_and_no_reset(self):
        state=self.path/'auth';state.mkdir();ad=root()
        class Metadata:
            def __init__(inner):inner.calls=[]
            def get(inner,p,timeout,maximum):
                inner.calls.append(p)
                if p.endswith('/token'):return pb.blob({'access_token':'DO_NOT_WRITE_THIS_TOKEN','expires_in':3600,'token_type':'Bearer'})
                if p.endswith('/email'):return ad['service_account'].encode()
                if p.endswith('project-id'):return ad['project'].encode()
                if p.endswith('/id'):return b'987'
                return b'projects/123/zones/us-central1-a'
        transport=Metadata();options=dict(instance_id='987',zone='us-central1-a',deadline=1900000000,state_root=state,max_auth_requests=5,check=lambda:None,transport=transport)
        token=a.MetadataToken(ad,**options)
        try:self.assertEqual(token(),'DO_NOT_WRITE_THIS_TOKEN');self.assertEqual(token(),'DO_NOT_WRITE_THIS_TOKEN')
        finally:token.close()
        self.assertEqual(len(transport.calls),5)
        for p in state.iterdir():self.assertNotIn(b'DO_NOT_WRITE_THIS_TOKEN',p.read_bytes())
        resumed=a.MetadataToken(ad,**options)
        try:
            with self.assertRaises(ValueError):resumed()
        finally:resumed.close()
    def test_token_failed_request_remains_reserved(self):
        state=self.path/'auth';state.mkdir()
        class Broken:
            def get(self,*args):raise RuntimeError('SECRET_CREDENTIAL_UNAVAILABLE')
        token=a.MetadataToken(root(),instance_id='987',zone='us-central1-a',deadline=1900000000,state_root=state,max_auth_requests=5,check=lambda:None,transport=Broken())
        try:
            with self.assertRaisesRegex(ValueError,'bounded workload credential unavailable'):token()
        finally:token.close()
        self.assertEqual(len(list(state.glob('*.attempt.json'))),1)
    def test_token_retained_root_floor_refuses_rollback(self):
        state=self.path/'auth';state.mkdir()
        class Broken:
            def get(self,*args):raise RuntimeError('not logged')
        opts=dict(instance_id='987',zone='us-central1-a',deadline=1900000000,state_root=state,
            max_auth_requests=5,check=lambda:None,transport=Broken(),state_identity=list(identity(state.stat())[:2]))
        token=a.MetadataToken(root(),**opts)
        try:
            with self.assertRaises(ValueError):token()
            floor=token.snapshot()
        finally:token.close()
        (state/'00000001.attempt.json').unlink();(state/'head.json').write_bytes(pb.blob({'count':0,'last_sha256':None}))
        with self.assertRaisesRegex(ValueError,'high-water rollback'):a.MetadataToken(root(),**opts,minimum=floor)
    def test_token_native_anchor_refuses_new_directory(self):
        state=self.path/'auth';state.mkdir()
        with self.assertRaisesRegex(ValueError,'native auth state anchor'):
            a.MetadataToken(root(),instance_id='987',zone='us-central1-a',deadline=1900000000,state_root=state,
                max_auth_requests=5,check=lambda:None,state_identity=[state.stat().st_dev,state.stat().st_ino+1])
    def test_existing_auth_binding_missing_head_never_resets(self):
        state=self.path/'auth';state.mkdir();opts=dict(instance_id='987',zone='us-central1-a',deadline=1900000000,
            state_root=state,max_auth_requests=5,check=lambda:None)
        token=a.MetadataToken(root(),**opts);token.close();(state/'head.json').unlink()
        with self.assertRaisesRegex(ValueError,'existing auth head missing'):a.MetadataToken(root(),**opts)
    def test_empty_replaced_auth_contents_cannot_reset_initial_native_anchor(self):
        state=self.path/'auth';state.mkdir();st=state.stat()
        anchor={'dev':st.st_dev,'ino':st.st_ino,'initial_ctime_ns':st.st_ctime_ns}
        opts=dict(instance_id='987',zone='us-central1-a',deadline=1900000000,
            state_root=state,max_auth_requests=5,check=lambda:None,state_identity=anchor)
        token=a.MetadataToken(root(),**opts);token.close()
        for p in state.iterdir():p.unlink()
        with self.assertRaisesRegex(ValueError,'empty auth namespace changed'):a.MetadataToken(root(),**opts)
    def background_fixture(self):
        work=self.path/'work';work.mkdir();base=work/'regional-archive';base.mkdir();(base/'metadata').mkdir();(base/'results').mkdir()
        raw=pb.blob({'generation':'a'*64});p=base/'metadata'/'route.json';p.write_bytes(raw)
        request={'config_path':str(p),'config_sha256':pb.digest(raw)}
        context={'work_root':str(work),'source_root':str(SOURCE),'regional_archive_route_sha256':pb.digest(raw),
                 'deadline_utc':'2030-01-01T00:00:00Z'}
        return request,context,base
    def test_nonblocking_archive_start_does_not_run_model_or_repeat(self):
        request,context,base=self.background_fixture();calls=[]
        def runner(argv,**options):
            calls.append(argv)
            self.assertTrue((base/'results'/('a'*64+'.BEGIN.json')).exists())
            return types.SimpleNamespace(returncode=0,stdout=b'ActiveState=active\nSubState=running\nResult=success\n')
        first=a.background_operation('regional-archive-start',request,context=context,runner=runner)
        second=a.background_operation('regional-archive-start',request,context=context,runner=runner)
        self.assertEqual(first['state'],'STARTED');self.assertFalse(second['new_run']);self.assertEqual(calls[0][0],'/usr/bin/systemd-run')
        self.assertEqual(calls[1][0],'/usr/bin/systemctl');self.assertIn('--property=KillMode=control-group',calls[0])
        self.assertNotIn('small-enterprise',' '.join(calls[0]));self.assertFalse(first['Model_execution'])
    def test_ambiguous_archive_spawn_keeps_begin_and_never_auto_retries(self):
        request,context,base=self.background_fixture();calls=[]
        def failed(argv,**options):calls.append(argv);raise TimeoutError()
        with self.assertRaisesRegex(ValueError,'original BEGIN retained'):
            a.background_operation('regional-archive-start',request,context=context,runner=failed)
        def poll(argv,**options):calls.append(argv);return types.SimpleNamespace(returncode=0,stdout=b'ActiveState=failed\n')
        result=a.background_operation('regional-archive-start',request,context=context,runner=poll)
        self.assertFalse(result['new_run']);self.assertEqual([x[0] for x in calls],['/usr/bin/systemd-run','/usr/bin/systemctl'])
    def test_background_config_tamper_blocks_before_spawn(self):
        request,context,base=self.background_fixture();Path(request['config_path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):a.background_operation('regional-archive-start',request,context=context,runner=lambda *a,**k:self.fail('dispatch'))
    def test_same_generation_new_config_cannot_spawn_second_writer(self):
        request,context,base=self.background_fixture()
        a.background_operation('regional-archive-start',request,context=context,runner=lambda *a,**k:types.SimpleNamespace(returncode=0))
        raw=pb.blob({'generation':'a'*64,'changed_floor':True});p=base/'metadata'/'route2.json';p.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'original ownership'):
            a.background_operation('regional-archive-start',{'config_path':str(p),'config_sha256':pb.digest(raw)},
                context={**context,'regional_archive_route_sha256':pb.digest(raw)},runner=lambda *a,**k:self.fail('second writer'))
    def lease_fixture(self):
        import time
        from research_tools.cloud_control import stamp
        work=self.path/'work';work.mkdir();runtime=pb.blob({'deadline_utc':'2030-01-01T00:00:00Z','controller_lease_timeout_seconds':120})
        (work/'compute-runtime.json').write_bytes(runtime);config=work/'config.json';config.write_bytes(b'root-config')
        context={'work_root':str(work),'runtime_sha256':pb.digest(runtime),'deadline_utc':'2030-01-01T00:00:00Z'}
        row={'schema':'DAMS-compute-controller-lease-1','runtime_sha256':pb.digest(runtime),'sequence':0,'observed_utc':stamp()}
        heartbeat=work/'controller-heartbeat.json';heartbeat.write_bytes(pb.blob(row))
        return context,config,heartbeat,row
    def test_original_120_second_lease_expires_without_extension(self):
        from datetime import datetime,timezone
        context,config,heartbeat,row=self.lease_fixture();guard=a.LeaseGuard(context,config,b'root-config')
        try:
            guard.check();heartbeat.write_bytes(pb.blob({**row,'observed_utc':'2020-01-01T00:00:00Z'}))
            with self.assertRaisesRegex(guard.worker.GuardError,'lease expired'):guard.check()
        finally:guard.close()
    def test_original_lease_rollbacks_block_decode_cancel(self):
        context,config,heartbeat,row=self.lease_fixture();guard=a.LeaseGuard(context,config,b'root-config')
        try:
            heartbeat.write_bytes(pb.blob({**row,'sequence':1}));self.assertFalse(guard.cancelled())
            heartbeat.write_bytes(pb.blob(row))
            with self.assertRaisesRegex(guard.worker.GuardError,'lease rolled back'):guard.cancelled()
        finally:guard.close()
    def test_running_regional_helper_source_change_blocks_lease_guard(self):
        context,config,heartbeat,row=self.lease_fixture();guard=a.LeaseGuard(context,config,b'root-config')
        try:
            original=Path.read_bytes
            def altered(p):return b'changed executable' if p==Path(a.__file__) else original(p)
            with patch.object(Path,'read_bytes',altered),self.assertRaisesRegex(ValueError,'running helper source changed'):
                guard.check()
        finally:guard.close()
    def test_same_region_zero_media_rejects_foreign_namespace_and_legacy(self):
        receipt=root();manifest=b'actual pinned source manifest';receipt['source_manifest_sha256']=pb.digest(manifest)
        raw=pb.blob(receipt);m=object.__new__(bm.Meter)
        m.config={'schema':bm.REGIONAL_CONFIG_SCHEMA,'global_deadline_utc':receipt['global_deadline_utc'],
            'same_region_admission':{'sha256':pb.digest(raw)},'gcs_namespaces':{'regional':{'bucket':receipt['bucket'],'prefix':receipt['prefix'],'generations':['a'*64]}},
            'allowed_paths':{'regional':{'GET':['/storage/v1/b/'+receipt['bucket']]}}}
        contents={'same_region_admission':raw,'source_manifest':manifest};m._same_region_media_binding(contents)
        m.config['gcs_namespaces']['regional']['bucket']='different-region-bucket'
        with self.assertRaises(ValueError):m._same_region_media_binding(contents)
        m.config['gcs_namespaces']['regional']['bucket']=receipt['bucket'];m.config['schema']=bm.CONFIG_SCHEMA
        with self.assertRaises(ValueError):m._same_region_media_binding(contents)
    def test_active_owned_producer_rejected_before_archive(self):
        context,config,heartbeat,row=self.lease_fixture()
        terminal={'runtime_sha256':context['runtime_sha256'],'inputs_unchanged':True,
            'owned_pipeline_group_verification':{'owned_pipeline_group_absent':True,'owned_pipeline_group_id':54321}}
        (Path(context['work_root'])/'compute-terminal.json').write_bytes(pb.blob(terminal))
        with patch.object(a.os,'killpg',return_value=None),self.assertRaisesRegex(ValueError,'active owned producer'):
            a.require_closed_producer(context)
        with patch.object(a.os,'killpg',side_effect=ProcessLookupError()):
            self.assertEqual(a.require_closed_producer(context),pb.digest(pb.blob(terminal)))
    def test_collector_continuous_lifetime_check_runs_first(self):
        from research_tools.compute_only_control import Collector
        c=object.__new__(Collector);c.closed=False
        def reject():raise ValueError('original lease expired')
        c.lifetime_check=reject
        with self.assertRaisesRegex(ValueError,'original lease expired'):c.check()
    def test_joint_capacity_counts_cache_and_sink_restores_without_ratio(self):
        gib=1024**3
        bounds=dict(scientific_raw_upper_bytes=32*gib,spool_upper_bytes=36*gib,
            collector_cache_upper_bytes=36*gib,local_cas_upper_bytes=36*gib,raw_lease_upper_bytes=16*gib,
            recovery_input_upper_bytes=8*gib,metadata_and_boot_upper_bytes=8*gib,safety_floor_bytes=80*gib)
        observed={'total_bytes':256*gib,'available_bytes':252*gib,'used_bytes':4*gib,'device':99}
        result=a.capacity_admission(bounds,observed)
        self.assertEqual(result['declared_peak_bytes'],252*gib);self.assertFalse(result['compression_assumed'])
        with self.assertRaises(ValueError):a.capacity_admission({**bounds,'collector_cache_upper_bytes':64*gib},observed)
        with self.assertRaises(ValueError):a.capacity_admission(bounds,{**observed,'available_bytes':230*gib,'used_bytes':26*gib})
    def test_native_capacity_exchange_payload_uses_real_statvfs_and_exact_sha(self):
        work=self.path/'capacity-work';work.mkdir();fs=os.statvfs(work);total=fs.f_frsize*fs.f_blocks;available=fs.f_frsize*fs.f_bavail
        plan={'schema':'dams-same-region-working-capacity-plan-v1','project':'test-project','region':'us-central1',
            'source_manifest_sha256':'a'*64,'global_deadline_utc':'2030-01-01T00:00:00Z','workload_admission_sha256':'b'*64,
            'bounds':dict(scientific_raw_upper_bytes=1024,spool_upper_bytes=1024,collector_cache_upper_bytes=1024,
                local_cas_upper_bytes=1024,raw_lease_upper_bytes=1024,recovery_input_upper_bytes=1024,
                metadata_and_boot_upper_bytes=total-available+8192,safety_floor_bytes=1024)}
        raw=pb.blob(plan);context={'work_root':str(work),'project':'test-project','zone':'us-central1-a',
            'source_manifest_sha256':'a'*64,'regional_global_deadline_utc':plan['global_deadline_utc'],
            'regional_workload_admission_sha256':'b'*64,'provider_identity_sha256':'c'*64,'regional_capacity_plan_sha256':pb.digest(raw)}
        result=a.native_capacity_plan(raw,context=context);self.assertTrue(result['actual_native_statvfs'])
        self.assertEqual(result['observation']['total_bytes'],total)
        with self.assertRaises(ValueError):a.native_capacity_plan(raw+b' ',context=context)
        (work/'compute-runtime.json').write_bytes(b'activated')
        with self.assertRaisesRegex(ValueError,'before model activation'):a.native_capacity_plan(raw,context=context)
    def test_regional_large_head_keeps_legacy_one_megabyte_bound(self):
        value={'results':{str(i):'a'*64 for i in range(20000)}}
        self.assertGreater(len(pb.blob(value)),bm.MAX_SMALL)
        m=object.__new__(bm.Meter);m.config={'schema':bm.REGIONAL_CONFIG_SCHEMA}
        fd=os.open(self.path,os.O_RDONLY|os.O_DIRECTORY)
        try:
            m._write(fd,'regional-large-head.json',value,maximum=m._head_bound())
            m.config['schema']=bm.CONFIG_SCHEMA
            with self.assertRaises(ValueError):m._write(fd,'legacy-head.json',value,maximum=m._head_bound())
        finally:os.close(fd)
    def test_meter_dynamic_namespace_exact_and_no_delete(self):
        meter=object.__new__(bm.Meter);meter.config={'allowed_paths':{'regional':{'GET':['/storage/v1/b/test-study-archive']}},
            'gcs_namespaces':{'regional':{'bucket':'test-study-archive','prefix':'study/stage1','generations':['a'*64]}},
            'per_request_upload_bytes':100000,'per_request_download_bytes':100000}
        raw=b'full bytes';sha=pb.digest(raw);name='study/stage1/objects/'+sha+'.z'
        from urllib.parse import urlencode,quote
        upload='/upload/storage/v1/b/test-study-archive/o?'+urlencode({'uploadType':'media','name':name,'ifGenerationMatch':'0'})
        event={'schema':bm.EVENT_SCHEMA,'backend_id':'regional','method':'POST','path':upload,'request_body_upper_bytes':len(raw),'request_body_sha256':sha,'response_body_limit_bytes':1000}
        self.assertEqual(meter._event(event),event)
        for change in [{'request_body_sha256':'b'*64},{'path':upload.replace('ifGenerationMatch=0','ifGenerationMatch=1')},
                       {'path':upload.replace('study%2Fstage1','other%2Fstage1')},{'method':'DELETE','request_body_upper_bytes':0}]:
            with self.subTest(change=change),self.assertRaises(ValueError):meter._event({**event,**change})
        path='/storage/v1/b/test-study-archive/o/'+quote(name,safe='')+'?'+urlencode({'generation':'123','ifGenerationMatch':'123'})
        self.assertEqual(meter._event({**event,'method':'GET','path':path,'request_body_upper_bytes':0})['path'],path)
        with self.assertRaises(ValueError):meter._event({**event,'method':'GET','path':path.replace('ifGenerationMatch=123','ifGenerationMatch=124'),'request_body_upper_bytes':0})
    def test_meter_metadata_generation_roster_refused(self):
        ns={'bucket':'test-study-archive','prefix':'study/stage1','generations':['a'*64]}
        from urllib.parse import quote
        event={'request_body_sha256':'c'*64}
        self.assertTrue(bm.Meter._regional_path(ns,'GET','/storage/v1/b/test-study-archive/o/'+quote('study/stage1/'+'a'*64+'/index.json',safe=''),event))
        self.assertFalse(bm.Meter._regional_path(ns,'GET','/storage/v1/b/test-study-archive/o/'+quote('study/stage1/'+'b'*64+'/index.json',safe=''),event))
    def test_legacy_meter_rejects_namespace_fields(self):
        original=Path(SOURCE/'research_tools/persistent_backend_budget_meter.py').read_text()
        self.assertIn('if regional else set()',original)
        # Real _validate_config checks unknown fields before fee/source parsing.
        m=object.__new__(bm.Meter);m.config={'schema':bm.CONFIG_SCHEMA,'gcs_namespaces':{}}
        with self.assertRaisesRegex(ValueError,'exact meter config fields'):m._validate_config()
    def test_all_scientific_files_byte_identical(self):
        originals=json.loads((SOURCE/'docs/frozen-studies/small-enterprise-five-year/same-region-original-source-sha256.json').read_bytes())
        for name,sha in originals.items():
            if name.startswith('dams_sim/') or name in ('research_tools/small_enterprise.py','research_tools/validate_longitudinal.py'):
                self.assertEqual(pb.digest((SOURCE/name).read_bytes()),sha,name)
        self.assertEqual(pb.digest((SOURCE/'research_tools/small_enterprise_cloud.py').read_bytes()),
            'fa63eed805bc8fd41fe71b1a317876e68e1c3f9ef0fb81dc27f039a64f37586f')
    def restoration_fixture(self):
        self.fix=BackendFixture(self.path);groups=self.path/'groups';groups.mkdir();records=self.path/'records';records.mkdir()
        generation='a'*64;group_path=groups/generation;group_path.mkdir();group=Directory(group_path)
        raw=bytes(range(255))*30;encoded=zlib.compress(raw,level=1);codec=_codec()
        chunk={'offset':0,'raw_bytes':len(raw),'raw_sha256':pb.digest(raw),'encoded_bytes':len(encoded),'encoded_sha256':pb.digest(encoded)}
        descriptor=codec.validate_manifest({'schema':1,'codec':codec.CODEC,'chunk_bytes':codec.CHUNK_BYTES,
            'raw_bytes':len(raw),'raw_sha256':pb.digest(raw),'encoded_bytes':len(encoded),'chunks':[chunk]})
        m={'generation':generation,'kind':'checkpoint','files':[{'name':'state.bin','codec':descriptor}],
           'group_sha256':'b'*64};mr=pb.blob(m);sr=pb.blob({'synthetic_seal':True})
        gate={'schema':'dams-compute-archive-scientific-gate-v1','profile_sha256':'c'*64,'job_sha256':self.fix.job,
            'generation':generation,'files':{'state.bin':{'bytes':len(raw),'sha256':pb.digest(raw)}},
            'gate':'checkpoint-full-state','individual_case_only':True,'full_study_gate':False,'science_complete':False}
        gr=pb.blob(gate);closure=object.__new__(ArchiveClosure);closure.profile_sha='c'*64;closure.job_sha=self.fix.job
        closure.source_raw=Path(sys.modules[ArchiveClosure.__module__].__file__).read_bytes()
        closure.groups=Directory(groups);closure.backends=self.fix.backends
        closure.proof_reuse=a.ProofReuse('d'*64,'2'*64);record_dir=Directory(records)
        c=types.SimpleNamespace(config={'source_sha256':'1'*64,'pipeline_driver_sha256':'8'*64},
            codec=codec,records=record_dir,_head={},_history=lambda h:None,check=lambda:None,
            _validate_group=lambda mr,sr:(pb.parse(mr),pb.parse(sr)))
        closure.c=c;ir=pb.blob(closure.index(m,sr,gr))
        for name,data in [('manifest.json',mr),('seal.json',sr),('scientific-gate.json',gr),('index.json',ir),
                          ('binding.json',pb.blob({'reservation':1,'index_sha256':pb.digest(ir),'scientific_gate_sha256':pb.digest(gr)}))]:
            group.write_new(name,data)
        record_dir.write_new('00000001.json',pb.blob({'object_sha256':pb.digest(ir)}))
        def fetch(backend,key,size,sha):
            stream=backend.read(key);out=b''
            try:
                while block:=stream.read(65536):out+=block
            finally:stream.close()
            self.assertEqual(len(out),size);self.assertEqual(pb.digest(out),sha);return out
        closure.fetch=fetch;closure.plan=lambda rows:None
        with patch.object(pb.os.path,'ismount',return_value=True):
            for backend in self.fix.backends:
                for name,data in [('manifest.json',mr),('seal.json',sr),('scientific-gate.json',gr),('index.json',ir)]:
                    backend.publish(generation+'/'+name,data)
                backend.publish('objects/'+pb.digest(encoded)+'.z',encoded)
        self.addCleanup(group.close);self.addCleanup(record_dir.close);self.addCleanup(closure.groups.close)
        return closure,group,m,mr,sr,gr,ir
    def test_full_native_restore_then_generation_proof_reuse(self):
        closure,group,m,*_=self.restoration_fixture()
        with patch.object(pb.os.path,'ismount',return_value=True):
            _,first=closure.verify(m['generation']);before=len(self.fix.google.calls)
            _,second=closure.verify(m['generation'])
        self.assertEqual(first,second)
        reused_calls=self.fix.google.calls[before:]
        self.assertTrue(reused_calls);self.assertTrue(all(method=='GET' and 'generation=' in path and 'alt=media' not in path for method,path in reused_calls))
        stored=pb.parse((group.path/'full-restoration-reuse.json').read_bytes())
        self.assertEqual(stored['copies'],first)
        self.assertTrue(all(pb.parse((group.path/(b.binding['backup_id']+'.receipt.json')).read_bytes())['verification']=='fresh-full-decode-to-sink-and-original-scientific-gate' for b in self.fix.backends))
    def test_reuse_custody_epoch_invalidated(self):
        closure,group,m,*_=self.restoration_fixture()
        with patch.object(pb.os.path,'ismount',return_value=True):
            closure.verify(m['generation']);closure.proof_reuse.epoch='f'*64
            with self.assertRaisesRegex(ValueError,'custody invalidated'):closure.verify(m['generation'])
    def test_reuse_local_bytes_invalidated(self):
        closure,group,m,*_=self.restoration_fixture()
        with patch.object(pb.os.path,'ismount',return_value=True):
            closure.verify(m['generation']);p=next((self.path/'cas'/'objects').glob('*.z'));p.write_bytes(b'altered')
            with self.assertRaises(ValueError):closure.verify(m['generation'])
    def test_reuse_original_full_receipt_invalidated(self):
        closure,group,m,*_=self.restoration_fixture()
        with patch.object(pb.os.path,'ismount',return_value=True):
            closure.verify(m['generation']);p=group.path/'regional-gcs.receipt.json';row=pb.parse(p.read_bytes())
            row['verification']='assumed-without-read';p.write_bytes(pb.blob(row))
            with self.assertRaises(ValueError):closure.verify(m['generation'])
    def test_reuse_deleted_remote_generation_invalidated(self):
        closure,group,m,*_=self.restoration_fixture()
        with patch.object(pb.os.path,'ismount',return_value=True):
            closure.verify(m['generation']);self.fix.google.objects.clear()
            with self.assertRaises(ValueError):closure.verify(m['generation'])


if __name__=='__main__':unittest.main(verbosity=2)
