"""Provider-free recovery controls with a real interrupted Model and Scheduler.

Tiny selectors are explicit engineering fixtures. Scientific Model, checkpoint,
Scheduler and whole original raw gate remain actual unchanged implementations.
No paid resource or formal sample is created by this suite.
"""
import copy
import dataclasses
import shutil
import uuid
import csv
import io
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research_tools')]
sys.dont_write_bytecode=True
from research_tools import small_enterprise_cloud as h
from dams_sim.config import Config
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.runtime import RuntimeLimits
from dams_sim.cli import run_world
from dams_sim.model import Model
from dams_sim.spec import case_key
from dams_sim.storage import canonical,digest,file_digest
from dams_sim.longitudinal_pipeline import driver_hash


class ResumeControls(unittest.TestCase):
    def setUp(self):
        parent=Path(os.environ.get('DAMS_RESUME_TEST_ROOT',tempfile.gettempdir())).resolve()
        self.temp=tempfile.TemporaryDirectory(prefix='resume-engineering-',dir=parent)
        self.root=Path(self.temp.name)
        now=datetime.now(timezone.utc)
        self.runtime=RuntimeLimits(workers_auto=False,max_workers=1,cpu_budget=1,
            memory_budget_bytes=2**30,per_world_rss_bytes=512*2**20,max_events=2000000,
            max_output_bytes=8*2**20,batch_max_output_bytes=64*2**20,
            batch_max_output_files=32798,per_world_output_files=8192,min_free_disk_bytes=0,
            world_timeout_seconds=30,deadline_utc=(now+timedelta(minutes=5)).isoformat(),
            stop_cutoff_utc=(now+timedelta(minutes=6)).isoformat(),checkpoint_interval_days=2,
            checkpoint_interval_seconds=600,journal_chunk_bytes=2**20,max_retries=1,
            provenance={'environment':'ENGINEERING_ONLY_NO_FORMAL_SAMPLE'})
        self.configs=[Config(n=4,days=5,guilds=2,team_size=2,sites=1,seed=20261013,world=912301,
            regime=regime,trace_every_days=1,max_wall_seconds=30,max_output_mb=8*2**20/1_000_000,
            max_rss_mb=512,longitudinal=LongitudinalConfig(calendar_start='2020-01-01',
            max_active_members=8,max_people_ever=16,annual_exit_probability=0,
            adoption_day=0,transition_days=1)) for regime in ('hierarchy','sublinear')]
        self.rows=[{'case_id':case_key(c),'config':c.to_dict(),'tags':{'parent_case_id':None,
            'arm_id':arm,'world':912301}} for arm,c in zip(h.SELECTED_ARMS,self.configs)]
        self.declaration={'engineering_fixture':True,'initial_n':4,'days':5,'formal_samples':0}
        self.arms=[{'schema':h.SCHEMA,'engineering_fixture':True,
            'study_sha256':digest(canonical(self.declaration)),
            'pilot_inventory_sha256':digest(canonical(self.rows)),'selected_world':912301,
            'selected_arm':arm,'selected_case_id':row['case_id']} for arm,row in zip(h.SELECTED_ARMS,self.rows)]
        self.profile={'schema':h.PAIR_SCHEMA,'engineering_fixture':True,
            'study_sha256':digest(canonical(self.declaration)),
            'pilot_inventory_sha256':digest(canonical(self.rows)),
            'selected_world':912301,'arm_profiles':self.arms}
        def selector(p,rt):
            self.assertIs(p['engineering_fixture'],True)
            return p
        self.stack=[]
        for name,value in [('validate_profile',selector),('validate_pair_profile',selector),
                            ('plan',lambda rt,world:(self.runtime,self.declaration,self.rows))]:
            handle=patch.object(h,name,value);handle.start();self.stack.append(handle)
    def tearDown(self):
        for handle in reversed(self.stack):handle.stop()
        if os.environ.get('DAMS_KEEP_RESUME_ENGINEERING')=='1':
            self.temp._finalizer.detach()
            print('RETAINED_ENGINEERING_EVIDENCE',self.id(),str(self.root),flush=True)
        else:self.temp.cleanup()
    def partial(self):
        output=self.root/'recovery'
        with patch.object(h,'Scheduler',side_effect=ValueError('intent-only engineering refusal')):
            with self.assertRaisesRegex(ValueError,'intent-only'):h.execute(self.profile,self.runtime,output)
        # Complete the first arm with the actual spawned original worker/raw gate.
        h._execute_owned(self.arms[0],self.runtime,output/h.SELECTED_ARMS[0])
        child=output/h.SELECTED_ARMS[1]
        with patch.object(h,'Scheduler',side_effect=ValueError('partial intent refusal')):
            with self.assertRaisesRegex(ValueError,'partial intent'):h._execute_owned(self.arms[1],self.runtime,child)
        attempt=child/'cases'/self.rows[1]['case_id']/'attempt-000';attempt.mkdir(parents=True)
        checks=iter([False,False,True])
        model=Model(self.configs[1],storage_dir=attempt,journal_chunk_bytes=self.runtime.journal_chunk_bytes)
        try:
          with self.assertRaises(InterruptedError):
            run_world(self.configs[1],attempt,restored=model,checkpoint_interval_days=2,
                journal_chunk_bytes=self.runtime.journal_chunk_bytes,
                stop_requested=lambda:next(checks),metadata_extra={
                    'pipeline_driver_sha256':driver_hash(),'scientific_case_id':self.rows[1]['case_id'],
                    'execution_provenance':self.runtime.provenance})
        finally:model.ledger.close()
        return output
    def grant(self,output):
        envelope=h.resume_envelope(self.profile,self.runtime,output)
        value={'schema':h.RESUME_GRANT_SCHEMA,'envelope_sha256':digest(canonical(envelope)),
            'issued_by':'Root','original_profile_sha256':envelope['profile_sha256'],
            'original_runtime_limits_sha256':envelope['runtime_limits_sha256'],
            'original_arm_states_sha256':digest(canonical(envelope['arm_states'])),
            'original_deadline':self.runtime.deadline,'owned_producers_absent':True,
            'shared_budget_admitted':True,'parent_authorization_sha256':None,
            'expires_epoch':self.runtime.deadline}
        common={'issued_by':'Root','original_profile_sha256':envelope['profile_sha256'],
            'original_runtime_limits_sha256':envelope['runtime_limits_sha256'],
            'original_arm_states_sha256':digest(canonical(envelope['arm_states'])),
            'source_sha256':h.CORE,'pipeline_driver_sha256':h.DRIVER,
            'case_ids':[v['case_id'] for v in envelope['arm_states']],
            'original_deadline':self.runtime.deadline,
            'envelope_sha256':value['envelope_sha256'],'native_tree_sha256':digest(canonical(envelope['tree']))}
        authority=self.root/('authority-'+uuid.uuid4().hex);authority.mkdir()
        original=authority/'original-authority-source.json'
        original.write_bytes(canonical({'engineering_fixture':True,'paid_actions':0,
            'original_source_sha256':h.CORE,'original_runtime_limits_sha256':envelope['runtime_limits_sha256'],
            'original_deadline':self.runtime.deadline})+b'\n')
        parent=authority/'parent-authorization.json'
        parent_binding={k:common[k] for k in ('issued_by','original_profile_sha256',
            'original_runtime_limits_sha256','source_sha256','pipeline_driver_sha256','case_ids','original_deadline')}
        parent.write_bytes(canonical({**parent_binding,'schema':'DAMS-small-enterprise-parent-authorization-1',
            'source_refs':[{'path':str(original),'sha256':file_digest(original)}]})+b'\n')
        value['parent_authorization_sha256']=file_digest(parent)
        common['parent_authorization_sha256']=file_digest(parent)
        parent_descriptor={'path':str(parent),'sha256':file_digest(parent)}
        evidence=authority/'closed-evidence.json'
        evidence.write_bytes(canonical({**common,'schema':'DAMS-small-enterprise-closed-dispatch-evidence-1',
            'owned_producers_absent':True,'parent_authorization':parent_descriptor})+b'\n')
        ledger=authority/'original-ledger.json'
        ledger.write_bytes(canonical({**common,'schema':'DAMS-small-enterprise-ledger-admission-evidence-1',
            'shared_budget_admitted':True,'parent_authorization':parent_descriptor})+b'\n')
        for key,schema,flag,original in (
            ('closed_dispatch_proof','DAMS-small-enterprise-closed-dispatch-proof-1','owned_producers_absent',evidence),
            ('ledger_admission','DAMS-small-enterprise-ledger-admission-proof-1','shared_budget_admitted',ledger)):
            proof=authority/(key+'.json')
            proof.write_bytes(canonical({**common,'schema':schema,flag:True,
                'evidence':[{'path':str(original),'sha256':file_digest(original)}]})+b'\n')
            value[key]={'path':str(proof),'sha256':file_digest(proof)}
        grant=authority/'grant.json';grant.write_bytes(canonical(value)+b'\n')
        return envelope,grant,file_digest(grant)
    def recover(self,output,envelope,grant,pin,callback=lambda _:True):
        return h.resume(self.profile,self.runtime,output,envelope,grant_path=grant,
                        grant_sha256=pin,root_lifetime_check=callback)
    def test_real_interrupted_day2_resume_matches_fresh_and_reuses_completed_arm(self):
        output=self.partial();first=output/h.SELECTED_ARMS[0]
        original={str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()}
        envelope,grant,pin=self.grant(output)
        self.recover(output,envelope,grant,pin)
        result=json.loads((output/'pair-result.json').read_bytes());self.assertTrue(result['paired_world_complete'])
        for p in first.rglob('*'):
            if p.is_file() and '/cases/' in str(p):
                self.assertEqual(p.read_bytes(),original[str(p.relative_to(output))])
        self.assertEqual((first/'single-arm-result.json').read_bytes(),original[str((first/'single-arm-result.json').relative_to(output))])
        case=output/h.SELECTED_ARMS[1]/'cases'/self.rows[1]['case_id']
        self.assertTrue((case/'attempt-001').is_dir());self.assertFalse((case/'attempt-002').exists())
        for p in (case/'attempt-000').rglob('*'):
            if p.is_file():self.assertEqual(p.read_bytes(),original[str(p.relative_to(output))])
        manifest=json.loads((case/'attempt-001/manifest.json').read_bytes())
        self.assertEqual(manifest['restart_origin']['parent_attempt'],'attempt-000')
        fresh=self.root/'baseline';h.execute(self.profile,self.runtime,fresh)
        for arm,row,config in zip(h.SELECTED_ARMS,self.rows,self.configs):
            a=output/arm/'cases'/row['case_id'];b=fresh/arm/'cases'/row['case_id']
            ra=sorted(p for p in a.glob('attempt-*') if p.is_dir())[-1];rb=b/'attempt-000'
            self.assertEqual((ra/'summary.json').read_bytes(),(rb/'summary.json').read_bytes())
            self.assertEqual(list(csv.DictReader(io.StringIO((ra/'timeseries.csv').read_text()))),
                             list(csv.DictReader(io.StringIO((rb/'timeseries.csv').read_text()))))
            ea=json.loads((ra/'final_state.json').read_bytes());eb=json.loads((rb/'final_state.json').read_bytes())
            self.assertEqual(ea['state_semantic_sha256'],eb['state_semantic_sha256'])
        self.assertEqual(self.runtime.world_timeout_seconds,30)
    def test_changed_checkpoint_bytes_rejected_before_resume_writes(self):
        output=self.partial();envelope,grant,pin=self.grant(output)
        index=next((output/h.SELECTED_ARMS[1]/'cases').rglob('checkpoint-index.json'))
        index.write_bytes(index.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'input identity'):self.recover(output,envelope,grant,pin)
        self.assertFalse(list(output.glob('resume-evidence-*')))
    def test_missing_or_live_owner_callback_refuses_without_writes(self):
        output=self.partial();envelope,grant,pin=self.grant(output)
        with self.assertRaisesRegex(ValueError,'lifetime'):self.recover(output,envelope,grant,pin,callback=lambda _:False)
        self.assertFalse(list(output.glob('resume-evidence-*')))
    def test_wrong_typed_grant_and_renewed_runtime_refused(self):
        output=self.partial();envelope,grant,pin=self.grant(output)
        value=json.loads(grant.read_bytes());value['owned_producers_absent']=False
        grant.write_bytes(canonical(value)+b'\n')
        with self.assertRaisesRegex(ValueError,'typed Root'):self.recover(output,envelope,grant,file_digest(grant))
        modified=copy.deepcopy(self.runtime.to_dict());modified['max_retries']=2
        with self.assertRaisesRegex(ValueError,'input identity'):
            h.resume(self.profile,modified,output,envelope,grant_path=grant,
                grant_sha256=file_digest(grant),root_lifetime_check=lambda _:True)
    def test_fresh_mode_still_refuses_existing_pair(self):
        output=self.partial()
        with self.assertRaisesRegex(ValueError,'already has state'):h.execute(self.profile,self.runtime,output)
    def test_corrupt_complete_arm_cannot_be_reused(self):
        output=self.partial();first=output/h.SELECTED_ARMS[0]
        summary=next(first.rglob('summary.json'));summary.write_bytes(b'{}\n')
        envelope,grant,pin=self.grant(output)
        with self.assertRaises(ValueError):self.recover(output,envelope,grant,pin)
        case=output/h.SELECTED_ARMS[1]/'cases'/self.rows[1]['case_id']
        self.assertFalse((case/'attempt-001').exists())
    def test_grant_replacement_identity_and_expiry_refused(self):
        output=self.partial();envelope,grant,pin=self.grant(output)
        raw=grant.read_bytes();replacement=self.root/'replace.json';replacement.write_bytes(raw)
        checks=0
        def swap(_):
            nonlocal checks
            checks+=1
            if checks==1:os.replace(replacement,grant)
            return True
        with self.assertRaisesRegex(ValueError,'grant changed'):
            self.recover(output,envelope,grant,pin,callback=swap)
        self.assertFalse((output/'pair-result.json').exists())
        envelope,grant,pin=self.grant(output)
        value=json.loads(grant.read_bytes());value['expires_epoch']=0
        grant.write_bytes(canonical(value)+b'\n')
        with self.assertRaisesRegex(ValueError,'expired'):
            self.recover(output,envelope,grant,file_digest(grant))
    def test_retained_checkpoint_mutation_during_callback_refused(self):
        output=self.partial();envelope,grant,pin=self.grant(output)
        index=next((output/h.SELECTED_ARMS[1]/'cases').rglob('checkpoint-index.json'))
        checks=0
        def mutation(_):
            nonlocal checks
            checks+=1
            if checks==1:index.write_bytes(index.read_bytes()+b' ')
            return True
        with self.assertRaisesRegex(ValueError,'evidence changed during resume'):
            self.recover(output,envelope,grant,pin,callback=mutation)
        self.assertFalse(list(output.glob('resume-evidence-*')))
    def test_missing_checkpoint_and_exhausted_attempt_budget_refused(self):
        output=self.partial();case=output/h.SELECTED_ARMS[1]/'cases'/self.rows[1]['case_id']
        index=case/'attempt-000/checkpoint-index.json';raw=index.read_bytes();index.unlink()
        envelope,grant,pin=self.grant(output)
        with self.assertRaisesRegex(ValueError,'lacks original checkpoint'):
            self.recover(output,envelope,grant,pin)
        self.assertFalse((case/'attempt-001').exists())
        index.write_bytes(raw)
        # Real original scheduler refuses count > max_retries before new attempt.
        attempt=case/'attempt-001';attempt.mkdir()
        for item in (case/'attempt-000').iterdir():
            if item.is_file():(attempt/item.name).write_bytes(item.read_bytes())
        envelope,grant,pin=self.grant(output)
        with self.assertRaisesRegex(RuntimeError,'finite attempt budget'):
            self.recover(output,envelope,grant,pin)
        self.assertFalse((case/'attempt-002').exists())
    def test_internal_helpers_refuse_arbitrary_resume_callback(self):
        unused=self.root/'must-not-create'
        with self.assertRaisesRegex(ValueError,'sealed resume admission'):
            h._execute_owned(self.arms[0],self.runtime,unused,resume_gate=lambda:True)
        with self.assertRaisesRegex(ValueError,'sealed resume admission'):
            h._execute_pair_owned(self.profile,self.runtime,unused,resume_gate=lambda:True)
        self.assertFalse(unused.exists())
    def test_failed_manifest_full_elapsed_not_checkpoint_prefix_is_enforced(self):
        output=self.partial();case=output/h.SELECTED_ARMS[1]/'cases'/self.rows[1]['case_id']
        marker=case/'attempt-000/manifest.json';value=json.loads(marker.read_bytes())
        value['total_wall_seconds']=31;marker.write_bytes(canonical(value)+b'\n')
        envelope,grant,pin=self.grant(output)
        with self.assertRaisesRegex(ValueError,'cumulative'):self.recover(output,envelope,grant,pin)
        self.assertFalse((case/'attempt-001').exists());self.assertFalse((output/'pair-result.json').exists())
    def test_final_scheduler_grant_revocation_cannot_accept_pair(self):
        output=self.partial();envelope,grant,pin=self.grant(output)
        live={'allowed':True};original=h.Scheduler
        class RevokingScheduler(original):
            def run(inner,*args,**kwargs):
                result=super().run(*args,**kwargs);live['allowed']=False
                grant.write_bytes(grant.read_bytes()+b' ');return result
        with patch.object(h,'Scheduler',RevokingScheduler),self.assertRaises(ValueError):
            self.recover(output,envelope,grant,pin,callback=lambda _:live['allowed'])
        self.assertFalse((output/'pair-result.json').exists())
    def test_lost_complete_marker_or_case_tree_never_regenerates(self):
        output=self.partial();first=output/h.SELECTED_ARMS[0]
        case=first/'cases'/self.rows[0]['case_id']
        (case/'attempt-000/manifest.json').unlink();(first/'single-arm-result.json').unlink()
        with self.assertRaisesRegex(ValueError,'complete evidence'):self.grant(output)
        shutil.rmtree(case)
        with self.assertRaisesRegex(ValueError,'case tree'):self.grant(output)
        self.assertFalse((case/'attempt-000').exists())
    def test_lost_pair_receipt_cannot_recount_complete_world(self):
        output=self.partial();envelope,grant,pin=self.grant(output);self.recover(output,envelope,grant,pin)
        (output/'pair-result.json').unlink();envelope,grant,pin=self.grant(output)
        with self.assertRaisesRegex(ValueError,'both arm receipts already accepted'):
            self.recover(output,envelope,grant,pin)
        self.assertFalse((output/'pair-result.json').exists())
    def test_recovery_administration_global_file_cap_is_prospectively_reserved(self):
        self.runtime=dataclasses.replace(self.runtime,batch_max_output_files=120,per_world_output_files=40)
        output=self.partial();count=sum(p.is_file() for p in output.rglob('*'))
        for i in range(119-count):(output/('filler-%03d.dat'%i)).write_bytes(b'x')
        envelope,grant,pin=self.grant(output)
        with self.assertRaisesRegex(ValueError,'whole-tree file reservation'):
            self.recover(output,envelope,grant,pin)
        self.assertEqual(sum(p.is_file() for p in output.rglob('*')),119)
        self.assertFalse(list(output.glob('resume-evidence-*')))
    def test_reference_bytes_and_proof_semantics_are_real_and_pinned(self):
        output=self.partial();envelope,grant,pin=self.grant(output)
        value=json.loads(grant.read_bytes());proof=Path(value['ledger_admission']['path'])
        typed=json.loads(proof.read_bytes());evidence=Path(typed['evidence'][0]['path'])
        evidence.write_bytes(evidence.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'referenced evidence bytes'):
            self.recover(output,envelope,grant,pin)
        envelope,grant,pin=self.grant(output);value=json.loads(grant.read_bytes())
        proof=Path(value['closed_dispatch_proof']['path']);typed=json.loads(proof.read_bytes())
        typed['case_ids']=['0'*64];proof.write_bytes(canonical(typed)+b'\n')
        value['closed_dispatch_proof']['sha256']=file_digest(proof);grant.write_bytes(canonical(value)+b'\n')
        with self.assertRaisesRegex(ValueError,'semantic binding'):
            self.recover(output,envelope,grant,file_digest(grant))
    def test_unclosed_checkpoint_time_prefix_is_insufficient(self):
        output=self.partial();case=output/h.SELECTED_ARMS[1]/'cases'/self.rows[1]['case_id']
        marker=case/'attempt-000/manifest.json';value=json.loads(marker.read_bytes())
        value['status']='running';value.pop('total_wall_seconds',None);marker.write_bytes(canonical(value)+b'\n')
        envelope,grant,pin=self.grant(output)
        with self.assertRaisesRegex(ValueError,'closed attempt elapsed evidence missing'):
            self.recover(output,envelope,grant,pin)
        self.assertFalse((case/'attempt-001').exists())
    def test_live_monitor_cooperatively_stops_original_worker_on_revocation(self):
        self.configs=[dataclasses.replace(c,days=300) for c in self.configs]
        self.rows=[{**r,'case_id':case_key(c),'config':c.to_dict()} for r,c in zip(self.rows,self.configs)]
        self.declaration={**self.declaration,'days':300}
        for arm,row in zip(self.arms,self.rows):
            arm.update(selected_case_id=row['case_id'],study_sha256=digest(canonical(self.declaration)),
                       pilot_inventory_sha256=digest(canonical(self.rows)))
        self.profile.update(study_sha256=digest(canonical(self.declaration)),pilot_inventory_sha256=digest(canonical(self.rows)))
        output=self.partial();envelope,grant,pin=self.grant(output)
        case=output/h.SELECTED_ARMS[1]/'cases'/self.rows[1]['case_id']
        def lifetime(_):return not (case/'attempt-001/checkpoint-index.json').exists()
        with self.assertRaisesRegex((ValueError,InterruptedError),'lifetime'):
            self.recover(output,envelope,grant,pin,callback=lifetime)
        marker=case/'attempt-001/manifest.json';self.assertTrue(marker.exists())
        value=json.loads(marker.read_bytes());self.assertEqual(value['status'],'failed')
        self.assertLess(value['days_completed'],300)
        self.assertFalse((output/'pair-result.json').exists())
    def test_underlying_foreign_authority_claims_cannot_hide_behind_wrapper(self):
        output=self.partial();envelope,grant,pin=self.grant(output);value=json.loads(grant.read_bytes())
        for key in ('closed_dispatch_proof','ledger_admission'):
            proof=Path(value[key]['path']);typed=json.loads(proof.read_bytes())
            leaf=Path(typed['evidence'][0]['path']);underlying=json.loads(leaf.read_bytes())
            underlying.update(case_ids=['0'*64],original_deadline=0,parent_authorization_sha256='0'*64)
            underlying['owned_producers_absent']=False;underlying['shared_budget_admitted']=False
            leaf.write_bytes(canonical(underlying)+b'\n');typed['evidence'][0]['sha256']=file_digest(leaf)
            proof.write_bytes(canonical(typed)+b'\n');value[key]['sha256']=file_digest(proof)
        grant.write_bytes(canonical(value)+b'\n')
        with self.assertRaisesRegex(ValueError,'underlying typed evidence semantic binding'):
            self.recover(output,envelope,grant,file_digest(grant))
        self.assertFalse((output/'pair-result.json').exists())
    def test_changed_native_tree_cannot_reuse_old_authority_proofs(self):
        output=self.partial();envelope,grant,pin=self.grant(output)
        (output/'late-extra.raw').write_bytes(b'retained original extra')
        newer=h.resume_envelope(self.profile,self.runtime,output)
        value=json.loads(grant.read_bytes());value['envelope_sha256']=digest(canonical(newer))
        grant.write_bytes(canonical(value)+b'\n')
        with self.assertRaisesRegex(ValueError,'referenced proof semantic binding'):
            self.recover(output,newer,grant,file_digest(grant))
        self.assertFalse((output/'pair-result.json').exists())
    def test_original_parent_authorization_body_is_parsed(self):
        output=self.partial();envelope,grant,pin=self.grant(output);value=json.loads(grant.read_bytes())
        descriptors=[]
        for key in ('closed_dispatch_proof','ledger_admission'):
            proof=Path(value[key]['path']);typed=json.loads(proof.read_bytes())
            leaf=Path(typed['evidence'][0]['path']);body=json.loads(leaf.read_bytes())
            descriptors.append((key,proof,typed,leaf,body))
        parent=Path(descriptors[0][4]['parent_authorization']['path'])
        body=json.loads(parent.read_bytes());body['case_ids']=['0'*64];parent.write_bytes(canonical(body)+b'\n')
        parent_sha=file_digest(parent);value['parent_authorization_sha256']=parent_sha
        for key,proof,typed,leaf,body in descriptors:
            body['parent_authorization']['sha256']=parent_sha;body['parent_authorization_sha256']=parent_sha
            leaf.write_bytes(canonical(body)+b'\n');typed['parent_authorization_sha256']=parent_sha
            typed['evidence'][0]['sha256']=file_digest(leaf);proof.write_bytes(canonical(typed)+b'\n')
            value[key]['sha256']=file_digest(proof)
        grant.write_bytes(canonical(value)+b'\n')
        with self.assertRaisesRegex(ValueError,'parent authorization semantic binding'):
            self.recover(output,envelope,grant,file_digest(grant))
    def test_symlink_and_hardlink_inputs_refused(self):
        output=self.partial();sentinel=output/'bad.json';sentinel.symlink_to(output/'paired-operation.json')
        with self.assertRaisesRegex(ValueError,'symlink'):h.resume_envelope(self.profile,self.runtime,output)
        sentinel.unlink();os.link(output/'paired-operation.json',sentinel)
        with self.assertRaisesRegex(ValueError,'multiply linked'):h.resume_envelope(self.profile,self.runtime,output)
    def test_cumulative_elapsed_timeout_not_reset(self):
        output=self.partial();case=output/h.SELECTED_ARMS[1]/'cases'/self.rows[1]['case_id']
        (case/'attempt-000-exit.json').write_bytes(canonical({'process_exit_code':1,'attempt_wall_seconds':30})+b'\n')
        envelope,grant,pin=self.grant(output)
        with self.assertRaisesRegex(ValueError,'cumulative'):self.recover(output,envelope,grant,pin)
        self.assertFalse((case/'attempt-001').exists())

if __name__=='__main__':unittest.main(verbosity=2)
