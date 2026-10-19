import os
import tempfile
import unittest
from pathlib import Path

from dams_sim.config import Config
from dams_sim.model import Model
from research_tools.r11_design import base_config
from research_tools.r11_study import _resume, _segments


class InterruptedCheckpointTest(unittest.TestCase):
    def test_resume_moves_aside_a_hidden_partial_snapshot(self):
        d, _ = base_config(scale=12, context='startup', growth='organic', world=0, seed=3, pre_years=0, post_years=1)
        config = Config.from_dict(d)
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            root = Path(tmp); cpdir = root/'progress'
            m = Model(config, storage_dir=root/'work')
            _segments(m, 60, cpdir, config, root, every=30)  # checkpoint at day 30
            m._long.ledger.close()
            # An attempt interrupted while writing the day-60 snapshot leaves its
            # hidden temporary file; the resumed attempt must not fail on it.
            (cpdir/'.checkpoint-day-000060.sqlite.tmp').write_bytes(b'partial')
            r = _resume(cpdir, config, root)
            self.assertEqual(r._long.day, 30)
            _segments(r, 90, cpdir, config, root, every=30)
            r._long.ledger.close()
            self.assertTrue((cpdir/'checkpoint-day-000060.json').exists())
            aside = list(cpdir.glob('interrupted-000060-*/.checkpoint-day-000060.sqlite.tmp'))
            self.assertEqual(len(aside), 1)
            self.assertEqual(aside[0].read_bytes(), b'partial')


if __name__ == '__main__':
    unittest.main()
