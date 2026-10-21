"""Offline, bounded archive fixtures. No GCP, network or simulated worlds."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

from dams_sim.storage import file_digest
from research_tools import r11_archive as archive
from research_tools.r11_retention import DESIGN_INPUTS, EXECUTION_INPUTS, science_manifest


ABSENT = {'owned_pipeline_group_id': 123456, 'owned_pipeline_group_absent': True,
          'absence_checked_utc': '2026-10-21T00:00:00Z', 'check': 'killpg(group_id, 0) returned ESRCH'}
FIXTURE_DEADLINE = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()


class Response(io.BytesIO):
    def __init__(self, raw, length=None):
        super().__init__(raw)
        self.headers = {'Content-Length': str(len(raw) if length is None else length)}


class MemoryStore:
    """Fake transport only; generation semantics are tested separately below."""
    def __init__(self):
        self.data, self.bindings = {}, {}

    def put_file(self, key, path, expected_sha, immutable=True):
        raw = Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise AssertionError('fixture local checksum differs')
        if immutable and key in self.data and self.data[key] != raw:
            raise AssertionError('fixture immutable overwrite')
        self.data[key] = raw
        self.verify_generation(key, expected_sha, len(raw))

    def put_json(self, key, value, immutable=True):
        raw = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
        if immutable and key in self.data and self.data[key] != raw:
            raise AssertionError('fixture immutable metadata overwrite')
        self.data[key] = raw
        self.verify_generation(key, hashlib.sha256(raw).hexdigest(), len(raw))

    def get_json(self, key):
        return json.loads(self.data[key])

    def check_deadline(self):
        return None

    def read_generation(self, binding, destination=None, *, capture=False):
        raw = self.data[binding['object'].removeprefix('fixture/')]
        if len(raw) != binding['bytes'] or hashlib.sha256(raw).hexdigest() != binding['sha256']:
            raise AssertionError('fixture fixed-generation read differs')
        return raw if capture else None

    def verify_generation(self, key, sha, length):
        raw = self.data[key]
        if len(raw) != length or hashlib.sha256(raw).hexdigest() != sha:
            raise AssertionError('fixture remote checksum differs')
        binding = {'bucket': 'fixture-bucket', 'object': 'fixture/'+key,
                   'generation': '1001', 'sha256': sha, 'bytes': length}
        self.bindings[key] = binding
        return binding


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get('TMPDIR', '/private/tmp')).resolve()
        if shutil.disk_usage(base).free < 2*1024**3:
            raise unittest.SkipTest('less than 2 GiB free; no archive fixture allocated')
        self.tmp = tempfile.TemporaryDirectory(prefix='r11-archive-', dir=base)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        size = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
        self.assertLess(size, 100*1024**2)

    def test_open_source_is_refused_before_upload(self):
        with (patch.object(archive, 'wait_pipeline_group_absent', return_value=ABSENT),
              patch.object(archive.shutil, 'which', return_value='/fixture/lsof'),
              patch.object(archive.subprocess, 'run', return_value=type('R', (), {'returncode': 0, 'stdout': b'42', 'stderr': b''})())):
            with self.assertRaisesRegex(archive.GuardError, 'closure'):
                archive.require_quiet(self.root, 123456, FIXTURE_DEADLINE)

    def test_real_open_sqlite_is_refused_then_closed_source_passes(self):
        if shutil.which('lsof') is None:
            self.skipTest('lsof is unavailable; deployment must refuse')
        database = sqlite3.connect(self.root/'active.sqlite')
        try:
            database.execute('CREATE TABLE original(event)'); database.commit()
            with patch.object(archive, 'wait_pipeline_group_absent', return_value=ABSENT):
                with self.assertRaises(archive.GuardError):
                    archive.require_quiet(self.root, 123456, FIXTURE_DEADLINE)
        finally:
            database.close()
        with patch.object(archive, 'wait_pipeline_group_absent', return_value=ABSENT):
            archive.require_quiet(self.root, 123456, FIXTURE_DEADLINE)

    def test_missing_prior_accounting_does_not_create_zero_counters(self):
        missing = self.root/'missing-state'
        config = {'state_directory': str(missing), 'max_requests': 10,
                  'max_download_bytes': 1024, 'max_storage_bytes': 1024}
        with self.assertRaises(archive.GuardError):
            archive.admitted_state(config)
        self.assertFalse(missing.exists())

    def test_cloud_preflight_refuses_different_state_before_runner(self):
        # Execute only the actual first embedded Python block, never bash/startup.
        script = (archive.ROOT/'cloud/r11-startup.sh').read_text()
        block = script.split("<<'PY'", 1)[1].split('\n', 1)[1].split('\nPY\n', 1)[0]
        compiled = compile(block, 'r11-startup-archive-preflight', 'exec')
        with (patch.object(sys, 'argv', ['preflight', '/fixture/admission']),
              patch.object(archive, 'config_file', return_value={'fixture': 'admission'}) as config,
              patch.object(archive, 'admitted_state', return_value=Path('/other/state')) as state):
            with self.assertRaisesRegex(SystemExit, 'fixed persistent transport'):
                exec(compiled, {})
            config.assert_called_once_with(Path('/fixture/admission'))
            state.assert_called_once_with({'fixture': 'admission'})
            state.return_value = Path('/srv/r11-archive/transport')
            exec(compiled, {})

    def test_missing_ledgers_after_preflight_are_refused_inside_existing_locks(self):
        state = self.root/'state'; state.mkdir(mode=0o700)
        records = {'requests.json': {'requests_upper': 3}, 'downloads.json': {'bytes_upper': 4},
                   'upload-state.json': {'uploaded': {}, 'bytes': 0}}
        for name, record in records.items():
            p = state/name; p.write_text(json.dumps(record)); p.chmod(0o600)
        config = {'state_directory': str(state), 'max_requests': 10,
                  'max_download_bytes': 1024, 'max_storage_bytes': 1024**2}
        archive.admitted_state(config)
        store = archive.GenerationStore('fixture-bucket', 'fixture', request_state=state/'requests.json',
                                        max_requests=10, transfer_state=state/'downloads.json',
                                        max_transfer_bytes=1024, require_existing_state=True)
        (state/'requests.json').unlink()
        with self.assertRaises(FileNotFoundError):
            store.charge_request()
        self.assertFalse((state/'requests.json').exists())
        (state/'downloads.json').unlink()
        with self.assertRaises(FileNotFoundError):
            store.charge_transfer(1)
        self.assertFalse((state/'downloads.json').exists())
        (state/'upload-state.json').unlink()
        source = self.root/'source'; source.mkdir(); (source/'original').write_bytes(b'original')
        memory = MemoryStore()
        with self.assertRaises(FileNotFoundError):
            archive.snapshot(source, memory, state/'upload-state.json', 1024**2,
                             transport_codec=archive.CODEC, archive_max_raw_bytes=1024,
                             archive_min_free_bytes=1024**2, require_existing_state=True)
        self.assertFalse((state/'upload-state.json').exists())
        self.assertEqual(memory.data, {})

    def test_legacy_request_initialization_is_unchanged_without_strict_flag(self):
        request = self.root/'legacy-requests.json'
        store = archive.GenerationStore('fixture-bucket', 'fixture', request_state=request, max_requests=10)
        store.charge_request()
        self.assertEqual(json.loads(request.read_text()), {'requests_upper': 1})

    def test_strict_transfer_lock_symlink_is_refused_without_chmod(self):
        target = self.root/'unrelated'; target.write_bytes(b'unchanged'); target.chmod(0o644)
        transfer = self.root/'downloads.json'
        transfer.write_text(json.dumps({'bytes_upper': 4})); transfer.chmod(0o600)
        transfer.with_suffix('.lock').symlink_to(target)
        store = archive.GenerationStore('fixture-bucket', 'fixture', request_state=self.root/'requests.json',
                                        max_requests=10, transfer_state=transfer,
                                        max_transfer_bytes=1024, require_existing_state=True)
        with self.assertRaises(archive.GuardError):
            store.charge_transfer(1)
        self.assertEqual(target.read_bytes(), b'unchanged')
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertEqual(json.loads(transfer.read_text()), {'bytes_upper': 4})

    def test_generation_is_pinned_and_eof_hash_checked(self):
        raw = b'original complete byte payload'
        store = archive.GenerationStore('fixture-bucket', 'fixture')
        urls = []
        def request(url, **kwargs):
            urls.append(url)
            if 'alt=media' in url:
                return Response(raw)
            return Response(json.dumps({'bucket': 'fixture-bucket', 'name': 'fixture/blob',
                                        'generation': '7788', 'size': str(len(raw))}).encode())
        store.request = request
        binding = store.verify_generation('blob', hashlib.sha256(raw).hexdigest(), len(raw))
        self.assertEqual(binding['generation'], '7788')
        self.assertIn('generation=7788', urls[-1])
        self.assertIn('ifGenerationMatch=7788', urls[-1])
        store.request = lambda url, **kwargs: Response(raw[:-1], length=len(raw))
        with self.assertRaisesRegex(archive.GuardError, 'EOF/hash'):
            store.read_generation(binding)
        store.request = lambda url, **kwargs: Response(raw+b'x', length=len(raw))
        with self.assertRaisesRegex(archive.GuardError, 'exceeds bound'):
            store.read_generation(binding)

    def test_whole_roster_rejects_added_or_changed_file(self):
        p = self.root/'original'; p.write_bytes(b'original')
        descriptor = {'files': {'original': {'bytes': 8, 'sha256': file_digest(p)}}}
        archive.verify_tree(self.root, descriptor)
        extra = self.root/'extra'; extra.write_bytes(b'new original')
        with self.assertRaisesRegex(archive.GuardError, 'roster'):
            archive.verify_tree(self.root, descriptor)
        extra.unlink(); p.write_bytes(b'changed!')
        with self.assertRaisesRegex(archive.GuardError, 'bytes changed'):
            archive.verify_tree(self.root, descriptor)

    def test_fifo_and_symlink_are_refused_before_and_after_transport(self):
        fifo = self.root/'original-pipe'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(archive.GuardError, 'special file'):
            archive.regular_roster(self.root)
        with self.assertRaisesRegex(archive.GuardError, 'special file'):
            archive.verify_tree(self.root, {'files': {}})
        fifo.unlink()
        original = self.root/'original'; original.write_bytes(b'original')
        (self.root/'alias').symlink_to(original)
        with self.assertRaisesRegex(archive.GuardError, 'symlink'):
            archive.regular_roster(self.root)

    def source_fixture(self):
        code = self.root/'code'; (code/'dams_sim').mkdir(parents=True)
        (code/'dams_sim/model.py').write_text('fixture = True\n')
        for name in {*DESIGN_INPUTS, *EXECUTION_INPUTS, *archive.IMPLEMENTATION}:
            p = code/name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{}\n' if p.suffix == '.json' else 'fixture = True\n')
        source = self.root/'study'; source.mkdir()
        (source/'plan.json').write_text(json.dumps({'scientific_dependencies': science_manifest(code)}))
        for name in ('world/prehistory/checkpoint.json', 'world/progress/checkpoint.json',
                     'world/interrupted-final-0/.partial.tmp', 'execution/runner.log'):
            p = source/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(name.encode())
        for name in ('world/prehistory/checkpoint.sqlite', 'world/progress/checkpoint.sqlite',
                     'world/interrupted-work-0/.longitudinal-working.sqlite'):
            p = source/name; p.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(p)
            try:
                db.execute('CREATE TABLE journal(payload)'); db.execute('INSERT INTO journal VALUES(?)', (name,)); db.commit()
            finally:
                db.close()
        return code, source

    def test_complete_hidden_roster_generation_receipt_and_offline_restore(self):
        code, source = self.source_fixture()
        memory = MemoryStore()
        state = self.root/'state'; state.mkdir(mode=0o700)
        for name, record in (('requests.json', {'requests_upper': 0}),
                              ('downloads.json', {'bytes_upper': 0}),
                              ('upload-state.json', {'uploaded': {}, 'bytes': 0, 'verified': [], 'metadata_sizes': {}})):
            p = state/name; p.write_text(json.dumps(record)); p.chmod(0o600)
        config = {'bucket': 'fixture-bucket', 'prefix': 'fixture', 'deadline_utc': FIXTURE_DEADLINE,
                  'max_requests': 10000, 'max_download_bytes': 90*1024**2, 'max_storage_bytes': 90*1024**2,
                  'max_raw_bytes': 90*1024**2, 'max_files': 1000, 'minimum_free_bytes': 1024**2,
                  'state_directory': str(state)}
        original_bundle = archive.source_bundle
        with (patch.object(archive, 'require_quiet', return_value=ABSENT),
              patch.object(archive, 'source_bundle', side_effect=lambda p: original_bundle(p, code)),
              patch.object(archive, 'GenerationStore', return_value=memory),
              patch('cloud_worker.wait_pipeline_group_absent', return_value=ABSENT)):
            receipt_path = archive.archive_closed(source, config, 123456, self.root/'state')
            receipt = json.loads(receipt_path.read_text())
            self.assertFalse(receipt['archive_accepted'])
            self.assertFalse(receipt['complete_preservation_accepted'])
            names = set(receipt['descriptor']['files'])
            self.assertIn('world/prehistory/checkpoint.sqlite', names)
            self.assertIn('world/progress/checkpoint.sqlite', names)
            self.assertIn('world/interrupted-work-0/.longitudinal-working.sqlite', names)
            self.assertIn('world/interrupted-final-0/.partial.tmp', names)
            self.assertIn('archive-source/source-manifest.json', names)
            # A restart's old verified bits must not replace generation custody.
            memory.bindings.clear()
            receipt_path = archive.archive_closed(source, config, 123456, self.root/'state')
            receipt = json.loads(receipt_path.read_text())
        mirror = self.root/'objects'
        for key, binding in receipt['objects'].items():
            p = mirror/binding['generation']/binding['sha256']; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(memory.data[key])
        destination = self.root/'restored'
        result = archive.restore_mirror(receipt_path, mirror, destination,
                                        max_raw_bytes=90*1024**2, minimum_free_bytes=1024**2)
        self.assertFalse(result['complete_preservation_accepted'])
        for name, record in receipt['descriptor']['files'].items():
            self.assertEqual(file_digest(destination/name), record['sha256'])
        binding = next(iter(receipt['objects'].values()))
        (mirror/binding['generation']/binding['sha256']).write_bytes(b'corrupt')
        with self.assertRaises(archive.GuardError):
            archive.restore_mirror(receipt_path, mirror, self.root/'corrupt-restore',
                                   max_raw_bytes=90*1024**2, minimum_free_bytes=1024**2)


if __name__ == '__main__':
    unittest.main()
