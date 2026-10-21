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

    def test_resume_removes_superseded_working_databases(self):
        # Every resume used to restore into a new work-rK directory while earlier
        # working databases stayed on disk; at 10,000 members they filled the disk.
        d, _ = base_config(scale=12, context='startup', growth='organic', world=0, seed=3, pre_years=0, post_years=1)
        config = Config.from_dict(d)
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            root = Path(tmp); cpdir = root/'progress'
            m = Model(config, storage_dir=root/'work')
            _segments(m, 60, cpdir, config, root, every=30)  # last checkpoint at day 30
            m._long.ledger.close()
            for old in ('work-r0', 'work-r3', 'restored'):
                (root/old).mkdir()
                (root/old/'ledger.sqlite').write_bytes(b'superseded')
            r = _resume(cpdir, config, root)
            r._long.ledger.close()
            self.assertEqual(r._long.day, 30)
            left = sorted(p.name for p in root.iterdir() if p.name.startswith(('work', 'restored')))
            self.assertEqual(left, ['work-r1'])
            self.assertFalse((root/'work-r1'/'ledger.sqlite').exists())
            # Unknown/advanced work is not established equivalent merely because
            # a checkpoint exists; preserve it while restoring the checkpoint.
            self.assertEqual((root/'interrupted-work-r0-0'/'ledger.sqlite').read_bytes(), b'superseded')


if __name__ == '__main__':
    unittest.main()


class PrehistoryRerunTest(unittest.TestCase):
    def test_rerun_after_checkpoint_without_receipt(self):
        # An attempt interrupted after writing the branch checkpoint but before its
        # receipt left an immutable snapshot that made the rerun fail.
        from research_tools.r11_study import prehistory
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            args = {'out': str(Path(tmp)/'pre'), 'scale': 12, 'context': 'traditional', 'growth': 'organic',
                    'world': 0, 'seed': 3, 'pre_years': 1, 'post_years': 1}
            first = prehistory(args)
            (Path(tmp)/'pre'/'prehistory.json').unlink()
            (Path(tmp)/'pre'/'failure.json').write_text('{"kind":"prehistory"}')
            second = prehistory(args)
            self.assertEqual(second['checkpoint_sha256'], first['checkpoint_sha256'])
            self.assertFalse((Path(tmp)/'pre'/'failure.json').exists())
            self.assertEqual(len(list((Path(tmp)/'pre').glob('interrupted-branch-*'))), 1)
