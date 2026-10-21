"""Provider-free durable compute-only collector, never a cloud coordinator.

Callbacks supply bounded binary streams. Conservative attempted payload holds
survive failures/retries; they are not wire, billing or authenticated counters.
Only two exact physical restorations and existing scientific I/O gates admit ACK.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time

from dams_sim._committed_pages import Directory, identity
from dams_sim.config import Config
from dams_sim.longitudinal_model import verify_snapshot
from dams_sim.longitudinal_pipeline import driver_hash
from dams_sim.spec import case_key
from dams_sim.storage import canonical, digest, source_hash
from dams_sim.transfer_spool import SCHEMA, MAX_FILES, MAX_METADATA_BYTES, _codec, _name, _sha, _natural
from research_tools.validate_longitudinal import CheckedLongitudinalCases,iter_journal_rows

COLLECTOR_SCHEMA = 'dams-compute-only-collector-v1'
KEYS = {'schema', 'stage_id', 'source_sha256', 'pipeline_driver_sha256', 'spec_sha256',
        'inventory_sha256', 'assignment_sha256', 'codec_source_sha256', 'codec_dependencies_sha256',
        'collector_source_sha256', 'deadline_utc', 'max_fetch_requests', 'max_fetch_bytes',
        'max_group_raw_bytes', 'min_free_bytes', 'state_dir', 'cache_dir', 'copy_dirs'}
ASSIGNMENT_KEYS = {'schema', 'source_sha256', 'pipeline_driver_sha256', 'spec_sha256',
                   'spec', 'inventory_sha256', 'cases'}
MANIFEST_KEYS = {'schema', 'generation', 'stage_id', 'source_sha256', 'config_sha256', 'case_id',
                 'attempt', 'kind', 'day', 'group_sha256', 'sealed', 'files',
                 'codec_source_sha256', 'codec_dependencies_sha256'}
SEAL_KEYS = {'schema', 'generation', 'manifest_sha256', 'group_sha256', 'manifest_bytes', 'encoded_bytes'}
COLLECTOR_IMPORT_SHA256 = digest(Path(__file__).read_bytes())


def require(value, reason):
    if not value:
        raise ValueError(reason)


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate collector JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite collector JSON')))


def blob(value):
    return canonical(value) + b'\n'


def child(parent, name):
    require(Path(_name(name)).name == name, 'unsafe collector directory')
    parent.check()
    try:
        os.mkdir(name, 0o700, dir_fd=parent.fd)
        os.fsync(parent.fd)
    except FileExistsError:
        pass
    result = Directory(parent.path / name)
    parent.check()
    return result


def read(directory, name, bound=MAX_METADATA_BYTES):
    directory.check()
    before = os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'collector leaf is not private regular bytes')
    raw, observed = directory.read_observed(name, bound)
    require(identity(before) == observed, 'collector metadata read changed')
    return raw, observed


def file_hash(directory, name, expected_bytes, check):
    directory.check()
    fd = directory.open(name, os.O_RDONLY | os.O_NONBLOCK)
    h = hashlib.sha256(); length = 0
    try:
        info = os.fstat(fd); observed = identity(info)
        require(info.st_nlink == 1 and info.st_size == expected_bytes, 'collector physical file size/link differs')
        while data := os.read(fd, 4 * 1024**2):
            check(); h.update(data); length += len(data)
            require(length <= expected_bytes, 'collector file grew')
        require(length == expected_bytes and identity(os.fstat(fd)) == observed, 'collector file read drift')
        directory.matches(name, fd)
    finally:
        os.close(fd)
    directory.check()
    require(identity(os.stat(name, dir_fd=directory.fd, follow_symlinks=False)) == observed,
            'collector closed file changed')
    return h.hexdigest(), observed


def leaf(root, name):
    parts = _name(name).split('/')
    current = root; opened = []
    try:
        for part in parts[:-1]:
            current = child(current, part); opened.append(current)
        return current, parts[-1], opened
    except BaseException:
        for directory in reversed(opened):
            directory.close()
        raise


def exact_tree(root, expected):
    root.check(); found = set()
    for parent, dirs, files in os.walk(root.path, followlinks=False):
        with_dir = Directory(parent)
        try:
            for name in dirs:
                info = os.stat(name, dir_fd=with_dir.fd, follow_symlinks=False)
                require(stat.S_ISDIR(info.st_mode), 'unsafe restored directory')
            for name in files:
                info = os.stat(name, dir_fd=with_dir.fd, follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe restored file')
                found.add((Path(parent) / name).relative_to(root.path).as_posix())
        finally:
            with_dir.close()
    require(found == set(expected), 'restored whole file roster differs')
    root.check()


class Collector:
    """One immutable binding and original append-only attempted-fetch budget.

    Existing state requires a separately retained known head on every reopen.
    Complete rollback of both this namespace AND all external pins is outside
    this contract. Two copies on the same device share a device failure domain.
    """
    def __init__(self, admission_file, *, admission_sha256, assignment_raw, minimum_head=None):
        self.closed = False; self.opened = []
        try:
            self._initialize(admission_file, admission_sha256, assignment_raw, minimum_head)
        except BaseException:
            self.close()
            raise

    def _initialize(self, admission_file, admission_sha256, assignment_raw, minimum_head):
        self.closed = False; self.opened = []
        path = Path(admission_file).absolute()
        admission_dir = Directory(path.parent)
        self.opened.append(admission_dir)
        raw, observed = read(admission_dir, path.name)
        require(digest(raw) == _sha(admission_sha256), 'external admission bytes differ')
        self.admission_observation = (admission_dir, path.name, raw, observed)
        self.admission_sha = admission_sha256; self.config = parse(raw)
        c = self.config
        require(isinstance(c, dict) and set(c) == KEYS and c['schema'] == COLLECTOR_SCHEMA,
                'collector admission shape differs')
        for name in ('source_sha256', 'pipeline_driver_sha256', 'spec_sha256', 'inventory_sha256',
                     'assignment_sha256', 'codec_source_sha256', 'collector_source_sha256'):
            _sha(c[name])
        require(isinstance(c['stage_id'], str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', c['stage_id']),
                'collector stage differs')
        self.codec = _codec()
        self.module_bytes = Path(__file__).read_bytes()
        require(digest(self.module_bytes) == c['collector_source_sha256'] == COLLECTOR_IMPORT_SHA256,
                'collector runtime source differs')
        self.dependencies = {name: digest((Path(self.codec.__file__).parent / name).read_bytes())
                             for name in ('cloud_control.py', 'cloud_archive.py')}
        require(c['codec_source_sha256'] == self.dependencies['cloud_archive.py']
                and c['codec_dependencies_sha256'] == self.dependencies, 'collector codec dependency differs')
        require(source_hash() == c['source_sha256'] and driver_hash() == c['pipeline_driver_sha256'],
                'collector executing source/driver differs')
        core_dir = Path(__file__).absolute().parents[1] / 'dams_sim'
        self.core_names = {p.name for p in core_dir.glob('*.py')}
        source_paths = set(core_dir.glob('*.py')) | {Path(__file__).absolute(),
            Path(self.codec.__file__).parent / 'cloud_control.py', Path(self.codec.__file__).parent / 'cloud_archive.py'}
        from research_tools import validate_longitudinal, longitudinal_derived
        source_paths |= {Path(validate_longitudinal.__file__), Path(longitudinal_derived.__file__)}
        self.source_observations = {p: identity(p.lstat()) for p in source_paths}
        self.core_dir = core_dir
        self.source_directory = Directory(core_dir.parent); self.opened.append(self.source_directory)
        for name in ('max_fetch_requests', 'max_fetch_bytes', 'max_group_raw_bytes'):
            _natural(c[name], True)
        _natural(c['min_free_bytes'])
        require(isinstance(c['deadline_utc'], str), 'collector UTC deadline differs')
        date = datetime.fromisoformat(c['deadline_utc'].replace('Z', '+00:00'))
        require(date.tzinfo is not None and date.utcoffset().total_seconds() == 0, 'collector deadline must be UTC')
        self.deadline = date.timestamp(); self.clock_wall = time.time(); self.clock_monotonic = time.monotonic()
        require(isinstance(c['copy_dirs'], list) and len(c['copy_dirs']) == 2, 'collector requires two destinations')
        paths = [Path(c[k]) for k in ('state_dir', 'cache_dir')] + [Path(p) for p in c['copy_dirs']]
        require(all(p.is_absolute() for p in paths) and len(set(paths)) == 4
                and not any(a in b.parents for a in paths for b in paths if a != b), 'collector namespaces overlap')
        operational = []
        for p in paths:
            d = Directory(p); operational.append(d); self.opened.append(d)
        self.state, self.cache, *self.copies = operational
        require(len({identity(os.fstat(d.fd))[:2] for d in self.copies}) == 2, 'collector destinations are not distinct')
        require(isinstance(assignment_raw, bytes) and len(assignment_raw) <= MAX_METADATA_BYTES
                and digest(assignment_raw) == c['assignment_sha256'], 'external assignment bytes differ')
        self.assignment_raw = assignment_raw; assignment = parse(assignment_raw)
        require(isinstance(assignment, dict) and set(assignment) == ASSIGNMENT_KEYS
                and assignment['schema'] == 'dams-compute-only-assignment-v1', 'collector assignment shape differs')
        require(all(assignment[k] == c[k] for k in ('source_sha256', 'pipeline_driver_sha256', 'spec_sha256', 'inventory_sha256')),
                'assignment source/spec/inventory differs')
        require(isinstance(assignment['spec'], dict) and digest(canonical(assignment['spec'])) == c['spec_sha256'],
                'assignment canonical specification bytes differ')
        rows = assignment['cases']
        require(isinstance(rows, list) and 1 <= len(rows) <= 65536 and digest(canonical(rows)) == c['inventory_sha256'],
                'literal assigned inventory SHA differs')
        self.rows = {}; self.configs = {}
        for row in rows:
            require(isinstance(row, dict) and set(row) == {'case_id', 'config', 'tags'}, 'assigned case shape differs')
            config = Config.from_dict(row['config']); ident = _sha(row['case_id'])
            require(ident == case_key(config) and ident not in self.rows and isinstance(row['tags'], dict)
                    and 'parent_case_id' in row['tags'], 'assigned case identity differs')
            self.rows[ident] = row; self.configs[ident] = config
        for row in rows:
            seen = {row['case_id']}; parent = row['tags']['parent_case_id']
            while parent is not None:
                require(parent in self.rows and parent not in seen, 'assigned ancestry missing/cyclic')
                seen.add(parent); parent = self.rows[parent]['tags']['parent_case_id']
        self.check()
        with self.locked():
            existing = set(os.listdir(self.state.fd))
            if 'binding.json' not in existing:
                require(existing == {'lock'} and minimum_head is None, 'existing collector history missing; cannot reset')
                self.state.write_new('binding.json', blob({'admission_sha256': self.admission_sha}))
                self.state.write_new('initial.json', blob({'sequence': 0, 'sha256': None, 'requests': 0, 'bytes': 0}))
                self.state.write_new('head.json', blob({'sequence': 0, 'sha256': None, 'requests': 0, 'bytes': 0}))
            else:
                require(minimum_head is not None, 'collector resume requires externally retained head')
                require(parse(read(self.state, 'binding.json')[0]) == {'admission_sha256': self.admission_sha},
                        'collector binding changed; no new allowance')
            self.records = child(self.state, 'reservations'); self.results = child(self.state, 'results')
            self.opened.extend([self.records, self.results])
            self._history(minimum_head)

    def check(self):
        require(not self.closed, 'collector closed')
        now = max(time.time(), self.clock_wall + time.monotonic() - self.clock_monotonic)
        if now >= self.deadline:
            raise TimeoutError('collector original absolute deadline reached')
        require({p.name for p in self.core_dir.glob('*.py')} == self.core_names, 'collector core roster changed')
        for path, observed in self.source_observations.items():
            require(not path.is_symlink() and identity(path.lstat()) == observed, 'collector frozen runtime/source changed')
        self.source_directory.check()
        directory, name, raw, observed = self.admission_observation
        require(identity(os.stat(name, dir_fd=directory.fd, follow_symlinks=False)) == observed,
                'external admission identity changed')
        directory.check()
        for d in getattr(self, 'copies', []):
            d.check()
        for d in (getattr(self, 'state', None), getattr(self, 'cache', None)):
            if d is not None:
                d.check()

    @contextmanager
    def locked(self):
        self.state.check()
        fd = self.state.open('lock', os.O_RDWR | os.O_CREAT | os.O_NONBLOCK)
        try:
            require(os.fstat(fd).st_nlink == 1, 'collector lock is hardlinked')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('collector already active') from None
            self.state.matches('lock', fd)
            yield
        finally:
            os.close(fd)

    def _history(self, minimum=None):
        require(parse(read(self.state, 'initial.json')[0]) == {'sequence': 0, 'sha256': None, 'requests': 0, 'bytes': 0},
                'collector initial head changed')
        previous = None; total = 0; heads = [{'sequence': 0, 'sha256': None, 'requests': 0, 'bytes': 0}]
        files = sorted(os.listdir(self.records.fd))
        for number, name in enumerate(files, 1):
            require(name == f'{number:08d}.json', 'collector reservation prefix incomplete')
            raw, _ = read(self.records, name); value = parse(raw)
            require(set(value) == {'sequence', 'previous_sha256', 'kind', 'object_sha256', 'reserved_bytes', 'admission_sha256'}
                    and type(value['sequence']) is int and value['sequence'] == number
                    and value['previous_sha256'] == previous and value['admission_sha256'] == self.admission_sha
                    and value['kind'] in ('metadata', 'chunk'), 'collector reservation history changed')
            _sha(value['object_sha256']); total += _natural(value['reserved_bytes'], True); previous = digest(raw)
            require(number <= self.config['max_fetch_requests'] and total <= self.config['max_fetch_bytes'],
                    'collector original cumulative allowance exceeded')
            heads.append({'sequence': number, 'sha256': previous, 'requests': number, 'bytes': total})
        cursor_raw = read(self.state, 'head.json')[0]; cursor = parse(cursor_raw)
        require(isinstance(cursor, dict) and set(cursor) == {'sequence', 'sha256', 'requests', 'bytes'}, 'collector durable cursor shape differs')
        for key in ('sequence', 'requests', 'bytes'):
            _natural(cursor[key])
        require(cursor['sequence'] < len(heads) and cursor == heads[cursor['sequence']],
                'collector durable known prefix missing; no refund/reset')
        # Every actual completed attempt result must still point to its reservation.
        for name in os.listdir(self.results.fd):
            require(name in files, 'collector fetch result has no reservation')
            value = parse(read(self.results, name)[0]); record_raw = read(self.records, name)[0]
            require(set(value) == {'reservation_sha256', 'observed_bytes', 'status', 'payload_sha256'}
                    and value['reservation_sha256'] == digest(record_raw)
                    and value['status'] in ('verified', 'failed'), 'collector result binding differs')
            require(_natural(value['observed_bytes']) <= parse(record_raw)['reserved_bytes'], 'collector observed bound differs')
            require(value['payload_sha256'] is None or _sha(value['payload_sha256']), 'collector payload SHA differs')
        if minimum is not None:
            require(isinstance(minimum, dict) and set(minimum) == {'sequence', 'sha256', 'requests', 'bytes'},
                    'collector external minimum head shape differs')
            for key in ('sequence', 'requests', 'bytes'):
                _natural(minimum[key])
            require(minimum['sequence'] < len(heads) and heads[minimum['sequence']] == minimum,
                    'collector known head rolled back or changed')
        self._head = heads[-1]
        if cursor != self._head:
            temporary = '.head-' + str(time.time_ns())
            self.state.write_new(temporary, blob(self._head))
            require(read(self.state, 'head.json')[0] == cursor_raw, 'collector cursor changed during advance')
            self.state.check()
            os.replace(temporary, 'head.json', src_dir_fd=self.state.fd, dst_dir_fd=self.state.fd)
            os.fsync(self.state.fd)
            require(read(self.state, 'head.json')[0] == blob(self._head), 'collector cursor publication differs')
        return dict(self._head)

    @property
    def head(self):
        self.check()
        return self._history(self._head)

    def _reserve(self, kind, bound, object_sha):
        self.check(); self._history(self._head)
        count = self._head['sequence'] + 1; upper = _natural(bound, True)
        require(count <= self.config['max_fetch_requests'] and self._head['bytes'] + upper <= self.config['max_fetch_bytes'],
                'collector fetch allowance exhausted before callback')
        value = {'sequence': count, 'previous_sha256': self._head['sha256'], 'kind': kind,
                 'object_sha256': _sha(object_sha), 'reserved_bytes': upper, 'admission_sha256': self.admission_sha}
        self.records.write_new(f'{count:08d}.json', blob(value))
        self._history(self._head)
        return count, digest(blob(value))

    def reserve_metadata_attempt(self, max_bytes):
        """Call BEFORE root metadata IAP fetch; failed attempts remain charged."""
        require(_natural(max_bytes, True) <= MAX_METADATA_BYTES, 'collector metadata bound exceeds trusted maximum')
        with self.locked():
            ticket = self._reserve('metadata', max_bytes + 1, digest(canonical({'metadata_attempt': self._head['sequence'] + 1})))
        return {'sequence': ticket[0], 'reservation_sha256': ticket[1], 'head': self.head}

    def _stream(self, callback, bound):
        observed = 0; data = bytearray(); response = None
        self.last_observed = 0
        try:
            response = callback()
            require(hasattr(response, 'read'), 'collector fetch callback must return binary stream')
            while observed <= bound:
                self.check()
                requested = min(65536, bound + 1 - observed)
                block = response.read(requested)
                require(isinstance(block, bytes), 'collector fetch stream is not bytes')
                require(len(block) <= requested, 'collector callback violates bounded read contract')
                if not block:
                    break
                observed += len(block); self.last_observed = observed; data.extend(block)
                require(observed <= bound, 'collector fetch payload exceeds expected bound')
            return bytes(data), observed
        finally:
            if response is not None and hasattr(response, 'close'):
                response.close()

    def _validate_group(self, manifest_raw, seal_raw):
        require(isinstance(manifest_raw, bytes) and isinstance(seal_raw, bytes)
                and len(manifest_raw) <= MAX_METADATA_BYTES and len(seal_raw) <= MAX_METADATA_BYTES,
                'collector sealed metadata exceeds bound')
        m, s = parse(manifest_raw), parse(seal_raw)
        require(isinstance(m, dict) and set(m) == MANIFEST_KEYS and isinstance(s, dict) and set(s) == SEAL_KEYS,
                'collector sealed metadata shape differs')
        require(m['schema'] == s['schema'] == SCHEMA and m['kind'] in ('checkpoint', 'final'), 'collector group schema/kind differs')
        for key in ('stage_id', 'source_sha256', 'codec_source_sha256', 'codec_dependencies_sha256'):
            require(m[key] == self.config[key], 'collector group source/stage/codec differs')
        require(s['manifest_sha256'] == digest(manifest_raw) and type(s['manifest_bytes']) is int
                and s['manifest_bytes'] == len(manifest_raw), 'collector seal manifest bytes differ')
        ident = {key: m[key] for key in ('stage_id', 'source_sha256', 'config_sha256', 'case_id', 'attempt', 'kind', 'day')}
        require(type(m['day']) is int and m['day'] >= 0 and isinstance(m['attempt'], str)
                and re.fullmatch('attempt-[0-9]{3,}', m['attempt']), 'collector attempt/day differs')
        require(m['generation'] == s['generation'] == digest(canonical(ident)), 'collector generation identity differs')
        require(m['case_id'] in self.rows and m['config_sha256'] == digest(canonical(self.rows[m['case_id']]['config'])),
                'collector group is outside original assignment')
        require(m['day'] <= self.configs[m['case_id']].days, 'collector group exceeds assigned horizon')
        files = m['files']; total_raw = encoded = 0; names = []
        require(isinstance(files, list) and 1 <= len(files) <= MAX_FILES, 'collector group file roster differs')
        for number, part in enumerate(files):
            require(isinstance(part, dict) and set(part) == {'name', 'part_directory', 'codec'}
                    and part['part_directory'] == f'parts/{number:05d}', 'collector part namespace differs')
            names.append(_name(part['name'])); self.codec.validate_manifest(part['codec'])
            total_raw += part['codec']['raw_bytes']; encoded += part['codec']['encoded_bytes']
        require(len(set(names)) == len(names) and total_raw <= self.config['max_group_raw_bytes'], 'collector raw/file cap differs')
        group_sha = digest(canonical([{'name': p['name'], 'bytes': p['codec']['raw_bytes'], 'sha256': p['codec']['raw_sha256']} for p in files]))
        require(m['group_sha256'] == s['group_sha256'] == group_sha and type(s['encoded_bytes']) is int
                and s['encoded_bytes'] == encoded, 'collector whole group commitment differs')
        if m['kind'] == 'checkpoint':
            require(set(m['sealed']) == {'item', 'index'}, 'collector checkpoint seal differs')
            item, index = m['sealed']['item'], m['sealed']['index']
            require(index.get('source_sha256') == m['source_sha256'] and index.get('config_sha256') == m['config_sha256']
                    and isinstance(index.get('snapshots'), list) and 1 <= len(index['snapshots']) <= 2
                    and index['snapshots'][0] == item and item.get('day') == m['day'], 'collector checkpoint index differs')
            require(isinstance(item.get('files'), list) and len(item['files']) == 2, 'collector only admits full legacy checkpoint pair')
            require(set(names) == {p['file'] for p in item['files']} | {'checkpoint-index.json'}, 'collector checkpoint roster differs')
            require(item['file'] in names and item['sha256'] == next(p['sha256'] for p in item['files'] if p['file'] == item['file']),
                    'collector checkpoint pointer differs')
        else:
            require(set(m['sealed']) == {'manifest'} and m['day'] == self.configs[m['case_id']].days, 'collector final seal/horizon differs')
            final = m['sealed']['manifest']
            require(final.get('status') == 'complete' and final.get('exit_code') == 0
                    and final.get('scientific_case_id') == m['case_id']
                    and final.get('source_sha256') == m['source_sha256']
                    and final.get('pipeline_driver_sha256') == self.config['pipeline_driver_sha256']
                    and final.get('config') == self.rows[m['case_id']]['config']
                    and final.get('config_sha256') == m['config_sha256'], 'collector final case/source/config differs')
            require(set(names) == set(final['output_sha256']) | {'manifest.json'}, 'collector final full raw roster differs')
        return m, s

    def _accept_metadata(self, ticket, raw):
        require(isinstance(ticket, dict) and set(ticket) == {'sequence', 'reservation_sha256', 'head'},
                'collector metadata reservation ticket differs')
        number = _natural(ticket['sequence'], True); self._history(ticket['head'])
        name = f'{number:08d}.json'; record_raw = read(self.records, name)[0]; record = parse(record_raw)
        require(digest(record_raw) == _sha(ticket['reservation_sha256']) and record['kind'] == 'metadata'
                and len(raw) < record['reserved_bytes'], 'collector metadata attempt was not reserved')
        result = blob({'reservation_sha256': digest(record_raw), 'observed_bytes': len(raw),
                       'status': 'verified', 'payload_sha256': digest(raw)})
        if name in os.listdir(self.results.fd):
            require(read(self.results, name)[0] == result, 'collector metadata reservation already binds different bytes')
        else:
            self.results.write_new(name, result)

    def _fetch_chunk(self, part, chunk, fetch):
        name = chunk['encoded_sha256'] + '.z'
        if name in os.listdir(self.cache.fd):
            actual, _ = file_hash(self.cache, name, chunk['encoded_bytes'], self.check)
            require(actual == chunk['encoded_sha256'], 'collector existing cache checksum differs')
            return name
        self.codec.require_capacity(self.cache.path, chunk['encoded_bytes'] + 8192, self.config['min_free_bytes'])
        ticket, record_sha = self._reserve('chunk', chunk['encoded_bytes'] + 1, chunk['encoded_sha256'])
        observed = 0; status = 'failed'
        try:
            raw, observed = self._stream(lambda: fetch(part, chunk), chunk['encoded_bytes'])
            require(len(raw) == chunk['encoded_bytes'] and digest(raw) == chunk['encoded_sha256'], 'collector fetched chunk checksum/size differs')
            self.codec.decode_chunk(raw, chunk)
            self.check(); self.cache.write_new(name, raw)
            actual, _ = file_hash(self.cache, name, len(raw), self.check)
            require(actual == chunk['encoded_sha256'], 'collector cache publication checksum differs')
            status = 'verified'
            return name
        finally:
            self.results.write_new(f'{ticket:08d}.json', blob({'reservation_sha256': record_sha,
                                  'observed_bytes': observed if status == 'verified' else self.last_observed,
                                  'status': status, 'payload_sha256': digest(raw) if status == 'verified' else None}))

    def _restore(self, collection, m):
        dirs = []
        current = collection
        for name in ('cases', m['case_id'], m['attempt']):
            current = child(current, name); dirs.append(current)
        try:
            expected = {p['name']: p['codec'] for p in m['files']}
            # Admission covers full group, decoder buffers and fs metadata before writes.
            self.codec.require_capacity(collection.path, sum(p['raw_bytes'] for p in expected.values())
                                        + 2 * self.codec.MAX_ENCODED_CHUNK + len(expected) * 8192,
                                        self.config['min_free_bytes'])
            for part in m['files']:
                target, name, opened = leaf(current, part['name'])
                try:
                    codec = part['codec']
                    if name not in os.listdir(target.fd):
                        fd = target.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                        h = hashlib.sha256(); size = 0
                        try:
                            for chunk in codec['chunks']:
                                self.check(); encoded = read(self.cache, chunk['encoded_sha256'] + '.z', self.codec.MAX_ENCODED_CHUNK)[0]
                                raw = self.codec.decode_chunk(encoded, chunk); h.update(raw); size += len(raw)
                                view = memoryview(raw)
                                while view:
                                    n = os.write(fd, view); require(n > 0, 'collector restore short write'); view = view[n:]
                            require(size == codec['raw_bytes'] and h.hexdigest() == codec['raw_sha256'], 'collector restored logical bytes differ')
                            os.fsync(fd); target.matches(name, fd); os.fsync(target.fd)
                        finally:
                            os.close(fd)
                    actual, _ = file_hash(target, name, codec['raw_bytes'], self.check)
                    require(actual == codec['raw_sha256'], 'collector restored file readback differs')
                finally:
                    for d in reversed(opened):
                        d.close()
            exact_tree(current, expected)
            return current.path
        finally:
            for d in reversed(dirs):
                d.close()

    def _gate(self, collection, attempt, m, parent_generations, parent_root):
        row = self.rows[m['case_id']]; config = self.configs[m['case_id']]
        parents = []; parent = row['tags']['parent_case_id']; origin_expected = None
        while parent is not None:
            require(parent in parent_generations, 'collector requires externally supplied completed parent generation')
            generation = _sha(parent_generations[parent])
            parent_group = Directory(parent_root.path / generation)
            try:
                parent_m = parse(read(parent_group, 'manifest.json')[0]); receipt = parse(read(parent_group, 'receipt.json')[0])
                require(parent_m['case_id'] == parent and parent_m['kind'] == 'final'
                        and receipt['generation'] == generation and receipt['gate'] == 'final-full-raw'
                        and receipt['admission_sha256'] == self.admission_sha, 'collector completed parent receipt differs')
                self._history(receipt['counter_high_water'])
                ack = parse(read(self.state, generation + '.ACK.json')[0])
                require(any(copy['receipt_sha256'] == digest(read(parent_group, 'receipt.json')[0])
                            for copy in ack['copies']), 'collector parent receipt lacks retained two-copy ACK')
                parent_attempt = parent_group.path / 'cases' / parent / parent_m['attempt']
                self._guard_files(parent_attempt, receipt['files'])
                parent_envelope, _ = verify_snapshot(parent_attempt / 'final_state.json', expected_config=self.configs[parent])
                if origin_expected is None:
                    origin_expected = {'parent_case_id': parent, 'parent_source_sha256': parent_envelope['source_sha256'],
                                       'parent_config_sha256': parent_envelope['config_sha256'], 'parent_day': parent_envelope['state']['day'],
                                       'parent_state_semantic_sha256': parent_envelope['state_semantic_sha256']}
                    parent_ledger = parent_attempt / parent_envelope['ledger']['file']
                    parent_history = parent_envelope['state']['history']
                parents.append((self.configs[parent].days, self.configs[parent]))
            finally:
                parent_group.close()
            parent = self.rows[parent]['tags']['parent_case_id']
        if m['kind'] == 'checkpoint':
            item, index = m['sealed']['item'], m['sealed']['index']
            require((attempt / 'checkpoint-index.json').read_bytes() == blob(index), 'collector actual index differs from seal')
            for part in item['files']:
                require(part['file'] == Path(part['file']).name, 'collector checkpoint leaf is unsafe')
                directory = Directory(attempt)
                try:
                    actual, _ = file_hash(directory, part['file'], _natural(part['bytes']), self.check)
                    require(actual == _sha(part['sha256']), 'collector actual checkpoint descriptor differs')
                finally:
                    directory.close()
            envelope, _ = verify_snapshot(attempt / item['file'], expected_config=config,
                                          expected_source_sha256=self.config['source_sha256'])
            require(envelope['state']['day'] == item['day'] and envelope['state_semantic_sha256'] == item['state_semantic_sha256'],
                    'collector checkpoint full state/day differs')
            gate = 'checkpoint-full-state'
        else:
            require((attempt / 'manifest.json').read_bytes() == blob(m['sealed']['manifest']), 'collector actual final manifest differs from seal')
            checked = CheckedLongitudinalCases(collection.path, {k: self.config[k] for k in
                                               ('source_sha256', 'pipeline_driver_sha256', 'spec_sha256')})
            value = checked.validate_case(row, attempt, config_history=tuple(sorted(parents, key=lambda v: v[0])))
            require(checked.result()['unique_complete_cases'] == 1 and checked.result()['full_study_gate'] is False,
                    'collector individual raw gate differs')
            envelope, _ = verify_snapshot(attempt / 'final_state.json', expected_config=config)
            gate = 'final-full-raw'
        origin = envelope['state']['branch_origin']
        if origin_expected is None:
            require(origin is None, 'unassigned branch parent present')
        else:
            require(origin == origin_expected,
                    'collector actual branch origin differs from completed parent')
            require(envelope['state']['day'] >= origin_expected['parent_day'], 'child checkpoint precedes assigned parent')
            require(canonical(envelope['state']['history'][:origin_expected['parent_day']])
                    == canonical(parent_history), 'collector child shared saved history differs')
            # Native append-only journal lexical rows preserve the exact shared
            # prehistory. Hash labels alone do not certify this prefix.
            child_ledger = attempt / envelope['ledger']['file']
            with closing(sqlite3.connect(parent_ledger.as_uri() + '?mode=ro&immutable=1', uri=True)) as db_parent, \
                    closing(sqlite3.connect(child_ledger.as_uri() + '?mode=ro&immutable=1', uri=True)) as db_child:
                before_parent = parent_ledger.stat(); before_child = child_ledger.stat()
                with closing(iter_journal_rows(db_parent,include_sequence=True)) as a, \
                        closing(iter_journal_rows(db_child,include_sequence=True)) as b:
                    for expected in a:
                        self.check(); require(next(b,None) == expected, 'collector child journal shared prefix differs')
                require(identity(parent_ledger.stat()) == identity(before_parent)
                        and identity(child_ledger.stat()) == identity(before_child), 'collector lineage ledger changed')
        if m['kind'] == 'final':
            require(m['sealed']['manifest'].get('branch_origin') == origin,
                    'collector final manifest branch origin differs')
        self.gate_details = {'case_id': m['case_id'], 'source_sha256': self.config['source_sha256'],
                             'config_sha256': m['config_sha256'], 'day': envelope['state']['day'],
                             'state_semantic_sha256': envelope['state_semantic_sha256'],
                             'branch_origin': origin, 'assigned_parent': origin_expected,
                             'attempt_manifest_origin_transported': m['kind'] == 'final',
                             'pipeline_driver_sha256': self.config['pipeline_driver_sha256'],
                             'spec_sha256': self.config['spec_sha256'], 'inventory_sha256': self.config['inventory_sha256']}
        return gate

    def _guard_files(self, attempt, files):
        directory = Directory(attempt)
        try:
            exact_tree(directory, files)
            for name, expected in files.items():
                name = _name(name); path = Path(attempt) / name
                parent = Directory(path.parent)
                try:
                    info = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                            and list(identity(info)) == expected['identity'], 'collector retained raw observation changed')
                    parent.check()
                finally:
                    parent.close()
        finally:
            directory.close()

    def _observations(self, collection, attempt, m):
        directory = Directory(attempt); result = {}
        try:
            exact_tree(directory, [p['name'] for p in m['files']])
            for part in m['files']:
                target, name, opened = leaf(directory, part['name'])
                try:
                    actual, observed = file_hash(target, name, part['codec']['raw_bytes'], self.check)
                    require(actual == part['codec']['raw_sha256'], 'collector final restored bytes differ')
                    result[part['name']] = {'bytes': part['codec']['raw_bytes'], 'sha256': actual, 'identity': list(observed)}
                finally:
                    for d in reversed(opened):
                        d.close()
        finally:
            directory.close()
        collection.check()
        return result

    def collect(self, manifest_raw, seal_raw, fetch, *, metadata_tickets, parent_generations=None):
        """Return exact ACK bytes; caller owns guest publication and retained head.

        Metadata must already have been reserved with reserve_metadata_attempt.
        ACK is never sent over a network by this provider-free module.
        """
        self.check(); m, seal = self._validate_group(manifest_raw, seal_raw)
        parent_generations = {} if parent_generations is None else parent_generations
        require(isinstance(parent_generations, dict), 'collector parent generation map differs')
        with self.locked():
            self._history(self._head)
            require(isinstance(metadata_tickets, list) and len(metadata_tickets) == 2
                    and metadata_tickets[0]['sequence'] != metadata_tickets[1]['sequence'],
                    'collector requires distinct manifest/seal attempt reservations')
            for ticket, raw in zip(metadata_tickets, (manifest_raw, seal_raw)):
                self._accept_metadata(ticket, raw)
            # Fetch verified encoded payloads ONCE; both raw restorations use this cache.
            for part in m['files']:
                for chunk in part['codec']['chunks']:
                    self._fetch_chunk(part, chunk, fetch)
            copies = []; observations = []; groups = []; attempts = []; receipt_raws = []
            group_anchors = []
            try:
                for number, destination in enumerate(self.copies, 1):
                    group = child(destination, m['generation']); groups.append(group)
                    group_anchors.append(identity(os.fstat(group.fd))[:2])
                    for name, raw in (('manifest.json', manifest_raw), ('seal.json', seal_raw)):
                        if name in os.listdir(group.fd):
                            require(read(group, name)[0] == raw, 'collector retained group metadata changed')
                        else:
                            group.write_new(name, raw)
                    attempt = self._restore(group, m); attempts.append(attempt)
                    gate = self._gate(group, attempt, m, parent_generations, destination)
                    files = self._observations(group, attempt, m); observations.append(files)
                    receipt = {'schema': COLLECTOR_SCHEMA, 'admission_sha256': self.admission_sha,
                               'generation': m['generation'], 'restoration_id': f'{m["generation"]}-copy-{number}',
                               'gate': gate, 'group_sha256': seal['group_sha256'], 'manifest_sha256': seal['manifest_sha256'],
                               'files': files, 'counter_high_water': self.head,
                               'scientific_binding': self.gate_details,
                               'individual_case_only': True, 'full_study_gate': False, 'science_complete': False}
                    raw = blob(receipt); receipt_raws.append(raw)
                    if 'receipt.json' in os.listdir(group.fd):
                        retained = parse(read(group, 'receipt.json')[0])
                        self._history(retained['counter_high_water'])
                        require({k: retained[k] for k in receipt if k != 'counter_high_water'}
                                == {k: receipt[k] for k in receipt if k != 'counter_high_water'}, 'collector old restoration receipt differs')
                        raw = read(group, 'receipt.json')[0]; receipt_raws[-1] = raw
                    else:
                        group.write_new('receipt.json', raw)
                    require(read(group, 'receipt.json')[0] == raw, 'collector durable receipt readback differs')
                    copies.append({'restoration_id': receipt['restoration_id'], 'receipt_sha256': digest(raw),
                                   'group_sha256': seal['group_sha256'], 'gate': gate})
                for name in observations[0]:
                    require(observations[0][name]['identity'][:2] != observations[1][name]['identity'][:2],
                            'collector restorations share physical files')
                # Publication guard rechecks every original restored observation.
                for group, attempt, files, receipt_raw in zip(groups, attempts, observations, receipt_raws):
                    require(self._observations(group, attempt, m) == files
                            and read(group, 'receipt.json')[0] == receipt_raw, 'collector copy/receipt changed before ACK')
                self.check(); self._history(self._head)
                ack = {'schema': SCHEMA, 'stage_id': m['stage_id'], 'source_sha256': m['source_sha256'],
                       'generation': m['generation'], 'manifest_sha256': seal['manifest_sha256'],
                       'group_sha256': seal['group_sha256'], 'gate': copies[0]['gate'], 'copies': copies}
                raw = blob(ack); name = m['generation'] + '.ACK.json'
                if name in os.listdir(self.state.fd):
                    require(read(self.state, name)[0] == raw, 'collector prior ACK changed')
                else:
                    self.state.write_new(name, raw)
                require(read(self.state, name)[0] == raw, 'collector ACK durable readback differs')
                for group, attempt, files, receipt_raw in zip(groups, attempts, observations, receipt_raws):
                    self._guard_files(attempt, files)
                    require(read(group, 'receipt.json')[0] == receipt_raw, 'collector receipt changed after ACK readback')
                for group in reversed(groups):
                    group.close()
                groups = []
                for destination, expected_anchor, attempt, files, receipt_raw in zip(self.copies, group_anchors, attempts, observations, receipt_raws):
                    destination.check()
                    closed_group = Directory(destination.path / m['generation'])
                    try:
                        require(identity(os.fstat(closed_group.fd))[:2] == expected_anchor, 'collector closed group namespace changed')
                        self._guard_files(attempt, files)
                        require(read(closed_group, 'receipt.json')[0] == receipt_raw, 'collector closed receipt changed')
                    finally:
                        closed_group.close()
                require(read(self.state, name)[0] == raw, 'collector ACK changed after copy close')
                self.check()
                return {'ack': ack, 'ack_bytes': raw, 'head': self.head,
                        'individual_case_only': True, 'full_study_gate': False, 'science_complete': False}
            finally:
                for group in reversed(groups):
                    group.close()

    def close(self):
        if not self.closed:
            self.closed = True
            for d in reversed(self.opened):
                d.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
