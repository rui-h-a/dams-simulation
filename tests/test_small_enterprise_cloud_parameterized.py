"""Portable engineering controls for the frozen N300 cloud selector and pair envelope.

Run from a clean source checkout with Python 3.11 or later:
    python -m unittest discover -s tests -p test_small_enterprise_cloud_parameterized.py

The synthetic UNSTARTED census and dispatcher stubs are engineering fixtures.
They authorize no paid resource, generate no scientific sample, accept no raw
result, and provide no evidence of scientific study completion.
"""
import ast
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1]
FROZEN_FIXTURES = SOURCE/'docs/frozen-studies/small-enterprise-five-year'
sys.path[:0] = [str(SOURCE), str(SOURCE/'research_tools')]
from research_tools import small_enterprise_cloud as candidate
from research_tools import small_enterprise as frozen
from dams_sim.runtime import RuntimeLimits
from dams_sim.config import Config
from dams_sim.spec import case_key

frozen_limits = json.loads((FROZEN_FIXTURES/'n300-wave-runtime.json').read_bytes())
LIMITS = copy.deepcopy(frozen_limits)
LIMITS.update(max_output_bytes=16*1024**3, batch_max_output_bytes=32*1024**3+256*1024**2,
              batch_max_output_files=20000, per_world_output_files=10000,
              max_workers=1, cpu_budget=1)

def synthetic_snapshot():
    cases = []
    for world in range(52000, 52066):
        for row in frozen.rows(300, world, RuntimeLimits.from_dict(frozen_limits)):
            cases.append({'case_id':row['case_id'], 'config_sha256':candidate.digest(candidate.canonical(row['config'])),
                          'world':world, 'arm':row['tags']['arm_id'], 'state':'UNSTARTED'})
    return {'schema':candidate.SNAPSHOT_SCHEMA, 'source_sha256':candidate.CORE,
            'pipeline_driver_sha256':candidate.DRIVER, 'study_sha256':candidate.CONFIRMATION_STUDY_SHA,
            'confirmation_roster_sha256':candidate.CONFIRMATION_ROSTER_SHA,
            'population':300, 'observed_utc':'2026-10-12T00:00:00Z', 'cases':cases}

SNAPSHOT = synthetic_snapshot()
def pin(snapshot): return candidate.digest(candidate.canonical(snapshot)+b'\n')
def profile(world=52008, arm='startup-existing', snapshot=None):
    snapshot = copy.deepcopy(SNAPSHOT) if snapshot is None else snapshot
    return candidate.build_profile(LIMITS, world=world, arm=arm,
                                   status_snapshot=snapshot, status_snapshot_sha256=pin(snapshot))

class ParameterizedSelector(unittest.TestCase):
    def test_01_same_source_selects_multiple_worlds_both_arms_without_identity_drift(self):
        helper_before = candidate.file_digest(Path(candidate.__file__))
        for world in (52008, 52009, 52065):
            original = [{k:r[k] for k in ('case_id','config','tags')}
                        for r in frozen.rows(300,world,RuntimeLimits.from_dict(frozen_limits))]
            _, declaration, actual = candidate.plan(LIMITS,world=world)
            self.assertEqual(candidate.canonical(actual), candidate.canonical(original))
            for arm, row in zip(candidate.SELECTED_ARMS, original):
                p = profile(world, arm)
                self.assertEqual(p['selected_case_id'], row['case_id'])
                self.assertEqual(p['selected_config_sha256'], candidate.digest(candidate.canonical(row['config'])))
                self.assertEqual(p['study_sha256'], candidate.digest(candidate.canonical(declaration)))
                self.assertEqual(p['days'], 1917)
                self.assertEqual(p['seed'], 20261008)
                self.assertEqual(row['case_id'], case_key(Config.from_dict(row['config'])))
                self.assertEqual(candidate.validate_profile(p,LIMITS),p)
                self.assertEqual(candidate.assignment(p,LIMITS)['cases'], original)
        self.assertEqual(candidate.file_digest(Path(candidate.__file__)), helper_before)
        self.assertNotIn('SELECTED_CONFIRMATION_WORLD',Path(candidate.__file__).read_text())

    def test_02_all_non_unstarted_states_refuse_fresh_run(self):
        for state in ('COMPLETE','PARTIAL','UNKNOWN','RUNNING','FAILED'):
            s=copy.deepcopy(SNAPSHOT)
            next(r for r in s['cases'] if r['world']==52008 and r['arm']=='startup-existing')['state']=state
            with self.subTest(state=state), self.assertRaisesRegex(ValueError,'not explicitly unstarted'):
                profile(snapshot=s)
            self.assertEqual(profile(52008,'startup-founding',s)['selected_arm'],'startup-founding')

    def test_03_bad_world_arm_and_numeric_types_refused(self):
        for world in (True,52008.0,42000,51999,52066,-1):
            with self.subTest(world=world), self.assertRaises(ValueError): profile(world)
        for arm in ('hierarchy','DAMS','both','',None):
            with self.subTest(arm=arm), self.assertRaises(ValueError): profile(arm=arm)

    def test_04_every_case_required_duplicates_and_unknown_status_refused(self):
        changes=[lambda s:s['cases'].pop(),
                 lambda s:s['cases'].__setitem__(1,copy.deepcopy(s['cases'][0])),
                 lambda s:s['cases'][0].__setitem__('state','complete'),
                 lambda s:s['cases'][0].__setitem__('world',True),
                 lambda s:s['cases'][0].__setitem__('config_sha256','0'*64),
                 lambda s:s['cases'][0].__setitem__('unexpected',0)]
        for mutation in changes:
            s=copy.deepcopy(SNAPSHOT); mutation(s)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError): profile(snapshot=s)

    def test_05_source_roster_population_and_digest_mismatch_refused(self):
        for field,value in [('source_sha256','0'*64),('pipeline_driver_sha256','0'*64),
                            ('study_sha256','0'*64),('confirmation_roster_sha256','0'*64),
                            ('population',120),('population',True),('schema','other'),
                            ('observed_utc','yesterday'),('observed_utc','2026-10-12Z'),('extra',1)]:
            s=copy.deepcopy(SNAPSHOT);s[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):profile(snapshot=s)
        with self.assertRaisesRegex(ValueError,'body pin'):
            candidate.build_profile(LIMITS,world=52008,arm='startup-existing',
                                   status_snapshot=SNAPSHOT,status_snapshot_sha256='0'*64)

    def test_06_profile_tampering_and_unsealed_paths_refused(self):
        p=profile()
        for field,value in [('selected_case_id','0'*64),('selected_world',52009),('seed',2),
                            ('days',1827),('status_snapshot_sha256','0'*64),('host_path','/private/host')]:
            modified=copy.deepcopy(p);modified[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):candidate.validate_profile(modified,LIMITS)
        modified=copy.deepcopy(p);modified['status_snapshot']['cases'][0]['state']='COMPLETE'
        with self.assertRaises(ValueError):candidate.validate_profile(modified,LIMITS)

    def test_07_guest_profile_is_self_contained_after_snapshot_file_disappears(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            path=Path(temporary)/'snapshot.json';path.write_bytes(candidate.canonical(SNAPSHOT)+b'\n')
            snapshot,_=candidate._profile(path,pin(SNAPSHOT))
            p=candidate.build_profile(LIMITS,world=52009,arm='startup-founding',
                                     status_snapshot=snapshot,status_snapshot_sha256=pin(snapshot))
            path.unlink()
            self.assertEqual(candidate.validate_profile(p,LIMITS),p)
            self.assertNotIn(str(path),candidate.canonical(p).decode())
            self.assertLessEqual(len(candidate.canonical(p)),65536)

    def test_08_frozen_config_limits_and_complete_case_envelope_preserved(self):
        for field,value in [('max_events',100),('world_timeout_seconds',1),('per_world_rss_bytes',1024),
                            ('journal_chunk_bytes',2097152),('max_output_bytes',1024)]:
            modified=copy.deepcopy(LIMITS);modified[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                candidate.build_profile(modified,world=52008,arm='startup-existing',
                                        status_snapshot=SNAPSHOT,status_snapshot_sha256=pin(SNAPSHOT))

    def test_09_existing_local_case_is_preserved_and_scheduler_never_called(self):
        p=profile()
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            output=Path(temporary)/'output';case=output/'cases'/p['selected_case_id']/'attempt-000001'
            case.mkdir(parents=True);sentinel=case/'checkpoint-retained';sentinel.write_bytes(b'unique retained state')
            with patch.object(candidate,'Scheduler',side_effect=AssertionError('no restart')):
                with self.assertRaisesRegex(ValueError,'already has local state'):
                    candidate.execute(p,LIMITS,output)
            self.assertEqual(sentinel.read_bytes(),b'unique retained state')
            self.assertFalse((output/'small-operation.json').exists())

    def test_10_completed_local_result_cannot_be_regenerated(self):
        p=profile()
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            output=Path(temporary)/'output';output.mkdir()
            marker=output/'single-arm-result.json';marker.write_bytes(b'{"retained":true}\n')
            with patch.object(candidate,'Scheduler',side_effect=AssertionError('no restart')):
                with self.assertRaisesRegex(ValueError,'completed case'):
                    candidate.execute(p,LIMITS,output)
            self.assertEqual(marker.read_bytes(),b'{"retained":true}\n')

    def test_11_native_case_lock_excludes_second_output_owner_and_releases(self):
        p=profile()
        with candidate._case_writer(p):
            with self.assertRaises(BlockingIOError):
                with candidate._case_writer(p): self.fail('second writer admitted')
        with candidate._case_writer(p):pass
        p2=profile(52009)
        with candidate._case_writer(p),candidate._case_writer(p2):pass

    def test_12_actual_child_cannot_acquire_held_case_lock(self):
        p=profile()
        code="import sys;sys.dont_write_bytecode=True;from research_tools import small_enterprise_cloud as c;import json\np=json.load(sys.stdin)\ntry:\n with c._case_writer(p): print('UNEXPECTED')\nexcept BlockingIOError: print('REFUSED')\n"
        env={**os.environ,'PYTHONPATH':str(SOURCE),'TMPDIR':str(Path(tempfile.gettempdir()).resolve())}
        with candidate._case_writer(p):
            result=subprocess.run([sys.executable,'-c',code],input=json.dumps(p),text=True,
                                  capture_output=True,env=env,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'REFUSED')

    def test_13_cli_plan_uses_pinned_readonly_snapshot_without_scientific_execution(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            temp=Path(temporary);snapshot=temp/'snapshot.json';snapshot.write_bytes(candidate.canonical(SNAPSHOT)+b'\n')
            limits=temp/'limits.json';limits.write_text(json.dumps(LIMITS))
            stdout=io.StringIO()
            with patch.object(candidate,'Scheduler',side_effect=AssertionError('no producer')),contextlib.redirect_stdout(stdout):
                rc=candidate.main(['--population','300','--output',str(temp/'unused'),'--runtime-limits',str(limits),
                                   '--plan-only','--world','52009','--arm','startup-existing',
                                   '--status-snapshot',str(snapshot),'--status-snapshot-sha256',pin(SNAPSHOT)])
            result=json.loads(stdout.getvalue())
            self.assertEqual(rc,0);self.assertEqual(result['profile']['selected_world'],52009)
            self.assertFalse(result['paid_actions_authorized']);self.assertFalse(result['paired_world_complete'])
            self.assertFalse((temp/'unused').exists())

    def test_14_noncanonical_or_duplicate_snapshot_json_refused(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            for raw in (json.dumps(SNAPSHOT,indent=2).encode(),
                        candidate.canonical(SNAPSHOT).replace(b'"population":300',b'"population":300,"population":300')+b'\n'):
                path=Path(temporary)/'snapshot.json';path.write_bytes(raw)
                sha=hashlib.sha256(raw).hexdigest()
                with self.assertRaises(ValueError):
                    s,_=candidate._profile(path,sha)
                    candidate.validate_status_snapshot(s,sha,LIMITS)

    def test_15_snapshot_symlink_and_hardlink_refused(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            tmp=Path(temporary);real=tmp/'real.json';real.write_bytes(candidate.canonical(SNAPSHOT)+b'\n')
            alias=tmp/'alias.json';alias.symlink_to(real)
            with self.assertRaises(ValueError):candidate._profile(alias,pin(SNAPSHOT))
            alias.unlink();os.link(real,alias)
            with self.assertRaises(ValueError):candidate._profile(alias,pin(SNAPSHOT))

    def test_16_failed_constructor_retains_both_frozen_arms_and_false_pair_gate(self):
        p=profile(52009)
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            output=Path(temporary)/'output'
            with patch.object(candidate,'Scheduler',side_effect=ValueError('engineering constructor refusal')):
                with self.assertRaisesRegex(ValueError,'engineering constructor refusal'):
                    candidate.execute(p,LIMITS,output)
            rows=json.loads((output/'confirmation-operation/case_inventory.json').read_bytes())
            self.assertEqual(candidate.canonical(rows),candidate.canonical(candidate.assignment(p,LIMITS)['cases']))
            census=json.loads((output/'confirmation-operation/case_census.json').read_bytes())
            self.assertEqual(census['expected_cases'],2)
            self.assertEqual(census['inventory_sha256'],p['pilot_inventory_sha256'])
            manifest=json.loads((output/'confirmation-operation/manifest.json').read_bytes())
            self.assertFalse(manifest['full_study_gate'])
            self.assertEqual(manifest['status'],'partial')
            self.assertTrue(manifest['partial_census_exact'])
            self.assertFalse((output/'single-arm-result.json').exists())
            self.assertFalse((output/'cases').exists())

    def test_17_original_frozen_science_and_worker_files_are_byte_identical(self):
        # The full 72-file original manifest remains available for provenance.
        # Archive/controller infrastructure may be independently versioned; it
        # is not a frozen scientific transition and is tested by its own suite.
        original=json.loads((FROZEN_FIXTURES/'cloud-helper-original-source-sha256.json').read_bytes())
        self.assertEqual(len(original),72)
        frozen_names={name for name in original if name.startswith('dams_sim/')}
        frozen_names.update({'research_tools/small_enterprise.py',
                             'research_tools/validate_longitudinal.py',
                             'research_tools/validate_pipeline.py',
                             'research_tools/recovery_checks.py',
                             'research_tools/compute_only_worker.py'})
        for name in sorted(frozen_names):
            with self.subTest(name=name):
                path=SOURCE/name
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_size,original[name]['bytes'])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),original[name]['sha256'])
        self.assertEqual(candidate.source_hash(),candidate.CORE)
        self.assertEqual(candidate.driver_hash(),candidate.DRIVER)
        self.assertEqual(candidate.file_digest(Path(frozen.__file__)),candidate.STUDY_IMPORT_SHA)



class PairedEnvelope(unittest.TestCase):
    def paired(self,world=52008,snapshot=None):
        snapshot=copy.deepcopy(SNAPSHOT) if snapshot is None else snapshot
        return candidate.build_pair_profile(LIMITS,world=world,status_snapshot=snapshot,
                                            status_snapshot_sha256=pin(snapshot))

    def fake_original_arm(self, arm, limits, output):
        """Engineering stub for dispatcher tests; no actual raw result acceptance."""
        output=Path(output)
        rows=candidate.assignment(arm,limits)['cases']
        stage=output/'confirmation-operation';stage.mkdir(exist_ok=True)
        (stage/'case_inventory.json').write_bytes(candidate.canonical(rows)+b'\n')
        case=output/'cases'/arm['selected_case_id']/'attempt-000001';case.mkdir(parents=True)
        (case/'engineering-stub-only').write_bytes(b'Not a scientific case or result')
        result={'status':'COMPLETE_INDIVIDUAL_FULL_RAW_VERIFIED','profile':arm,
                'case_id':arm['selected_case_id'],'manifest_sha256':'0'*64,
                'paired_world_complete':False,'full_study_complete':False,
                'empirical_validation':False}
        (output/'single-arm-result.json').write_bytes(candidate.canonical(result)+b'\n')

    def test_18_paired_original_profiles_same_science_and_wire_bound(self):
        for world in (52008,52009):
            p=self.paired(world)
            self.assertLessEqual(len(candidate.canonical(p)),65536)
            self.assertEqual(candidate.validate_profile(p,LIMITS),p)
            self.assertEqual([a['selected_arm'] for a in p['arm_profiles']],list(candidate.SELECTED_ARMS))
            self.assertEqual([a['selected_world'] for a in p['arm_profiles']],[world,world])
            self.assertEqual(candidate.assignment(p,LIMITS)['cases'],candidate.assignment(profile(world),LIMITS)['cases'])
            for arm in p['arm_profiles']:
                self.assertEqual(arm,profile(world,arm['selected_arm']))

    def test_19_different_world_snapshot_source_order_and_nonunstarted_refused(self):
        good=self.paired()
        mutations=[lambda p:p['arm_profiles'].__setitem__(1,profile(52009,'startup-founding')),
                   lambda p:p['arm_profiles'].reverse(),
                   lambda p:p['arm_profiles'][1].__setitem__('status_snapshot_sha256','0'*64),
                   lambda p:p.__setitem__('source_sha256','0'*64),
                   lambda p:p.__setitem__('selected_world',52009),
                   lambda p:p.__setitem__('seed',2)]
        for mutation in mutations:
            p=copy.deepcopy(good);mutation(p)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):candidate.validate_profile(p,LIMITS)
        for state in ('COMPLETE','PARTIAL','UNKNOWN','RUNNING','FAILED'):
            snapshot=copy.deepcopy(SNAPSHOT)
            next(r for r in snapshot['cases'] if r['world']==52008 and r['arm']=='startup-founding')['state']=state
            with self.subTest(state=state),self.assertRaises(ValueError):self.paired(snapshot=snapshot)

    def test_20_original_case_output_and_disk_floor_cannot_be_reduced(self):
        for field,value in [('max_output_bytes',8*1024**3),('max_output_bytes',32*1024**3),
                            ('batch_max_output_bytes',16*1024**3),('min_free_disk_bytes',79*1024**3)]:
            limits=copy.deepcopy(LIMITS);limits[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                candidate.build_pair_profile(limits,world=52008,status_snapshot=SNAPSHOT,status_snapshot_sha256=pin(SNAPSHOT))

    def test_21_both_original_arms_dispatch_once_in_order_to_separate_outputs(self):
        p=self.paired();calls=[]
        def stub(arm,limits,output):
            calls.append((arm['selected_arm'],str(output),candidate.digest(candidate.canonical(arm))))
            self.fake_original_arm(arm,limits,output)
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            output=Path(temporary)/'pair'
            with patch.object(candidate,'_execute_owned',side_effect=stub):candidate.execute(p,LIMITS,output)
            self.assertEqual([x[0] for x in calls],list(candidate.SELECTED_ARMS))
            self.assertEqual([Path(x[1]).name for x in calls],list(candidate.SELECTED_ARMS))
            result=json.loads((output/'pair-result.json').read_bytes())
            self.assertTrue(result['paired_world_complete'])
            self.assertEqual(result['independent_confirmation_worlds'],1)
            self.assertFalse(result['full_study_complete']);self.assertFalse(result['empirical_validation'])
            intent=json.loads((output/'paired-operation.json').read_bytes())
            self.assertEqual(len(intent['completed_arm_receipts']),2)
            for arm in p['arm_profiles']:
                child=output/arm['selected_arm']
                self.assertTrue((child/'single-arm-result.json').exists())
                self.assertEqual(candidate.canonical(json.loads((child/'confirmation-operation/case_inventory.json').read_bytes())),
                                 candidate.canonical(candidate.assignment(arm,LIMITS)['cases']))

    def test_22_failed_second_arm_preserves_first_and_never_declares_pair_complete(self):
        p=self.paired();calls=[]
        def stub(arm,limits,output):
            calls.append(arm['selected_arm'])
            if len(calls)==2:
                (Path(output)/'second-arm-engineering-failure').write_bytes(b'preserved failure')
                raise ValueError('second arm failed')
            self.fake_original_arm(arm,limits,output)
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            output=Path(temporary)/'pair'
            with patch.object(candidate,'_execute_owned',side_effect=stub):
                with self.assertRaisesRegex(ValueError,'second arm failed'):candidate.execute(p,LIMITS,output)
            self.assertFalse((output/'pair-result.json').exists())
            first=output/'startup-existing'
            self.assertTrue((first/'single-arm-result.json').exists())
            self.assertTrue((first/'cases'/p['arm_profiles'][0]['selected_case_id']/'attempt-000001/engineering-stub-only').exists())
            self.assertTrue((output/'startup-founding/second-arm-engineering-failure').exists())
            intent=json.loads((output/'paired-operation.json').read_bytes())
            self.assertFalse(intent['paired_world_complete']);self.assertEqual(len(intent['completed_arm_receipts']),1)
            with patch.object(candidate,'_execute_owned',side_effect=AssertionError('must preserve before restart')):
                with self.assertRaisesRegex(ValueError,'already has state'):candidate.execute(p,LIMITS,output)

    def test_23_missing_or_wrong_first_receipt_does_not_run_second_arm(self):
        p=self.paired()
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            output=Path(temporary)/'pair'
            with patch.object(candidate,'_execute_owned',return_value=None) as dispatch:
                with self.assertRaisesRegex(ValueError,'receipt missing'):candidate.execute(p,LIMITS,output)
            self.assertEqual(dispatch.call_count,1);self.assertFalse((output/'pair-result.json').exists())

    def test_24_cli_same_existing_plan_interface_accepts_paired_envelope(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            temp=Path(temporary);snapshot=temp/'snapshot.json';snapshot.write_bytes(candidate.canonical(SNAPSHOT)+b'\n')
            limits=temp/'limits.json';limits.write_text(json.dumps(LIMITS));stdout=io.StringIO()
            with patch.object(candidate,'Scheduler',side_effect=AssertionError('no producer')),contextlib.redirect_stdout(stdout):
                rc=candidate.main(['--population','300','--output',str(temp/'unused'),'--runtime-limits',str(limits),
                                   '--plan-only','--pair','--world','52008','--status-snapshot',str(snapshot),
                                   '--status-snapshot-sha256',pin(SNAPSHOT)])
            result=json.loads(stdout.getvalue());self.assertEqual(rc,0)
            self.assertEqual(result['profile']['schema'],candidate.PAIR_SCHEMA)
            self.assertEqual(len(result['assignment']['cases']),2)
            self.assertFalse(result['paired_world_complete']);self.assertFalse((temp/'unused').exists())

    def test_25_existing_guest_runtime_and_environment_dispatch_accept_pair(self):
        from datetime import datetime,timedelta,timezone
        from research_tools import compute_only_worker as worker
        start=datetime.now(timezone.utc);expiry=start+timedelta(hours=4)
        limits=copy.deepcopy(LIMITS)
        limits.update(deadline_utc=(expiry-timedelta(seconds=900)).isoformat(),
                      stop_cutoff_utc=(expiry-timedelta(seconds=500)).isoformat())
        p=candidate.build_pair_profile(limits,world=52008,status_snapshot=SNAPSHOT,status_snapshot_sha256=pin(SNAPSHOT))
        runtime={'schema':worker.SCHEMA,'source_commit':'8a6c84be2a45e8809a4e5b4900bf027cf1247e57',
                 'source_manifest_sha256':'0'*64,'provider_identity_sha256':'1'*64,'transfer_config_sha256':'2'*64,
                 'spec':'small-enterprise-5y','scale':300,'runtime_limits':limits,
                 'purchase_mode':'STANDARD','machine_type':'c4d-highmem-4',
                 'expected_guest':{'architecture':'x86_64','vcpus':4,'memory_gib_min':30,'memory_gib_max':31},
                 'expected_identity':{'instance_id':'SOURCE-ONLY','zone':'us-central1-a',
                                      'boot_disk_device_name':'source-only','guest_os_release_sha256':'3'*64,
                                      'boot_disk_bytes_min':200*1024**3},
                 'deadline_utc':expiry.isoformat(),'watchdog_shutdown_utc':(expiry-timedelta(seconds=30)).isoformat(),
                 'pipeline_stop_grace_seconds':360,'controller_lease_timeout_seconds':120,
                 'small_enterprise_profile':p}
        self.assertIs(worker.validate_runtime(runtime),runtime)
        bad=copy.deepcopy(runtime);bad['small_enterprise_profile']['selected_world']=42000
        with self.assertRaises(ValueError):worker.validate_runtime(bad)
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            root=Path(temporary);profile_file=root/'pair.json';raw=candidate.canonical(p)+b'\n';profile_file.write_bytes(raw)
            runtime_file=root/'limits.json';runtime_file.write_text(json.dumps(limits))
            with patch.dict(os.environ,{candidate.ENV:str(profile_file),candidate.SHA_ENV:candidate.digest(raw)}), \
                 patch.object(candidate,'execute',return_value=None) as dispatch:
                rc=candidate.main(['--population','300','--output',str(root/'output'),
                                   '--runtime-limits',str(runtime_file)])
            self.assertEqual(rc,0);self.assertEqual(dispatch.call_count,1)
            self.assertEqual(candidate.canonical(dispatch.call_args.args[0]),candidate.canonical(p))
            self.assertFalse((root/'output').exists())

if __name__=='__main__':unittest.main(verbosity=2)
