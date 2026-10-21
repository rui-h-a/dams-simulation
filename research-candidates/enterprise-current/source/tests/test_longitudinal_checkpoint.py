"""Checkpoint codecs preserve bytes, typed identities and execution provenance."""
import copy
import dataclasses
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dams_sim.config import Config
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.longitudinal_model import LongitudinalEngine, verify_snapshot, write_recovery_receipt
from dams_sim.longitudinal_storage import (CHECKPOINT_FORMAT_VERSION, STATE_HASH_CODEC,
    LEGACY_STATE_HASH_CODEC, checkpoint_codec, hash_state_json, json_chunks,
    journal_json, snapshot_semantics)
from dams_sim.model import Model
from dams_sim.storage import canonical, file_digest, source_hash


class CheckpointCodecTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.counter=0;self.models=[]

    def tearDown(self):
        for model in self.models:model.ledger.close()
        self.temp.cleanup()

    def directory(self):
        self.counter+=1;return self.root/str(self.counter)

    def config(self,*,n=12,guilds=12,days=18,**long):
        values={'annual_exit_probability':0.,'annual_vacancy_fill_probability':0.,'adoption_mode':'never'}
        values.update(long)
        return Config(n=n,days=days,guilds=guilds,team_size=1,trace_every_days=1,
                      max_output_mb=30,longitudinal=LongitudinalConfig(**values)).validate()

    def model(self,config):
        model=Model(config,storage_dir=self.directory());self.models.append(model);return model

    def restored(self,path,config):
        model=Model.restore_checkpoint(path,storage_dir=self.directory(),expected_config=config)
        self.models.append(model);return model

    def full_hash(self,state,ledger_hash,codec):
        h=hashlib.sha256();hash_state_json(h,{k:v for k,v in state.items() if k!='agents'},codec)
        for agent in state['agents']:h.update(b'\n');h.update(canonical(agent))
        h.update(b'\n');h.update(ledger_hash.encode());return h.hexdigest()

    def legacy_fixture(self,model,*,foreign_source=None):
        """Create separate tiny fixtures with the original writer's exact codec."""
        path=self.root/'legacy.json';database=path.with_suffix('.sqlite')
        side=model.ledger.snapshot(database)
        db=sqlite3.connect(database)
        try:
            for seq,payload in list(db.execute("SELECT seq,payload FROM journal WHERE kind='day_end'")):
                db.execute('UPDATE journal SET payload=? WHERE seq=?',
                           (journal_json(json.loads(payload),codec=LEGACY_STATE_HASH_CODEC,kind='day_end'),seq))
            db.commit()
        finally:db.close()
        side.update(snapshot_semantics(database));side['sha256']=file_digest(database);side['bytes']=database.stat().st_size
        state=copy.deepcopy(model._long.header());state.pop('state_hash_codec')
        state['agents']=[dataclasses.asdict(a) for a in model.agents]
        if foreign_source is not None:state['execution_source_sha256']=foreign_source
        envelope={'source_sha256':state['execution_source_sha256'],
                  'config_sha256':hashlib.sha256(canonical(model.config.to_dict())).hexdigest(),
                  'state_semantic_sha256':self.full_hash(state,side['semantic_sha256'],LEGACY_STATE_HASH_CODEC),
                  'ledger':side,'state':state}
        path.write_bytes(canonical(envelope)+b'\n')
        return path,envelope

    def test_string_key_codec_survives_nested_json_roundtrip(self):
        value={'slots':{2:2,10:10,1:1},'nested':[{10:'ten',2:'two'}]}
        encoded=''.join(json_chunks(value))
        self.assertEqual(encoded,''.join(json_chunks(json.loads(encoded))))
        self.assertLess(encoded.index('"10"'),encoded.index('"2"'))
        for bad in ({2:0,'2':1},{True:1},{1.5:1},{'x':float('nan')}):
            with self.assertRaises(ValueError):''.join(json_chunks(bad))

    def test_write_verify_restore_for_slot_and_guild_sort_boundaries(self):
        for guilds in (2,12):
            with self.subTest(guilds=guilds):
                config=self.config(guilds=guilds)
                model=self.model(config).run(3);path=self.root/f'cp-{guilds}.json'
                descriptor=model.write_checkpoint(path);envelope,database=verify_snapshot(path,expected_config=config)
                self.assertEqual(descriptor['checkpoint_format_version'],CHECKPOINT_FORMAT_VERSION)
                self.assertEqual(checkpoint_codec(envelope),STATE_HASH_CODEC)
                self.assertEqual(envelope['state']['model_version'],'longitudinal-1')
                self.assertEqual(envelope['state_semantic_sha256'],model._long.semantic_digest())
                restored=self.restored(path,config)
                self.assertEqual(model.state(),restored.state())
                self.assertEqual(file_digest(database),descriptor['files'][1]['sha256'])
                with self.assertRaisesRegex(ValueError,'immutable'):model.write_checkpoint(path)
                raw=Model.restore(json.loads(''.join(json_chunks(model.state()))),storage_dir=self.directory())
                self.models.append(raw);self.assertEqual(raw.state(),model.state())
                self.assertEqual(raw._long.semantic_digest(),envelope['state_semantic_sha256'])

    def test_uninterrupted_resume_differential_with_lifecycle_and_merger(self):
        config=dataclasses.replace(self.config(n=16,guilds=12,days=24,
            annual_vacancy_fill_probability=1.,exit_schedule=((4,0,'exit'),),
            workforce_targets=((6,14),(10,16)),guild_moves=((5,2,4),),guild_mergers=((7,0,1),),
            adoption_mode='fixed',adoption_day=11),update_interval_days=7)
        uninterrupted=self.model(config).run()
        split=self.model(config).run(9);path=self.root/'resume.json';split.write_checkpoint(path)
        resumed=self.restored(path,config).run()
        self.assertEqual(uninterrupted.state(),resumed.state())
        self.assertEqual(uninterrupted._long.semantic_digest(),resumed._long.semantic_digest())
        self.assertEqual(canonical(uninterrupted.summary()),canonical(resumed.summary()))

    def test_checkpoint_restored_fork_preserves_parent_and_branch_differential(self):
        config=self.config(days=8);parent=self.model(config).run(5)
        path=self.root/'parent.json';descriptor=parent.write_checkpoint(path)
        restored=self.restored(path,config)
        branch=dataclasses.replace(config,days=18,regime='equal',longitudinal=dataclasses.replace(
            config.longitudinal,adoption_mode='fixed',adoption_day=5,training_hours_per_workday=.2))
        left=parent.fork(branch,storage_dir=self.directory());right=restored.fork(branch,storage_dir=self.directory())
        self.models.extend((left,right))
        self.assertEqual(left.state(),right.state())
        self.assertEqual(left.branch_origin['parent_state_semantic_sha256'],descriptor['state_semantic_sha256'])
        self.assertEqual(left.ledger.semantic_digest(),parent.ledger.semantic_digest())
        left.run();path=self.root/'branch.json';left.write_checkpoint(path)
        final=self.restored(path,branch);right.run()
        self.assertEqual(left.state(),right.state());self.assertEqual(left.state(),final.state())

    def test_legacy_numeric_header_and_journal_recover_original_digest_and_bytes(self):
        config=self.config();model=self.model(config).run(3)
        path,original=self.legacy_fixture(model);database=path.with_suffix('.sqlite')
        before=(file_digest(path),file_digest(database))
        value,_=verify_snapshot(path,expected_source_sha256=source_hash())
        self.assertEqual(checkpoint_codec(value),LEGACY_STATE_HASH_CODEC)
        self.assertEqual(value['state_semantic_sha256'],original['state_semantic_sha256'])
        restored=self.restored(path,config)
        self.assertEqual(restored._long.semantic_digest(),original['state_semantic_sha256'])
        self.assertNotIn('state_hash_codec',restored.state())
        self.assertEqual(before,(file_digest(path),file_digest(database)))
        retained=restored.state();retained_digest=restored._long.semantic_digest()
        with self.assertRaisesRegex(ValueError,'inspection-only'):restored.run()
        branch_directory=self.directory()
        with self.assertRaisesRegex(ValueError,'inspection-only'):
            restored.fork(config,storage_dir=branch_directory)
        self.assertFalse(branch_directory.exists())
        self.assertEqual(restored.state(),retained);self.assertEqual(restored._long.semantic_digest(),retained_digest)
        with self.assertRaisesRegex(ValueError,'current state hash codec'):
            restored.write_checkpoint(self.root/'legacy-continuation.json')

    def test_historical_source_is_io_only_and_receipt_keeps_original_identity(self):
        model=self.model(self.config()).run(3);old_source='f'*64
        path,original=self.legacy_fixture(model,foreign_source=old_source)
        before=(file_digest(path),file_digest(path.with_suffix('.sqlite')))
        with self.assertRaisesRegex(ValueError,'source differs'):verify_snapshot(path)
        with patch.object(LongitudinalEngine,'__init__',side_effect=AssertionError('Model construction forbidden')):
            receipt=write_recovery_receipt(path,self.root/'recovery.json',expected_source_sha256=old_source)
        self.assertEqual(receipt['original_source_sha256'],old_source)
        self.assertEqual(receipt['verifier_source_sha256'],source_hash())
        self.assertEqual(receipt['original_model_version'],'longitudinal-1')
        self.assertEqual(receipt['original_state_semantic_sha256'],original['state_semantic_sha256'])
        self.assertEqual(receipt['original_ledger']['row_counts'],original['ledger']['row_counts'])
        self.assertTrue(receipt['resume_requires_original_execution_source'])
        with self.assertRaisesRegex(ValueError,'immutable'):
            write_recovery_receipt(path,self.root/'recovery.json',expected_source_sha256=old_source)
        with self.assertRaisesRegex(ValueError,'source differs'):
            Model.restore(original['state'],storage_dir=self.directory())
        with self.assertRaisesRegex(ValueError,'source differs'):
            Model.restore_checkpoint(path,storage_dir=self.directory())
        self.assertEqual(before,(file_digest(path),file_digest(path.with_suffix('.sqlite'))))
        original['state']['execution_source_sha256']='e'*64
        path.write_bytes(canonical(original))
        with self.assertRaisesRegex(ValueError,'source differs'):
            verify_snapshot(path,expected_source_sha256=old_source)

    def test_unknown_inconsistent_and_removed_codec_markers_rejected(self):
        model=self.model(self.config()).run(2);path=self.root/'marker.json';model.write_checkpoint(path)
        saved=json.loads(path.read_text())
        changes=({'state_hash_codec':'unknown'},{'checkpoint_format_version':3},
                 {'checkpoint_format_version':True},{'state_hash_codec':LEGACY_STATE_HASH_CODEC})
        for change in changes:
            changed=copy.deepcopy(saved);changed.update(change);path.write_bytes(canonical(changed))
            with self.assertRaisesRegex(ValueError,'format/hash codec'):verify_snapshot(path)
        changed=copy.deepcopy(saved);changed.pop('state_hash_codec');changed.pop('checkpoint_format_version')
        path.write_bytes(canonical(changed))
        with self.assertRaisesRegex(ValueError,'format/hash codec'):verify_snapshot(path)
        changed=copy.deepcopy(saved);changed['state'].pop('state_hash_codec');path.write_bytes(canonical(changed))
        with self.assertRaisesRegex(ValueError,'format/hash codec'):verify_snapshot(path)

    def test_verifier_import_drift_cannot_relabel_recovery_receipt(self):
        model=self.model(self.config()).run(2);path=self.root/'drift.json';model.write_checkpoint(path)
        destination=self.root/'drift-receipt.json';original=source_hash()
        with patch('dams_sim.longitudinal_model.source_hash',return_value='d'*64):
            with self.assertRaisesRegex(ValueError,'verifier source changed after import'):
                write_recovery_receipt(path,destination,expected_source_sha256=original)
        self.assertFalse(destination.exists())

    def test_integer_alias_duplicate_json_and_digest_corruption_rejected(self):
        model=self.model(self.config()).run(2);path=self.root/'bad.json';model.write_checkpoint(path)
        saved=json.loads(path.read_text())
        for alias in ('01','+1','-0'):
            changed=copy.deepcopy(saved);changed['state']['slots'][alias]=changed['state']['slots'].pop('1')
            changed['state_semantic_sha256']=self.full_hash(changed['state'],changed['ledger']['semantic_sha256'],STATE_HASH_CODEC)
            path.write_bytes(canonical(changed))
            with self.assertRaisesRegex(ValueError,'not canonical'):verify_snapshot(path)
        path.write_text('{"state":{},"state":{}}')
        with self.assertRaisesRegex(ValueError,'duplicate'):verify_snapshot(path)
        changed=copy.deepcopy(saved);changed['state']['cash']+=1;path.write_bytes(canonical(changed))
        with self.assertRaisesRegex(ValueError,'full state digest mismatch'):verify_snapshot(path)
        path.write_bytes(canonical(saved));path.with_suffix('.sqlite').write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError,'integrity mismatch'):verify_snapshot(path)


if __name__=='__main__':unittest.main()
