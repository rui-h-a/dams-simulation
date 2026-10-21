"""Bounded, exact-byte cloud transport; never changes scientific file formats."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import zlib

from cloud_control import GuardError

CODEC = 'deflate-chunks-v1'
RAW_CODEC = 'raw-v1'
CHUNK_BYTES = 4 * 1024**2
MAX_ENCODED_CHUNK = CHUNK_BYTES + 4096
SCHEMA = 1
MAX_DESCRIPTOR_BYTES = 32 * 1024**2
# Historical raw snapshots remain readable under the shared 32 MiB metadata
# bound. A new stage may freeze a much smaller prospective request-cost cap.
MAX_ARCHIVE_FILES = 131072


def lexical_path(path):
    """Inspect lexical ancestors before canonicalization can hide a symlink."""
    path=Path(path)
    if '..' in path.parts:
        raise GuardError('parent traversal in local archive path')
    path=path.absolute()
    # macOS itself aliases these root directories; only their exact OS targets
    # are accepted. User/workspace aliases are never accepted or resolved first.
    system_aliases={'/var':'/private/var','/tmp':'/private/tmp','/etc':'/private/etc'} if sys.platform=='darwin' else {}
    for current in reversed((path,*path.parents)):
        try:info=current.lstat()
        except FileNotFoundError:continue
        except OSError:raise GuardError('cannot inspect local archive path') from None
        if stat.S_ISLNK(info.st_mode):
            if str(current) not in system_aliases or str(current.resolve())!=system_aliases[str(current)]:
                raise GuardError('symlink ancestor in local archive path')
        elif current!=path and not stat.S_ISDIR(info.st_mode):
            raise GuardError('local archive ancestor is not a directory')
    return path


def file_limit(value):
    value=natural(value,'file cap',positive=True)
    if value>MAX_ARCHIVE_FILES:raise GuardError('archive file cap exceeds the transport hard bound')
    return value


def natural(value, label, *, positive=False):
    if type(value) is not int or value < int(positive) or value > 2**63 - 1:
        raise GuardError('invalid archive ' + label)
    return value


def sha256(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise GuardError('invalid archive SHA-256')
    return value


def safe_name(name):
    if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name
            or len(name) > 4096 or any(ord(c) < 32 for c in name)):
        raise GuardError('unsafe archive path')
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in ('', '.', '..') for p in name.split('/')) or path.as_posix() != name:
        raise GuardError('unsafe archive path')
    return path


def identity(path):
    return stat_identity(Path(path).stat())


def stat_identity(s):
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def require_capacity(path, required, minimum_free):
    natural(required, 'required bytes'); natural(minimum_free, 'minimum free bytes')
    path = lexical_path(path)
    while not path.exists():
        if path == path.parent:
            raise GuardError('archive filesystem is unavailable')
        path = path.parent
    if shutil.disk_usage(path).free < required + minimum_free:
        raise GuardError('actual archive disk capacity is insufficient')


def validate_manifest(value):
    keys = {'schema', 'codec', 'chunk_bytes', 'raw_bytes', 'raw_sha256', 'encoded_bytes', 'chunks'}
    if (not isinstance(value, dict) or set(value) != keys or type(value['schema']) is not int
            or value['schema'] != SCHEMA or value['codec'] != CODEC
            or type(value['chunk_bytes']) is not int or value['chunk_bytes'] != CHUNK_BYTES):
        raise GuardError('unsupported archive manifest')
    length = natural(value['raw_bytes'], 'raw bytes')
    sha256(value['raw_sha256']); natural(value['encoded_bytes'], 'encoded bytes')
    chunks = value['chunks']
    if not isinstance(chunks, list) or len(chunks) != (length + CHUNK_BYTES - 1) // CHUNK_BYTES:
        raise GuardError('archive chunk roster differs from the raw length')
    offset = encoded = 0
    chunk_keys = {'offset', 'raw_bytes', 'raw_sha256', 'encoded_bytes', 'encoded_sha256'}
    for chunk in chunks:
        if not isinstance(chunk, dict) or set(chunk) != chunk_keys:
            raise GuardError('unsupported archive chunk')
        if natural(chunk['offset'], 'offset') != offset:
            raise GuardError('archive offsets are not contiguous and ordered')
        size = natural(chunk['raw_bytes'], 'chunk raw bytes', positive=True)
        if size != min(CHUNK_BYTES, length - offset):
            raise GuardError('archive chunk length differs from the fixed boundary')
        n = natural(chunk['encoded_bytes'], 'chunk encoded bytes', positive=True)
        if n > MAX_ENCODED_CHUNK:
            raise GuardError('encoded chunk exceeds its hard bound')
        sha256(chunk['raw_sha256']); sha256(chunk['encoded_sha256'])
        offset += size; encoded += n
    if offset != length or encoded != value['encoded_bytes']:
        raise GuardError('archive total length differs')
    if not length and value['raw_sha256'] != hashlib.sha256(b'').hexdigest():
        raise GuardError('empty archive checksum differs')
    return value


def encode_file(source, publish, *, max_raw_bytes, minimum_free_bytes, scratch):
    """Publish immutable chunks through callback(path, chunk); no full-file copy."""
    source = lexical_path(source);scratch=lexical_path(scratch); before = identity(source)
    if source.is_symlink() or not source.is_file() or before[2] > natural(max_raw_bytes, 'raw cap'):
        raise GuardError('source exceeds the archive raw allowance or is unsafe')
    require_capacity(scratch, MAX_ENCODED_CHUNK * 2, minimum_free_bytes)
    chunks = []; raw_hash = hashlib.sha256(); offset = encoded_total = 0
    fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream, tempfile.TemporaryDirectory(prefix='dams-chunk-', dir=scratch) as folder:
        if stat_identity(os.fstat(stream.fileno())) != before:
            raise GuardError('source identity changed before archive encoding')
        target = Path(folder) / 'encoded'
        while raw := stream.read(CHUNK_BYTES):
            if offset + len(raw) > max_raw_bytes:
                raise GuardError('source grew beyond the archive raw allowance')
            encoded = zlib.compress(raw, level=1)
            if len(encoded) > MAX_ENCODED_CHUNK:
                raise GuardError('encoded chunk exceeds its hard bound')
            require_capacity(folder, MAX_ENCODED_CHUNK * 2, minimum_free_bytes)
            target.write_bytes(encoded)
            chunk = {'offset': offset, 'raw_bytes': len(raw), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
                     'encoded_bytes': len(encoded), 'encoded_sha256': hashlib.sha256(encoded).hexdigest()}
            publish(target, chunk)
            chunks.append(chunk); raw_hash.update(raw); offset += len(raw); encoded_total += len(encoded)
    if source.is_symlink() or identity(source) != before or offset != before[2]:
        raise GuardError('source changed during archive encoding')
    return validate_manifest({'schema': SCHEMA, 'codec': CODEC, 'chunk_bytes': CHUNK_BYTES,
                             'raw_bytes': offset, 'raw_sha256': raw_hash.hexdigest(),
                             'encoded_bytes': encoded_total, 'chunks': chunks})


def decode_chunk(encoded, chunk):
    """A bounded zlib decoder; one exact stream and at most one raw chunk."""
    if len(encoded) != chunk['encoded_bytes'] or hashlib.sha256(encoded).hexdigest() != chunk['encoded_sha256']:
        raise GuardError('encoded chunk checksum/length differs')
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(encoded, chunk['raw_bytes'] + 1)
    except zlib.error:
        raise GuardError('invalid zlib chunk stream') from None
    if (len(raw) != chunk['raw_bytes'] or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or hashlib.sha256(raw).hexdigest() != chunk['raw_sha256']):
        raise GuardError('zlib chunk is truncated, trailing, oversized or corrupt')
    return raw


def restore_file(manifest, fetch, destination, *, max_raw_bytes, minimum_free_bytes):
    """fetch(chunk, temporary_path) must itself enforce encoded_bytes on input."""
    validate_manifest(manifest)
    if manifest['raw_bytes'] > natural(max_raw_bytes, 'raw cap'):
        raise GuardError('archive raw length exceeds the trusted allowance')
    destination = lexical_path(destination)
    if destination.exists() or destination.is_symlink():
        raise GuardError('archive restore target already exists')
    require_capacity(destination.parent, manifest['raw_bytes'] + MAX_ENCODED_CHUNK + 8192, minimum_free_bytes)
    destination.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(); length = 0
    try:
        with tempfile.TemporaryDirectory(prefix='dams-restore-chunk-', dir=destination.parent) as folder:
            encoded_path = Path(folder) / 'encoded'
            with destination.open('xb') as output:
                for chunk in manifest['chunks']:
                    require_capacity(destination.parent, chunk['raw_bytes'] + chunk['encoded_bytes'], minimum_free_bytes)
                    fetch(chunk, encoded_path)
                    # Limit the read even when a faulty callback ignored its byte cap.
                    with encoded_path.open('rb') as stream:
                        encoded = stream.read(chunk['encoded_bytes'] + 1)
                    raw = decode_chunk(encoded, chunk)
                    output.write(raw); h.update(raw); length += len(raw)
                    encoded_path.unlink()
                output.flush(); os.fsync(output.fileno())
        if length != manifest['raw_bytes'] or h.hexdigest() != manifest['raw_sha256']:
            raise GuardError('restored logical file checksum/length differs')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return h.hexdigest()
