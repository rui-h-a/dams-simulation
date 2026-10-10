"""Private committed-WAL-page prototype; no DAMS imports or old-source resume.

Only a caller-owned, single EXCLUSIVE SQLite session is admitted.  Incremental
capture reads a WAL header, its pinned cursor, and newly committed frames.  It
never labels its Merkle commitment a legacy byte/row/state SHA.  Full byte SHA
is calculated at explicit export/closure boundaries.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import fcntl
import functools
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import threading
import uuid

SCHEMA = 'dams-private-committed-page-checkpoint-v2'
NODE_SCHEMA = 'dams-private-page-trie-v1'
DELTA_SCHEMA = 'dams-private-wal-frame-segment-v1'
SQLITE_PIN = '3.53.4'
MAX_BYTES = 64 * 1024**2
MAX_NEW_FRAMES = 8192
MAX_PAGES = 32768
MIN_FREE = 1024**3
CID = re.compile(r'^[0-9a-f]{64}$')
MASK = (1 << 32)-1


class Refusal(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Refusal(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)+'\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def module_sha():
    return sha(Path(__file__).read_bytes())


@dataclasses.dataclass
class Meter:
    base_read: int = 0
    wal_read: int = 0
    source_header_read: int = 0
    cas_read: int = 0
    cas_written: int = 0
    index_written: int = 0
    manifest_written: int = 0
    source_full_hashes: int = 0
    export_read: int = 0
    export_written: int = 0
    export_readback: int = 0
    native_checkpoints: int = 0
    namespace_stats: int = 0
    seal_read: int = 0
    seal_written: int = 0
    fork_read: int = 0


class Directory:
    """Held no-follow ancestors; all payload operations are dir_fd relative."""
    def __init__(self, path):
        self.path = Path(os.path.abspath(path))
        require(not any(n in ('.', '..') for n in self.path.parts), 'DIRECTORY_LEXICAL')
        self.anchors = []
        # One completed write observation, not a fresh filesystem baseline.
        # Keep write_new's original no-return API for existing callers/hooks.
        self.completed_write = None
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        self.anchors.append((None, None, fd, os.fstat(fd)))
        try:
            for part in self.path.parts[1:]:
                parent = fd
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                self.anchors.append((parent, part, fd, os.fstat(fd)))
            self.fd = fd
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        for parent, name, fd, initial in self.anchors:
            held = os.fstat(fd)
            require((held.st_dev, held.st_ino) == (initial.st_dev, initial.st_ino), 'DIRECTORY_FD_CHANGED')
            if name is not None:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                require((current.st_dev, current.st_ino) == (initial.st_dev, initial.st_ino) and not os.path.islink(self.path), 'DIRECTORY_NAMESPACE_CHANGED')

    def sub(self, name):
        require(name in ('pages', 'nodes', 'segments', 'manifests', 'heads', 'seals'), 'STORE_DIRECTORY')
        self.check()
        try:
            os.mkdir(name, 0o700, dir_fd=self.fd)
        except FileExistsError:
            pass
        self.check()
        return Directory(self.path/name)

    def open(self, name, flags, mode=0o600):
        require('/' not in name and name not in ('', '.', '..'), 'LEAF_NAME')
        self.check()
        fd = os.open(name, flags | os.O_NOFOLLOW, mode, dir_fd=self.fd)
        try:
            require(os.fstat(fd).st_mode & 0o170000 == 0o100000, 'LEAF_NOT_REGULAR')
            self.check()
            return fd
        except BaseException:
            os.close(fd)
            raise

    def matches(self, name, fd):
        self.check()
        current = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        held = os.fstat(fd)
        require(identity(current) == identity(held), 'LEAF_NAMESPACE_CHANGED')

    def read_observed(self, name, limit):
        """Return exact bytes with the original held-FD anchor after close."""
        fd = self.open(name, os.O_RDONLY)
        try:
            initial = identity(os.fstat(fd))
            require(initial[2] <= limit, 'READ_BOUND')
            raw = bytearray()
            while len(raw) < initial[2]:
                data = os.read(fd, min(65536, initial[2]-len(raw)))
                require(data, 'READ_SHORT')
                raw.extend(data)
            require(os.read(fd, 1) == b'' and identity(os.fstat(fd)) == initial, 'READ_DRIFT_OR_EXTRA')
            self.matches(name, fd)
        finally:
            os.close(fd)
        self.check()
        require(identity(os.stat(name, dir_fd=self.fd, follow_symlinks=False)) == initial, 'CLOSED_READ_CHANGED')
        return bytes(raw), initial

    def read(self, name, limit):
        return self.read_observed(name, limit)[0]

    def write_new(self, name, raw):
        self.completed_write = None
        fd = self.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            view = memoryview(raw)
            while view:
                n = os.write(fd, view)
                require(n > 0, 'WRITE_SHORT')
                view = view[n:]
            os.fsync(fd)
            self.matches(name, fd)
            os.fsync(self.fd)
            written_anchor = identity(os.fstat(fd))
        finally:
            os.close(fd)
        actual, read_anchor = self.read_observed(name, len(raw))
        require(actual == raw, 'WRITE_READBACK')
        require(read_anchor == written_anchor, 'WRITE_READBACK_ANCHOR')
        self.completed_write = (name, sha(raw), written_anchor)

    def close(self):
        for _, _, fd, _ in reversed(getattr(self, 'anchors', [])):
            os.close(fd)
        self.anchors = []


def checksum(raw, magic, current=(0, 0)):
    require(magic in (0x377f0682, 0x377f0683) and len(raw) % 8 == 0, 'CHECKSUM_INPUT')
    endian = '<' if magic == 0x377f0682 else '>'
    a, b = current
    for x, y in struct.iter_unpack(endian+'II', raw):
        a = (a+x+b) & MASK
        b = (b+y+a) & MASK
    return a, b


def wal_header(raw, page_size):
    require(len(raw) == 32, 'WAL_HEADER_SHORT')
    magic, version, ps, sequence, salt1, salt2, c1, c2 = struct.unpack('>8I', raw)
    require(version == 3007000 and ps == page_size, 'WAL_FORMAT_OR_PAGE_SIZE')
    require(checksum(raw[:24], magic) == (c1, c2), 'WAL_HEADER_CHECKSUM')
    return {'magic': magic, 'salts': (salt1, salt2), 'checksum': (c1, c2), 'sequence': sequence}


class NativeSession:
    """Cooperative commit gate PLUS SQLite EXCLUSIVE locks, not a flock claim.

    The caller creates a private database, sets EXCLUSIVE before first WAL
    access, and keeps exactly this connection open.  This class does not open
    SQLite connections, alter a scientific DB, or claim arbitrary-writer VFS
    interception.  Direct SQL/OS writes outside this gate violate admission.
    """
    def __init__(self, connection, database, *, day=0, fixture_source_sha256):
        self.connection = connection
        self.path = Path(os.path.abspath(database))
        self.folder = Directory(self.path.parent)
        self.main_fd = self.folder.open(self.path.name, os.O_RDONLY)
        self.wal_fd = self.folder.open(self.path.name+'-wal', os.O_RDONLY)
        self.main_inode = identity(os.fstat(self.main_fd))[:2]
        self.wal_inode = identity(os.fstat(self.wal_fd))[:2]
        self.day = day
        self.fixture_source_sha256 = fixture_source_sha256
        self.lock = threading.RLock()
        self.closed = False
        self.bound_branch = None
        self.poisoned = False
        self.source_id = connection.execute('SELECT sqlite_source_id()').fetchone()[0]
        self.compile_options_sha256 = sha(canonical(sorted(r[0] for r in connection.execute('PRAGMA compile_options'))))
        self.check()

    def check(self):
        require(not self.closed and sqlite3.sqlite_version == SQLITE_PIN, 'SQLITE_RUNTIME_PIN')
        require(not self.poisoned, 'ROLLED_BACK_SPILL_LIFECYCLE_UNSUPPORTED')
        require(not self.connection.in_transaction, 'ACTIVE_NATIVE_TRANSACTION')
        require(self.connection.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal', 'NATIVE_NOT_WAL')
        require(self.connection.execute('PRAGMA synchronous').fetchone()[0] == 2, 'NATIVE_NOT_FULL')
        require(self.connection.execute('PRAGMA locking_mode').fetchone()[0].lower() == 'exclusive', 'NATIVE_NOT_EXCLUSIVE')
        require(self.connection.execute('PRAGMA wal_autocheckpoint').fetchone()[0] == 0, 'AUTOCHECKPOINT_ENABLED')
        rows = self.connection.execute('PRAGMA database_list').fetchall()
        require(len(rows) == 1 and rows[0][1] == 'main' and os.path.abspath(rows[0][2]) == str(self.path), 'NATIVE_DATABASE_IDENTITY')
        self.folder.matches(self.path.name, self.main_fd)
        self.folder.matches(self.path.name+'-wal', self.wal_fd)
        require(identity(os.fstat(self.main_fd))[:2] == self.main_inode, 'NATIVE_MAIN_INODE')
        require(identity(os.fstat(self.wal_fd))[:2] == self.wal_inode, 'NATIVE_WAL_INODE')

    @contextlib.contextmanager
    def fence(self):
        with self.lock:
            self.check()
            yield
            self.check()

    def commit_day(self, day, callback):
        with self.fence():
            require(day == self.day+1, 'DAY_SEQUENCE')
            self.connection.execute('BEGIN IMMEDIATE')
            try:
                callback(self.connection)
                require(self.connection.in_transaction, 'CALLER_COMMITTED_OUTSIDE_GATE')
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                self.poisoned = True
                raise
            self.day = day

    def checkpoint_truncate(self):
        result = self.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        require(result == (0, 0, 0), 'NATIVE_CHECKPOINT_NOT_COMPLETE')
        wal = self.path.with_name(self.path.name+'-wal')
        require(wal.exists() and wal.stat().st_size == 0, 'NATIVE_RESET_NOT_EMPTY')
        return result

    def close(self):
        if not self.closed:
            self.connection.close()
            os.close(self.main_fd)
            os.close(self.wal_fd)
            self.folder.close()
            self.closed = True


@dataclasses.dataclass(frozen=True)
class Handle:
    branch: str
    manifest: str
    head_sha256: str
    record: str | None = None
    closure_count: int = 0
    closure_sha256: str = "0"*64


def writer(method):
    @functools.wraps(method)
    def locked(self, *args, **kwargs):
        if self.writer_depth:
            return method(self, *args, **kwargs)
        self.check()
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refusal('CAS_WRITER_BUSY')
        self.writer_depth = 1
        try:
            self.directory.matches('.writer-lock', self.lock_fd)
            self.closure_check(refresh=True)
            return method(self, *args, **kwargs)
        finally:
            self.writer_depth = 0
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
    return locked


class PageStore:
    def __init__(self, root, *, min_free_bytes=MIN_FREE, hook=None):
        require(type(min_free_bytes) is int and min_free_bytes >= MIN_FREE, 'FREE_FLOOR_CANNOT_LOWER')
        self.directory = Directory(root)
        self.areas = {}
        self.lock_fd = None
        try:
            for n in ('pages','nodes','segments','manifests','heads','seals'): self.areas[n]=self.directory.sub(n)
        except BaseException:
            self.close()
            raise
        self.min_free = min_free_bytes
        self.meter = Meter()
        self.hook = hook or (lambda phase: None)
        self.source_sha256 = module_sha()
        self.writer_depth = 0
        self.records = []
        self.object_seals = {}
        self.seal_sha = '0'*64
        try:
            self.lock_fd = self.directory.open('.writer-lock', os.O_RDWR | os.O_CREAT | os.O_EXCL)
            os.fsync(self.lock_fd); os.fsync(self.directory.fd)
        except FileExistsError:
            self.lock_fd = self.directory.open('.writer-lock', os.O_RDWR)
        self.check()
        try:
            with self.locked_closure():
                self.closure_check(refresh=True)
        except BaseException:
            self.close()
            raise

    @contextlib.contextmanager
    def locked_closure(self):
        try: fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Refusal('CAS_WRITER_BUSY')
        try:
            self.directory.matches('.writer-lock', self.lock_fd)
            yield
        finally: fcntl.flock(self.lock_fd, fcntl.LOCK_UN)

    def closure_check(self, refresh=False):
        """Full metadata roster, no historical payload rehash; append-only seals.

        ctime/dev/inode/size/mtime changes and missing/extra payloads refuse.
        This is a bounded local POSIX namespace contract, not a malicious VFS
        or administrator-proof immutable-storage claim. Reopen verifies the
        sealed chain, and the external Handle pins its minimum complete prefix.
        """
        self.check()
        d = self.areas['seals']
        names = sorted(os.listdir(d.fd))
        require(names == [f'{i:08d}.json' for i in range(len(names))], 'SEAL_CHAIN_GAP')
        require(len(names) >= len(self.records), 'SEAL_CHAIN_ROLLBACK')
        for record in self.records:
            self.meter.namespace_stats += 1
            require(identity(os.stat(record['seal_name'], dir_fd=d.fd, follow_symlinks=False)) == record['seal_anchor'], 'SEAL_METADATA_DRIFT')
        if len(names) > len(self.records):
            require(refresh, 'SEAL_PREFIX_ADVANCED_DURING_OPERATION')
            for name in names[len(self.records):]:
                raw, observed_anchor = d.read_observed(name, 16384); self.meter.seal_read += len(raw)
                rec = json.loads(raw)
                require(canonical(rec) == raw and rec['schema'] == 'dams-cas-object-seal-v2' and rec['producer_sha256'] == self.source_sha256 and rec['previous'] == self.seal_sha and rec['sequence'] == len(self.records), 'SEAL_CHAIN_BINDING')
                area, cid = rec['area'], rec['cid']
                require(area in ('pages','nodes','segments','manifests','heads') and ((area != 'heads' and CID.fullmatch(cid)) or (area == 'heads' and re.fullmatch(r'[a-z0-9_-]{1,40}(?:\.[0-9a-f]{64})?\.json',cid))) and (area,cid) not in self.object_seals, 'SEAL_OBJECT_IDENTITY')
                require(identity(os.stat(name,dir_fd=d.fd,follow_symlinks=False)) == observed_anchor, 'REFRESH_SEAL_READBACK_CHANGED')
                item = dict(rec, seal_name=name, seal_anchor=observed_anchor, seal_sha256=sha(raw))
                self.records.append(item); self.object_seals[(area,cid)] = item; self.seal_sha = sha(raw)
        expected = {area:set() for area in ('pages','nodes','segments','manifests','heads')}
        for rec in self.records:
            expected[rec['area']].add(rec['cid'])
            st = os.stat(rec['cid'],dir_fd=self.areas[rec['area']].fd,follow_symlinks=False); self.meter.namespace_stats += 1
            require(identity(st) == tuple(rec['anchor']) and st.st_mode & 0o170000 == 0o100000, 'CAS_SEALED_OBJECT_DRIFT')
        for area, wanted in expected.items():
            found=set(os.listdir(self.areas[area].fd))
            if area=='heads': found={x for x in found if not x.startswith('.prepared-')}
            require(found == wanted, 'CAS_UNSEALED_OR_MISSING_OBJECT')
        self.check()

    def seal_object(self, area, cid, expected_raw):
        # A file's current stat is not proof of the previously read-back bytes.
        # Pin the payload FD before fresh byte verification, hold it through
        # durable receipt creation, and reject drift at/after the close cutoff.
        d=self.areas[area];fd=d.open(cid,os.O_RDONLY);receipt_fd=None
        initial=identity(os.fstat(fd))
        try:
            actual=d.read(cid,len(expected_raw))
            if area != 'heads':self.meter.cas_read += len(actual)
            require(actual==expected_raw,'SEAL_PAYLOAD_BYTES_CHANGED')
            require(identity(os.fstat(fd))==initial,'SEAL_PAYLOAD_METADATA_DRIFT')
            d.matches(cid,fd)
            raw=canonical({'schema':'dams-cas-object-seal-v2','producer_sha256':self.source_sha256,'sequence':len(self.records),'previous':self.seal_sha,'area':area,'cid':cid,'anchor':list(initial)})
            name=f'{len(self.records):08d}.json'
            seals=self.areas['seals']
            seals.write_new(name,raw);self.meter.seal_written+=len(raw);self.meter.seal_read+=len(raw)
            observed=seals.completed_write
            require(type(observed) is tuple and len(observed)==3 and observed[:2]==(name,sha(raw)), 'SEAL_RECEIPT_WRITE_OBSERVATION')
            receipt_anchor=observed[2]
            receipt_fd=seals.open(name,os.O_RDONLY)
            require(identity(os.fstat(receipt_fd))==receipt_anchor,'SEAL_RECEIPT_READBACK_CHANGED')
            seals.matches(name,receipt_fd)
            require(identity(os.fstat(fd))==initial,'SEAL_PAYLOAD_METADATA_DRIFT')
            d.matches(cid,fd)
            rec=json.loads(raw)
            item=dict(rec,seal_name=name,seal_anchor=receipt_anchor,seal_sha256=sha(raw))
            self.records.append(item);self.object_seals[(area,cid)]=item;self.seal_sha=sha(raw)
            require(identity(os.fstat(receipt_fd))==receipt_anchor,'SEAL_RECEIPT_READBACK_CHANGED')
            seals.matches(name,receipt_fd)
        finally:
            os.close(fd)
            if receipt_fd is not None:os.close(receipt_fd)
        d.check()
        require(identity(os.stat(cid,dir_fd=d.fd,follow_symlinks=False))==initial,'CLOSED_SEALED_PAYLOAD_DRIFT')
        seals.check()
        require(identity(os.stat(name,dir_fd=seals.fd,follow_symlinks=False))==receipt_anchor,'CLOSED_SEAL_RECEIPT_CHANGED')

    def closure_floor(self, handle):
        require(isinstance(handle,Handle),'BRANCH_HANDLE')
        require(type(handle.closure_count) is int and 0 < handle.closure_count <= len(self.records), 'CLOSURE_ROLLBACK')
        actual = '0'*64 if not handle.closure_count else self.records[handle.closure_count-1]['seal_sha256']
        require(actual == handle.closure_sha256, 'CLOSURE_PIN_CHANGED')

    def record_name(self, handle):
        return handle.record or handle.branch+'.json'

    @writer
    def current_record(self, branch, *, externally_pinned_root):
        """Explicit operator closure traversal, never called by capture."""
        require(type(branch) is str and re.fullmatch(r'[a-z0-9_-]{1,40}',branch),'BRANCH_NAME')
        require(type(externally_pinned_root) is str and CID.fullmatch(externally_pinned_root),'BRANCH_ROOT_PIN')
        require(sha(self.areas['heads'].read(branch+'.json',16384)) == externally_pinned_root,'BRANCH_ROOT_PIN')
        name=branch+'.json'
        for _ in range(MAX_PAGES):
            raw=self.areas['heads'].read(name,16384); h=sha(raw)
            next_name=branch+'.'+h+'.json'
            try:self.areas['heads'].read(next_name,16384)
            except FileNotFoundError:
                state=json.loads(raw)
                result=Handle(branch,state['manifest'],h,name,len(self.records),self.seal_sha)
                self.hook('after-recovery-record-read')
                self.manifest(result)
                self.closure_check()
                self.check()
                return result
            name=next_name
        raise Refusal('OPERATOR_HEAD_TRAVERSAL_BOUND')

    def check(self):
        require(module_sha() == self.source_sha256, 'RUNTIME_SOURCE_CHANGED')
        self.directory.check()
        for d in self.areas.values():
            d.check()

    def free(self, reserve=0):
        self.free_at(self.directory, reserve)

    def free_at(self, directory, reserve=0):
        s = os.fstatvfs(directory.fd)
        require(s.f_bavail*s.f_frsize >= self.min_free+reserve, 'FREE_SPACE_FLOOR')

    @writer
    def put(self, area, raw):
        self.check()
        self.free(len(raw)+4096)
        cid = sha(raw)
        try:
            self.areas[area].write_new(cid, raw)
            self.meter.cas_written += len(raw)
            self.meter.cas_read += len(raw)
            self.seal_object(area,cid,raw)
            if area == 'nodes': self.meter.index_written += len(raw)
            if area == 'manifests': self.meter.manifest_written += len(raw)
        except FileExistsError:
            actual=self.areas[area].read(cid,len(raw)); self.meter.cas_read += len(actual)
            require(actual == raw, 'EXISTING_CID_BYTES_CHANGED')
            require((area,cid) in self.object_seals,'EXISTING_CID_UNSEALED')
        self.hook('object-durable')
        return cid

    def get(self, area, cid, limit):
        require(type(cid) is str and CID.fullmatch(cid), 'OBJECT_CID')
        raw = self.areas[area].read(cid, limit)
        self.meter.cas_read += len(raw)
        require(sha(raw) == cid, 'OBJECT_SHA')
        return raw

    def json_object(self, area, cid, limit=1024**2):
        raw = self.get(area, cid, limit)
        value = json.loads(raw)
        require(canonical(value) == raw, 'OBJECT_CANONICAL')
        return value

    def node(self, cid, depth):
        if cid is None:
            return {'schema': NODE_SCHEMA, 'depth': depth, 'count': 0, 'children': {}}
        n = self.json_object('nodes', cid, 16384)
        require(set(n) == {'schema', 'depth', 'count', 'children'} and n['schema'] == NODE_SCHEMA and n['depth'] == depth, 'NODE_SCHEMA')
        children = n['children']
        require(type(children) is dict and len(children) <= 16, 'NODE_CHILDREN')
        for k, v in children.items():
            require(k in '0123456789abcdef' and len(k) == 1 and set(v) == {'cid', 'count'} and CID.fullmatch(v['cid']) and type(v['count']) is int and 0 < v['count'] <= MAX_PAGES, 'NODE_EDGE')
        require(n['count'] == sum(v['count'] for v in children.values()) and (depth < 7 or all(v['count'] == 1 for v in children.values())), 'NODE_COUNT')
        return n

    def replace_page(self, root, page, page_cid):
        require(1 <= page <= MAX_PAGES and CID.fullmatch(page_cid), 'PAGE_KEY')
        key = f'{page:08x}'
        def update(cid, depth):
            if depth == 8:
                return {'cid': page_cid, 'count': 1}
            n = self.node(cid, depth)
            n['children'][key[depth]] = update(n['children'].get(key[depth], {}).get('cid'), depth+1)
            n['count'] = sum(v['count'] for v in n['children'].values())
            return {'cid': self.put('nodes', canonical(n)), 'count': n['count']}
        return update(root, 0)['cid']

    def clip(self, root, pages):
        def prune(cid, depth, prefix):
            lower = prefix << (4*(8-depth))
            upper = lower+(1 << (4*(8-depth)))-1
            if lower > pages:
                return None
            if upper <= pages:
                return {'cid': cid, 'count': 1} if depth == 8 else {'cid': cid, 'count': self.node(cid, depth)['count']}
            n = self.node(cid, depth)
            children = {}
            for k, v in n['children'].items():
                result = prune(v['cid'], depth+1, (prefix << 4)+int(k, 16))
                if result is not None: children[k] = result
            n['children'] = children
            n['count'] = sum(v['count'] for v in children.values())
            if not children: return None
            return {'cid': self.put('nodes', canonical(n)), 'count': n['count']}
        result = prune(root, 0, 0)
        require(result is not None and result['count'] == pages, 'PAGE_COVERAGE')
        return result['cid']

    def page(self, root, number, size):
        cid = root
        for depth, key in enumerate(f'{number:08x}'):
            n = self.node(cid, depth)
            require(key in n['children'], 'MISSING_LOGICAL_PAGE')
            cid = n['children'][key]['cid']
        raw = self.get('pages', cid, size)
        require(len(raw) == size, 'PAGE_BYTES')
        return raw

    def manifest(self, handle):
        require(isinstance(handle, Handle) and re.fullmatch(r'[a-z0-9_-]{1,40}', handle.branch), 'BRANCH_HANDLE')
        self.closure_floor(handle)
        raw = self.areas['heads'].read(self.record_name(handle), 16384)
        require(sha(raw) == handle.head_sha256, 'HEAD_ROLLBACK_OR_DRIFT')
        state = json.loads(raw)
        require(canonical(state) == raw and state['manifest'] == handle.manifest, 'HEAD_MANIFEST')
        require(state['branch']==handle.branch,'HEAD_BRANCH_BINDING')
        name=self.record_name(handle)
        if name==handle.branch+'.json': require(state['parent_head'] is None,'HEAD_ROOT_CONTEXT')
        else:
            require(name==handle.branch+'.'+str(state['parent_head'])+'.json' and CID.fullmatch(state['parent_head']),'HEAD_SUCCESSOR_CONTEXT')
        m = self.checkpoint(handle.manifest)
        require(m['cursor'] == state['cursor'], 'CURSOR_BINDING')
        try: os.stat(handle.branch+'.'+handle.head_sha256+'.json',dir_fd=self.areas['heads'].fd,follow_symlinks=False)
        except FileNotFoundError: pass
        else: raise Refusal('HEAD_ROLLBACK_OR_STALE_SUCCESSOR')
        return m, state

    def checkpoint(self, cid):
        """Pinned immutable historical checkpoints need no mutable HEAD replay."""
        m = self.json_object('manifests', cid, 16384)
        require(m['schema'] == SCHEMA and m['producer_sha256'] == self.source_sha256 and m['sqlite_version'] == SQLITE_PIN and m['science_complete'] is False and m['legacy_semantic_sha_compatible'] is False, 'CHECKPOINT_SOURCE_CODEC')
        require(type(m['pages']) is int and 0 < m['pages'] <= MAX_PAGES and CID.fullmatch(m['root']) and type(m['page_size']) is int and 512 <= m['page_size'] <= 65536 and m['page_size'] & (m['page_size']-1) == 0 and m['pages']*m['page_size'] <= MAX_BYTES, 'CHECKPOINT_GEOMETRY')
        return m

    def publish(self, branch, manifest, state, expected=None, final_check=lambda: None):
        require(self.writer_depth > 0, 'PUBLICATION_REQUIRES_WRITER_GATE')
        require(re.fullmatch(r'[a-z0-9_-]{1,40}',branch),'BRANCH_NAME')
        cid=self.put('manifests',canonical(manifest))
        state=dict(state,manifest=cid,cursor=manifest['cursor'],parent_head=None if expected is None else expected.head_sha256,branch=branch)
        raw=canonical(state); d=self.areas['heads']
        name=branch+'.json' if expected is None else branch+'.'+expected.head_sha256+'.json'
        if expected is not None:self.manifest(expected)
        tmp='.prepared-'+uuid.uuid4().hex
        self.free(len(raw)+4096); d.write_new(tmp,raw)
        fd=d.open(tmp,os.O_RDONLY)
        try:
            self.hook('before-head-publication')
            self.check();self.free();final_check();self.closure_check()
            if expected is not None:self.manifest(expected)
            d.matches(tmp,fd)
            # One atomic no-overwrite slot; no rename/replace of a current HEAD.
            # Temporary+published names add nlink, so capture anchor after link.
            try: os.link(tmp,name,src_dir_fd=d.fd,dst_dir_fd=d.fd,follow_symlinks=False)
            except FileExistsError: raise Refusal('HEAD_ALREADY_EXISTS_OR_SUCCESSOR_OCCUPIED')
            published_anchor=identity(os.fstat(fd))
            os.fsync(d.fd)
            self.seal_object('heads',name,raw)
            self.hook('after-head-publication')
            d.matches(name,fd)
            require(d.read(name,16384)==raw,'PUBLISHED_HEAD_BYTES')
            final_check();self.closure_check();self.check()
        finally:os.close(fd)
        self.hook('after-head-fd-close')
        self.check();self.free();final_check()
        require(identity(os.stat(name,dir_fd=d.fd,follow_symlinks=False))==published_anchor,'CLOSED_HEAD_INODE_CHANGED')
        require(d.read(name,16384)==raw,'CLOSED_HEAD_BYTES')
        self.closure_check()
        return Handle(branch,cid,sha(raw),name,len(self.records),self.seal_sha)

    def source_geometry(self, session):
        raw = os.pread(session.main_fd, 100, 0)
        self.meter.source_header_read += len(raw)
        require(len(raw) == 100 and raw[:16] == b'SQLite format 3\0' and raw[18:20] == b'\x02\x02', 'MAIN_HEADER')
        size = struct.unpack('>H', raw[16:18])[0]
        size = 65536 if size == 1 else size
        require(512 <= size <= 65536 and size & (size-1) == 0, 'MAIN_PAGE_SIZE')
        require(os.fstat(session.main_fd).st_size % size == 0, 'MAIN_SIZE')
        return size

    @writer
    def seed(self, session, *, branch='main'):
        require(re.fullmatch(r'[a-z0-9_-]{1,40}', branch), 'BRANCH_NAME')
        try: self.areas['heads'].read(branch+'.json',16384)
        except FileNotFoundError: pass
        else: raise Refusal('HEAD_ALREADY_EXISTS')
        require(session.bound_branch is None, 'SOURCE_ALREADY_BOUND_TO_BRANCH')
        with session.fence():
            session.bound_branch=(str(self.directory.path),branch)
            session.checkpoint_truncate()
            self.meter.native_checkpoints += 1
            self.hook('after-native-reset')
            size = self.source_geometry(session)
            initial = identity(os.fstat(session.main_fd))
            require(0 < initial[2] <= MAX_BYTES and initial[2]//size <= MAX_PAGES, 'BASE_BOUND')
            self.free(initial[2]*2+1024**2)
            root, h = None, hashlib.sha256()
            for number in range(1, initial[2]//size+1):
                raw = os.pread(session.main_fd, size, (number-1)*size)
                require(len(raw) == size, 'BASE_READ_SHORT')
                self.meter.base_read += len(raw)
                h.update(raw)
                root = self.replace_page(root, number, self.put('pages', raw))
            self.meter.source_full_hashes += 1
            require(identity(os.fstat(session.main_fd)) == initial, 'BASE_DRIFT')
            m = {'schema': SCHEMA, 'kind': 'base', 'parent': None, 'producer_sha256': self.source_sha256, 'fixture_source_sha256': session.fixture_source_sha256, 'sqlite_version': SQLITE_PIN, 'sqlite_source_id': session.source_id, 'compile_options_sha256': session.compile_options_sha256, 'day': session.day, 'page_size': size, 'pages': initial[2]//size, 'root': root, 'segment': None, 'cursor': None, 'epoch': 0, 'base_byte_sha256': h.hexdigest(), 'science_complete': False, 'legacy_semantic_sha_compatible': False}
            state = {'main_anchor': list(initial), 'revision': 0}
            def final():
                session.check()
                require(identity(os.fstat(session.main_fd)) == initial, 'BASE_PUBLICATION_DRIFT')
            return self.publish(branch, m, state, final_check=final)

    @writer
    def capture(self, session, expected):
        with session.fence():
            m, state = self.manifest(expected)
            require(session.bound_branch==(str(self.directory.path),expected.branch),'SOURCE_BRANCH_BINDING')
            require(session.fixture_source_sha256 == m['fixture_source_sha256'] and session.source_id == m['sqlite_source_id'] and session.compile_options_sha256 == m['compile_options_sha256'], 'NATIVE_SOURCE_PIN')
            require(session.day > m['day'], 'NO_NEW_DAY')
            require(identity(os.fstat(session.main_fd)) == tuple(state['main_anchor']), 'UNMANAGED_MAIN_CHECKPOINT')
            size = self.source_geometry(session)
            require(size == m['page_size'], 'PAGE_SIZE_CHANGED')
            name = session.path.name+'-wal'
            fd = session.folder.open(name, os.O_RDONLY)
            try:
                initial = identity(os.fstat(fd))
                require(0 <= initial[2] <= MAX_BYTES, 'WAL_SIZE_BOUND')
                if initial[2] == 0:
                    require(m['cursor'] is None, 'UNMANAGED_WAL_RESET')
                    nm=dict(m,kind='committed-read-only-day',parent=expected.manifest,day=session.day,segment=None)
                    def no_write_final():
                        session.check();session.folder.matches(name,fd)
                        require(identity(os.fstat(fd))==initial and identity(os.fstat(session.main_fd))==tuple(state['main_anchor']), 'SOURCE_PUBLICATION_DRIFT')
                    return self.publish(expected.branch,nm,dict(state,revision=state['revision']+1),expected=expected,final_check=no_write_final)
                require(initial[2]>=32,'WAL_SIZE_BOUND')
                require((initial[2]-32) % (size+24) == 0, 'PARTIAL_WAL_FRAME')
                header = os.pread(fd, 32, 0)
                self.meter.wal_read += len(header)
                parsed = wal_header(header, size)
                cursor = m['cursor']
                start, current = 0, parsed['checksum']
                if cursor is not None:
                    require(header.hex() == cursor['header_hex'], 'UNMANAGED_WAL_RESET')
                    start = cursor['frame']
                    current = tuple(cursor['checksum'])
                    boundary = os.pread(fd, 24, 32+(start-1)*(size+24))
                    self.meter.wal_read += len(boundary)
                    require(len(boundary) == 24 and boundary.hex() == cursor['terminal_header_hex'], 'WAL_CURSOR_CHANGED')
                total = (initial[2]-32)//(size+24)
                require(0 <= total-start <= MAX_NEW_FRAMES, 'NO_NEW_FRAMES_OR_FRAME_BOUND')
                if total == start:
                    nm=dict(m,kind='committed-read-only-day',parent=expected.manifest,day=session.day,segment=None)
                    def no_new_final():
                        session.check();session.folder.matches(name,fd)
                        seen=os.pread(fd,32,0);self.meter.wal_read+=len(seen)
                        require(identity(os.fstat(fd))==initial and seen==header and identity(os.fstat(session.main_fd))==tuple(state['main_anchor']),'SOURCE_PUBLICATION_DRIFT')
                    return self.publish(expected.branch,nm,dict(state,revision=state['revision']+1),expected=expected,final_check=no_new_final)
                root, pages, frames, last_commit = m['root'], m['pages'], [], 0
                for number in range(start+1, total+1):
                    frame = os.pread(fd, size+24, 32+(number-1)*(size+24))
                    self.meter.wal_read += len(frame)
                    require(len(frame) == size+24, 'WAL_FRAME_SHORT')
                    pg, dbpages, salt1, salt2, c1, c2 = struct.unpack('>6I', frame[:24])
                    require((salt1, salt2) == parsed['salts'], 'WAL_FRAME_SALTS')
                    require(1 <= pg <= MAX_PAGES and 0 <= dbpages <= MAX_PAGES, 'WAL_PAGE_OR_COMMIT_SIZE')
                    current = checksum(frame[:8]+frame[24:], parsed['magic'], current)
                    require(current == (c1, c2), 'WAL_FRAME_CHECKSUM')
                    page_cid = self.put('pages', frame[24:])
                    root = self.replace_page(root, pg, page_cid)
                    frames.append({'number': number, 'header_hex': frame[:24].hex(), 'page': page_cid})
                    if dbpages:
                        require(dbpages*size <= MAX_BYTES, 'COMMITTED_DATABASE_BOUND')
                        root = self.clip(root, dbpages)
                        pages, last_commit = dbpages, number
                require(last_commit == total, 'UNCOMMITTED_WAL_TAIL')
                require(self.node(root, 0)['count'] == pages, 'COMMITTED_PAGE_COVERAGE')
                cursor = {'header_hex': header.hex(), 'frame': total, 'checksum': list(current), 'terminal_header_hex': frames[-1]['header_hex']}
                segment = self.put('segments', canonical({'schema': DELTA_SCHEMA, 'header_hex': header.hex(), 'from_frame': start+1, 'to_frame': total, 'frames': frames}))
                nm = dict(m, kind='committed-delta', parent=expected.manifest, day=session.day, root=root, pages=pages, segment=segment, cursor=cursor)
                def final():
                    session.check()
                    session.folder.matches(name, fd)
                    observed=os.pread(fd,32,0);self.meter.wal_read += len(observed)
                    require(identity(os.fstat(fd)) == initial and observed == header and identity(os.fstat(session.main_fd)) == tuple(state['main_anchor']), 'SOURCE_PUBLICATION_DRIFT')
                self.hook('after-wal-capture')
                final()
                result = self.publish(expected.branch, nm, dict(state, revision=state['revision']+1), expected=expected, final_check=final)
            finally:
                os.close(fd)
            session.check()
            self.manifest(result)
            self.check()
            return result

    @writer
    def reset(self, session, expected):
        """Managed native checkpoint; no full image/ancestor rehash here."""
        with session.fence():
            m, state = self.manifest(expected)
            require(session.bound_branch==(str(self.directory.path),expected.branch),'SOURCE_BRANCH_BINDING')
            require(session.day == m['day'] and m['cursor'] is not None, 'RESET_UNCAPTURED_DAY')
            wal = session.path.with_name(session.path.name+'-wal')
            cursor = m['cursor']
            require(wal.stat().st_size == 32+cursor['frame']*(m['page_size']+24), 'RESET_UNCAPTURED_FRAMES')
            fd = session.folder.open(wal.name, os.O_RDONLY)
            try:
                require(os.pread(fd, 32, 0).hex() == cursor['header_hex'] and os.pread(fd, 24, 32+(cursor['frame']-1)*(m['page_size']+24)).hex() == cursor['terminal_header_hex'], 'RESET_CURSOR')
                self.meter.wal_read += 56
                session.folder.matches(wal.name, fd)
            finally: os.close(fd)
            session.checkpoint_truncate()
            self.meter.native_checkpoints += 1
            initial = identity(os.fstat(session.main_fd))
            self.hook('after-native-reset')
            require(initial[2] == m['pages']*m['page_size'], 'RESET_MAIN_SIZE')
            require(os.pread(session.main_fd, 100, 0) == self.page(m['root'], 1, m['page_size'])[:100], 'RESET_MAIN_HEADER')
            self.meter.source_header_read += 100
            nm = dict(m, kind='managed-native-reset', parent=expected.manifest, segment=None, cursor=None, epoch=m['epoch']+1)
            def final():
                session.check()
                require(identity(os.fstat(session.main_fd)) == initial and wal.stat().st_size == 0, 'RESET_PUBLICATION_DRIFT')
            return self.publish(expected.branch, nm, {'main_anchor': list(initial), 'revision': state['revision']+1}, expected=expected, final_check=final)

    @writer
    def export(self, expected, destination):
        """Explicit whole-image closure; computes full raw SHA and rechecks pages."""
        self.closure_floor(expected)
        m = self.checkpoint(expected.manifest)
        destination = Path(os.path.abspath(destination))
        d = Directory(destination.parent)
        fd = None
        try:
            self.free_at(d,m['pages']*m['page_size']+65536)
            fd = d.open(destination.name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
            h = hashlib.sha256()
            for number in range(1, m['pages']+1):
                raw = self.page(m['root'], number, m['page_size'])
                self.meter.export_read += len(raw)
                h.update(raw)
                view = memoryview(raw)
                while view:
                    n = os.write(fd, view)
                    require(n > 0, 'EXPORT_WRITE_SHORT')
                    self.meter.export_written += n
                    view = view[n:]
            os.fsync(fd)
            d.matches(destination.name, fd)
            os.fsync(d.fd)
            require(os.fstat(fd).st_size == m['pages']*m['page_size'], 'EXPORT_SIZE')
            check = hashlib.sha256()
            offset = 0
            while offset < os.fstat(fd).st_size:
                raw = os.pread(fd, min(65536, os.fstat(fd).st_size-offset), offset)
                require(raw, 'EXPORT_READBACK_SHORT')
                self.meter.export_readback += len(raw)
                check.update(raw); offset += len(raw)
            require(check.hexdigest() == h.hexdigest(), 'EXPORT_READBACK_SHA')
            d.matches(destination.name, fd)
            self.checkpoint(expected.manifest)
            self.check()
            self.free_at(d)
            proof = {'schema': 'dams-private-page-export-proof-v1', 'producer_sha256': self.source_sha256, 'manifest': expected.manifest, 'byte_sha256': h.hexdigest(), 'bytes': offset, 'anchor': list(identity(os.fstat(fd))), 'science_complete': False, 'old_source_resume_allowed': False}
            proof_cid = self.put('segments', canonical(proof))
            os.close(fd)
            fd = None
            self.hook('after-export-fd-close')
            d.check()
            self.free_at(d)
            require(identity(os.stat(destination.name, dir_fd=d.fd, follow_symlinks=False)) == tuple(proof['anchor']), 'CLOSED_EXPORT_CHANGED')
            self.closure_check()
            self.closure_floor(expected)
            self.check()
            return dict(proof, proof_cid=proof_cid)
        finally:
            if fd is not None: os.close(fd)
            d.close()

    @writer
    def fork(self, session, parent, export_proof, *, branch):
        with session.fence():
            self.closure_floor(parent)
            m = self.checkpoint(parent.manifest)
            require(type(export_proof) is dict and 'proof_cid' in export_proof, 'FORK_EXPORT_PROOF')
            pinned = self.json_object('segments', export_proof['proof_cid'], 16384)
            require(dict(pinned, proof_cid=export_proof['proof_cid']) == export_proof and pinned['producer_sha256'] == self.source_sha256 and export_proof['schema'] == 'dams-private-page-export-proof-v1' and export_proof['manifest'] == parent.manifest and export_proof['science_complete'] is False and export_proof['bytes'] == m['pages']*m['page_size'], 'FORK_EXPORT_PROOF')
            require(identity(os.fstat(session.main_fd))[:4] == tuple(export_proof['anchor'])[:4] and session.day == m['day'], 'FORK_IMAGE_CHANGED')
            require(session.bound_branch is None,'SOURCE_ALREADY_BOUND_TO_BRANCH')
            fork_initial=identity(os.fstat(session.main_fd))
            h=hashlib.sha256();offset=0
            while offset < export_proof['bytes']:
                data=os.pread(session.main_fd,min(65536,export_proof['bytes']-offset),offset)
                require(data,'FORK_IMAGE_SHORT');self.meter.fork_read+=len(data);offset+=len(data);h.update(data)
            require(h.hexdigest()==export_proof['byte_sha256'] and identity(os.fstat(session.main_fd))==fork_initial,'FORK_IMAGE_SHA_CHANGED')
            session.bound_branch=(str(self.directory.path),branch)
            require(session.fixture_source_sha256 == m['fixture_source_sha256'] and session.source_id == m['sqlite_source_id'], 'FORK_SOURCE_PIN')
            require(session.path.with_name(session.path.name+'-wal').stat().st_size == 0, 'FORK_WAL_NOT_EMPTY')
            nm = dict(m, kind='fork', parent=parent.manifest, segment=None, cursor=None, epoch=0)
            initial = identity(os.fstat(session.main_fd))
            def final():
                session.check()
                require(identity(os.fstat(session.main_fd)) == initial and session.path.with_name(session.path.name+'-wal').stat().st_size == 0, 'FORK_PUBLICATION_DRIFT')
            return self.publish(branch, nm, {'main_anchor': list(initial), 'revision': 0}, final_check=final)

    def close(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd);self.lock_fd=None
        for d in self.areas.values(): d.close()
        self.directory.close()
