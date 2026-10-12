"""Native local control regressions; no GCP, new research samples or credentials.

Only guest hardware/package execution attestation is mocked in subprocess tests.
File publication, immutable source checks, leases, stopping, and reaping are real.
"""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT), str(ROOT / 'research_tools')]

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'research_tools' / file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

w = load('fault_control_worker', 'compute_only_worker.py')
r = load('fault_control_remote', 'compute_only_remote.py')

def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()

class LeaseControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.work = Path(self.temp.name)
        self.runtime_sha = 'a' * 64
        self.now = 1_800_000_000.
        self.put(self.body(0))
        self.directory = w.Directory(self.work)

    def tearDown(self):
        self.directory.close()
        self.temp.cleanup()

    def body(self, sequence, at=None):
        return {'schema': 'DAMS-compute-controller-lease-1',
                'runtime_sha256': self.runtime_sha, 'sequence': sequence,
                'observed_utc': iso(self.now if at is None else at)}

    def put(self, body):
        p = self.work / ('new-' + str(time.time_ns()))
        p.write_bytes(w.canonical(body)); p.chmod(0o600)
        os.replace(p, self.work / 'controller-heartbeat.json')

    def interleaved_read(self, rotate):
        native = os.read
        calls = []
        def read(fd, size):
            data = native(fd, size)
            if data and (rotate == 'always' or not calls):
                calls.append(True)
                self.put(self.body(len(calls)))
            return data
        return read, calls

    def test_atomic_replace_old_immutable_read_rejects_new_lease_read_accepts(self):
        hook, _ = self.interleaved_read('once')
        with patch.object(w.os, 'read', side_effect=hook):
            with self.assertRaisesRegex(w.GuardError, 'changed during read'):
                self.directory.read('controller-heartbeat.json')
        self.put(self.body(0))
        hook, calls = self.interleaved_read('once')
        with patch.object(w.os, 'read', side_effect=hook):
            actual = w.lease(self.directory, self.runtime_sha, 120, lambda: self.now)
        self.assertEqual(actual, self.body(1)); self.assertEqual(len(calls), 1)

    def test_three_continuous_atomic_rotations_refuse_boundedly(self):
        hook, calls = self.interleaved_read('always')
        with patch.object(w.os, 'read', side_effect=hook):
            with self.assertRaisesRegex(w.GuardError, 'bounded reads'):
                self.directory.read_lease()
        self.assertEqual(len(calls), 3)

    def test_in_place_change_refuses_without_relaxing_immutable_sources(self):
        native = os.read; mutated = []
        def read(fd, size):
            data = native(fd, size)
            if data and not mutated:
                mutated.append(True)
                p = self.work / 'controller-heartbeat.json'
                p.write_bytes(w.canonical(self.body(12345)))
            return data
        with patch.object(w.os, 'read', side_effect=read):
            with self.assertRaisesRegex(w.GuardError, 'inode changed'):
                self.directory.read_lease()

    def test_aliases_permissions_and_nonlease_name_refuse(self):
        p = self.work / 'controller-heartbeat.json'
        os.link(p, self.work / 'alias')
        with self.assertRaises(w.GuardError): self.directory.read_lease()
        (self.work / 'alias').unlink(); p.chmod(0o644)
        with self.assertRaises(w.GuardError): self.directory.read_lease()
        p.chmod(0o600)
        with self.assertRaises(w.GuardError): self.directory.read_lease('source.json')
        p.unlink(); p.symlink_to(self.work / 'absent')
        with self.assertRaises((w.GuardError, OSError)): self.directory.read_lease()

    def test_fresh_validation_clock_avoids_prescan_future_false_positive(self):
        self.put(self.body(1, self.now + 6))
        with self.assertRaisesRegex(w.GuardError, 'future dated'):
            w.lease(self.directory, self.runtime_sha, 120, self.now)
        self.assertEqual(w.lease(self.directory, self.runtime_sha, 120,
                                lambda: self.now + 7), self.body(1, self.now + 6))

    def test_expiry_future_sequence_and_time_tamper_still_refuse(self):
        previous = self.body(2)
        for body in (self.body(3, self.now - 121), self.body(3, self.now + 6),
                     self.body(1), self.body(2, self.now + 1),
                     self.body(3, self.now - 1),
                     {**self.body(3), 'runtime_sha256': 'b' * 64}):
            with self.subTest(body=body):
                self.put(body)
                with self.assertRaises(w.GuardError):
                    w.lease(self.directory, self.runtime_sha, 120, lambda: self.now, previous)

    def test_wall_clock_rollback_does_not_extend_lifetime(self):
        with patch.object(w.time, 'time', return_value=100), \
             patch.object(w.time, 'monotonic', return_value=200): clock = w.Clock()
        with patch.object(w.time, 'time', return_value=90), \
             patch.object(w.time, 'monotonic', return_value=206):
            self.assertEqual(clock.now(), 106)

    def test_first_failure_remains_durable_and_secondary_source_fault_is_separate(self):
        history = w.GuardHistory(self.directory, type('Clock', (), {'now': lambda _: self.now})(),
                                 self.runtime_sha, 'b' * 64, iso(self.now))
        first = w.GuardError('controller lease expired or future dated')
        first.guard_id = 'lease_time'; first.expected = {'max_age_seconds': 120}
        first.actual = {'age_seconds': 121, 'private': 'never-publish-this'}
        history.record(first, 'controller_lease', 'running', self.body(9))
        receipt = history.receipt.copy(); raw = (self.work / receipt['file']).read_bytes()
        source = w.GuardError('frozen guest input changed')
        history.record(source, 'source_identity', 'terminal_recheck', self.body(9))
        self.assertEqual((self.work / receipt['file']).read_bytes(), raw)
        self.assertEqual(history.first['guard_id'], 'lease_time')
        self.assertEqual(history.secondary[0]['check_stage'], 'source_identity')
        self.assertTrue(history.input_failed)
        self.assertEqual(w.sha(raw), receipt['sha256'])
        self.assertNotIn('never-publish-this', raw.decode())

    def test_unknown_exception_and_cause_are_redacted_and_storage_fault_does_not_mask_first(self):
        history = w.GuardHistory(self.directory, type('Clock', (), {'now': lambda _: self.now})(),
                                 self.runtime_sha, 'b' * 64, iso(self.now))
        error = ValueError('secret-token-and-path')
        error.__cause__ = OSError(13, 'credential-path-secret')
        with patch.object(self.directory, 'write', side_effect=OSError(28, 'disk-full-secret')):
            result = history.record(error, 'controller_lease', 'running', self.body(0))
        self.assertEqual(result, 'ValueError'); self.assertFalse(history.receipt['durably_saved'])
        body = json.dumps(history.first)
        for forbidden in ('secret-token', 'credential-path', 'disk-full-secret'):
            self.assertNotIn(forbidden, body)
        self.assertEqual(history.first['cause_chain'][1]['errno'], 13)
        self.assertFalse(history.input_failed)

class SubprocessControls(unittest.TestCase):
    def exercise(self, secondary_input_fault=False, fault_stage=None):
        with tempfile.TemporaryDirectory(dir=HERE) as folder:
            base = Path(folder); root = base / 'root'; work = base / 'work'
            root.mkdir(); work.mkdir(); (root / 'dams_sim').mkdir()
            script = root / 'run.sh'; script.write_text('#!/bin/sh\nexec /bin/sleep 20\n'); script.chmod(0o700)
            (root / 'dams_sim' / 'fixture.py').write_text('# native immutable fixture\n')
            (root / 'source-manifest.json').write_bytes(b'fixture manifest\n')
            start = time.time()
            limits = w.RuntimeLimits(max_workers=1, cpu_budget=1, batch_max_output_files=100,
                per_world_output_files=50, deadline_utc=iso(start + 60), stop_cutoff_utc=iso(start + 70),
                cooperative_stop_grace_seconds=.1).to_dict()
            transfer = w.canonical({'fixture': 'no transfer provider'})
            runtime = {'schema': w.SCHEMA, 'source_commit': 'a' * 40,
                'source_manifest_sha256': 'b' * 64, 'spec': 'longitudinal-adoption-5y', 'scale': 2,
                'runtime_limits': limits, 'purchase_mode': 'STANDARD', 'machine_type': 'n2-standard-4',
                'expected_guest': {'architecture': 'x86_64', 'vcpus': 4},
                'expected_identity': {'instance_id': 'fixture', 'zone': 'fixture-zone',
                    'boot_disk_device_name': 'fixture-disk', 'guest_os_release_sha256': 'c' * 64,
                    'boot_disk_bytes_min': 1}, 'provider_identity_sha256': 'd' * 64,
                'deadline_utc': iso(start + 120), 'watchdog_shutdown_utc': iso(start + 90),
                'pipeline_stop_grace_seconds': 6, 'controller_lease_timeout_seconds': 120,
                'transfer_config_sha256': w.sha(transfer)}
            self.assertIs(w.validate_runtime(runtime), runtime)
            for name, raw in (('compute-runtime.json', w.canonical(runtime)), ('transfer-config.json', transfer)):
                p = work / name; p.write_bytes(raw); p.chmod(0o600)
            heartbeat = {'schema': 'DAMS-compute-controller-lease-1', 'runtime_sha256': w.sha(w.canonical(runtime)),
                         'sequence': 0, 'observed_utc': iso(start)}
            p = work / 'controller-heartbeat.json'; p.write_bytes(w.canonical(heartbeat)); p.chmod(0o600)
            observations = {n: (w.sha((root / n).read_bytes()), w.anchor((root / n).stat()))
                            for n in ('run.sh', 'source-manifest.json', 'dams_sim/fixture.py')}
            native_read = w.Directory.read_lease; calls = []
            def read(directory, *args, **kwargs):
                calls.append(True)
                if (fault_stage is None and len(calls) == 3) or (fault_stage == 'prelaunch' and len(calls) == 2):
                    new = work / 'new-lease'; new.write_bytes(w.canonical({**heartbeat, 'sequence': 1,
                        'observed_utc': iso(start - 121)})); new.chmod(0o600); os.replace(new, p)
                    if secondary_input_fault: (root / 'dams_sim/fixture.py').write_text('# actual changed source\n')
                return native_read(directory, *args, **kwargs)
            verification = {'instance_id': 'fixture', 'zone': 'fixture-zone', 'machine_type': 'n2-standard-4'}
            native_popen = subprocess.Popen
            def popen(*args, **kwargs):
                if fault_stage == 'launch': raise OSError(13, 'private-launch-path')
                process = native_popen(*args, **kwargs)
                if fault_stage == 'supervision':
                    native_poll = process.poll; observed = []
                    def poll():
                        if not observed:
                            observed.append(True); raise OSError(5, 'private-supervision-path')
                        return native_poll()
                    process.poll = poll
                return process
            with patch.object(w, 'validate_package', return_value=observations), \
                 patch.object(w, 'verify_executing_source', return_value=None), \
                 patch.object(w, 'measured_guest', return_value={}), \
                 patch.object(w, 'verify_identity', return_value=verification), \
                 patch.object(w.Directory, 'read_lease', new=read), \
                 patch.object(w.subprocess, 'Popen', new=popen):
                if fault_stage:
                    with self.assertRaises(w.GuardError if fault_stage == 'prelaunch' else OSError):
                        w.run_compute(work / 'compute-runtime.json', root, work)
                else: self.assertEqual(w.run_compute(work / 'compute-runtime.json', root, work), 2)
            if fault_stage:
                receipts = list(work.glob('first-guard-fault-*.json')); self.assertEqual(len(receipts), 1)
                first = r.parse(receipts[0].read_bytes())
                expected = {'prelaunch': 'controller_lease', 'launch': 'pipeline_launch',
                            'supervision': 'pipeline_supervision'}[fault_stage]
                self.assertEqual(first['check_stage'], expected)
                self.assertNotIn('private-launch-path', receipts[0].read_text())
                self.assertNotIn('private-supervision-path', receipts[0].read_text())
                if fault_stage == 'supervision':
                    pid = r.parse((work / 'process.json').read_bytes())['pid']
                    with self.assertRaises(ProcessLookupError): os.killpg(pid, 0)
                else: self.assertFalse((work / 'process.json').exists())
                return
            terminal = json.loads((work / 'compute-terminal.json').read_bytes())
            self.assertEqual(terminal['first_guard_failure']['guard_id'], 'lease_time')
            self.assertEqual(terminal['stop_reason'], 'input_or_controller_guard')
            self.assertEqual(terminal['inputs_unchanged'], not secondary_input_fault)
            self.assertTrue(terminal['control_state']['controller_guard_failed'])
            self.assertTrue(terminal['owned_pipeline_group_verification']['owned_pipeline_group_absent'])
            self.assertFalse(terminal['science_complete'])
            pid = terminal['owned_pipeline_group_verification']['owned_pipeline_group_id']
            with self.assertRaises(ProcessLookupError): os.killpg(pid, 0)
            first = (work / terminal['first_guard_receipt']['file']).read_bytes()
            self.assertEqual(w.sha(first), terminal['first_guard_receipt']['sha256'])
            if secondary_input_fault:
                self.assertEqual(terminal['secondary_guard_failures'][0]['check_stage'], 'source_identity')
            self.assertTrue((work / 'process.json').is_file())
            with w.directory(work) as directory, self.assertRaisesRegex(w.GuardError, 'already attempted'):
                with w.single_run(directory): self.fail('automatic restart must refuse')

    def test_real_child_lease_failure_reaps_and_preserves_verified_inputs(self): self.exercise()
    def test_real_terminal_input_failure_cannot_overwrite_first_control_failure(self): self.exercise(True)
    def test_prelaunch_lease_failure_saves_first_fault_without_launch(self): self.exercise(fault_stage='prelaunch')
    def test_os_launch_failure_is_redacted_and_saved_before_refusal(self): self.exercise(fault_stage='launch')
    def test_supervision_exception_saves_first_fault_and_reaps_real_child(self): self.exercise(fault_stage='supervision')

class ForensicControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HERE); self.work = Path(self.temp.name)
        (self.work / 'output').mkdir(); self.payload = b'complete unvalidated raw evidence\x00\xff'
        (self.work / 'output' / 'raw.sqlite-wal').write_bytes(self.payload)
        self.closed = subprocess.Popen(['/bin/sleep', '0'], start_new_session=True)
        self.closed.wait(timeout=2)
        self.terminal = {'runtime_sha256': 'a' * 64, 'inputs_unchanged': False,
            'owned_pipeline_group_verification': {'owned_pipeline_group_absent': True,
                                                 'owned_pipeline_group_id': self.closed.pid}}
        (self.work / 'compute-terminal.json').write_bytes(r.blob(self.terminal))
        self.c = {'runtime_sha256': 'a' * 64, 'source_manifest_sha256': 'b' * 64,
            'source_sha256': 'c' * 64, 'max_terminal_files': 3, 'max_terminal_bytes': 4096,
            'deadline_utc': iso(time.time() + 60)}
        self.c['forensic_admission'] = {'schema': 'DAMS-root-readonly-forensic-admission-1',
            **{k: self.c[k] for k in ('runtime_sha256', 'source_manifest_sha256',
                                    'max_terminal_files', 'max_terminal_bytes', 'deadline_utc')},
            'terminal_sha256': r.sha(r.blob(self.terminal)), 'read_only': True, 'science_acceptance': False}

    def tearDown(self): self.temp.cleanup()

    def roster(self):
        return r.parse(r.closed_terminal_transport(self.c, self.work, self.terminal,
                       'terminal-roster', b'', forensic=True))

    def test_false_input_guard_allows_explicit_unvalidated_complete_byte_preservation(self):
        roster = self.roster(); self.assertEqual(roster['schema'], 'dams-compute-forensic-roster-v1')
        self.assertEqual(roster['source_integrity_status'], 'UNVALIDATED'); self.assertFalse(roster['science_complete'])
        file = roster['files'][0]; self.assertEqual(file['sha256'], r.sha(self.payload))
        actual = r.closed_terminal_transport(self.c, self.work, self.terminal, 'terminal-read',
            r.blob({'file': file, 'offset': 0, 'length': len(self.payload)}), forensic=True)
        self.assertEqual(actual, self.payload)
        with self.assertRaisesRegex(ValueError, 'source guard'):
            r.closed_terminal_transport(self.c, self.work, self.terminal, 'terminal-roster', b'')

    def test_missing_foreign_or_scientific_admission_refuses(self):
        original = self.c['forensic_admission'].copy()
        for key, bad in (('runtime_sha256', 'd' * 64), ('source_manifest_sha256', 'd' * 64),
                         ('terminal_sha256', 'd' * 64), ('max_terminal_files', 4),
                         ('max_terminal_bytes', 4097), ('deadline_utc', iso(time.time() + 100)),
                         ('read_only', False), ('science_acceptance', True), ('schema', 'fake')):
            with self.subTest(key=key):
                self.c['forensic_admission'] = {**original, key: bad}
                with self.assertRaises(ValueError): self.roster()
        self.c.pop('forensic_admission')
        with self.assertRaises(ValueError): self.roster()

    def test_live_owned_group_refuses_even_if_terminal_claims_absence(self):
        child = subprocess.Popen(['/bin/sleep', '10'], start_new_session=True)
        try:
            self.terminal['owned_pipeline_group_verification']['owned_pipeline_group_id'] = child.pid
            (self.work / 'compute-terminal.json').write_bytes(r.blob(self.terminal))
            self.c['forensic_admission']['terminal_sha256'] = r.sha(r.blob(self.terminal))
            with self.assertRaisesRegex(ValueError, 'still exists'): self.roster()
        finally:
            os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=2)

    def test_native_change_aliases_and_caps_refuse_without_deleting_data(self):
        file = self.roster()['files'][0]
        p = self.work / 'output' / file['path']; p.write_bytes(self.payload + b'changed')
        with self.assertRaisesRegex(ValueError, 'replaced'):
            r.closed_terminal_transport(self.c, self.work, self.terminal, 'terminal-read',
                r.blob({'file': file, 'offset': 0, 'length': 1}), forensic=True)
        os.link(p, self.work / 'output' / 'alias')
        with self.assertRaises(ValueError): self.roster()
        (self.work / 'output' / 'alias').unlink()
        self.c['max_terminal_bytes'] = 1; self.c['forensic_admission']['max_terminal_bytes'] = 1
        with self.assertRaisesRegex(ValueError, 'admitted bound'): self.roster()
        self.assertEqual(p.read_bytes(), self.payload + b'changed')

    def test_overbound_payload_is_refused_before_payload_io(self):
        p = self.work / 'output' / 'raw.sqlite-wal'; p.write_bytes(b'x' * 262144)
        self.c['max_terminal_bytes'] = 32
        self.c['forensic_admission']['max_terminal_bytes'] = 32
        native = os.read; identity = (p.stat().st_dev, p.stat().st_ino)
        def read(fd, size):
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) == identity:
                self.fail('overbound payload must never be opened/read for hashing')
            return native(fd, size)
        with patch.object(r.os, 'read', side_effect=read):
            with self.assertRaisesRegex(ValueError, 'admitted bound'): self.roster()
        self.assertEqual(p.stat().st_size, 262144)

    def test_mid_scan_namespace_addition_refuses_an_incomplete_roster(self):
        p = self.work / 'output' / 'raw.sqlite-wal'
        identity = (p.stat().st_dev, p.stat().st_ino); native = os.read; added = []
        def read(fd, size):
            data = native(fd, size); st = os.fstat(fd)
            if data and (st.st_dev, st.st_ino) == identity and not added:
                added.append(True)
                (self.work / 'output' / 'new-evidence.bin').write_bytes(b'new evidence')
            return data
        with patch.object(r.os, 'read', side_effect=read):
            with self.assertRaisesRegex(ValueError, 'namespace changed'): self.roster()
        self.assertEqual((self.work / 'output' / 'new-evidence.bin').read_bytes(), b'new evidence')

    def test_regular_to_fifo_open_race_refuses_without_blocking(self):
        file = self.roster()['files'][0]; p = self.work / 'output' / file['path']
        native = os.open; changed = []
        def open_file(target, flags, *args, **kwargs):
            if str(target) == str(p) and not changed:
                changed.append(True); p.unlink(); os.mkfifo(p, 0o600)
            return native(target, flags, *args, **kwargs)
        def timeout(*_): raise TimeoutError('FIFO open blocked despite bounded transport')
        old = signal.signal(signal.SIGALRM, timeout)
        try:
            signal.setitimer(signal.ITIMER_REAL, 1)
            with patch.object(r.os, 'open', side_effect=open_file):
                with self.assertRaisesRegex(ValueError, 'FD replaced'):
                    r.closed_terminal_transport(self.c, self.work, self.terminal, 'terminal-read',
                        r.blob({'file': file, 'offset': 0, 'length': 1}), forensic=True)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGALRM, old)
        self.assertTrue(changed)

    def test_route_checks_real_context_before_reading_and_never_imports_guest_package(self):
        context = {**self.c, 'project': 'fixture-project', 'zone': 'fixture-zone', 'instance_id': 'fixture',
                   'source_root': '/opt/dams', 'work_root': '/var/lib/dams-compute'}
        identity = {k: context[k] for k in ('project', 'zone', 'instance_id')}
        native_path = r.path
        def mapped(p):
            return self.work if str(p) == '/var/lib/dams-compute' else native_path(p)
        with patch.object(r, 'path', side_effect=mapped), \
             patch.object(r, 'package', side_effect=AssertionError('forensic must not import untrusted guest code')):
            result = r.parse(r.operation('forensic-terminal-roster', context, b'', lambda: identity))
            self.assertEqual(result['files'][0]['sha256'], r.sha(self.payload))
            with self.assertRaisesRegex(ValueError, 'identity'):
                r.operation('forensic-terminal-roster', context, b'', lambda: {**identity, 'instance_id': 'foreign'})
        context['source_root'] = '/unapproved'
        with self.assertRaisesRegex(ValueError, 'namespace'):
            r.operation('forensic-terminal-roster', context, b'', lambda: identity)

if __name__ == '__main__': unittest.main()
