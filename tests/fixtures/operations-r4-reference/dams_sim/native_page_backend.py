"""Opt-in native SQLite page storage; bounded I/O, never a Model semantic hash.

The vendored codec is the independently reviewed committed-page r3 source.
These profiles are a fixture envelope, not actual-scale workload admission.
The caller separately retains the latest owner floor and scientific state JSON.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from . import _committed_pages as codec

BACKEND = 'native-committed-pages-v1'
CODEC_SHA256 = '6e6f3cc05678e89910dbc9f1c518ae2642be65962b501b58cf4cc64c2353e9e6'
BRANCH = re.compile(r'[a-z0-9_-]{1,40}\Z')
HANDLE_KEYS = set(codec.Handle.__dataclass_fields__)


def need(value, message):
    if not value:
        raise ValueError(message)


def hash_value(value):
    need(type(value) is str and codec.CID.fullmatch(value) is not None, 'native hash must be lowercase SHA256')
    return value


@dataclass(frozen=True)
class PageProfile:
    max_image_bytes: int = codec.MAX_BYTES
    max_new_frames: int = codec.MAX_NEW_FRAMES
    max_pages: int = codec.MAX_PAGES
    min_free_bytes: int = codec.MIN_FREE

    def validate(self):
        for name, ceiling in (('max_image_bytes', codec.MAX_BYTES), ('max_new_frames', codec.MAX_NEW_FRAMES), ('max_pages', codec.MAX_PAGES)):
            value = getattr(self, name)
            need(type(value) is int and 0 < value <= ceiling, 'native profile exceeds reviewed toy envelope: '+name)
        need(type(self.min_free_bytes) is int and self.min_free_bytes >= codec.MIN_FREE, 'native free floor cannot be lowered')
        return self

    def document(self):
        self.validate()
        return {'schema': 'native-page-profile-1', **asdict(self)}

    def sha256(self):
        return hashlib.sha256(codec.canonical(self.document())).hexdigest()


@dataclass(frozen=True)
class NativePageOptions:
    store_root: Path
    branch: str
    execution_source_sha256: str
    profile: PageProfile = PageProfile()

    def validate(self):
        need(type(self.branch) is str and BRANCH.fullmatch(self.branch) is not None, 'native branch name invalid')
        hash_value(self.execution_source_sha256)
        need(type(self.profile) is PageProfile, 'native profile type invalid')
        self.profile.validate()
        path = os.fspath(self.store_root)
        need(type(path) is str and '..' not in path.split('/') and '\x00' not in path, 'native store lexical path invalid')
        return self


def handle(value):
    if type(value) is codec.Handle:
        result = value
    else:
        need(type(value) is dict and set(value) == HANDLE_KEYS, 'complete native Handle required')
        result = codec.Handle(**value)
    need(type(result.branch) is str and BRANCH.fullmatch(result.branch) is not None, 'native Handle branch invalid')
    for value in (result.manifest, result.head_sha256, result.closure_sha256):
        hash_value(value)
    need(type(result.closure_count) is int and result.closure_count > 0, 'native Handle floor invalid')
    need(type(result.record) is str and (result.record == result.branch+'.json' or re.fullmatch(re.escape(result.branch)+r'\.[0-9a-f]{64}\.json', result.record)), 'native Handle record invalid')
    return result


def configure(connection):
    need(sqlite3.sqlite_version == codec.SQLITE_PIN, 'native pages require reviewed SQLite runtime '+codec.SQLITE_PIN)
    need(not connection.in_transaction, 'native options require committed initialization')
    connection.execute('PRAGMA locking_mode=EXCLUSIVE')
    need(connection.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() == 'wal', 'native WAL mode unavailable')
    connection.execute('PRAGMA synchronous=FULL')
    connection.execute('PRAGMA wal_autocheckpoint=0')


class NativePageBackend:
    def __init__(self, connection, database, options):
        need(type(options) is NativePageOptions, 'native options type invalid')
        options.validate()
        need(codec.module_sha() == CODEC_SHA256, 'accepted native codec source changed')
        self.options = options
        self.profile_sha = options.profile.sha256()
        self.contract = (os.path.abspath(options.store_root), options.branch, options.execution_source_sha256, self.profile_sha)
        self.connection = connection
        self.database = Path(database)
        self.store = None
        self.session = None
        self.retained_handle = None
        self.latest_floor = None
        self.in_day = False
        self.closed = False

    def _check_contract(self):
        need(not self.closed, 'native backend closed')
        self.options.validate()
        need((os.path.abspath(self.options.store_root), self.options.branch, self.options.execution_source_sha256, self.options.profile.sha256()) == self.contract, 'native options/profile changed')
        need(codec.module_sha() == CODEC_SHA256, 'accepted native codec source changed')

    def _open_store(self, *, create=True):
        self._check_contract()
        path = Path(self.contract[0])
        parent = codec.Directory(path.parent)
        try:
            if create:
                try:
                    os.mkdir(path.name, 0o700, dir_fd=parent.fd)
                except FileExistsError:
                    pass
            else:
                need(stat.S_ISDIR(os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False).st_mode), 'existing native store required')
            parent.check()
            self.store = codec.PageStore(path, min_free_bytes=self.options.profile.min_free_bytes)
        finally:
            parent.close()

    def _floor(self, external=None):
        need(self.store is not None, 'native pages not activated')
        with self.store.locked_closure():
            self.store.closure_check(refresh=True)
            if self.latest_floor is not None:
                self.store.closure_floor(self.latest_floor)
            if external is not None:
                self.store.closure_floor(handle(external))

    def _advance(self, current):
        self.retained_handle = handle(current)
        self.latest_floor = replace(self.retained_handle, closure_count=len(self.store.records), closure_sha256=self.store.seal_sha)
        self.store.closure_floor(self.latest_floor)

    def _geometry(self, current):
        current = handle(current)
        raw = self.store.areas['heads'].read(current.record, 16384)
        state = json.loads(raw)
        need(codec.sha(raw) == current.head_sha256 and codec.canonical(state) == raw and
             state['manifest'] == current.manifest and state['branch'] == current.branch, 'native historical Handle head binding differs')
        if current.record == current.branch+'.json':
            need(state['parent_head'] is None, 'native historical root context differs')
        else:
            need(current.record == current.branch+'.'+str(state['parent_head'])+'.json', 'native historical predecessor differs')
        value = self.store.checkpoint(current.manifest)
        need(state['cursor'] == value['cursor'], 'native historical cursor differs')
        profile = self.options.profile
        need(value['pages'] <= profile.max_pages and value['pages']*value['page_size'] <= profile.max_image_bytes, 'native image exceeds admitted toy profile')
        need(value['fixture_source_sha256'] == self.options.execution_source_sha256, 'native execution source differs')
        return value

    def activate(self, day=0, *, known_latest_floor=None):
        self._check_contract()
        need(type(day) is int and day >= 0 and self.session is None, 'native activation day/session invalid')
        need(not self.connection.in_transaction, 'native activation requires committed day')
        self._open_store()
        if self.store.records:
            need(known_latest_floor is not None, 'existing native store requires external latest floor')
        self._floor(known_latest_floor)
        pages = self.connection.execute('PRAGMA page_count').fetchone()[0]
        size = self.connection.execute('PRAGMA page_size').fetchone()[0]
        need(pages <= self.options.profile.max_pages and pages*size <= self.options.profile.max_image_bytes, 'native base exceeds admitted toy profile')
        self.session = codec.NativeSession(self.connection, self.database, day=day, fixture_source_sha256=self.options.execution_source_sha256)
        result = self.store.seed(self.session, branch=self.options.branch)
        self._advance(result)
        self._geometry(result)
        return result

    def commit_day(self, completed_day, callback):
        self._check_contract()
        need(self.session is not None and callable(callback), 'native daily gate not active')
        need(type(completed_day) is int and completed_day >= 0, 'native completed day must be an integer')
        def body(connection):
            self.in_day = True
            try:
                result = callback()
                pages = self.connection.execute('PRAGMA page_count').fetchone()[0]
                size = self.connection.execute('PRAGMA page_size').fetchone()[0]
                need(pages <= self.options.profile.max_pages and pages*size <= self.options.profile.max_image_bytes, 'native daily image exceeds admitted toy profile')
                return result
            finally:
                self.in_day = False
        self.session.commit_day(completed_day, body)

    def snapshot(self, day, *, known_latest_floor=None):
        self._check_contract()
        need(self.session is not None and type(day) is int and day == self.session.day, 'native snapshot completed day differs')
        self.session.check()
        self._floor(known_latest_floor)
        previous = self._geometry(self.retained_handle)
        if day != previous['day']:
            need(day > previous['day'], 'native checkpoint day rollback')
            wal_bytes = self.database.with_name(self.database.name+'-wal').stat().st_size
            page_size = previous['page_size']
            start = previous['cursor']['frame'] if previous['cursor'] is not None else 0
            frames = max(0, (wal_bytes-32)//(page_size+24))
            need(wal_bytes <= self.options.profile.max_image_bytes and frames-start <= self.options.profile.max_new_frames, 'native WAL exceeds admitted toy profile')
            self._advance(self.store.capture(self.session, self.retained_handle))
        self._floor(known_latest_floor)
        value = self._geometry(self.retained_handle)
        return {'backend': BACKEND, 'codec_source_sha256': CODEC_SHA256, 'execution_source_sha256': self.options.execution_source_sha256,
                'day': day, 'handle': asdict(self.retained_handle), 'profile': self.options.profile.document(),
                'profile_sha256': self.profile_sha, 'bytes': value['pages']*value['page_size'], 'page_size': value['page_size']}

    def reset(self, *, known_latest_floor=None):
        self._check_contract(); self._floor(known_latest_floor)
        self._advance(self.store.reset(self.session, self.retained_handle))
        return self.retained_handle

    def export(self, selected, destination, *, known_latest_floor):
        self._check_contract(); selected = handle(selected)
        need(known_latest_floor is not None, 'export requires external latest floor')
        self._floor(known_latest_floor); self._geometry(selected)
        proof = self.store.export(selected, destination)
        # The native proof cutoff precedes its final directory cleanup. Check
        # the actual image the consumer will open, retaining the original token.
        if self.retained_handle is None:
            self.retained_handle = selected
        self._advance(self.retained_handle)
        path = Path(destination)
        self.verify_export(path, proof)
        self._floor(known_latest_floor)
        return {**proof, 'latest_floor': asdict(self.latest_floor), 'consumer_image_rechecked': True}

    @staticmethod
    def verify_export(path, proof):
        path = Path(path); parent = codec.Directory(path.parent)
        fd = None
        ancestors = [(a[3].st_dev, a[3].st_ino) for a in parent.anchors]
        try:
            fd = parent.open(path.name, os.O_RDONLY)
            token = codec.identity(os.fstat(fd))
            need(token == tuple(proof['anchor']) and token[2] == proof['bytes'], 'native exported image identity differs')
            total = 0; sha = hashlib.sha256()
            while True:
                part = os.read(fd, 65536)
                if not part: break
                total += len(part)
                need(total <= proof['bytes'], 'native exported image oversized')
                sha.update(part)
            need(total == proof['bytes'] and sha.hexdigest() == proof['byte_sha256'], 'native exported image bytes differ')
            parent.matches(path.name, fd)
            need(codec.identity(os.fstat(fd)) == token, 'native exported image changed during read')
        finally:
            if fd is not None: os.close(fd)
            parent.close()
        current = Path('/')
        for index, part in enumerate(parent.path.parts):
            if index:current = current/part
            observed = current.lstat()
            need(stat.S_ISDIR(observed.st_mode) and (observed.st_dev, observed.st_ino) == ancestors[index], 'native export ancestor changed after close')
        observed = path.lstat()
        need(codec.identity(observed) == tuple(proof['anchor']) and stat.S_ISREG(observed.st_mode), 'native exported image changed after close')

    def adopt(self, descriptor, *, known_latest_floor):
        self._check_contract()
        need(known_latest_floor is not None, 'native restore requires external latest floor')
        selected = handle(descriptor['handle'])
        need(descriptor['backend'] == BACKEND and descriptor['codec_source_sha256'] == CODEC_SHA256 and descriptor['execution_source_sha256'] == self.options.execution_source_sha256, 'native descriptor source/backend differs')
        need(descriptor['profile'] == self.options.profile.document() and descriptor['profile_sha256'] == self.profile_sha, 'native descriptor profile differs')
        self._open_store(create=False); self._floor(known_latest_floor)
        geometry = self._geometry(selected)
        need(type(descriptor['day']) is int and descriptor['day'] == geometry['day'] and descriptor['bytes'] == geometry['pages']*geometry['page_size'] and descriptor['page_size'] == geometry['page_size'], 'native descriptor day/geometry differs')
        need(self.options.branch != selected.branch, 'native restore requires new storage branch')
        # The connection is opened by the caller after export, so this method
        # binds the supplied independently checked original export proof.
        proof = descriptor['_export_proof']
        # Opening WAL can change ctime while the native fork contract retains
        # original dev/inode/size/mtime and rehashes every byte itself.
        self.session = codec.NativeSession(self.connection, self.database, day=descriptor['day'], fixture_source_sha256=self.options.execution_source_sha256)
        self._advance(self.store.fork(self.session, selected, proof, branch=self.options.branch))
        self._geometry(self.retained_handle)
        return self.retained_handle

    def close(self):
        if self.closed: return
        try:
            if self.store is not None: self.store.close()
        finally:
            if self.session is not None: self.session.close()
            else: self.connection.close()
            self.closed = True


DESCRIPTOR_KEYS = {'schema_version', 'backend', 'codec_source_sha256', 'execution_source_sha256',
                   'day', 'handle', 'profile', 'profile_sha256', 'bytes', 'page_size',
                   'semantic_sha256', 'row_counts'}
EXPORT_KEYS = {'schema', 'producer_sha256', 'manifest', 'byte_sha256', 'bytes', 'anchor',
               'science_complete', 'old_source_resume_allowed', 'proof_cid'}


def descriptor_check(value, options, tables):
    """Strict operational descriptor; the owner pins its whole CP bytes."""
    need(type(value) is dict and set(value) == DESCRIPTOR_KEYS, 'native descriptor inventory differs')
    options.validate()
    need(type(value['schema_version']) is int and value['schema_version'] == 1, 'native ledger schema differs')
    need(value['backend'] == BACKEND and value['codec_source_sha256'] == CODEC_SHA256 and
         value['execution_source_sha256'] == options.execution_source_sha256, 'native descriptor source/backend differs')
    handle(value['handle'])
    need(type(value['day']) is int and value['day'] >= 0, 'native descriptor day invalid')
    need(type(value['bytes']) is int and 0 < value['bytes'] <= options.profile.max_image_bytes, 'native descriptor bytes invalid')
    size = value['page_size']
    need(type(size) is int and 512 <= size <= 65536 and size & (size-1) == 0 and
         value['bytes'] % size == 0 and value['bytes']//size <= options.profile.max_pages, 'native descriptor page geometry invalid')
    need(type(value['profile']) is dict and value['profile'] == options.profile.document() and
         value['profile_sha256'] == options.profile.sha256(), 'native descriptor profile differs')
    # Equality alone admits True == 1. Check the typed fields separately.
    for name, expected in options.profile.document().items():
        need(type(value['profile'][name]) is type(expected), 'native descriptor profile type differs')
    hash_value(value['semantic_sha256'])
    counts = value['row_counts']
    need(type(counts) is dict and set(counts) == set(tables) and
         all(type(n) is int and n >= 0 for n in counts.values()), 'native descriptor row counts invalid')
    return value


def export_existing(options, descriptor, destination, *, known_latest_floor, tables):
    """One full materialization from an existing owner-fenced CAS, no recovery scan."""
    descriptor_check(descriptor, options, tables)
    need(known_latest_floor is not None, 'native restore requires external latest floor')
    latest = handle(known_latest_floor)
    # No SQLite connection and no fresh CAS root is created by this export.
    backend = NativePageBackend(None, destination, options)
    try:
        backend._open_store(create=False)
        backend._floor(latest)
        selected = handle(descriptor['handle'])
        m = backend._geometry(selected)
        need(m['day'] == descriptor['day'] and m['pages']*m['page_size'] == descriptor['bytes'] and
             m['page_size'] == descriptor['page_size'], 'native descriptor day/geometry differs')
        backend.retained_handle = selected
        return backend.export(selected, destination, known_latest_floor=latest)
    finally:
        if backend.store is not None:
            backend.store.close()
        backend.closed = True


def ensure_directory(path):
    """Create only missing regular directories under no-follow held ancestors."""
    lexical = os.fspath(path)
    need('..' not in lexical.split('/') and '\x00' not in lexical, 'native directory lexical path invalid')
    path = Path(os.path.abspath(lexical))
    pending = []
    current = path
    while not current.exists() and not current.is_symlink():
        pending.append(current.name)
        current = current.parent
    anchor = codec.Directory(current)
    try:
        for name in reversed(pending):
            os.mkdir(name, 0o700, dir_fd=anchor.fd)
            anchor.check()
            child = codec.Directory(anchor.path/name)
            anchor.close();anchor=child
        anchor.check()
    finally:
        anchor.close()
    return path


def open_new_database(path):
    """Exclusive new leaf; SQLite never creates or truncates an existing leaf."""
    path = Path(os.path.abspath(path))
    parent = codec.Directory(path.parent)
    fd = None;connection = None
    try:
        fd = parent.open(path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        os.fsync(fd);os.fsync(parent.fd)
        parent.matches(path.name,fd)
        connection=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True)
        parent.matches(path.name,fd)
        rows=connection.execute('PRAGMA database_list').fetchall()
        need(len(rows)==1 and os.path.abspath(rows[0][2])==str(path),'native working database path changed')
        return connection
    except BaseException:
        if connection is not None:connection.close()
        raise
    finally:
        if fd is not None:os.close(fd)
        parent.close()
