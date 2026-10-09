"""Frozen compute-only guest supervision; no object store or upload services.

The coordinator owns provider expiry, hardware/image readback and IAP transfer.
This worker runs the existing scientific pipeline exactly once. A lost control
lease stops computation; it never renews the frozen scientific/node deadlines.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

from cloud_worker import guest_metadata, instance_metadata, verify_guest, wait_pipeline_group_absent
from cloud_control import GuardError, utc, stamp
from dams_sim.runtime import RuntimeLimits

SCHEMA = 'DAMS-compute-only-guest-1'
JSON_BOUND = 4 * 1024**2
RUNTIME_FIELDS = {'schema', 'source_commit', 'source_manifest_sha256', 'spec', 'scale',
                  'runtime_limits', 'purchase_mode', 'machine_type', 'expected_guest',
                  'expected_identity', 'provider_identity_sha256', 'deadline_utc',
                  'watchdog_shutdown_utc', 'pipeline_stop_grace_seconds',
                  'controller_lease_timeout_seconds', 'transfer_config_sha256'}


def require(ok, reason):
    if not ok:
        raise GuardError(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate configuration key')
        result[key] = value
    return result


def parse(data):
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise GuardError('invalid bounded JSON') from None


def anchor(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_nlink)


def lexical(path):
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        info = os.lstat(current)
        require(not stat.S_ISLNK(info.st_mode), 'symlinked guest path')
        if current != path:
            require(stat.S_ISDIR(info.st_mode), 'invalid guest path ancestor')
    return path


class Directory:
    """Existing trusted namespace only; held FD with lexical ancestor guards."""
    def __init__(self, path):
        self.path = lexical(path)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.initial = os.fstat(self.fd)
        self.check()

    def check(self):
        lexical(self.path)
        info = os.stat(self.path, follow_symlinks=False)
        require((info.st_dev, info.st_ino) == (self.initial.st_dev, self.initial.st_ino),
                'guest directory namespace changed')

    def read(self, name, bound=JSON_BOUND):
        require(isinstance(name, str) and name and not name.startswith('/')
                and all(p not in ('', '.', '..') for p in name.split('/')), 'unsafe guest file path')
        self.check()
        path = lexical(self.path / name)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and before.st_size <= bound, 'guest file is not bounded/private/regular')
            data = bytearray()
            while True:
                block = os.read(fd, min(65536, bound + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                require(len(data) <= bound, 'guest file exceeds bound')
            require(anchor(before) == anchor(os.fstat(fd))
                    == anchor(os.stat(path, follow_symlinks=False)), 'guest file changed during read')
            self.check()
            return bytes(data), anchor(before)
        finally:
            os.close(fd)

    def write(self, name, value, *, exclusive=False):
        require('/' not in name and name not in ('', '.', '..'), 'unsafe guest receipt name')
        self.check()
        data = canonical(value)
        require(len(data) <= JSON_BOUND, 'guest receipt exceeds bound')
        temporary = '.receipt-' + str(time.time_ns())
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self.check()
            if exclusive:
                os.link(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd,
                        follow_symlinks=False)
                os.unlink(temporary, dir_fd=self.fd)
            else:
                try:
                    info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                            'unsafe existing guest receipt')
                except FileNotFoundError:
                    pass
                os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
            actual, _ = self.read(name)
            require(actual == data, 'guest receipt publication changed')
            self.check()
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def close(self):
        os.close(self.fd)


@contextmanager
def directory(path):
    item = Directory(path)
    try:
        yield item
    finally:
        item.close()


@contextmanager
def single_run(work):
    work.check()
    fd = os.open('compute.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                 0o600, dir_fd=work.fd)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'invalid compute lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GuardError('compute pipeline already owned') from None
        require(not os.path.lexists(work.path / 'process.json'),
                'compute execution already attempted; automatic restart refused')
        yield
    finally:
        os.close(fd)


class Clock:
    """A backwards wall-clock adjustment cannot extend the admitted lifetime."""
    def __init__(self):
        self.wall, self.monotonic = time.time(), time.monotonic()

    def now(self):
        return max(time.time(), self.wall + time.monotonic() - self.monotonic)


def validate_runtime(value):
    require(isinstance(value, dict) and set(value) == RUNTIME_FIELDS
            and value['schema'] == SCHEMA, 'unsupported compute-only runtime')
    for key in ('source_manifest_sha256', 'provider_identity_sha256', 'transfer_config_sha256'):
        require(isinstance(value[key], str) and re.fullmatch('[a-f0-9]{64}', value[key]),
                'invalid frozen SHA')
    require(isinstance(value['source_commit'], str)
            and re.fullmatch('[a-f0-9]{40}', value['source_commit']), 'invalid frozen source commit')
    require(value['spec'] in ('longitudinal-adoption-5y', 'longitudinal-adoption-10y')
            and type(value['scale']) is int and value['scale'] >= 2, 'invalid frozen scientific selection')
    for key in ('pipeline_stop_grace_seconds', 'controller_lease_timeout_seconds'):
        require(type(value[key]) is int and 1 <= value[key] <= 86400, 'invalid compute-only time bound')
    require(all(isinstance(value[k], str) for k in ('deadline_utc', 'watchdog_shutdown_utc')),
            'invalid compute-only absolute time')
    identity = value['expected_identity']
    require(isinstance(identity, dict) and set(identity) == {
        'instance_id', 'zone', 'boot_disk_device_name', 'guest_os_release_sha256', 'boot_disk_bytes_min'},
        'incomplete frozen guest identity')
    require(all(isinstance(identity[k], str) and 0 < len(identity[k]) <= 128
                for k in ('instance_id', 'zone', 'boot_disk_device_name'))
            and isinstance(identity['guest_os_release_sha256'], str)
            and re.fullmatch('[a-f0-9]{64}', identity['guest_os_release_sha256'])
            and type(identity['boot_disk_bytes_min']) is int and identity['boot_disk_bytes_min'] > 0,
            'invalid frozen guest identity')
    limits = RuntimeLimits.from_dict(value['runtime_limits'])
    soft, hard = limits.deadline, limits.stop_cutoff
    watchdog, expiry = utc(value['watchdog_shutdown_utc']).timestamp(), utc(value['deadline_utc']).timestamp()
    require(soft + value['pipeline_stop_grace_seconds'] <= hard < watchdog <= expiry,
            'compute stop bounds cannot extend node expiry')
    require(limits.cooperative_stop_grace_seconds + 5 <= value['pipeline_stop_grace_seconds'],
            'pipeline grace cannot cover cooperative stop and reaping')
    require(bool(value['expected_guest']), 'compute-only requires full guest hardware expectations')
    require(limits.batch_max_output_bytes > 0 and limits.batch_max_output_files > 0,
            'compute-only requires bounded raw bytes and files')
    return value


def validate_package(root, runtime):
    data, manifest_anchor = root.read('source-manifest.json')
    require(sha(data) == runtime['source_manifest_sha256'], 'frozen source manifest differs')
    manifest = parse(data)
    require(isinstance(manifest, dict) and set(manifest) == {'commit', 'source_files_sha256'}
            and manifest['commit'] == runtime['source_commit'], 'invalid frozen source package')
    files = manifest['source_files_sha256']
    required = {'run.sh', 'uv.lock', 'pyproject.toml', 'research_tools/compute_only_worker.py',
                'research_tools/cloud_worker.py', 'research_tools/cloud_control.py',
                'research_tools/cloud_archive.py', 'cloud/compute-only-services.sh'}
    core = {p.relative_to(root.path).as_posix() for p in (root.path / 'dams_sim').glob('*.py')}
    require(isinstance(files, dict) and core and required | core <= set(files), 'source package omits runtime files')
    observations = {'source-manifest.json': (sha(data), manifest_anchor)}
    for name, expected in files.items():
        require(isinstance(expected, str) and re.fullmatch('[a-f0-9]{64}', expected), 'invalid source file SHA')
        content, initial = root.read(name, 64 * 1024**2)
        require(sha(content) == expected, 'frozen source file differs')
        observations[name] = (expected, initial)
    return observations


def check_files(root, observations):
    root.check()
    require({p.relative_to(root.path).as_posix() for p in (root.path / 'dams_sim').glob('*.py')}
            == {name for name in observations if name.startswith('dams_sim/') and name.count('/') == 1
                and name.endswith('.py')}, 'core module roster changed')
    for name, (_, initial) in observations.items():
        path = lexical(root.path / name)
        require(anchor(os.stat(path, follow_symlinks=False)) == initial, 'frozen guest input changed')


def verify_executing_source(root, observations):
    """The checked package must be the code actually loaded by this service."""
    modules = {'research_tools/compute_only_worker.py': Path(__file__),
               'research_tools/cloud_worker.py': Path(sys.modules['cloud_worker'].__file__),
               'research_tools/cloud_control.py': Path(sys.modules['cloud_control'].__file__),
               'research_tools/cloud_archive.py': Path(sys.modules['cloud_archive'].__file__),
               'dams_sim/runtime.py': Path(sys.modules['dams_sim.runtime'].__file__)}
    for name, actual in modules.items():
        require(lexical(actual) == root.path / name and name in observations,
                'executing module is outside frozen source package')
    check_files(root, observations)


def lease(work, runtime_sha, timeout, now, previous=None):
    data, _ = work.read('controller-heartbeat.json')
    value = parse(data)
    require(isinstance(value, dict) and set(value) == {'schema', 'runtime_sha256', 'sequence', 'observed_utc'}
            and value['schema'] == 'DAMS-compute-controller-lease-1'
            and value['runtime_sha256'] == runtime_sha and type(value['sequence']) is int
            and value['sequence'] >= 0 and isinstance(value['observed_utc'], str), 'invalid controller lease')
    observed = utc(value['observed_utc']).timestamp()
    require(now - timeout <= observed <= now + 5, 'controller lease expired or future dated')
    if previous is not None:
        require(value['sequence'] >= previous['sequence'], 'controller lease rolled back')
        if value['sequence'] == previous['sequence']:
            require(value == previous, 'controller lease changed without sequence advancement')
        else:
            require(observed >= utc(previous['observed_utc']).timestamp(), 'controller lease time rolled back')
    return value


def measured_guest():
    value = guest_metadata()
    value['boot_disk_device_name'] = instance_metadata('disks/0/device-name')
    value['guest_os_release_sha256'] = sha(Path('/etc/os-release').read_bytes())
    disk = os.statvfs('/')
    value['guest_root_filesystem_bytes'] = disk.f_frsize * disk.f_blocks
    return value


def verify_identity(runtime, measurements):
    verification = verify_guest(runtime, measurements)
    require(verification['hardware_frozen_verified'], 'guest hardware was not fully verified')
    expected = runtime['expected_identity']
    for name in ('instance_id', 'zone', 'boot_disk_device_name', 'guest_os_release_sha256'):
        require(measurements.get(name) == expected[name], 'guest identity differs from frozen readback')
    require(type(measurements.get('guest_root_filesystem_bytes')) is int
            and measurements['guest_root_filesystem_bytes'] >= expected['boot_disk_bytes_min'],
            'guest root filesystem is smaller than frozen disk allowance')
    return {**verification, 'instance_id': measurements['instance_id'], 'zone': measurements['zone'],
            'boot_disk_device_name': measurements['boot_disk_device_name'],
            'guest_os_release_sha256': measurements['guest_os_release_sha256'],
            'guest_root_filesystem_bytes': measurements['guest_root_filesystem_bytes'],
            'provider_identity_sha256': runtime['provider_identity_sha256'],
            'image_and_provider_disk_id_verified_by': 'separate coordinator provider receipt SHA'}


def service_timeout(runtime, *, now=None):
    validate_runtime(runtime)
    remaining = utc(runtime['watchdog_shutdown_utc']).timestamp() - (time.time() if now is None else now)
    require(remaining >= 1, 'cannot install expired compute-only service')
    return max(1, min(runtime['pipeline_stop_grace_seconds'] + 10, math.floor(remaining)))


def run_compute(runtime_path, root_path, work_path):
    clock = Clock()
    with directory(work_path) as work, directory(root_path) as root:
        runtime_path = lexical(runtime_path)
        require(runtime_path.parent == work.path, 'runtime must be in the owned work namespace')
        data, initial = work.read(runtime_path.name)
        runtime = validate_runtime(parse(data))
        runtime_sha = sha(data)
        soft = utc(runtime['runtime_limits']['deadline_utc']).timestamp()
        hard = utc(runtime['runtime_limits']['stop_cutoff_utc']).timestamp()
        require(clock.now() < soft, 'scientific deadline already elapsed')
        with single_run(work):
            package = validate_package(root, runtime)
            verify_executing_source(root, package)
            transfer, transfer_anchor = work.read('transfer-config.json')
            require(sha(transfer) == runtime['transfer_config_sha256'], 'frozen transfer configuration differs')
            control = lease(work, runtime_sha, runtime['controller_lease_timeout_seconds'], clock.now())
            verification = verify_identity(runtime, measured_guest())
            limits = {**runtime['runtime_limits'], 'provenance': {
                **runtime['runtime_limits'].get('provenance', {}),
                'instance_id': verification['instance_id'], 'zone': verification['zone'],
                'machine_type': verification['machine_type'], 'packaged_commit': runtime['source_commit'],
                'environment': 'GCP'}}
            work.write('runtime-limits.json', limits, exclusive=True)
            output = work.path / 'output'
            require(not os.path.lexists(output), 'existing raw output requires explicit root reconciliation')
            os.mkdir('output', 0o700, dir_fd=work.fd)
            with directory(output) as out:
                execution = {'schema': 'DAMS-compute-only-execution-1', 'runtime_sha256': runtime_sha,
                             'source_commit': runtime['source_commit'], 'source_manifest_sha256': runtime['source_manifest_sha256'],
                             'transfer_config_sha256': runtime['transfer_config_sha256'], 'start_utc': stamp(),
                             'deadline_utc': runtime['deadline_utc'], 'guest_hardware_verification': verification,
                             'science_complete': False}
                out.write('cloud-execution.json', execution, exclusive=True)
                check_files(root, package)
                require(work.read(runtime_path.name) == (data, initial)
                        and work.read('transfer-config.json') == (transfer, transfer_anchor), 'guest input changed before launch')
                control = lease(work, runtime_sha, runtime['controller_lease_timeout_seconds'], clock.now(), control)
                require(clock.now() < soft, 'scientific deadline elapsed during guest preflight')
                env = dict(os.environ)
                for name in ('DAMS_CLOUD_PRIVATE_CONFIG', 'DAMS_CLOUD_STATE_DIR', 'GOOGLE_APPLICATION_CREDENTIALS'):
                    env.pop(name, None)
                env.update(DAMS_PACKAGED_COMMIT=runtime['source_commit'],
                           DAMS_SOURCE_MANIFEST=str(root.path / 'source-manifest.json'),
                           DAMS_TRANSFER_CONFIG=str(work.path / 'transfer-config.json'),
                           DAMS_OFFLINE_DEPENDENCIES='1')
                command = [str(root.path / 'run.sh'), '--spec', runtime['spec'], '--scale', str(runtime['scale']),
                           '--output', str(output), '--runtime-limits', str(work.path / 'runtime-limits.json')]
                logfd = os.open('pipeline.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600, dir_fd=out.fd)
                previous_handlers = {}
                stop_requested = False
                reason = 'pipeline_exit'
                error = None
                def stop(*_):
                    nonlocal stop_requested
                    stop_requested = True
                with os.fdopen(logfd, 'wb') as log:
                    process = subprocess.Popen(command, cwd=root.path, env=env, stdout=log,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                    try:
                        work.write('process.json', {'pid': process.pid, 'runtime_sha256': runtime_sha,
                                                   'start_utc': stamp(), 'deadline_utc': runtime['deadline_utc']}, exclusive=True)
                        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                            previous_handlers[sig] = signal.signal(sig, stop)
                        term_at = None
                        kill_at = None
                        while True:
                            now = clock.now()
                            if term_at is None:
                                try:
                                    check_files(root, package)
                                    work.check(); out.check()
                                    require(work.read(runtime_path.name) == (data, initial)
                                            and work.read('transfer-config.json') == (transfer, transfer_anchor),
                                            'frozen guest configuration changed')
                                    control = lease(work, runtime_sha, runtime['controller_lease_timeout_seconds'], now, control)
                                except (GuardError, OSError, ValueError) as caught:
                                    error = type(caught).__name__
                                    reason = 'input_or_controller_guard'
                                    stop_requested = True
                                if now >= soft:
                                    reason = 'scientific_deadline'; stop_requested = True
                                elif stop_requested and reason == 'pipeline_exit':
                                    reason = 'service_signal'
                                if stop_requested:
                                    term_at = now
                                    kill_at = min(hard - 2, now + runtime['pipeline_stop_grace_seconds'] - 2)
                                    try:
                                        os.killpg(process.pid, signal.SIGTERM)
                                    except ProcessLookupError:
                                        pass
                            code = process.poll()
                            if code is not None:
                                # A descendant can outlive its direct pipeline parent.
                                try:
                                    os.killpg(process.pid, 0)
                                except ProcessLookupError:
                                    break
                                if term_at is None:
                                    term_at = now; kill_at = min(hard - 2, now + runtime['pipeline_stop_grace_seconds'] - 2)
                                    reason = 'orphaned_owned_group'
                                    os.killpg(process.pid, signal.SIGTERM)
                            if kill_at is not None and now >= kill_at:
                                try:
                                    os.killpg(process.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                process.wait(timeout=max(.01, min(2, hard - clock.now())))
                                break
                            if now >= hard:
                                raise GuardError('owned pipeline did not stop before hard cutoff')
                            time.sleep(.1)
                        code = process.wait(timeout=max(.01, min(2, hard - clock.now())))
                        absent = wait_pipeline_group_absent(process.pid, runtime['runtime_limits']['stop_cutoff_utc'],
                                                            max_wait_seconds=max(0, min(2, hard - clock.now())))
                    except BaseException:
                        # The direct parent may have exited while owned descendants remain.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=2)
                        raise
                    finally:
                        if process.poll() is None:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait(timeout=2)
                        for sig, handler in previous_handlers.items():
                            signal.signal(sig, handler)
                work.check(); out.check()
                try:
                    check_files(root, package)
                    require(work.read(runtime_path.name) == (data, initial)
                            and work.read('transfer-config.json') == (transfer, transfer_anchor),
                            'guest input changed at terminal cutoff')
                except (GuardError, OSError, ValueError) as caught:
                    error = type(caught).__name__
                    reason = 'input_or_controller_guard'
                terminal = {**execution, 'end_utc': stamp(), 'exit_code': code, 'stop_reason': reason,
                            'guard_error_type': error, 'last_controller_sequence': control['sequence'],
                            'owned_pipeline_group_verification': absent, 'transfer_ack_verified': False,
                            'inputs_unchanged': error is None, 'science_complete': False}
                out.write('cloud-terminal.json', terminal, exclusive=True)
                work.write('compute-terminal.json', terminal, exclusive=True)
                return code if not error else 2


def watchdog(runtime_path, work_path):
    clock = Clock()
    with directory(work_path) as work:
        path = lexical(runtime_path)
        require(path.parent == work.path, 'watchdog runtime is outside owned work')
        data, initial = work.read(path.name)
        runtime = validate_runtime(parse(data))
        cutoff = utc(runtime['watchdog_shutdown_utc']).timestamp()
        while clock.now() < cutoff:
            try:
                require(work.read(path.name) == (data, initial), 'watchdog runtime changed')
            except (GuardError, OSError):
                break  # Failure cannot extend the shutdown time.
            time.sleep(min(1, max(0, cutoff - clock.now())))
        subprocess.run(['/sbin/shutdown', '-h', 'now'], check=True, timeout=15)
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'watchdog', 'service-timeout'))
    parser.add_argument('--root', type=Path, default=Path('/opt/dams'))
    parser.add_argument('--work', type=Path, default=Path('/var/lib/dams-compute'))
    parser.add_argument('--runtime', type=Path)
    args = parser.parse_args(argv)
    runtime = args.runtime or args.work / 'compute-runtime.json'
    try:
        if args.command == 'run':
            return run_compute(runtime, args.root, args.work)
        if args.command == 'watchdog':
            return watchdog(runtime, args.work)
        with directory(args.work) as work:
            require(lexical(runtime).parent == work.path, 'runtime outside owned work')
            value = parse(work.read(runtime.name)[0])
            print(service_timeout(value))
            return 0
    except (GuardError, ValueError, OSError, subprocess.SubprocessError):
        print('COMPUTE_ONLY_REFUSED', file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
