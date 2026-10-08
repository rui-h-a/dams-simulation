"""Real owned-child failure boundaries; no simulation Model is constructed."""
from argparse import Namespace
from contextlib import closing
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from dams_sim.config import Config
from research_tools import longitudinal_benchmark as benchmark


NATIVE_POPEN = subprocess.Popen
HARMLESS_CHILD = '''
import json,signal,sqlite3,sys,time
from pathlib import Path
path=Path(sys.argv[1]); mode=sys.argv[2]
run=path/'world'; run.mkdir()
with sqlite3.connect(run/'.longitudinal-working.sqlite') as db:
    db.execute('CREATE TABLE journal(seq INTEGER PRIMARY KEY,day INTEGER,kind TEXT,payload TEXT)')
    db.execute('INSERT INTO journal VALUES(1,0,?,?)',('day_end',json.dumps({'date':'2020-01-01'})))
db.close()
if mode=='ignore': signal.signal(signal.SIGTERM,signal.SIG_IGN)
else:
    def stop(signum,frame):
        (path/'term-handled').write_text('owned harmless child')
        raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop)
(path/'ready').write_text('ready')
while True: time.sleep(.01)
'''


def wait_ready(path, process):
    deadline = time.monotonic() + 5
    while not (path / 'ready').exists():
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError('harmless child did not become ready')
        time.sleep(.005)


class CpuCounterTests(unittest.TestCase):
    def test_host_timebase_is_applied_instead_of_assuming_nanoseconds(self):
        self.assertAlmostEqual(benchmark.mach_cpu_seconds(24_000_000, 125, 3), 1.)
        self.assertEqual(benchmark.mach_cpu_seconds(0, 125, 3), 0.)
        self.assertEqual(benchmark.mach_cpu_seconds(1_000_000_000, 1, 1), 1.)

    def test_invalid_counter_or_timebase_is_never_a_cpu_measurement(self):
        for values in ((1, 0, 1), (1, 1, 0), (-1, 125, 3), (True, 1, 1), (1, 1.0, 1)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                benchmark.mach_cpu_seconds(*values)

    @unittest.skipUnless(sys.platform == 'darwin', 'host Mach accounting')
    def test_live_cpu_delta_matches_process_time(self):
        before = benchmark.process_metrics(os.getpid())
        start = time.process_time()
        while time.process_time() - start < .12:
            sum(i * i for i in range(300))
        own_delta = time.process_time() - start
        after = benchmark.process_metrics(os.getpid())
        self.assertEqual(after['cpu_counter_basis'], 'mach_absolute_time')
        self.assertGreater(after['cpu_timebase_numer'], 0)
        self.assertGreater(after['cpu_timebase_denom'], 0)
        delta = after['cpu_seconds'] - before['cpu_seconds']
        # Reads bracket the self-timed burn; allow 30 ms accounting/measurement
        # overhead, but not the previous architecture-dependent factor of 41.7.
        self.assertAlmostEqual(delta, own_delta, delta=.03)


class ChildReceiptTests(unittest.TestCase):
    def test_child_keeps_committed_day_when_run_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            config = Config(n=4, days=30, team_size=2)
            benchmark.atomic_json(path / 'task.json', {'source_sha256': 'frozen', 'config': config.to_dict(),
                'checkpoint_interval_days': 90, 'checkpoint_interval_seconds': 60})

            def failed_world(config, run, **kwargs):
                with closing(sqlite3.connect(run / '.longitudinal-working.sqlite')) as db, db:
                    db.execute('CREATE TABLE journal(seq INTEGER PRIMARY KEY,day INTEGER,kind TEXT,payload TEXT)')
                    db.execute('INSERT INTO journal VALUES(1,2,?,?)', ('day_end', json.dumps({'date': '2020-01-03'})))
                    db.execute('INSERT INTO journal VALUES(2,3,?,?)', ('work', '{}'))
                benchmark.atomic_json(run / 'manifest.json', {'days_completed': 3})
                raise TimeoutError('controlled harmless runner failure')

            handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
            try:
                with patch.object(benchmark, 'source_hash', return_value='frozen'), patch('dams_sim.cli.run_world', side_effect=failed_world):
                    code = benchmark.child(path)
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
            receipt = benchmark.read_json(path / 'child.json')
            self.assertEqual(code, 1)
            self.assertEqual(receipt['last_committed_day_end']['day'], 2)
            self.assertEqual(receipt['journal_rows'], 2)
            self.assertEqual(receipt['completed_calendar_days'], 3)
            self.assertFalse(receipt['completed_entire_horizon'])
            self.assertEqual(benchmark.read_json(path / 'child-process.json')['pid'], os.getpid())

    def test_sql_observation_excludes_uncommitted_day(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with closing(sqlite3.connect(path / '.longitudinal-working.sqlite')) as writer:
                writer.execute('CREATE TABLE journal(seq INTEGER PRIMARY KEY,day INTEGER,kind TEXT,payload TEXT)')
                writer.execute('INSERT INTO journal VALUES(1,0,?,?)', ('day_end', '{}'))
                writer.commit()
                writer.execute('INSERT INTO journal VALUES(2,1,?,?)', ('day_end', '{}'))
                observed = benchmark.ledger_observation(path)
                self.assertEqual(observed['last_committed_day_end']['day'], 0)
                self.assertEqual(observed['journal_rows'], 1)
                self.assertIsNone(observed['sqlite_observation_error'])
                writer.rollback()


@unittest.skipUnless(os.name == 'posix', 'TERM/KILL/session boundary is POSIX')
class OwnedProbeTests(unittest.TestCase):
    def setUp(self):
        self.launched = []

    def tearDown(self):
        # All cleanup targets are Popen objects launched by this test.
        for process in self.launched:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def harness(self, path, mode='cooperate', deny_term=False):
        def launch(command, **kwargs):
            self.assertTrue(kwargs['start_new_session'])
            process = NATIVE_POPEN([sys.executable, '-c', HARMLESS_CHILD, str(path), mode], **kwargs)
            self.launched.append(process)
            wait_ready(path, process)
            if deny_term:
                process.terminate = unittest.mock.Mock(side_effect=PermissionError('controlled owned-child TERM denial'))
            return process
        return launch

    def probe(self, path, mode='cooperate', metrics=None, deny_term=False):
        task = {'preflight_refusals': [], 'source_sha256': 'frozen'}
        args = Namespace(rss_mib=1024, output_bytes=10**7, min_free_disk_bytes=1,
                         watchdog_seconds=5 if metrics else .1, stop_grace_seconds=.05, sample_seconds=.005)
        ordinary = {'rss_bytes': 1024, 'cpu_seconds': 0., 'disk_read_bytes': 0, 'disk_write_bytes': 0}
        with patch.object(benchmark, 'source_hash', return_value='frozen'), \
             patch.object(benchmark.subprocess, 'Popen', side_effect=self.harness(path, mode, deny_term)), \
             patch.object(benchmark, 'process_metrics', side_effect=metrics, return_value=ordinary), \
             patch.object(benchmark.os, 'killpg', side_effect=AssertionError('must never signal any group')):
            return benchmark.run_probe(path, task, args)

    def test_watchdog_term_reaps_only_owned_child_and_keeps_partial_sql(self):
        foreign = NATIVE_POPEN([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        self.launched.append(foreign)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.assertEqual(self.probe(path), 1)
            receipt = benchmark.read_json(path / 'measurement.json')
            self.assertEqual(receipt['status'], 'censored')
            self.assertEqual(receipt['watchdog_stop'], 'external_wall_guard')
            self.assertTrue(receipt['child_cleanup']['term_requested'])
            self.assertFalse(receipt['child_cleanup']['kill_requested'])
            self.assertTrue(receipt['child_cleanup']['reaped'])
            self.assertEqual(receipt['last_committed_day_end']['day'], 0)
            self.assertFalse(receipt['completed_entire_horizon'])
            self.assertTrue((path / 'term-handled').exists())
            self.assertIsNone(foreign.poll())
            launch = benchmark.read_json(path / 'launch.json')
            self.assertEqual(launch['monitor_pid'], os.getpid())
            self.assertEqual(launch['process_group_id'], launch['pid'])
            self.assertEqual(launch['session_id'], launch['pid'])

    def test_watchdog_escalates_to_kill_for_owned_term_ignorer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.assertEqual(self.probe(path, mode='ignore'), 1)
            receipt = benchmark.read_json(path / 'measurement.json')
            self.assertEqual(receipt['exit_code'], -signal.SIGKILL)
            self.assertTrue(receipt['child_cleanup']['kill_requested'])
            self.assertFalse(receipt['child_cleanup']['child_still_running'])
            self.assertFalse((path / 'term-handled').exists())

    def test_monitor_exception_keeps_samples_receipt_and_reaps_before_reraise(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'controlled monitor failure'):
                self.probe(path, metrics=[{'rss_bytes': 1024, 'cpu_seconds': 0.}, RuntimeError('controlled monitor failure')])
            receipt = benchmark.read_json(path / 'measurement.json')
            self.assertEqual(receipt['status'], 'censored')
            self.assertEqual(receipt['watchdog_stop'], 'monitor_exception')
            self.assertEqual(receipt['monitor_error']['type'], 'RuntimeError')
            self.assertTrue(receipt['child_cleanup']['reaped'])
            self.assertFalse(receipt['completed_entire_horizon'])
            self.assertTrue((path / 'process_samples.csv').read_text().startswith('wall_seconds,'))

    def test_denied_term_is_recorded_without_group_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.assertEqual(self.probe(path, deny_term=True), 1)
            receipt = benchmark.read_json(path / 'measurement.json')
            self.assertEqual(receipt['status'], 'censored')
            self.assertEqual(receipt['child_cleanup']['errors'][0]['type'], 'PermissionError')
            self.assertTrue(receipt['child_cleanup']['kill_requested'])
            self.assertTrue(receipt['child_cleanup']['reaped'])
            self.assertFalse(receipt['completed_entire_horizon'])

    def test_both_signals_denied_reports_live_owned_child_with_bounded_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            process = self.harness(path, 'ignore')([], start_new_session=True)
            begin = time.monotonic()
            with patch.object(process, 'terminate', side_effect=PermissionError('controlled TERM denial')), \
                 patch.object(process, 'kill', side_effect=PermissionError('controlled KILL denial')), \
                 patch.object(benchmark.os, 'killpg', side_effect=AssertionError('no foreign group fallback')):
                cleanup = benchmark.stop_owned_child(process, .01, kill_grace_seconds=.01)
            self.assertLess(time.monotonic() - begin, 1)
            self.assertTrue(cleanup['child_still_running'])
            self.assertFalse(cleanup['reaped'])
            self.assertIsNone(cleanup['exit_code'])
            self.assertEqual([e['operation'] for e in cleanup['errors']], ['terminate', 'kill', 'wait_after_kill'])
            process.kill()
            process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
