"""Owned small runs verify disk reservations throttle, rather than abort, a batch."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dams_sim.config import Config
from dams_sim.pipeline import driver_hash
from dams_sim.runtime import RuntimeLimits
from dams_sim.scheduler import Scheduler


class DiskAdmissionTests(unittest.TestCase):
    def limits(self):
        return RuntimeLimits.from_dict({
            'max_workers': 2, 'cpu_budget': 2, 'memory_budget_bytes': 512_000_000,
            'max_output_bytes': 2_000_000, 'batch_max_output_bytes': 3_500_000,
            'max_events': 1000, 'max_retries': 0, 'world_timeout_seconds': 30,
            'checkpoint_interval_days': 2,
            'deadline_utc': (datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat(),
        })

    def configs(self):
        return [Config(n=12, days=3, world=w, max_output_mb=2,
                       max_rss_mb=128, max_events=1000) for w in (7101, 7102, 7103)]

    def test_occupied_disk_slots_wait_for_actual_owned_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = Scheduler(Path(directory), self.limits(), driver_hash())
            configs = self.configs()
            results = scheduler.run(configs)
            self.assertEqual(scheduler.peak_active, 1)
            self.assertEqual(len(results), 3)
            self.assertTrue(all((Path(directory)/r['attempt']/'final_state.json').is_file()
                                for r in results))
            # Full disk reservations serialize admission without changing worlds.
            self.assertEqual(scheduler.run(list(reversed(configs))), list(reversed(results)))

    def test_retained_outputs_with_no_active_worker_still_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = Scheduler(Path(directory), self.limits(), driver_hash())
            with patch('dams_sim.scheduler.disk_bytes', return_value=2_000_000), \
                 patch.object(scheduler.ctx, 'Process', side_effect=AssertionError('must not launch')) as launch:
                with self.assertRaisesRegex(RuntimeError, 'insufficient disk'):
                    scheduler.run(self.configs())
            self.assertEqual(launch.call_count, 0)


if __name__ == '__main__':
    unittest.main()
