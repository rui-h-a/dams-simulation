"""Metadata and mocked-worker controls only: no Model/SQL/provider/raw reads."""
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'research_tools'))
sys.path.insert(0, str(ROOT))
from research_tools import original_longitudinal_selector as s
from research_tools import frozen_science_cloud as f
from dams_sim.runtime import RuntimeLimits
from dams_sim.config import Config
from dams_sim.scheduler import Scheduler, branch_identity
import research_tools.compute_only_worker as cw

FIXTURE = Path(__file__).with_name('fixtures') / 'original_selected'


class SelectedMetadata(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name).resolve() / 'work'
        shutil.copytree(FIXTURE, self.work)
        self.a = s.parse((self.work / 'selected-original-case.json').read_bytes())
        self.prepared = s.prepare(self.work, self.a)
        self.lock_path = '/synthetic-original/.pipeline.lock'
        self.lock_patch = patch.object(s, 'ORIGINAL_PIPELINE_LOCK_PATH_SHA256', s.digest(self.lock_path.encode()))
        self.lock_patch.start()

    def tearDown(self):
        self.lock_patch.stop()
        self.temp.cleanup()

    def handoff(self):
        return {'schema': 'DAMS-original-selected-handoff-1', 'status': 'ROOT_EXCLUSIVE_ORIGINAL_PIPELINE_HANDOFF',
                'selection_identity_sha256': self.prepared['selection_identity_sha256'],
                'original_runtime_limits_sha256': s.RUNTIME, 'source_sha256': s.CORE, 'driver_sha256': s.DRIVER,
                'pipeline_lock': {'path': self.lock_path, 'dev': 1, 'ino': 2,
                                  'owner_pid': 4242, 'owner_birth': 'synthetic-epoch', 'exclusive_held': True},
                'writer_absence_receipt_sha256': 'a' * 64, 'parent_full_raw_gate_sha256': 'b' * 64,
                'deadline_utc': self.prepared['original_limits']['deadline_utc'],
                'observed_utc': datetime.now(timezone.utc).isoformat(), 'lease_timeout_seconds': 120,
                'selected_case_unstarted': True, 'return_import_status': 'RETURN_IMPORT_BLOCKED'}

    def guest(self):
        return {'instance_id': '123456', 'zone': 'synthetic-zone', 'machine_type': 'synthetic-machine',
                'hardware_frozen_verified': True}

    def test_actual_pinned144_first_original_sorted_case(self):
        self.assertEqual(self.prepared['row']['case_id'], s.CID)
        self.assertEqual(self.prepared['row']['config']['world'], 20003)
        self.assertEqual(self.prepared['row']['config']['days'], 3743)
        self.assertEqual(self.prepared['origin']['parent_day'], 366)

    def test_core_driver_entry_exact(self):
        s.check_science()

    def test_original_runtime_full789_retained(self):
        raw = (self.work / 'original-runtime-limits.json').read_bytes()
        self.assertEqual(len(raw), 789)
        self.assertEqual(s.digest(raw), s.RUNTIME)

    def test_assignment_seed_config_or_case_override_refused(self):
        for key, value in [('case_id', 'f' * 64), ('config_sha256', 'f' * 64), ('spec_sha256', 'f' * 64)]:
            a = copy.deepcopy(self.a); a[key] = value
            with self.assertRaises(ValueError): s.prepare(self.work, a)

    def test_metadata_bytes_drift_refused(self):
        p = self.work / 'original-statuses.json'; p.write_bytes(p.read_bytes() + b' ')
        with self.assertRaises(ValueError): s.prepare(self.work, self.a)

    def test_inventory_duplicate_case_refused(self):
        inv = s.parse((self.work / 'original-inventory.json').read_bytes())
        inv[0] = inv[1]
        with self.assertRaises(ValueError): s.first_ready(inv, s.parse((self.work / 'original-statuses.json').read_bytes()))

    def test_completed_selected_refused(self):
        inv = s.parse((self.work / 'original-inventory.json').read_bytes())
        statuses = s.parse((self.work / 'original-statuses.json').read_bytes())
        statuses.append({'case_id': s.CID, 'status': 'complete'})
        with self.assertRaises(ValueError): s.first_ready(inv, statuses)

    def test_attempted_selected_refused_not_retry_reset(self):
        inv = s.parse((self.work / 'original-inventory.json').read_bytes())
        statuses = s.parse((self.work / 'original-statuses.json').read_bytes())
        statuses.append({'case_id': s.CID, 'status': 'failed'})
        with self.assertRaises(ValueError): s.first_ready(inv, statuses)

    def test_parent_dependency_uncomplete_refused(self):
        inv = s.parse((self.work / 'original-inventory.json').read_bytes())
        statuses = s.parse((self.work / 'original-statuses.json').read_bytes())
        for row in statuses:
            if row['case_id'] == s.PARENT: row['status'] = 'failed'
        with self.assertRaises(ValueError): s.first_ready(inv, statuses)

    def test_parent_roster_omission_and_sha_refused(self):
        for action in ('remove', 'sha'):
            a = copy.deepcopy(self.a)
            if action == 'remove': del a['parent_files']['final_state.sqlite']
            else: a['parent_files']['final_state.sqlite']['sha256'] = 'f' * 64
            with self.assertRaises(ValueError): s.prepare(self.work, a)

    def test_fifo_reader_is_nonblocking_and_refuses(self):
        p = self.work / 'fifo'; os.mkfifo(p)
        with self.assertRaises(ValueError): s.read_pin(p, 'a' * 64)

    def test_symlink_reader_refuses(self):
        p = self.work / 'link'; p.symlink_to(self.work / 'original-runtime-limits.json')
        with self.assertRaises(ValueError): s.read_pin(p, s.RUNTIME)

    def test_ancestor_symlink_refuses(self):
        p = self.work / 'linked-parent'; p.symlink_to(self.work / 'selected-parent', target_is_directory=True)
        with self.assertRaises(ValueError): s.read_pin(p / 'manifest.json', s.PARENT_MANIFEST)

    def test_hardlink_refuses(self):
        p = self.work / 'original-runtime-limits.json'; os.link(p, self.work / 'alias')
        with self.assertRaises(ValueError): s.read_pin(p, s.RUNTIME)

    def test_duplicate_json_nonfinite_refused(self):
        for value in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.assertRaises(ValueError): s.parse(value)

    def test_handoff_missing_authority_or_lock_refused(self):
        for change in ({'status': 'PREPARE_ONLY'}, {'selected_case_unstarted': False},
                       {'selection_identity_sha256': 'c' * 64}, {'return_import_status': 'IMPORT_ACCEPTED'}):
            h = self.handoff(); h.update(change)
            with self.assertRaises(ValueError): s.validate_root_handoff(h, self.prepared)
        h = self.handoff(); h['pipeline_lock']['exclusive_held'] = False
        with self.assertRaises(ValueError): s.validate_root_handoff(h, self.prepared)

    def test_expired_future_handoff_and_deadline_refused(self):
        for delta in (-1000, 1000):
            h = self.handoff(); h['observed_utc'] = (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()
            with self.assertRaises(ValueError): s.validate_root_handoff(h, self.prepared)
        h = self.handoff(); h['deadline_utc'] = '2030-01-01T00:00:00Z'
        with self.assertRaises(ValueError): s.validate_root_handoff(h, self.prepared)

    def test_fresh_handoff_is_validation_not_os_lock_proof(self):
        self.assertIs(s.validate_root_handoff(self.handoff(), self.prepared)['selected_case_unstarted'], True)

    def test_other_pipeline_lock_cannot_substitute(self):
        h = self.handoff(); h['pipeline_lock']['path'] = '/synthetic-other/.pipeline.lock'
        with self.assertRaises(ValueError): s.validate_root_handoff(h, self.prepared)

    def test_actual_tiny_lock_held_preflight_and_wrong_owner_refused(self):
        import fcntl
        path = self.work / '.pipeline.lock'; path.write_bytes(b'')
        st = path.stat(); lock = {'path': str(path), 'dev':st.st_dev,'ino':st.st_ino,
                                 'owner_pid':os.getpid(),'owner_birth':'synthetic-owned-test'}
        with path.open('rb') as held, patch.object(s,'ORIGINAL_PIPELINE_LOCK_PATH_SHA256',s.digest(str(path).encode())):
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result = s.local_lock_preflight(lock,owner_matches=lambda *a:True,writers_absent=lambda *a:True)
            self.assertIs(result['authority_issued'],False)
            with self.assertRaises(ValueError):
                s.local_lock_preflight(lock,owner_matches=lambda *a:False,writers_absent=lambda *a:True)
            with self.assertRaises(ValueError):
                s.local_lock_preflight(lock,owner_matches=lambda *a:True,writers_absent=lambda *a:False)

    def test_actual_unoccupied_tiny_lock_refuses_without_persistent_lock(self):
        import fcntl
        path = self.work / '.pipeline.lock'; path.write_bytes(b'')
        st = path.stat(); lock = {'path':str(path),'dev':st.st_dev,'ino':st.st_ino,
                                 'owner_pid':os.getpid(),'owner_birth':'synthetic-owned-test'}
        with patch.object(s,'ORIGINAL_PIPELINE_LOCK_PATH_SHA256',s.digest(str(path).encode())):
            with self.assertRaises(ValueError):
                s.local_lock_preflight(lock,owner_matches=lambda *a:True,writers_absent=lambda *a:True)
        with path.open('rb') as free:
            fcntl.flock(free,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_truthful_provenance_only_metadata_amendment(self):
        original = self.prepared['original_limits']; frozen = copy.deepcopy(original)
        amended = s.truthful_limits(original, self.guest(), '1' * 40)
        self.assertEqual(original, frozen)
        self.assertEqual({k:v for k,v in amended.items() if k != 'provenance'},
                         {k:v for k,v in original.items() if k != 'provenance'})
        self.assertEqual(amended['provenance']['environment'], 'GCP')
        self.assertEqual(amended['provenance']['packaged_commit'], s.COMMIT)
        self.assertEqual(amended['max_retries'], 1)

    def test_missing_true_guest_refused(self):
        with self.assertRaises(ValueError): s.truthful_limits(self.prepared['original_limits'], {}, '1' * 40)

    def test_old_inspect_truthful_guest_actually_RETURN_IMPORT_BLOCKED(self):
        c = Config.from_dict(self.prepared['row']['config']); out = self.work / 'inspect'
        attempt = out / 'cases' / s.CID / 'attempt-000'; attempt.mkdir(parents=True)
        m = {'branch_origin': branch_identity(self.prepared['origin']), 'source_sha256': s.CORE,
             'pipeline_driver_sha256': s.DRIVER, 'config': json.loads(s.canonical(c.to_dict())), 'status': 'complete',
             'execution_provenance': s.truthful_limits(self.prepared['original_limits'], self.guest(), '1'*40)['provenance'],
             'config_sha256': s.CONFIG, 'scientific_case_id': s.CID}
        (attempt / 'manifest.json').write_text(json.dumps(m))
        scheduler = Scheduler.__new__(Scheduler); scheduler.root = out; scheduler.driver_hash = s.DRIVER
        scheduler.limits = RuntimeLimits.from_dict(self.prepared['original_limits'])
        with self.assertRaisesRegex(ValueError, 'cached execution provenance differs: environment'):
            scheduler._inspect(c, self.prepared['origin'])
        # A truthful expected-provenance adapter would still require full raw,
        # not just a new sidecar or a successful provenance comparison.
        scheduler.limits = RuntimeLimits.from_dict(s.truthful_limits(self.prepared['original_limits'], self.guest(), '1'*40))
        with patch('dams_sim.scheduler.verify_outputs', side_effect=ValueError('RAW_GATE_REQUIRED')):
            with self.assertRaisesRegex(ValueError, 'RAW_GATE_REQUIRED'): scheduler._inspect(c, self.prepared['origin'])

    def test_unissued_run_refuses_before_any_worker(self):
        with self.assertRaisesRegex(ValueError, 'handoff was not sealed'):
            s.run_selected(self.work, self.work, self.a, self.guest(), '1'*40, guard=lambda:None)

    def test_mocked_exact_original_singleton_scheduler_path(self):
        h = self.handoff(); raw = s.canonical(h); (self.work / 'selected-root-handoff.json').write_bytes(raw)
        a = copy.deepcopy(self.a); a['root_handoff_sha256'] = s.digest(raw)
        out = self.work / 'output'; out.mkdir()
        captured = {}
        class FakeScheduler:
            owned_workers_absent = True
            def __init__(self, root, limits, driver): captured.update(root=root, limits=limits, driver=driver)
            def run(self, configs, origins):
                captured.update(configs=configs, origins=origins); return [{'synthetic': True}]
        with patch.object(s, 'verify_parent_payload', return_value={}):
            result = s.run_selected(self.work, out, a, self.guest(), '1'*40,
                                    scheduler_factory=FakeScheduler, guard=lambda:None)
        self.assertEqual(len(captured['configs']), 1)
        self.assertEqual(captured['configs'][0].to_dict(), Config.from_dict(self.prepared['row']['config']).to_dict())
        self.assertEqual(captured['origins'], {s.CID:self.prepared['origin']})
        self.assertEqual(captured['driver'], s.DRIVER)
        self.assertEqual(result['original_scientific_case_count_increment'], 0)
        self.assertEqual(result['return_import_status'], 'RETURN_IMPORT_BLOCKED')

    def test_existing_output_case_refused_no_attempt_reset(self):
        h = self.handoff(); raw = s.canonical(h); (self.work / 'selected-root-handoff.json').write_bytes(raw)
        a = copy.deepcopy(self.a); a['root_handoff_sha256'] = s.digest(raw)
        out = self.work / 'output'; (out / 'cases').mkdir(parents=True)
        with patch.object(s, 'verify_parent_payload', return_value={}):
            with self.assertRaisesRegex(ValueError, 'reset original attempts'):
                s.run_selected(self.work, out, a, self.guest(), '1'*40, guard=lambda:None)

    def test_parent_payload_tiny_eof_positive_and_corrupt(self):
        tiny = self.work / 'tiny'; (tiny / 'selected-parent').mkdir(parents=True)
        value = b'opaque-test-only'; (tiny / 'selected-parent/a').write_bytes(value)
        a = {'parent_files': {'a':{'sha256':s.digest(value),'bytes':len(value)}}}
        self.assertEqual(set(s.verify_parent_payload(tiny, a, guard=lambda:None)), {'a'})
        a['parent_files']['a']['sha256'] = '0'*64
        with self.assertRaises(ValueError): s.verify_parent_payload(tiny, a, guard=lambda:None)

    def test_parent_payload_extra_leaf_refused(self):
        tiny = self.work / 'tiny'; (tiny / 'selected-parent').mkdir(parents=True)
        (tiny / 'selected-parent/extra').write_bytes(b'x')
        with self.assertRaises(ValueError): s.verify_parent_payload(tiny, {'parent_files':{}}, guard=lambda:None)

    def test_child_execution_missing_supervisor_pin_refused(self):
        with self.assertRaisesRegex(ValueError, 'leased execution pins missing'):
            s.main(['--work',str(self.work),'--assignment-sha256',s.digest((self.work/'selected-original-case.json').read_bytes()),'--execute'])


class ComputeCompatibility(unittest.TestCase):
    def runtime(self):
        limits = s.parse((FIXTURE / 'original-runtime-limits.json').read_bytes())
        inv = s.parse((FIXTURE / 'original-inventory.json').read_bytes())
        profile = {'schema': f.SCHEMA, 'source_sha256': s.CORE, 'driver_sha256': s.DRIVER,
                   'run_sh_sha256': s.ENTRY, 'spec_sha256': s.SPEC,
                   'inventory_sha256': s.digest(s.canonical(inv)),
                   'legacy_scientific_provenance': limits['provenance'], 'raw_bytes': limits['batch_max_output_bytes'],
                   'raw_files': 20000, 'min_free_bytes': limits['min_free_disk_bytes'],
                   'archive_codec_sha256': f.CODEC, 'archive_max_raw_bytes': limits['batch_max_output_bytes'],
                   'archive_max_files': 20000, 'lease_timeout_seconds': 120,
                   'global_deadline_utc': limits['deadline_utc'], 'cleanup_deadline_utc': limits['deadline_utc'],
                   'handoff_receipt_sha256': 'a'*64, 'operational_soft_stop_utc': '2026-10-13T23:49:00Z',
                   'operational_hard_stop_utc': '2026-10-13T23:54:00Z', 'original_runtime_limits_sha256': s.RUNTIME}
        return {'schema': cw.SCHEMA, 'source_commit': '1'*40, 'source_manifest_sha256': '2'*64,
                'spec': 'longitudinal-adoption-5y', 'scale': 1000, 'runtime_limits': limits,
                'purchase_mode': 'STANDARD', 'machine_type': 'synthetic-type', 'expected_guest': {'fixture':True},
                'expected_identity': {'instance_id':'1','zone':'synthetic','boot_disk_device_name':'boot',
                                      'guest_os_release_sha256':'3'*64,'boot_disk_bytes_min':1},
                'provider_identity_sha256':'4'*64,'deadline_utc':'2026-10-14T00:00:00Z',
                'watchdog_shutdown_utc':'2026-10-13T23:59:30Z','pipeline_stop_grace_seconds':300,
                'controller_lease_timeout_seconds':120,'transfer_config_sha256':'5'*64,
                'preservation_profile':profile,
                'original_selected_case': {'schema':s.SCHEMA,'assignment_sha256':'6'*64}}

    def test_selected_runtime_original_n1000_and3743_accepts(self):
        r = self.runtime(); self.assertIs(cw.validate_runtime(r), r)

    def test_wrong_scale_spec_or_cli_selection_refused(self):
        for key, value in [('scale',300), ('spec','longitudinal-adoption-10y')]:
            r = self.runtime(); r[key] = value
            with self.assertRaises(Exception): cw.validate_runtime(r)

    def test_runtime_override_cpu_cap_retry_deadline_refused(self):
        for key, value in [('cpu_budget',1), ('max_output_bytes',1000000), ('max_retries',0),
                           ('deadline_utc','2026-10-16T08:54:46.960873+00:00')]:
            r = self.runtime(); r['runtime_limits'][key] = value
            with self.assertRaises(Exception): cw.validate_runtime(r)

    def test_unknown_selected_keys_or_no_preservation_refused(self):
        r = self.runtime(); r['original_selected_case']['world_override'] = 99
        with self.assertRaises(Exception): cw.validate_runtime(r)
        r = self.runtime(); del r['preservation_profile']
        with self.assertRaises(Exception): cw.validate_runtime(r)

    def test_stock_wholepipeline_runtime_and_argv_kept(self):
        r = self.runtime(); del r['original_selected_case']
        self.assertIs(cw.validate_runtime(r), r)
        self.assertEqual(cw.pipeline_command('/source','/work','/work/output',r,'a'*64,{}),
                         ['/source/run.sh','--spec','longitudinal-adoption-5y','--scale','1000',
                          '--output','/work/output','--runtime-limits','/work/runtime-limits.json'])

    def test_selected_command_uses_existing_original_scheduler_api_not_new_flags(self):
        r = self.runtime(); command = cw.pipeline_command('/source','/work','/work/output',r,'a'*64,{})
        self.assertEqual(command[:4], [sys.executable,'-B','-m','research_tools.original_longitudinal_selector'])
        self.assertNotIn('--population', command)
        self.assertNotIn('--scale', command)


if __name__ == '__main__':
    unittest.main()
