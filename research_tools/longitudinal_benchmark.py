"""Bounded, process-isolated resource probes of complete longitudinal configs.

These are hardware probes, not a shortened scientific study. The requested
calendar, adoption, lifecycle, modules and tail remain in every child config.
A censored/refused probe provides no completed-horizon certification. No cloud
API, paid resource or remote upload is used by this tool.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import ctypes
import dataclasses
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dams_sim.config import Config
from dams_sim.longitudinal import estimate_longitudinal
from dams_sim.longitudinal_design import LONGITUDINAL_NAMES, resolve_longitudinal_spec, world_plan
from dams_sim.storage import atomic_csv, atomic_json, file_digest, provenance, source_hash
from research_tools.benchmark import RusageV2, process_io


class MachTimebaseInfo(ctypes.Structure):
    _fields_ = [('numer', ctypes.c_uint32), ('denom', ctypes.c_uint32)]


@lru_cache(maxsize=1)
def mach_timebase():
    """Convert the host's Mach absolute-time counters, not assumed nanoseconds.

    XNU fill_task_rusage copies task_power_info's Mach-time totals into
    ri_user_time/ri_system_time. The scale varies by architecture and host.
    """
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    lib.mach_timebase_info.argtypes = [ctypes.POINTER(MachTimebaseInfo)]
    lib.mach_timebase_info.restype = ctypes.c_int
    info = MachTimebaseInfo()
    if lib.mach_timebase_info(ctypes.byref(info)) != 0 or not info.numer or not info.denom:
        raise OSError('Mach CPU timebase unavailable')
    return info.numer, info.denom


def mach_cpu_seconds(ticks, numer, denom):
    if type(ticks) is not int or ticks < 0 or type(numer) is not int or type(denom) is not int or numer <= 0 or denom <= 0:
        raise ValueError('invalid Mach CPU counter/timebase')
    return ticks * numer / denom / 1_000_000_000


def process_metrics(pid):
    value = process_io(pid)
    if value is None:
        return None
    value = dict(value)
    value['cpu_seconds'] = None
    if sys.platform == 'darwin':
        try:
            lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
            data = RusageV2()
            lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            lib.proc_pid_rusage.restype = ctypes.c_int
            if lib.proc_pid_rusage(pid, 2, ctypes.byref(data)) == 0:
                numer, denom = mach_timebase()
                value.update(cpu_seconds=mach_cpu_seconds(data.ri_user_time + data.ri_system_time, numer, denom),
                             cpu_counter_basis='mach_absolute_time',
                             cpu_user_ticks=data.ri_user_time, cpu_system_ticks=data.ri_system_time,
                             cpu_timebase_numer=numer, cpu_timebase_denom=denom)
        except (OSError, AttributeError, ValueError) as error:
            value['cpu_measurement_error'] = type(error).__name__
    elif sys.platform.startswith('linux'):
        try:
            # The command name may itself contain spaces or parentheses.
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            value['cpu_seconds'] = (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')
            value['cpu_counter_basis'] = 'linux_proc_stat_clock_ticks'
        except (OSError, ValueError, IndexError):
            pass
    return value


def tree_bytes(path):
    total = 0
    for item in path.rglob('*'):
        try:
            if item.is_file():
                total += item.stat().st_size
        except FileNotFoundError:
            # Atomic temporary files can disappear between enumeration/stat.
            # This allowance is only for observational disk usage, not hashes.
            continue
    return total


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def ledger_observation(run):
    """Observe committed SQL only; this is not a recoverable full-state claim."""
    working = run / '.longitudinal-working.sqlite'
    value = {'working_sqlite_bytes': working.stat().st_size if working.exists() else None,
             'journal_rows': None, 'last_committed_day_end': None,
             'sqlite_observation_error': None}
    if working.exists():
        try:
            with closing(sqlite3.connect(f'file:{working.resolve()}?mode=ro', uri=True, timeout=1)) as connection:
                value['journal_rows'] = connection.execute('SELECT count(*) FROM journal').fetchone()[0]
                item = connection.execute("SELECT day,payload FROM journal WHERE kind='day_end' ORDER BY seq DESC LIMIT 1").fetchone()
                if item:
                    value['last_committed_day_end'] = {'day': item[0], 'payload': json.loads(item[1])}
        except (sqlite3.DatabaseError, ValueError) as error:
            # A failed day/killed writer is evidence, never a complete ledger.
            value['sqlite_observation_error'] = {'type': type(error).__name__, 'message': str(error)}
    return value


def process_identity(pid):
    value = {'pid': pid}
    for name, reader in (('process_group_id', getattr(os, 'getpgid', None)),
                         ('session_id', getattr(os, 'getsid', None))):
        try:
            value[name] = reader(pid) if reader else None
        except OSError as error:
            value[name] = None
            value[name + '_error'] = {'type': type(error).__name__, 'message': str(error)}
    return value


def stop_owned_child(process, grace_seconds, *, kill_grace_seconds=2):
    """Stop only this unreaped Popen child; never interpret its PID as a PGID.

    The benchmark child does not spawn workers. Popen's signal methods poll the
    owned child before signalling, avoiding an already reaped/reused PID. A
    denied signal is recorded, followed by a bounded escalation/wait; no other
    process or group is targeted to compensate for that denial.
    """
    value = {'pid': process.pid, 'signal_target': 'owned_unreaped_popen_child_only',
             'term_requested': False, 'kill_requested': False, 'errors': []}
    if process.poll() is None:
        try:
            process.terminate()
            value['term_requested'] = True
        except OSError as error:
            value['errors'].append({'operation': 'terminate', 'type': type(error).__name__, 'message': str(error)})
        try:
            process.wait(timeout=grace_seconds)
        except (subprocess.TimeoutExpired, OSError) as error:
            if isinstance(error, OSError):
                value['errors'].append({'operation': 'wait_after_terminate', 'type': type(error).__name__, 'message': str(error)})
            try:
                process.kill()
                value['kill_requested'] = True
            except OSError as error:
                value['errors'].append({'operation': 'kill', 'type': type(error).__name__, 'message': str(error)})
            try:
                process.wait(timeout=kill_grace_seconds)
            except (subprocess.TimeoutExpired, OSError) as error:
                value['errors'].append({'operation': 'wait_after_kill', 'type': type(error).__name__,
                                        'message': 'owned child did not exit within the bounded cleanup wait' if isinstance(error, subprocess.TimeoutExpired) else str(error)})
    value['exit_code'] = process.poll()
    value['child_still_running'] = value['exit_code'] is None
    value['reaped'] = not value['child_still_running']
    return value


def task_config(args):
    spec = resolve_longitudinal_spec(args.spec, args.scale)
    base = spec.base(max_events=10**12)
    _, children = world_plan(spec, base, args.world)
    available = {tags['arm_id']: (config, tags) for config, tags in children}
    if args.arm not in available:
        raise ValueError(f'arm is not in declared spec: {args.arm}')
    original, tags = available[args.arm]
    estimate = estimate_longitudinal(original)
    # Resource allowances change, scientific fields do not.
    config = dataclasses.replace(original, max_events=estimate['work_events'],
                                 max_wall_seconds=args.world_seconds,
                                 max_rss_mb=args.rss_mib,
                                 max_output_mb=args.output_bytes / 1_000_000).validate()
    return spec, config, tags, estimate


def child(path):
    from dams_sim.cli import run_world
    task = read_json(path / 'task.json')
    atomic_json(path / 'child-process.json', {**process_identity(os.getpid()),
                                             'parent_pid': os.getppid(),
                                             'python': sys.executable,
                                             'source_sha256': source_hash()})
    if task['source_sha256'] != source_hash():
        atomic_json(path / 'child.json', {'status': 'source_refused', 'exit_code': 2})
        return 2
    config = Config.from_dict(task['config'])
    run = path / 'world'
    run.mkdir()
    stop = False

    def request_stop(signum, frame):
        nonlocal stop
        stop = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, request_stop)
    begin, cpu = time.monotonic(), time.process_time()
    error = None
    try:
        result = run_world(config, run,
                           checkpoint_interval_days=task['checkpoint_interval_days'],
                           checkpoint_interval_seconds=task['checkpoint_interval_seconds'],
                           stop_requested=lambda: stop)
        status = result['status']
    except Exception as failure:
        status = 'failed'
        error = {'type': type(failure).__name__, 'message': str(failure)}
    usage = resource.getrusage(resource.RUSAGE_SELF)
    peak = usage.ru_maxrss if sys.platform != 'darwin' else usage.ru_maxrss / 1024
    manifest = read_json(run / 'manifest.json') or {}
    summary = read_json(run / 'summary.json')
    days = summary['days_completed'] if summary else manifest.get('days_completed', 0)
    committed = ledger_observation(run)
    snapshots = read_json(run / 'checkpoint-index.json')
    measurement = {'status': status, 'exit_code': 0 if status == 'complete' else 1,
                   'error': error, 'source_sha256': task['source_sha256'],
                   'config_sha256': manifest.get('config_sha256'),
                   'requested_calendar_days': config.days, 'completed_calendar_days': days,
                   'completed_entire_horizon': status == 'complete' and days == config.days,
                   'child_wall_seconds': time.monotonic() - begin,
                   'child_cpu_seconds': time.process_time() - cpu,
                   'whole_child_peak_rss_mib': peak / 1024,
                   'retained_output_bytes': tree_bytes(run),
                   **committed,
                   'checkpoint_index': snapshots,
                   'summary': summary, 'manifest': manifest,
                   'checkpoint_io_included_in_simulation': True,
                   'cpu_parallelism': 1, 'scientific_mc_worlds_added': 0}
    atomic_json(path / 'child.json', measurement)
    return measurement['exit_code']


def run_probe(path, task, args):
    atomic_json(path / 'task.json', task)
    if task['preflight_refusals']:
        value = {'status': 'preflight_refused', 'reasons': task['preflight_refusals'],
                 'completed_entire_horizon': False, 'model_constructed': False}
        atomic_json(path / 'measurement.json', value)
        return 2
    if source_hash() != task['source_sha256']:
        raise RuntimeError('source changed after plan; no child started')
    env = os.environ.copy()
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        env[key] = '1'
    command = [sys.executable, str(Path(__file__).resolve()), '_child', '--output', str(path)]
    start = time.monotonic()
    observations = []
    stop_reason = None
    monitor_error = None
    failure = None
    cleanup = None
    with (path / 'stdout.log').open('w') as out, (path / 'stderr.log').open('w') as err:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=err,
                                   start_new_session=True)
        try:
            atomic_json(path / 'launch.json', {**process_identity(process.pid),
                                              'monitor_pid': os.getpid(), 'command': command,
                                              'start_new_session': True,
                                              'signal_target': 'owned_unreaped_popen_child_only',
                                              'python': sys.executable, 'load_average': list(os.getloadavg())})
            print(json.dumps({'pid': process.pid, 'output': str(path)}), flush=True)
            missing = 0
            while process.poll() is None:
                metrics = process_metrics(process.pid)
                used = tree_bytes(path / 'world')
                free = shutil.disk_usage(path).free
                elapsed = time.monotonic() - start
                row = {'wall_seconds': elapsed, 'output_bytes': used, 'free_disk_bytes': free,
                       **(metrics or {})}
                observations.append(row)
                missing = 0 if metrics else missing + 1
                if source_hash() != task['source_sha256']:
                    stop_reason = 'source_changed_during_probe'
                elif metrics and metrics['rss_bytes'] > args.rss_mib * 1024**2:
                    stop_reason = 'external_rss_guard'
                elif used > args.output_bytes:
                    stop_reason = 'external_output_guard'
                elif free < args.min_free_disk_bytes:
                    stop_reason = 'external_free_disk_guard'
                elif elapsed > args.watchdog_seconds:
                    stop_reason = 'external_wall_guard'
                elif missing >= 8:
                    stop_reason = 'process_resource_measurement_unavailable'
                if stop_reason:
                    break
                time.sleep(args.sample_seconds)
        except BaseException as error:
            failure = error
            monitor_error = {'type': type(error).__name__, 'message': str(error)}
            stop_reason = stop_reason or 'monitor_exception'
        finally:
            # Resource breach uses a short grace to avoid additional growth;
            # the time guard allows a completed-day snapshot to finish.
            grace = args.stop_grace_seconds if stop_reason == 'external_wall_guard' else 2
            cleanup = stop_owned_child(process, grace)
        exit_code = cleanup['exit_code']
    elapsed = time.monotonic() - start
    receipt_errors = []

    def observe(operation, reader, default=None):
        try:
            return reader()
        except Exception as error:
            receipt_errors.append({'operation': operation, 'type': type(error).__name__, 'message': str(error)})
            return default

    observe('write_process_samples', lambda: atomic_csv(path / 'process_samples.csv', observations))
    measured = observe('read_child_measurement', lambda: read_json(path / 'child.json'))
    if measured is not None and not isinstance(measured, dict):
        receipt_errors.append({'operation': 'read_child_measurement', 'type': 'ValueError',
                               'message': 'child measurement is not an object'})
        measured = None
    unchanged = observe('source_hash_after_exit', lambda: source_hash() == task['source_sha256'])
    retained = observe('retained_output_bytes', lambda: tree_bytes(path / 'world'))
    committed = observe('read_committed_ledger', lambda: ledger_observation(path / 'world'), {})
    if unchanged is not True:
        stop_reason = stop_reason or 'source_identity_after_exit_unconfirmed'
    if cleanup['errors'] or cleanup['child_still_running'] or receipt_errors:
        stop_reason = stop_reason or 'cleanup_or_receipt_incomplete'
    complete = exit_code == 0 and not stop_reason and bool(measured and measured.get('completed_entire_horizon'))
    value = {'status': 'complete' if complete else 'censored' if stop_reason else 'failed',
             'exit_code': exit_code, 'watchdog_stop': stop_reason,
             'monitor_error': monitor_error, 'child_cleanup': cleanup, 'receipt_errors': receipt_errors,
             'spawn_to_exit_wall_seconds': elapsed, 'sample_interval_seconds': args.sample_seconds,
             'source_unchanged': unchanged,
             'sampled_peak_rss_bytes': max((x.get('rss_bytes', 0) for x in observations), default=None),
             'sampled_last_cpu_seconds': next((x.get('cpu_seconds') for x in reversed(observations) if x.get('cpu_seconds') is not None), None),
             'sampled_disk_read_bytes': max((x.get('disk_read_bytes', 0) for x in observations), default=None),
             'sampled_disk_write_bytes': max((x.get('disk_write_bytes', 0) for x in observations), default=None),
             'retained_output_bytes': retained, 'child_measurement': measured,
             **committed,
             'completed_entire_horizon': complete,
             'hardware_certified_beyond_measured_config': False,
             'other_user_apps_stopped': False, 'os_page_cache_flushed': False,
             'interpretation': 'One fresh interpreter/direct complete trajectory; no paired-study branch comparison, no confirmation sample. A resource stop retains full requested config and partial evidence.'}
    atomic_json(path / 'measurement.json', value)
    if failure is not None:
        raise failure
    return 0 if value['completed_entire_horizon'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'run', '_child'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--spec', choices=LONGITUDINAL_NAMES, default=LONGITUDINAL_NAMES[0])
    parser.add_argument('--scale', type=int, default=1000)
    parser.add_argument('--arm', default='startup-founding')
    parser.add_argument('--world', type=int, default=90000)
    parser.add_argument('--world-seconds', type=float, default=120)
    parser.add_argument('--watchdog-seconds', type=float, default=180)
    parser.add_argument('--stop-grace-seconds', type=float, default=15)
    parser.add_argument('--rss-mib', type=float, default=1024)
    parser.add_argument('--output-bytes', type=int, default=2 * 1024**3)
    parser.add_argument('--min-free-disk-bytes', type=int, default=1024**3)
    parser.add_argument('--checkpoint-interval-days', type=int, default=90)
    parser.add_argument('--checkpoint-interval-seconds', type=float, default=60)
    parser.add_argument('--sample-seconds', type=float, default=.25)
    parser.add_argument('--allow-resource-censoring', action='store_true',
                        help='Permit forecast-over-budget probe with unchanged horizon; never a promise of full completion.')
    args = parser.parse_args(argv)
    if args.mode == '_child':
        return child(args.output.resolve())
    for key in ('world_seconds', 'watchdog_seconds', 'stop_grace_seconds', 'rss_mib',
                'checkpoint_interval_seconds', 'sample_seconds'):
        if not math.isfinite(getattr(args, key)) or getattr(args, key) <= 0:
            parser.error(f'{key} must be finite and positive')
    if not 1 <= args.checkpoint_interval_days <= 50000 or args.output_bytes < 100000 or args.min_free_disk_bytes < 100000:
        parser.error('invalid disk/checkpoint bound')
    if args.watchdog_seconds <= args.world_seconds:
        parser.error('watchdog must allow the internal attempt timeout to report first')
    path = args.output.resolve()
    path.mkdir(parents=True, exist_ok=False)
    try:
        spec, config, tags, estimate = task_config(args)
        free = shutil.disk_usage(path).free
        refusals = []
        if free < args.output_bytes + args.min_free_disk_bytes:
            refusals.append('free disk cannot reserve maximum retained output and explicit headroom')
        if not args.allow_resource_censoring and estimate['estimated_output_bytes'] > args.output_bytes:
            refusals.append('unvalidated full-horizon output forecast exceeds probe allocation')
        if not args.allow_resource_censoring and estimate['estimated_peak_rss_bytes'] > args.rss_mib * 1024**2:
            refusals.append('unvalidated full-horizon RSS forecast exceeds probe allocation')
        current = provenance()
        expected_source = source_hash()
        source = path / 'source' / 'dams_sim'
        source.mkdir(parents=True)
        for item in sorted((ROOT / 'dams_sim').glob('*.py')):
            shutil.copy2(item, source / item.name)
        captured_source = hashlib.sha256(b''.join(x.name.encode()+b'\0'+x.read_bytes()
                                                  for x in sorted(source.glob('*.py')))).hexdigest()
        if captured_source != expected_source or source_hash() != expected_source:
            raise RuntimeError('source changed during snapshot; no valid plan/child produced')
        task = {'spec': spec.to_dict(), 'config': config.to_dict(), 'declared_arm_tags': tags,
                'resource_estimate': estimate, 'source_sha256': expected_source,
                'source_files': {x.name: file_digest(x) for x in sorted(source.glob('*.py'))},
                'tool_sha256': file_digest(Path(__file__)), 'provenance': current,
                'checkpoint_interval_days': args.checkpoint_interval_days,
                'checkpoint_interval_seconds': args.checkpoint_interval_seconds,
                'bounds': vars(args) | {'output': str(path)},
                'preflight_free_disk_bytes': free, 'preflight_refusals': refusals,
                'host': {'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
                         'affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
                         'load_average': list(os.getloadavg()) if hasattr(os, 'getloadavg') else None,
                         'python': platform.python_version(), 'python_executable': sys.executable,
                         'other_user_apps_stopped': False, 'os_page_cache_flushed': False},
                'source_status': 'candidate unless independently frozen; no relabel of old measurements',
                'allow_resource_censoring': args.allow_resource_censoring,
                'scientific_horizon_changed': False,
                'direct_from_founding_conditions': True,
                'not_a_paired_scientific_study': True}
        atomic_json(path / 'task.json', task)
        if args.mode == 'plan':
            print(json.dumps({'output': str(path), 'model_started': False, 'refusals': refusals,
                              'estimate': estimate}, indent=2))
            return 0
        return run_probe(path, task, args)
    except BaseException as error:
        atomic_json(path / 'tool_failure.json', {'type': type(error).__name__, 'message': str(error),
                                               'status': 'failed', 'no_completion_claim': True})
        raise


if __name__ == '__main__':
    raise SystemExit(main())
