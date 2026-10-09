"""Finite ledger-only exactness/adversarial controls; never creates a Model."""
import dataclasses
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
import zlib
from contextlib import closing

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT
sys.path.insert(0, str(SOURCE))
pkg = types.ModuleType('original_dams');pkg.__path__ = [str(SOURCE / 'dams_sim')]
sys.modules['original_dams'] = pkg
fixture = Path(__file__).resolve().parent / 'fixtures' / 'legacy_v1_longitudinal_storage.py'
spec = importlib.util.spec_from_file_location('original_dams.longitudinal_storage', fixture)
original_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = original_module
spec.loader.exec_module(original_module)
Original = original_module.ExactLedger
from dams_sim.longitudinal_storage import ExactLedger, snapshot_semantics, TABLE_COLUMNS
from dams_sim import journal_chunks
from dams_sim.storage import canonical


@dataclasses.dataclass
class Claim:
    ready: int
    event: str
    agent: int
    created: int
    observed: float
    audit_detected: bool
    fraudulent: bool
    correction: bool = False
    priority: float = 0.


class JournalChunks(unittest.TestCase):
    def setUp(self):
        temporary_root=ROOT/'runs'/'journal-chunks'/'ledger-controls'
        temporary_root.mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='fixture-',dir=temporary_root)
        self.path = Path(self.temp.name)
        self.opened = []

    def tearDown(self):
        for x in reversed(self.opened):
            try:x.close()
            except sqlite3.Error:pass
        self.temp.cleanup()

    def ledger(self, name, original=False, **kw):
        obj = (Original if original else ExactLedger)(self.path / name, **kw)
        self.opened.append(obj);return obj

    def mutate(self,path,sql,args=()):
        with closing(sqlite3.connect(path)) as db:
            with db:db.execute(sql,args)

    def same(self, old, new):
        self.assertEqual(old.materialize(), new.materialize())
        self.assertEqual(old.semantic_digest(), new.semantic_digest())
        self.assertEqual({t:len(list(old.rows(t))) for t in TABLE_COLUMNS}, new.logical_row_counts())

    def populate(self, ledger):
        results = []
        for day in range(5):
            ledger.begin()
            for i in range(13):
                event = f'event:{day}:{i}'
                payload = {'unicode':'臺灣🌏\x00\n'*(i+1), 'number':1.25,
                           'nested':{2:'two',10:[False,None,-0.0]}, 'event':event}
                ledger.add_journal(day,'work',event,payload)
                results.append(ledger.mark_seen(event))
                results.append(ledger.mark_seen(event))
                ledger.push('review',i%3,Claim(day+1,event,i,day,i+.125,i%2==0,i%3==0,priority=i%4/3))
                ledger.add_credit(i,i%3,i/7)
                ledger.add_delay('review',i%4)
            for guild in range(3):
                ready=ledger.take_ready('review',guild,day,2)
                results.append([dataclasses.asdict(c) for c in ready])
            ledger.decay_credits(.99)
            ledger.commit()
            results.append({'day':day,'logical_rows':ledger.materialize(),
                            'semantic_sha256':ledger.semantic_digest()})
        return results

    def test_exact_days_queues_duplicates_and_large_unicode_row(self):
        old=self.ledger('old',original=True)
        new=self.ledger('new',journal_chunk_bytes=128)
        self.assertEqual(self.populate(old),self.populate(new));self.same(old,new)
        for x in (old,new):
            x.begin();x.add_journal(5,'day_end','large',{'text':'臺灣🌏\n'*6000});x.commit()
        self.same(old,new)
        self.assertEqual(new.db.execute('SELECT count(*) FROM journal').fetchone()[0],0)
        self.assertLessEqual(new.db.execute('SELECT max(raw_bytes) FROM journal_chunks').fetchone()[0],128)
        self.assertEqual(list(new.db.execute('SELECT name,seq FROM sqlite_sequence ORDER BY name')),
                         list(old.db.execute('SELECT name,seq FROM sqlite_sequence ORDER BY name')))
        self.assertNotIn('journal_chunks',dict(new.db.execute('SELECT name,seq FROM sqlite_sequence')))

    def test_atomic_rollback_after_insert_and_before_delete(self):
        old=self.ledger('old',original=True);new=self.ledger('new',journal_chunk_bytes=128)
        self.populate(old);self.populate(new);before=new.semantic_digest()
        for x in (old,new):
            x.begin();x.add_journal(6,'work','uncommitted',{'x':'q'*900})
            x.add_credit(90,4,3.0);x.mark_seen('uncommitted')
        real=journal_chunks.zlib.compress;calls=0
        def fail_second(data):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('injected packing failure after first insert')
            return real(data)
        with patch.object(journal_chunks.zlib,'compress',side_effect=fail_second):
            with self.assertRaises(RuntimeError):new.commit()
        self.assertTrue(new.db.in_transaction);new.rollback();old.rollback()
        self.same(old,new);self.assertEqual(new.semantic_digest(),before)
        new.db.execute("CREATE TEMP TRIGGER refuse_delete BEFORE DELETE ON journal BEGIN SELECT RAISE(ABORT,'injected delete failure'); END")
        new.begin();new.add_journal(7,'work','delete-failure',{'x':'y'*600})
        with self.assertRaises(sqlite3.IntegrityError):new.commit()
        new.rollback();self.assertEqual(new.semantic_digest(),before)
        new.db.execute('DROP TRIGGER refuse_delete')

    def test_snapshot_restore_self_contained_and_new_sequence(self):
        old=self.ledger('old',original=True);new=self.ledger('new',journal_chunk_bytes=512)
        self.populate(old);self.populate(new)
        a=old.snapshot(self.path/'old.sqlite');b=new.snapshot(self.path/'new.sqlite')
        self.assertEqual(a['semantic_sha256'],b['semantic_sha256']);self.assertEqual(a['row_counts'],b['row_counts'])
        self.assertEqual(b['schema_version'],2)
        self.assertEqual(snapshot_semantics(self.path/'new.sqlite'),
                         {k:b[k] for k in ('schema_version','semantic_sha256','row_counts')})
        restored=self.ledger('restored',snapshot=self.path/'new.sqlite')
        self.assertEqual(restored.journal_chunk_bytes,512);self.same(old,restored)
        for x in (old,restored):
            x.begin();x.add_journal(8,'work','next',{'x':False});x.commit()
        self.same(old,restored)
        self.assertEqual(list(self.path.glob('new.sqlite*')),[self.path/'new.sqlite'])
        facade=ExactLedger.__new__(ExactLedger);facade.db=restored.db
        self.assertEqual(facade.semantic_digest(),restored.semantic_digest())
        self.assertEqual(facade.logical_row_counts(),restored.logical_row_counts())

    def test_restore_rows_replaces_chunks_gaps_and_empty(self):
        old=self.ledger('old',original=True);new=self.ledger('new',journal_chunk_bytes=128)
        self.populate(old);self.populate(new)
        values=old.materialize();values['journal']=[(2,0,'x','a','{}'),(10,1,'y','b','{}')]
        values['__sequences__']=[('journal',15),('pending',90)]
        for x in (old,new):x.restore_rows(values)
        self.same(old,new)
        for x in (old,new):x.restore_rows(values)
        self.same(old,new)
        for x in (old,new):x.begin();x.add_journal(2,'z','c',{});x.commit()
        self.same(old,new);self.assertEqual(list(new.rows('journal'))[-1][0],16)
        values['journal']=[]
        for x in (old,new):x.restore_rows(values)
        self.same(old,new);self.assertEqual(new.db.execute('SELECT count(*) FROM journal_chunks').fetchone()[0],0)

    def test_logical_counts_materialization_guard_and_hot_tail(self):
        new=self.ledger('new',journal_chunk_bytes=128);self.populate(new)
        total=sum(new.logical_row_counts().values())
        with self.assertRaises(MemoryError):new.materialize(max_rows=total-1)
        self.assertEqual(len(new.materialize(max_rows=total)['journal']),65)
        new.begin();new.add_journal(9,'x','hot',{'x':1})
        self.assertEqual(list(new.rows('journal'))[-1][3],'hot')
        self.assertEqual(new.logical_row_counts()['journal'],66);new.rollback()
        self.assertEqual(new.logical_row_counts()['journal'],65)

    def test_restore_rows_invalid_sequence_is_atomic(self):
        new=self.ledger('new',journal_chunk_bytes=128);self.populate(new)
        before=new.materialize();sha=new.semantic_digest()
        for name,sequences in [('missing',[x for x in before['__sequences__'] if x[0]!='journal']),
                               ('lower',[(n,0 if n=='journal' else s) for n,s in before['__sequences__']])]:
            with self.subTest(name=name):
                values={**before,'__sequences__':sequences}
                with self.assertRaises(ValueError):new.restore_rows(values)
                self.assertFalse(new.db.in_transaction)
                self.assertEqual(new.materialize(),before)
                self.assertEqual(new.semantic_digest(),sha)

    def test_default_snapshot_options_and_output_exclusivity(self):
        new=self.ledger('default');old=self.ledger('old',original=True)
        self.populate(new);self.populate(old);self.same(old,new)
        self.assertEqual(new.schema_version,1);self.assertIsNone(new.journal_chunk_bytes)
        new.snapshot(self.path/'legacy.sqlite')
        old.snapshot(self.path/'original-legacy.sqlite')
        self.assertEqual((self.path/'legacy.sqlite').read_bytes(),
                         (self.path/'original-legacy.sqlite').read_bytes())
        for value in (True,127,4194305,1.5,'128'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):self.ledger('invalid-'+str(value),journal_chunk_bytes=value)
        valid=self.ledger('max',journal_chunk_bytes=4194304)
        valid.begin();valid.add_journal(0,'x','a',{});valid.commit();valid.validate_schema()
        with self.assertRaises(ValueError):self.ledger('native',page_options=object(),journal_chunk_bytes=128)
        with self.assertRaises(ValueError):self.ledger('mismatch',snapshot=self.path/'legacy.sqlite',journal_chunk_bytes=128)
        sentinel=self.path/'sentinel';sentinel.write_bytes(b'KEEP')
        with self.assertRaises(ValueError):new.snapshot(sentinel)
        link=self.path/'link';link.symlink_to(sentinel)
        with self.assertRaises(ValueError):new.snapshot(link)
        self.assertEqual(sentinel.read_bytes(),b'KEEP')

    def test_corruptions_refused_by_restore_and_snapshot_validator(self):
        new=self.ledger('new',journal_chunk_bytes=256)
        new.begin();new.add_journal(0,'x','a',{'text':'🌏a'*100});new.add_journal(1,'x','b',{});new.commit()
        new.snapshot(self.path/'clean.sqlite')
        mutations={
            'encoded_sha':"UPDATE journal_chunks SET encoded_sha256='"+'0'*64+"' WHERE chunk=1",
            'raw_sha':"UPDATE journal_chunks SET raw_sha256='"+'0'*64+"' WHERE chunk=1",
            'raw_length':'UPDATE journal_chunks SET raw_bytes=raw_bytes+1 WHERE chunk=1',
            'encoded_length':'UPDATE journal_chunks SET encoded_bytes=encoded_bytes+1 WHERE chunk=1',
            'row_count':'UPDATE journal_chunks SET row_count=row_count+1 WHERE chunk=1',
            'first_seq':'UPDATE journal_chunks SET first_seq=first_seq+1 WHERE chunk=1',
            'last_seq':'UPDATE journal_chunks SET last_seq=last_seq+1 WHERE chunk=1',
            'ordinal_gap':'DELETE FROM journal_chunks WHERE chunk=1',
            'missing_tail':'DELETE FROM journal_chunks WHERE chunk=(SELECT max(chunk) FROM journal_chunks)',
            'catalog_count':'UPDATE journal_chunk_config SET chunk_count=chunk_count+1',
            'catalog_rows':'UPDATE journal_chunk_config SET archived_rows=archived_rows+1',
            'catalog_last':'UPDATE journal_chunk_config SET last_seq=last_seq+1',
            'sequence_lower':"UPDATE sqlite_sequence SET seq=0 WHERE name='journal'",
            'sequence_missing':"DELETE FROM sqlite_sequence WHERE name='journal'",
            'tail_overlap':"INSERT INTO journal VALUES(1,0,'x','overlap','{}')",
            'config_missing':'DELETE FROM journal_chunk_config',
            'schema_unknown':'PRAGMA user_version=3',
            'extra_table':'CREATE TABLE surprise(x)',
        }
        for name,sql in mutations.items():
            with self.subTest(name=name):
                p=self.path/(name+'.sqlite');shutil.copyfile(self.path/'clean.sqlite',p)
                self.mutate(p,sql)
                with self.assertRaises(ValueError):snapshot_semantics(p)
                with self.assertRaises(ValueError):self.ledger('refuse-'+name,snapshot=p)

    def test_bounded_decompression_and_forged_well_hashed_streams(self):
        new=self.ledger('new',journal_chunk_bytes=256)
        new.begin();new.add_journal(0,'x','a',{});new.commit();new.snapshot(self.path/'clean.sqlite')
        base_raw=canonical([1,0,'x','a','{}'])+b'\n'
        raw_cases={
            'truncated_row':base_raw[:-1],
            'invalid_utf8':b'[1,0,"x","a","\xff"]\n',
            'noncanonical':b'[1, 0, "x", "a", "{}"]\n',
            'wrong_type':canonical([True,0,'x','a','{}'])+b'\n',
            'wrong_seq':canonical([2,0,'x','a','{}'])+b'\n',
            'duplicate_row':base_raw+base_raw,
        }
        for name,raw in raw_cases.items():
            with self.subTest(name=name):
                p=self.path/(name+'.sqlite');shutil.copyfile(self.path/'clean.sqlite',p)
                encoded=zlib.compress(raw)
                self.mutate(p,'UPDATE journal_chunks SET raw_bytes=?,encoded_bytes=?,raw_sha256=?,encoded_sha256=?,payload=?',
                            (len(raw),len(encoded),hashlib.sha256(raw).hexdigest(),hashlib.sha256(encoded).hexdigest(),encoded))
                with self.assertRaises(ValueError):snapshot_semantics(p)
        for name,encoded in [('truncated_zlib',zlib.compress(base_raw)[:-1]),
                             ('trailing_stream',zlib.compress(base_raw)+zlib.compress(b'other')),
                             ('decompression_bomb',zlib.compress(b'x'*100000))]:
            with self.subTest(name=name):
                p=self.path/(name+'.sqlite');shutil.copyfile(self.path/'clean.sqlite',p)
                self.mutate(p,'UPDATE journal_chunks SET encoded_bytes=?,encoded_sha256=?,payload=?',
                            (len(encoded),hashlib.sha256(encoded).hexdigest(),encoded))
                with self.assertRaises(ValueError):snapshot_semantics(p)


if __name__=='__main__':unittest.main(verbosity=2)
