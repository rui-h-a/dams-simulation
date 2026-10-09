"""Exact disk-backed longitudinal queues, event journal and delay histograms.

SQLite is an execution storage representation, not a different scientific model.
Canonical row order and a streamed semantic digest are independent of SQLite page
layout. Snapshot bytes remain separately hashed for integrity and atomic recovery.
"""
from __future__ import annotations
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .storage import canonical,file_digest

SCHEMA_VERSION=1
CHECKPOINT_FORMAT_VERSION=2
STATE_HASH_CODEC='json-string-keys-v2'
LEGACY_STATE_HASH_CODEC='python-numeric-state-keys-v1'
INTEGER_STATE_MAPS=('adoption','guild_alias','memory','migration_paid','routine','slot_guild','slots')


def json_chunks(value):
    """Stream deterministic JSON whose mapping order survives a JSON round trip.

    JSON object keys are strings. Convert integer keys before sorting, rather
    than letting Python sort integers before JSONEncoder converts them. Reject
    aliases such as 2 and '2'; silently collapsing them would lose state.
    This is a repository codec, not a claim of RFC 8785 canonicalization.
    """
    if isinstance(value,dict):
        keys={}
        for key in value:
            if type(key) not in (str,int):raise ValueError('checkpoint JSON keys must be strings or integers')
            text=str(key)
            if text in keys:raise ValueError('checkpoint JSON keys collide after string conversion')
            keys[text]=key
        yield '{'
        for n,text in enumerate(sorted(keys)):
            if n:yield ','
            yield canonical(text).decode();yield ':'
            yield from json_chunks(value[keys[text]])
        yield '}'
    elif isinstance(value,(list,tuple)):
        yield '['
        for n,item in enumerate(value):
            if n:yield ','
            yield from json_chunks(item)
        yield ']'
    else:
        yield json.dumps(value,separators=(',',':'),allow_nan=False)


def integer_state_maps(header):
    """Reconstruct only the seven integer-key maps in historical schema 1.

    Numeric ordering is part of the original writer's digest. This typed view
    recovers that digest without changing its persisted bytes or provenance.
    It also rejects noncanonical keys and aliases before a restore can lose
    identities through int(key) conversion.
    """
    result=dict(header)
    for name in INTEGER_STATE_MAPS:
        result[name]=integer_key_map(header[name],name=name)
    return result


def integer_key_map(values,*,name):
    if not isinstance(values,dict):raise ValueError('longitudinal integer state map differs: '+name)
    typed={}
    for key,value in values.items():
        if type(key) is int:integer=key
        elif type(key) is str:
            try:integer=int(key)
            except ValueError:raise ValueError('longitudinal integer state key differs: '+name) from None
            if str(integer)!=key:raise ValueError('longitudinal integer state key is not canonical: '+name)
        else:raise ValueError('longitudinal integer state key differs: '+name)
        if integer<0 or integer in typed:raise ValueError('longitudinal integer state key aliases or is negative: '+name)
        typed[integer]=value
    return typed


def hash_state_json(h,header,codec):
    if codec==STATE_HASH_CODEC:chunks=json_chunks(header)
    elif codec==LEGACY_STATE_HASH_CODEC:
        chunks=json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(integer_state_maps(header))
    else:raise ValueError('unknown longitudinal state hash codec')
    for chunk in chunks:h.update(chunk.encode())


def checkpoint_codec(envelope):
    """An unmarked historical checkpoint has exactly the historical codec."""
    version=envelope.get('checkpoint_format_version')
    codec=envelope.get('state_hash_codec')
    state_codec=envelope['state'].get('state_hash_codec')
    if version is None and codec is None and state_codec is None:return LEGACY_STATE_HASH_CODEC
    if type(version) is not int or version!=CHECKPOINT_FORMAT_VERSION or codec!=STATE_HASH_CODEC or state_codec!=codec:
        raise ValueError('unknown or inconsistent longitudinal checkpoint format/hash codec')
    return codec


def load_checkpoint_json(path):
    def object_pairs(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate checkpoint JSON object key')
            result[key]=value
        return result
    def nonfinite(value):raise ValueError('nonfinite checkpoint JSON value')
    return json.loads(Path(path).read_text(),object_pairs_hook=object_pairs,parse_constant=nonfinite)


def journal_json(data,*,codec=STATE_HASH_CODEC,kind=None):
    if codec==STATE_HASH_CODEC:
        # Each journal payload is bounded by one event/member/domain record.
        # Normalize this small record once and retain json.dumps' C fast path;
        # large checkpoint headers instead use the streaming encoder above.
        def string_keys(value):
            if isinstance(value,dict):
                result={}
                for key,item in value.items():
                    if type(key) not in (str,int):raise ValueError('journal JSON keys must be strings or integers')
                    text=str(key)
                    if text in result:raise ValueError('journal JSON keys collide after string conversion')
                    result[text]=string_keys(item)
                return result
            if isinstance(value,(list,tuple)):return [string_keys(item) for item in value]
            return value
        return canonical(string_keys(data)).decode()
    if codec!=LEGACY_STATE_HASH_CODEC:raise ValueError('unknown longitudinal journal codec')
    # Only these historical day_end fields had integer JSON object keys. All
    # other payload dictionaries used string keys in the original writer.
    if kind=='day_end':
        data=dict(data)
        for name in ('adoption_days','memory','routine'):
            data[name]=integer_key_map(data[name],name='journal.'+name)
    return canonical(data).decode()

TABLE_COLUMNS={
 'pending':('seq','kind','guild','ready','priority','event','agent','created','observed','audit_detected','fraudulent','correction'),
 'seen':('event',),
 'journal':('seq','day','kind','event','payload'),
 'credits':('person','guild','amount'),
 'delays':('kind','delay','count'),
}
TABLE_ORDER={'pending':'seq','seen':'event COLLATE BINARY','journal':'seq','credits':'person,guild','delays':'kind,delay'}
TABLE_TYPES={'pending':('INTEGER','TEXT','INTEGER','INTEGER','REAL','TEXT','INTEGER','INTEGER','REAL','INTEGER','INTEGER','INTEGER'),
             'seen':('TEXT',),'journal':('INTEGER','INTEGER','TEXT','TEXT','TEXT'),'credits':('INTEGER','INTEGER','REAL'),'delays':('TEXT','INTEGER','INTEGER')}

class ExactLedger:
    def __init__(self,directory=None,*,snapshot=None,page_options=None):
        self._pages=None
        self.journal_codec=STATE_HASH_CODEC
        self.directory=Path(directory) if directory is not None else Path(tempfile.mkdtemp(prefix='dams-longitudinal-'))
        if page_options is None:self.directory.mkdir(parents=True,exist_ok=True)
        else:
            from .native_page_backend import NativePageOptions, ensure_directory, need, CODEC_SHA256, codec
            need(type(page_options) is NativePageOptions, 'native options type invalid')
            page_options.validate()
            need(sqlite3.sqlite_version==codec.SQLITE_PIN,'native pages require reviewed SQLite runtime '+codec.SQLITE_PIN)
            need(codec.module_sha()==CODEC_SHA256,'accepted native codec source changed')
            ensure_directory(self.directory)
        self.path=self.directory/'.longitudinal-working.sqlite'
        if self.path.exists() or self.path.is_symlink():raise ValueError('working longitudinal database already exists; restore into a fresh directory')
        if page_options is None:self.db=sqlite3.connect(self.path)
        else:
            from .native_page_backend import open_new_database
            self.db=open_new_database(self.path)
        if page_options is None:
            self.db.execute('PRAGMA journal_mode=DELETE');self.db.execute('PRAGMA synchronous=FULL')
        else:
            from .native_page_backend import configure
            configure(self.db)
        if snapshot is not None:
            source=sqlite3.connect(f'file:{Path(snapshot).resolve()}?mode=ro',uri=True)
            try:source.backup(self.db)
            finally:source.close()
            self.validate_schema()
        else:
            self.db.executescript('''
            PRAGMA user_version=1;
            CREATE TABLE pending(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,guild INTEGER NOT NULL,ready INTEGER NOT NULL,priority REAL NOT NULL,event TEXT NOT NULL,agent INTEGER NOT NULL,created INTEGER NOT NULL,observed REAL NOT NULL,audit_detected INTEGER NOT NULL,fraudulent INTEGER NOT NULL,correction INTEGER NOT NULL);
            CREATE INDEX pending_ready ON pending(kind,guild,ready,priority,seq);
            CREATE TABLE seen(event TEXT PRIMARY KEY NOT NULL);
            CREATE TABLE journal(seq INTEGER PRIMARY KEY AUTOINCREMENT,day INTEGER NOT NULL,kind TEXT NOT NULL,event TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE credits(person INTEGER NOT NULL,guild INTEGER NOT NULL,amount REAL NOT NULL,PRIMARY KEY(person,guild));
            CREATE TABLE delays(kind TEXT NOT NULL,delay INTEGER NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(kind,delay));
            ''')
            self.db.commit()
        if page_options is not None:
            from .native_page_backend import NativePageBackend
            self._pages=NativePageBackend(self.db,self.path,page_options)
    def validate_schema(self):
        if self.db.execute('PRAGMA user_version').fetchone()[0]!=SCHEMA_VERSION:raise ValueError('unknown longitudinal ledger schema')
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables!=set(TABLE_COLUMNS):raise ValueError('longitudinal ledger table inventory differs')
        for table,columns in TABLE_COLUMNS.items():
            info=list(self.db.execute(f'PRAGMA table_info({table})'))
            if tuple(r[1] for r in info)!=columns or tuple(r[2] for r in info)!=TABLE_TYPES[table]:raise ValueError('longitudinal ledger columns/types differ: '+table)
            pk={'pending':(1,)+(0,)*11,'seen':(1,),'journal':(1,0,0,0,0),'credits':(1,2,0),'delays':(1,2,0)}[table]
            required=(0,)+(1,)*(len(columns)-1) if table in {'pending','journal'} else (1,)*len(columns)
            if tuple(r[5] for r in info)!=pk or tuple(r[3] for r in info)!=required or any(r[4] is not None for r in info):raise ValueError('longitudinal ledger constraints differ: '+table)
        expected={'pending','seen','journal','credits','delays','sqlite_sequence','pending_ready','sqlite_autoindex_seen_1','sqlite_autoindex_credits_1','sqlite_autoindex_delays_1'}
        if {r[0] for r in self.db.execute('SELECT name FROM sqlite_master')}!=expected:raise ValueError('longitudinal ledger schema contains unplanned objects')
        if tuple(r[2] for r in self.db.execute('PRAGMA index_info(pending_ready)'))!=('kind','guild','ready','priority','seq'):raise ValueError('longitudinal queue index differs')
        sequences=list(self.db.execute('SELECT name,seq FROM sqlite_sequence'))
        if len({r[0] for r in sequences})!=len(sequences) or any(name not in {'pending','journal'} or type(seq) is not int or seq<0 or seq<self.db.execute(f'SELECT coalesce(max(seq),0) FROM {name}').fetchone()[0] for name,seq in sequences):raise ValueError('longitudinal next-event sequence differs')
        if self.db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('SQLite snapshot is corrupt')
    @property
    def native_pages_active(self):
        return self._pages is not None and self._pages.session is not None
    @property
    def retained_handle(self):
        return None if self._pages is None else self._pages.retained_handle
    @property
    def latest_floor(self):
        return None if self._pages is None else self._pages.latest_floor
    def _mutation_gate(self):
        if self.native_pages_active and not self._pages.in_day:
            raise ValueError('native ledger mutation requires the owned daily commit gate')
    def begin(self):
        if self.native_pages_active:raise ValueError('native daily transaction is owned by commit_day')
        self.db.execute('BEGIN IMMEDIATE')
    def commit(self):
        if self.native_pages_active:raise ValueError('native daily transaction is owned by commit_day')
        self.db.commit()
    def rollback(self):
        if self.native_pages_active:self._pages.session.poisoned=True
        self.db.rollback()
    def activate_pages(self,day=0,*,known_latest_floor=None):
        if self._pages is None:raise ValueError('native page options were not selected')
        return self._pages.activate(day,known_latest_floor=known_latest_floor)
    def commit_day(self,completed_day,callback):
        if self.native_pages_active:return self._pages.commit_day(completed_day,callback)
        self.begin()
        try:result=callback();self.commit();return result
        except BaseException:self.rollback();raise
    def snapshot_pages(self,day,*,known_latest_floor=None):
        if not self.native_pages_active:raise ValueError('native pages not activated')
        value=self._pages.snapshot(day,known_latest_floor=known_latest_floor)
        # Preserve the original ordered-row digest; a page root is never its substitute.
        value.update(schema_version=SCHEMA_VERSION,semantic_sha256=self.semantic_digest(),
                     row_counts={t:self.db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLE_COLUMNS})
        self._pages.session.check();self._pages._floor(known_latest_floor)
        return value
    def export_pages(self,selected,destination,*,known_latest_floor):
        if not self.native_pages_active:raise ValueError('native pages not activated')
        return self._pages.export(selected,destination,known_latest_floor=known_latest_floor)
    def reset_pages(self,*,known_latest_floor=None):
        if not self.native_pages_active:raise ValueError('native pages not activated')
        return self._pages.reset(known_latest_floor=known_latest_floor)
    @classmethod
    def from_pages(cls,directory,descriptor,*,page_options,known_latest_floor,verify_export=None):
        from .native_page_backend import (NativePageBackend, configure, descriptor_check,
                                         ensure_directory, export_existing, EXPORT_KEYS, need)
        descriptor_check(descriptor,page_options,TABLE_COLUMNS)
        need(known_latest_floor is not None,'native restore requires external latest floor')
        frozen=canonical(descriptor);descriptor=json.loads(frozen)
        need(verify_export is None or callable(verify_export),'native export verifier must be callable')
        directory=Path(directory);ensure_directory(directory)
        path=directory/'.longitudinal-working.sqlite'
        receipt=export_existing(page_options,descriptor,path,known_latest_floor=known_latest_floor,tables=TABLE_COLUMNS)
        original_proof={k:receipt[k] for k in EXPORT_KEYS}
        if verify_export is not None:
            # Caller verifies scientific JSON + flat-row state on this one exported inode.
            verify_export(path,json.loads(frozen),json.loads(canonical(receipt)))
        NativePageBackend.verify_export(path,original_proof)
        semantics=snapshot_semantics(path)
        need(semantics['semantic_sha256']==descriptor['semantic_sha256'] and semantics['row_counts']==descriptor['row_counts'],
             'native exported ledger semantic digest/counts differ')
        NativePageBackend.verify_export(path,original_proof)
        obj=cls.__new__(cls);obj.journal_codec=STATE_HASH_CODEC
        obj.directory=directory;obj.path=path;obj._pages=None
        obj.db=sqlite3.connect(path.absolute().as_uri()+'?mode=rw',uri=True)
        try:
            # Full schema/quick_check already ran on this exact exported inode.
            # Repeating quick_check on the EXCLUSIVE writer opens SQLite's TEMP
            # database and violates the reviewed single-main NativeSession gate.
            configure(obj.db);obj.db.execute('PRAGMA schema_version').fetchone()
            obj._pages=NativePageBackend(obj.db,path,page_options)
            obj._pages.adopt({**descriptor,'_export_proof':original_proof},known_latest_floor=receipt['latest_floor'])
            return obj
        except BaseException:
            if obj._pages is not None:obj._pages.close()
            else:obj.db.close()
            raise
    def add_journal(self,day,kind,event,data):
        self._mutation_gate()
        self.db.execute('INSERT INTO journal(day,kind,event,payload) VALUES(?,?,?,?)',(day,kind,event,journal_json(data,codec=self.journal_codec,kind=kind)))
    def mark_seen(self,event):
        self._mutation_gate()
        cursor=self.db.execute('INSERT OR IGNORE INTO seen VALUES(?)',(event,));return cursor.rowcount==1
    def seen_count(self):return self.db.execute('SELECT count(*) FROM seen').fetchone()[0]
    def push(self,kind,guild,claim):
        self._mutation_gate()
        d=asdict(claim)
        self.db.execute('INSERT INTO pending(kind,guild,ready,priority,event,agent,created,observed,audit_detected,fraudulent,correction) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(kind,guild,d['ready'],d['priority'],d['event'],d['agent'],d['created'],d['observed'],int(d['audit_detected']),int(d['fraudulent']),int(d['correction'])))
    def take_ready(self,kind,guild,day,limit):
        self._mutation_gate()
        if limit<=0:return []
        rows=self.db.execute('SELECT seq,ready,event,agent,created,observed,audit_detected,fraudulent,correction,priority FROM pending WHERE kind=? AND guild=? AND ready<=? ORDER BY ready,priority,seq LIMIT ?',(kind,guild,day,limit)).fetchall()
        self.db.executemany('DELETE FROM pending WHERE seq=?',((r[0],) for r in rows))
        from .model import Claim
        return [Claim(r[1],r[2],r[3],r[4],r[5],bool(r[6]),bool(r[7]),bool(r[8]),r[9]) for r in rows]
    def take_sequence(self,seq):
        self._mutation_gate()
        row=self.db.execute('SELECT ready,event,agent,created,observed,audit_detected,fraudulent,correction,priority FROM pending WHERE seq=?',(seq,)).fetchone()
        if row is None:raise ValueError('pending evidence row disappeared')
        self.db.execute('DELETE FROM pending WHERE seq=?',(seq,))
        from .model import Claim
        return Claim(*row[:5],bool(row[5]),bool(row[6]),bool(row[7]),row[8])
    def take_ready_domains(self,kind,domains,day,limit):
        self._mutation_gate()
        if limit<=0:return []
        domains=set(domains)
        if len(domains)<=300:
            rows=list(self.db.execute('SELECT seq,guild FROM pending WHERE kind=? AND guild IN ('+','.join('?' for _ in domains)+') AND ready<=? ORDER BY ready,priority,seq LIMIT ?',
                (kind,*sorted(domains),day,limit)))
        else:
            rows=[]
            for seq,guild in self.db.execute('SELECT seq,guild FROM pending WHERE kind=? AND ready<=? ORDER BY ready,priority,seq',(kind,day)):
                if guild in domains:rows.append((seq,guild))
                if len(rows)==limit:break
        return [(guild,self.take_sequence(seq)) for seq,guild in rows]
    def pending_count(self,kind=None,guild=None):
        conditions=[];args=[]
        if kind is not None:conditions.append('kind=?');args.append(kind)
        if guild is not None:conditions.append('guild=?');args.append(guild)
        sql='SELECT count(*) FROM pending'+(' WHERE '+' AND '.join(conditions) if conditions else '')
        return self.db.execute(sql,args).fetchone()[0]
    def add_credit(self,person,guild,value):
        self._mutation_gate()
        self.db.execute('INSERT INTO credits VALUES(?,?,?) ON CONFLICT(person,guild) DO UPDATE SET amount=amount+excluded.amount',(person,guild,value))
    def credit(self,person,guild):
        row=self.db.execute('SELECT amount FROM credits WHERE person=? AND guild=?',(person,guild)).fetchone();return row[0] if row else 0.
    def decay_credits(self,factor):
        self._mutation_gate();self.db.execute('UPDATE credits SET amount=amount*?',(factor,))
    def add_delay(self,kind,delay):
        self._mutation_gate();self.db.execute('INSERT INTO delays VALUES(?,?,1) ON CONFLICT(kind,delay) DO UPDATE SET count=count+1',(kind,delay))
    def delay_summary(self,kind,q=.95):
        rows=self.db.execute('SELECT delay,count FROM delays WHERE kind=? ORDER BY delay',(kind,)).fetchall();n=sum(c for _,c in rows)
        if not n:return None,None
        mean=sum(d*c for d,c in rows)/n;position=(n-1)*q;lo=int(position);hi=lo+int(position>lo);a=b=None;seen=0
        for d,count in rows:
            if a is None and lo<seen+count:a=d
            if hi<seen+count:b=d;break
            seen+=count
        return mean,a+(b-a)*(position-lo)
    def rows(self,table):
        if table not in TABLE_COLUMNS:raise ValueError('unknown table')
        yield from self.db.execute(f'SELECT * FROM {table} ORDER BY {TABLE_ORDER[table]}')
    def semantic_digest(self):
        h=hashlib.sha256()
        for table in sorted(TABLE_COLUMNS):
            h.update(canonical({'table':table,'columns':TABLE_COLUMNS[table]}));h.update(b'\n')
            for row in self.rows(table):h.update(canonical(row));h.update(b'\n')
        h.update(canonical({'sqlite_sequence':list(self.db.execute('SELECT name,seq FROM sqlite_sequence ORDER BY name'))}))
        return h.hexdigest()
    def snapshot(self,path):
        if self.native_pages_active:raise ValueError('native checkpoints use snapshot_pages; final images use export_pages')
        if self.db.in_transaction:raise ValueError('checkpoint must be at a committed day boundary')
        path=Path(path)
        if path.exists():raise ValueError('immutable longitudinal snapshot already exists')
        target=sqlite3.connect(path)
        try:self.db.backup(target);target.commit()
        finally:target.close()
        import os
        with path.open('rb') as stream:os.fsync(stream.fileno())
        return {'schema_version':SCHEMA_VERSION,'file':path.name,'sha256':file_digest(path),'semantic_sha256':self.semantic_digest(),'bytes':path.stat().st_size,'row_counts':{t:self.db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLE_COLUMNS}}
    def materialize(self,max_rows=100000):
        if sum(self.db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLE_COLUMNS)>max_rows:raise MemoryError('use streaming snapshot API for large longitudinal state')
        return {**{t:list(self.rows(t)) for t in TABLE_COLUMNS},'__sequences__':list(self.db.execute('SELECT name,seq FROM sqlite_sequence ORDER BY name'))}
    def restore_rows(self,values):
        self._mutation_gate()
        if set(values)!=set(TABLE_COLUMNS)|{'__sequences__'}:raise ValueError('ledger row inventory differs')
        with self.db:
            for table,columns in TABLE_COLUMNS.items():
                self.db.execute(f'DELETE FROM {table}')
                self.db.executemany(f'INSERT INTO {table} VALUES('+','.join('?' for _ in columns)+')',values[table])
            self.db.execute('DELETE FROM sqlite_sequence')
            self.db.executemany('INSERT INTO sqlite_sequence VALUES(?,?)',values['__sequences__'])
        self.validate_schema()
    def close(self):
        if self._pages is not None:self._pages.close()
        else:self.db.close()


def snapshot_semantics(path):
    """Verify a sealed, complete flat image; never pass an active WAL base."""
    # A completed native export retains a WAL-mode SQLite header, but its
    # complete bytes are in this one immutable image. A normal mode=ro reader
    # would create WAL/SHM siblings and change the published output roster.
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    obj=ExactLedger.__new__(ExactLedger);obj.db=db
    try:
        obj.validate_schema()
        return {'schema_version':SCHEMA_VERSION,'semantic_sha256':obj.semantic_digest(),'row_counts':{t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLE_COLUMNS}}
    finally:db.close()
