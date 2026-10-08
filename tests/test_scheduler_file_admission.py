"""Tiny owned runs: whole-pipeline prospective file slots preserve case science."""
import dataclasses
from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dams_sim.config import Config
from dams_sim.pipeline import driver_hash
from dams_sim.runtime import RuntimeLimits
from dams_sim.scheduler import Scheduler,output_files
from dams_sim.storage import file_digest


class FileAdmissionTests(unittest.TestCase):
    def limits(self,**extra):
        return RuntimeLimits.from_dict({'max_workers':2,'cpu_budget':2,'memory_budget_bytes':1_000_000_000,
            'max_output_bytes':2_000_000,'batch_max_output_bytes':10_000_000,'max_events':1000,
            'max_retries':0,'world_timeout_seconds':30,'checkpoint_interval_days':2,
            'deadline_utc':(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()}|extra)

    def configs(self):
        return [Config(n=12,days=3,world=w,max_output_mb=2,max_rss_mb=256,max_events=1000) for w in (8101,8102,8103)]

    def test_file_reservations_wait_for_owned_attempt_and_preserve_exact_scientific_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bounded=root/'bounded';legacy=root/'legacy';bounded.mkdir();legacy.mkdir()
            scheduler=Scheduler(bounded,self.limits(batch_max_output_files=80,per_world_output_files=48),driver_hash())
            configs=self.configs();results=scheduler.run(configs)
            reference=Scheduler(legacy,self.limits(),driver_hash()).run(configs)
            self.assertEqual(scheduler.peak_active,1);self.assertEqual(len(results),3)
            for result,other in zip(results,reference):
                self.assertEqual(file_digest(bounded/result['attempt']/'final_state.json'),file_digest(legacy/other['attempt']/'final_state.json'))
            self.assertLessEqual(output_files(bounded),80)
            self.assertEqual(scheduler.run(list(reversed(configs))),list(reversed(results)))

    def test_scope_includes_prior_stage_hidden_and_temporary_files_before_any_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);prior=root/'precision-pilot';prior.mkdir()
            for index in range(4):(prior/f'.stable-temp-{index}').write_bytes(b'evidence')
            scheduler=Scheduler(root,self.limits(batch_max_output_files=50,per_world_output_files=48),driver_hash())
            with patch.object(scheduler.ctx,'Process',side_effect=AssertionError('must not launch')) as launch:
                with self.assertRaisesRegex(InterruptedError,'whole pipeline file slots'):scheduler.run(self.configs())
            launch.assert_not_called();self.assertEqual(len(list(prior.iterdir())),4)

    def test_exhaustion_retains_completed_case_and_exact_unlaunched_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);limits=self.limits(batch_max_output_files=55,per_world_output_files=48)
            scheduler=Scheduler(root,limits,driver_hash());configs=self.configs()
            with self.assertRaisesRegex(InterruptedError,'whole pipeline file slots'):scheduler.run(configs)
            statuses=list((root/'cases').rglob('manifest.json'))
            self.assertEqual(len(statuses),1)
            import json
            self.assertEqual(json.loads(statuses[0].read_text())['status'],'complete')
            self.assertLessEqual(output_files(root),55)

    def test_legacy_disabled_and_strict_matching_file_fields(self):
        self.assertEqual(self.limits().batch_max_output_files,0)
        for change in ({'batch_max_output_files':True},{'per_world_output_files':1},
                       {'batch_max_output_files':100,'per_world_output_files':0},
                       {'batch_max_output_files':47,'per_world_output_files':48},
                       {'batch_max_output_files':100,'per_world_output_files':48.0}):
            with self.assertRaises(ValueError):self.limits(**change)


if __name__=='__main__':unittest.main()
