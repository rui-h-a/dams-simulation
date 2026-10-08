"""Tiny bounded tests for own-write checkpoint reuse and boundary stopping."""
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dams_sim.cli import run_world, _checkpoint_identity, _verify_owned_checkpoint_bytes
from dams_sim.config import Config
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.longitudinal_model import verify_snapshot
from dams_sim.longitudinal_storage import hash_state_json, STATE_HASH_CODEC
from dams_sim.model import Model
from dams_sim.storage import canonical, file_digest, source_hash


class CheckpointStopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.models=[];self.counter=0

    def tearDown(self):
        for model in self.models:
            model.ledger.rollback();model.ledger.close()
        self.temp.cleanup()

    def model(self,days=2):
        self.counter+=1;path=self.root/str(self.counter);path.mkdir()
        config=Config(n=8,days=days,guilds=2,team_size=2,trace_every_days=1,max_output_mb=5,
                      longitudinal=LongitudinalConfig(adoption_mode='never',annual_exit_probability=0.,
                                                     annual_vacancy_fill_probability=0.)).validate()
        model=Model(config,storage_dir=path);self.models.append(model)
        return model,path

    def run_case(self,model,path,**kwargs):
        return run_world(model.config,path,restored=model,checkpoint_interval_days=1,
                         checkpoint_interval_seconds=10**9,**kwargs)

    def test_fresh_writer_reuses_one_ledger_digest_without_changing_full_hash_codec(self):
        model,path=self.model();model.run(1)
        h=hashlib.sha256();hash_state_json(h,model._long.header(),STATE_HASH_CODEC)
        for agent in model.agents:h.update(b'\n');h.update(canonical(dataclasses.asdict(agent)))
        h.update(b'\n');h.update(model.ledger.semantic_digest().encode());reference=h.hexdigest()
        with patch.object(model.ledger,'semantic_digest',wraps=model.ledger.semantic_digest) as ledger_digest:
            item=model.write_checkpoint(path/'checkpoint-day-000001.json')
        self.assertEqual(ledger_digest.call_count,1)
        envelope,_=verify_snapshot(path/item['file'],expected_config=model.config)
        self.assertEqual(item['state_semantic_sha256'],reference)
        self.assertEqual(envelope['state_semantic_sha256'],reference)
        self.assertEqual(envelope['state_hash_codec'],STATE_HASH_CODEC)

    def test_cached_and_unknown_same_state_differential_with_actual_group_bytes(self):
        cached,cached_path=self.model()
        with patch('dams_sim.longitudinal_model.verify_snapshot',wraps=verify_snapshot) as deep, \
             patch.object(cached.ledger,'semantic_digest',wraps=cached.ledger.semantic_digest) as ledger_digest:
            result=self.run_case(cached,cached_path)
        self.assertEqual(result['status'],'complete');deep.assert_not_called()
        # Two fresh periodic CPs, one live same-day check, and one final-state writer.
        self.assertEqual(ledger_digest.call_count,4)
        unknown,unknown_path=self.model();unknown.run()
        unknown.write_checkpoint(unknown_path/'checkpoint-day-000002.json')
        with patch('dams_sim.longitudinal_model.verify_snapshot',wraps=verify_snapshot) as deep:
            self.run_case(unknown,unknown_path)
        deep.assert_called_once_with(unknown_path/'checkpoint-day-000002.json',expected_config=unknown.config)
        self.assertEqual(canonical(cached.state()),canonical(unknown.state()))
        self.assertEqual(canonical(cached.summary()),canonical(unknown.summary()))
        for name in ('checkpoint-day-000002.json','checkpoint-day-000002.sqlite','final_state.json','final_state.sqlite'):
            self.assertEqual((cached_path/name).read_bytes(),(unknown_path/name).read_bytes())

    def test_owned_same_day_json_sqlite_delete_and_symlink_tampering_reject(self):
        for kind in ('json','sqlite','delete','symlink'):
            model,path=self.model()
            def stop():
                if model.day!=1:return False
                checkpoint=path/'checkpoint-day-000001.json'
                if kind in ('json','sqlite'):
                    target=checkpoint if kind=='json' else checkpoint.with_suffix('.sqlite')
                    info=target.stat();data=bytearray(target.read_bytes());data[-1]^=1;target.write_bytes(data)
                    os.utime(target,ns=(info.st_atime_ns,info.st_mtime_ns))
                elif kind=='delete':
                    checkpoint.unlink();checkpoint.with_suffix('.sqlite').unlink()
                else:
                    retained=path/'retained.json';checkpoint.rename(retained);checkpoint.symlink_to(retained)
                return True
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'same-day checkpoint'):
                self.run_case(model,path,stop_requested=stop)
            self.assertEqual(json.loads((path/'manifest.json').read_text())['status'],'failed')

    def test_same_day_boundary_stop_reuses_own_group_and_retains_verified_checkpoint(self):
        model,path=self.model()
        with patch('dams_sim.longitudinal_model.verify_snapshot',wraps=verify_snapshot) as deep, \
             patch.object(model.ledger,'semantic_digest',wraps=model.ledger.semantic_digest) as ledger_digest:
            with self.assertRaisesRegex(InterruptedError,'completed-day boundary'):
                self.run_case(model,path,stop_requested=lambda:model.day==1)
        deep.assert_not_called();self.assertEqual(ledger_digest.call_count,2)
        index=json.loads((path/'checkpoint-index.json').read_text())
        self.assertEqual(len(index['snapshots']),1)
        self.assertEqual(index['snapshots'][0]['day'],1)
        envelope,_=verify_snapshot(path/index['snapshots'][0]['file'],expected_config=model.config)
        self.assertEqual(envelope['state_semantic_sha256'],model.semantic_digest())
        self.assertEqual(json.loads((path/'manifest.json').read_text())['error_type'],'InterruptedError')
        self.assertFalse((path/'final_state.json').exists())

    def test_actual_sha_is_checked_even_when_identity_probe_is_forged_unchanged(self):
        model,path=self.model();model.run(1);item=model.write_checkpoint(path/'checkpoint-day-000001.json')
        identities={part['file']:_checkpoint_identity(path/part['file']) for part in item['files']}
        target=path/item['file'];data=bytearray(target.read_bytes());data[-1]^=1;target.write_bytes(data)
        with patch('dams_sim.cli._checkpoint_identity',side_effect=lambda p:identities[p.name]):
            with self.assertRaisesRegex(ValueError,'bytes changed'):
                _verify_owned_checkpoint_bytes(path,item,identities)

    def test_cached_header_and_ledger_changes_reject_after_real_byte_verification(self):
        for kind in ('header','ledger'):
            model,path=self.model()
            def stop():
                if model.day!=1:return False
                if kind=='header':model._long.cash+=1
                else:
                    model.ledger.add_journal(0,'test','extra-event',{'changed':True});model.ledger.commit()
                return True
            with self.subTest(kind=kind),patch('dams_sim.longitudinal_model.verify_snapshot',wraps=verify_snapshot) as deep:
                with self.assertRaisesRegex(ValueError,'differs from current complete state'):
                    self.run_case(model,path,stop_requested=stop)
                deep.assert_not_called()

    def test_unknown_checkpoint_deep_verification_and_noncached_state_change_reject(self):
        model,path=self.model(days=1);model.run();model.write_checkpoint(path/'checkpoint-day-000001.json')
        model._long.cash+=1
        with patch('dams_sim.longitudinal_model.verify_snapshot',wraps=verify_snapshot) as deep:
            with self.assertRaisesRegex(ValueError,'differs from current complete state'):
                self.run_case(model,path)
        deep.assert_called_once_with(path/'checkpoint-day-000001.json',expected_config=model.config)

    def test_cached_source_and_config_guards_reject_without_deep_fallback(self):
        original=source_hash()
        for kind in ('source','config'):
            model,path=self.model()
            def stop():
                if model.day!=1:return False
                if kind=='config':object.__setattr__(model.config,'alpha',.9)
                return True
            def current_source():return 'd'*64 if kind=='source' and model.day==1 else original
            with self.subTest(kind=kind),patch('dams_sim.cli.source_hash',side_effect=current_source), \
                 patch('dams_sim.longitudinal_model.verify_snapshot',wraps=verify_snapshot) as deep:
                with self.assertRaisesRegex(ValueError,'frozen source/config differs'):
                    self.run_case(model,path,stop_requested=stop)
                deep.assert_not_called()

    def test_failed_day_or_open_transaction_cannot_reuse_an_owned_checkpoint(self):
        for kind in ('failed','transaction'):
            model,path=self.model()
            def stop():
                if model.day!=1:return False
                if kind=='failed':model._long.failed_day=True
                else:model.ledger.begin()
                return True
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'completed day boundary'):
                self.run_case(model,path,stop_requested=stop)
        with patch.object(model.ledger,'semantic_digest',wraps=model.ledger.semantic_digest) as ledger_digest:
            model._long.failed_day=True
            with self.assertRaisesRegex(ValueError,'partially failed day'):model.semantic_digest()
            ledger_digest.assert_not_called()


if __name__=='__main__':unittest.main()
