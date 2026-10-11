"""Portable, provider-free controls for the N300 operational helper.

All selection/status/cloud inputs are generated engineering fixtures. The short
pair uses four agents and five days; it adds no formal samples. Original spawned
Scheduler/Model, whole-case raw validator and full checkpoint restore run.
"""
import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research_tools')]
sys.dont_write_bytecode = True
from research_tools import small_enterprise_cloud as h
from research_tools import cloud_control as cc
from dams_sim.config import Config
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.model import Model
from dams_sim.runtime import RuntimeLimits, available_memory
from dams_sim.scheduler import Scheduler, output_files
from dams_sim.spec import case_key
from dams_sim.storage import canonical, digest, file_digest
from dams_sim.longitudinal_pipeline import driver_hash


def limits():
    now = datetime.now(timezone.utc)
    return RuntimeLimits(workers_auto=False, max_workers=1, cpu_budget=1,
        memory_budget_bytes=2**30, per_world_rss_bytes=512*2**20,
        max_events=2_000_000, max_output_bytes=16*2**30,
        batch_max_output_bytes=32*2**30, batch_max_output_files=8192,
        per_world_output_files=8192, min_free_disk_bytes=80*2**30,
        world_timeout_seconds=86400, deadline_utc=(now+timedelta(minutes=10)).isoformat(),
        stop_cutoff_utc=(now+timedelta(minutes=11)).isoformat(),
        checkpoint_interval_days=90, checkpoint_interval_seconds=600,
        journal_chunk_bytes=2**20, max_retries=1).to_dict()


def selector_fixture(runtime):
    # Synthetic statuses are parser/selector fixtures, never evidence that a
    # genuine case is unstarted or permission to execute it.
    rows=[]
    for world in range(52000,52066):
        for row in h.plan(runtime,world=world)[2]:
            rows.append({'case_id':row['case_id'],'config_sha256':digest(canonical(row['config'])),
                'world':world,'arm':row['tags']['arm_id'],'state':'UNSTARTED'})
    snapshot={'schema':h.SNAPSHOT_SCHEMA,'source_sha256':h.CORE,
        'pipeline_driver_sha256':h.DRIVER,'study_sha256':h.CONFIRMATION_STUDY_SHA,
        'confirmation_roster_sha256':h.CONFIRMATION_ROSTER_SHA,'population':300,
        'observed_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'cases':rows}
    return h.build_pair_profile({**runtime,'batch_max_output_files':32798},world=52008,
        status_snapshot=snapshot,status_snapshot_sha256=digest(canonical(snapshot)+b'\n'))


def cloud_fixture(runtime,profile):
    region='us-central1';zone=region+'-a'
    stage={'id':'engineering-pair','spec':'small-enterprise-5y','scale':300,
        'machine_type':'n2-highmem-4','purchase_mode':'STANDARD','disk_type':'pd-balanced',
        'architecture_declaration':'official-n2-x86_64',
        'quota':{'metric':'N2_CPUS','info_id':'N2-CPUS-per-project-region','dimensions':{'region':region}},
        'expected_guest':{'architecture':'x86_64','vcpus':4,'memory_gib_min':'30','memory_gib_max':'32'},
        'termination_action':'STOP','small_enterprise_profile':profile,'runtime_limits':runtime}
    now=datetime.now(timezone.utc)
    config={'version':1,'authorization_id':'engineering-fixture','project':'engineering-research',
        'region':region,'zones':[zone],'bucket':'engineering-study-archive',
        'service_account':'fixture@engineering-research.iam.gserviceaccount.com',
        'subnet':'engineering-subnet','image':'https://example.invalid/projects/engineering/global/images/fixed-image',
        'image_id':'1','source_commit':'0'*40,'global_deadline_utc':(now+timedelta(hours=1)).isoformat(),
        'budget_cap_usd':'100','reserve_usd':'20','prior_spend_usd':'0',
        'price_snapshot':{'region':region,'currency':'USD','checked_utc':now.isoformat(),
            'sources':['https://example.invalid/engineering-fixture-only'],
            'machines':{'n2-highmem-4':{'vcpus':4,'memory_gib':'32','zone':zone}}},
        'stages':[stage],'gcloud_configuration':'engineering-fixture','gcloud_account':'fixture@example.invalid',
        'network_mode':'iap-ephemeral-ip','dedicated_project':True,
        'outbound_access_verified':True,'restricted_firewall_verified':True,
        'execution_mode':'compute-only-iap'}
    return config,stage


class Controls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.limits=limits();cls.profile=selector_fixture(cls.limits)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_actual_layering_contradiction_rejected_before_scheduler(self):
        with patch.object(h,'Scheduler',side_effect=AssertionError('not admitted')):
            with self.assertRaisesRegex(ValueError,'declared 8192, required 32798'):
                h.validate_pair_profile(self.profile,self.limits)
    def test_retry_metadata_exit_and_temporary_slots_accounted(self):
        value=h.paired_file_slot_requirements(self.limits)
        self.assertEqual(value['attempt_file_slots'],2*2*8192)
        self.assertEqual(value['attempt_exit_receipt_slots'],4)
        self.assertEqual(value['named_metadata_slots'],20)
        self.assertEqual(value['temporary_publication_slots'],6)
        self.assertEqual(value['minimum_whole_pair_files'],32798)
        self.assertEqual(value['minimum_terminal_files'],32798)
        self.assertFalse(value['scientific_file_production_measured'])
    def test_explicit_file_candidate_preserves_scientific_inventory(self):
        proposed={**self.limits,'batch_max_output_files':32798}
        self.assertEqual(h.validate_pair_profile(self.profile,proposed),self.profile)
        original=h.plan(self.limits,world=52008)[2]
        self.assertEqual(canonical(h.assignment(self.profile,proposed)['cases']),canonical(original))
        self.assertEqual(proposed['max_output_bytes'],16*2**30)
        self.assertEqual(proposed['batch_max_output_bytes'],32*2**30)
        self.assertEqual(proposed['min_free_disk_bytes'],80*2**30)
    def test_no_automatic_limit_raise_and_exact_boundary(self):
        before=copy.deepcopy(self.limits)
        with self.assertRaises(ValueError):h._admit_paired_file_slots(self.limits)
        self.assertEqual(self.limits,before)
        with self.assertRaises(ValueError):h._admit_paired_file_slots({**before,'batch_max_output_files':32797})
        h._admit_paired_file_slots({**before,'batch_max_output_files':32798})
    def test_owned_attempt_cap_not_reduced_and_retries_not_ignored(self):
        with self.assertRaisesRegex(ValueError,'unchanged 8192'):
            h._admit_paired_file_slots({**self.limits,'batch_max_output_files':32798,'per_world_output_files':4096})
        value={**self.limits,'batch_max_output_files':32798,'max_retries':2}
        self.assertEqual(h.paired_file_slot_requirements(value)['minimum_whole_pair_files'],49184)
        with self.assertRaisesRegex(ValueError,'required 49184'):h._admit_paired_file_slots(value)
    def test_original_checked_config_and_create_hook_fail_without_sdk(self):
        config,stage=cloud_fixture(self.limits,self.profile)
        startup=ROOT/'cloud/compute-only-wait-source.sh'
        with patch.object(cc.Gcloud,'run',side_effect=AssertionError('no provider')) as sdk:
            with self.assertRaisesRegex(cc.GuardError,'declared 8192, required 32798'):cc.checked_config(config)
            with self.assertRaisesRegex(cc.GuardError,'declared 8192, required 32798'):
                cc.create_compute_only_command(config,stage,{},config['zones'][0],startup,
                    startup_sha256=file_digest(startup))
            self.assertEqual(sdk.call_count,0)
    def test_original_reader_still_requires_exact_three_identity_keys(self):
        identity={'schema_version':3,'source_sha256':h.CORE,'pipeline_driver_sha256':h.DRIVER,
            'spec_sha256':self.profile['study_sha256']}
        with self.assertRaisesRegex(ValueError,'identity is incomplete'):
            h.study.CheckedLongitudinalCases(self.root,identity)
        reader=h.study.CheckedLongitudinalCases(self.root,{k:identity[k] for k in
            ('source_sha256','pipeline_driver_sha256','spec_sha256')})
        self.assertEqual(reader.result()['unique_complete_cases'],0)
    @unittest.skipUnless(available_memory()>=2**30,'native Scheduler control requires declared 1GiB RAM')
    def test_original_scheduler_native_four_metadata_failure(self):
        for name in ('.small-operation.lock','small-operation.json','confirmation-operation/case_inventory.json','confirmation-operation/manifest.json'):
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'{}\n')
        self.assertEqual(output_files(self.root),4)
        # Zero floor isolates the file guard on small CI disks. This is a
        # Scheduler engineering fixture, not an authorized runtime/profile.
        runtime=RuntimeLimits.from_dict({**self.limits,'min_free_disk_bytes':0})
        config=Config.from_dict(h.plan(self.limits,world=52008)[2][0]['config'])
        scheduler=Scheduler(self.root,runtime,driver_hash())
        with self.assertRaisesRegex(InterruptedError,'insufficient whole pipeline file slots'):
            scheduler.run([config])
        self.assertTrue(scheduler.owned_workers_absent);self.assertFalse((self.root/'cases').exists())
    @unittest.skipUnless(available_memory()>=2**30,'native pair control requires declared 1GiB RAM')
    def test_real_engineering_pair_model_raw_gate_and_checkpoint_restore(self):
        runtime=RuntimeLimits.from_dict({**self.limits,'batch_max_output_files':32798,
            'max_output_bytes':8*2**20,'batch_max_output_bytes':32*2**20,'min_free_disk_bytes':0,
            'checkpoint_interval_days':2,'world_timeout_seconds':30,
            'provenance':{'environment':'ENGINEERING_ONLY_NO_FORMAL_SAMPLE'}})
        configs=[Config(n=4,days=5,guilds=2,team_size=2,sites=1,seed=20261012,world=912300,
            regime=regime,trace_every_days=1,max_wall_seconds=30,max_output_mb=8*2**20/1_000_000,
            max_rss_mb=512,longitudinal=LongitudinalConfig(calendar_start='2020-01-01',
                max_active_members=8,max_people_ever=16,annual_exit_probability=0,
                adoption_day=0,transition_days=1)) for regime in ('hierarchy','sublinear')]
        rows=[{'case_id':case_key(c),'config':c.to_dict(),'tags':{'parent_case_id':None,'arm_id':arm,
            'world':912300}} for arm,c in zip(h.SELECTED_ARMS,configs)]
        declaration={'engineering_fixture':True,'initial_n':4,'days':5,'formal_samples':0}
        arms=[{'schema':h.SCHEMA,'engineering_fixture':True,'study_sha256':digest(canonical(declaration)),
            'pilot_inventory_sha256':digest(canonical(rows)),'selected_world':912300,'selected_arm':arm,
            'selected_case_id':row['case_id']} for arm,row in zip(h.SELECTED_ARMS,rows)]
        profile={'schema':h.PAIR_SCHEMA,'engineering_fixture':True,'study_sha256':digest(canonical(declaration)),
            'pilot_inventory_sha256':digest(canonical(rows)),'selected_world':912300,'arm_profiles':arms}
        def selector(value,rt):
            self.assertIs(value['engineering_fixture'],True)
            if value['schema']==h.PAIR_SCHEMA:h._admit_paired_file_slots(rt)
            return value
        def plan(rt,*,world):
            self.assertEqual(world,912300);return runtime,declaration,rows
        output=self.root/'engineering-output'
        with patch.object(h,'validate_profile',selector),patch.object(h,'validate_pair_profile',selector),patch.object(h,'plan',plan):
            h.execute(profile,runtime,output)
        result=json.loads((output/'pair-result.json').read_bytes())
        self.assertTrue(result['paired_world_complete']);self.assertEqual(len(result['arms']),2)
        for arm,row in zip(h.SELECTED_ARMS,rows):
            child=output/arm;attempts=[p for p in (child/'cases'/row['case_id']).glob('attempt-*') if p.is_dir()]
            self.assertEqual(len(attempts),1)
            self.assertTrue((child/'cases'/row['case_id']/'attempt-000-exit.json').is_file())
            manifest=json.loads((attempts[0]/'manifest.json').read_bytes())
            self.assertEqual(manifest['status'],'complete');self.assertEqual(manifest['config'],json.loads(canonical(row['config'])))
            index=json.loads((attempts[0]/'checkpoint-index.json').read_bytes())
            checkpoint=attempts[0]/index['snapshots'][0]['file']
            envelope=json.loads(checkpoint.read_bytes())
            restored=Model.restore_checkpoint(checkpoint,storage_dir=self.root/('restore-'+arm),
                expected_config=Config.from_dict(row['config']))
            try:
                self.assertEqual(restored.day,5)
                self.assertEqual(restored._long.semantic_digest(),envelope['state_semantic_sha256'])
            finally:restored._long.ledger.close()
        self.assertLess(output_files(output),runtime.batch_max_output_files)


if __name__=='__main__':unittest.main(verbosity=2)
