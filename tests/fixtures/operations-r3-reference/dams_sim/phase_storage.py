"""Opt-in finite execution storage; never changes a scientific Config.

The Linux caller supplies two exclusive ext4 loop filesystems.  Block capacity
is a physical backstop, not by itself a logical-byte certificate: every checked
leaf must also be fully allocated and regular.  The exact source's SQLite page
backups and sequential JSON/CSV writers are the admitted writers.
"""
from __future__ import annotations
import dataclasses
import errno
import json
import os
from pathlib import Path
import stat

ENV = 'DAMS_PHASE_STORAGE_CONFIG'
SCHEMA = 'dams-bounded-phase-storage-v1'
FIELDS = {'schema', 'output_dir', 'spool_dir', 'raw_bytes', 'spool_bytes',
          'checkpoint_headroom_bytes', 'metadata_reserve_bytes', 'receipt_file',
          'receipt_sha256', 'source_sha256'}


class PhaseCensored(RuntimeError):
    """The original case is incomplete; retained checkpoints are not a result."""


def from_environment(attempt=None):
    value = os.environ.get(ENV)
    return None if value is None else PhaseGuard(Path(value), attempt)


class PhaseGuard:
    def __init__(self, path, attempt=None):
        from .storage import file_digest, source_hash
        self.path = Path(path).absolute()
        self.value = json.loads(self.path.read_bytes())
        v = self.value
        if set(v) != FIELDS or v['schema'] != SCHEMA or v['source_sha256'] != source_hash():
            raise ValueError('bounded phase source/fields differ')
        self.root = Path(v['output_dir']).absolute()
        self.spool = Path(v['spool_dir']).absolute()
        for name, ceiling in (('raw_bytes', 48*2**30), ('spool_bytes', 24*2**30),
                              ('checkpoint_headroom_bytes', 8*2**30), ('metadata_reserve_bytes', 64*2**20)):
            n = v[name]
            if type(n) is not int or not 4096 <= n <= ceiling:
                raise ValueError('bounded phase explicit byte range differs')
        if v['checkpoint_headroom_bytes'] + v['metadata_reserve_bytes'] >= v['raw_bytes']:
            raise ValueError('bounded phase has no working capacity')
        if attempt is not None:
            attempt = Path(attempt).absolute()
            if attempt != self.root and self.root not in attempt.parents:
                raise ValueError('bounded attempt lies outside admitted output scope')
        receipt = Path(v['receipt_file']).absolute()
        if file_digest(receipt) != v['receipt_sha256']:
            raise ValueError('bounded filesystem receipt differs')
        self.receipt = json.loads(receipt.read_bytes())
        if self.receipt['schema'] != SCHEMA or self.receipt['source_sha256'] != v['source_sha256']:
            raise ValueError('bounded filesystem receipt source differs')
        self.anchors = {}
        for key, p, maximum in (('raw', self.root, v['raw_bytes']), ('spool', self.spool, v['spool_bytes'])):
            for ancestor in (p, *p.parents):
                if not stat.S_ISDIR(ancestor.lstat().st_mode):
                    raise ValueError('bounded namespace ancestor is not regular directory')
            info = p.stat(); bound = self.receipt[key]
            if (bound['target'] != str(p) or bound['device'] != info.st_dev
                    or bound['capacity_bytes'] > maximum or bound['filesystem'] != 'ext4'):
                raise ValueError('bounded mounted filesystem binding differs')
            total = os.statvfs(p).f_frsize * os.statvfs(p).f_blocks
            if not 0 < total <= maximum or total != bound['capacity_bytes']:
                raise ValueError('bounded filesystem capacity readback differs')
            self.anchors[key] = (info.st_dev, info.st_ino)
        if self.anchors['raw'][0] == self.anchors['spool'][0]:
            raise ValueError('raw/spool must be separate hard-cap filesystems')
        self.reserve = self.root / '.phase-metadata-reserve'
        self.check()

    def check(self, *, allocated=True):
        from .storage import file_digest
        if json.loads(self.path.read_bytes()) != self.value:
            raise ValueError('bounded phase options changed')
        if file_digest(Path(self.value['receipt_file'])) != self.value['receipt_sha256']:
            raise ValueError('bounded phase filesystem receipt changed')
        totals = {}
        for key, root, maximum in (('raw', self.root, self.value['raw_bytes']),
                                   ('spool', self.spool, self.value['spool_bytes'])):
            info = root.lstat()
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self.anchors[key]:
                raise ValueError('bounded phase mount/namespace replaced')
            total = 0
            for parent, dirs, names in os.walk(root, followlinks=False):
                for name in dirs:
                    q = Path(parent)/name
                    if not stat.S_ISDIR(q.lstat().st_mode) or q.stat().st_dev != info.st_dev:
                        raise ValueError('bounded phase foreign mount/directory')
                for name in names:
                    q = Path(parent)/name
                    try: s = q.lstat()
                    except FileNotFoundError: continue  # owned atomic rename; hard filesystem bound remains
                    if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_dev != info.st_dev
                            or (allocated and s.st_blocks*512 < s.st_size)):
                        raise PhaseCensored('bounded phase sparse/foreign/nonregular leaf')
                    total += s.st_size
            if total > maximum:
                raise PhaseCensored('bounded phase logical output capacity exhausted')
            totals[key] = total
        return totals

    def before_step(self, *, active=False):
        if not self.reserve.exists():
            raise PhaseCensored('bounded phase already resource-censored; no automatic retry or new case')
        self.check(allocated=not active)
        fs = os.statvfs(self.root)
        if fs.f_bavail*fs.f_frsize < self.value['checkpoint_headroom_bytes']:
            raise PhaseCensored('bounded phase checkpoint headroom reached; original horizon remains incomplete')

    def before_checkpoint(self, model):
        self.check()
        # No ledger materialization/RNG call.  Flat snapshot header+agents have
        # identical JSON scalars; 8KiB covers its fixed legacy envelope/digests.
        from .storage import canonical
        long = model._long
        if getattr(model.ledger, 'native_pages_active', False):
            raise ValueError('bounded phase currently admits only legacy flat checkpoints')
        db = model.ledger.db
        sqlite_bytes = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
        json_upper = len(canonical(long.header())) + sum(len(canonical(dataclasses.asdict(a)))+1 for a in long.agents) + 8192
        fs = os.statvfs(self.root)
        if sqlite_bytes + json_upper + 2*2**20 > fs.f_bavail*fs.f_frsize:
            raise PhaseCensored('bounded phase cannot reserve a new complete checkpoint; prior sealed checkpoint retained')

    def release_metadata_reserve(self):
        # Only this tool's allocated zero filler, never scientific bytes.
        if not self.reserve.exists(): return
        s = self.reserve.lstat(); expected = self.receipt['metadata_reserve_identity']
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1
                or [s.st_dev, s.st_ino, s.st_size] != expected):
            raise ValueError('bounded metadata reserve ownership changed')
        self.reserve.unlink()
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)


def disk_error(error):
    return isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EDQUOT)
