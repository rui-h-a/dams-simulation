"""Small IO-only fixtures for original retention and source admission.

No simulated world, network request or cloud operation is run by these tests.
"""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dams_sim.storage import canonical, file_digest
from research_tools import r11_study
from research_tools.r11_retention import (
    DESIGN_INPUTS, EXECUTION_INPUTS, bind_plan, retention_report, retire_equivalent_work, science_manifest,
)


class PreservationTest(unittest.TestCase):
    def setUp(self):
        # Refuse fixture allocation if the filesystem is already under pressure.
        # This suite uses far less than 100 MiB and never fills space deliberately.
        self.base = Path(os.environ.get('TMPDIR', '/private/tmp')).resolve()
        if shutil.disk_usage(self.base).free < 2*1024**3:
            raise unittest.SkipTest('less than 2 GiB free; no fixture allocated')
        self.tmp = tempfile.TemporaryDirectory(prefix='r11-preservation-', dir=self.base)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        total = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
        self.assertLess(total, 100*1024**2)

    def source_fixture(self):
        (self.root/'dams_sim').mkdir()
        (self.root/'dams_sim/model.py').write_text('frozen = 1\n')
        for name in (*DESIGN_INPUTS, *EXECUTION_INPUTS):
            p = self.root/name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{}\n' if p.suffix == '.json' else 'frozen = 1\n')

    def test_core_design_target_and_template_changes_are_bound(self):
        self.source_fixture()
        before = science_manifest(self.root)
        for name in ('dams_sim/model.py', *DESIGN_INPUTS, *EXECUTION_INPUTS):
            p = self.root/name
            original = p.read_bytes()
            p.write_bytes(original+b' ')
            changed = science_manifest(self.root)
            self.assertNotEqual(changed, before, name)
            with self.assertRaisesRegex(ValueError, 'scientific dependencies changed'):
                plan = self.root/'plan.json'
                plan.write_bytes(canonical({'scientific_dependencies': before}))
                bind_plan(plan, {'scientific_dependencies': changed})
            p.write_bytes(original)
        self.assertEqual(science_manifest(self.root), before)

    def test_legacy_plan_is_not_rewritten_or_admitted(self):
        output = self.root/'output'
        output.mkdir()
        p = output/'plan.json'
        original = b'{"source_sha256":"old","audit_worlds":[]}\n'
        p.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'migration is not implemented'):
            bind_plan(p, {'scientific_dependencies': {}})
        self.assertEqual(p.read_bytes(), original)

    def test_same_bound_plan_is_read_only_and_different_plan_refused(self):
        self.source_fixture()
        plan = {'scientific_dependencies': science_manifest(self.root), 'worlds': [1]}
        output = self.root/'output'
        output.mkdir()
        p = output/'plan.json'
        bind_plan(p, plan)
        original, inode = p.read_bytes(), p.stat().st_ino
        bind_plan(p, plan)
        self.assertEqual(p.read_bytes(), original)
        self.assertEqual(p.stat().st_ino, inode)
        with self.assertRaisesRegex(ValueError, 'existing plan differs'):
            bind_plan(p, {**plan, 'worlds': [2]})
        self.assertEqual(p.read_bytes(), original)

    def test_missing_template_is_refused(self):
        self.source_fixture()
        (self.root/DESIGN_INPUTS[-1]).unlink()
        with self.assertRaisesRegex(ValueError, 'missing or symlink'):
            science_manifest(self.root)

    def test_no_plan_with_old_artifacts_cannot_be_relabelled(self):
        (self.root/'prehistory.json').write_text('{"seed":"different historical seed"}')
        with self.assertRaisesRegex(ValueError, 'empty output directory'):
            bind_plan(self.root/'plan.json', {'scientific_dependencies': {}})
        self.assertFalse((self.root/'plan.json').exists())

    def test_completed_compact_is_reported_missing_without_rerun(self):
        pre = self.root/'prehistory'
        pre.mkdir()
        (pre/'prehistory.json').write_text('{}')
        arm = self.root/'world-w1'/'control-hierarchy'
        arm.mkdir(parents=True)
        receipt = {'retention': 'compact', 'ledger_semantic_sha256': 'historical'}
        (arm/'arm.json').write_text(json.dumps(receipt))
        (arm/'final_state.compact.sqlite').write_bytes(b'not an original journal')
        before = (arm/'arm.json').read_bytes()
        with patch.object(r11_study.Config, 'from_dict', side_effect=AssertionError('must not simulate')):
            self.assertEqual(r11_study.arm({'prehistory': str(pre), 'out': str(arm)}), receipt)
        report = retention_report(self.root)
        self.assertEqual(report['missing_original_journal'], ['world-w1/control-hierarchy'])
        self.assertFalse(report['complete_preservation_accepted'])
        self.assertFalse(report['arms'][0]['rerun_requested'])
        self.assertEqual((arm/'arm.json').read_bytes(), before)

    def test_new_arm_keeps_original_journal_even_with_old_false_flag(self):
        pre = self.root/'prehistory'
        pre.mkdir()
        (pre/'prehistory.json').write_text('{"config":{},"branch_day":0}')
        out = self.root/'world-w1'/'control-hierarchy'
        out.mkdir(parents=True)
        db = out/'final_state.sqlite'
        fixture = self.root/'fixture-original.sqlite'
        with closing(sqlite3.connect(fixture)) as connection:
            connection.execute('CREATE TABLE journal_chunks(payload BLOB)')
            connection.execute('INSERT INTO journal_chunks VALUES(?)', (b'original-event',))
            connection.commit()
        original = file_digest(fixture)
        model = Mock()
        model.history = [{'day': 1}]
        model.summary.return_value = {}
        model.semantic_digest.return_value = 'state'
        model._long.ledger.semantic_digest.return_value = 'ledger'
        model._long.active = [1]
        model._long.agents = [1]
        model._long.closed_day = None
        def final(path):
            shutil.copyfile(fixture, db)
            path.write_text(json.dumps({'ledger': {'file': db.name, 'bytes': db.stat().st_size,
                                                  'sha256': file_digest(db)}}))
        model.write_final_state.side_effect = final
        with (patch.object(r11_study.Config, 'from_dict', return_value=SimpleNamespace(days=1)),
              patch.object(r11_study, 'arm_config', return_value={}),
              patch.object(r11_study, '_resume', return_value=model),
              patch.object(r11_study, '_segments'),
              patch.object(r11_study, '_validate', return_value=({}, str(db), {'fixture': 'pass'}))):
            receipt = r11_study.arm({'prehistory': str(pre), 'out': str(out),
                                     'arm': 'control-hierarchy', 'retain_journal': False})
        self.assertEqual(receipt['retention'], 'journal')
        self.assertEqual(file_digest(db), original)
        with closing(sqlite3.connect(f'file:{db}?mode=ro', uri=True)) as connection:
            self.assertEqual(connection.execute('SELECT payload FROM journal_chunks').fetchall(), [(b'original-event',)])
        with patch('dams_sim.longitudinal_storage.snapshot_semantics', return_value={'semantic_sha256': 'ledger'}):
            report = retention_report(self.root)
        self.assertEqual(report['missing_original_journal'], [])
        self.assertFalse(report['complete_preservation_accepted'])
        with patch('dams_sim.longitudinal_storage.snapshot_semantics', return_value={'semantic_sha256': 'pruned-with-hot-rows'}):
            self.assertEqual(retention_report(self.root)['missing_original_journal'], ['world-w1/control-hierarchy'])
        db.write_bytes(b'changed')
        self.assertEqual(retention_report(self.root)['missing_original_journal'], ['world-w1/control-hierarchy'])

    def test_bad_checkpoint_does_not_delete_potential_recovery_work(self):
        progress = self.root/'progress'
        progress.mkdir()
        (progress/'checkpoint-day-000030.json').write_text('{}')
        work = self.root/'work'
        work.mkdir()
        original = work/'ledger.sqlite'
        original.write_bytes(b'potential unique original')
        with patch('dams_sim.longitudinal_model.verify_snapshot', side_effect=ValueError('bad checkpoint')):
            with self.assertRaisesRegex(ValueError, 'bad checkpoint'):
                r11_study._resume(progress, Mock(), self.root)
        self.assertEqual(original.read_bytes(), b'potential unique original')

    def test_failed_restore_does_not_delete_work_after_checkpoint_verification(self):
        progress = self.root/'progress'
        progress.mkdir()
        (progress/'checkpoint-day-000030.json').write_text('{}')
        work = self.root/'work'
        work.mkdir()
        original = work/'ledger.sqlite'
        original.write_bytes(b'potential unique original')
        with (patch('dams_sim.longitudinal_model.verify_snapshot', return_value=({'ledger': {}}, None)),
              patch.object(r11_study.Model, 'restore_checkpoint', side_effect=OSError('fixture disk admission failure'))):
            with self.assertRaisesRegex(OSError, 'fixture disk admission failure'):
                r11_study._resume(progress, Mock(), self.root)
        self.assertEqual(original.read_bytes(), b'potential unique original')

    def test_only_proven_equivalent_work_is_retired_after_restore(self):
        keep = self.root/'work-r1'
        keep.mkdir()
        expected = {'schema_version': 2, 'semantic_sha256': 'checkpoint', 'row_counts': {'journal': 2}}
        for name in ('work', 'restored'):
            d = self.root/name
            d.mkdir()
            (d/'.longitudinal-working.sqlite').write_bytes(name.encode())
        def semantics(path):
            return expected if path.parent.name == 'work' else {**expected, 'semantic_sha256': 'newer'}
        with patch('dams_sim.longitudinal_storage.snapshot_semantics', side_effect=semantics):
            retire_equivalent_work(self.root, expected, keep=keep)
        self.assertFalse((self.root/'work').exists())
        self.assertTrue(keep.is_dir())
        self.assertEqual((self.root/'interrupted-restored-0'/'.longitudinal-working.sqlite').read_bytes(), b'restored')
        receipt = json.loads((self.root/'cache-retirement-0.json').read_text())
        self.assertTrue(receipt['restoration_completed_before_retirement'])

    def test_no_checkpoint_partial_is_preserved_under_new_name(self):
        work = self.root/'work'
        work.mkdir()
        (work/'ledger.sqlite').write_bytes(b'incomplete original')
        r11_study._retire_partial(self.root, ('work',))
        self.assertEqual((self.root/'interrupted-work-0'/'ledger.sqlite').read_bytes(), b'incomplete original')
        self.assertFalse(work.exists())


if __name__ == '__main__':
    unittest.main()
