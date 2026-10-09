"""One finite, sequential Linux engineering validation invocation; no retry."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import time

from capabilities import ROOT, KIT, verify_pins

STAGES = (
    ('supervisor-routing', ['-m', 'unittest', 'discover', '-s', 'validation/journal_chunks', '-p', 'test_supervisor_routing.py', '-v'], 4),
    ('process-ownership', ['-m', 'unittest', 'discover', '-s', 'validation/journal_chunks', '-p', 'test_process_ownership.py', '-v'], 6),
    ('ledger', ['-m', 'unittest', 'discover', '-s', 'validation/journal_chunks', '-p', 'test_journal_chunks.py', '-v'], 9),
    ('legacy-model', ['-m', 'unittest', 'discover', '-s', 'validation/journal_chunks/legacy_tests', '-p', 'test_longitudinal_model.py', '-v'], 16),
    ('legacy-checkpoint', ['-m', 'unittest', 'discover', '-s', 'validation/journal_chunks/legacy_tests', '-p', 'test_longitudinal_checkpoint.py', '-v'], 9),
    ('legacy-benchmark', ['-m', 'unittest', 'discover', '-s', 'validation/journal_chunks/legacy_tests', '-p', 'test_longitudinal_benchmark.py', '-v'], 10),
    ('model-integration', ['validation/journal_chunks/model_controls.py'], None),
    ('scheduler-integration', ['validation/journal_chunks/scheduler_controls.py'], None),
)
WALL_PER_STAGE = 180
RSS_LIMIT = 1024 * 2**20
OUTPUT_LIMIT = 512 * 2**20
LOG_LIMIT = 2 * 2**20
FREE_FLOOR = 2**30


def file_usage(directory):
    total = 0
    for path in directory.rglob('*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            pass  # only transient fixture/SQLite observation; identity gates stay strict
    return total


def read_process(pid):
    """Return a birth/PPID-stable observation, or no observation after exit."""
    try:
        base = Path('/proc') / str(pid)
        before = (base / 'stat').read_text().rsplit(')', 1)[1].split()
        status = (base / 'status').read_text()
        children = tuple(map(int, (base / 'task' / str(pid) / 'children').read_text().split()))
        after = (base / 'stat').read_text().rsplit(')', 1)[1].split()
        if (before[19], before[1]) != (after[19], after[1]):
            return None
        rss = re.search(r'^VmRSS:\s+(\d+) kB$', status, re.M)
        return (before[19], int(before[1]), int(rss.group(1)) * 1024 if rss else 0, children)
    except FileNotFoundError:
        return None


def process_tree(pid, root_birth, seen):
    """Enroll only verified descendants; captured births are never replaced."""
    result = {}; pending = [(pid, root_birth, None, None)]
    while pending:
        current, expected_birth, parent, parent_birth = pending.pop()
        if current in result:
            continue
        root = read_process(pid)
        if root is None or root[0] != root_birth:
            break
        observed = read_process(current)
        if observed is None or (expected_birth is not None and observed[0] != expected_birth):
            continue
        if current in seen and seen[current][0] != observed[0]:
            continue
        if parent is not None:
            verified_parent = read_process(parent)
            if observed[1] != parent or verified_parent is None or verified_parent[0] != parent_birth:
                continue
            # Recheck the child after the parent: neither relationship nor
            # birth may have changed while reading the parent identity.
            checked = read_process(current)
            if checked is None or checked[:2] != observed[:2]:
                continue
            observed = checked
        result[current] = (observed[0], observed[2])
        seen.setdefault(current, result[current])
        pending.extend((child, None, current, observed[0]) for child in observed[3])
    return result


def stop_owned(process, root_birth, root_descriptor, seen):
    # Root descriptor was pinned immediately after Popen, before any poll/reap.
    # A reused root PID must neither replace its birth nor enroll descendants.
    if root_birth is not None:
        process_tree(process.pid, root_birth, seen)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            signal.pidfd_send_signal(root_descriptor, sig)
        except ProcessLookupError:
            pass
        for pid, (birth, _) in seen.items():
            if pid == process.pid:
                continue
            try:
                descriptor = os.pidfd_open(pid)
                try:
                    current = read_process(pid)
                    if current is not None and current[0] == birth:
                        signal.pidfd_send_signal(descriptor, sig)
                finally:
                    os.close(descriptor)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            continue
        time.sleep(.1)
    if process.poll() is None:
        signal.pidfd_send_signal(root_descriptor, signal.SIGKILL)
        process.wait(timeout=2)


def child_limits():
    resource.setrlimit(resource.RLIMIT_AS, (2 * 2**30, 2 * 2**30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 2**20, 128 * 2**20))
    resource.setrlimit(resource.RLIMIT_CPU, (180, 180))


def supervisor_preflight():
    """Refuse unavailable identity supervision before launching any child."""
    if sys.platform != 'linux':
        raise ValueError('this guarded runner requires actual Linux /proc evidence')
    if not callable(getattr(os, 'pidfd_open', None)) or not callable(getattr(signal, 'pidfd_send_signal', None)):
        raise ValueError('supervisor requires callable PIDfd open and signal APIs before Popen')
    if not Path('/proc').is_dir() or read_process(os.getpid()) is None:
        raise ValueError('supervisor requires actual readable /proc identity before Popen')
    descriptor = os.pidfd_open(os.getpid())
    os.close(descriptor)  # actual kernel capability, without sending a signal
    return {'runtime_label': 'system-supervisor', 'python_version': sys.version.split()[0],
            'platform': sys.platform, 'pidfd_open_available': True,
            'pidfd_send_signal_available': True, 'proc_identity_readable': True,
            'actual_pidfd_self_open': True, 'scientific_controls_executed_by_supervisor': False}


def control_executable(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('control Python must be an explicit absolute executable path')
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError('control Python must be an executable file')
    return str(resolved)


def control_command(executable, arguments):
    return [executable, *arguments]


def managed_capability(executable):
    completed = subprocess.run(control_command(executable, [str(KIT / 'capabilities.py'),
        '--label', 'uv-managed', '--required-managed']), cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        raise ValueError('explicit managed control interpreter capability failed: ' + completed.stderr[-2000:])
    value = json.loads(completed.stdout)
    if value['python_version'] != '3.14.2' or value['schema2_actual_control'] != 'PASS':
        raise ValueError('control interpreter must actually be Python3.14.2 with schema2 capability')
    return value


def main(control_python):
    supervisor = supervisor_preflight()
    executable = control_executable(control_python)
    capability = managed_capability(executable)
    print(json.dumps({'supervisor_capabilities': supervisor, 'managed_control_capabilities': capability}, sort_keys=True), flush=True)
    out = ROOT / 'runs' / 'journal-chunks' / 'linux-validation'
    out.mkdir(parents=True, exist_ok=False)
    temporary = out / 'temporary'; temporary.mkdir()
    env = dict(os.environ, PYTHONPATH=str(ROOT), TMPDIR=str(temporary), PYTHONDONTWRITEBYTECODE='1')
    records = []; started = time.monotonic()
    result = {'status': 'RUNNING', 'capabilities': capability, 'supervisor_capabilities': supervisor,
              'all_stage_control_interpreters': 'explicit-managed-Python3.14.2', 'engineering_controls_only': True,
              'accepted_scientific_worlds_added': 0, 'GCP_called': False,
              'large_population_or_full_pipeline_admission': False,
              'bounds': {'stage_wall_seconds': WALL_PER_STAGE, 'tree_rss_bytes': RSS_LIMIT,
                         'runs_bytes': OUTPUT_LIMIT, 'stage_log_bytes': LOG_LIMIT, 'free_floor_bytes': FREE_FLOOR,
                         'per_process_address_bytes': 2 * 2**30, 'per_file_bytes': 128 * 2**20}, 'stages': records}
    try:
        for name, arguments, expected_count in STAGES:
            verify_pins()
            log = out / (name + '.log'); begin = time.monotonic(); seen = {}; peak_rss = 0
            with log.open('xb') as output:
                process = subprocess.Popen(control_command(executable, arguments), cwd=ROOT, env=env,
                    stdout=output, stderr=subprocess.STDOUT, preexec_fn=child_limits)
                root_descriptor = os.pidfd_open(process.pid)
                root_birth = None
                refusal = None
                try:
                    root = read_process(process.pid)
                    if root is None:
                        raise RuntimeError('owned invocation initial birth unavailable')
                    root_birth = root[0]
                    seen[process.pid] = (root_birth, root[2])
                    while process.poll() is None:
                        tree = process_tree(process.pid, root_birth, seen)
                        peak_rss = max(peak_rss, sum(value[1] for value in tree.values()))
                        if time.monotonic() - begin > WALL_PER_STAGE: refusal = 'wall'
                        elif peak_rss > RSS_LIMIT: refusal = 'RSS'
                        elif log.stat().st_size > LOG_LIMIT: refusal = 'log'
                        elif file_usage(ROOT / 'runs') > OUTPUT_LIMIT: refusal = 'output'
                        elif shutil.disk_usage(ROOT).free < FREE_FLOOR: refusal = 'free-space'
                        if refusal:
                            raise RuntimeError('owned test invocation guard: ' + refusal)
                        time.sleep(.2)
                    if process.returncode != 0:
                        stop_owned(process, root_birth, root_descriptor, seen)
                except BaseException:
                    stop_owned(process, root_birth, root_descriptor, seen)
                    raise
                finally:
                    os.close(root_descriptor)
            if log.stat().st_size > LOG_LIMIT:
                raise RuntimeError('final test log exceeds bound')
            raw = log.read_bytes(); text = raw.decode('utf-8', errors='replace')
            print('TEST_LOG_BEGIN ' + name, flush=True); print(text, end='', flush=True); print('TEST_LOG_END ' + name, flush=True)
            count = re.search(r'Ran (\d+) tests? in', text)
            skip = re.search(r'OK \(skipped=(\d+)\)', text)
            record = {'name': name, 'exit_code': process.returncode, 'wall_seconds': time.monotonic() - begin,
                      'observed_peak_process_tree_rss_bytes': peak_rss, 'log_bytes': len(raw),
                      'log_sha256': hashlib.sha256(raw).hexdigest(), 'guard_failed': refusal}
            if expected_count is not None:
                record.update({'registered_tests': int(count.group(1)) if count else None,
                               'skipped_tests': int(skip.group(1)) if skip else 0})
                if record['registered_tests'] != expected_count:
                    raise AssertionError('legacy/ledger test inventory differs: ' + name)
            records.append(record)
            if process.returncode != 0:
                raise RuntimeError('test invocation failed: ' + name)
            verify_pins()
        result.update({'status': 'PASS', 'identity_after': verify_pins(), 'wall_seconds': time.monotonic() - started})
    except BaseException as error:
        result.update({'status': 'FAIL', 'error_type': type(error).__name__, 'error': str(error),
                       'wall_seconds': time.monotonic() - started})
        (out / 'result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
        print(json.dumps(result, sort_keys=True), flush=True)
        raise
    (out / 'result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--control-python', required=True)
    options = parser.parse_args()
    main(options.control_python)
