"""Bounded root IAP transport. No launch/authentication is performed on import.

Collector holds are attempted payload bounds, not SSH wire/billing meters.
Root must retain the head and admit nonzero transport overhead before dispatch.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import time
import urllib.request

from dams_sim._committed_pages import Directory, identity
from dams_sim.storage import canonical, digest, source_hash
from dams_sim.transfer_spool import MAX_METADATA_BYTES, SCHEMA, _codec, _natural, _sha
from research_tools.compute_only_control import Collector, blob, parse, read, require

TRANSPORT_SCHEMA = 'dams-compute-only-iap-v1'
IMPORT_SHA = digest(Path(__file__).read_bytes())
CONFIG_KEYS = {'schema', 'transport_source_sha256', 'collector_admission_sha256', 'stage_id',
               'source_sha256', 'codec_source_sha256', 'codec_dependencies_sha256',
               'project', 'zone', 'instance_name', 'instance_id', 'gcloud_configuration', 'gcloud_account', 'ssh_key_file',
               'remote_source_root', 'remote_runtime_file', 'remote_runtime_sha256',
               'provider_identity_sha256', 'remote_transfer_config', 'remote_transfer_config_sha256',
               'deadline_utc', 'attempt_timeout_seconds', 'max_stderr_bytes',
               'transport_overhead_reserve_bytes', 'attempt_log_dir'}
REQUEST_KEYS = {'context', 'generation', 'relative_file', 'max_bytes', 'expected_bytes', 'expected_sha256'}
CONTEXT_KEYS = CONFIG_KEYS - {'collector_admission_sha256', 'gcloud_configuration', 'gcloud_account',
                            'ssh_key_file',
                            'attempt_timeout_seconds', 'max_stderr_bytes',
                            'transport_overhead_reserve_bytes', 'attempt_log_dir'}


def utc(value):
    require(isinstance(value, str), 'transport UTC value is not text')
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(date.tzinfo is not None and date.utcoffset().total_seconds() == 0, 'transport deadline is not UTC')
    return date.timestamp()


def absolute(value):
    require(isinstance(value, str) and value.startswith('/') and '\x00' not in value
            and all(p not in ('', '.', '..') for p in value.split('/')[1:]), 'unsafe transport absolute path')
    return Path(value)


class ProcessStream:
    """One owned subprocess group, bounded pipes and fixed operation cutoff."""
    def __init__(self, argv, *, bound, stderr_bound, cutoff, check, completed, popen=subprocess.Popen):
        self.bound = _natural(bound); self.stderr_bound = _natural(stderr_bound, True)
        self.cutoff = cutoff; self.check = check; self.completed = completed
        self.pending = bytearray(); self.stdout_bytes = 0; self.stderr_bytes = 0
        self.stderr_sha = hashlib.sha256(); self.eof = False; self.closed = False; self.failed = False
        self.proc = None; self.selector = selectors.DefaultSelector()
        try:
            self.check(); require(time.monotonic() < cutoff, 'transport attempt already expired')
            self.proc = popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
            for stream, name in ((self.proc.stdout, 'stdout'), (self.proc.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False); self.selector.register(stream, selectors.EVENT_READ, name)
        except BaseException:
            self.failed = True; self.close(); raise

    def _finish(self):
        require(time.monotonic() < self.cutoff, 'transport attempt timed out')
        code = self.proc.wait(timeout=max(0.001, self.cutoff - time.monotonic()))
        require(code == 0, 'transport subprocess nonzero exit')
        try:
            os.killpg(self.proc.pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise ValueError('owned transport process group still present')
        self.eof = True; self.check()

    def read(self, size):
        require(type(size) is int and size > 0 and not self.closed, 'transport read must be positive/bounded')
        try:
            # Buffered payload and EOF have the same deadline as pipe reads.
            self.check()
            if time.monotonic() >= self.cutoff:
                raise TimeoutError('transport fixed attempt deadline')
            while not self.pending and not self.eof:
                self.check(); remaining = self.cutoff - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('transport fixed attempt deadline')
                if not self.selector.get_map():
                    self._finish(); break
                for key, _ in self.selector.select(min(remaining, 0.1)):
                    remaining_bound = ((self.bound - self.stdout_bytes) if key.data == 'stdout'
                                       else (self.stderr_bound - self.stderr_bytes))
                    block = os.read(key.fileobj.fileno(), min(65536, remaining_bound + 1))
                    if not block:
                        self.selector.unregister(key.fileobj); continue
                    if key.data == 'stdout':
                        self.stdout_bytes += len(block)
                        require(self.stdout_bytes <= self.bound, 'transport stdout exceeds payload bound')
                        self.pending.extend(block)
                    else:
                        self.stderr_bytes += len(block); self.stderr_sha.update(block)
                        require(self.stderr_bytes <= self.stderr_bound, 'transport stderr exceeds bound')
            self.check()
            if time.monotonic() >= self.cutoff:
                raise TimeoutError('transport fixed attempt deadline')
            block = bytes(self.pending[:size]); del self.pending[:size]
            return block
        except BaseException:
            self.failed = True
            try:
                self.close()
            except BaseException:
                # Cleanup has recorded its failure; preserve the read refusal.
                pass
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        accepted = self.eof and not self.failed and not self.pending
        errors = []
        try:
            if accepted:
                self.check()
                if time.monotonic() >= self.cutoff:
                    raise TimeoutError('transport fixed attempt deadline')
        except BaseException as error:
            errors.append(error); accepted = False
        try:
            if self.proc is not None:
                try:
                    # Reap an already-finished parent before signalling. On
                    # macOS a still-unreaped zombie can yield EPERM to killpg.
                    self.proc.poll()
                    if not accepted:
                        try:
                            os.killpg(self.proc.pid, 0)
                        except ProcessLookupError:
                            pass
                        else:
                            os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    errors.append(error)
                    # Only the exact Popen child is a valid fallback target.
                    try:
                        if self.proc.poll() is None:
                            self.proc.kill()
                    except BaseException as error:
                        errors.append(error)
                try:
                    self.proc.wait(timeout=2)
                except BaseException as error:
                    errors.append(error)
                finally:
                    for stream in (self.proc.stdout, self.proc.stderr):
                        try:
                            stream.close()
                        except BaseException as error:
                            errors.append(error)
        finally:
            try:
                self.selector.close()
            except BaseException as error:
                errors.append(error)
            try:
                self.completed({'status': 'accepted-stream' if accepted and not errors else 'failed-or-unconsumed',
                                'stdout_observed_bytes': self.stdout_bytes, 'stderr_observed_bytes': self.stderr_bytes,
                                'stderr_sha256': self.stderr_sha.hexdigest(),
                                'exit_code': None if self.proc is None else self.proc.returncode,
                                'cleanup_error_types': [type(error).__name__ for error in errors],
                                'wire_bytes_measured': False, 'science_complete': False})
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]


class IAPTransport:
    """Fixed approved instance and original Collector pool, no automatic retries.

    retain(head, attempt) must durably retain the exact head before dispatch and
    return that head unchanged. Root owns overhead/cost admission, not this tool.
    """
    def __init__(self, config_file, *, config_sha256, collector, retain, popen=subprocess.Popen):
        require(isinstance(collector, Collector) and callable(retain), 'transport requires original collector/retainer')
        self.collector = collector; self.retain = retain; self.popen = popen
        self.config_path = absolute(str(config_file)); self.directory = Directory(self.config_path.parent)
        self.log = None; self.closed = False
        try:
            raw, self.config_anchor = read(self.directory, self.config_path.name)
            require(digest(raw) == _sha(config_sha256), 'externally retained transport configuration differs')
            self.raw = raw; self.config = c = parse(raw)
            self.frozen_config = canonical(c)
            require(isinstance(c, dict) and set(c) == CONFIG_KEYS and c['schema'] == TRANSPORT_SCHEMA,
                    'transport configuration shape differs')
            require(c['transport_source_sha256'] == IMPORT_SHA == digest(Path(__file__).read_bytes()),
                    'transport runtime source differs')
            require(c['collector_admission_sha256'] == collector.admission_sha, 'transport uses another collector pool')
            for key in ('stage_id', 'source_sha256', 'codec_source_sha256', 'codec_dependencies_sha256', 'deadline_utc'):
                require(c[key] == collector.config[key], 'transport source/stage/codec/deadline differs')
            for key in ('remote_runtime_sha256', 'provider_identity_sha256', 'remote_transfer_config_sha256'):
                _sha(c[key])
            for key in ('project', 'zone', 'instance_name', 'gcloud_configuration'):
                require(isinstance(c[key], str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', c[key]),
                        'transport fixed target invalid')
            require(isinstance(c['instance_id'], str) and re.fullmatch('[0-9]{1,32}', c['instance_id']), 'transport instance ID invalid')
            require(isinstance(c['gcloud_account'], str) and re.fullmatch('[A-Za-z0-9_.+@-]{1,254}', c['gcloud_account']),
                    'transport account invalid')
            for key in ('remote_source_root', 'remote_runtime_file', 'remote_transfer_config', 'attempt_log_dir'):
                absolute(c[key])
            key_path = absolute(c['ssh_key_file']); self.key_directory = Directory(key_path.parent)
            self.key_name = key_path.name; self.key_observations = {}
            for name in (self.key_name, self.key_name + '.pub'):
                info = os.stat(name, dir_fd=self.key_directory.fd, follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'transport requires existing regular SSH key pair')
                self.key_observations[name] = identity(info)
            require(type(c['attempt_timeout_seconds']) is int and 3 <= c['attempt_timeout_seconds'] <= 300,
                    'transport attempt timeout invalid')
            require(0 < _natural(c['max_stderr_bytes']) <= 65536, 'transport stderr bound invalid')
            _natural(c['transport_overhead_reserve_bytes'], True)
            self.clock_wall, self.clock_monotonic = time.time(), time.monotonic()
            self.deadline = utc(c['deadline_utc']); self.dispatch_floor = collector.head['sequence']
            self.log = Directory(c['attempt_log_dir'])
            paths = [collector.state.path, collector.cache.path, *(d.path for d in collector.copies)]
            require(all(self.log.path != p and self.log.path not in p.parents and p not in self.log.path.parents for p in paths),
                    'transport log namespace overlaps collector bytes')
            self.bound_group = None; self.check()
        except BaseException:
            self.close(); raise

    def check(self):
        require(not self.closed, 'transport closed')
        self.collector.check(); self.directory.check()
        require(canonical(self.config) == self.frozen_config, 'transport in-memory frozen configuration changed')
        require(identity(os.stat(self.config_path.name, dir_fd=self.directory.fd, follow_symlinks=False)) == self.config_anchor,
                'transport frozen configuration changed')
        require(digest(Path(__file__).read_bytes()) == IMPORT_SHA, 'transport runtime code changed')
        if self.log is not None:
            self.log.check()
        if hasattr(self, 'key_directory'):
            self.key_directory.check()
            for name, observed in self.key_observations.items():
                require(identity(os.stat(name, dir_fd=self.key_directory.fd, follow_symlinks=False)) == observed,
                        'transport enrolled key identity changed')
        now = max(time.time(), self.clock_wall + time.monotonic() - self.clock_monotonic)
        if now >= self.deadline:
            raise TimeoutError('transport original whole-operation UTC deadline')
        return self.deadline - now

    def _request(self, generation, relative_file, bound, length=None, sha=None):
        _sha(generation); _natural(bound, True)
        require(relative_file in ('manifest.json', 'seal.json') or
                re.fullmatch(r'parts/[0-9]{5}/[0-9]{20}\.z', relative_file), 'transport file outside sealed group')
        if length is not None:
            _natural(length); require(length <= bound, 'transport expected size exceeds bound')
        if sha is not None:
            _sha(sha)
        return {'context': {key: self.config[key] for key in CONTEXT_KEYS}, 'generation': generation,
                'relative_file': relative_file, 'max_bytes': bound, 'expected_bytes': length, 'expected_sha256': sha}

    def argv(self, request):
        c = self.config; token = base64.b64encode(blob(request)).decode('ascii')
        script = ('import sys;sys.path[:0]=' + repr([c['remote_source_root'], c['remote_source_root'] + '/research_tools'])
                  + ';from research_tools.compute_only_transport import remote_entry;remote_entry(' + repr(token) + ')')
        command = '/usr/bin/sudo -n /usr/bin/python3 -B -c ' + shlex.quote(script)
        return ['gcloud', 'compute', 'ssh', c['instance_name'], '--project=' + c['project'], '--zone=' + c['zone'],
                '--configuration=' + c['gcloud_configuration'], '--account=' + c['gcloud_account'],
                '--tunnel-through-iap', '--strict-host-key-checking=yes', '--quiet', '--verbosity=error',
                '--ssh-key-file=' + c['ssh_key_file'],
                '--ssh-flag=-T', '--ssh-flag=-oBatchMode=yes', '--ssh-flag=-oConnectTimeout=15', '--command=' + command]

    def _dispatch(self, request, ticket):
        remaining = self.check(); head = self.collector.head
        require(head == ticket['head'] and head['sequence'] > self.dispatch_floor, 'transport reservation is stale/reused')
        record_name = f'{head["sequence"]:08d}.json'
        record_raw, record_anchor = read(self.collector.records, record_name); record = parse(record_raw)
        require(digest(record_raw) == ticket['reservation_sha256'] and record['reserved_bytes'] >= request['max_bytes'] + 1,
                'transport reservation does not cover payload/overread')
        argv = self.argv(request)
        attempt = {'schema': TRANSPORT_SCHEMA, 'collector_admission_sha256': self.collector.admission_sha,
                   'head': head, 'request_sha256': digest(blob(request)), 'argv_sha256': digest(canonical(argv)),
                   'generation': request['generation'], 'relative_file': request['relative_file'],
                   'payload_bound': request['max_bytes'], 'stderr_bound': self.config['max_stderr_bytes'],
                   'transport_overhead_reserve_bytes': self.config['transport_overhead_reserve_bytes'],
                   'overhead_is_root_planning_reserve_not_wire_measurement': True,
                   'deadline_utc': self.config['deadline_utc'], 'science_complete': False}
        frozen_head, frozen_attempt = blob(head), blob(attempt)
        require(self.retain(dict(head), attempt) == head, 'root did not retain exact charged head')
        def reservation_check():
            self.check()
            require(blob(head) == frozen_head and blob(attempt) == frozen_attempt
                    and self.collector.head == head, 'transport retained head/reservation changed before spawn')
            current_raw, current_anchor = read(self.collector.records, record_name)
            require(current_raw == record_raw and current_anchor == record_anchor,
                    'transport retained reservation record changed before spawn')
        reservation_check(); self.dispatch_floor = head['sequence']
        name = f'{head["sequence"]:08d}'
        self.log.write_new(name + '-DISPATCH.json', blob(attempt))
        require(read(self.log, name + '-DISPATCH.json')[0] == blob(attempt), 'transport dispatch receipt readback differs')
        # The last two seconds are reserved for killing/reaping this subprocess,
        # inside the original whole-operation allowance, never an extension.
        duration = min(self.config['attempt_timeout_seconds'], remaining, self.check())
        require(duration > 2, 'transport insufficient original deadline for bounded reaping')
        cutoff = time.monotonic() + duration - 2
        def completed(result):
            self.log.check(); self.log.write_new(name + '-RESULT.json', blob({'dispatch_sha256': digest(blob(attempt)), **result}))
        def guarded_popen(argv, **kwargs):
            reservation_check()
            return self.popen(argv, **kwargs)
        return ProcessStream(argv, bound=request['max_bytes'], stderr_bound=self.config['max_stderr_bytes'],
                             cutoff=cutoff, check=self.check, completed=completed, popen=guarded_popen)

    def metadata(self, generation, name, max_bytes, *, expected_bytes=None, expected_sha256=None):
        require(name in ('manifest.json', 'seal.json') and _natural(max_bytes, True) <= MAX_METADATA_BYTES,
                'transport metadata outside trusted bounds')
        request = self._request(generation, name, max_bytes, expected_bytes, expected_sha256)
        ticket = self.collector.reserve_metadata_attempt(max_bytes)
        stream = self._dispatch(request, ticket); data = bytearray()
        try:
            while block := stream.read(min(65536, max_bytes + 1 - len(data))):
                data.extend(block); require(len(data) <= max_bytes, 'transport metadata overread')
        finally:
            stream.close()
        raw = bytes(data)
        require(expected_bytes is None or len(raw) == expected_bytes, 'transport metadata size differs')
        require(expected_sha256 is None or digest(raw) == expected_sha256, 'transport metadata SHA differs')
        self.check(); return raw, ticket

    def bind_group(self, manifest_raw, seal_raw):
        self.check(); m, s = self.collector._validate_group(manifest_raw, seal_raw)
        self.bound_group = (blob(m), m, s)

    def chunk_callback(self, part, chunk):
        self.check(); require(self.bound_group is not None, 'transport group not bound')
        original, m, _ = self.bound_group
        require(blob(m) == original and any(canonical(p) == canonical(part) and chunk in p['codec']['chunks'] for p in m['files']),
                'transport part/chunk changed or outside sealed group')
        head = self.collector.head; raw = read(self.collector.records, f'{head["sequence"]:08d}.json')[0]; value = parse(raw)
        require(value['kind'] == 'chunk' and value['object_sha256'] == chunk['encoded_sha256']
                and value['reserved_bytes'] == chunk['encoded_bytes'] + 1, 'collector did not reserve exact new chunk attempt')
        request = self._request(m['generation'], part['part_directory'] + '/' + f'{chunk["offset"]:020d}.z',
                                chunk['encoded_bytes'], chunk['encoded_bytes'], chunk['encoded_sha256'])
        return self._dispatch(request, {'head': head, 'reservation_sha256': digest(raw)})

    def batch_chunks(self, *, max_frames, max_payload_bytes):
        """Explicit bounded batch callback; retains original Collector pool."""
        return BatchChunks(self, max_frames=max_frames, max_payload_bytes=max_payload_bytes)

    def close(self):
        if not self.closed:
            self.closed = True
            if self.log is not None:
                self.log.close()
            if hasattr(self, 'key_directory'):
                self.key_directory.close()
            self.directory.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def metadata_identity():
    result = {}
    for field, suffix in (('instance_id', 'instance/id'), ('zone', 'instance/zone'), ('project', 'project/project-id')):
        request = urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/' + suffix,
                                         headers={'Metadata-Flavor': 'Google'})
        with urllib.request.urlopen(request, timeout=2) as response:
            raw = response.read(1025)
        require(len(raw) <= 1024, 'guest identity exceeds bound')
        result[field] = raw.decode().strip().split('/')[-1]
    return result


def remote_read(request, *, get_identity=metadata_identity):
    """No guest credentials. Return only a stable sealed leaf after all gates."""
    require(isinstance(request, dict) and set(request) == REQUEST_KEYS, 'remote request shape differs')
    c = request['context']; require(isinstance(c, dict) and set(c) == CONTEXT_KEYS and c['schema'] == TRANSPORT_SCHEMA,
                                    'remote context shape differs')
    require(c['transport_source_sha256'] == IMPORT_SHA == digest(Path(__file__).read_bytes()), 'remote transport source differs')
    require(time.time() < utc(c['deadline_utc']), 'remote original deadline reached')
    require(get_identity() == {key: c[key] for key in ('instance_id', 'zone', 'project')}, 'remote instance identity differs')
    require(source_hash() == c['source_sha256'], 'remote core source differs')
    codec = _codec(); dependencies = {name: digest((Path(codec.__file__).parent / name).read_bytes())
                                     for name in ('cloud_archive.py', 'cloud_control.py')}
    require(c['codec_source_sha256'] == dependencies['cloud_archive.py'] and c['codec_dependencies_sha256'] == dependencies,
            'remote codec differs')
    roots = []
    directories = {}
    def held_directory(path):
        path = Path(path)
        if path not in directories:
            directories[path] = Directory(path); roots.append(directories[path])
        return directories[path]
    def pinned_file(path, expected):
        path = absolute(path); directory = held_directory(path.parent)
        raw, observed = read(directory, path.name)
        require(digest(raw) == expected, 'remote private runtime/transfer input differs')
        return parse(raw), (directory, path.name, observed)
    try:
        runtime, runtime_pin = pinned_file(c['remote_runtime_file'], c['remote_runtime_sha256'])
        require(runtime['provider_identity_sha256'] == c['provider_identity_sha256']
                and runtime['transfer_config_sha256'] == c['remote_transfer_config_sha256']
                and runtime['expected_identity']['instance_id'] == c['instance_id']
                and runtime['expected_identity']['zone'] == c['zone'], 'remote runtime/provider reference differs')
        transfer, transfer_pin = pinned_file(c['remote_transfer_config'], c['remote_transfer_config_sha256'])
        require(set(transfer) == {'stage_id', 'spool_dir', 'ack_dir', 'expected_source_sha256', 'max_spool_bytes', 'max_transfer_bytes', 'deadline_utc'}
                and transfer['stage_id'] == c['stage_id'] and transfer['expected_source_sha256'] == c['source_sha256'],
                'remote transfer stage/source differs')
        root = held_directory(absolute(c['remote_source_root']))
        require(Path(__file__).absolute() == root.path / 'research_tools/compute_only_transport.py',
                'remote executing helper outside frozen package')
        manifest_raw, source_anchor = read(root, 'source-manifest.json')
        require(digest(manifest_raw) == runtime['source_manifest_sha256'], 'remote source package differs')
        package = parse(manifest_raw)
        require(package['commit'] == runtime['source_commit'] and
                package['source_files_sha256']['research_tools/compute_only_transport.py'] == IMPORT_SHA,
                'remote helper not in frozen source package')
        observations = []
        for name, expected in package['source_files_sha256'].items():
            require(isinstance(name, str) and not name.startswith('/') and all(x not in ('', '.', '..') for x in name.split('/')),
                    'remote unsafe source path')
            p = root.path / name; directory = held_directory(p.parent)
            raw, observed = read(directory, p.name, 64 * 1024**2)
            require(digest(raw) == _sha(expected), 'remote source package file differs')
            observations.append((directory, p.name, observed))
        generation = _sha(request['generation']); spool = held_directory(absolute(transfer['spool_dir']))
        group = held_directory(spool.path / 'groups' / generation)
        raw_m, m_anchor = read(group, 'manifest.json'); raw_s, s_anchor = read(group, 'seal.json')
        m, s = parse(raw_m), parse(raw_s)
        ident = {key: m[key] for key in ('stage_id', 'source_sha256', 'config_sha256', 'case_id', 'attempt', 'kind', 'day')}
        require(m['schema'] == s['schema'] == SCHEMA and m['generation'] == s['generation'] == generation == digest(canonical(ident))
                and m['stage_id'] == c['stage_id'] and m['source_sha256'] == c['source_sha256']
                and m['codec_source_sha256'] == c['codec_source_sha256'] and m['codec_dependencies_sha256'] == dependencies
                and s['manifest_sha256'] == digest(raw_m) and s['manifest_bytes'] == len(raw_m), 'remote generation seal/source differs')
        name = request['relative_file']; bound = _natural(request['max_bytes'], True)
        require(bound <= MAX_METADATA_BYTES, 'remote read exceeds trusted maximum')
        if name in ('manifest.json', 'seal.json'):
            raw = raw_m if name == 'manifest.json' else raw_s
        else:
            require(isinstance(name, str) and re.fullmatch(r'parts/[0-9]{5}/[0-9]{20}\.z', name), 'remote file outside sealed group')
            match = [(part, chunk) for part in m['files'] for chunk in part['codec']['chunks']
                     if part['part_directory'] + '/' + f'{chunk["offset"]:020d}.z' == name]
            require(len(match) == 1, 'remote chunk not uniquely in sealed roster')
            part, chunk = match[0]; codec.validate_manifest(part['codec'])
            require(request['expected_bytes'] == chunk['encoded_bytes'] and request['expected_sha256'] == chunk['encoded_sha256'],
                    'remote requested chunk pin differs')
            path = group.path / name; directory = held_directory(path.parent)
            raw, observed = read(directory, path.name, bound); observations.append((directory, path.name, observed))
        require(len(raw) <= bound and (request['expected_bytes'] is None or type(request['expected_bytes']) is int
                and len(raw) == request['expected_bytes']) and (request['expected_sha256'] is None
                or digest(raw) == _sha(request['expected_sha256'])), 'remote stable bytes differ')
        observations.extend([runtime_pin, transfer_pin, (root, 'source-manifest.json', source_anchor),
                             (group, 'manifest.json', m_anchor), (group, 'seal.json', s_anchor)])
        for directory, leaf, observed in observations:
            directory.check(); require(identity(os.stat(leaf, dir_fd=directory.fd, follow_symlinks=False)) == observed,
                                       'remote bytes/namespace drifted before emission')
        require(time.time() < utc(c['deadline_utc']), 'remote read reached deadline')
        return raw
    finally:
        for directory in reversed(roots):
            directory.close()


def remote_entry(token):
    try:
        require(isinstance(token, str) and len(token) <= 32768, 'remote request exceeds bound')
        raw = base64.b64decode(token, validate=True); request = parse(raw)
        data = remote_read(request)
        view = memoryview(data)
        while view:
            n = os.write(1, view); require(n > 0, 'remote stdout short write'); view = view[n:]
    except BaseException:
        os.write(2, b'COMPUTE_ONLY_REMOTE_READ_REFUSED\n')
        raise SystemExit(2) from None


def no_storage_scope_plan(legacy_argv, *, original_argv_sha256, prepare_only_startup, startup_sha256):
    """Pure plan transform. Never executes create and never approves spending."""
    require(isinstance(legacy_argv, list) and all(isinstance(x, str) for x in legacy_argv)
            and legacy_argv[:4] == ['gcloud', 'compute', 'instances', 'create']
            and digest(canonical(legacy_argv)) == _sha(original_argv_sha256), 'original root create plan differs')
    accounts = [x for x in legacy_argv if x.startswith('--service-account=')]
    scopes = [x for x in legacy_argv if x.startswith('--scopes=')]
    require(len(accounts) == len(scopes) == 1 and scopes == ['--scopes=https://www.googleapis.com/auth/devstorage.read_write']
            and not any(x in ('--no-service-account', '--no-scopes') for x in legacy_argv), 'original storage credential plan unexpected')
    require('--instance-termination-action=DELETE' in legacy_argv and '--boot-disk-auto-delete' in legacy_argv
            and sum(x.startswith('--termination-time=') for x in legacy_argv) == 1,
            'root create plan lacks original absolute deletion')
    old_scripts = [x for x in legacy_argv if x.startswith('--metadata-from-file=')]
    require(len(old_scripts) == 1 and old_scripts[0].startswith('--metadata-from-file=startup-script='),
            'root startup script namespace differs')
    startup = absolute(str(prepare_only_startup))
    directory = Directory(startup.parent)
    try:
        raw, _ = read(directory, startup.name, 4 * 1024**2)
        require(digest(raw) == _sha(startup_sha256), 'approved compute-only bootstrap bytes differ')
    finally:
        directory.close()
    argv = [x for x in legacy_argv if x not in accounts + scopes + old_scripts]
    argv += ['--no-service-account', '--no-scopes', '--metadata-from-file=startup-script=' + str(startup)]
    return {'schema': TRANSPORT_SCHEMA, 'argv': argv, 'argv_sha256': digest(canonical(argv)),
            'original_argv_sha256': original_argv_sha256, 'root_paid_admission_required': True,
            'provider_readback_required': True, 'startup_sha256': startup_sha256,
            'bootstrap_behavior_requires_root_review': True, 'created': False, 'science_complete': False}

# Optional batch seam; existing single-fetch API above is unchanged.
BATCH_SCHEMA = 'dams-compute-only-iap-batch-v1'
BATCH_INPUT_BOUND = 2048
BATCH_OUTPUT_BOUND = 2048
BATCH_SESSION_INPUT_BOUND = 32768
BATCH_TERMINAL_BOUND = 256
BATCH_MAX_FRAMES = 1024
BATCH_MAX_PAYLOAD = 1024**3
BATCH_KEYS = {'schema', 'context', 'generation', 'manifest_sha256', 'seal_sha256',
              'max_frames', 'max_payload_bytes'}
FRAME_KEYS = {'index', 'request_sha256', 'relative_file', 'bytes', 'sha256'}


def _framed(raw):
    require(isinstance(raw, bytes) and len(raw) <= BATCH_OUTPUT_BOUND, 'batch frame header bound')
    return len(raw).to_bytes(4, 'big') + raw


def _exact(stream, count, check):
    require(type(count) is int and 0 <= count <= MAX_METADATA_BYTES, 'batch exact read bound')
    result = bytearray()
    while len(result) < count:
        check(); part = stream.read(min(65536, count - len(result)))
        require(isinstance(part, bytes) and 0 < len(part) <= count - len(result), 'batch frame truncated/invalid stream')
        result.extend(part)
    check(); return bytes(result)


def _frame(stream, check, bound=BATCH_OUTPUT_BOUND):
    count = int.from_bytes(_exact(stream, 4, check), 'big')
    require(0 < count <= bound, 'batch framed metadata bound')
    raw = _exact(stream, count, check); value = parse(raw)
    require(raw == blob(value), 'batch frame metadata is not canonical')
    return value, 4 + count


class BatchChunks:
    """One SSH process, sequential exact callbacks, original charged history.

    Caps only restrict the sealed roster/remaining original allowance. They do
    not create another request/byte pool or extend either existing deadline.
    """
    def __init__(self, transport, *, max_frames, max_payload_bytes):
        require(isinstance(transport, IAPTransport), 'batch original transport required')
        self.transport = transport; self.collector = transport.collector
        transport.check(); require(transport.bound_group is not None, 'batch sealed group not bound')
        original, self.m, self.seal = transport.bound_group
        self.original = original
        require(type(max_frames) is int and 1 <= max_frames <= BATCH_MAX_FRAMES
                and type(max_payload_bytes) is int and 0 < max_payload_bytes <= BATCH_MAX_PAYLOAD,
                'batch explicit operation caps invalid')
        require(max_frames <= self.collector.config['max_fetch_requests']
                and max_payload_bytes <= self.collector.config['max_fetch_bytes'],
                'batch operation caps exceed original admitted pool')
        self.plan = []; seen = {}; cached = set(os.listdir(self.collector.cache.fd))
        for part in self.m['files']:
            for chunk in part['codec']['chunks']:
                key = chunk['encoded_sha256']; size = chunk['encoded_bytes']; name = key + '.z'
                if key in seen:
                    require(seen[key] == size, 'batch repeated CID has different byte length'); continue
                seen[key] = size
                if name in cached:
                    from research_tools.compute_only_control import file_hash
                    require(file_hash(self.collector.cache, name, size, transport.check)[0] == key,
                            'batch existing immutable cache differs'); continue
                request = transport._request(self.m['generation'], part['part_directory'] + '/' + f'{chunk["offset"]:020d}.z',
                                             size, size, key)
                self.plan.append((digest(blob(part)), blob(chunk), request))
        require(len(self.plan) <= max_frames and sum(x[2]['expected_bytes'] for x in self.plan) <= max_payload_bytes,
                'batch sealed missing roster exceeds operation caps')
        self.request = {'schema': BATCH_SCHEMA, 'context': {key: transport.config[key] for key in CONTEXT_KEYS},
                        'generation': self.m['generation'], 'manifest_sha256': self.seal['manifest_sha256'],
                        'seal_sha256': digest(blob(self.seal)), 'max_frames': max_frames, 'max_payload_bytes': max_payload_bytes}
        self.request_raw = blob(self.request); self.context_sha = digest(self.request_raw)
        require(len(base64.b64encode(self.request_raw)) <= BATCH_SESSION_INPUT_BOUND, 'batch initial request bound')
        self.plan = tuple(self.plan); self.plan_identity = self.plan
        self.plan_request_sha = tuple(digest(blob(x[2])) for x in self.plan)
        self.index = 0; self.stream = None
        self.finished = not self.plan; self.done = self.finished; self.failed = False; self.closed = False; self.active = None
        self.cutoff = None; self.output_allowance = BATCH_TERMINAL_BOUND + 1
        self.session_ticket = None; self.frame_tickets = []; self.session_name = None

    def check(self):
        require(not self.closed and not self.failed, 'batch closed/failed')
        self.transport.check()
        require(self.plan is self.plan_identity and blob(self.request) == self.request_raw, 'batch frozen roster/context changed')
        if self.cutoff is not None and not self.done and time.monotonic() >= self.cutoff:
            raise TimeoutError('batch fixed session deadline')

    def _roster_check(self):
        self.check(); require(blob(self.m)==self.original, 'batch sealed group roster changed')

    def _metadata_reserve(self, bound):
        # Collector.collect already holds its original exclusive lock across
        # _fetch_chunk and this callback; a second flock would deadlock/refuse.
        sequence, sha = self.collector._reserve('metadata', bound + 1,
            digest(canonical({'metadata_attempt': self.collector.head['sequence'] + 1})))
        return {'sequence': sequence, 'reservation_sha256': sha, 'head': self.collector.head}

    def _ticket(self, ticket):
        raw, anchor = read(self.collector.records, f'{ticket["sequence"]:08d}.json')
        require(digest(raw) == ticket['reservation_sha256'], 'batch charged reservation differs')
        return (f'{ticket["sequence"]:08d}.json', raw, anchor)

    def _retained(self, tickets, request, frame):
        observations = [self._ticket(t) for t in tickets]; head = self.collector.head
        attempt = {'schema': BATCH_SCHEMA, 'collector_admission_sha256': self.collector.admission_sha,
                   'head': head, 'context_sha256': self.context_sha, 'generation': self.m['generation'],
                   'frame': frame, 'request_sha256': digest(blob(request)),
                   'reservation_sha256': [digest(raw) for _, raw, _ in observations],
                   'deadline_utc': self.transport.config['deadline_utc'], 'science_complete': False,
                   'wire_bytes_measured': False, 'framing_reservations_are_application_bounds': True}
        head_raw, attempt_raw, request_raw = blob(head), blob(attempt), blob(request)
        require(self.transport.retain(dict(head), attempt) == head, 'batch root did not retain exact charged head')
        def gate():
            self.check(); require(blob(head) == head_raw and blob(attempt) == attempt_raw and blob(request) == request_raw and self.collector.head == head,
                                  'batch retained exact head changed before I/O')
            for name, raw, anchor in observations:
                require(read(self.collector.records, name) == (raw, anchor), 'batch retained reservation changed before I/O')
        gate(); require(head['sequence'] > self.transport.dispatch_floor, 'batch charged head stale/reused')
        name = f'{head["sequence"]:08d}-BATCH-{frame:04d}-DISPATCH.json'
        self.transport.log.write_new(name, attempt_raw)
        require(read(self.transport.log, name)[0] == attempt_raw, 'batch dispatch readback differs')
        gate(); self.transport.dispatch_floor = head['sequence']
        return gate

    def _start(self, gate):
        remaining = self.transport.check(); duration = min(remaining, self.transport.config['attempt_timeout_seconds'])
        require(duration > 2, 'batch original deadline lacks reap margin')
        self.cutoff = time.monotonic() + duration - 2
        c = self.transport.config; token = base64.b64encode(self.request_raw).decode('ascii')
        script = ('import sys;sys.path[:0]=' + repr([c['remote_source_root'], c['remote_source_root'] + '/research_tools'])
                  + ';from research_tools.compute_only_transport import remote_batch_entry;remote_batch_entry(' + repr(token) + ')')
        argv = self.transport.argv(self.plan[0][2])
        argv[-1] = '--command=/usr/bin/sudo -n /usr/bin/python3 -B -c ' + shlex.quote(script)
        self.remote_command_bytes = argv[-1].encode('utf-8')
        require(len(self.remote_command_bytes) <= BATCH_SESSION_INPUT_BOUND, 'batch remote command exceeds reserved input')
        def spawn(argv, **kwargs):
            gate(); kwargs['stdin'] = subprocess.PIPE
            return self.transport.popen(argv, **kwargs)
        def completed(result):
            self.transport.log.check()
            self.transport.log.write_new(self.session_name + '-RESULT.json', blob({'schema': BATCH_SCHEMA, **result}))
        self.stream = ProcessStream(argv, bound=self.output_allowance, stderr_bound=c['max_stderr_bytes'],
                                    cutoff=self.cutoff, check=self.check, completed=completed, popen=spawn)
        os.set_blocking(self.stream.proc.stdin.fileno(), False)

    def _write(self, raw, gate):
        require(isinstance(raw, bytes) and len(raw) <= BATCH_INPUT_BOUND, 'batch request input bound')
        view = memoryview(raw); fd = self.stream.proc.stdin.fileno()
        while view:
            gate()
            try:n = os.write(fd, view)
            except BlockingIOError:
                import select
                select.select([], [fd], [], min(.05, max(0, self.cutoff - time.monotonic()))); continue
            require(n > 0, 'batch request stdin short write'); view = view[n:]
        gate()

    def callback(self, part, chunk):
        try:
            self._roster_check(); require(self.active is None and self.index < len(self.plan), 'batch callback order/active frame differs')
            p, ch, request = self.plan[self.index]
            require(digest(blob(part)) == p and blob(chunk) == ch and digest(blob(request)) == self.plan_request_sha[self.index],
                    'batch callback outside ordered sealed allowlist')
            head = self.collector.head; record_raw, _ = read(self.collector.records, f'{head["sequence"]:08d}.json'); record = parse(record_raw)
            require(record['kind'] == 'chunk' and record['object_sha256'] == chunk['encoded_sha256']
                    and record['reserved_bytes'] == chunk['encoded_bytes'] + 1
                    and head['sequence'] > self.transport.dispatch_floor, 'batch exact original chunk reservation missing/stale')
            chunk_ticket = {'sequence': head['sequence'], 'reservation_sha256': digest(record_raw)}
            tickets = [chunk_ticket]
            if self.stream is None:
                self.session_ticket = self._metadata_reserve(
                    BATCH_SESSION_INPUT_BOUND + self.transport.config['max_stderr_bytes'] + BATCH_TERMINAL_BOUND)
                tickets.append(self.session_ticket); self.session_name = f'{self.session_ticket["sequence"]:08d}-BATCH'
            framing = self._metadata_reserve(BATCH_INPUT_BOUND + BATCH_OUTPUT_BOUND)
            tickets.append(framing); self.frame_tickets.append(framing)
            frame = {'index': self.index, 'request_sha256': digest(blob(request)), 'relative_file': request['relative_file'],
                     'bytes': chunk['encoded_bytes'], 'sha256': chunk['encoded_sha256']}
            outgoing = blob(frame); require(len(outgoing) <= BATCH_INPUT_BOUND, 'batch outgoing frame exceeds reserved input')
            self.output_allowance += chunk['encoded_bytes'] + BATCH_OUTPUT_BOUND + 1
            gate = self._retained(tickets, request, self.index)
            if self.stream is None:self._start(gate)
            else:self.stream.bound = self.output_allowance
            self._write(outgoing, gate)
            header, metadata = _frame(self.stream, self.check)
            require(isinstance(header, dict) and type(header.get('index')) is int and type(header.get('bytes')) is int
                    and header == {'schema': BATCH_SCHEMA, 'context_sha256': self.context_sha, **frame},
                    'batch exact response frame identity/length/hash differs')
            payload = _exact(self.stream, chunk['encoded_bytes'], self.check)
            footer, extra = _frame(self.stream, self.check); metadata += extra
            require(isinstance(footer, dict) and type(footer.get('index')) is int
                    and footer == {'schema': BATCH_SCHEMA, 'index': self.index, 'request_sha256': frame['request_sha256'],
                               'sha256': chunk['encoded_sha256'], 'status': 'ok'}
                    and digest(payload) == chunk['encoded_sha256'] and metadata <= BATCH_OUTPUT_BOUND,
                    'batch footer/payload checksum differs')
            self.collector._accept_metadata(framing, outgoing + _framed(blob(header)) + _framed(blob(footer)))
            self.index += 1
            if self.index == len(self.plan):self._finish()
            self.active = BatchFrame(self, payload)
            return self.active
        except BaseException:
            self.abort(); raise

    def _finish(self):
        self._roster_check(); self.stream.proc.stdin.close()
        value, terminal_bytes = _frame(self.stream, self.check, BATCH_TERMINAL_BOUND)
        require(isinstance(value, dict) and type(value.get('frames')) is int
                and value == {'schema': BATCH_SCHEMA, 'context_sha256': self.context_sha, 'frames': self.index, 'status': 'complete'},
                'batch terminal receipt differs')
        require(self.stream.read(1) == b'', 'batch trailing output after complete')
        self.stream.close(); self._roster_check(); self.finished = True
        # Only opaque protocol/diagnostic evidence, never stderr plaintext.
        name, raw, _ = self._ticket(self.session_ticket)
        observed = len(self.remote_command_bytes) + terminal_bytes + self.stream.stderr_bytes
        require(observed < parse(raw)['reserved_bytes'], 'batch session metadata exceeds charged bound')
        self.collector.results.write_new(name, blob({'reservation_sha256': digest(raw),
            'observed_bytes': observed, 'status': 'verified', 'payload_sha256': None}))

    def abort(self):
        self.failed = True
        if self.stream is not None:
            try:self.stream.proc.stdin.close()
            except (BrokenPipeError, ValueError):pass
            try:self.stream.failed = True; self.stream.close()
            except BaseException:pass

    def close(self):
        if self.closed:return
        try:
            if not self.failed:
                self.check(); require(self.stream is None or self.done and self.active is None, 'batch closed before all exact frames consumed')
        except BaseException:
            self.abort(); raise
        finally:
            if self.stream is not None and not self.stream.closed:self.abort()
            self.closed = True

    def __enter__(self):return self
    def __exit__(self, kind, value, traceback):
        if kind is not None:self.abort()
        self.close()


class BatchFrame:
    def __init__(self, batch, raw):
        self.batch = batch; self.buffer = io.BytesIO(raw); self.eof = False; self.closed = False
    def read(self, size):
        try:
            require(type(size) is int and size > 0 and not self.closed, 'batch payload read bound')
            self.batch.check(); data = self.buffer.read(size); self.eof = not data
            self.batch.check(); return data
        except BaseException:
            self.batch.abort(); raise
    def close(self):
        if self.closed:return
        self.closed = True
        try:
            require(self.eof, 'batch payload closed before consumer exact EOF')
            self.batch.check()
        except BaseException:
            self.batch.abort(); raise
        finally:
            self.buffer.close(); self.batch.active = None
        if self.batch.finished:self.batch.done = True



class RemoteBatchReader:
    """Full package read once, held namespace/file observations per frame."""
    def __init__(self, initial, *, get_identity=metadata_identity):
        self.directories = {}; self.observations = []; self.closed = False
        self.initial_raw = blob(initial); self.context_sha = digest(self.initial_raw); self.initial = parse(self.initial_raw)
        require(isinstance(initial, dict) and set(initial) == BATCH_KEYS and initial['schema'] == BATCH_SCHEMA,
                'remote batch initial shape differs')
        c = self.context = self.initial['context']
        require(isinstance(c, dict) and set(c) == CONTEXT_KEYS and c['schema'] == TRANSPORT_SCHEMA
                and c['transport_source_sha256'] == IMPORT_SHA == digest(Path(__file__).read_bytes()),
                'remote batch context/source differs')
        require(type(initial['max_frames']) is int and 1 <= initial['max_frames'] <= BATCH_MAX_FRAMES
                and type(initial['max_payload_bytes']) is int and 0 < initial['max_payload_bytes'] <= BATCH_MAX_PAYLOAD,
                'remote batch operation caps differ')
        for name in ('manifest_sha256','seal_sha256','generation'):_sha(initial[name])
        self.wall, self.mono = time.time(), time.monotonic(); self.deadline = utc(c['deadline_utc'])
        self.index = 0; self.total = 0; self.seen = set()
        try:
            self.check()
            require(get_identity() == {key: c[key] for key in ('instance_id', 'zone', 'project')}, 'remote batch instance identity differs')
            require(source_hash() == c['source_sha256'], 'remote batch core source differs')
            self.codec = _codec(); dependencies = {name: digest((Path(self.codec.__file__).parent/name).read_bytes())
                for name in ('cloud_archive.py','cloud_control.py')}
            require(c['codec_source_sha256'] == dependencies['cloud_archive.py'] and c['codec_dependencies_sha256'] == dependencies,
                    'remote batch codec differs')
            runtime = self.pinned(c['remote_runtime_file'], c['remote_runtime_sha256'])
            require(runtime['provider_identity_sha256'] == c['provider_identity_sha256']
                    and runtime['transfer_config_sha256'] == c['remote_transfer_config_sha256']
                    and runtime['expected_identity']['instance_id'] == c['instance_id']
                    and runtime['expected_identity']['zone'] == c['zone'], 'remote batch runtime/provider differs')
            transfer = self.pinned(c['remote_transfer_config'], c['remote_transfer_config_sha256'])
            require(set(transfer) == {'stage_id','spool_dir','ack_dir','expected_source_sha256','max_spool_bytes','max_transfer_bytes','deadline_utc'}
                    and transfer['stage_id'] == c['stage_id'] and transfer['expected_source_sha256'] == c['source_sha256']
                    and transfer['deadline_utc'] == c['deadline_utc']
                    and initial['max_payload_bytes'] <= _natural(transfer['max_transfer_bytes'],True),
                    'remote batch original transfer/source/deadline/cap differs')
            root = self.held(absolute(c['remote_source_root']))
            require(Path(__file__).absolute() == root.path/'research_tools/compute_only_transport.py', 'remote batch helper outside package')
            raw = self.observed(root, 'source-manifest.json'); require(digest(raw) == runtime['source_manifest_sha256'], 'remote batch package SHA differs')
            package = parse(raw)
            require(isinstance(package,dict) and set(package) == {'commit','source_files_sha256'}
                    and package['commit'] == runtime['source_commit']
                    and package['source_files_sha256']['research_tools/compute_only_transport.py'] == IMPORT_SHA,
                    'remote batch helper/commit pin differs')
            for name, expected in package['source_files_sha256'].items():
                require(isinstance(name,str) and not name.startswith('/') and all(x not in ('','.','..') for x in name.split('/')),
                        'remote batch source path differs')
                path = root.path/name; directory = self.held(path.parent)
                require(digest(self.observed(directory,path.name,64*1024**2)) == _sha(expected), 'remote batch package bytes differ')
            group = self.held(absolute(transfer['spool_dir'])/'groups'/initial['generation'])
            raw_m = self.observed(group,'manifest.json'); raw_s = self.observed(group,'seal.json')
            require(digest(raw_m) == initial['manifest_sha256'] and digest(raw_s) == initial['seal_sha256'], 'remote batch group pin differs')
            m,s = parse(raw_m),parse(raw_s)
            ident = {key:m[key] for key in ('stage_id','source_sha256','config_sha256','case_id','attempt','kind','day')}
            require(m['schema'] == s['schema'] == SCHEMA and m['generation'] == s['generation'] == initial['generation'] == digest(canonical(ident))
                    and m['stage_id'] == c['stage_id'] and m['source_sha256'] == c['source_sha256']
                    and m['codec_source_sha256'] == c['codec_source_sha256'] and m['codec_dependencies_sha256'] == dependencies
                    and s['manifest_sha256'] == digest(raw_m) and s['manifest_bytes'] == len(raw_m), 'remote batch generation seal differs')
            self.allow = {}
            for part in m['files']:
                require(isinstance(part['part_directory'],str) and re.fullmatch(r'parts/[0-9]{5}',part['part_directory']), 'remote batch part path differs')
                self.codec.validate_manifest(part['codec'])
                for chunk in part['codec']['chunks']:
                    name = part['part_directory']+'/'+f'{chunk["offset"]:020d}.z'
                    require(name not in self.allow, 'remote batch chunk path duplicated')
                    self.allow[name] = (group.path/name, chunk)
            self.check()
        except BaseException:
            self.close(); raise

    def held(self,path):
        path=Path(path)
        if path not in self.directories:self.directories[path]=Directory(path)
        return self.directories[path]

    def observed(self,directory,name,bound=MAX_METADATA_BYTES):
        raw,anchor=read(directory,name,bound);self.observations.append((directory,name,anchor));return raw

    def pinned(self,path,expected):
        path=absolute(path);directory=self.held(path.parent);raw=self.observed(directory,path.name)
        require(digest(raw)==_sha(expected),'remote batch runtime/transfer input differs')
        return parse(raw)

    def check(self):
        require(not self.closed,'remote batch reader closed')
        require(blob(self.initial)==self.initial_raw,'remote batch frozen context changed')
        now=max(time.time(),self.wall+time.monotonic()-self.mono)
        if now>=self.deadline:raise TimeoutError('remote batch original UTC deadline')
        for directory,name,anchor in self.observations:
            directory.check();require(identity(os.stat(name,dir_fd=directory.fd,follow_symlinks=False))==anchor,
                                      'remote batch retained package/runtime/group/chunk drift')

    def read_frame(self,frame):
        self.check();require(isinstance(frame,dict) and set(frame)==FRAME_KEYS
            and type(frame['index']) is int and frame['index']==self.index
            and self.index<self.initial['max_frames'], 'remote batch frame index/type differs')
        name=frame['relative_file'];require(isinstance(name,str) and name in self.allow,'remote batch frame outside exact allowlist')
        path,chunk=self.allow[name]
        require(type(frame['bytes']) is int and frame['bytes']==chunk['encoded_bytes']
                and frame['sha256']==chunk['encoded_sha256'] and frame['sha256'] not in self.seen,
                'remote batch chunk length/hash/duplicate differs')
        request={'context':self.context,'generation':self.initial['generation'],'relative_file':name,
                 'max_bytes':chunk['encoded_bytes'],'expected_bytes':chunk['encoded_bytes'],'expected_sha256':chunk['encoded_sha256']}
        require(frame['request_sha256']==digest(blob(request)),'remote batch request ID differs')
        require(self.total+chunk['encoded_bytes']<=self.initial['max_payload_bytes'],'remote batch payload cap exceeded')
        directory=self.held(path.parent);raw=self.observed(directory,path.name,chunk['encoded_bytes'])
        require(len(raw)==chunk['encoded_bytes'] and digest(raw)==chunk['encoded_sha256'],'remote batch actual chunk differs')
        self.check();self.index+=1;self.total+=len(raw);self.seen.add(frame['sha256']);return raw

    def close(self):
        if self.closed:return
        self.closed=True
        for directory in reversed(list(self.directories.values())):directory.close()


def remote_batch_entry(token):
    reader=None;selector=None
    try:
        require(isinstance(token,str) and len(token)<=BATCH_SESSION_INPUT_BOUND,'remote batch initial bound')
        raw=base64.b64decode(token,validate=True);initial=parse(raw)
        require(blob(initial)==raw,'remote batch initial is not canonical')
        reader=RemoteBatchReader(initial);context=reader.context_sha
        # Controller loss does not leave an unbounded blocked stdin/stdout.
        os.set_blocking(0,False);os.set_blocking(1,False);selector=selectors.DefaultSelector();selector.register(0,selectors.EVENT_READ)
        pending=bytearray()
        def output(raw):
            import select
            view=memoryview(raw)
            while view:
                reader.check()
                try:n=os.write(1,view)
                except BlockingIOError:
                    select.select([],[1],[],.05);continue
                require(n>0,'remote batch stdout short write');view=view[n:]
        while True:
            reader.check()
            if b'\n' not in pending:
                if not selector.select(.1):continue
                block=os.read(0,min(65536,BATCH_INPUT_BOUND-len(pending)+1))
                if not block:
                    require(not pending,'remote batch stdin truncated request')
                    require(reader.index>0,'remote batch empty request stream')
                    reader.check();output(_framed(blob({'schema':BATCH_SCHEMA,'context_sha256':context,'frames':reader.index,'status':'complete'})))
                    reader.check();break
                pending.extend(block);require(len(pending)<=BATCH_INPUT_BOUND,'remote batch stdin bound')
                if b'\n' not in pending:continue
            line,_,remaining=pending.partition(b'\n');pending=bytearray(remaining);encoded=bytes(line)+b'\n';frame=parse(encoded)
            require(blob(frame)==encoded,'remote batch request not canonical')
            payload=reader.read_frame(frame);reader.check()
            output(_framed(blob({'schema':BATCH_SCHEMA,'context_sha256':context,**frame})))
            output(payload)
            output(_framed(blob({'schema':BATCH_SCHEMA,'index':frame['index'],'request_sha256':frame['request_sha256'],
                                'sha256':frame['sha256'],'status':'ok'})))
            reader.check()
    except BaseException:
        # No request values, paths, runtime data, credentials or signed URLs.
        os.write(2,b'COMPUTE_ONLY_REMOTE_BATCH_REFUSED\n')
        raise SystemExit(2) from None
    finally:
        if selector is not None:selector.close()
        if reader is not None:reader.close()
