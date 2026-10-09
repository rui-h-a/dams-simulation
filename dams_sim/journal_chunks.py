"""Lossless bounded zlib fragments of the original ordered journal row stream.

The BLOBs and their original rows are changed in one SQLite transaction. A
single row may span arbitrarily many fragments: the decompressed fragment is
bounded, while reconstructing that original row necessarily uses row-sized
memory, as the original TEXT journal API did. No event-size cap is introduced.
"""
from __future__ import annotations

import hashlib
import json
import zlib

from .storage import canonical

MIN_CHUNK_BYTES = 128
MAX_CHUNK_BYTES = 4 * 1024 * 1024
CHUNK_COLUMNS = ('chunk', 'first_seq', 'last_seq', 'row_count', 'raw_bytes',
                 'encoded_bytes', 'raw_sha256', 'encoded_sha256', 'payload')
CHUNK_TYPES = ('INTEGER',) * 6 + ('TEXT', 'TEXT', 'BLOB')
CONFIG_COLUMNS = ('id', 'chunk_bytes', 'chunk_count', 'archived_rows', 'last_seq')
CONFIG_TYPES = ('INTEGER',) * 5
SCHEMA_SQL = '''
CREATE TABLE journal_chunk_config(id INTEGER PRIMARY KEY,chunk_bytes INTEGER NOT NULL,chunk_count INTEGER NOT NULL,archived_rows INTEGER NOT NULL,last_seq INTEGER NOT NULL);
CREATE TABLE journal_chunks(chunk INTEGER PRIMARY KEY,first_seq INTEGER NOT NULL,last_seq INTEGER NOT NULL,row_count INTEGER NOT NULL,raw_bytes INTEGER NOT NULL,encoded_bytes INTEGER NOT NULL,raw_sha256 TEXT NOT NULL,encoded_sha256 TEXT NOT NULL,payload BLOB NOT NULL);
'''


def checked_target(value):
    if type(value) is not int or not MIN_CHUNK_BYTES <= value <= MAX_CHUNK_BYTES:
        raise ValueError('journal chunk target must be an integer from 128 to 4194304 bytes')
    return value


def read_config(db):
    values = list(db.execute('SELECT * FROM journal_chunk_config'))
    if len(values) != 1 or values[0][0] != 1:
        raise ValueError('journal chunk configuration differs')
    value = values[0]
    checked_target(value[1])
    if (any(type(x) is not int or x < 0 for x in value[2:])
            or (value[2] == 0) != (value[3] == 0)
            or (value[3] == 0) != (value[4] == 0)):
        raise ValueError('journal chunk catalog counters differ')
    return value


def read_target(db):
    return read_config(db)[1]


def encoded_limit(target):
    # A deliberately conservative ceiling above zlib's compressBound formula.
    return target + (target // 16384 + 1) * 5 + 64


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _digest_value(value):
    return (type(value) is str and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def decode_chunk(item, target):
    chunk, first, last, count, raw_size, encoded_size, raw_sha, encoded_sha, encoded = item
    if (any(type(x) is not int for x in (chunk, first, last, count, raw_size, encoded_size))
            or chunk <= 0 or first <= 0 or last < first or count < 0
            or not 1 <= raw_size <= target or count > raw_size
            or not 1 <= encoded_size <= encoded_limit(target)
            or type(encoded) is not bytes or len(encoded) != encoded_size
            or not _digest_value(raw_sha) or not _digest_value(encoded_sha)
            or _sha(encoded) != encoded_sha):
        raise ValueError('journal chunk metadata/encoded integrity differs')
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(encoded, raw_size + 1)
    except zlib.error as error:
        raise ValueError('journal chunk compression stream is corrupt') from error
    if (len(raw) != raw_size or not decoder.eof or decoder.unconsumed_tail
            or decoder.unused_data or _sha(raw) != raw_sha):
        raise ValueError('journal chunk raw integrity/length/stream differs')
    return raw


def _row(data):
    def nonfinite(value):
        raise ValueError('nonfinite journal chunk JSON value')
    try:
        value = json.loads(data, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('journal chunk row encoding differs') from error
    if (type(value) is not list or len(value) != 5 or type(value[0]) is not int
            or value[0] <= 0 or type(value[1]) not in (int, float)
            or any(type(v) is not str for v in value[2:])
            or canonical(value) + b'\n' != data):
        raise ValueError('journal chunk original row shape/types/canonical bytes differ')
    return tuple(value)


def iter_rows(db, target):
    """Validate every fragment and yield original rows, then the hot SQL tail."""
    target = checked_target(target)
    config = read_config(db)
    if config[1] != target:
        raise ValueError('journal chunk target differs from persisted catalog')
    carry = bytearray()
    carry_seq = None
    completed_seq = 0
    previous_chunk = 0
    total_rows = 0
    for item in db.execute('SELECT * FROM journal_chunks ORDER BY chunk'):
        raw = decode_chunk(item, target)
        chunk, first, last, expected_count = item[:4]
        if chunk != previous_chunk + 1:
            raise ValueError('journal chunk ordinal gap/order differs')
        previous_chunk = chunk
        observed_first = carry_seq
        observed_last = None
        observed_count = 0
        completed_rows = []
        offset = 0
        while offset < len(raw):
            end = raw.find(b'\n', offset)
            if end < 0:
                carry.extend(raw[offset:])
                if carry_seq is not None and carry_seq != last:
                    raise ValueError('journal split row sequence differs')
                carry_seq = last
                if carry_seq <= completed_seq:
                    raise ValueError('journal split row order differs')
                if observed_first is None:
                    observed_first = carry_seq
                observed_last = carry_seq
                break
            carry.extend(raw[offset:end + 1])
            row = _row(bytes(carry))
            seq = row[0]
            if seq <= completed_seq or (carry_seq is not None and seq != carry_seq):
                raise ValueError('journal chunk row sequence/order differs')
            if observed_first is None:
                observed_first = seq
            observed_last = seq
            observed_count += 1
            completed_seq = seq
            carry.clear()
            carry_seq = None
            offset = end + 1
            completed_rows.append(row)
        if (observed_first != first or observed_last != last
                or observed_count != expected_count):
            raise ValueError('journal chunk row count/sequence metadata differs')
        total_rows += observed_count
        yield from completed_rows
    if carry:
        raise ValueError('journal chunk row is truncated')
    if (previous_chunk, total_rows, completed_seq) != config[2:]:
        raise ValueError('journal chunk catalog completeness differs')
    for row in db.execute('SELECT * FROM journal ORDER BY seq'):
        if type(row[0]) is not int or row[0] <= completed_seq:
            raise ValueError('journal hot/chunk sequence overlaps or differs')
        completed_seq = row[0]
        yield row


def pack(db, target):
    """Append committed-day fragments and retire their SQL rows atomically.

    Caller owns an active SQLite transaction and commits only after this returns.
    No durable state or counters live outside that transaction.
    """
    if not db.in_transaction:
        raise ValueError('journal packing requires the original active transaction')
    target = checked_target(target)
    config = read_config(db)
    if config[1] != target:
        raise ValueError('journal chunk target differs from persisted catalog')
    latest = db.execute('SELECT chunk,last_seq FROM journal_chunks ORDER BY chunk DESC LIMIT 1').fetchone()
    ordinal, archived_last = latest if latest is not None else (0, 0)
    if (ordinal, archived_last) != (config[2], config[4]):
        raise ValueError('journal chunk catalog tail differs')
    archived_rows = config[3]
    buffer = bytearray()
    first = last = count = 0
    last_packed = archived_last

    def flush():
        nonlocal ordinal, first, last, count
        if not buffer:
            return
        raw = bytes(buffer)
        encoded = zlib.compress(raw)
        if len(encoded) > encoded_limit(target):
            raise ValueError('journal compressor exceeds bounded encoded fragment')
        ordinal += 1
        db.execute('INSERT INTO journal_chunks VALUES(?,?,?,?,?,?,?,?,?)',
                   (ordinal, first, last, count, len(raw), len(encoded),
                    _sha(raw), _sha(encoded), sqlite_blob(encoded)))
        buffer.clear()
        first = last = count = 0

    for row in db.execute('SELECT * FROM journal ORDER BY seq'):
        seq = row[0]
        if type(seq) is not int or seq <= last_packed:
            raise ValueError('journal packing sequence overlaps or differs')
        raw = canonical(row) + b'\n'
        offset = 0
        while offset < len(raw):
            if not buffer:
                first = seq
            amount = min(target - len(buffer), len(raw) - offset)
            buffer.extend(raw[offset:offset + amount])
            offset += amount
            last = seq
            if offset == len(raw):
                count += 1
            if len(buffer) == target:
                flush()
        last_packed = seq
        archived_rows += 1
    flush()
    if last_packed > archived_last:
        db.execute('DELETE FROM journal WHERE seq<=?', (last_packed,))
        db.execute('UPDATE journal_chunk_config SET chunk_count=?,archived_rows=?,last_seq=? WHERE id=1',
                   (ordinal, archived_rows, last_packed))


def sqlite_blob(value):
    # sqlite3 binds bytes as BLOB already; separate name makes intent explicit.
    return value
