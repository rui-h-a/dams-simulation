"""Actual tiny raw cases for interruption accounting, never a formal study."""
import dataclasses
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dams_sim.cli import run_world
from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.longitudinal_pipeline import driver_hash, execute_stage, stopped_case_census
from dams_sim.spec import case_key
from dams_sim.storage import digest, canonical, file_digest, source_hash


class StoppedCensusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.models = []
        constructor = Model.__init__
        def tracked(model, *args, **kwargs):
            constructor(model, *args, **kwargs)
            self.models.append(model)
        self.model_patch = patch.object(Model, '__init__', new=tracked)
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.root = Path(self.tmp.name).resolve()
        self.identity = {'schema_version': 3, 'source_sha256': source_hash(),
                         'pipeline_driver_sha256': driver_hash(), 'spec_sha256': 'b'*64}
        self.configs = [Config(n=12, guilds=2, days=3, world=i, max_wall_seconds=20, trace_every_days=1,
                              longitudinal=LongitudinalConfig(adoption_mode='never')).validate()
                        for i in range(5)]
        self.inventory = [{'case_id': case_key(c), 'config': c.to_dict(),
                           'tags': {'role': 'prehistory-prefix', 'parent_case_id': None}}
                          for c in self.configs]

    def tearDown(self):
        for model in self.models:
            model.ledger.close()
        self.tmp.cleanup()

    def attempt(self, index):
        path = self.root/'cases'/self.inventory[index]['case_id']/'attempt-000'
        path.mkdir(parents=True)
        return path

    def run_case(self, index, *, stop=None):
        path = self.attempt(index); c = self.configs[index]
        kwargs = {'checkpoint_interval_days': 1,
                  'metadata_extra': {'scientific_case_id': case_key(c),
                                     'pipeline_driver_sha256': self.identity['pipeline_driver_sha256']}}
        if stop is None:
            run_world(c, path, **kwargs)
        else:
            with self.assertRaises(InterruptedError):
                run_world(c, path, stop_requested=stop, **kwargs)
        return path

    def census(self):
        return stopped_case_census(self.root, self.inventory, self.identity)

    def test_real_raw_partition_counts_completed_not_acknowledged_in_memory(self):
        self.run_case(0); self.run_case(1)
        checks = iter((False, False, True))
        self.run_case(2, stop=lambda: next(checks))
        self.attempt(3)
        result = self.census()
        self.assertEqual([len(result['groups'][k]) for k in
                         ('complete', 'valid-latest-checkpoint', 'failed', 'unstarted')], [2, 1, 1, 1])
        self.assertEqual(result['remaining_case_ids'], sorted(r['case_id'] for r in self.inventory[2:]))
        self.assertFalse(result['full_study_gate'])
        self.assertEqual(next(r for r in result['records'] if r['case_id'] == self.inventory[2]['case_id'])['checkpoint_day'], 2)

    def test_corrupt_latest_is_failed_without_falling_back_to_previous(self):
        checks = iter((False, False, True)); p = self.run_case(0, stop=lambda: next(checks))
        index = json.loads((p/'checkpoint-index.json').read_text())
        self.assertEqual([r['day'] for r in index['snapshots']], [2, 1])
        latest = p/index['snapshots'][0]['file']; latest.write_bytes(latest.read_bytes()+b' ')
        result = self.census()
        self.assertEqual(result['groups']['failed'], [self.inventory[0]['case_id']])
        self.assertEqual(result['groups']['valid-latest-checkpoint'], [])

    def test_incomplete_daily_raw_cannot_be_promoted_by_rewritten_manifest_hash(self):
        p = self.run_case(0); csv = p/'timeseries.csv'
        rows = csv.read_text().splitlines(True); csv.write_text(''.join(rows[:-1]))
        manifest = json.loads((p/'manifest.json').read_text())
        manifest['output_sha256']['timeseries.csv'] = file_digest(csv)
        (p/'manifest.json').write_text(json.dumps(manifest))
        result = self.census()
        self.assertEqual(result['groups']['complete'], [])
        self.assertEqual(result['groups']['failed'], [self.inventory[0]['case_id']])

    def test_wrong_source_and_symlink_attempt_are_failed(self):
        p = self.run_case(0); m = json.loads((p/'manifest.json').read_text())
        m['source_sha256'] = '0'*64; (p/'manifest.json').write_text(json.dumps(m))
        case = self.root/'cases'/self.inventory[1]['case_id']; case.mkdir()
        (case/'attempt-000').symlink_to(p)
        result = self.census()
        self.assertEqual(result['groups']['failed'], sorted(r['case_id'] for r in self.inventory[:2]))
        self.assertEqual(result['groups']['complete'], [])

    def test_duplicate_inventory_or_wrong_identity_refuses(self):
        with self.assertRaisesRegex(ValueError, 'duplicates'):
            stopped_case_census(self.root, self.inventory+[self.inventory[0]], self.identity)
        with self.assertRaisesRegex(ValueError, 'source/driver'):
            stopped_case_census(self.root, self.inventory, self.identity|{'source_sha256': '0'*64})

    def assigned_child(self):
        parent = self.configs[0]
        child = dataclasses.replace(parent, days=5, regime='equal',
            longitudinal=dataclasses.replace(parent.longitudinal,
                adoption_mode='fixed', adoption_day=4)).validate()
        self.configs[1] = child
        self.inventory[1] = {'case_id': case_key(child), 'config': child.to_dict(),
                            'tags': {'role': 'strategy', 'parent_case_id': case_key(parent)}}
        return parent, child

    def test_from_scratch_checkpoint_is_not_the_assigned_parent_branch(self):
        self.assigned_child(); self.run_case(0)
        checks = iter((False, False, False, True))
        self.run_case(1, stop=lambda: next(checks))
        result = self.census()
        self.assertEqual(result['groups']['valid-latest-checkpoint'], [])
        self.assertIn(self.inventory[1]['case_id'], result['groups']['failed'])
        reason = next(r['reason'] for r in result['records'] if r['case_id'] == self.inventory[1]['case_id'])
        self.assertIn('assigned parent', reason)

    def test_genuine_fork_checkpoint_requires_both_origin_and_complete_parent(self):
        parent_config, child_config = self.assigned_child()
        parent_path = self.run_case(0); child_path = self.attempt(1)
        parent_manifest = json.loads((parent_path/'manifest.json').read_text())
        origin = {'parent_case_id': case_key(parent_config),
                  'parent_source_sha256': self.identity['source_sha256'],
                  'parent_config_sha256': parent_manifest['config_sha256'],
                  'parent_day': parent_config.days,
                  'parent_state_semantic_sha256': parent_manifest['final_state_descriptor']['state_semantic_sha256']}
        working = self.root/'parent-working'; working.mkdir()
        parent = Model.restore_checkpoint(parent_path/'final_state.json', storage_dir=working,
                                          expected_config=parent_config)
        child = None
        try:
            child = parent.fork(child_config, storage_dir=child_path)
            child.branch_origin.update(origin)
            checks = iter((False, True))
            with self.assertRaises(InterruptedError):
                run_world(child_config, child_path, restored=child,
                    checkpoint_interval_days=1, stop_requested=lambda: next(checks),
                    metadata_extra={'scientific_case_id': case_key(child_config),
                        'pipeline_driver_sha256': self.identity['pipeline_driver_sha256'],
                        'branch_origin': origin})
        finally:
            parent.ledger.close()
            if child is not None:
                child.ledger.close()
        self.assertEqual(self.census()['groups']['valid-latest-checkpoint'], [case_key(child_config)])
        marker = child_path/'manifest.json'; saved = marker.read_bytes()
        value = json.loads(saved); value['branch_origin']['parent_case_id'] = 'wrong-parent'
        marker.write_text(json.dumps(value))
        self.assertIn(case_key(child_config), self.census()['groups']['failed'])
        marker.write_bytes(saved)
        (parent_path/'manifest.json').unlink()
        result = self.census()
        self.assertEqual(result['groups']['valid-latest-checkpoint'], [])
        self.assertIn(case_key(child_config), result['groups']['failed'])

    def test_checkpoint_without_source_driver_manifest_is_not_promoted(self):
        checks = iter((False, False, True))
        path = self.run_case(0, stop=lambda: next(checks))
        (path/'manifest.json').unlink()
        result = self.census()
        self.assertEqual(result['groups']['valid-latest-checkpoint'], [])
        self.assertIn(self.inventory[0]['case_id'], result['groups']['failed'])

    def test_execute_stage_preserves_primary_failure_and_exact_partial_count(self):
        root = self.root; owner = self
        class Scheduler:
            owned_workers_absent = True
            limits = SimpleNamespace(provenance={})
            def run(self, configs, branch_origins=None):
                owner.run_case(0)
                raise InterruptedError('injected batch stop after a complete raw case')
        with patch('dams_sim.longitudinal_pipeline.stage_inventory', return_value=self.inventory):
            with self.assertRaisesRegex(InterruptedError, 'injected batch stop'):
                execute_stage(root, 'precision-pilot', None, None, [0], Scheduler(), self.identity)
        m = json.loads((root/'precision-pilot/manifest.json').read_text())
        self.assertEqual(m['completed_rows'], 1)
        self.assertTrue(m['partial_census_exact'])
        self.assertEqual(m['remaining_case_ids'], sorted(r['case_id'] for r in self.inventory[1:]))
        self.assertEqual(m['case_census_sha256'], file_digest(root/'precision-pilot/case_census.json'))
        self.assertFalse(json.loads((root/'precision-pilot/case_census.json').read_text())['full_study_gate'])

    def test_unverified_worker_absence_does_not_read_live_raw_or_report_zero(self):
        class Scheduler:
            owned_workers_absent = False
            limits = SimpleNamespace(provenance={})
            def run(self, configs, branch_origins=None):
                raise RuntimeError('owned reaping failed')
        with patch('dams_sim.longitudinal_pipeline.stage_inventory', return_value=self.inventory), \
             patch('dams_sim.longitudinal_pipeline.stopped_case_census', side_effect=AssertionError('no live raw')) as census:
            with self.assertRaisesRegex(RuntimeError, 'owned reaping failed'):
                execute_stage(self.root, 'precision-pilot', None, None, [0], Scheduler(), self.identity)
            census.assert_not_called()
        m = json.loads((self.root/'precision-pilot/manifest.json').read_text())
        self.assertIsNone(m['completed_rows'])
        self.assertFalse(m['partial_census_exact'])
        self.assertFalse((self.root/'precision-pilot/case_census.json').exists())


if __name__ == '__main__':
    unittest.main()
