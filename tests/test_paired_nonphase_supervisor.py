"""Bounded native filesystem counterexamples; no model/provider/auth execution."""
import ast, copy, importlib.util, json, os
from pathlib import Path
import sys, tempfile, types, unittest
from unittest.mock import patch
HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
ROOT = PACKAGE / 'source' if (PACKAGE / 'source' / 'research_tools' / 'compute_only_worker.py').is_file() else PACKAGE
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT), str(ROOT / 'research_tools')]
spec = importlib.util.spec_from_file_location('candidate_worker', ROOT / 'research_tools/compute_only_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

class Controls(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.work = Path(self.temp.name)
        self.out = self.work / 'output'
        self.out.mkdir()
        self.runtime = {'small_enterprise_profile': {'schema': 'DAMS-small-enterprise-cloud-confirmation-pair-1'}, 'runtime_limits': {'min_free_disk_bytes': 80 * 2 ** 30, 'batch_max_output_bytes': 8192, 'batch_max_output_files': 8, 'phase_max_output_bytes': 0, 'phase_spool_bytes': 0, 'phase_checkpoint_headroom_bytes': 0, 'phase_metadata_reserve_bytes': 0}}

    def tearDown(self):
        self.temp.cleanup()

    def file(self, name, size):
        p = self.out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'x' * size)
        return p

    def call(self, free=200 * 2 ** 30):
        fs = types.SimpleNamespace(f_bavail=free, f_frsize=1)
        with patch.object(worker.os, 'statvfs', return_value=fs):
            return worker.paired_nonphase_capacity(self.out, self.work, self.runtime)

    def test_whole_pair_count_includes_both_children_failure_and_hidden_metadata(self):
        self.file('startup-existing/cases/a/attempt-000/raw.db', 100)
        self.file('startup-founding/cases/b/attempt-000/FAILED.json', 100)
        self.file('startup-founding/cases/b/attempt-001/raw.db', 100)
        self.file('.tmp-complete-checkpoint', 100)
        self.file('pipeline.log', 100)
        result = self.call()
        self.assertEqual((result['logical_bytes'], result['files']), (500, 5))
        self.assertFalse(result['atomic_filesystem_quota'])
        self.assertFalse(result['science_complete'])

    def test_child_local_pass_but_whole_pair_bytes_fail(self):
        first = self.file('startup-existing/cases/a/raw.db', 5000)
        second = self.file('startup-founding/cases/b/raw.db', 5000)
        with self.assertRaisesRegex(worker.GuardError, 'whole paired cumulative output byte'):
            self.call()
        self.assertEqual((first.stat().st_size, second.stat().st_size), (5000, 5000))

    def test_whole_pair_file_count_fail_preserves_every_file(self):
        for i in range(9):
            self.file(('startup-existing/' if i < 5 else 'startup-founding/') + str(i), 1)
        with self.assertRaisesRegex(worker.GuardError, 'whole paired cumulative output file'):
            self.call()
        self.assertEqual(sum((p.is_file() for p in self.out.rglob('*'))), 9)

    def test_true80gib_floor_is_not_lowered_or_in_rawloop(self):
        with self.assertRaisesRegex(worker.GuardError, 'backing device free floor'):
            self.call(79 * 2 ** 30)
        self.assertEqual(self.call(80 * 2 ** 30)['minimum_physical_free_bytes'], 80 * 2 ** 30)

    def test_floor_rechecked_after_census(self):
        observations = [types.SimpleNamespace(f_bavail=n * 2 ** 30, f_frsize=1) for n in (200, 79)]
        with patch.object(worker.os, 'statvfs', side_effect=observations), self.assertRaisesRegex(worker.GuardError, 'backing device free floor'):
            worker.paired_nonphase_capacity(self.out, self.work, self.runtime)

    def test_lower_floor_refused_before_disk_call(self):
        self.runtime['runtime_limits']['min_free_disk_bytes'] = 79 * 2 ** 30
        with self.assertRaisesRegex(worker.GuardError, 'below original80'):
            self.call()

    def test_symlink_directory_refuses_and_target_preserved(self):
        target = self.work / 'outside'
        target.mkdir()
        (target / 'data').write_bytes(b'evidence')
        (self.out / 'link').symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(worker.GuardError, 'symlink directory'):
            self.call()
        self.assertEqual((target / 'data').read_bytes(), b'evidence')

    def test_hardlink_nonregular_leaf_refuses(self):
        p = self.file('original', 10)
        os.link(p, self.out / 'second')
        with self.assertRaisesRegex(worker.GuardError, 'nonregular leaf'):
            self.call()
        self.assertEqual(p.read_bytes(), b'x' * 10)

    def test_legacy_single_and_optional_phase_defaults_remain_disabled(self):
        for profile in ({}, {'schema': 'DAMS-small-enterprise-cloud-confirmation-arm-2'}):
            self.runtime['small_enterprise_profile'] = profile
            with patch.object(worker.os, 'statvfs', side_effect=AssertionError('legacy not touched')):
                self.assertIsNone(worker.paired_nonphase_capacity(self.out, self.work, self.runtime))
        self.runtime['small_enterprise_profile'] = {'schema': 'DAMS-small-enterprise-cloud-confirmation-pair-1'}
        self.runtime['runtime_limits']['phase_max_output_bytes'] = 48 * 2 ** 30
        with patch.object(worker.os, 'statvfs', side_effect=AssertionError('phase not changed')):
            self.assertIsNone(worker.paired_nonphase_capacity(self.out, self.work, self.runtime))
if __name__ == '__main__':
    unittest.main()
