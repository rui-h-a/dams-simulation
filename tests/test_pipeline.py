import dataclasses
from datetime import datetime, timezone, timedelta
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.cli import run_world
from dams_sim.design import stage_tasks
from dams_sim.pipeline import driver_hash,make_base
from dams_sim.runtime import RuntimeLimits
from dams_sim.scheduler import Scheduler,restore_checkpoint
from dams_sim.spec import resolve_spec,case_key,workload
from dams_sim.storage import canonical,digest

class PipelineTests(unittest.TestCase):
    def limits(self,**kw):
        return RuntimeLimits.from_dict(dict(max_workers=2,max_retries=2,
            deadline_utc=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(),
            checkpoint_interval_days=1)|kw)
    def test_streaming_state_matches_exact_canonical_state(self):
        # Twelve guild keys expose integer-versus-text JSON key ordering mistakes.
        m=Model(Config(n=36,days=4,guilds=12,sites=3)).run()
        s=io.BytesIO()
        class Sink:
            def write(self,x):s.write(x.encode() if isinstance(x,str) else x)
        m.write_state(Sink())
        self.assertEqual(s.getvalue(),canonical(m.state()))
    def test_strict_runtime_and_ten_million_event_guard(self):
        Config(n=10_000_000,days=30,max_events=300_000_000).validate()
        with self.assertRaises(ValueError):Config(n=10_000_000,days=30,max_events=299_999_999).validate()
        with self.assertRaises(ValueError):Config(n=10_000_000,days=30).validate()
        for d in ({'cpu_budget':2.0},{'max_workers':True},{'max_events':float('inf')},{'provenance':{'project':'private'}},{'typo':1}):
            with self.assertRaises(ValueError):self.limits(**d)
    def test_spec_reaches_every_dynamic_stage_and_recovery_size(self):
        s=resolve_spec('full-study',1000);b=make_base(s,self.limits(max_events=1_000_000,max_output_bytes=500_000_000))
        for stage in ('pilot','confirmation','stress','sensitivity','scenarios','extended'):
            tasks=stage_tasks(stage,s,b,32)
            self.assertTrue(tasks)
            self.assertTrue(all(c.n==1000 and c.days==60 for c,t in tasks))
        for c,t in stage_tasks('stress',s,b):
            if 'attack_budget_setting' in t:
                self.assertIn(c.attack_budget_hours_per_day,(1000*(2/120),1000*(8/120)))
    def test_periodic_checkpoint_and_restore_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Config(n=24,days=12,guilds=3);path=Path(tmp)
            run_world(p,path,checkpoint_interval_days=2)
            idx=json.loads((path/'checkpoint-index.json').read_text())
            self.assertEqual([r['day'] for r in idx['snapshots']],[12,10])
            restored,_=restore_checkpoint(path,p)
            self.assertEqual(canonical(restored.state()),canonical(Model(p).run().state()))
            file=path/idx['snapshots'][0]['file'];file.write_bytes(file.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError,'integrity'):restore_checkpoint(path,p)
    def test_worker_count_and_idempotent_completed_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            configs=[Config(n=24,days=10,guilds=3,world=w) for w in (71,72,73)]
            a=Path(tmp)/'a';b=Path(tmp)/'b';a.mkdir();b.mkdir()
            first=Scheduler(a,dataclasses.replace(self.limits(),max_workers=1),driver_hash()).run(configs)
            second=Scheduler(b,self.limits(),driver_hash()).run(list(reversed(configs)))
            left={c.world:digest((a/r['attempt']/'final_state.json').read_bytes()) for c,r in zip(configs,first)}
            right={c.world:digest((b/r['attempt']/'final_state.json').read_bytes()) for c,r in zip(reversed(configs),second)}
            self.assertEqual(left,right)
            before=list((a/'cases').rglob('manifest.json'))
            Scheduler(a,self.limits(),driver_hash()).run(configs)
            self.assertEqual(before,list((a/'cases').rglob('manifest.json')))
    def test_killed_worker_automatically_resumes_same_final_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=Config(n=1200,days=16,guilds=4,max_wall_seconds=30,max_rss_mb=256)
            limits=self.limits(world_timeout_seconds=30)
            code="from pathlib import Path;from dams_sim.config import Config;from dams_sim.runtime import RuntimeLimits;from dams_sim.scheduler import Scheduler;from dams_sim.pipeline import driver_hash;import json;Scheduler(Path(%r),RuntimeLimits.from_dict(json.loads(%r)),driver_hash()).run([Config.from_dict(json.loads(%r))])"%(tmp,json.dumps(limits.to_dict()),json.dumps(p.to_dict()))
            proc=subprocess.Popen([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            deadline=time.monotonic()+20;victim=None
            while time.monotonic()<deadline:
                indexes=list(root.rglob('checkpoint-index.json'))
                if indexes:
                    manifest=json.loads((indexes[0].parent/'manifest.json').read_text())
                    victim=manifest['worker_pid'];os.kill(victim,signal.SIGKILL);break
                if proc.poll() is not None:break
                time.sleep(.01)
            out,err=proc.communicate(timeout=60)
            self.assertIsNotNone(victim,(out,err))
            self.assertEqual(proc.returncode,0,err.decode())
            complete=[a for a in root.rglob('manifest.json') if json.loads(a.read_text())['status']=='complete']
            self.assertEqual(len(complete),1)
            final=json.loads((complete[0].parent/'final_state.json').read_text())
            self.assertEqual(canonical(final),canonical(Model(p).run().state()))
            self.assertIn('restart_origin',json.loads(complete[0].read_text()))
    def test_scale_confirmation_prespecified_worlds_and_exploratory_exception(self):
        for n in (120,1000,10000,100000,1000000):
            spec=resolve_spec('scale-confirmation',n)
            self.assertEqual((spec.days,spec.pilot_worlds,spec.confirmation_min,spec.confirmation_max),(30,0,8,8))
        large=resolve_spec('scale-confirmation',10000000)
        self.assertEqual(large.confirmation_min,2)
        self.assertIn('exploratory',large.inferential_scope)
        self.assertEqual(large.stages,('confirmation',))

    def test_packaged_source_verification_refuses_missing_changed_or_mismatched_map(self):
        import shutil
        from dams_sim.storage import file_digest
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=Path(__file__).resolve().parents[1]
            shutil.copytree(source/'dams_sim',root/'dams_sim',ignore=shutil.ignore_patterns('__pycache__'))
            mapping={str(p.relative_to(root)):file_digest(p) for p in (root/'dams_sim').glob('*.py')}
            manifest=root/'source-manifest.json';commit='a'*40
            value={'commit':commit,'source_files_sha256':mapping}
            manifest.write_text(json.dumps(value))
            env=os.environ|{'PYTHONPATH':tmp,'DAMS_PACKAGED_COMMIT':commit,'DAMS_SOURCE_MANIFEST':str(manifest)}
            cmd=[sys.executable,'-c','from dams_sim.storage import provenance;print(provenance()["git_commit"])']
            result=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),commit)
            value['source_files_sha256'].pop('dams_sim/model.py');manifest.write_text(json.dumps(value))
            self.assertNotEqual(subprocess.run(cmd,cwd=root,env=env,capture_output=True).returncode,0)
            value={'commit':'b'*40,'source_files_sha256':mapping};manifest.write_text(json.dumps(value))
            self.assertNotEqual(subprocess.run(cmd,cwd=root,env=env,capture_output=True).returncode,0)

    def test_local_completed_cache_cannot_acquire_cloud_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Config(n=24,days=4,guilds=3)
            Scheduler(Path(tmp),self.limits(),driver_hash()).run([p])
            cloud=dict(environment='GCP',instance_id='fixture-instance',machine_type='fixture-machine',zone='fixture-zone',project_hash='a'*64,task_hash='b'*64)
            with self.assertRaisesRegex(ValueError,'provenance differs'):
                Scheduler(Path(tmp),self.limits(provenance=cloud),driver_hash()).run([p])
            self.assertEqual(len(list(Path(tmp).rglob('summary.json'))),1)

    def test_disk_and_absolute_deadline_do_not_report_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Config(n=24,days=12,guilds=3)
            with self.assertRaises(RuntimeError):Scheduler(Path(tmp),self.limits(batch_max_output_bytes=100_000_000,min_free_disk_bytes=10_000_000_000_000),driver_hash()).run([p])
            old=dataclasses.replace(self.limits(),deadline_utc='2000-01-01T00:00:00+00:00')
            with self.assertRaises(InterruptedError):Scheduler(Path(tmp),old,driver_hash()).run([p])
            self.assertFalse(list(Path(tmp).rglob('summary.json')))

if __name__=='__main__':unittest.main()
