"""Separate, versioned owner records for opt-in native checkpoint publication.

This private execution record is outside both the CAS and scientific output tree.
It binds the actual checkpoint bytes to a caller-retained closure floor. It is
not authentication, and cannot detect rollback of this entire directory together
with the CAS without a separately retained newer floor/pin.
"""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

from ._committed_pages import Directory, Handle
from .storage import canonical

SCHEMA = 'dams-native-checkpoint-owner-v1'
_SHA = re.compile(r'^[0-9a-f]{64}$')


def handle_dict(value):
    value = dataclasses.asdict(value) if isinstance(value, Handle) else value
    if not isinstance(value, dict) or set(value) != {f.name for f in dataclasses.fields(Handle)}:
        raise ValueError('native full Handle fields differ')
    if (type(value['branch']) is not str or not re.fullmatch(r'[a-z0-9_-]{1,40}', value['branch'])
            or any(type(value[k]) is not str or not _SHA.fullmatch(value[k]) for k in ('manifest', 'head_sha256', 'closure_sha256'))
            or type(value['closure_count']) is not int or value['closure_count'] <= 0
            or type(value['record']) is not str
            or not re.fullmatch(re.escape(value['branch']) + r'(?:\.[0-9a-f]{64})?\.json', value['record'])):
        raise ValueError('native full Handle values differ')
    return dict(value)


def default_owner_directory(options, config_sha256):
    """An external sibling; never place owner records inside a moving attempt."""
    root = Path(options.store_root).absolute()
    return root.with_name(root.name + '-owners') / config_sha256


class CheckpointOwner:
    def __init__(self, directory, *, source_sha256, config_sha256):
        if any(type(v) is not str or not _SHA.fullmatch(v) for v in (source_sha256, config_sha256)):
            raise ValueError('native owner source/config pin differs')
        self.path = Path(directory).absolute()
        self.path.mkdir(parents=True, exist_ok=True)
        self.directory = Directory(self.path)
        self.source_sha256, self.config_sha256 = source_sha256, config_sha256
        self.lock = self.directory.open('.owner-lock', os.O_RDWR | os.O_CREAT)
        self.closed = False

    def _read_json(self, name):
        raw = self.directory.read(name, 1024 * 1024)
        def pairs(items):
            result = {}
            for k, v in items:
                if k in result: raise ValueError('duplicate native owner JSON key')
                result[k] = v
            return result
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite native owner JSON')))
        if canonical(value) + b'\n' != raw:
            raise ValueError('native owner bytes are not canonical')
        return value, hashlib.sha256(raw).hexdigest()

    def latest(self, *, minimum_floor=None):
        """Read the complete latest version; never fall back to an older record."""
        self.directory.check()
        names = set(os.listdir(self.directory.fd))
        versions = {n for n in names if re.fullmatch(r'owner-[0-9]{12}\.json', n)}
        if names - versions - {'.owner-lock', 'owner-head.json'}:
            raise ValueError('native owner namespace contains unplanned files')
        if 'owner-head.json' not in names:
            if versions: raise ValueError('native owner latest publication is incomplete')
            return None
        head, _ = self._read_json('owner-head.json')
        if set(head) != {'schema', 'record', 'sha256'} or head['schema'] != SCHEMA or head['record'] not in versions:
            raise ValueError('native owner head differs')
        row, sha = self._read_json(head['record'])
        fields = {'schema', 'sequence', 'previous_sha256', 'source_sha256', 'config_sha256',
                  'checkpoint', 'descriptor', 'floor'}
        if (set(row) != fields or row['schema'] != SCHEMA or type(row['sequence']) is not int
                or row['sequence'] < 1 or head['record'] != f"owner-{row['sequence']:012d}.json"
                or sha != head['sha256'] or row['source_sha256'] != self.source_sha256
                or row['config_sha256'] != self.config_sha256
                or versions != {f'owner-{n:012d}.json' for n in range(1, row['sequence'] + 1)}):
            raise ValueError('native owner latest version/source/config differs')
        previous = None
        for n in range(1, row['sequence'] + 1):
            record, record_sha = self._read_json(f'owner-{n:012d}.json')
            if record.get('sequence') != n or record.get('previous_sha256') != previous:
                raise ValueError('native owner version chain differs')
            previous = record_sha
        floor = handle_dict(row['floor'])
        if minimum_floor is not None:
            lower = handle_dict(minimum_floor)
            if (floor['closure_count'] < lower['closure_count'] or
                    floor['closure_count'] == lower['closure_count'] and floor['closure_sha256'] != lower['closure_sha256']):
                raise ValueError('native owner floor precedes externally retained floor')
        row['floor'] = floor
        return row

    def for_checkpoint(self, path, *, minimum_floor=None):
        row = self.latest(minimum_floor=minimum_floor)
        if row is None: raise ValueError('native checkpoint external owner record is missing')
        actual = observed_file(path)
        if row['checkpoint'] != actual:
            raise ValueError('native checkpoint differs from latest external owner record')
        return row

    def publish(self, path, descriptor, floor):
        """After checkpoint publication, fsync an immutable version then its head."""
        floor = handle_dict(floor)
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            self.directory.matches('.owner-lock', self.lock)
            old = self.latest()
            if old is not None:
                lower = handle_dict(old['floor'])
                if floor['closure_count'] < lower['closure_count'] or (floor['closure_count'] == lower['closure_count'] and floor['closure_sha256'] != lower['closure_sha256']):
                    raise ValueError('native owner floor cannot regress')
            actual = observed_file(path)
            if actual['sha256'] != descriptor['sha256'] or Path(actual['path']).name != descriptor['file']:
                raise ValueError('native owner checkpoint publication bytes differ')
            sequence = 1 if old is None else old['sequence'] + 1
            previous = None if old is None else self._read_json(f"owner-{old['sequence']:012d}.json")[1]
            row = {'schema': SCHEMA, 'sequence': sequence, 'previous_sha256': previous,
                   'source_sha256': self.source_sha256, 'config_sha256': self.config_sha256,
                   'checkpoint': actual, 'descriptor': descriptor, 'floor': floor}
            name = f'owner-{sequence:012d}.json'; raw = canonical(row) + b'\n'
            self.directory.write_new(name, raw)
            if observed_file(path) != actual:
                raise ValueError('native checkpoint changed during owner publication')
            temporary = '.head-publication'
            self.directory.write_new(temporary, canonical({'schema': SCHEMA, 'record': name, 'sha256': hashlib.sha256(raw).hexdigest()}) + b'\n')
            os.replace(temporary, 'owner-head.json', src_dir_fd=self.directory.fd, dst_dir_fd=self.directory.fd)
            os.fsync(self.directory.fd)
            self.directory.check()
            return self.for_checkpoint(path)
        finally:
            fcntl.flock(self.lock, fcntl.LOCK_UN)

    def close(self):
        if not self.closed:
            os.close(self.lock); self.directory.close(); self.closed = True


def observed_file(path):
    path = Path(path).absolute(); directory = Directory(path.parent)
    fd = None
    try:
        fd = directory.open(path.name, os.O_RDONLY)
        before = os.fstat(fd); anchor = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        h = hashlib.sha256(); size = 0
        while block := os.read(fd, 1024 * 1024): h.update(block); size += len(block)
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != anchor or size != anchor[2]:
            raise ValueError('native checkpoint bytes changed during owner read')
        directory.matches(path.name, fd)
        os.close(fd); fd = None
        current = os.stat(path.name, dir_fd=directory.fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != anchor:
            raise ValueError('native checkpoint changed after owner read')
        return {'path': str(path), 'sha256': h.hexdigest(), 'bytes': size, 'identity': list(anchor)}
    finally:
        if fd is not None: os.close(fd)
        directory.close()
