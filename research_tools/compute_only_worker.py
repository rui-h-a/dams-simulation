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


def require(ok, reason, *, guard_id=None, expected=None, actual=None):
    if not ok:
        error = GuardError(reason)
        error.guard_id = guard_id
        error.expected = expected
        error.actual = actual
        raise error


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def paired_nonphase_capacity(output, work, runtime):
    """Observe the whole pair on its true backing device; never truncate data.

    The existing Scheduler still reserves each complete case and controls its
    original writes/checkpoints. This additional supervisor observes BOTH arm
    children, failed attempts and metadata at each original control tick. A
    failure enters the existing cooperative-stop/reap path. This is a sampled
    software guard, not an atomic filesystem quota or a scientific raw gate.
    Legacy/single-arm/opt-in loop execution retains its previous behavior.
    """
    profile=runtime.get('small_enterprise_profile',{})
    if profile.get('schema')!='DAMS-small-enterprise-cloud-confirmation-pair-1':return None
    limits=runtime['runtime_limits']
    if any(limits.get(k,0) for k in ('phase_max_output_bytes','phase_spool_bytes',
            'phase_checkpoint_headroom_bytes','phase_metadata_reserve_bytes')):return None
    require(limits['min_free_disk_bytes']>=80*2**30, 'paired physical safety floor below original80GiB')
    output=lexical(output);work=lexical(work)
    root_info=output.stat();underlay=work.stat()
    require(stat.S_ISDIR(root_info.st_mode) and stat.S_ISDIR(underlay.st_mode)
            and root_info.st_dev==underlay.st_dev, 'nonphase pair must use actual backing device')
    fs=os.statvfs(work);free=fs.f_bavail*fs.f_frsize
    require(free>=limits['min_free_disk_bytes'], 'paired actual backing device free floor exhausted')
    total=0;files=0
    for parent,dirs,names in os.walk(output,followlinks=False):
        for name in dirs:
            p=Path(parent)/name
            try:s=p.lstat()
            except FileNotFoundError:continue
            require(stat.S_ISDIR(s.st_mode) and s.st_dev==root_info.st_dev,
                    'paired output foreign or symlink directory')
        for name in names:
            p=Path(parent)/name
            try:s=p.lstat()
            except FileNotFoundError:continue  # owned atomic publication; no deletion here
            require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_dev==root_info.st_dev,
                    'paired output foreign or nonregular leaf')
            total+=s.st_size;files+=1
            require(total<=limits['batch_max_output_bytes'], 'whole paired cumulative output byte cap reached')
            require(not limits['batch_max_output_files'] or files<=limits['batch_max_output_files'],
                    'whole paired cumulative output file cap reached')
    require(anchor(output.stat())[:2]==anchor(root_info)[:2]
            and anchor(work.stat())[:2]==anchor(underlay)[:2], 'paired output/backing namespace replaced')
    fs=os.statvfs(work);free=fs.f_bavail*fs.f_frsize
    require(free>=limits['min_free_disk_bytes'], 'paired actual backing device free floor exhausted')
    return {'schema':'DAMS-paired-nonphase-capacity-observation-1','logical_bytes':total,
            'files':files,'physical_available_bytes':free,
            'minimum_physical_free_bytes':limits['min_free_disk_bytes'],
            'whole_pair_including_failed_attempts':True,'sampled_supervisor_guard':True,
            'atomic_filesystem_quota':False,'science_complete':False}


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

    def read_lease(self, name='controller-heartbeat.json', *, attempts=3, bound=4096):
        """A legal atomic lease rotation is retried; immutable reads stay strict.

        In-place writes, aliases, permission changes and malformed files still
        refuse. An opened inode can become unlinked only when its exact pathname
        has been atomically replaced by a distinct private regular inode.
        """
        require(name == 'controller-heartbeat.json' and attempts == 3 and bound == 4096,
                'lease read contract changed', guard_id='lease_read_contract')
        for _ in range(attempts):
            self.check()
            path = lexical(self.path / name)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                        and before.st_uid == self.initial.st_uid
                        and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size <= bound,
                        'lease file is not bounded private regular bytes', guard_id='lease_file_shape')
                data = bytearray()
                while True:
                    block = os.read(fd, min(65536, bound + 1 - len(data)))
                    if not block:
                        break
                    data.extend(block)
                    require(len(data) <= bound, 'lease file exceeds bound', guard_id='lease_file_shape')
                after = os.fstat(fd)
                current = os.stat(path, follow_symlinks=False)
                self.check()
                require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                         before.st_mode, before.st_uid, before.st_gid)
                        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                            after.st_mode, after.st_uid, after.st_gid),
                        'lease inode changed during read', guard_id='lease_inplace_change')
                if (current.st_dev, current.st_ino) == (before.st_dev, before.st_ino):
                    require(anchor(before) == anchor(after) == anchor(current),
                            'lease inode changed during read', guard_id='lease_inplace_change')
                    return bytes(data), anchor(current)
                require(after.st_nlink == 0 and stat.S_ISREG(current.st_mode)
                        and current.st_nlink == 1 and current.st_dev == before.st_dev
                        and current.st_uid == before.st_uid and current.st_gid == before.st_gid
                        and stat.S_IMODE(current.st_mode) == 0o600 and current.st_size <= bound,
                        'lease replacement is not an atomic private version', guard_id='lease_rotation_shape')
            finally:
                os.close(fd)
        raise GuardError('lease rotated beyond bounded reads')

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


class GuardHistory:
    """Create-only first failure; later diagnostics never overwrite its cause."""
    INPUT_GUARDS = {'source_identity', 'configuration', 'original_limits'}
    SAFE_REASONS = {
        'controller lease expired or future dated', 'controller lease rolled back',
        'controller lease changed without sequence advancement', 'controller lease time rolled back',
        'invalid controller lease', 'invalid bounded JSON', 'guest file changed during read',
        'frozen guest input changed', 'frozen guest configuration changed',
        'guest input changed at terminal cutoff', 'guest directory namespace changed',
        'lease inode changed during read', 'lease replacement is not an atomic private version',
        'lease rotated beyond bounded reads', 'lease file is not bounded private regular bytes',
        'paired actual backing device free floor exhausted', 'whole paired cumulative output byte cap reached',
        'whole paired cumulative output file cap reached',
        'guest input changed before launch', 'scientific deadline elapsed during guest preflight',
        'owned pipeline did not stop before hard cutoff',
    }

    def __init__(self, work, clock, runtime_sha, source_manifest_sha, start_utc):
        self.work, self.clock = work, clock
        self.runtime_sha, self.source_manifest_sha = runtime_sha, source_manifest_sha
        self.start_utc = start_utc
        self.started_monotonic = time.monotonic()
        self.first = None
        self.secondary = []
        self.additional_count = 0
        self.input_failed = False
        self.receipt = None

    @staticmethod
    def detail(value):
        if value is None or type(value) in (bool, int):
            return value
        if type(value) is float:
            return value if math.isfinite(value) else None
        if isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value):
            return value
        if isinstance(value, dict) and len(value) <= 12:
            return {key: GuardHistory.detail(item) for key, item in value.items()
                    if key in {'max_age_seconds', 'future_seconds', 'age_seconds', 'sequence',
                               'source_identity_seconds', 'output_scan_seconds', 'pre_lease_seconds'}}
        return 'redacted'

    def record(self, error, guard_id, phase, control, timings=None):
        if guard_id in self.INPUT_GUARDS:
            self.input_failed = True
        now = self.clock.now()
        chain = []
        current = error
        for _ in range(3):
            if current is None:
                break
            reason = str(current) if isinstance(current, GuardError) else ''
            chain.append({'exception_type': type(current).__name__,
                          'reason': reason if reason in self.SAFE_REASONS else 'unspecified_guard_failure',
                          'errno': current.errno if isinstance(current, OSError) else None})
            current = current.__cause__ or current.__context__
        item = {'schema': 'DAMS-first-guard-failure-1',
                'guard_id': getattr(error, 'guard_id', None) or guard_id, 'check_stage': guard_id,
                'phase': phase, 'UTC': datetime.fromtimestamp(now, timezone.utc).isoformat(),
                'monotonic_seconds_since_execution_start': time.monotonic() - self.started_monotonic,
                'cause_chain': chain, 'expected': self.detail(getattr(error, 'expected', None)),
                'actual': self.detail(getattr(error, 'actual', None)),
                'last_controller_sequence': control.get('sequence') if control else None,
                'lease_age_seconds': now - utc(control['observed_utc']).timestamp() if control else None,
                'runtime_sha256': self.runtime_sha, 'source_manifest_sha256': self.source_manifest_sha,
                'execution_start_utc': self.start_utc,
                'check_timings_seconds': self.detail(timings or {}), 'science_complete': False}
        if self.first is None:
            self.first = item
            leaf = 'first-guard-fault-' + sha(canonical({'runtime': self.runtime_sha, 'start': self.start_utc})) + '.json'
            try:
                self.work.write(leaf, item, exclusive=True)
                self.receipt = {'file': leaf, 'sha256': sha(canonical(item)), 'durably_saved': True}
            except (GuardError, OSError, ValueError) as persistence_error:
                # An inability to write diagnostics cannot prevent cooperative stop.
                self.receipt = {'file': leaf, 'durably_saved': False,
                                'exception_type': type(persistence_error).__name__}
        else:
            self.additional_count += 1
            if len(self.secondary) < 16:
                self.secondary.append(item)
        return self.first['cause_chain'][0]['exception_type']


def reap_timeout(clock, hard):
    """An owned-child wait never receives time beyond the original hard stop."""
    return max(0., min(2., hard - clock.now()))


def install_original_limits(work, runtime):
    """Preservation-only: pass retained original RuntimeLimits bytes unchanged."""
    raw, original_anchor = work.read('original-runtime-limits.json', bound=65536)
    require(sha(raw) == runtime['preservation_profile']['original_runtime_limits_sha256']
            and parse(raw) == runtime['runtime_limits'], 'original runtime full bytes differ')
    fd = os.open('runtime-limits.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=work.fd)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.fsync(work.fd)
    work.check()
    installed, installed_anchor = work.read('runtime-limits.json', bound=65536)
    require(installed == raw, 'installed original runtime bytes differ')
    return raw, original_anchor, installed_anchor


def guard_original_limits(work, retained):
    raw, original_anchor, installed_anchor = retained
    require(work.read('original-runtime-limits.json', bound=65536) == (raw, original_anchor)
            and work.read('runtime-limits.json', bound=65536) == (raw, installed_anchor),
            'retained or installed original runtime bytes changed')


def validate_runtime(value):
    require(isinstance(value, dict) and set(value) in (RUNTIME_FIELDS, RUNTIME_FIELDS | {'preservation_profile'}, RUNTIME_FIELDS | {'small_enterprise_profile'})
            and value['schema'] == SCHEMA, 'unsupported compute-only runtime')
    for key in ('source_manifest_sha256', 'provider_identity_sha256', 'transfer_config_sha256'):
        require(isinstance(value[key], str) and re.fullmatch('[a-f0-9]{64}', value[key]),
                'invalid frozen SHA')
    require(isinstance(value['source_commit'], str)
            and re.fullmatch('[a-f0-9]{40}', value['source_commit']), 'invalid frozen source commit')
    require(value['spec'] in ('longitudinal-adoption-5y', 'longitudinal-adoption-10y', 'small-enterprise-5y')
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
    if value['spec']=='small-enterprise-5y':
        from research_tools.small_enterprise_cloud import validate_profile
        require(value['scale']==300 and 'small_enterprise_profile' in value and 'preservation_profile' not in value,'small enterprise f9 runtime selection differs')
        validate_profile(value['small_enterprise_profile'],value['runtime_limits'])
    else:
        require('small_enterprise_profile' not in value,'small profile belongs only to its exact scientific selection')
    if 'preservation_profile' in value:
        from research_tools.frozen_science_cloud import validate_profile
        profile = validate_profile(value['preservation_profile'], value['runtime_limits'])
        require(profile['lease_timeout_seconds'] == value['controller_lease_timeout_seconds'],
                'preservation/controller lease differs')
        from dams_sim.spec import resolve_spec
        require(resolve_spec(value['spec'], value['scale']).sha256 == profile['spec_sha256'],
                'original scientific specification selection differs')
        require(utc(value['deadline_utc']) <= utc(profile['cleanup_deadline_utc']),
                'node expiry exceeds retained cleanup deadline')
    soft, hard = limits.deadline, limits.stop_cutoff
    watchdog, expiry = utc(value['watchdog_shutdown_utc']).timestamp(), utc(value['deadline_utc']).timestamp()
    if 'preservation_profile' in value:
        require(soft + value['pipeline_stop_grace_seconds'] <= hard,
                'original scientific maximum stop gap differs')
        soft = utc(profile['operational_soft_stop_utc']).timestamp()
        hard = utc(profile['operational_hard_stop_utc']).timestamp()
        require(watchdog == expiry - 30, 'operational watchdog must retain node expiry minus 30s')
    require(soft + value['pipeline_stop_grace_seconds'] <= hard < watchdog <= expiry,
            'compute stop bounds cannot extend node expiry')
    if 'preservation_profile' in value:
        # Original Scheduler clips both cooperative stop and reaping to this
        # one absolute cutoff. Include guest reaping in the available gap;
        # do not require a new five seconds after the original hard stop.
        required_grace = min(limits.cooperative_stop_grace_seconds + 5, hard - soft)
        require(3 <= value['pipeline_stop_grace_seconds'] and
                required_grace <= value['pipeline_stop_grace_seconds'],
                'pipeline grace must cover available original stop/reap time')
    else:
        require(limits.cooperative_stop_grace_seconds + 5 <= value['pipeline_stop_grace_seconds'],
                'pipeline grace cannot cover cooperative stop and reaping')
    require(bool(value['expected_guest']), 'compute-only requires full guest hardware expectations')
    require(limits.batch_max_output_bytes > 0 and
            (limits.batch_max_output_files > 0 or 'preservation_profile' in value),
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
    if 'small_enterprise_profile' in runtime:
        from research_tools.small_enterprise_cloud import validate_profile
        validate_profile(runtime['small_enterprise_profile'],runtime['runtime_limits'])
        required |= {'research_tools/small_enterprise.py','research_tools/small_enterprise_cloud.py'}
    if 'preservation_profile' in runtime:
        required.add('research_tools/frozen_science_cloud.py')
        from research_tools.frozen_science_cloud import CORE, DRIVER, ENTRY, CODEC
        from dams_sim.storage import source_hash
        from dams_sim.longitudinal_pipeline import driver_hash
        require(source_hash() == CORE and driver_hash() == DRIVER
                and sha(root.read('run.sh')[0]) == ENTRY
                and sha(root.read('research_tools/cloud_archive.py', 64 * 1024**2)[0]) == CODEC,
                'actual original core/driver/entry/full-raw codec changed')
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
    data, _ = work.read_lease()
    value = parse(data)
    require(isinstance(value, dict) and set(value) == {'schema', 'runtime_sha256', 'sequence', 'observed_utc'}
            and value['schema'] == 'DAMS-compute-controller-lease-1'
            and value['runtime_sha256'] == runtime_sha and type(value['sequence']) is int
            and value['sequence'] >= 0 and isinstance(value['observed_utc'], str),
            'invalid controller lease', guard_id='lease_binding', expected=runtime_sha)
    observed = utc(value['observed_utc']).timestamp()
    # Obtain the rollback-resistant clock AFTER the versioned read. The source
    # and output scans preceding this call must not date a new lease in the future.
    now = now() if callable(now) else now
    require(now - timeout <= observed <= now + 5, 'controller lease expired or future dated',
            guard_id='lease_time', expected={'max_age_seconds': timeout, 'future_seconds': 5},
            actual={'age_seconds': now - observed, 'sequence': value['sequence']})
    if previous is not None:
        require(value['sequence'] >= previous['sequence'], 'controller lease rolled back',
                guard_id='lease_sequence', expected=previous['sequence'], actual=value['sequence'])
        if value['sequence'] == previous['sequence']:
            require(value == previous, 'controller lease changed without sequence advancement',
                    guard_id='lease_same_sequence', expected=sha(canonical(previous)), actual=sha(data))
        else:
            require(observed >= utc(previous['observed_utc']).timestamp(), 'controller lease time rolled back',
                    guard_id='lease_time_sequence', expected=utc(previous['observed_utc']).timestamp(), actual=observed)
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
        if 'preservation_profile' in runtime:
            soft = utc(runtime['preservation_profile']['operational_soft_stop_utc']).timestamp()
            hard = utc(runtime['preservation_profile']['operational_hard_stop_utc']).timestamp()
        require(clock.now() < soft, 'scientific deadline already elapsed')
        with single_run(work):
            package = validate_package(root, runtime)
            verify_executing_source(root, package)
            transfer, transfer_anchor = work.read('transfer-config.json')
            require(sha(transfer) == runtime['transfer_config_sha256'], 'frozen transfer configuration differs')
            control = lease(work, runtime_sha, runtime['controller_lease_timeout_seconds'], clock.now)
            verification = verify_identity(runtime, measured_guest())
            limits = {**runtime['runtime_limits'], 'provenance': {
                **runtime['runtime_limits'].get('provenance', {}),
                'instance_id': verification['instance_id'], 'zone': verification['zone'],
                'machine_type': verification['machine_type'], 'packaged_commit': runtime['source_commit'],
                'environment': 'GCP'}}
            preservation = runtime.get('preservation_profile')
            if preservation is not None:
                # Explicit same-science legacy contract, not guest hardware attestation.
                # Fresh provider/image/runtime identity remains in cloud-execution.json.
                limits['provenance'] = preservation['legacy_scientific_provenance']
            if preservation is not None:
                original_limits = install_original_limits(work, runtime)
            else:
                work.write('runtime-limits.json', limits, exclusive=True)
            output = work.path / 'output'
            phase=None
            if limits.get('phase_max_output_bytes',0):
                from dams_sim.phase_storage import PhaseGuard,ENV
                phase_path=work.path/'phase-storage-config.json';phase=PhaseGuard(phase_path)
                require(phase.root==output and phase.value['raw_bytes']==limits['phase_max_output_bytes']
                        and phase.value['spool_bytes']==limits['phase_spool_bytes'],'guest bounded storage/runtime differs')
                require({p.name for p in output.iterdir()}=={'.phase-metadata-reserve'},'bounded output contains prior evidence; no restart')
            else:
                migration = None
                if preservation is not None and os.path.lexists(output):
                    from research_tools.frozen_science_cloud import prepare_existing_output
                    migration = prepare_existing_output(work.path, preservation)
                else:
                    require(not os.path.lexists(output), 'existing raw output requires explicit root reconciliation')
                    require(preservation is None or preservation['handoff_receipt_sha256'] is None,
                            'declared original handoff output is missing')
                    os.mkdir('output', 0o700, dir_fd=work.fd)
            with directory(output) as out:
                execution = {'schema': 'DAMS-compute-only-execution-1', 'runtime_sha256': runtime_sha,
                             'source_commit': runtime['source_commit'], 'source_manifest_sha256': runtime['source_manifest_sha256'],
                             'transfer_config_sha256': runtime['transfer_config_sha256'], 'start_utc': stamp(),
                             'deadline_utc': runtime['deadline_utc'], 'guest_hardware_verification': verification,
                             'science_complete': False}
                if preservation is not None:
                    execution['preservation_profile_sha256'] = sha(canonical(preservation))
                    execution['original_runtime_limits_sha256'] = preservation['original_runtime_limits_sha256']
                    execution['operational_stop_clock'] = {k: preservation[k] for k in
                        ('operational_soft_stop_utc', 'operational_hard_stop_utc')}
                    execution['original_migration'] = migration
                    execution['legacy_scientific_provenance_is_not_guest_attestation'] = True
                    execution['current_guest_execution_provenance'] = verification
                    from research_tools.frozen_science_cloud import check_output_capacity
                    check_output_capacity(output, preservation)
                out.write('cloud-execution.json', execution, exclusive=True)
                history = GuardHistory(work, clock, runtime_sha, runtime['source_manifest_sha256'],
                                       execution['start_utc'])
                guard_id = 'source_identity'
                try:
                    check_files(root, package)
                    if preservation is not None:
                        guard_id = 'original_limits'
                        guard_original_limits(work, original_limits)
                    guard_id = 'configuration'
                    require(work.read(runtime_path.name) == (data, initial)
                            and work.read('transfer-config.json') == (transfer, transfer_anchor), 'guest input changed before launch')
                    guard_id = 'controller_lease'
                    control = lease(work, runtime_sha, runtime['controller_lease_timeout_seconds'], clock.now, control)
                    guard_id = 'pipeline_deadline'
                    require(clock.now() < soft, 'scientific deadline elapsed during guest preflight')
                except (GuardError, OSError, ValueError) as caught:
                    history.record(caught, guard_id, 'prelaunch', control)
                    raise
                env = dict(os.environ)
                for name in ('DAMS_CLOUD_PRIVATE_CONFIG', 'DAMS_CLOUD_STATE_DIR', 'GOOGLE_APPLICATION_CREDENTIALS'):
                    env.pop(name, None)
                env.update(DAMS_PACKAGED_COMMIT=runtime['source_commit'],
                           DAMS_SOURCE_MANIFEST=str(root.path / 'source-manifest.json'),
                           DAMS_TRANSFER_CONFIG=str(work.path / 'transfer-config.json'),
                           DAMS_OFFLINE_DEPENDENCIES='1')
                if phase is not None:
                    temporary=output/'.phase-tmp';temporary.mkdir(mode=0o700)
                    env.update({ENV:str(phase_path),'TMPDIR':str(temporary),'TMP':str(temporary),
                                'TEMP':str(temporary),'SQLITE_TMPDIR':str(temporary)})
                if 'small_enterprise_profile' in runtime:
                    profile_raw=canonical(runtime['small_enterprise_profile'])
                    work.write('small-enterprise-profile.json',runtime['small_enterprise_profile'],exclusive=True)
                    from research_tools.small_enterprise_cloud import ENV,SHA_ENV
                    env.update({ENV:str(work.path/'small-enterprise-profile.json'),SHA_ENV:sha(profile_raw)})
                command = [str(root.path / 'run.sh'), '--spec', runtime['spec'], '--scale', str(runtime['scale']),
                           '--output', str(output), '--runtime-limits', str(work.path / 'runtime-limits.json')]
                paired_capacity=paired_nonphase_capacity(output,work.path,runtime)
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
                    try:
                        process = subprocess.Popen(command, cwd=root.path, env=env, stdout=log,
                                                   stderr=subprocess.STDOUT, start_new_session=True)
                    except (OSError, ValueError, subprocess.SubprocessError) as caught:
                        history.record(caught, 'pipeline_launch', 'prelaunch', control)
                        raise
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
                                guard_id = 'source_identity'
                                timings = {}
                                iteration_started = time.monotonic()
                                try:
                                    check_files(root, package)
                                    timings['source_identity_seconds'] = time.monotonic() - iteration_started
                                    guard_id = 'output_namespace'
                                    work.check(); out.check()
                                    guard_id = 'output_capacity'
                                    scan_started = time.monotonic()
                                    paired_capacity=paired_nonphase_capacity(output,work.path,runtime)
                                    timings['output_scan_seconds'] = time.monotonic() - scan_started
                                    if preservation is not None:
                                        check_output_capacity(output, preservation)
                                        guard_id = 'original_limits'
                                        guard_original_limits(work, original_limits)
                                    guard_id = 'configuration'
                                    require(work.read(runtime_path.name) == (data, initial)
                                            and work.read('transfer-config.json') == (transfer, transfer_anchor),
                                            'frozen guest configuration changed')
                                    guard_id = 'controller_lease'
                                    timings['pre_lease_seconds'] = time.monotonic() - iteration_started
                                    control = lease(work, runtime_sha, runtime['controller_lease_timeout_seconds'], clock.now, control)
                                except (GuardError, OSError, ValueError) as caught:
                                    error = history.record(caught, guard_id, 'running', control, timings)
                                    if reason == 'pipeline_exit':
                                        reason = 'input_or_controller_guard'
                                    stop_requested = True
                                # Scans and lease reads never extend a deadline by
                                # continuing to use the pre-scan timestamp.
                                now = clock.now()
                                if now >= soft:
                                    if reason == 'pipeline_exit':
                                        reason = ('operational_deadline' if preservation is not None else 'scientific_deadline')
                                    stop_requested = True
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
                                process.wait(timeout=(reap_timeout(clock, hard) if preservation is not None
                                                      else max(.01, min(2, hard - clock.now()))))
                                break
                            if now >= hard:
                                raise GuardError('owned pipeline did not stop before hard cutoff')
                            time.sleep(.1)
                        code = process.wait(timeout=(reap_timeout(clock, hard) if preservation is not None
                                                     else max(.01, min(2, hard - clock.now()))))
                        absent = wait_pipeline_group_absent(process.pid, (runtime['preservation_profile']['operational_hard_stop_utc']
                                                             if preservation is not None else runtime['runtime_limits']['stop_cutoff_utc']),
                                                            max_wait_seconds=max(0, min(2, hard - clock.now())))
                    except BaseException as caught:
                        # The direct parent may have exited while owned descendants remain.
                        # Stop and reap before diagnostic filesystem I/O; a broken
                        # receipt path must never keep an owned producer alive.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=(reap_timeout(clock, hard) if preservation is not None else 2))
                        finally:
                            if isinstance(caught, (GuardError, OSError, ValueError, subprocess.SubprocessError)):
                                try:
                                    history.record(caught, 'pipeline_supervision', 'running', control)
                                except Exception:
                                    # Retain the original exception when even the
                                    # bounded diagnostic recorder is unavailable.
                                    pass
                        raise
                    finally:
                        if process.poll() is None:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait(timeout=(reap_timeout(clock, hard) if preservation is not None else 2))
                        for sig, handler in previous_handlers.items():
                            signal.signal(sig, handler)
                terminal_inputs_verified = False
                guard_id = 'output_namespace'
                try:
                    work.check(); out.check()
                    guard_id = 'source_identity'
                    check_files(root, package)
                    if preservation is not None:
                        guard_id = 'original_limits'
                        guard_original_limits(work, original_limits)
                    guard_id = 'configuration'
                    require(work.read(runtime_path.name) == (data, initial)
                            and work.read('transfer-config.json') == (transfer, transfer_anchor),
                            'guest input changed at terminal cutoff')
                    terminal_inputs_verified = True
                    guard_id = 'output_capacity'
                    paired_capacity=paired_nonphase_capacity(output,work.path,runtime)
                except (GuardError, OSError, ValueError) as caught:
                    error = history.record(caught, guard_id, 'terminal_recheck', control)
                    if reason == 'pipeline_exit':
                        reason = 'input_or_controller_guard'
                phase_totals=None
                if phase is not None:
                    try:phase_totals=phase.check()
                    except (OSError,ValueError,RuntimeError) as caught:
                        error=history.record(caught, 'output_capacity', 'terminal_recheck', control)
                        if reason == 'pipeline_exit':reason='bounded_storage_terminal_guard'
                terminal = {**execution, 'end_utc': stamp(), 'exit_code': code, 'stop_reason': reason,
                            'guard_error_type': error, 'last_controller_sequence': control['sequence'],
                            'owned_pipeline_group_verification': absent, 'transfer_ack_verified': False,
                            'inputs_unchanged': terminal_inputs_verified and not history.input_failed,
                            'input_integrity': {'verified_at_start': True,
                                                'verified_at_terminal': terminal_inputs_verified,
                                                'input_guard_failed': history.input_failed},
                            'control_state': {'stop_requested': stop_requested,
                                              'controller_guard_failed': bool(history.first and
                                                  history.first['check_stage'] == 'controller_lease')},
                            'output_integrity': {'full_raw_scientific_gate_passed': False},
                            'first_guard_failure': history.first, 'first_guard_receipt': history.receipt,
                            'secondary_guard_failures': history.secondary,
                            'secondary_guard_failure_count': history.additional_count,
                            'science_complete': False}
                if phase is not None:
                    terminal['bounded_phase_storage']={'schema':'dams-bounded-phase-storage-v1',
                        'logical_bytes':phase_totals,'raw_limit':phase.value['raw_bytes'],
                        'encoded_limit':phase.value['spool_bytes'],'fully_allocated_quiescent_files':phase_totals is not None,
                        'original_scientific_horizon_unchanged':True}
                if paired_capacity is not None:
                    terminal['paired_nonphase_capacity']=paired_capacity
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
