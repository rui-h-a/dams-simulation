"""Synthetic metadata/stage/native controls only; no science, SQL or provider.

Fixture clock/physical free-space observations are explicit mocks, not evidence
of actual Root authority or native device capacity. Production guards are real.
"""
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import test_original_longitudinal_selection as legacy
from research_tools import frozen_science_cloud as f
from research_tools import original_longitudinal_selector as s
from research_tools import compute_only_worker as worker
from research_tools import compute_only_entry as entry
from research_tools import compute_only_lifecycle as lifecycle
from research_tools import cloud_control as control


class StageBinding(unittest.TestCase):
    def setUp(self):
        self.legacy=legacy.SelectedMetadata('test_actual_pinned144_first_original_sorted_case');self.legacy.setUp()
        self.clock=patch.object(s.time,'time',return_value=1791806400.0);self.clock.start()
        self.work=self.legacy.work
        self.h=self.legacy.handoff()
        self.h['observed_utc']='2026-10-12T12:00:00Z'
        handoff_raw=s.canonical(self.h);(self.work/'selected-root-handoff.json').write_bytes(handoff_raw)
        self.a=copy.deepcopy(self.legacy.a);self.a['root_handoff_sha256']=s.digest(handoff_raw)
        assignment_raw=s.canonical(self.a);(self.work/'selected-original-case.json').write_bytes(assignment_raw)
        self.selected={'schema':s.SCHEMA,'assignment_sha256':s.digest(assignment_raw)}
        prepared=s.prepare(self.work,self.a)
        self.e0={'schema':'DAMS-root-original-before-start-handoff-evaluation-1',
            'evaluated_utc':'2026-10-12T12:00:00Z','original_root_handoff_sha256':self.a['root_handoff_sha256'],
            'selected_assignment_sha256':self.selected['assignment_sha256'],'case_id':s.CID,
            'source_sha256':s.CORE,'driver_sha256':s.DRIVER,'original_runtime_limits_sha256':s.RUNTIME,
            'result':'ROOT_BEFORE_START_ORIGINAL_HANDOFF_VALIDATED'}
        e0raw=s.canonical(self.e0);(self.work/'selected-before-provider-evaluation.json').write_bytes(e0raw)
        self.h1=copy.deepcopy(self.h);self.h1['observed_utc']='2026-10-12T12:10:00Z'
        self.h1['writer_absence_receipt_sha256']='d'*64
        h1raw=s.canonical(self.h1);(self.work/'selected-activation-handoff.json').write_bytes(h1raw)
        self.e1={**self.e0,'evaluated_utc':'2026-10-12T12:10:00Z','original_root_handoff_sha256':s.digest(h1raw)}
        e1raw=s.canonical(self.e1);(self.work/'selected-activation-evaluation.json').write_bytes(e1raw)
        self.r=legacy.ComputeCompatibility().runtime();self.r['original_selected_case']=self.selected
        self.r['original_selected_activation']={'schema':'DAMS-original-selected-activation-1',
            'handoff_sha256':s.digest(h1raw),'prior_evaluation_sha256':s.digest(e0raw),'evaluation_sha256':s.digest(e1raw)}
        p=self.r['preservation_profile'];p['schema']=f.SELECTED_SCHEMA
        p['handoff_receipt_sha256']=self.a['root_handoff_sha256']
        p['selected_capacity']={'schema':'DAMS-original-single-case-storage-bound-1','case_id':s.CID,
            'config_sha256':s.CONFIG,'assignment_sha256':self.selected['assignment_sha256'],
            'selection_identity_sha256':prepared['selection_identity_sha256'],
            'original_runtime_limits_sha256':s.RUNTIME,'attempt_raw_upper_bytes':50299409408,
            'max_attempts':2,'output_metadata_upper_bytes':f.SELECTED_METADATA_BYTES,
            'parent_raw_bytes':sum(v['bytes'] for v in self.a['parent_files'].values()),'parent_raw_files':13}
        p['raw_bytes']=2*50299409408+f.SELECTED_METADATA_BYTES;p['archive_max_raw_bytes']=p['raw_bytes']
        p['raw_files']=256;p['archive_max_files']=256
        self.stage={'id':'synthetic-original-selected','spec':self.r['spec'],'scale':self.r['scale'],
            'machine_type':self.r['machine_type'],'expected_guest':self.r['expected_guest'],
            'preservation_profile':copy.deepcopy(p),'runtime_limits':copy.deepcopy(self.r['runtime_limits']),
            'pipeline_stop_grace_seconds':300,'termination_action':'STOP','max_result_gib':'192','max_egress_gib':'192',
            'max_seconds':3600,'transport_codec':'deflate-chunks-v1','archive_max_raw_bytes':p['raw_bytes'],
            'archive_min_free_bytes':p['min_free_bytes'],'archive_max_files':256,'disk_gib':300,
            'original_selected_case':copy.deepcopy(self.selected),
            'selected_assignment':{'path':str(self.work/'selected-original-case.json'),
                                   'sha256':self.selected['assignment_sha256']},
            'selected_handoff_evaluation':{'path':str(self.work/'selected-before-provider-evaluation.json'),
                                          'sha256':s.digest(e0raw)},
            'root_handoff_work':str(self.work),'selection_identity_sha256':prepared['selection_identity_sha256']}

    def tearDown(self):self.clock.stop();self.legacy.tearDown()

    def phase(self,r):
        transfer={'stage_id':self.stage['id'],'expected_source_sha256':entry.source_hash(),
                  'deadline_utc':r['deadline_utc'],'max_spool_bytes':1024,'max_transfer_bytes':1024}
        r['transfer_config_sha256']=entry.digest(entry.blob(transfer))
        p={'vm':{'id':'1'},'disk':{'name':'synthetic-boot'},'termination_utc':r['deadline_utc'],'approval_sha256':'8'*64}
        r['provider_identity_sha256']=entry.digest(entry.blob({'vm':p['vm'],'disk':p['disk']}))
        d={'provider_identity_sha256':r['provider_identity_sha256'],
           'source_manifest_sha256':r['source_manifest_sha256'],'measurements':{'instance_id':'1'}}
        refs={k:entry.blob(v) for k,v in [('provider',p),('doctor',d),('runtime',r),('transfer',transfer)]}
        obj=entry.PhaseContinuation.__new__(entry.PhaseContinuation)
        obj.entry=SimpleNamespace(c={'source_commit':r['source_commit']},stage=self.stage,
                                  manifest_sha=r['source_manifest_sha256'],reference=lambda ref:refs[ref])
        obj.value={**{k:k for k in refs},'approval':{'sha256':'8'*64}};obj.hold={'termination_utc':r['deadline_utc']}
        with patch.object(control,'validate_compute_only_instance',return_value=None),\
             patch.object(control,'stage_capabilities',return_value={'purchase_mode':'STANDARD'}),\
             patch.object(worker,'verify_identity',return_value=None):
            return obj._enrollment()

    def test_full9_assignment_and_parent_identity_before_stage(self):
        prepared=f.validate_selected_stage(self.stage)
        self.assertEqual(prepared['selection_identity_sha256'],self.stage['selection_identity_sha256'])
        self.assertEqual(len(prepared['input_native']),5)

    def test_missing_assignment_or_wrong_SHA_or_stage_identity_refused(self):
        for key in ('selected_assignment','root_handoff_work','selection_identity_sha256'):
            v=copy.deepcopy(self.stage);v.pop(key)
            with self.assertRaises((ValueError,FileNotFoundError)):f.validate_selected_stage(v)
        v=copy.deepcopy(self.stage);v['original_selected_case']['assignment_sha256']='f'*64
        with self.assertRaises(ValueError):f.validate_selected_stage(v)

    def test_mini6_assignment_and_wrong_parent_input_scope_refused(self):
        a={k:v for k,v in self.a.items() if k in ('schema','case_id','config_sha256','source_sha256','driver_sha256','spec_sha256')}
        p=self.work/'selected-original-case.json';p.write_bytes(s.canonical(a))
        stage=copy.deepcopy(self.stage);sha=s.digest(p.read_bytes());stage['selected_assignment']['sha256']=sha
        stage['original_selected_case']['assignment_sha256']=sha;stage['preservation_profile']['selected_capacity']['assignment_sha256']=sha
        with self.assertRaises(ValueError):f.validate_selected_stage(stage)

    def test_parent_bytes_or_selection_binding_cannot_be_understated(self):
        for key,value in [('parent_raw_bytes',1),('selection_identity_sha256','f'*64)]:
            stage=copy.deepcopy(self.stage);stage['preservation_profile']['selected_capacity'][key]=value
            with self.assertRaises(ValueError):f.validate_selected_stage(stage)

    def test_subset_bound_keeps_twoattempts_and_original789(self):
        p=self.r['preservation_profile'];original=s.digest(s.canonical(self.r['runtime_limits']))
        bound=f.selected_storage_bound(self.selected,p,self.r['runtime_limits'])
        self.assertEqual(bound['output_raw_upper_bytes'],100632373248)
        self.assertEqual(bound['parent_input_upper_bytes'],1037296713)
        self.assertIs(f.validate_profile(p,self.r['runtime_limits'],selected=self.selected),p)
        self.assertEqual(s.digest(s.canonical(self.r['runtime_limits'])),original)
        self.assertEqual(self.r['runtime_limits']['batch_max_output_bytes'],206158430208)

    def test_oneattempt_or_smaller_fullcase_or_metadata_is_refused(self):
        for key,value in [('max_attempts',1),('attempt_raw_upper_bytes',50299409407),('output_metadata_upper_bytes',1)]:
            p=copy.deepcopy(self.r['preservation_profile']);p['selected_capacity'][key]=value
            with self.assertRaises(ValueError):f.validate_profile(p,self.r['runtime_limits'],selected=self.selected)

    def test_batch_default_cannot_use_singlecase_profile(self):
        old=legacy.ComputeCompatibility().runtime();p=old['preservation_profile'];p['raw_bytes']=100632373248
        with self.assertRaisesRegex(ValueError,'original raw'):f.validate_profile(p,old['runtime_limits'])
        with self.assertRaises(ValueError):f.validate_profile(self.r['preservation_profile'],self.r['runtime_limits'])
        self.assertIs(f.validate_selected_stage({'id':'synthetic-default'}),None)

    def test_original_generic_runtime_and_selected_legacy_API_unchanged(self):
        old=legacy.ComputeCompatibility().runtime();self.assertIs(worker.validate_runtime(old),old)
        old.pop('original_selected_case');self.assertIs(worker.validate_runtime(old),old)
        self.assertEqual(worker.pipeline_command('/source','/work','/work/output',old,'a'*64,{})[0],'/source/run.sh')

    def test_matching_runtime_stage_enrollment_real_predicate_accepts(self):
        self.assertEqual(self.phase(copy.deepcopy(self.r))[0],{'id':'1'})

    def test_original_wrongassignment_and_missingselector_counterexamples_closed(self):
        r=copy.deepcopy(self.r);r['original_selected_case']['assignment_sha256']='7'*64
        with self.assertRaises(Exception):self.phase(r)
        r=copy.deepcopy(self.r);r.pop('original_selected_case')
        with self.assertRaises(Exception):self.phase(r)

    def test_zeroMetadata_badfloor_wrongsource_refused(self):
        for key,value in [('min_free_bytes',1),('source_sha256','f'*64),('raw_bytes',1),('raw_files',0)]:
            p=copy.deepcopy(self.r['preservation_profile']);p[key]=value
            with self.assertRaises(ValueError):f.validate_profile(p,self.r['runtime_limits'],selected=self.selected)

    def test_stop_and_deadline_callsites_forward_explicit_subset(self):
        c={'execution_mode':'compute-only-iap','global_deadline_utc':self.r['runtime_limits']['deadline_utc']}
        with patch.object(control,'stage_capabilities',return_value={'purchase_mode':'STANDARD'}):
            self.assertEqual(control.stage_termination_action(c,self.stage,compute_only=True),'STOP')
        self.assertEqual(control.stage_science_deadlines(self.stage,self.r['deadline_utc']),
                         {k:self.r['runtime_limits'][k] for k in ('deadline_utc','stop_cutoff_utc')})

    def test_archive_two_trees_and_floor_still_required(self):
        self.assertEqual(control.stage_archive_options(self.stage)['archive_max_raw_bytes'],100632373248)
        bad=copy.deepcopy(self.stage);bad['disk_gib']=200
        with self.assertRaises(control.GuardError):control.stage_archive_options(bad)

    def test_output_only_CID_and_failed_attempts_guard_without_rawIO(self):
        out=self.work/'output';(out/'cases'/s.CID/'attempt-000').mkdir(parents=True)
        (out/'cases'/s.CID/'attempt-001').mkdir();(out/'cloud-execution.json').write_bytes(b'{}')
        p=self.r['preservation_profile']
        with patch.object(f.shutil,'disk_usage',return_value=SimpleNamespace(free=128*2**30)):
            self.assertEqual(f.check_output_capacity(out,p,selected=self.selected)['files'],1)
            (out/'cases'/s.CID/'attempt-002').mkdir()
            with self.assertRaises(ValueError):f.check_output_capacity(out,p,selected=self.selected)

    def test_other_case_and_symlink_output_refused(self):
        out=self.work/'output';(out/'cases'/('f'*64)).mkdir(parents=True)
        with self.assertRaises(ValueError):f.check_output_capacity(out,self.r['preservation_profile'],selected=self.selected)
        link=self.work/'linked-output';link.mkdir();(link/'alias').symlink_to(self.work/'original-runtime-limits.json')
        with self.assertRaises(ValueError):f.check_output_capacity(link,self.r['preservation_profile'],selected=self.selected)

    def test_native_floor_not_reduced_by_singleton(self):
        out=self.work/'empty-output';out.mkdir()
        with patch.object(f.shutil,'disk_usage',return_value=SimpleNamespace(free=80*2**30-1)):
            with self.assertRaisesRegex(ValueError,'free floor'):f.check_output_capacity(out,self.r['preservation_profile'],selected=self.selected)

    def test_entry_beforelaunch_and_activation_have_same_binding(self):
        root=Path(f.__file__).resolve().parents[1]
        et=ast.parse((root/'research_tools/compute_only_entry.py').read_bytes())
        ctor=next(m for c in et.body if isinstance(c,ast.ClassDef) and c.name=='Entry'
                  for m in c.body if isinstance(m,ast.FunctionDef) and m.name=='__init__')
        calls=[n.func.id for n in ast.walk(ctor) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        self.assertIn('validate_selected_stage',calls)
        for cls,method in [(entry.PhaseContinuation,'_enrollment'),(lifecycle.Live,'load_runtime')]:
            import inspect
            self.assertIn("get('original_selected_case')",inspect.getsource(getattr(cls,method)))

    def test_real_entry_constructor_refuses_wrong_singleton_before_prepare_or_provider(self):
        stage=copy.deepcopy(self.stage);stage['selected_assignment']['sha256']='f'*64
        options={k:None for k in entry.FIELDS};options.update(schema=entry.SCHEMA,phase='plan',
            operation_id='a'*64,stage_id=stage['id'])
        config={'compute_only':options,'stages':[stage],'execution_stage_ids':[stage['id']],
                'requested_spec':stage['spec'],'requested_scale':1000}
        with patch.object(control,'Gcloud',side_effect=AssertionError('NO_PROVIDER_CONSTRUCTOR')):
            with self.assertRaisesRegex(ValueError,'assignment pin differs'):entry.Entry(config)

    def test_actual_regional_predicate_uses_subset_and_separate_parent(self):
        p=self.r['preservation_profile']
        bounds={'scientific_raw_upper_bytes':p['raw_bytes'],'spool_upper_bytes':1024,
            'collector_cache_upper_bytes':1024,'local_cas_upper_bytes':1024,
            'raw_lease_upper_bytes':50299409408,'recovery_input_upper_bytes':1037296713,
            'metadata_and_boot_upper_bytes':4096,'safety_floor_bytes':80*2**30}
        obj=lifecycle.Live.__new__(lifecycle.Live);obj.regional=True;obj.runtime=copy.deepcopy(self.r)
        obj.vm={'id':'synthetic-vm'};obj.disk={'name':'synthetic-disk'}
        plan={'bounds':bounds};raw=entry.blob(plan);transfer=entry.blob({'max_spool_bytes':1024,'max_transfer_bytes':1024})
        obj.o={'capacity_plan':{'path':'synthetic-plan'},'transfer':{'path':'synthetic-transfer'}}
        obj.entry=SimpleNamespace(manifest_sha='e'*64,reference=lambda ref:raw if ref['path']=='synthetic-plan' else transfer)
        obj.load_runtime=lambda:None;obj._fresh_provider=lambda:None
        exchanges=[]
        obj.exchange=lambda action,request:(exchanges.append((action,request)) or entry.blob({
            'plan_sha256':entry.digest(request),'source_manifest_sha256':'e'*64,
            'provider_identity_sha256':entry.digest(entry.blob({'vm':obj.vm,'disk':obj.disk}))}))
        obj.event=lambda name,result:result
        self.assertEqual(obj.regional_capacity()['plan_sha256'],entry.digest(raw))
        self.assertEqual(len(exchanges),1)
        # Raw excludes the parent, while recovery inputs include it exactly
        # once. This is a fake plan/exchange, not an actual native free PASS.
        plan['bounds']['recovery_input_upper_bytes']=1037296712;raw=entry.blob(plan);exchanges.clear()
        with self.assertRaisesRegex(ValueError,'capacity plan'):obj.regional_capacity()
        self.assertEqual(exchanges,[])

    def test_bootstrap_over300_scope_continuations_pass_H0_current_start_refuses(self):
        prepared=f.validate_selected_stage(self.stage,now=1791807000.0)
        self.assertEqual(prepared['row']['case_id'],s.CID)
        with self.assertRaisesRegex(ValueError,'lease expired'):
            f.validate_selected_stage(self.stage,now=1791807000.0,require_fresh_handoff=True)
        # A fresh, differently named H1 retains ALL H0 scope and original caps.
        s.validate_activation_handoff(self.h1,self.h,prepared,now=1791807000.0)

    def test_H1_changes_scope_or_relabels_already_started_refused(self):
        prepared=s.prepare(self.work,self.a)
        for key,value in [('selected_case_unstarted',False),('deadline_utc','2030-01-01T00:00:00Z'),
                          ('selection_identity_sha256','f'*64),('parent_full_raw_gate_sha256','f'*64)]:
            current=copy.deepcopy(self.h1);current[key]=value
            with self.assertRaises(ValueError):s.validate_activation_handoff(current,self.h,prepared,now=1791807000.0)
        with self.assertRaises(ValueError):s.validate_activation_handoff(self.h,self.h,prepared,now=1791807000.0)

    def test_missing_H1_wrong_priorEval_and_unbound_argv_refused(self):
        r=copy.deepcopy(self.r);r.pop('original_selected_activation')
        with self.assertRaises(Exception):worker.validate_runtime(r)
        r=copy.deepcopy(self.r);r['original_selected_activation']['prior_evaluation_sha256']='f'*64
        with self.assertRaises(Exception):self.phase(r)
        argv=worker.pipeline_command('/source','/work','/work/output',self.r,'a'*64,{})
        self.assertIn('--activation-handoff-sha256',argv)
        self.assertIn(self.r['original_selected_activation']['evaluation_sha256'],argv)

    def test_true_selected_API_uses_H1_after_longbootstrap_with_mocked_payload_and_scheduler(self):
        out=self.work/'synthetic-output';out.mkdir()
        calls=[]
        class TinyScheduler:
            def __init__(self,*args):self.owned_workers_absent=True
            def run(self,configs,origins):calls.append((configs,origins));return [{'synthetic':True}]
        with patch.object(s.time,'time',return_value=1791807000.0),\
             patch.object(s,'verify_parent_payload',return_value={}):
            value=s.run_selected(self.work,out,self.a,self.legacy.guest(),'1'*40,
                scheduler_factory=TinyScheduler,guard=lambda:None,activation_handoff=self.h1,
                prior_evaluation=self.e0,activation_evaluation=self.e1,
                assignment_sha256=self.selected['assignment_sha256'],
                activation_handoff_sha256=self.r['original_selected_activation']['handoff_sha256'])
        self.assertEqual(value['case_id'],s.CID);self.assertEqual(len(calls[0][0]),1)
        self.assertEqual(calls[0][0][0].days,3743)


if __name__=='__main__':unittest.main()
