"""Opaque native spool/codec/Collector I/O; scientific gate is fixture-only.
No Model, SDK/auth/network. Original test12 expectation remains untouched.
"""
import copy,json,os,shutil,unittest
from pathlib import Path
from unittest.mock import patch
from research_tools.compute_only_control import blob,parse
from dams_sim.storage import digest,canonical
from dams_sim.transfer_spool import TransferSpool
import test_compute_only_lifecycle as old
from research_tools import compute_only_remote as remote
ROOT=Path(__file__).absolute().parents[1]

def fixture_manifest(directory):
    m=parse((ROOT/'source-manifest.json').read_bytes())
    for n in m['source_files_sha256']:m['source_files_sha256'][n]=digest((ROOT/n).read_bytes())
    p=directory/'fixture-source-manifest.json';p.write_bytes(blob(m));return old.ref(p)

class PreservationTests(unittest.TestCase):
    def setUp(self):
        external=Path(os.environ['DAMS_PRESERVATION_TEST_DIR']);external.mkdir(exist_ok=True)
        mr=fixture_manifest(external);original=old.ref
        self.pin=patch.object(old,'ref',side_effect=lambda p:mr if Path(p)==ROOT/'source-manifest.json'else original(p));self.pin.start()
        self.f=old.LifecycleTests('test_01_no_original_hold_or_wrong_approval_no_provider');self.f._testMethodName=self._testMethodName;self.f.setUp()
        self.x=self.f.make();self.f.establish(self.x);self.f.runtime(self.x)
        self.work=self.f.work/'guest-work';self.work.mkdir();(self.work/'output').mkdir()
        self.transfer={**parse((self.f.work/'transfer.json').read_bytes()),'spool_dir':str(self.work/'spool'),'ack_dir':str(self.work/'acks'),'max_spool_bytes':64*1024**2,'max_transfer_bytes':64*1024**2}
        self.f.options['transfer']=old.save(self.f.work/'transfer.json',self.transfer)
        r=parse((self.f.work/'runtime.json').read_bytes());r['transfer_config_sha256']=digest(blob(self.transfer))
        self.f.options['runtime']=old.save(self.f.work/'runtime.json',r);self.x.raw=blob(self.f.options)
        (self.work/'transfer-config.json').write_bytes(blob(self.transfer));(self.work/'compute-runtime.json').write_bytes(blob(r))
        self.spool=TransferSpool(self.work/'transfer-config.json',expected_source_sha256=self.f.ad['source_sha256'])
        row=next(iter(self.x.collector.rows.values()));self.row=row;attempt=self.work/'output/cases'/row['case_id']/'attempt-000';attempt.mkdir(parents=True)
        raws={'checkpoint-day-000002.json':b'{}','checkpoint-day-000002.sqlite':b'OPAQUE-SQLITE-ONLY\x00'*16}
        for n,v in raws.items():(attempt/n).write_bytes(v)
        self.item={'file':'checkpoint-day-000002.json','sha256':digest(raws['checkpoint-day-000002.json']),'day':2,'state_semantic_sha256':'e'*64,
            'files':[{'file':n,'sha256':digest(v),'bytes':len(v)}for n,v in raws.items()]}
        index={'source_sha256':self.f.ad['source_sha256'],'config_sha256':digest(canonical(row['config'])),'snapshots':[self.item]}
        (attempt/'checkpoint-index.json').write_bytes(blob(index))
        meta={'source_sha256':self.f.ad['source_sha256'],'config':row['config'],'config_sha256':index['config_sha256'],'scientific_case_id':row['case_id']}
        seal=self.spool.publish_checkpoint(attempt,self.item,index,meta);self.group=self.work/'spool/groups'/seal['generation'];self.generation=seal['generation']
        self.m=parse((self.group/'manifest.json').read_bytes());self.output=self.work/'output'
        identity={'schema_version':3,**{k:self.f.ad[k]for k in ('source_sha256','pipeline_driver_sha256','spec_sha256')}}
        inv=[row];census={'schema':'DAMS-stopped-case-census-1','identity':identity,'inventory_sha256':digest(canonical(inv)),'expected_cases':1,'full_study_gate':False,
            'groups':{'complete':[],'valid-latest-checkpoint':[row['case_id']],'failed':[],'unstarted':[]},'remaining_case_ids':[row['case_id']],
            'unexpected_case_ids':[],'records':[{'case_id':row['case_id'],'classification':'valid-latest-checkpoint','attempt':'cases/'+row['case_id']+'/attempt-000',
                'checkpoint_day':2,'checkpoint_file':self.item['file'],'checkpoint_sha256':self.item['sha256'],'checkpoint_index_sha256':digest(blob(index))}]}
        stage=self.output/'precision-pilot';stage.mkdir();(stage/'case_inventory.json').write_bytes(blob(inv));(stage/'case_census.json').write_bytes(blob(census))
        self.stage_meta={**identity,'inventory_sha256':digest(canonical(inv)),'expected_rows':1,'status':'censored','exit_code':1,'partial_census_exact':True,'case_census_sha256':digest(blob(census))}
        (stage/'manifest.json').write_bytes(blob(self.stage_meta));(self.output/'opaque-interrupted-sidecar-journal').write_bytes(b'KEEP-FORENSIC-NOT-VALID-STATE')
        self.terminal={'runtime_sha256':digest(blob(r)),'owned_pipeline_group_verification':{'owned_pipeline_group_absent':True,'owned_pipeline_group_id':2147483647},'inputs_unchanged':True,'science_complete':False}
        (self.work/'compute-terminal.json').write_bytes(blob(self.terminal));self.runtime=r
        self.context={**self.x._context(),'source_root':str(ROOT),'work_root':str(self.work),'runtime_sha256':digest(blob(r))}
    def tearDown(self):self.f.tearDown();self.pin.stop()
    def native(self,action,raw=b'',bound=None):
        with patch.object(remote,'context'),patch.object(remote,'package',return_value=(ROOT,{'source_files_sha256':{'research_tools/compute_only_remote.py':self.context['remote_helper_sha256']}})):
            return remote.operation(action,self.context,raw)
    def accept_opaque(self):
        c=self.x.collector;mr=(self.group/'manifest.json').read_bytes();sr=(self.group/'seal.json').read_bytes()
        tickets=[c.reserve_metadata_attempt(len(v))for v in (mr,sr)]
        def fetch(part,ch):return (self.group/part['part_directory']/f'{ch["offset"]:020d}.z').open('rb')
        # Actual native fetch/restore/closed receipts/ACK; fake scientific gate is explicit.
        c.gate_details={'fixture_only_no_scientific_validation':True}
        with patch.object(c,'_gate',return_value='checkpoint-full-state'):result=c.collect(mr,sr,fetch,metadata_tickets=tickets)
        (self.work/'acks'/(self.generation+'.json')).write_bytes(result['ack_bytes'])
        with self.spool._locked():self.spool._history();self.spool._reconcile_acks()
    def closeout(self):
        with patch.object(self.x,'exchange',side_effect=self.native):return self.x.closeout()
    def test_01_native_sealed_dict_poll_and_unacked_closeout_refused(self):
        view=parse(self.native('poll'));self.assertEqual(view['reservation_generations'],[self.generation]);self.assertEqual(view['spool_high_water']['sequence'],1)
        self.assertEqual(view['sealed_generations'][0]['kind'],'checkpoint');self.assertFalse(view['sealed_generations'][0]['ack_present'])
        with self.assertRaisesRegex(ValueError,'ACK coverage'):self.closeout()
        self.assertTrue(self.f.sdk.exists);self.assertFalse(any('delete'in a for a in self.f.sdk.calls))
    def test_02_native_two_copy_receipts_census_then_normal_delete(self):
        self.accept_opaque();r=self.closeout();self.assertTrue(r['cleanup']['compute_and_disk_absence_verified']);self.assertFalse(r['science_complete'])
        self.assertEqual(r['census']['classifications'][self.row['case_id']],'valid-latest-checkpoint')
        self.assertFalse(self.f.sdk.exists);self.assertEqual(self.x.e['termination_utc'],self.f.e['termination_utc'])
        self.assertEqual((self.f.work/'terminal1/opaque-interrupted-sidecar-journal').read_bytes(),b'KEEP-FORENSIC-NOT-VALID-STATE')
    def test_03_preservation_failure_no_delete_no_hold_refund(self):
        self.accept_opaque()
        with patch.object(self.x,'exchange',side_effect=self.native),patch.object(self.x,'pull_terminal',side_effect=ValueError('exact preservation failure')):
            with self.assertRaisesRegex(ValueError,'preservation failure'):self.x.closeout()
        self.assertTrue(self.f.sdk.exists);self.assertFalse(any('delete'in a for a in self.f.sdk.calls))
        b=parse((self.f.work/'budget.json').read_bytes());self.assertEqual(b['entries'][self.f.s['id']]['reserved_usd'],self.f.e['reserved_usd']);self.assertEqual(b['deadline_utc'],self.f.c['global_deadline_utc'])
    def test_04_missing_reservation_group_not_empty_success(self):
        shutil.rmtree(self.group)
        view=parse(self.native('poll'));self.assertEqual(view['unsealed_generations'],[self.generation])
        with self.assertRaisesRegex(ValueError,'generation closure'):self.closeout()
        self.assertTrue(self.f.sdk.exists)
    def test_05_native_sealed_boolean_and_manifest_corruption_refuse(self):
        m=copy.deepcopy(self.m);m['sealed']=True;raw=blob(m);(self.group/'manifest.json').write_bytes(raw)
        seal=parse((self.group/'seal.json').read_bytes());seal.update(manifest_sha256=digest(raw),manifest_bytes=len(raw));(self.group/'seal.json').write_bytes(blob(seal))
        with self.assertRaisesRegex(ValueError,'sealed payload shape'):self.native('poll')
    def test_06_history_gap_or_counter_rollback_refuse(self):
        (self.work/'spool/accounting.json').write_bytes(blob({'schema':'dams-compute-only-transfer-v1','sequence':0,'previous_sha256':None,'reserved_bytes':0}))
        with self.assertRaisesRegex(ValueError,'accounting/prefix'):self.native('poll')
    def test_07_guest_local_ack_change_no_delete(self):
        self.accept_opaque();a=parse((self.group/'ACK.json').read_bytes());a['copies'][0]['receipt_sha256']='0'*64
        (self.group/'ACK.json').write_bytes(blob(a));(self.work/'acks'/(self.generation+'.json')).write_bytes(blob(a))
        with self.assertRaisesRegex(ValueError,'guest/local ACK'):self.closeout()
        self.assertTrue(self.f.sdk.exists)
    def test_08_second_receipt_or_copy_drift_no_delete(self):
        self.accept_opaque();p=self.x.collector.copies[1].path/self.generation/'cases'/self.row['case_id']/'attempt-000/checkpoint-day-000002.sqlite';p.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.closeout()
        self.assertTrue(self.f.sdk.exists)
    def test_09_census_latest_pointer_corrupt_no_delete(self):
        self.accept_opaque();p=self.output/'precision-pilot/case_census.json';c=parse(p.read_bytes());c['records'][0]['checkpoint_day']=1;p.write_bytes(blob(c))
        self.stage_meta['case_census_sha256']=digest(p.read_bytes());(self.output/'precision-pilot/manifest.json').write_bytes(blob(self.stage_meta))
        with self.assertRaisesRegex(ValueError,'latest checkpoint'):self.closeout()
        self.assertTrue(self.f.sdk.exists)
    def test_10_empty_census_or_assignment_partition_no_delete(self):
        self.accept_opaque();p=self.output/'precision-pilot/case_inventory.json';p.write_bytes(blob([]))
        with self.assertRaisesRegex(ValueError,'stage inventory'):self.closeout()
        self.assertTrue(self.f.sdk.exists)
    def test_11_terminal_non_census_raw_late_drift_refuses_before_delete(self):
        self.accept_opaque();original=self.x._preservation_census
        def mutate(m):result=original(m);(self.f.work/'terminal1/opaque-interrupted-sidecar-journal').write_bytes(b'latechange');return result
        with patch.object(self.x,'_preservation_census',side_effect=mutate),self.assertRaisesRegex(ValueError,'terminal bytes'):self.closeout()
        self.assertTrue(self.f.sdk.exists);self.assertFalse(any('delete'in a for a in self.f.sdk.calls))
    def test_12_final_sdk_predelete_receipt_drift_refuses(self):
        self.accept_opaque();native=self.f.sdk.run
        def mutate(args,**kw):
            result=native(args,**kw)
            if args[1:3]==['compute','instances'] and 'list'in args:
                p=self.x.collector.copies[0].path/self.generation/'receipt.json';p.write_bytes(p.read_bytes()+b' ')
            return result
        with patch.object(self.f.sdk,'run',side_effect=mutate),self.assertRaisesRegex(ValueError,'ACK/receipt drift'):self.closeout()
        self.assertTrue(self.f.sdk.exists);self.assertFalse(any('delete'in a for a in self.f.sdk.calls))
