"""Small argument/file-boundary tests only: no Model, native CP or DB fixture."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]/'research_tools/r12_joint_study.py'
SPEC = importlib.util.spec_from_file_location('r12_joint_study_pure', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
BEFORE = set(sys.modules)
SPEC.loader.exec_module(MODULE)
LOADED = set(sys.modules)-BEFORE


class AdapterBoundaryTest(unittest.TestCase):
    def test_import_does_not_load_engine_or_sqlite(self):
        self.assertFalse(any(n.startswith('dams_sim') or n == 'sqlite3' for n in LOADED))

    def test_roster_has_unique_world_and_unique_complete_parent(self):
        value = {'schema': MODULE.ROSTER_SCHEMA, 'worlds': [
            {'world': 11, 'parent_checkpoint': '/data/eleven/common.json'}]}
        detached = MODULE.validate_roster(value)
        detached[0]['world'] = 12
        self.assertEqual(value['worlds'][0]['world'], 11)
        for extra in ({'world': 11, 'parent_checkpoint': '/other/common.json'},
                      {'world': 12, 'parent_checkpoint': '/data/eleven/common.json'}):
            with self.assertRaises(ValueError):
                MODULE.validate_roster({**value, 'worlds': value['worlds']+[extra]})

    def test_roster_refuses_implicit_worlds_paths_and_capsule_fields(self):
        for world, path in ((True, '/raw/cp.json'), (0, '/raw/cp.json'),
                            (1, 'relative.json'), (1, '/raw/../cp.json')):
            with self.subTest(world=world, path=path), self.assertRaises(ValueError):
                MODULE.validate_roster({'schema': MODULE.ROSTER_SCHEMA,
                    'worlds': [{'world': world, 'parent_checkpoint': path}]})
        with self.assertRaises(ValueError):MODULE.validate_roster({'worlds': []})
        with self.assertRaises(ValueError):MODULE.validate_roster({'schema': MODULE.ROSTER_SCHEMA,
            'worlds': [{'world': 1, 'parent_checkpoint': '/raw/cp.json', 'target_capsule': {}}]})

    def test_early_header_cannot_promote_r11_or_owned_target(self):
        base = {'schema': 'dams-two-firm-joint-checkpoint-1', 'status': 'unoffered', 'offer': None,
            'owners': {'buyer': 'buyer', 'target': 'seller'}, 'day': 3,
            'terms': {'global_world': 11, 'offer_day': 3},
            'firms': {'buyer': {'config': {}}, 'target': {'config': {}}}}
        MODULE.shared_header(base, 11)  # structure only, never raw admission
        cases = [dict(base, schema='r11-arm'), dict(base, status='integrated'),
                 dict(base, day=4), dict(base, owners={'buyer': 'buyer', 'target': 'buyer'})]
        capsule = copy.deepcopy(base);capsule['firms']['target']['config']['enterprise_acquisition'] = {}
        cases.append(capsule)
        for value in cases:
            with self.assertRaises(ValueError):MODULE.shared_header(value, 11)
        with self.assertRaises(ValueError):MODULE.shared_header(base, 12)

    def test_duplicate_keys_and_nonfinite_refuse(self):
        for raw in (b'{"world":1,"world":2}', b'{"value":NaN}'):
            with self.assertRaises(ValueError):MODULE.strict_json(raw)

    def test_tiny_file_publication_preserves_existing_and_pending_evidence(self):
        parent = Path(os.environ['DAMS_R12_ADAPTER_TEST_TMP']).resolve(strict=True)
        info = os.statvfs(parent)
        if info.f_bavail*info.f_frsize < 2*1024**3:self.skipTest('2GiB free floor')
        with tempfile.TemporaryDirectory(prefix='pure-argument-', dir=parent) as temporary:
            root = Path(temporary);path = root/'plan.json'
            MODULE.write_new(path, {'first': 1});original = path.read_bytes()
            with self.assertRaises(ValueError):MODULE.write_new(path, {'second': 2})
            self.assertEqual(path.read_bytes(), original)
            pending = root/'other.json.pending';pending.write_bytes(b'interrupted')
            with self.assertRaises(FileExistsError):MODULE.write_new(root/'other.json', {})
            self.assertEqual(pending.read_bytes(), b'interrupted')
            alias = root/'symlink.json';alias.symlink_to(path)
            with self.assertRaises(ValueError):MODULE.regular(alias)
            self.assertLess(sum(p.lstat().st_size for p in root.iterdir()), 1024)

    def test_wrong_external_plan_hash_refuses_before_any_core_or_output(self):
        parent = Path(os.environ['DAMS_R12_ADAPTER_TEST_TMP']).resolve(strict=True)
        info = os.statvfs(parent)
        if info.f_bavail*info.f_frsize < 2*1024**3:self.skipTest('2GiB free floor')
        with tempfile.TemporaryDirectory(prefix='pure-plan-', dir=parent) as temporary:
            root = Path(temporary);path = root/'plan.json'
            path.write_bytes(b'{}\n');original = path.read_bytes()
            with patch.object(MODULE, 'source_manifest', side_effect=AssertionError('core source path reached')):
                with self.assertRaisesRegex(ValueError, 'externally fixed'):
                    MODULE.stage(root, '0'*64, 11, 'centralize')
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['plan.json'])
            self.assertNotEqual(hashlib.sha256(original).hexdigest(), '0'*64)

    def test_dotdot_output_cannot_bypass_parent_disjoint_before_any_input(self):
        parent = Path(os.environ['DAMS_R12_ADAPTER_TEST_TMP']).resolve(strict=True)
        info = os.statvfs(parent)
        if info.f_bavail*info.f_frsize < 2*1024**3:self.skipTest('2GiB free floor')
        with tempfile.TemporaryDirectory(prefix='pure-dotdot-', dir=parent) as temporary:
            root = Path(temporary);(root/'raw/parent').mkdir(parents=True);(root/'raw/other').mkdir()
            original = root/'raw/parent/common.json';original.write_bytes(b'{}\n')
            output = root/'raw/other/../parent/new'
            self.assertFalse(output.is_relative_to(original.parent))
            self.assertTrue(output.resolve().is_relative_to(original.parent))
            with patch.object(MODULE, 'read_json', side_effect=AssertionError('input read reached')) as read, \
                 patch.object(MODULE, 'source_manifest', side_effect=AssertionError('source import reached')) as source, \
                 patch.object(MODULE, '_parent_binding', side_effect=AssertionError('native CP reached')) as native:
                with self.assertRaisesRegex(ValueError, 'path traversal'):
                    MODULE.prepare(root/'not-read-roster.json', output)
                read.assert_not_called();source.assert_not_called();native.assert_not_called()
            self.assertEqual(original.read_bytes(), b'{}\n')
            self.assertFalse(output.exists())

    def test_fixed_plan_hash_cannot_authorize_stage_inside_original_parent(self):
        parent = Path(os.environ['DAMS_R12_ADAPTER_TEST_TMP']).resolve(strict=True)
        info = os.statvfs(parent)
        if info.f_bavail*info.f_frsize < 2*1024**3:self.skipTest('2GiB free floor')
        with tempfile.TemporaryDirectory(prefix='pure-fixed-parent-', dir=parent) as temporary:
            study = Path(temporary)/'raw/parent';study.mkdir(parents=True)
            original = study/'common.json';original.write_bytes(b'{}\n')
            plan = {'schema': MODULE.PLAN_SCHEMA, 'study_directory': str(study),
                'assigned_worlds': [11], 'arms': list(MODULE.ARMS), 'engineering_only': True,
                'raw_admitted': False, 'formal_worlds_added': 0, 'inference_authorized': False,
                'source_manifest': {}, 'worlds': [{'world': 11, 'parent_checkpoint': str(original)}]}
            raw = MODULE.canonical(plan)+b'\n';(study/'plan.json').write_bytes(raw)
            fixed = hashlib.sha256(raw).hexdigest()  # real full SHA, not the wrong-hash negative
            with patch.object(MODULE, 'source_manifest', side_effect=AssertionError('source reached')) as source, \
                 patch.object(MODULE, '_parent_binding', side_effect=AssertionError('native CP reached')) as native, \
                 patch.object(MODULE.os, 'open', side_effect=AssertionError('lock/output reached')) as opened:
                with self.assertRaisesRegex(ValueError, 'disjoint'):
                    MODULE.stage(study, fixed, 11, 'centralize')
                source.assert_not_called();native.assert_not_called();opened.assert_not_called()
            self.assertEqual(original.read_bytes(), b'{}\n')
            self.assertEqual(sorted(p.name for p in study.iterdir()), ['common.json', 'plan.json'])

    def test_dotdot_study_refused_before_plan_read(self):
        with patch.object(MODULE, 'read_json', side_effect=AssertionError('plan read reached')) as read:
            with self.assertRaisesRegex(ValueError, 'path traversal'):
                MODULE.admitted_plan('/raw/other/../parent', '0'*64, 11, 'centralize')
            read.assert_not_called()

    def test_changed_parent_between_header_and_native_binding_refuses(self):
        old = {'schema': 'dams-two-firm-joint-checkpoint-1', 'status': 'unoffered', 'offer': None,
            'owners': {'buyer': 'buyer', 'target': 'seller'}, 'day': 3,
            'terms': {'global_world': 11, 'offer_day': 3, 'consideration': 10.},
            'firms': {'buyer': {'config': {}}, 'target': {'config': {}}}}
        newer = copy.deepcopy(old);newer['terms']['consideration'] = 20.
        old_sha = hashlib.sha256(MODULE.canonical(old)).hexdigest()
        new_sha = hashlib.sha256(MODULE.canonical(newer)).hexdigest()
        fake = types.ModuleType('dams_sim.joint_counterfactual')
        payload = {'parent_files': {'common.json': {'bytes': len(MODULE.canonical(newer)), 'sha256': new_sha}}}
        fake.CounterfactualPlan = types.SimpleNamespace(from_checkpoint=lambda p: types.SimpleNamespace(payload=payload))
        package = types.ModuleType('dams_sim');package.__path__ = []
        with patch.dict(sys.modules, {'dams_sim': package, 'dams_sim.joint_counterfactual': fake}), \
             patch.object(MODULE, 'read_json', return_value=(old, old_sha)), \
             patch.object(MODULE, 'observed', side_effect=AssertionError('later group reads reached')) as observed:
            with self.assertRaisesRegex(ValueError, 'header changed'):
                MODULE._parent_binding('/raw/common.json', 11)
            observed.assert_not_called()


if __name__ == '__main__':unittest.main()
