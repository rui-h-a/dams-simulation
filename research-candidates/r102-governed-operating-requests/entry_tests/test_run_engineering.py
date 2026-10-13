"""Metadata/filesystem guard controls only. Never imports or invokes a Model."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BASE = Path(__file__).absolute().parents[1]
spec = importlib.util.spec_from_file_location('r102_local_entry_controls', BASE / 'run_engineering.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
FREE = SimpleNamespace(f_bavail=100 * 1024**3, f_frsize=1)


class EntryControls(unittest.TestCase):
    def setUp(self):
        # Own temporary fixture beneath this test directory; no original raw.
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.base = self.root / 'candidate'
        (self.base / 'source/dams_sim').mkdir(parents=True)
        for p in (BASE / 'source/dams_sim').glob('*.py'):
            shutil.copyfile(p, self.base / 'source/dams_sim' / p.name)
        (self.base / 'profiles').mkdir()
        shutil.copyfile(BASE / 'profiles/engineering-pair.json', self.base / 'profiles/engineering-pair.json')
        shutil.copyfile(BASE / 'run_engineering.py', self.base / 'run_engineering.py')
        files = {str(p.relative_to(self.base)): {'bytes': p.stat().st_size,
                    'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in self.base.rglob('*') if p.is_file()}
        (self.base / 'publication-manifest.json').write_text(json.dumps({'source_sha256': r.SOURCE_SHA, 'files': files}))
        self.out = self.root / 'out'
        self.free = patch.object(r.os, 'statvfs', return_value=FREE)
        self.free.start()
        self.addCleanup(self.free.stop)
        self.addCleanup(self.temp.cleanup)

    def guard(self):
        profile, pins, digest = r.load_inputs(self.base)
        out = r.fresh_output(self.out, self.base)
        return r.Guard(out, self.base, pins)

    def test_readonly_actual_packet_and_fixed_profile(self):
        before = set(sys.modules)
        profile, pins, digest = r.load_inputs(BASE)
        self.assertEqual([a['name'] for a in profile['arms']], ['hierarchy', 'dams'])
        self.assertEqual(sum(a[k]['n'] for a in profile['arms'] for k in ('buyer_config', 'target_config')), 8)
        self.assertIn(BASE / 'run_engineering.py', pins)
        self.assertFalse(any(n.startswith('dams_sim') for n in set(sys.modules) - before))

    def test_rss_native_units_and_unknown_refused(self):
        self.assertEqual(r.peak_rss_bytes('darwin', 123), 123)
        self.assertEqual(r.peak_rss_bytes('linux', 123), 123 * 1024)
        with self.assertRaisesRegex(r.Refused, 'RSS units'):
            r.peak_rss_bytes('freebsd', 123)

    def test_existing_output_rejected_and_retained(self):
        self.out.mkdir()
        marker = self.out / 'prior.raw'
        marker.write_bytes(b'original')
        with self.assertRaisesRegex(r.Refused, 'fresh output'):
            r.fresh_output(self.out, self.base)
        self.assertEqual(marker.read_bytes(), b'original')

    def test_source_or_ancestor_output_rejected(self):
        with self.assertRaisesRegex(r.Refused, 'separate'):
            r.fresh_output(self.base / 'new-output', self.base)
        with self.assertRaisesRegex(r.Refused, 'separate'):
            r.fresh_output(self.root, self.base)

    def test_symlink_ancestor_and_parent_traversal_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(r.Refused, 'ancestor alias'):
            r.fresh_output(alias / 'new-output', self.base)
        with self.assertRaisesRegex(r.Refused, 'parent traversal'):
            r.fresh_output(self.root / 'candidate/../out', self.base)
        self.assertFalse((self.base / 'new-output').exists())

    def test_source_sha_and_profile_pin(self):
        source = self.base / 'source/dams_sim/model.py'
        source.write_bytes(source.read_bytes() + b'\n# altered\n')
        with self.assertRaisesRegex(r.Refused, 'SHA/size'):
            r.load_inputs(self.base)

    def test_unlisted_source_and_hardlink_refused(self):
        extra = self.base / 'source/foreign.raw'
        extra.write_bytes(b'foreign')
        with self.assertRaisesRegex(r.Refused, 'source roster'):
            r.load_inputs(self.base)
        extra.unlink()
        (self.root / 'second-name').hardlink_to(self.base / 'source/dams_sim/model.py')
        with self.assertRaisesRegex(r.Refused, 'single-link'):
            r.load_inputs(self.base)

    def test_native_mutation_firstfault_retained(self):
        guard = self.guard()
        p = self.base / 'source/dams_sim/model.py'
        p.write_bytes(p.read_bytes() + b'\n')
        with self.assertRaisesRegex(r.Refused, 'native identity'):
            guard.check()
        first = dict(guard.first_fault)
        guard.deadline = 0
        with self.assertRaisesRegex(r.Refused, 'lifetime'):
            guard.check()
        self.assertEqual(guard.first_fault, first)

    def test_sparse_whole_namespace_cap(self):
        guard = self.guard()
        with (self.out / 'sparse.raw').open('wb') as stream:
            stream.truncate(r.OUTPUT_CAP + 1)
        with self.assertRaisesRegex(r.Refused, 'whole-output capacity'):
            guard.check()

    def test_free_watermark_and_rss_boundary(self):
        guard = self.guard()
        with patch.object(r.os, 'statvfs', return_value=SimpleNamespace(f_bavail=0, f_frsize=1)):
            with self.assertRaisesRegex(r.Refused, 'free-space'):
                guard.check()
        with patch.object(r, 'peak_rss_bytes', return_value=r.RSS_CAP + 1):
            with self.assertRaisesRegex(r.Refused, 'RSS bound'):
                guard.check()

    def test_lifetime_and_output_special_or_alias(self):
        guard = self.guard()
        with patch.object(r.time, 'monotonic', return_value=guard.deadline):
            with self.assertRaisesRegex(r.Refused, 'lifetime'):
                guard.check()
        (self.out / 'alias.raw').symlink_to(self.base / 'run_engineering.py')
        with self.assertRaisesRegex(r.Refused, 'tree alias'):
            guard.check()
        (self.out / 'alias.raw').unlink()
        r.os.mkfifo(self.out / 'pipe')
        with self.assertRaisesRegex(r.Refused, 'special'):
            guard.check()

    def test_postreceipt_guard_refuses_acceptance(self):
        guard = self.guard()
        original = r.save
        def write_then_revoke(path, value):
            original(path, value)
            guard.deadline = 0
        pending = self.out / 'engineering-receipt.pending.json'
        with patch.object(r, 'save', side_effect=write_then_revoke):
            with self.assertRaisesRegex(r.Refused, 'lifetime'):
                r.guarded_save(guard, pending, {'scope': 'metadata counterexample only'})
        self.assertTrue(pending.is_file())
        self.assertFalse((self.out / 'engineering-receipt.json').exists())

    def test_inherited_storage_refused_and_transfer_removed(self):
        with patch.dict(r.os.environ, {'DAMS_PHASE_STORAGE_CONFIG': 'foreign.json'}):
            with self.assertRaisesRegex(r.Refused, 'phase storage'):
                r.environment_preflight()
        with patch.dict(r.os.environ, {'DAMS_TRANSFER_CONFIG': 'foreign.json',
                                     'DAMS_PACKAGED_COMMIT': 'foreign',
                                     'DAMS_SOURCE_MANIFEST': 'foreign.json'}, clear=True):
            r.environment_preflight()
            self.assertNotIn('DAMS_TRANSFER_CONFIG', r.os.environ)
            self.assertNotIn('DAMS_PACKAGED_COMMIT', r.os.environ)
            self.assertNotIn('DAMS_SOURCE_MANIFEST', r.os.environ)

    def test_full_firstcause_and_oversized_diagnostic_refused(self):
        try:
            try:
                raise r.Refused('original first cause ' + 'a' * 3000)
            except r.Refused as cause:
                raise ValueError('outer wrapper') from cause
        except ValueError as error:
            document = r.failure_document(error, {'message': str(error.__cause__)})
            self.assertIn('a' * 3000, document['full_error_chain'])
            self.assertEqual(document['message'], 'outer wrapper')
            self.assertIn('original first cause', document['first_guard_fault']['message'])
        with self.assertRaisesRegex(r.Refused, '64 KiB'):
            r.failure_document(ValueError('b' * 70000), None)

    def test_api_wiring_and_only_delayed_model_imports(self):
        tree = ast.parse((BASE / 'run_engineering.py').read_text())
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any(getattr(n, 'module', '') and n.module.startswith('dams_sim') for n in top))
        code = (BASE / 'run_engineering.py').read_text()
        self.assertIn('stop_requested=guard.stop_requested, preservation_guard=guard.check', code)
        self.assertIn('JointRun.restore', code)
        self.assertNotIn('lambda: True', code)
        self.assertEqual(r.LIFETIME, 1800)
        self.assertEqual(r.OUTPUT_CAP, 2 * 1024**3)
        self.assertEqual(r.MIN_FREE, 80 * 1024**3)


if __name__ == '__main__':
    unittest.main()
