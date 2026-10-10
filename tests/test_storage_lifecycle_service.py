import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

from research_tools.storage_lifecycle_service import ReleaseTarBackend, run_once, storage
GuardError = storage.GuardError


class ReplicaReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)

    def transport(self, members):
        payload = self.root / "transport.tar"
        with tarfile.open(payload, "w") as stream:
            for name, data, kind in members:
                info = tarfile.TarInfo(name); info.size = len(data); info.type = kind
                stream.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        data = payload.read_bytes()
        config = {"repo":"fixture/private","asset_id":1,"failure_domain":"fixture-remote",
                  "tar_sha256":hashlib.sha256(data).hexdigest(),"tar_bytes":len(data),"timeout_seconds":2}
        backend = ReleaseTarBackend(config, self.root / "cache", 0,
                                    command=[sys.executable,"-c","import sys;sys.stdout.buffer.write(open(sys.argv[1],'rb').read())",str(payload)])
        self.addCleanup(backend.close)
        return backend, payload

    def test_fresh_binary_read_is_required_and_cache_removed(self):
        raw = b"encoded-object"; key = hashlib.sha256(raw).hexdigest()
        b, transport = self.transport([("archive.json",b'{}',tarfile.REGTYPE),
                                       ("objects/"+key,raw,tarfile.REGTYPE)])
        self.assertEqual(b.read_manifest(),b'{}')
        self.assertEqual(b.read_object(key,len(raw)),raw)
        cache = b.path; b.close(); self.assertFalse(cache.exists())
        transport.write_bytes(b"different")
        with self.assertRaises(GuardError): b.read_manifest()
        self.assertFalse(any((self.root/"cache").iterdir()))

    def test_outside_member_and_symlink_are_refused(self):
        for members in [[("../evidence",b"x",tarfile.REGTYPE)],
                        [("archive.json",b"",tarfile.SYMTYPE)]]:
            with self.subTest(members=members):
                b,_ = self.transport(members)
                with self.assertRaises(GuardError): b.read_manifest()
                self.assertFalse(any((self.root/"cache").iterdir()))

    def test_duplicate_member_and_missing_index_are_refused(self):
        for members in [[("archive.json",b'{}',tarfile.REGTYPE)]*2,
                        [("objects/"+"a"*64,b"x",tarfile.REGTYPE)]]:
            with self.subTest(members=members):
                b,_ = self.transport(members)
                with self.assertRaises(GuardError): b.read_manifest()

    def test_binary_exit_failure_is_not_an_accepted_replica(self):
        b,_ = self.transport([("archive.json",b'{}',tarfile.REGTYPE)])
        b.command = [sys.executable,"-c","raise SystemExit(1)"]
        with self.assertRaises(GuardError): b.read_manifest()

    def test_timeout_child_is_reaped_and_cache_removed(self):
        b,_ = self.transport([("archive.json",b'{}',tarfile.REGTYPE)])
        b.config["timeout_seconds"] = 0.1
        b.command = [sys.executable,"-c","import time;time.sleep(20)"]
        with self.assertRaisesRegex(GuardError,"deadline"): b.read_manifest()
        self.assertFalse(any((self.root/"cache").iterdir()))

    def test_short_member_bound_and_bad_locator_refuse(self):
        key = "a"*64
        b,_ = self.transport([("archive.json",b'{}',tarfile.REGTYPE),("objects/"+key,b"too large",tarfile.REGTYPE)])
        b.read_manifest()
        with self.assertRaises(GuardError): b.read_object(key,1)
        with self.assertRaises(GuardError): b.read_object("../secret",1)
        with self.assertRaises(GuardError):
            ReleaseTarBackend(dict(b.config,repo="../private"),self.root/"cache",0)

    def test_all_download_bytes_are_charged_to_io_budget(self):
        b,_ = self.transport([("archive.json",b'{}',tarfile.REGTYPE)])
        charged=[]; b.io_callback=charged.append
        self.assertEqual(b.read_manifest(),b'{}')
        self.assertEqual(sum(charged),b.config['tar_bytes'])

    def test_lifecycle_cancellation_reaps_owned_get_and_removes_cache(self):
        b,_ = self.transport([("archive.json",b'{}',tarfile.REGTYPE)])
        b.command=[sys.executable,'-c','import time;time.sleep(20)']
        calls=[]
        def cancel():
            calls.append(1)
            if len(calls)>1: raise GuardError('fixture lifecycle cancellation')
        b.tick_callback=cancel
        with self.assertRaisesRegex(GuardError,'cancellation'): b.read_manifest()
        self.assertFalse(any((self.root/'cache').iterdir()))


class ServiceRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        self.cases = self.root/"cases"; self.cases.mkdir(); (self.cases/"closed").mkdir()
        self.raw = self.cases/"closed/raw.bin"; self.raw.write_bytes(b"unique fixture evidence"*1000)
        def pin(p):
            data=p.read_bytes(); return {"path":str(p),"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}
        gates=[]
        for role,data in [("source",{}),("manifest",{"status":"fixture-complete"}),
                          ("result",{"status":"fixture-validated"}),
                          ("ownership",{"case_id":"closed","status":"closed","active_writer_count":0,"writer_fence_verified":True})]:
            p=self.root/(role+".json"); p.write_text(json.dumps(data))
            gates.append({"role":role,"pin":pin(p),"required":{} if role=="source" else {"status":data["status"]}})
        self.admission = self.root/"admission.json"
        self.admission.write_text(json.dumps({"case_id":"closed","relative_case_dir":"closed",
            "files":[dict({k:v for k,v in pin(self.raw).items() if k!='path'},relative="raw.bin",category="original",cleanup_eligible=False)],"gates":gates}))
        self.config = self.root/"config.json"
        self.config.write_text(json.dumps({"schema":"DAMS-storage-service-1","cases_root":str(self.cases),
            "vault_root":str(self.root/"vault"),"gate_roots":[str(self.root)],"replica_cache":str(self.root/"cache"),
            "admissions":[str(self.admission)],"policy":{"primary_failure_domain":"fixture-local",
            "minimum_free_bytes":0,"low_watermark_free_bytes":0,"max_archive_bytes":1024**2,"max_io_bytes_per_second":0}}))

    def test_original_archive_and_restart_never_delete_unique_evidence(self):
        first=run_once(self.config)
        self.assertFalse(any(x["status"]=="RETAINED_FAIL_CLOSED" for x in first["cases"]))
        vault=self.root/"vault"
        archive=(vault/"cases/closed/archive.json").read_bytes()
        second=run_once(self.config)
        self.assertEqual((vault/"cases/closed/archive.json").read_bytes(),archive)
        self.assertTrue(self.raw.exists())
        self.assertEqual(second["cases"][0]["status"],"ARCHIVED_ORIGINALS_RETAINED")

    def test_manifest_drift_retains_files_and_has_monitor_reason(self):
        (self.root/"manifest.json").write_text('{"status":"changed"}')
        report=run_once(self.config)
        self.assertEqual(report["cases"][0]["status"],"RETAINED_FAIL_CLOSED")
        self.assertTrue(self.raw.exists())
        self.assertTrue((self.root/"vault/monitor-state.json").exists())

    def test_new_cleanup_candidate_needs_exact_independent_backup(self):
        definition=json.loads(self.admission.read_text())
        definition["files"][0].update(category="regenerable",cleanup_eligible=True)
        self.admission.write_text(json.dumps(definition))
        report=run_once(self.config)
        self.assertEqual(report["cases"][-1]["status"],"RETAINED_AWAITING_INDEPENDENT_REPLICA")
        self.assertTrue(self.raw.exists())

    def test_restarted_admission_change_is_refused(self):
        run_once(self.config)
        definition=json.loads(self.admission.read_text())
        definition["files"][0]["category"]="regenerable"
        self.admission.write_text(json.dumps(definition))
        report=run_once(self.config)
        self.assertEqual(report["cases"][0]["status"],"RETAINED_FAIL_CLOSED")
        self.assertTrue(self.raw.exists())


if __name__ == "__main__":
    unittest.main()
