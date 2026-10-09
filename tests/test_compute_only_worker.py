"""Tiny real subprocess supervision; no Model, provider, token, or object store."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'research_tools'))
import compute_only_worker as worker
from cloud_control import GuardError


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def save(path, value):
    path.write_bytes(worker.canonical(value))


class ComputeOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('DAMS_TEST_ROOT'))
        self.base = Path(self.tmp.name)
        self.root, self.work = self.base / 'source', self.base / 'work'
        self.root.mkdir(); self.work.mkdir()
        (self.root / 'dams_sim').mkdir(); (self.root / 'research_tools').mkdir(); (self.root / 'cloud').mkdir()
        names = ['run.sh', 'uv.lock', 'pyproject.toml', 'dams_sim/fixture.py',
                 'research_tools/compute_only_worker.py', 'research_tools/cloud_worker.py',
                 'research_tools/cloud_control.py', 'research_tools/cloud_archive.py', 'cloud/compute-only-services.sh']
        for name in names:
            (self.root / name).write_text('fixture\n')
        self.script("print('bounded pure IO exit')")
        self.transfer = {'fixture': 'opaque pinned transfer config; no credentials'}
        save(self.work / 'transfer-config.json', self.transfer)
        now = time.time()
        cpus = os.cpu_count() or 1
        self.runtime = {
            'schema': worker.SCHEMA, 'source_commit': '1' * 40,
            'source_manifest_sha256': '0' * 64, 'spec': 'longitudinal-adoption-5y', 'scale': 1000,
            'purchase_mode': 'SPOT', 'machine_type': 'c4d-highmem-4',
            'expected_guest': {'architecture': 'x86_64', 'vcpus': 4, 'memory_gib_min': 29, 'memory_gib_max': 33},
            'expected_identity': {'instance_id': '42', 'zone': 'synthetic-zone',
                                  'boot_disk_device_name': 'synthetic-boot',
                                  'guest_os_release_sha256': '2' * 64, 'boot_disk_bytes_min': 1000},
            'provider_identity_sha256': '3' * 64, 'deadline_utc': iso(now + 60),
            'watchdog_shutdown_utc': iso(now + 50), 'pipeline_stop_grace_seconds': 6,
            'controller_lease_timeout_seconds': 20, 'transfer_config_sha256': worker.sha((self.work / 'transfer-config.json').read_bytes()),
            'runtime_limits': {'cpu_budget': min(1, cpus), 'memory_budget_bytes': 1_000_000,
                               'max_workers': 1, 'max_output_bytes': 100_000, 'batch_max_output_bytes': 200_000,
                               'batch_max_output_files': 64, 'per_world_output_files': 32,
                               'deadline_utc': iso(now + 30), 'stop_cutoff_utc': iso(now + 40),
                               'cooperative_stop_grace_seconds': 1}}
        self.measurements = {
            'instance_id': '42', 'zone': 'synthetic-zone', 'machine_type': 'c4d-highmem-4',
            'guest_scheduling': {'preemptible': 'TRUE', 'automatic_restart': 'FALSE', 'on_host_maintenance': 'TERMINATE'},
            'guest_architecture': 'x86_64', 'guest_cpu_count': 4, 'guest_cpu_affinity': [0, 1, 2, 3],
            'guest_memory_total_bytes': 31 * 2**30, 'guest_memory_total_source': 'fixture MemTotal',
            'guest_boot_disk_metadata_interface': 'NVME', 'guest_root_block': {'interface': 'NVME'},
            'boot_disk_device_name': 'synthetic-boot', 'guest_os_release_sha256': '2' * 64,
            'guest_root_filesystem_bytes': 2000}
        self.pin()

    def tearDown(self):
        receipt_root = os.environ.get('DAMS_TEST_RECEIPTS')
        if receipt_root:
            target = Path(receipt_root) / self._testMethodName
            target.mkdir(parents=True, exist_ok=False)
            for relative in ('compute-runtime.json', 'transfer-config.json', 'controller-heartbeat.json',
                             'process.json', 'compute-terminal.json', 'output/cloud-execution.json',
                             'output/cloud-terminal.json', 'output/pipeline.log', 'output/argv.json', 'output/env.json'):
                path = self.work / relative
                if path.is_file() and not path.is_symlink():
                    (target / relative.replace('/', '--')).write_bytes(path.read_bytes())
        self.tmp.cleanup()

    def script(self, body):
        (self.root / 'run.sh').write_text('#!' + sys.executable + '\n' + body + '\n')
        (self.root / 'run.sh').chmod(0o700)

    def pin(self):
        hashes = {p.relative_to(self.root).as_posix(): worker.sha(p.read_bytes())
                  for p in self.root.rglob('*') if p.is_file() and p.name != 'source-manifest.json'}
        save(self.root / 'source-manifest.json', {'commit': '1' * 40, 'source_files_sha256': hashes})
        self.runtime['source_manifest_sha256'] = worker.sha((self.root / 'source-manifest.json').read_bytes())
        save(self.work / 'compute-runtime.json', self.runtime)
        self.heartbeat()

    def heartbeat(self, *, sequence=0, observed=None):
        save(self.work / 'controller-heartbeat.json', {
            'schema': 'DAMS-compute-controller-lease-1', 'runtime_sha256': worker.sha((self.work / 'compute-runtime.json').read_bytes()),
            'sequence': sequence, 'observed_utc': iso(time.time() if observed is None else observed)})

    def run_worker(self):
        # Fixture run.sh is a tiny subprocess, not the scientific source package.
        # Loaded-module origin has a separate fail-closed control below.
        with patch.object(worker, 'measured_guest', return_value=self.measurements), \
                patch.object(worker, 'verify_executing_source'):
            return worker.run_compute(self.work / 'compute-runtime.json', self.root, self.work)

    def terminal(self):
        return json.loads((self.work / 'compute-terminal.json').read_text())

    def after_process(self, action):
        errors = []
        def run():
            end = time.monotonic() + 5
            while not (self.work / 'process.json').exists():
                if time.monotonic() > end:
                    errors.append('no pipeline process'); return
                time.sleep(.01)
            action()
        thread = threading.Thread(target=run)
        thread.start()
        return thread, errors

    def test_success_uses_existing_entry_and_spool_env(self):
        self.script("import json,os,sys\nfrom pathlib import Path\na=sys.argv\no=Path(a[a.index('--output')+1])\n(o/'argv.json').write_text(json.dumps(a[1:]))\n(o/'env.json').write_text(json.dumps({k:os.environ.get(k) for k in ['DAMS_TRANSFER_CONFIG','DAMS_PACKAGED_COMMIT','DAMS_OFFLINE_DEPENDENCIES','DAMS_CLOUD_PRIVATE_CONFIG','GOOGLE_APPLICATION_CREDENTIALS']}))")
        self.pin()
        with patch.dict(os.environ, {'DAMS_CLOUD_PRIVATE_CONFIG': 'do-not-inherit', 'GOOGLE_APPLICATION_CREDENTIALS': 'do-not-inherit'}):
            self.assertEqual(self.run_worker(), 0)
        env = json.loads((self.work / 'output/env.json').read_text())
        self.assertEqual(env['DAMS_TRANSFER_CONFIG'], str(self.work / 'transfer-config.json'))
        self.assertEqual(env['DAMS_PACKAGED_COMMIT'], '1' * 40)
        self.assertEqual(env['DAMS_OFFLINE_DEPENDENCIES'], '1')
        self.assertIsNone(env['DAMS_CLOUD_PRIVATE_CONFIG']); self.assertIsNone(env['GOOGLE_APPLICATION_CREDENTIALS'])
        argv = json.loads((self.work / 'output/argv.json').read_text())
        self.assertEqual(argv[:4], ['--spec', 'longitudinal-adoption-5y', '--scale', '1000'])
        self.assertFalse(self.terminal()['science_complete'])
        self.assertFalse(self.terminal()['transfer_ack_verified'])
        self.assertTrue(self.terminal()['owned_pipeline_group_verification']['owned_pipeline_group_absent'])

    def test_single_execution_no_restart(self):
        self.assertEqual(self.run_worker(), 0)
        before = (self.work / 'compute-terminal.json').read_bytes()
        with self.assertRaisesRegex(GuardError, 'already attempted'):
            self.run_worker()
        self.assertEqual((self.work / 'compute-terminal.json').read_bytes(), before)

    def test_scientific_cutoff_sends_real_TERM(self):
        self.script("import signal,time\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,lambda *_: (Path('term-seen').write_text('TERM'),exit(0)))\nwhile True:time.sleep(.05)")
        now = time.time()
        self.runtime['runtime_limits'].update(deadline_utc=iso(now + .6), stop_cutoff_utc=iso(now + 8))
        self.pin()
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual(self.terminal()['stop_reason'], 'scientific_deadline')
        self.assertEqual((self.root / 'term-seen').read_text(), 'TERM')

    def test_lost_controller_TERM_bounded_no_deadline_reset(self):
        self.script("import signal,time\nsignal.signal(signal.SIGTERM,lambda *_:exit(0))\nwhile True:time.sleep(.05)")
        self.runtime['controller_lease_timeout_seconds'] = 1
        self.pin()
        original_deadline = self.runtime['deadline_utc']
        self.assertEqual(self.run_worker(), 2)
        self.assertEqual(self.terminal()['stop_reason'], 'input_or_controller_guard')
        self.assertEqual(self.terminal()['deadline_utc'], original_deadline)

    def test_noncooperative_child_is_KILLed_and_reaped(self):
        self.script("import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nwhile True:time.sleep(.05)")
        self.runtime['controller_lease_timeout_seconds'] = 1
        self.pin()
        start = time.monotonic()
        self.assertEqual(self.run_worker(), 2)
        self.assertEqual(self.terminal()['exit_code'], -signal.SIGKILL)
        self.assertLess(time.monotonic() - start, 8)

    def test_real_service_signal_keeps_owned_grace(self):
        self.script("import signal,time\nsignal.signal(signal.SIGTERM,lambda *_:exit(0))\nwhile True:time.sleep(.05)")
        self.pin()
        thread, errors = self.after_process(lambda: (time.sleep(.2), os.kill(os.getpid(), signal.SIGTERM)))
        self.assertEqual(self.run_worker(), 0)
        thread.join(); self.assertFalse(errors)
        self.assertEqual(self.terminal()['stop_reason'], 'service_signal')

    def test_source_tamper_before_launch_refused(self):
        (self.root / 'dams_sim/fixture.py').write_text('changed')
        with self.assertRaisesRegex(GuardError, 'source file differs'):
            self.run_worker()
        self.assertFalse((self.work / 'process.json').exists())

    def test_source_change_during_execution_stops_preserves_exit(self):
        self.script("import signal,time\nsignal.signal(signal.SIGTERM,lambda *_:exit(0))\nwhile True:time.sleep(.05)")
        self.pin()
        thread, errors = self.after_process(lambda: (time.sleep(.2), (self.root / 'dams_sim/fixture.py').write_text('changed')))
        self.assertEqual(self.run_worker(), 2)
        thread.join(); self.assertFalse(errors)
        self.assertFalse(self.terminal()['inputs_unchanged'])
        self.assertFalse(self.terminal()['science_complete'])

    def test_added_core_module_guard(self):
        self.script("import signal,time\nsignal.signal(signal.SIGTERM,lambda *_:exit(0))\nwhile True:time.sleep(.05)")
        self.pin()
        thread, errors = self.after_process(lambda: (time.sleep(.2), (self.root / 'dams_sim/extra.py').write_text('extra')))
        self.assertEqual(self.run_worker(), 2)
        thread.join(); self.assertFalse(errors)

    def test_transfer_config_change_stops(self):
        self.script("import signal,time\nsignal.signal(signal.SIGTERM,lambda *_:exit(0))\nwhile True:time.sleep(.05)")
        self.pin()
        thread, errors = self.after_process(lambda: (time.sleep(.2), save(self.work / 'transfer-config.json', {'changed': True})))
        self.assertEqual(self.run_worker(), 2)
        thread.join(); self.assertFalse(errors)

    def test_spool_failure_exit_preserved_no_auto_retry(self):
        self.script("raise SystemExit(3)")
        self.pin()
        self.assertEqual(self.run_worker(), 3)
        self.assertEqual(self.terminal()['exit_code'], 3)
        self.assertEqual(self.terminal()['stop_reason'], 'pipeline_exit')

    def test_expired_science_refused(self):
        self.runtime['runtime_limits']['deadline_utc'] = iso(time.time() - 1)
        self.pin()
        with self.assertRaisesRegex(GuardError, 'deadline already elapsed'):
            self.run_worker()
        self.assertFalse((self.work / 'process.json').exists())

    def test_invalid_deadline_order(self):
        self.runtime['watchdog_shutdown_utc'] = self.runtime['runtime_limits']['stop_cutoff_utc']
        with self.assertRaises(GuardError):
            worker.validate_runtime(self.runtime)

    def test_empty_guest_legacy_shortcut_rejected(self):
        self.runtime['expected_guest'] = {}
        with self.assertRaises(GuardError):
            worker.validate_runtime(self.runtime)

    def test_identity_machine_cpu_ram_disk_os_mismatch(self):
        for key, value in [('instance_id', 'foreign'), ('zone', 'foreign'), ('machine_type', 'm3-ultramem-32'),
                           ('guest_cpu_count', 3), ('guest_memory_total_bytes', 1),
                           ('boot_disk_device_name', 'foreign'), ('guest_os_release_sha256', '4' * 64),
                           ('guest_root_filesystem_bytes', 500)]:
            with self.subTest(key=key), self.assertRaises(GuardError):
                worker.verify_identity(self.runtime, {**self.measurements, key: value})

    def test_manifest_omission_rejected(self):
        data = json.loads((self.root / 'source-manifest.json').read_text())
        del data['source_files_sha256']['research_tools/compute_only_worker.py']
        save(self.root / 'source-manifest.json', data)
        self.runtime['source_manifest_sha256'] = worker.sha((self.root / 'source-manifest.json').read_bytes())
        save(self.work / 'compute-runtime.json', self.runtime); self.heartbeat()
        with self.assertRaisesRegex(GuardError, 'omits runtime'):
            self.run_worker()

    def test_loaded_module_cannot_verify_another_source_tree(self):
        with worker.directory(self.root) as root:
            observations = worker.validate_package(root, self.runtime)
            with self.assertRaisesRegex(GuardError, 'outside frozen source package'):
                worker.verify_executing_source(root, observations)

    def test_controller_lease_must_still_be_fresh_after_guest_preflight(self):
        self.runtime['controller_lease_timeout_seconds'] = 1
        self.pin()
        def measurement():
            time.sleep(1.1)
            return self.measurements
        with patch.object(worker, 'verify_executing_source'), \
                patch.object(worker, 'measured_guest', side_effect=measurement):
            with self.assertRaisesRegex(GuardError, 'controller lease expired'):
                worker.run_compute(self.work / 'compute-runtime.json', self.root, self.work)
        self.assertFalse((self.work / 'process.json').exists())

    def test_symlink_source_refused(self):
        target = self.base / 'external'; target.write_text('fixture')
        (self.root / 'dams_sim/fixture.py').unlink()
        (self.root / 'dams_sim/fixture.py').symlink_to(target)
        with self.assertRaises(GuardError):
            self.run_worker()
        self.assertEqual(target.read_text(), 'fixture')

    def test_symlink_work_ancestor_refused(self):
        alias = self.base / 'alias'; alias.symlink_to(self.work, target_is_directory=True)
        with self.assertRaises(GuardError):
            worker.run_compute(alias / 'compute-runtime.json', self.root, alias)
        self.assertFalse((self.work / 'compute.lock').exists())

    def test_source_hardlink_refused(self):
        os.link(self.root / 'dams_sim/fixture.py', self.base / 'linked')
        with self.assertRaises(GuardError):
            self.run_worker()

    def test_work_namespace_swap_refused_no_foreign_receipt(self):
        with worker.directory(self.work) as held:
            os.rename(self.work, self.base / 'held-work'); self.work.mkdir()
            with self.assertRaises(GuardError):
                held.write('foreign.json', {})
        self.assertEqual(list(self.work.iterdir()), [])

    def test_controller_missing_expired_wrong_runtime(self):
        with worker.directory(self.work) as held:
            for kind in ('expired', 'wrong'):
                with self.subTest(kind=kind):
                    self.heartbeat(observed=time.time() - 60 if kind == 'expired' else None)
                    if kind == 'wrong':
                        data = json.loads((self.work / 'controller-heartbeat.json').read_text())
                        data['runtime_sha256'] = '5' * 64; save(self.work / 'controller-heartbeat.json', data)
                    with self.assertRaises(GuardError):
                        worker.lease(held, worker.sha((self.work / 'compute-runtime.json').read_bytes()), 20, time.time())
            (self.work / 'controller-heartbeat.json').unlink()
            with self.assertRaises(FileNotFoundError):
                worker.lease(held, '5' * 64, 20, time.time())

    def test_controller_sequence_rollback_and_same_sequence_refresh_refused(self):
        with worker.directory(self.work) as held:
            digest = worker.sha((self.work / 'compute-runtime.json').read_bytes())
            self.heartbeat(sequence=2)
            previous = worker.lease(held, digest, 20, time.time())
            for seq in (1, 2):
                self.heartbeat(sequence=seq)
                with self.assertRaises(GuardError):
                    worker.lease(held, digest, 20, time.time(), previous)
            self.heartbeat(sequence=3)
            self.assertEqual(worker.lease(held, digest, 20, time.time(), previous)['sequence'], 3)

    def test_controller_advancement_does_not_extend_deadlines(self):
        self.heartbeat(sequence=100)
        before = worker.canonical(self.runtime)
        with worker.directory(self.work) as held:
            worker.lease(held, worker.sha((self.work / 'compute-runtime.json').read_bytes()), 20, time.time())
        self.assertEqual(worker.canonical(self.runtime), before)

    def test_clock_rollback_does_not_extend(self):
        with patch.object(worker.time, 'time', return_value=100), patch.object(worker.time, 'monotonic', return_value=50):
            clock = worker.Clock()
        with patch.object(worker.time, 'time', return_value=1), patch.object(worker.time, 'monotonic', return_value=60):
            self.assertEqual(clock.now(), 110)

    def test_duplicate_json_nan_bool_runtime_rejected(self):
        for data in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.assertRaises(GuardError):
                worker.parse(data)
        self.runtime['scale'] = True
        with self.assertRaises(GuardError):
            worker.validate_runtime(self.runtime)

    def test_malformed_identity_and_deadline_types_refused(self):
        for key in ('deadline_utc', 'watchdog_shutdown_utc'):
            with self.subTest(key=key), self.assertRaises(GuardError):
                worker.validate_runtime({**self.runtime, key: True})
        for value in (True, 123, None):
            with self.subTest(os_sha=value), self.assertRaises(GuardError):
                worker.validate_runtime({**self.runtime, 'expected_identity': {
                    **self.runtime['expected_identity'], 'guest_os_release_sha256': value}})

    def test_service_timeout_bounded_fixed_node_lifetime(self):
        now = time.time()
        self.assertEqual(worker.service_timeout(self.runtime, now=now), 16)
        self.assertEqual(worker.service_timeout(self.runtime, now=worker.utc(self.runtime['watchdog_shutdown_utc']).timestamp() - 3), 3)
        with self.assertRaises(GuardError):
            worker.service_timeout(self.runtime, now=worker.utc(self.runtime['watchdog_shutdown_utc']).timestamp() + 1)

    def test_watchdog_fixed_shutdown_only_no_store(self):
        self.runtime['watchdog_shutdown_utc'] = iso(time.time() - 1)
        self.runtime['runtime_limits'].update(deadline_utc=iso(time.time() - 20), stop_cutoff_utc=iso(time.time() - 10))
        self.pin()
        with patch.object(worker.subprocess, 'run') as run:
            self.assertEqual(worker.watchdog(self.work / 'compute-runtime.json', self.work), 0)
        run.assert_called_once_with(['/sbin/shutdown', '-h', 'now'], check=True, timeout=15)

    def test_service_wrapper_no_upload_and_no_bootstrap_science(self):
        body = (ROOT / 'cloud/compute-only-services.sh').read_text()
        self.assertNotIn('ExecStopPost', body)
        self.assertNotIn('dams-upload', body)
        self.assertNotIn('cloud_worker.py run', body)
        self.assertIn('Restart=no', body)
        self.assertIn('dams-compute-watchdog.service', body)

    def test_cli_failure_sanitized(self):
        with patch('sys.stderr') as err:
            self.assertEqual(worker.main(['run', '--root', str(self.root), '--work', str(self.base / 'missing')]), 2)
        self.assertIn('COMPUTE_ONLY_REFUSED', ''.join(str(c.args[0]) for c in err.write.call_args_list if c.args))


if __name__ == '__main__':
    unittest.main()
