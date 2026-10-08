import hashlib
import json
from pathlib import Path
import sys
import tempfile
import os
import signal
import subprocess
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'research_tools'))
from cloud_control import GuardError
from cloud_worker import snapshot, download_snapshot, Store


class FakeStore:
    def __init__(self): self.files={};self.calls=0;self.fail=False;self.corrupt=False
    def put_file(self,key,path,sha,immutable=True):
        self.calls+=1
        if self.fail: raise OSError('simulated interrupted upload')
        data=Path(path).read_bytes()
        if key in self.files and immutable and self.files[key]!=data: raise GuardError('immutable mismatch')
        self.files[key]=data
    def put_json(self,key,data,immutable=True): self.files[key]=json.dumps(data).encode()
    def get_json(self,key): return json.loads(self.files[key]) if key in self.files else None
    def download(self,key,path):
        data=self.files[key]+(b'corruption' if self.corrupt else b'');Path(path).write_bytes(data)
        return hashlib.sha256(data).hexdigest()


class CloudWorkerTests(unittest.TestCase):
    def test_restore_upload_race_cannot_replace_complete_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();store=FakeStore()
            (source/'a').write_bytes(b'a');(source/'b').write_bytes(b'b')
            sid=snapshot(source,store,root/'first-ledger.json',10000);before=store.files['latest.json']
            original=store.download
            def concurrent_download(key,path):
                result=original(key,path)
                with self.assertRaises(BlockingIOError):snapshot(root/'restored',store,root/'second-ledger.json',10000)
                self.assertEqual(store.files['latest.json'],before)
                return result
            store.download=concurrent_download
            download_snapshot(store,sid,root/'restored')
            self.assertEqual((root/'restored/b').read_bytes(),b'b')
            empty=root/'empty';empty.mkdir()
            with self.assertRaises(GuardError):snapshot(empty,store,root/'empty-ledger.json',10000)

    def test_metadata_and_ambiguous_uploaded_bytes_are_reserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();(source/'a').write_bytes(b'a');store=FakeStore()
            with self.assertRaises(GuardError):snapshot(source,store,root/'ledger.json',1)
            self.assertNotIn('latest.json',store.files)
            sid=snapshot(source,store,root/'ledger.json',10000)
            state=json.loads((root/'ledger.json').read_text())
            self.assertGreater(state['bytes'],1);self.assertTrue(state['metadata_sizes'])
            self.assertGreaterEqual(state['bytes'],sum(len(v) for v in store.files.values()))
    def test_cumulative_request_and_download_allowances_survive_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);requests=root/'requests.json';transfer=root/'transfer.json'
            store=Store('fixture','prefix',request_state=requests,max_requests=2,transfer_state=transfer,max_transfer_bytes=10)
            store.charge_request();store.charge_transfer(7)
            resumed=Store('fixture','prefix',request_state=requests,max_requests=2,transfer_state=transfer,max_transfer_bytes=10)
            resumed.charge_request()
            with self.assertRaises(GuardError):resumed.charge_request()
            with self.assertRaises(GuardError):resumed.charge_transfer(4)
            self.assertEqual(json.loads(requests.read_text())['requests_upper'],2)
            self.assertEqual(json.loads(transfer.read_text())['bytes_upper'],7)

    def test_local_store_actual_model_sigkill_checkpoint_restore_bytes(self):
        from dams_sim.config import Config
        from dams_sim.model import Model
        from dams_sim.scheduler import restore_checkpoint
        from dams_sim.storage import canonical
        from cloud_control import Ledger
        from tests.test_cloud_control import fixture
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'guest-output';source.mkdir();config=Config(n=24,days=10,guilds=3)
            c=fixture();ledger=Ledger(root/'private-ledger.json',c);before=ledger.reserve(c['stages'][0])
            # Pause a genuine Model between completed days, after two immutable
            # checkpoints exist; SIGKILL bypasses cleanup and final manifests.
            code="""from pathlib import Path
from dams_sim.config import Config
from dams_sim.cli import run_world
import json,time
count=0
def stop():
 global count
 count+=1
 if count==4:
  Path(%r).write_text('ready')
  while True:time.sleep(.1)
 return False
run_world(Config.from_dict(json.loads(%r)),Path(%r),checkpoint_interval_days=1,stop_requested=stop)
""" % (str(root/'ready'),json.dumps(config.to_dict()),str(source))
            process=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                until=time.monotonic()+10
                while not (root/'ready').exists() and time.monotonic()<until and process.poll() is None:time.sleep(.01)
                self.assertTrue((root/'ready').exists())
                store=FakeStore();sid=snapshot(source,store,root/'upload-state.json',2_000_000)
                os.kill(process.pid,signal.SIGKILL);process.communicate(timeout=10)
                self.assertEqual(process.returncode,-signal.SIGKILL)
                descriptor=download_snapshot(store,sid,root/'replacement-output')
                restored,origin=restore_checkpoint(root/'replacement-output',config)
                self.assertIsNotNone(restored);self.assertEqual(restored.day,3)
                self.assertEqual(canonical(restored.run().state()),canonical(Model(config).run().state()))
                self.assertEqual(ledger.reserve(c['stages'][0]),before)
                self.assertNotIn('private-ledger.json',descriptor['files'])
            finally:
                if process.poll() is None:process.kill();process.communicate(timeout=10)

    def test_restore_preserves_stale_files_outside_exact_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();destination=root/'restored';destination.mkdir()
            (source/'current').write_bytes(b'new');(destination/'old-checkpoint').write_bytes(b'old')
            store=FakeStore();sid=snapshot(source,store,root/'state.json',1024)
            download_snapshot(store,sid,destination)
            self.assertFalse((destination/'old-checkpoint').exists())
            old=list((root/'restored-snapshot-history').rglob('old-checkpoint'))
            self.assertEqual(len(old),1);self.assertEqual(old[0].read_bytes(),b'old')

    def test_actual_snapshot_partial_retry_and_checksum_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'source';src.mkdir();(src/'final_state.json').write_text('{"day":30}\n')
            store=FakeStore();store.fail=True
            with self.assertRaises(OSError): snapshot(src,store,root/'state.json',1024)
            self.assertNotIn('latest.json',store.files)
            store.fail=False;sid=snapshot(src,store,root/'state.json',1024)
            calls=store.calls;snapshot(src,store,root/'state.json',1024);self.assertEqual(calls,store.calls)
            verified=download_snapshot(store,sid,root/'download')
            self.assertEqual(verified['files']['final_state.json']['bytes'],11)
            self.assertEqual((src/'final_state.json').read_bytes(),(root/'download/final_state.json').read_bytes())
            store.corrupt=True
            with self.assertRaises(GuardError): download_snapshot(store,sid,root/'bad')

    def test_checkpoint_pair_output_cap_and_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'source';src.mkdir();state=root/'state.json';store=FakeStore()
            (src/'checkpoint-day-000003.json').write_text('{"day":3}')
            (src/'checkpoint-index.json').write_text(json.dumps({'snapshots':[{'file':'checkpoint-day-000003.json','sha256':'0'*64}]}))
            with self.assertRaises(GuardError): snapshot(src,store,state,4096)
            self.assertNotIn('latest.json',store.files)
            sha=hashlib.sha256((src/'checkpoint-day-000003.json').read_bytes()).hexdigest()
            (src/'checkpoint-index.json').write_text(json.dumps({'snapshots':[{'file':'checkpoint-day-000003.json','sha256':sha}]}))
            snapshot(src,store,state,4096)
            (src/'huge').write_bytes(b'x'*10000)
            with self.assertRaises(GuardError): snapshot(src,store,state,4096)
            (src/'huge').unlink();(src/'linked').symlink_to(src/'checkpoint-index.json')
            with self.assertRaises(GuardError): snapshot(src,store,state,4096)


if __name__ == '__main__': unittest.main()
