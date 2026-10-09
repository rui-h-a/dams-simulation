"""Tiny transport/actual legacy checkpoint controls; no formal scale claim."""
import dataclasses
from datetime import datetime,timezone,timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import unittest
from unittest.mock import patch

from dams_sim.config import Config
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.cli import run_world
from dams_sim.longitudinal_model import verify_snapshot
from dams_sim.model import Model
from dams_sim.storage import canonical,digest,source_hash,file_digest
from dams_sim.spec import case_key
from dams_sim.transfer_spool import TransferSpool,SCHEMA,_codec


class TransferSpoolTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(os.environ['DAMS_TRANSFER_TEST_DIR'])/self._testMethodName
        self.root.mkdir(parents=True,exist_ok=False)
        self.source=source_hash();self.models=[]
        self.no_git=patch('dams_sim.storage.subprocess.check_output',side_effect=OSError('no Git invocation in owned controls'))
        self.no_git.start()
        original=Model.__init__
        def tracked(model,*args,**kwargs):
            original(model,*args,**kwargs);self.models.append(model)
        self.model_init=patch.object(Model,'__init__',new=tracked);self.model_init.start()
        self.config_file=self.root/'transfer-config.json'
        self.values={'stage_id':'tiny-transfer-r1','spool_dir':str(self.root/'spool'),
            'ack_dir':str(self.root/'acks'),'expected_source_sha256':self.source,
            'max_spool_bytes':64*1024**2,'max_transfer_bytes':64*1024**2,
            'deadline_utc':(datetime.now(timezone.utc)+timedelta(minutes=3)).isoformat()}
        self.write_config()

    def tearDown(self):
        for model in self.models:model.ledger.close()
        self.model_init.stop()
        self.no_git.stop()

    def write_config(self):self.config_file.write_bytes(canonical(self.values)+b'\n')

    def spool(self,**kwargs):return TransferSpool(self.config_file,expected_source_sha256=self.source,**kwargs)

    def fixture(self):
        attempt=self.root/'case'/'attempt-000';attempt.mkdir(parents=True)
        config=self.tiny_config();model=Model(config,storage_dir=attempt);self.models.append(model);model.run(3)
        item=model.write_checkpoint(attempt/'checkpoint-day-000003.json')
        metadata={'source_sha256':self.source,'config':config.to_dict(),
                  'config_sha256':digest(canonical(config.to_dict())),'scientific_case_id':case_key(config)}
        index={'source_sha256':self.source,'config_sha256':metadata['config_sha256'],'snapshots':[item]}
        (attempt/'checkpoint-index.json').write_bytes(canonical(index)+b'\n')
        return attempt,item,index,metadata

    def published(self):
        spool=self.spool();args=self.fixture();seal=spool.publish_checkpoint(*args)
        group=Path(self.values['spool_dir'])/'groups'/seal['generation']
        return spool,args,seal,group

    def restore(self,group,destination):
        manifest=json.loads((group/'manifest.json').read_text());destination.mkdir()
        for part in manifest['files']:
            def fetch(chunk,path):
                encoded=group/part['part_directory']/f'{chunk["offset"]:020d}.z'
                shutil.copyfile(encoded,path)
            _codec().restore_file(part['codec'],fetch,destination/part['name'],
                                 max_raw_bytes=64*1024**2,minimum_free_bytes=8192)
        return manifest

    def ack(self,group,*,gate='checkpoint-full-state'):
        seal=json.loads((group/'seal.json').read_text());manifest=json.loads((group/'manifest.json').read_text())
        copies=[]
        for number in (1,2):
            destination=self.root/f'restored-{number}'
            if manifest['kind']=='final':
                collection=destination;destination=collection/'cases'/manifest['case_id']/manifest['attempt']
                destination.parent.mkdir(parents=True)
            self.restore(group,destination)
            if manifest['kind']=='checkpoint':
                envelope,_=verify_snapshot(destination/manifest['sealed']['item']['file'],expected_source_sha256=self.source)
                self.assertEqual(envelope['state_semantic_sha256'],manifest['sealed']['item']['state_semantic_sha256'])
            else:
                from research_tools.validate_longitudinal import CheckedLongitudinalCases
                from dams_sim.longitudinal_pipeline import driver_hash
                identity={'source_sha256':self.source,'pipeline_driver_sha256':driver_hash(),'spec_sha256':'f'*64}
                checked=CheckedLongitudinalCases(collection,identity)
                checked.validate_case({'case_id':manifest['case_id'],'config':manifest['sealed']['manifest']['config'],
                                      'tags':{'role':'strategy','parent_case_id':None}},destination)
                self.assertEqual(checked.result()['unique_complete_cases'],1)
                self.assertFalse(checked.result()['full_study_gate'])
            receipt={'restoration_id':f'owned-durable-copy-{number}','gate':gate,
                     'group_sha256':seal['group_sha256'],'files':{
                         p['name']:{'bytes':(destination/p['name']).stat().st_size,'sha256':file_digest(destination/p['name'])}
                         for p in manifest['files']}}
            receipt_path=self.root/f'copy-{number}-receipt.json';receipt_path.write_bytes(canonical(receipt)+b'\n')
            copies.append({k:receipt[k] for k in ('restoration_id','gate','group_sha256')}|
                          {'receipt_sha256':file_digest(receipt_path)})
        value={'schema':SCHEMA,'stage_id':self.values['stage_id'],'source_sha256':self.source,
               'generation':seal['generation'],'manifest_sha256':seal['manifest_sha256'],
               'group_sha256':seal['group_sha256'],'gate':gate,'copies':copies}
        path=Path(self.values['ack_dir'])/(seal['generation']+'.json');path.write_bytes(canonical(value)+b'\n')
        return path,value

    def test_real_codec_two_restores_preserve_all_original_filenames_bytes(self):
        spool,args,seal,group=self.published()
        for number in (1,2):
            restored=self.root/f'copy-{number}';manifest=self.restore(group,restored)
            self.assertEqual({p.name for p in restored.iterdir()},{p['name'] for p in manifest['files']})
            for part in manifest['files']:
                self.assertEqual((restored/part['name']).read_bytes(),(args[0]/part['name']).read_bytes())
        self.assertEqual(file_digest(group/'manifest.json'),seal['manifest_sha256'])

    def test_unACKed_spool_survives_resume_and_cumulative_reservation_unchanged(self):
        spool,args,seal,group=self.published();reservation=spool._history();reserved=spool.reserved
        resumed=self.spool();self.assertEqual(resumed.reserved,reserved)
        self.assertTrue((group/'parts').is_dir());self.assertEqual(resumed._history(),reservation)
        self.assertEqual(resumed.publish_checkpoint(*args),seal)
        self.assertEqual(len(resumed._history()),1)

    def test_exact_two_copy_ACK_removes_only_encoded_parts_retains_audit_and_source(self):
        spool,args,seal,group=self.published();reserved=spool._history();charged=spool.reserved
        self.ack(group);resumed=self.spool()
        self.assertFalse((group/'parts').exists())
        self.assertEqual({p.name for p in group.iterdir()},{'manifest.json','seal.json','ACK.json'})
        self.assertEqual(resumed.reserved,charged);self.assertEqual(resumed._history(),reserved)
        self.assertTrue((args[0]/args[1]['file']).exists())
        self.assertEqual(resumed.publish_checkpoint(*args),seal)

    def test_ACK_one_copy_duplicate_copy_wrong_gate_or_manifest_cannot_delete(self):
        spool,args,seal,group=self.published();path,valid=self.ack(group)
        for mutation in ('one','same','gate','manifest','receipt'):
            value=json.loads(canonical(valid))
            if mutation=='one':value['copies']=value['copies'][:1]
            elif mutation=='same':value['copies'][1]=value['copies'][0]
            elif mutation=='gate':value['gate']='final-full-raw'
            elif mutation=='manifest':value['manifest_sha256']='0'*64
            else:value['copies'][1]['receipt_sha256']=value['copies'][0]['receipt_sha256']
            path.write_bytes(canonical(value))
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.spool()
            self.assertTrue((group/'parts').is_dir());self.assertFalse((group/'ACK.json').exists())

    def test_corrupt_encoded_actual_bytes_with_valid_ACK_refuses_and_keeps_evidence(self):
        spool,args,seal,group=self.published();self.ack(group)
        part=next((group/'parts').rglob('*.z'));raw=bytearray(part.read_bytes());raw[0]^=1;part.write_bytes(raw)
        with self.assertRaisesRegex(Exception,'checksum'):self.spool()
        self.assertTrue(part.exists());self.assertFalse((group/'ACK.json').exists())

    def test_corrupt_sealed_manifest_hash_refuses_ACK(self):
        spool,args,seal,group=self.published();self.ack(group)
        (group/'manifest.json').write_bytes((group/'manifest.json').read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'sealed manifest'):self.spool()
        self.assertTrue((group/'parts').is_dir())

    def test_foreign_file_in_ACKed_generation_is_never_removed(self):
        spool,args,seal,group=self.published();self.ack(group);foreign=group/'parts'/'00000'/'foreign.dat';foreign.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError,'roster'):self.spool()
        self.assertEqual(foreign.read_bytes(),b'keep')

    def test_spool_bytes_and_worst_case_scratch_are_prospectively_bounded(self):
        self.values['max_spool_bytes']=4096;self.write_config();spool=self.spool();args=self.fixture()
        with self.assertRaisesRegex(RuntimeError,'spool capacity'):spool.publish_checkpoint(*args)
        self.assertEqual(list((spool.root/'reservations').iterdir()),[])

    def test_cumulative_transfer_never_refunds_after_ACK(self):
        spool,args,seal,group=self.published();spool._history();charged=spool.reserved;self.ack(group);self.spool()
        # Read-only cumulative assertion, then independent new capped namespace.
        self.assertEqual(self.spool().reserved,charged)
        self.values['spool_dir']=str(self.root/'capped');self.values['ack_dir']=str(self.root/'capped-acks')
        self.values['max_transfer_bytes']=1;self.write_config();capped=self.spool()
        with self.assertRaisesRegex(RuntimeError,'cumulative allowance'):capped.publish_checkpoint(*args)
        self.assertEqual(len(capped._history()),0)

    def test_missing_binding_or_reservation_gap_cannot_reset_counters(self):
        spool,args,seal,group=self.published()
        (spool.root/'binding.json').unlink()
        with self.assertRaisesRegex(ValueError,'binding missing'):self.spool()
        self.assertTrue((group/'parts').is_dir())

    def test_config_cap_edit_is_not_resume(self):
        self.published();self.values['max_transfer_bytes']+=1;self.write_config()
        with self.assertRaisesRegex(ValueError,'binding changed'):self.spool()

    def test_missing_reservation_tail_and_group_cannot_reset_high_water(self):
        spool,args,seal,group=self.published()
        shutil.rmtree(group)
        next((spool.root/'reservations').iterdir()).unlink()
        with self.assertRaisesRegex(ValueError,'history rolled back'):self.spool()

    def test_symlink_ACK_refuses_without_following_target(self):
        spool,args,seal,group=self.published();path,value=self.ack(group)
        target=self.root/'ack-target.json';path.rename(target);path.symlink_to(target)
        with self.assertRaisesRegex(Exception,'symlink'):self.spool()
        self.assertTrue((group/'parts').is_dir())

    def test_same_generation_changed_actual_expected_file_is_not_republished(self):
        spool,args,seal,group=self.published()
        args[1]['files'][0]['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'prior source bytes'):spool.publish_checkpoint(*args)
        self.assertEqual(len(spool._history()),1)

    def test_same_generation_hash_only_spoof_cannot_skip_actual_source_recheck(self):
        spool,args,seal,group=self.published()
        path=args[0]/args[1]['file'];path.write_bytes(b'Y'*path.stat().st_size)
        with self.assertRaisesRegex(ValueError,'actual source bytes'):spool.publish_checkpoint(*args)
        self.assertEqual(len(spool._history()),1)

    def test_duplicate_config_fields_are_rejected(self):
        raw=canonical(self.values).decode();raw=raw[:-1]+',"max_spool_bytes":1}'
        self.config_file.write_text(raw)
        with self.assertRaisesRegex(ValueError,'duplicate'):self.spool()

    def test_cached_foreign_codec_module_is_not_used(self):
        import sys,types
        foreign=types.ModuleType('cloud_archive');foreign.__file__='/foreign/cloud_archive.py'
        foreign.encode_file=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('foreign codec executed'))
        with patch.dict(sys.modules,{'cloud_archive':foreign,'cloud_control':types.ModuleType('foreign-control')}):
            spool,args,seal,group=self.published()
        self.assertTrue((group/'seal.json').is_file())

    def test_expired_and_whole_attempt_deadlines_refuse_before_publication(self):
        self.values['deadline_utc']='2000-01-01T00:00:00Z';self.write_config()
        with self.assertRaisesRegex(TimeoutError,'original absolute'):self.spool()

    def test_monotonic_deadline_refuses_even_if_private_UTC_future(self):
        with self.assertRaisesRegex(TimeoutError,'attempt deadline'):self.spool(deadline_monotonic=time.monotonic()-1)

    def test_config_bool_limits_unknown_fields_and_nonUTC_refused(self):
        original=dict(self.values)
        for key,value in (('max_spool_bytes',True),('deadline_utc','2030-01-01T00:00:00'),('unexpected',1)):
            self.values=dict(original);self.values[key]=value;self.write_config()
            with self.subTest(key=key),self.assertRaises(ValueError):self.spool()

    def test_source_file_same_size_hash_spoof_is_refused_before_seal(self):
        spool=self.spool();args=self.fixture();file=args[0]/args[1]['file'];file.write_bytes(b'X'*file.stat().st_size)
        with self.assertRaisesRegex(ValueError,'actual source bytes'):spool.publish_checkpoint(*args)
        self.assertEqual(len(spool._history()),1)
        group=next((spool.root/'groups').iterdir());self.assertFalse((group/'seal.json').exists())
        with self.assertRaisesRegex(RuntimeError,'incomplete; no fresh retry'):spool.publish_checkpoint(*args)

    def test_source_group_mutation_during_codec_is_refused(self):
        spool=self.spool();args=self.fixture();original=_codec().encode_file;calls=0
        def mutating(*a,**kw):
            nonlocal calls
            result=original(*a,**kw);calls+=1
            if calls==1:(args[0]/args[1]['file']).write_bytes(b'changed')
            return result
        with patch.object(_codec(),'encode_file',side_effect=mutating):
            with self.assertRaisesRegex(ValueError,'source group changed'):spool.publish_checkpoint(*args)
        self.assertFalse((next((spool.root/'groups').iterdir())/'seal.json').exists())

    def test_symlink_source_and_symlink_ACK_refused(self):
        spool=self.spool();args=self.fixture();file=args[0]/args[1]['file'];target=self.root/'original';file.rename(target);file.symlink_to(target)
        with self.assertRaisesRegex(Exception,'symlink'):spool.publish_checkpoint(*args)

    def test_native_JSON_only_checkpoint_refuses(self):
        spool=self.spool();args=self.fixture();args[1]['files']=args[1]['files'][:1]
        with self.assertRaisesRegex(ValueError,'native CAS unsupported'):spool.publish_checkpoint(*args)

    def tiny_config(self):
        return Config(n=8,days=5,guilds=2,sites=2,team_size=2,trace_every_days=1,max_output_mb=30,
            max_rss_mb=512,longitudinal=LongitudinalConfig(adoption_mode='never',annual_exit_probability=0.,
            annual_vacancy_fill_probability=0.,world_context='tiny-transfer-engineering-r1')).validate()

    def test_real_cli_periodic_before_pruning_full_restore_and_final_raw_group(self):
        path=self.root/'actual';path.mkdir()
        config=self.tiny_config()
        from dams_sim.longitudinal_pipeline import driver_hash
        with patch.dict(os.environ,{'DAMS_TRANSFER_CONFIG':str(self.config_file)}):
            result=run_world(config,path,checkpoint_interval_days=1,metadata_extra={
                'pipeline_driver_sha256':driver_hash(),'scientific_case_id':case_key(config)})
        self.assertEqual(result['status'],'complete')
        groups=list((Path(self.values['spool_dir'])/'groups').iterdir());self.assertEqual(len(groups),6)
        first=next(p for p in groups if json.loads((p/'manifest.json').read_text())['kind']=='checkpoint'
                   and json.loads((p/'manifest.json').read_text())['day']==1)
        restored=self.root/'old-pruned-restored';manifest=self.restore(first,restored)
        self.assertFalse((path/'checkpoint-day-000001.json').exists())
        envelope,database=verify_snapshot(restored/'checkpoint-day-000001.json',expected_config=config)
        self.assertEqual(envelope['state']['day'],1)
        resumed=Model.restore_checkpoint(restored/'checkpoint-day-000001.json',storage_dir=self.root/'resumed',expected_config=config)
        self.models.append(resumed);resumed.run()
        final=next(p for p in groups if json.loads((p/'manifest.json').read_text())['kind']=='final')
        final_path=self.root/'final-restored';sealed=self.restore(final,final_path)
        full,_=verify_snapshot(final_path/'final_state.json',expected_config=config)
        self.assertEqual(resumed.semantic_digest(),full['state_semantic_sha256'])
        self.assertEqual({p['name'] for p in sealed['files']},{str(p.relative_to(path)) for p in path.rglob('*') if p.is_file()})
        for part in sealed['files']:self.assertEqual(file_digest(path/part['name']),file_digest(final_path/part['name']))
        self.assertIn('.longitudinal-working.sqlite',{p['name'] for p in sealed['files']})
        self.ack(final,gate='final-full-raw');self.spool()
        self.assertFalse((final/'parts').exists())

    def test_cli_env_absent_never_constructs_transport(self):
        path=self.root/'default';path.mkdir()
        with patch.dict(os.environ,{},clear=True),patch('dams_sim.transfer_spool.TransferSpool.from_environment',side_effect=AssertionError('default called transfer')):
            result=run_world(self.tiny_config(),path,checkpoint_interval_days=2)
        self.assertEqual(result['status'],'complete');self.assertFalse(Path(self.values['spool_dir']).exists())

    def test_cli_native_transfer_refuses_before_any_Model(self):
        path=self.root/'native';path.mkdir()
        with patch.dict(os.environ,{'DAMS_TRANSFER_CONFIG':str(self.config_file)}),patch('dams_sim.cli.Model',side_effect=AssertionError('constructed native Model')):
            with self.assertRaisesRegex(ValueError,'native CAS'):run_world(self.tiny_config(),path,page_options=object())
        self.assertFalse((path/'manifest.json').exists())

    def test_real_cli_transport_failure_precedes_old_CP_pruning_and_keeps_groups(self):
        path=self.root/'interrupted';path.mkdir();original=TransferSpool.publish_checkpoint
        def bounded(spool,attempt,item,index,metadata,**kw):
            if item['day']==3:
                self.assertTrue((attempt/'checkpoint-day-000001.json').exists())
                raise RuntimeError('owned transfer bound exhausted')
            return original(spool,attempt,item,index,metadata,**kw)
        with patch.dict(os.environ,{'DAMS_TRANSFER_CONFIG':str(self.config_file)}),patch.object(TransferSpool,'publish_checkpoint',new=bounded):
            with self.assertRaisesRegex(RuntimeError,'owned transfer bound'):
                run_world(self.tiny_config(),path,checkpoint_interval_days=1)
        self.assertEqual(json.loads((path/'manifest.json').read_text())['status'],'failed')
        for day in (1,2,3):self.assertTrue((path/f'checkpoint-day-{day:06d}.json').exists())
        groups=list((Path(self.values['spool_dir'])/'groups').iterdir())
        self.assertEqual(len(groups),2)
        self.assertTrue(all((group/'seal.json').is_file() for group in groups))


if __name__=='__main__':unittest.main()
