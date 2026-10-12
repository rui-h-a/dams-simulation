"""Isolated synthetic custody/authority; original byte gates, no Model or SQL.

All 13-file payloads here are tiny synthetic bytes, NOT scientific raw data.
Root-looking fixture statuses exercise schema predicates, not actual issuance.
Mixed census uses an explicit fake semantic verifier and is not a raw PASS.
A fixed synthetic UTC keeps immutable original deadlines reproducible after
their expiry; it never changes the adapter's real default clock/authority.
"""
import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'research_tools'))
from research_tools import original_longitudinal_return as r
from research_tools import original_longitudinal_selector as s
from dams_sim.config import Config
from dams_sim.runtime import RuntimeLimits
from dams_sim.scheduler import Scheduler
from dams_sim.storage import canonical,digest

FIXTURE = ROOT/'tests/fixtures/original_selected'


class ReturnCompatibility(unittest.TestCase):
    def setUp(self):
        self.fixture_now=datetime(2026,10,12,12,tzinfo=timezone.utc)
        clock_patch=patch.object(r.time,'time',return_value=self.fixture_now.timestamp())
        clock_patch.start();self.addCleanup(clock_patch.stop)
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name).resolve()
        self.out = self.base/'output'; self.out.mkdir(); self.meta = self.base/'meta'; self.meta.mkdir()
        self.work = self.meta/'original-selection'; self.work.mkdir()
        # Copy exactly the five immutable SMALL metadata inputs. The original
        # 13 parent file pins include payload, but no parent payload is present
        # or opened by these return controls.
        for relative in s.INPUTS:
            destination = self.work/relative; destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(FIXTURE/relative,destination)
        self.assignment = s.parse((FIXTURE/'selected-original-case.json').read_bytes())
        prepared = s.prepare(self.work,self.assignment)
        self.lock_path = '/synthetic-original/.pipeline.lock'
        lock_patch = patch.object(s,'ORIGINAL_PIPELINE_LOCK_PATH_SHA256',digest(self.lock_path.encode()))
        lock_patch.start(); self.addCleanup(lock_patch.stop)
        now = self.fixture_now
        self.handoff = {'schema':'DAMS-original-selected-handoff-1',
            'status':'ROOT_EXCLUSIVE_ORIGINAL_PIPELINE_HANDOFF',
            'selection_identity_sha256':prepared['selection_identity_sha256'],
            'original_runtime_limits_sha256':s.RUNTIME,'source_sha256':s.CORE,'driver_sha256':s.DRIVER,
            'pipeline_lock':{'path':self.lock_path,'dev':1,'ino':2,'owner_pid':4242,
                            'owner_birth':'synthetic-epoch','exclusive_held':True},
            'writer_absence_receipt_sha256':'a'*64,'parent_full_raw_gate_sha256':'b'*64,
            'deadline_utc':prepared['original_limits']['deadline_utc'],
            'observed_utc':(now-timedelta(hours=2)).isoformat(),'lease_timeout_seconds':120,
            'selected_case_unstarted':True,'return_import_status':'RETURN_IMPORT_BLOCKED'}
        handoff_path = self.work/'selected-root-handoff.json'; handoff_path.write_bytes(canonical(self.handoff))
        self.assignment['root_handoff_sha256'] = self.pin(handoff_path)['sha256']
        assignment_path = self.work/'selected-original-case.json'; assignment_path.write_bytes(canonical(self.assignment))
        assignment_pin = self.pin(assignment_path)
        self.evaluation = {'schema':'DAMS-root-original-before-start-handoff-evaluation-1',
            'evaluated_utc':(now-timedelta(hours=2)+timedelta(seconds=30)).isoformat(),
            'original_root_handoff_sha256':self.assignment['root_handoff_sha256'],
            'selected_assignment_sha256':assignment_pin['sha256'],'case_id':s.CID,
            'source_sha256':s.CORE,'driver_sha256':s.DRIVER,'original_runtime_limits_sha256':s.RUNTIME,
            'result':'ROOT_BEFORE_START_ORIGINAL_HANDOFF_VALIDATED'}
        evaluation_pin = self.write_work('selected-before-provider-evaluation.json',self.evaluation)
        self.activation=copy.deepcopy(self.handoff)
        self.activation.update(observed_utc=(now-timedelta(seconds=25)).isoformat(),
                               writer_absence_receipt_sha256='c'*64)
        activation_pin=self.write_work('selected-activation-handoff.json',self.activation)
        self.activation_evaluation=copy.deepcopy(self.evaluation)
        self.activation_evaluation.update(evaluated_utc=(now-timedelta(seconds=22)).isoformat(),
                                          original_root_handoff_sha256=activation_pin['sha256'])
        activation_evaluation_pin=self.write_work('selected-activation-evaluation.json',self.activation_evaluation)
        self.inventory = s.parse((FIXTURE/'original-inventory.json').read_bytes())
        self.rows = {x['case_id']:x for x in self.inventory}
        self.limits = s.parse((FIXTURE/'original-runtime-limits.json').read_bytes())
        self.guest = {'environment':'GCP','packaged_commit':s.COMMIT,'instance_id':'synthetic-123',
                      'machine_type':'synthetic-machine','zone':'synthetic-zone'}
        self.parent_semantic = 'd90d2debbf315f3c195129dad17838e9922a4208a013700c04f327264001848a'
        self.branch = {'parent_case_id':s.PARENT,'parent_source_sha256':s.CORE,
                       'parent_config_sha256':digest(canonical(self.rows[s.PARENT]['config'])),
                       'parent_day':366,'parent_state_semantic_sha256':self.parent_semantic}
        self.manifests = {}
        self.child = self.make_case(s.CID,self.guest,self.branch)
        self.parent = self.make_case(s.PARENT,self.limits['provenance'],None)
        package = {'commit':'1'*40,'source_files_sha256':{
            p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in (ROOT/'dams_sim').glob('*.py')}}
        package['source_files_sha256']['run.sh'] = s.ENTRY
        package_pin = self.write('package.json',package)
        self.guest_runtime = {'schema':'DAMS-compute-only-guest-1',
            'spec':'longitudinal-adoption-5y','scale':1000,'runtime_limits':self.limits,
            'preservation_profile':{'original_runtime_limits_sha256':s.RUNTIME},
            'original_selected_case':{'schema':s.SCHEMA,'assignment_sha256':assignment_pin['sha256']},
            'original_selected_activation':{'schema':'DAMS-original-selected-activation-1',
                'handoff_sha256':activation_pin['sha256'],'prior_evaluation_sha256':evaluation_pin['sha256'],
                'evaluation_sha256':activation_evaluation_pin['sha256']}}
        runtime_pin = self.write('guest-runtime.json',self.guest_runtime)
        self.execution = {'schema':'DAMS-compute-only-execution-1','selected_case_id':s.CID,
                     'start_utc':(now-timedelta(seconds=20)).isoformat(),
                     'end_utc':(now-timedelta(seconds=10)).isoformat(),'exit_code':0,
                     'owned_pipeline_group_verification':{'owned_pipeline_group_absent':True},
                     'inputs_unchanged':True,'science_complete':False,
                     'original_activation_handoff_sha256':activation_pin['sha256'],
                     'original_activation_evaluation_sha256':activation_evaluation_pin['sha256'],
                     'original_before_provider_evaluation_sha256':evaluation_pin['sha256'],
                     'source_manifest_sha256':package_pin['sha256'],'source_commit':package['commit'],
                     'runtime_sha256':runtime_pin['sha256'],'original_runtime_limits_sha256':s.RUNTIME,
                     'guest_hardware_verification':{'hardware_frozen_verified':True,
                        **{k:self.guest[k] for k in ('instance_id','machine_type','zone')}}}
        execution_pin = self.write('execution.json',self.execution)
        mp = self.pin(self.child/'manifest.json')
        raw_native = {p.name:s.native(p.lstat()) for p in self.child.iterdir()}
        self.gate = {'schema':'DAMS-root-original-external-full-raw-gate-1','status':'ROOT_FULL_ORIGINAL_CASE_RAW_VALIDATED',
                'case_id':s.CID,'config_sha256':s.CONFIG,'source_sha256':s.CORE,'driver_sha256':s.DRIVER,
                'original_runtime_sha256':s.RUNTIME,'validator_sha256':r.RAW_SHA,'branch_identity':self.branch,
                'expected_provenance':self.guest,'manifest_sha256':mp['sha256'],
                'source_package_sha256':package_pin['sha256'],'guest_execution_sha256':execution_pin['sha256'],
                'original_handoff_evaluation_pin':evaluation_pin,
                'original_handoff_evaluated_utc':self.evaluation['evaluated_utc'],
                'selected_assignment_sha256':assignment_pin['sha256'],
                'original_root_handoff_sha256':self.assignment['root_handoff_sha256'],
                'original_activation_evaluation_pin':activation_evaluation_pin,
                'original_activation_evaluated_utc':self.activation_evaluation['evaluated_utc'],
                'original_activation_handoff_sha256':activation_pin['sha256'],
                'raw_files_sha256':{**self.manifests[s.CID]['output_sha256'],'manifest.json':mp['sha256']}}
        self.custody = {'schema':'DAMS-root-closed-original-case-custody-1','status':'ROOT_CLOSED_CURRENT_RAW_CUSTODY',
                    'case_id':s.CID,'manifest_sha256':mp['sha256'],'raw_native':raw_native,
                    'observed_utc':(now-timedelta(seconds=5)).isoformat(),
                    'guest_execution_sha256':execution_pin['sha256'],
                    'original_activation_handoff_sha256':activation_pin['sha256'],
                   'active_writers':[],'required_hot_consumers':[],'producer_exit_code':0,'producer_reaped':True}
        self.record = {'case_id':s.CID,'config_sha256':s.CONFIG,'branch_identity':self.branch,'expected_provenance':self.guest,
                       'attempt_relative':str(self.child.relative_to(self.out)),'manifest_pin':mp,'raw_native':raw_native,
                       'raw_gate_pin':self.write('raw-gate.json',self.gate),'custody_pin':self.write('custody.json',self.custody),
                       'guest_execution_pin':execution_pin,'source_package_pin':package_pin,
                       'guest_runtime_pin':runtime_pin,'selected_assignment_pin':assignment_pin}
        issued = self.fixture_now; expires = issued+timedelta(hours=1)
        self.v = {'schema':'DAMS-original-external-acceptance-registry-1','status':'ROOT_ISSUED_TRUTHFUL_EXTERNAL_CASE_ACCEPTANCE',
                  'source_sha256':s.CORE,'driver_sha256':s.DRIVER,'entry_sha256':s.ENTRY,'spec_sha256':s.SPEC,
                  'inventory_file_sha256':s.INVENTORY,'original_runtime_sha256':s.RUNTIME,
                  'issued_utc':issued.isoformat(),'expires_utc':expires.isoformat(),'cases':[self.record]}
        self.inventory_pin = self.pin(FIXTURE/'original-inventory.json')
        self.runtime_pin = self.pin(FIXTURE/'original-runtime-limits.json')

    def tearDown(self): self.temp.cleanup()

    def pin(self,p):
        raw = p.read_bytes(); return {'path':str(p),'sha256':digest(raw),'bytes':len(raw)}

    def write(self,name,value):
        p = self.meta/name; p.write_bytes(canonical(value)); return self.pin(p)

    def write_work(self,name,value):
        p=self.work/name;p.write_bytes(canonical(value));return self.pin(p)

    def changed_scope(self, *, assignment=None, handoff=None, evaluation=None, execution=None, gate=None,
                      activation=None, activation_evaluation=None, runtime=None, custody=None):
        """Coherently re-pin synthetic receipts, exposing semantic blind spots.

        A stale outer SHA would reject trivially. Every dependent mock pin here
        is updated, while the real selector/lease validators remain unmocked.
        These Root-looking fields never establish actual Root issuance.
        """
        h = copy.deepcopy(self.handoff)
        if handoff:h.update(handoff)
        hp = self.work/'selected-root-handoff.json'; hp.write_bytes(canonical(h))
        a = copy.deepcopy(self.assignment if assignment is None else assignment)
        if assignment is None or a.get('root_handoff_sha256') == self.assignment['root_handoff_sha256']:
            a['root_handoff_sha256'] = self.pin(hp)['sha256']
        ap = self.work/'selected-original-case.json'; ap.write_bytes(canonical(a)); assignment_pin = self.pin(ap)
        ev = copy.deepcopy(self.evaluation)
        ev.update(original_root_handoff_sha256=a.get('root_handoff_sha256'),
                  selected_assignment_sha256=assignment_pin['sha256'])
        if evaluation:ev.update(evaluation)
        evaluation_pin = self.write_work('selected-before-provider-evaluation.json',ev)
        ex=copy.deepcopy(self.execution)
        if execution:ex.update(execution)
        start=datetime.fromisoformat(ex['start_utc'])
        h1=copy.deepcopy(h)
        h1.update(observed_utc=(start-timedelta(seconds=5)).isoformat(),writer_absence_receipt_sha256='c'*64)
        if activation:h1.update(activation)
        h1pin=self.write_work('selected-activation-handoff.json',h1)
        e1=copy.deepcopy(self.activation_evaluation)
        e1.update(selected_assignment_sha256=assignment_pin['sha256'],original_root_handoff_sha256=h1pin['sha256'],
                  evaluated_utc=(start-timedelta(seconds=2)).isoformat())
        if activation_evaluation:e1.update(activation_evaluation)
        e1pin=self.write_work('selected-activation-evaluation.json',e1)
        runtime_value=copy.deepcopy(self.guest_runtime)
        runtime_value['original_selected_case']['assignment_sha256']=assignment_pin['sha256']
        runtime_value['original_selected_activation']={'schema':'DAMS-original-selected-activation-1',
            'handoff_sha256':h1pin['sha256'],'prior_evaluation_sha256':evaluation_pin['sha256'],
            'evaluation_sha256':e1pin['sha256']}
        if runtime:runtime_value.update(runtime)
        runtime_pin=self.write('changed-runtime.json',runtime_value)
        ex.update(runtime_sha256=runtime_pin['sha256'],original_activation_handoff_sha256=h1pin['sha256'],
                  original_activation_evaluation_sha256=e1pin['sha256'],
                  original_before_provider_evaluation_sha256=evaluation_pin['sha256'])
        if execution:ex.update(execution)
        execution_pin = self.write('changed-execution.json',ex)
        g = copy.deepcopy(self.gate)
        g.update(original_handoff_evaluation_pin=evaluation_pin,original_handoff_evaluated_utc=ev['evaluated_utc'],
                 selected_assignment_sha256=assignment_pin['sha256'],
                 original_root_handoff_sha256=a.get('root_handoff_sha256'),
                 original_activation_evaluation_pin=e1pin,original_activation_evaluated_utc=e1['evaluated_utc'],
                 original_activation_handoff_sha256=h1pin['sha256'],
                 guest_execution_sha256=execution_pin['sha256'])
        if gate:g.update(gate)
        owner=copy.deepcopy(self.custody)
        owner.update(guest_execution_sha256=execution_pin['sha256'],original_activation_handoff_sha256=h1pin['sha256'])
        if custody:owner.update(custody)
        record = copy.deepcopy(self.record)
        record.update(selected_assignment_pin=assignment_pin,guest_runtime_pin=runtime_pin,
                      guest_execution_pin=execution_pin,raw_gate_pin=self.write('changed-gate.json',g),
                      custody_pin=self.write('changed-custody.json',owner))
        value = copy.deepcopy(self.v); value['cases'] = [record]; return value

    def make_case(self,cid,provenance,branch):
        path = self.out/'cases'/cid/'attempt-000'; path.mkdir(parents=True)
        config = self.rows[cid]['config']
        names = list(s.parse((FIXTURE/'selected-parent/manifest.json').read_bytes())['output_sha256'])
        outputs = {}
        for name in names:
            data = canonical({'days_completed':config['days'],'n':config['n']}) if name=='summary.json' else b'{}'
            (path/name).write_bytes(data); outputs[name] = digest(data)
        m = {'status':'complete','exit_code':0,'source_sha256':s.CORE,'pipeline_driver_sha256':s.DRIVER,
             'scientific_case_id':cid,'config':config,'config_sha256':digest(canonical(config)),
             'branch_origin':branch,'execution_provenance':provenance,'git_commit':'1'*40,'output_sha256':outputs,
             'platform':'Linux-synthetic-fixture' if provenance['environment']=='GCP' else 'macOS-synthetic-fixture',
             'final_state_descriptor':{'state_semantic_sha256':self.parent_semantic}}
        (path/'manifest.json').write_bytes(canonical(m)); self.manifests[cid] = m; return path

    def registry(self,value=None):
        v = copy.deepcopy(self.v if value is None else value)
        pin = self.write('registry.json',v)
        auth = {'schema':'DAMS-root-original-case-acceptance-authority-1','status':'ROOT_ISSUED_ACCEPTANCE_ONLY',
                'registry_sha256':pin['sha256'],'accepted_case_ids':sorted(x['case_id'] for x in v['cases']),
                'issued_utc':v['issued_utc'],'expires_utc':v['expires_utc'],
                'provider_permission':False,'science_model_changed':False}
        ap = self.write('authority.json',auth)
        return r.Registry(self.out,pin,ap,self.inventory_pin,self.runtime_pin)

    def scheduler(self,registry):
        o = r.OperationalScheduler.__new__(r.OperationalScheduler)
        o.root=self.out; o.registry=registry; o.driver_hash=s.DRIVER
        o.limits=RuntimeLimits.from_dict(self.limits); o.page_options=None; o.known_latest_floor=None
        return o

    def origin(self): return {**self.branch,'parent_checkpoint':'synthetic-parent-checkpoint'}

    def test_fixed_registry_binds_original144_13_native_and_truth(self):
        reg=self.registry(); self.assertEqual(len(reg.rows),144)
        self.assertEqual(reg.bind_case(self.rows[s.CID],self.child),self.guest)

    def test_actual_original_prepare_and_handoff_validator_reused_without_parent_payload(self):
        with patch.object(s,'prepare',wraps=s.prepare) as prepare, \
             patch.object(s,'validate_root_handoff',wraps=s.validate_root_handoff) as handoff:
            reg=self.registry()
        self.assertEqual(prepare.call_count,1); self.assertGreaterEqual(handoff.call_count,2)
        self.assertEqual(reg.assignment_scopes[s.CID]['prepared']['origin']['parent_day'],366)
        self.assertEqual({p.name for p in (self.work/'selected-parent').iterdir()},
                         {'manifest.json','checkpoint-index.json'})

    def test_six_key_mini_assignment_is_rejected_with_coherently_rebound_receipts(self):
        a={k:self.assignment[k] for k in ('schema','case_id','config_sha256','source_sha256','driver_sha256','spec_sha256')}
        with self.assertRaisesRegex(ValueError,'complete nine-key'):
            self.registry(self.changed_scope(assignment=a))

    def test_assignment_immutable_input_override_and_extra_scope_are_rejected(self):
        for kind in ('wrong-input','extra-key'):
            with self.subTest(kind=kind):
                a=copy.deepcopy(self.assignment)
                if kind=='wrong-input':a['input_sha256']['original-statuses.json']='f'*64
                else:a['alternate_case']='f'*64
                with self.assertRaises(ValueError):self.registry(self.changed_scope(assignment=a))

    def test_parent_full13_roster_hash_and_boolean_size_are_rejected(self):
        for kind in ('missing','wrong-sha','boolean-size'):
            with self.subTest(kind=kind):
                a=copy.deepcopy(self.assignment)
                if kind=='missing':del a['parent_files']['final_state.sqlite']
                elif kind=='wrong-sha':a['parent_files']['final_state.sqlite']['sha256']='f'*64
                else:a['parent_files']['final_state.sqlite']['bytes']=True
                with self.assertRaises(ValueError):self.registry(self.changed_scope(assignment=a))

    def test_unsealed_handoff_and_wrong_original_handoff_conditions_are_rejected(self):
        a=copy.deepcopy(self.assignment);a['root_handoff_sha256']=None
        with self.assertRaisesRegex(ValueError,'handoff was not sealed'):
            self.registry(self.changed_scope(assignment=a))
        for changes in ({'selected_case_unstarted':False}, {'source_sha256':'f'*64},
                        {'selection_identity_sha256':'f'*64}, {'deadline_utc':'2030-01-01T00:00:00Z'},
                        {'parent_full_raw_gate_sha256':'not-a-sha'},
                        {'pipeline_lock':{**self.handoff['pipeline_lock'],'exclusive_held':False}}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):self.registry(self.changed_scope(handoff=changes))

    def test_historical_valid_lease_is_not_claimed_as_current_live_lock(self):
        observed=self.fixture_now-timedelta(hours=2)
        v=self.changed_scope(handoff={'observed_utc':observed.isoformat()},
            evaluation={'evaluated_utc':(observed+timedelta(seconds=30)).isoformat()},
            execution={'start_utc':(observed+timedelta(seconds=40)).isoformat()})
        reg=self.registry(v);scope=reg.assignment_scopes[s.CID]
        self.assertTrue(scope['historical_handoff_only_not_live_lock'])
        with self.assertRaisesRegex(ValueError,'lease expired'):
            s.validate_root_handoff(scope['handoff'],scope['prepared'])
        expired=copy.deepcopy(v);expired['expires_utc']=(self.fixture_now-timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):self.registry(expired)

    def test_evaluation_receipt_wrong_assignment_handoff_source_result_and_schema_are_rejected(self):
        for changes in ({'selected_assignment_sha256':'f'*64}, {'original_root_handoff_sha256':'f'*64},
                        {'source_sha256':'f'*64}, {'result':'UNISSUED'}, {'schema':'UNSUPPORTED'},
                        {'case_id':s.PARENT}, {'extra_authority':True}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):self.registry(self.changed_scope(evaluation=changes))

    def test_evaluation_before_observation_after_guest_BEGIN_or_outside_original_lease_is_rejected(self):
        observed=datetime.fromisoformat(self.handoff['observed_utc'])
        for evaluated in (observed-timedelta(seconds=1),
                          datetime.fromisoformat(self.execution['start_utc'])+timedelta(seconds=1)):
            with self.subTest(evaluated=evaluated):
                with self.assertRaises(ValueError):
                    self.registry(self.changed_scope(evaluation={'evaluated_utc':evaluated.isoformat()}))
        earlier=self.fixture_now-timedelta(minutes=5)
        with self.assertRaisesRegex(ValueError,'lease expired'):
            self.registry(self.changed_scope(
                handoff={'observed_utc':earlier.isoformat()},
                evaluation={'evaluated_utc':(earlier+timedelta(seconds=121)).isoformat()},
                execution={'start_utc':(earlier+timedelta(seconds=122)).isoformat()}))

    def test_gate_evaluation_pin_is_required_and_raw_gate_assignment_binding_is_exact(self):
        v=self.changed_scope(gate={'selected_assignment_sha256':'f'*64})
        with self.assertRaises(ValueError):self.registry(v)
        g=copy.deepcopy(self.gate);del g['original_handoff_evaluation_pin']
        v=copy.deepcopy(self.v);v['cases'][0]['raw_gate_pin']=self.write('missing-evaluation-gate.json',g)
        with self.assertRaisesRegex(ValueError,'evidence are missing'):self.registry(v)

    def test_original_metadata_and_evaluation_native_change_invalidates_registry(self):
        reg=self.registry();(self.work/'original-statuses.json').write_bytes(b'{}')
        with self.assertRaises(ValueError):reg.guard()

    def test_before_start_evaluation_reference_and_in_memory_scope_are_guarded(self):
        reg=self.registry();(self.work/'selected-before-provider-evaluation.json').write_bytes(b'{}')
        with self.assertRaises(ValueError):reg.guard()
        # The original Registry stays invalidated. Construct an independent new
        # fixture Registry for the separate in-memory mutation countercontrol.
        (self.work/'selected-before-provider-evaluation.json').write_bytes(canonical(self.evaluation))
        reg=self.registry();reg.assignment_scopes[s.CID]['historical_handoff_only_not_live_lock']=False
        with self.assertRaisesRegex(ValueError,'changed in memory'):reg.guard()

    def test_bootstrap_delay_keeps_H0_A_immutable_with_distinct_H1_before_Model(self):
        before=(self.work/'selected-original-case.json').read_bytes()
        reg=self.registry();scope=reg.assignment_scopes[s.CID]
        self.assertEqual((self.work/'selected-original-case.json').read_bytes(),before)
        self.assertNotEqual(scope['activation_handoff_sha256'],scope['root_handoff_sha256'])
        self.assertNotEqual(scope['activation_evaluation_sha256'],scope['before_provider_evaluation_sha256'])
        self.assertFalse(self.execution['science_complete'])
        with self.assertRaisesRegex(ValueError,'lease expired'):
            s.validate_root_handoff(scope['handoff'],scope['prepared'])

    def test_H1_cannot_change_H0_authority_scope_even_with_coherent_repinning(self):
        for changes in ({'lease_timeout_seconds':121}, {'parent_full_raw_gate_sha256':'f'*64},
                        {'pipeline_lock':{**self.handoff['pipeline_lock'],'owner_pid':4243}},
                        {'selected_case_unstarted':False}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):self.registry(self.changed_scope(activation=changes))

    def test_single_handoff_or_evaluation_SHA_cannot_fake_two_roles(self):
        with self.assertRaisesRegex(ValueError,'H0 reused'):
            self.registry(self.changed_scope(activation=self.handoff))
        with self.assertRaises(ValueError):
            self.registry(self.changed_scope(gate={
                'original_activation_evaluation_pin':self.gate['original_handoff_evaluation_pin']}))

    def test_activation_runtime_exact_four_keys_and_evaluation_pin_are_required(self):
        old=copy.deepcopy(self.guest_runtime['original_selected_activation']);del old['evaluation_sha256']
        with self.assertRaises(ValueError):
            self.registry(self.changed_scope(runtime={'original_selected_activation':old}))
        g=copy.deepcopy(self.gate);del g['original_activation_evaluation_pin']
        v=copy.deepcopy(self.v);v['cases'][0]['raw_gate_pin']=self.write('missing-activation-gate.json',g)
        with self.assertRaisesRegex(ValueError,'evidence are missing'):self.registry(v)

    def test_H1_evaluation_after_BEGIN_or_expired_at_activation_is_rejected(self):
        start=datetime.fromisoformat(self.execution['start_utc'])
        with self.assertRaises(ValueError):
            self.registry(self.changed_scope(activation_evaluation={
                'evaluated_utc':(start+timedelta(seconds=1)).isoformat()}))
        with self.assertRaisesRegex(ValueError,'lease expired'):
            self.registry(self.changed_scope(activation={
                'observed_utc':(start-timedelta(seconds=180)).isoformat()}))

    def test_fresh_E1_cannot_hide_580_seconds_old_H1_at_actual_BEGIN(self):
        start=datetime.fromisoformat(self.execution['start_utc'])
        observed=start-timedelta(seconds=580)
        # E1 was genuinely inside H1's original120s lease; every SHA is
        # coherently rebound. It expired before true BEGIN despite E1<=BEGIN.
        value=self.changed_scope(activation={'observed_utc':observed.isoformat()},
            activation_evaluation={'evaluated_utc':(observed+timedelta(seconds=30)).isoformat()})
        with self.assertRaisesRegex(ValueError,'lease expired'):
            self.registry(value)

    def test_H1_fresh_at_true_BEGIN_passes_both_historical_and_BEGIN_validation(self):
        start=datetime.fromisoformat(self.execution['start_utc'])
        observed=start-timedelta(seconds=60)
        value=self.changed_scope(activation={'observed_utc':observed.isoformat()},
            activation_evaluation={'evaluated_utc':(observed+timedelta(seconds=30)).isoformat()})
        with patch.object(s,'validate_activation_handoff',wraps=s.validate_activation_handoff) as validator:
            reg=self.registry(value)
        self.assertTrue(reg.assignment_scopes[s.CID]['historical_handoff_only_not_live_lock'])
        self.assertEqual(validator.call_count,2)
        self.assertEqual(validator.call_args_list[-1].kwargs['now'],start.timestamp())

    def test_terminal_exit_group_absence_and_unchanged_inputs_are_mandatory(self):
        for changes in ({'exit_code':False},{'exit_code':1},
                        {'owned_pipeline_group_verification':{'owned_pipeline_group_absent':False}},
                        {'inputs_unchanged':False}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):self.registry(self.changed_scope(execution=changes))

    def test_current_custody_is_after_exit_fresh_and_bound_to_execution_H1(self):
        for changes in ({'guest_execution_sha256':'f'*64},{'original_activation_handoff_sha256':'f'*64},
                        {'observed_utc':(self.fixture_now-timedelta(seconds=11)).isoformat()}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):self.registry(self.changed_scope(custody=changes))
        with self.assertRaisesRegex(ValueError,'custody is stale'):
            self.registry(self.changed_scope(
                execution={'start_utc':(self.fixture_now-timedelta(seconds=400)).isoformat(),
                           'end_utc':(self.fixture_now-timedelta(seconds=302)).isoformat()},
                custody={'observed_utc':(self.fixture_now-timedelta(seconds=301)).isoformat()}))

    def test_actual_execution_binds_all_three_H0_H1_evaluation_pins(self):
        for key in ('original_activation_handoff_sha256','original_activation_evaluation_sha256',
                    'original_before_provider_evaluation_sha256'):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):self.registry(self.changed_scope(execution={key:'f'*64}))

    def test_H1_and_its_evaluation_native_change_invalidate_return_registry(self):
        reg=self.registry();(self.work/'selected-activation-evaluation.json').write_bytes(b'{}')
        with self.assertRaises(ValueError):reg.guard()

    def test_native_old_inspect_byte_verifier_runs_guest_and_limits_restored(self):
        reg=self.registry(); op=self.scheduler(reg); original_limits=op.limits
        import dams_sim.scheduler as scheduler
        with patch.object(scheduler,'verify_outputs',wraps=scheduler.verify_outputs) as verifier:
            value,_=op._inspect(Config.from_dict(self.rows[s.CID]['config']),self.origin())
            self.assertEqual(verifier.call_count,1)
        self.assertEqual(value['summary']['days_completed'],3743)
        self.assertIs(op.limits,original_limits)
        self.assertEqual(op.limits.provenance,self.limits['provenance'])

    def test_old_inspect_raw_error_and_finally_limits_identity_restored(self):
        reg=self.registry(); op=self.scheduler(reg); original_limits=op.limits
        with patch('dams_sim.scheduler.verify_outputs',side_effect=ValueError('FULL_BYTE_GATE_REQUIRED')):
            with self.assertRaisesRegex(ValueError,'FULL_BYTE_GATE_REQUIRED'):
                op._inspect(Config.from_dict(self.rows[s.CID]['config']),self.origin())
        self.assertIs(op.limits,original_limits)

    def test_registered_expectation_does_not_bypass_original_full_semantic_reader(self):
        reg=self.registry(); CaseReader,_=r.reader_classes(reg)
        reader=CaseReader(self.out,{'source_sha256':s.CORE,'pipeline_driver_sha256':s.DRIVER,'spec_sha256':s.SPEC})
        # These opaque tiny bytes hash correctly, yet fail the ORIGINAL complete
        # state/checkpoint gate before any SQLite connection or Model execution.
        with self.assertRaisesRegex(ValueError,'checkpoint index'):
            reader.validate_case(self.rows[s.CID],self.child)

    def test_final_reader_calls_true_original_case_with_guest_expectation(self):
        reg=self.registry(); _,StudyReader=r.reader_classes(reg)
        reader=StudyReader.__new__(StudyReader); reader.root=self.out
        with patch.object(r.original_validator.CheckedLongitudinalStudy,'_case',side_effect=ValueError('ORIGINAL_FULL_RAW_GATE')) as gate:
            with self.assertRaisesRegex(ValueError,'ORIGINAL_FULL_RAW_GATE'):
                reader._case(self.rows[s.CID],{'attempt':str(self.child.relative_to(self.out))},self.limits['provenance'])
            self.assertEqual(gate.call_args.args[2],self.guest)

    def test_local_default_actual_inspect_unchanged(self):
        reg=self.registry(); op=self.scheduler(reg); original=op.limits
        result,_=op._inspect(Config.from_dict(self.rows[s.PARENT]['config']))
        self.assertEqual(result['summary']['days_completed'],366); self.assertIs(op.limits,original)

    def test_no_registry_direct_original_entry_and_census(self):
        with patch.object(r.original_pipeline,'run_longitudinal_pipeline',return_value={'synthetic':True}) as run:
            self.assertEqual(r.invoke_pipeline('original-spec',12,self.out,'runtime'),{'synthetic':True})
            run.assert_called_once_with('original-spec',12,self.out,'runtime')
        with patch.object(r.original_pipeline,'stopped_case_census',return_value={'synthetic':True}) as census:
            r.census(self.out,[],{},expected_provenance={'old':'unchanged'})
            census.assert_called_once_with(self.out,[],{},expected_provenance={'old':'unchanged'})

    def test_wrong_core_config_CID_environment_branch_and_runtime_refused(self):
        changes=[('source_sha256','f'*64),('driver_sha256','f'*64),('original_runtime_sha256','f'*64)]
        for key,value in changes:
            v=copy.deepcopy(self.v);v[key]=value
            with self.assertRaises(ValueError):self.registry(v)
        for key,value in [('case_id','f'*64),('config_sha256','f'*64),('expected_provenance',self.limits['provenance']),
                          ('branch_identity',{**self.branch,'parent_day':365})]:
            v=copy.deepcopy(self.v);v['cases'][0][key]=value
            with self.assertRaises(ValueError):self.registry(v)

    def test_missing_or_broader_Root_authority_refused(self):
        rp=self.write('registry.json',self.v)
        for auth in [{'schema':'UNISSUED','status':'PREPARE_ONLY'},
                     {'schema':'DAMS-root-original-case-acceptance-authority-1','status':'ROOT_ISSUED_ACCEPTANCE_ONLY',
                      'registry_sha256':rp['sha256'],'accepted_case_ids':[s.CID],
                      'issued_utc':self.v['issued_utc'],'expires_utc':self.v['expires_utc'],
                      'provider_permission':True,'science_model_changed':False}]:
            with self.assertRaises(ValueError):
                r.Registry(self.out,rp,self.write('authority.json',auth),self.inventory_pin,self.runtime_pin)

    def test_wrong_raw_receipt_or_active_custody_refused(self):
        for name,key,value in [('raw-gate.json','status','UNVERIFIED'),('raw-gate.json','source_sha256','f'*64),
                               ('custody.json','active_writers',[123]),('custody.json','producer_exit_code',False)]:
            record=copy.deepcopy(self.record); pin_key='raw_gate_pin' if name=='raw-gate.json' else 'custody_pin'
            document=s.parse(Path(record[pin_key]['path']).read_bytes());document[key]=value
            record[pin_key]=self.write('changed-'+name,document)
            v=copy.deepcopy(self.v);v['cases']=[record]
            with self.assertRaises(ValueError):self.registry(v)

    def test_expired_or_extended_registry_refused(self):
        for expires in [(self.fixture_now-timedelta(seconds=1)).isoformat(),'2030-01-01T00:00:00Z']:
            v=copy.deepcopy(self.v);v['expires_utc']=expires
            with self.assertRaises(ValueError):self.registry(v)

    def test_current_proof_native_change_invalidates(self):
        reg=self.registry();(self.meta/'authority.json').write_bytes(b'{}')
        with self.assertRaises(ValueError):reg.guard()

    def test_own_source_fence_change_invalidates_without_science_edit(self):
        reg=self.registry();owned=self.meta/'synthetic-source';owned.write_bytes(b'a')
        reg.source_native[owned]=s.native(owned.lstat());owned.write_bytes(b'b')
        with self.assertRaisesRegex(ValueError,'source changed'):reg.guard()

    def test_current_raw_native_change_or_wrong_attempt_refused(self):
        reg=self.registry();(self.child/'timeseries.csv').write_bytes(b'mutated')
        with self.assertRaises(ValueError):reg.bind_case(self.rows[s.CID],self.child)
        other=self.out/'cases'/s.CID/'attempt-001';other.mkdir()
        with self.assertRaises(ValueError):reg.bind_case(self.rows[s.CID],other)

    def test_unknown_unassigned_and_duplicate_registry_refused(self):
        reg=self.registry()
        with self.assertRaises(ValueError):reg.expected('f'*64)
        v=copy.deepcopy(self.v);v['cases'].append(v['cases'][0])
        with self.assertRaises(ValueError):self.registry(v)
        row=copy.deepcopy(self.rows[s.CID]);row['config']['seed']=7
        with self.assertRaises(ValueError):reg.assigned(row)

    def test_explicit_namespace_four_metadata_adaptations_no_original_patch(self):
        reg=self.registry();old_scheduler=r.original_pipeline.Scheduler
        old_census=r.original_pipeline.stopped_case_census
        ns=r.build_namespace(reg)
        self.assertEqual(len(ns['_operational_metadata_adaptations']),4)
        provenance=ns['_explicit_operational_source_provenance']
        self.assertIs(provenance['four_metadata_adaptations_reverse_AST_exact'],True)
        self.assertEqual(provenance['actual_operator_source_sha256'],r.IMPORT_SHA)
        self.assertEqual(provenance['scientific_driver_reference_sha256'],s.DRIVER)
        self.assertNotEqual(provenance['actual_operator_source_sha256'],s.DRIVER)
        self.assertIs(r.original_pipeline.Scheduler,old_scheduler)
        self.assertIs(r.original_pipeline.stopped_case_census,old_census)
        self.assertNotIn('dams_sim._explicit_operational_return_r5',sys.modules)

    def test_mixed_localGuest144_census_disjoint_identity_not_new_worlds(self):
        reg=self.registry()
        def fake_semantic_gate(reader,row,attempt,**kwargs):
            cid=row['case_id'];m=self.manifests[cid]
            self.assertEqual(kwargs['expected_provenance'],self.guest if cid==s.CID else self.limits['provenance'])
            return {'config':Config.from_dict(row['config']),'attempt':Path(attempt),'manifest':m,
                    'branch_origin':m['branch_origin'],
                    'state_semantic_sha256':self.parent_semantic if cid==s.PARENT else 'f'*64}
        with patch.object(r.original_validator.CheckedLongitudinalCases,'validate_case',new=fake_semantic_gate),\
             patch('dams_sim.longitudinal_outputs.iter_timeseries',side_effect=lambda _:iter([{'synthetic-prefix':1}])):
            value=r.census(self.out,self.inventory,{'schema_version':3,'source_sha256':s.CORE,
                           'pipeline_driver_sha256':s.DRIVER,'spec_sha256':s.SPEC},
                           expected_provenance=self.limits['provenance'],registry=reg)
        self.assertEqual(value['expected_cases'],144)
        self.assertEqual(set(value['groups']['complete']),{s.CID,s.PARENT})
        self.assertEqual(len(value['groups']['unstarted']),142)
        self.assertIs(value['full_study_gate'],False)
        self.assertEqual(len({x['case_id'] for x in value['records']}),144)

    def test_census_duplicate_rows_refused(self):
        reg=self.registry()
        with self.assertRaises(ValueError):r.census(self.out,[self.rows[s.CID]]*2,{},registry=reg)

    def test_raw_symlink_manifest_refuses(self):
        reg=self.registry();p=self.child/'timeseries.csv';p.unlink();p.symlink_to(self.meta/'package.json')
        with self.assertRaises(ValueError):reg.bind_case(self.rows[s.CID],self.child)

    def test_guest_runtime_override_and_core_extra_module_refused(self):
        runtime=s.parse(Path(self.record['guest_runtime_pin']['path']).read_bytes())
        runtime['runtime_limits']['cpu_budget']=1
        v=copy.deepcopy(self.v);v['cases'][0]['guest_runtime_pin']=self.write('changed-runtime.json',runtime)
        with self.assertRaises(ValueError):self.registry(v)
        package=s.parse(Path(self.record['source_package_pin']['path']).read_bytes())
        package['source_files_sha256']['dams_sim/unassigned_science.py']='f'*64
        newpin=self.write('extra-package.json',package)
        execution=s.parse(Path(self.record['guest_execution_pin']['path']).read_bytes())
        execution['source_manifest_sha256']=newpin['sha256']
        ep=self.write('extra-execution.json',execution)
        gate=s.parse(Path(self.record['raw_gate_pin']['path']).read_bytes())
        gate['source_package_sha256']=newpin['sha256'];gate['guest_execution_sha256']=ep['sha256']
        v=copy.deepcopy(self.v);v['cases'][0].update(source_package_pin=newpin,guest_execution_pin=ep,
                                                raw_gate_pin=self.write('extra-gate.json',gate))
        with self.assertRaises(ValueError):self.registry(v)

    def test_true_guest_provenance_cannot_hide_false_mac_platform(self):
        reg=self.registry();record=reg.cases[s.CID]
        m=copy.deepcopy(self.manifests[s.CID]);m['platform']='macOS-false-cloud-fixture'
        (self.child/'manifest.json').write_bytes(canonical(m))
        record['manifest_pin']=self.pin(self.child/'manifest.json')
        record['raw_native']['manifest.json']=s.native((self.child/'manifest.json').lstat())
        reg.proofs[s.CID][0]['raw_files_sha256']['manifest.json']=record['manifest_pin']['sha256']
        with self.assertRaises(ValueError):reg.bind_case(self.rows[s.CID],self.child)


if __name__=='__main__': unittest.main()
