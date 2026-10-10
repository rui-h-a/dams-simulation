"""Owned opaque bytes and fake REST/auth only; no SQL, Model or provider."""
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from dams_sim.storage import canonical, digest, source_hash
from dams_sim.config import Config
from dams_sim.spec import case_key
from dams_sim.longitudinal_pipeline import driver_hash
from research_tools import compute_only_entry as e
from research_tools import compute_only_backend_factory as f
from research_tools import compute_only_persistent_backends as pb
from research_tools import persistent_backend_budget_meter as bm
from research_tools.compute_only_control import blob, Collector, ARCHIVE_COLLECTOR_SCHEMA, COLLECTOR_SCHEMA
from research_tools.compute_only_archive import IMPORT_SHA as ARCHIVE_SHA

ROOT=Path(__file__).absolute().parents[1]
OUTPUT=Path(os.environ.get('DAMS_R5_TEST_ROOT',str(ROOT.parent/'test-fixtures')))
OUTPUT.mkdir(exist_ok=True)

def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tests'/('test_'+name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

meter_test=module('persistent_backend_budget_meter')
adapter_test=module('compute_only_persistent_backends')


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=OUTPUT,prefix=self._testMethodName+'-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.reference_serial=0; self.entries=[];self.factories=[];self.auth_calls=[]
        self.addCleanup(lambda:[x.close() for x in reversed(self.entries)])
        self.addCleanup(lambda:[x.close() for x in reversed(self.factories)])
        self.google=adapter_test.MockGoogle()
        self.mount=mock.patch.object(pb.os.path,'ismount',return_value=True);self.mount.start();self.addCleanup(self.mount.stop)
        # Reuse only the synthetic meter fixture builder, never its data/job.
        self.mt=meter_test.MeterTests();self.mt.setUp();self.addCleanup(self.mt.tearDown)
        config=Config(n=2,days=2,guilds=1,sites=1,team_size=1,world=70000).validate()
        spec={'name':'explicit-opaque-R5-wiring-fixture'}
        rows=[{'case_id':case_key(config),'config':config.to_dict(),'tags':{'parent_case_id':None}}]
        self.assignment=blob({'schema':'dams-compute-only-assignment-v1','source_sha256':source_hash(),
            'pipeline_driver_sha256':driver_hash(),'spec_sha256':digest(canonical(spec)),
            'inventory_sha256':digest(canonical(rows)),'spec':spec,'cases':rows})
        self.job={'stage_id':'r5-owned-fixture','source_sha256':source_hash(),'pipeline_driver_sha256':driver_hash(),
                  'spec_sha256':digest(canonical(spec)),'inventory_sha256':digest(canonical(rows)),'assignment_sha256':digest(self.assignment),
                  'runtime_sha256':'4'*64,'provider_identity_sha256':'5'*64}
        self.backends=[self.adapter('external-cas',0),self.adapter('gcs',1)]
        self.admission={'schema':ARCHIVE_COLLECTOR_SCHEMA,**{k:v for k,v in self.job.items() if k not in ('runtime_sha256','provider_identity_sha256')},
                        'deadline_utc':'2029-01-01T00:00:00Z'}
        for name in ('collector-state','collector-cache','collector-copy1','collector-copy2','lease'):(self.root/name).mkdir()
        self.admission.update(codec_source_sha256=digest((ROOT/'research_tools/cloud_archive.py').read_bytes()),
            codec_dependencies_sha256={n:digest((ROOT/'research_tools'/n).read_bytes()) for n in ('cloud_control.py','cloud_archive.py')},
            collector_source_sha256=digest((ROOT/'research_tools/compute_only_control.py').read_bytes()),
            max_fetch_requests=10000,max_fetch_bytes=64*1024**2,max_group_raw_bytes=100000,min_free_bytes=0,
            state_dir=str(self.root/'collector-state'),cache_dir=str(self.root/'collector-cache'),
            copy_dirs=[str(self.root/'collector-copy1'),str(self.root/'collector-copy2')])
        self.profile={'schema':'dams-compute-archive-backed-profile-v1','mode':'external-persistent',
            'collector_binding_sha256':digest(canonical(self.admission)),'helper_sha256':ARCHIVE_SHA,'job':self.job,
            'lease_dir':str(self.root/'lease'),'max_lease_raw_bytes':100000,'max_archive_encoded_bytes':100000,
            'backup_bindings':[row[2] for row in self.backends],'prior_cases':{},'max_operation_seconds':10,'max_rss_bytes':1024**3}
        self.profile_ref=self.ref(self.profile);self.admission['storage_profile']=self.profile_ref;self.admission_ref=self.ref(self.admission)
        config=copy.deepcopy(self.mt.config);now=datetime.now(timezone.utc)
        config['created_at_utc']=(now-timedelta(seconds=1)).isoformat();self.mt.current['tasks'][meter_test.TASK_ID]['reserved_utc']=(now-timedelta(seconds=60)).isoformat()
        config['current_budget']=self.mt.file('current-r5.json',self.mt.current)
        config['source_job']=self.ref(self.job)
        config['source_manifest']={'path':str(ROOT/'source-manifest.json'),'sha256':digest((ROOT/'source-manifest.json').read_bytes())}
        config['source_files']=[{'path':str(ROOT/n),'sha256':digest((ROOT/n).read_bytes())} for n in f.DEPENDENCIES]
        config['allowed_paths']={'gcs-private':{'GET':['/storage/v1/b/fixture-bucket','/storage/v1/b/fixture-bucket/iam']}}
        config['generation_media_paths']={};config['generation_delete_paths']={}
        self.meter_config=config;self.meter_ref=self.ref(config)
        self.factory_config={'schema':f.SCHEMA,'storage_profile':self.profile_ref,'meter':self.meter_ref,'minimum_meter':None,
            'backends':[{'config':row[0],'enrollment':row[1]} for row in self.backends],
            'auth':{'configuration':'r5-private-fixture','account':'fixture.invalid','project':'fixture-project','min_expiry_seconds':1800,'timeout_seconds':3}}
        self.factory_ref=self.ref(self.factory_config)
        transport=self.ref({'remote_runtime_sha256':self.job['runtime_sha256'],'provider_identity_sha256':self.job['provider_identity_sha256']})
        self.c={'compute_only':{'schema':e.SCHEMA,'phase':'collect','stage_id':self.job['stage_id'],'operation_id':'f'*64,
            'prepare':{'deadline_utc':'2028-12-31T00:00:00Z','planned_termination_utc':'2029-01-01T00:00:00Z','max_seconds':10,
                'max_source_bytes':4*1024**2,'max_source_files':200,'max_log_bytes':1024,'receipt_file':str(self.root/'prepare.json'),'offline':True},
            'package_manifest':config['source_manifest'],'component_sha256':{n:digest((ROOT/n).read_bytes()) for n in e.COMPONENTS|e.ARCHIVE_COMPONENTS},
            'archive_backend_factory':self.factory_ref,'collect':{'admission':self.admission_ref,'transport':transport}},
            'source_commit':'UNBOUND_ROOT_GENUINE_RELEASE_COMMIT','paid_actions_authorized':True,
            'requested_spec':'longitudinal-adoption-5y','requested_scale':1000,
            'stages':[{'id':self.job['stage_id'],'spec':'longitudinal-adoption-5y','scale':1000,**{k:self.job[k] for k in ('spec_sha256','inventory_sha256','assignment_sha256')}}],
            'global_deadline_utc':meter_test.GLOBAL_DEADLINE,'gcloud_configuration':'r5-private-fixture','gcloud_account':'fixture.invalid','project':'fixture-project'}

    def ref(self,value):
        self.reference_serial+=1;path=self.root/f'ref-{self.reference_serial}.json';raw=value if isinstance(value,bytes) else blob(value)
        path.write_bytes(raw);path.chmod(0o600);return {'path':str(path),'sha256':digest(raw)}

    def adapter(self,kind,index):
        state=self.root/f'adapter-state-{index}';state.mkdir();cas=self.root/f'cas-{index}';cas.mkdir()
        ident=lambda p:[p.stat().st_dev,p.stat().st_ino]
        job_sha=digest(canonical(self.job))
        domain='filesystem-device:'+str(cas.stat().st_dev) if kind=='external-cas' else 'provider:google-cloud-storage'
        locator=str(cas) if kind=='external-cas' else 'gs://fixture-bucket/enrolled/job'
        enrollment={'schema':'dams-compute-persistent-backup-enrollment-v1','kind':'external-persistent','job_sha256':job_sha,
            'backup_id':'primary-cas' if kind=='external-cas' else 'gcs-private','domain':domain,'locator':locator}
        eraw=blob(enrollment);binding={k:enrollment[k] for k in ('backup_id','domain','locator')};binding['enrollment_sha256']=digest(eraw)
        config={'schema':'dams-compute-persistent-adapter-v1','kind':kind,'binding':binding,'job_sha256':job_sha,'source_sha256':pb.IMPORT_SHA,
            'state_root':str(state),'state_identity':ident(state),'max_object_bytes':65536,'timeout_seconds':2,'max_retries':0}
        config.update({'root':str(cas),'root_identity':ident(cas),'volume':str(self.root),'volume_identity':ident(self.root),'min_free_bytes':0}
            if kind=='external-cas' else {'bucket':'fixture-bucket','prefix':'enrolled/job','expected_location':'US-CENTRAL1','expected_versioning':True,
            'expected_soft_delete_seconds':604800,'expected_ubla':True,'expected_public_access_prevention':'enforced'})
        return self.ref(config),self.ref(eraw),binding

    def entry(self,c=None):
        state=self.root/f'entry-state-{len(self.entries)}';state.mkdir()
        with mock.patch('research_tools.cloud_control.stage_capabilities',return_value={}):value=e.Entry(c or self.c,root=ROOT,state=state)
        self.entries.append(value);return value

    def auth(self,argv,**options):
        self.auth_calls.append(argv)
        value={'credential':{'access_token':'fixture-token','token_expiry':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()},
               'configuration':{'properties':{'core':{'account':'fixture.invalid','project':'fixture-project'}}}}
        return subprocess.Popen(['python3','-c','import sys;sys.stdout.write('+repr(json.dumps(value))+')'],**options)

    def factory(self,entry=None):
        value=f.Factory(entry or self.entry(),self.factory_ref,self.admission_ref,popen=self.auth,transport=self.google)
        self.factories.append(value);return value

    def test_real_factory_pair_meter_before_provider_and_full_readback(self):
        value=self.factory();self.assertEqual(self.auth_calls,[]);self.assertEqual(self.google.calls,[])
        pair=value();self.assertEqual({type(b) for b in pair},{pb.ExternalCASBackend,pb.GCSBackend})
        self.assertEqual(len(self.auth_calls),1);self.assertIn('--min-expiry=1800s',self.auth_calls[0]);self.assertEqual(len(self.google.calls),2)
        snapshot=value.snapshot();self.assertEqual(snapshot['requests'],2);self.assertEqual(snapshot['completed_attempts'],2)
        self.assertEqual(snapshot['downloaded_body_bytes_upper'],2*pb.MAX_POLICY);self.assertFalse(snapshot['provider_invoice'])
        raw=b'exact opaque bytes';key='objects/'+digest(raw)+'.z';pair[0].publish(key,raw);s=pair[0].read(key)
        self.assertEqual(s.read(1024),raw);self.assertEqual(s.read(1),b'');s.close()

    def test_missing_factory_refused_without_auth(self):
        c=copy.deepcopy(self.c);c['compute_only'].pop('archive_backend_factory');c['compute_only']['component_sha256']={n:v for n,v in c['compute_only']['component_sha256'].items() if n in e.COMPONENTS}
        entry=self.entry(c)
        with self.assertRaisesRegex(ValueError,'explicit pinned'):entry.archive_backends(self.admission_ref)
        self.assertEqual(self.google.calls,[]);self.assertEqual(self.auth_calls,[])

    def test_legacy_no_factory_api_is_none(self):
        c=copy.deepcopy(self.c);c['compute_only'].pop('archive_backend_factory');c['compute_only']['component_sha256']={n:v for n,v in c['compute_only']['component_sha256'].items() if n in e.COMPONENTS}
        entry=self.entry(c);self.assertIsNone(entry.archive_backends(self.ref({'schema':'dams-compute-only-collector-v1'})))

    def test_legacy_with_factory_refused(self):
        entry=self.entry()
        with self.assertRaisesRegex(ValueError,'legacy Collector'):entry.archive_backends(self.ref({'schema':'dams-compute-only-collector-v1'}))
        self.assertEqual(self.auth_calls,[])

    def test_same_faultdomain_refuses_before_state_or_auth(self):
        self.profile['backup_bindings'][1]['domain']=self.profile['backup_bindings'][0]['domain']
        self.factory_config['storage_profile']=self.ref(self.profile);self.admission['storage_profile']=self.factory_config['storage_profile']
        self.factory_ref=self.ref(self.factory_config);self.admission_ref=self.ref(self.admission)
        with self.assertRaises(ValueError):self.factory()
        self.assertEqual(self.auth_calls,[]);self.assertEqual(self.google.calls,[])
        self.assertEqual(os.listdir(self.root/'adapter-state-0'),[])

    def test_wrong_current_job_rejects_before_auth(self):
        self.profile['job']['source_sha256']='a'*64
        self.factory_config['storage_profile']=self.ref(self.profile);self.admission['storage_profile']=self.factory_config['storage_profile']
        self.factory_ref=self.ref(self.factory_config);self.admission_ref=self.ref(self.admission)
        with self.assertRaises(ValueError):self.factory()
        self.assertEqual(self.auth_calls,[])

    def test_wrong_runtime_provider_rejects(self):
        self.c['compute_only']['collect']['transport']=self.ref({'remote_runtime_sha256':'a'*64,'provider_identity_sha256':self.job['provider_identity_sha256']})
        with self.assertRaisesRegex(ValueError,'runtime/provider'):self.factory()
        self.assertEqual(self.auth_calls,[])

    def test_missing_dependency_refuses_before_auth(self):
        self.meter_config['source_files']=self.meter_config['source_files'][:-1]
        self.factory_config['meter']=self.ref(self.meter_config);self.factory_ref=self.ref(self.factory_config)
        with self.assertRaisesRegex(ValueError,'source dependencies'):self.factory()
        self.assertEqual(self.auth_calls,[])

    def test_profile_cannot_exceed_original_retention_hold(self):
        self.profile['max_archive_encoded_bytes']=self.meter_config['limits']['max_retained_encoded_bytes']+1
        self.factory_config['storage_profile']=self.ref(self.profile);self.admission['storage_profile']=self.factory_config['storage_profile']
        self.factory_ref=self.ref(self.factory_config);self.admission_ref=self.ref(self.admission)
        with self.assertRaisesRegex(ValueError,'retention allocation'):self.factory()
        self.assertEqual(self.auth_calls,[]);self.assertEqual(self.google.calls,[])

    def test_source_manifest_includes_runtime_dependencies(self):
        manifest=json.loads((ROOT/'source-manifest.json').read_bytes())
        self.assertEqual(manifest['commit'],'UNBOUND_ROOT_GENUINE_RELEASE_COMMIT')
        for n in f.DEPENDENCIES:self.assertEqual(manifest['source_files_sha256'][n],digest((ROOT/n).read_bytes()))

    def test_meter_budget_failure_refuses_before_auth(self):
        bad=copy.deepcopy(self.mt.current);bad['tasks']['old-hold']['reserved_cost_usd_upper']='0'
        self.meter_config['current_budget']=self.ref(bad);self.factory_config['meter']=self.ref(self.meter_config);self.factory_ref=self.ref(self.factory_config)
        value=self.factory()
        with self.assertRaises(ValueError):value()
        self.assertEqual(self.auth_calls,[]);self.assertEqual(self.google.calls,[])

    def test_failed_policy_reads_remain_charged_and_no_retry(self):
        self.google.policy['location']='WRONG';value=self.factory()
        with self.assertRaises(ValueError):value()
        self.assertEqual(len(self.google.calls),1);snapshot=value.meter.snapshot()
        self.assertEqual(snapshot['requests'],1);self.assertEqual(snapshot['downloaded_body_bytes_upper'],pb.MAX_POLICY)
        with self.assertRaises(ValueError):value()
        self.assertEqual(len(self.google.calls),1)

    def test_existing_meter_requires_retained_floor(self):
        value=self.factory();value();value.close();second=self.factory()
        with self.assertRaisesRegex(ValueError,'retained minimum'):second()
        self.assertEqual(len(self.google.calls),2)

    def test_retained_floor_reopens_same_meter_and_advances(self):
        value=self.factory();value();floor=value.snapshot();value.close()
        self.factory_config['minimum_meter']=self.ref(floor);self.factory_ref=self.ref(self.factory_config)
        second=self.factory();second();self.assertEqual(second.snapshot()['requests'],4)
        self.assertEqual(second.meter.config_sha256,value.meter.config_sha256)

    def test_boolean_floor_refuses_before_auth(self):
        value=self.factory();value();floor=value.snapshot();value.close();floor['requests']=True
        self.factory_config['minimum_meter']=self.ref(floor);self.factory_ref=self.ref(self.factory_config)
        second=self.factory()
        with self.assertRaises(ValueError):second()
        self.assertEqual(len(self.google.calls),2)

    def test_late_pinned_input_changed_before_materialize(self):
        value=self.factory();Path(self.profile_ref['path']).write_bytes(b'{}')
        with self.assertRaises(ValueError):value()
        self.assertEqual(self.auth_calls,[]);self.assertEqual(self.google.calls,[])

    def test_existing_execute_forwards_lazy_factory_to_live(self):
        entry=self.entry();options={'collector_admission':self.admission_ref}
        entry.options['phase']='lifecycle';entry.options['lifecycle']=self.ref(options)
        # No actual Live/provider here; verify the unchanged original dispatch seam.
        marker=object()
        with mock.patch.object(e,'Entry',return_value=entry),mock.patch.object(entry,'archive_backends',return_value=marker),mock.patch('research_tools.compute_only_lifecycle.execute',return_value={'test':'forwarded'}) as live:
            self.assertEqual(e.execute(entry.c,entry.state.path),{'test':'forwarded'})
            self.assertIs(live.call_args.kwargs['archive_backends'],marker)

    def test_real_collector_defers_factory_then_constructs_native_archive_closure(self):
        value=self.factory()
        with Collector(self.admission_ref['path'],admission_sha256=self.admission_ref['sha256'],
                       assignment_raw=self.assignment,archive_backends=value) as collector:
            self.assertIsNotNone(collector.archive)
            self.assertEqual(collector.archive.p['job'],self.job)
            self.assertEqual(collector.archive.backends,list(value.backends))
            self.assertEqual(collector.head['requests'],0)
            self.assertEqual(value.snapshot()['requests'],2)

    def test_collector_bad_assignment_refuses_before_lazy_factory_auth(self):
        value=self.factory()
        with self.assertRaises(ValueError):
            Collector(self.admission_ref['path'],admission_sha256=self.admission_ref['sha256'],
                      assignment_raw=b'{}',archive_backends=value)
        self.assertIsNone(value.meter);self.assertEqual(self.auth_calls,[]);self.assertEqual(self.google.calls,[])

    def test_actual_legacy_collector_remains_without_archive(self):
        admission=dict(self.admission);admission['schema']=COLLECTOR_SCHEMA;admission.pop('storage_profile')
        ref=self.ref(admission)
        with Collector(ref['path'],admission_sha256=ref['sha256'],assignment_raw=self.assignment) as collector:
            self.assertIsNone(collector.archive);self.assertEqual(collector.head['requests'],0)

    def test_existing_collector_binding_cannot_be_relabelled_archive(self):
        admission=dict(self.admission);admission['schema']=COLLECTOR_SCHEMA;admission.pop('storage_profile')
        ref=self.ref(admission)
        with Collector(ref['path'],admission_sha256=ref['sha256'],assignment_raw=self.assignment) as collector:minimum=dict(collector.head)
        value=self.factory()
        with self.assertRaisesRegex(ValueError,'binding changed'):
            Collector(self.admission_ref['path'],admission_sha256=self.admission_ref['sha256'],
                      assignment_raw=self.assignment,minimum_head=minimum,archive_backends=value)
        self.assertIsNone(value.meter);self.assertEqual(self.auth_calls,[])

    def test_no_root_authorization_refuses_before_meter_or_auth(self):
        self.c['paid_actions_authorized']=False;value=self.factory()
        with self.assertRaisesRegex(ValueError,'root operation'):value()
        self.assertIsNone(value.meter);self.assertEqual(self.auth_calls,[])

    def test_original_cli_dispatches_factory_and_retains_success_meter(self):
        from research_tools import cloud_control as cc
        c=copy.deepcopy(self.c);c['execution_mode']='compute-only-iap'
        config_ref=self.ref(c);state=self.root/'cli-state';state.mkdir()
        original=f.Factory
        def construct(entry,ref,admission_ref):
            return original(entry,ref,admission_ref,popen=self.auth,transport=self.google)
        def collect(entry):
            backend_factory=entry.archive_backends(self.admission_ref)
            with Collector(self.admission_ref['path'],admission_sha256=self.admission_ref['sha256'],
                    assignment_raw=self.assignment,archive_backends=backend_factory) as collector:
                return {'scope':'actual-CLI-factory-ArchiveClosure-construction-only','science_complete':False}
        with mock.patch.object(cc,'checked_config',side_effect=lambda value:value),mock.patch.object(cc,'stage_capabilities',return_value={}),\
                mock.patch.object(f,'Factory',side_effect=construct),mock.patch.object(e.Entry,'collect',collect),mock.patch('sys.stdout',new_callable=io.StringIO):
            code=cc.main(['execute','--private-config',config_ref['path'],'--state-dir',str(state),'--spec','longitudinal-adoption-5y','--scale','1000'])
        self.assertEqual(code,0);receipt=json.loads((state/('f'*64+'-BACKEND-METER.json')).read_bytes())
        self.assertIs(receipt['operation_succeeded'],True);self.assertEqual(receipt['meter']['requests'],2)

    def test_existing_execute_retains_failed_operation_meter(self):
        entry=self.entry();original=f.Factory
        def construct(en,ref,admission_ref):return original(en,ref,admission_ref,popen=self.auth,transport=self.google)
        def collect(en):
            factory=en.archive_backends(self.admission_ref);factory()
            raise ValueError('intentional failed owned operation')
        self.google.policy['location']='WRONG'
        with mock.patch.object(e,'Entry',return_value=entry),mock.patch.object(f,'Factory',side_effect=construct),mock.patch.object(entry,'collect',side_effect=lambda:collect(entry)):
            with self.assertRaises(ValueError):e.execute(entry.c,entry.state.path)
        receipt=json.loads((entry.state.path/('f'*64+'-BACKEND-METER.json')).read_bytes())
        self.assertIs(receipt['operation_succeeded'],False);self.assertEqual(receipt['meter']['requests'],1)
        self.assertEqual(receipt['meter']['downloaded_body_bytes_upper'],pb.MAX_POLICY)


if __name__=='__main__':unittest.main()
