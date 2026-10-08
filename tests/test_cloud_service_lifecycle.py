"""Actual Bash unit materialization in a private fake filesystem; no systemd/GCP."""
from datetime import datetime,timezone,timedelta
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'research_tools'))
from cloud_control import GuardError
from cloud_worker import service_stop_timeout,main


class ServiceLifecycleTests(unittest.TestCase):
    def runtime(self):
        return {'pipeline_stop_grace_seconds':360,'shutdown_margin_seconds':3630,
                'runtime_limits':{'cooperative_stop_grace_seconds':300},'upload_interval_seconds':300,
                'deadline_utc':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()}

    def test_runtime_stop_timeout_retains_full_cp_and_archive_window(self):
        now=datetime(2030,1,1,tzinfo=timezone.utc);runtime=self.runtime();runtime['deadline_utc']=(now+timedelta(hours=2)).isoformat()
        self.assertEqual(service_stop_timeout(runtime,now=now),3990)
        self.assertEqual(service_stop_timeout({},now=now),45)
        self.assertEqual(service_stop_timeout(runtime,now=now+timedelta(seconds=7100)),100)
        for invalid in ({'pipeline_stop_grace_seconds':True},{'shutdown_margin_seconds':360},
                        {'runtime_limits':{'cooperative_stop_grace_seconds':301}},
                        {'deadline_utc':(now-timedelta(seconds=1)).isoformat()}):
            with self.assertRaises(GuardError):service_stop_timeout(runtime|invalid,now=now)

    def test_actual_install_script_materializes_3990_and_keeps_absolute_watchdog_and_no_restart(self):
        for explicit in (True,False):
            with self.subTest(explicit=explicit),tempfile.TemporaryDirectory() as temporary:
                base=Path(temporary);units=base/'units';units.mkdir();work=base/'work';work.mkdir();binpath=base/'bin';binpath.mkdir()
                runtime=self.runtime()
                if not explicit:
                    runtime.pop('pipeline_stop_grace_seconds');runtime['runtime_limits'].pop('cooperative_stop_grace_seconds')
                (work/'guest-runtime.json').write_text(json.dumps(runtime))
                calls=base/'systemctl-calls';stub=binpath/'systemctl'
                stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DAMS_TEST_SYSTEMCTL_CALLS"\n');stub.chmod(0o700)
                script=(ROOT/'cloud/install-services.sh').read_text().replace('/opt/dams',str(ROOT)).replace('/var/lib/dams',str(work)).replace('/etc/systemd/system',str(units))
                target=base/'install-test.sh';target.write_text(script)
                result=subprocess.run(['bash',str(target)],env=os.environ|{'PATH':str(binpath)+os.pathsep+os.environ['PATH'],
                                      'DAMS_TEST_SYSTEMCTL_CALLS':str(calls)},capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                pipeline=(units/'dams-pipeline.service').read_text()
                self.assertIn('TimeoutStopSec='+str(3990 if explicit else 45)+'\n',pipeline)
                self.assertIn('Type=simple\n',pipeline);self.assertIn('KillMode=mixed\n',pipeline);self.assertIn('Restart=no\n',pipeline)
                self.assertIn('ExecStopPost=',pipeline)
                self.assertIn('TimeoutStartSec=infinity',(units/'dams-upload.service').read_text())
                self.assertEqual(calls.read_text().splitlines(),['daemon-reload','enable --now dams-upload.timer dams-watchdog.service','enable --now dams-pipeline.service'])

    def test_unchanged_watchdog_poweroff_margin_leaves_3600_seconds_with_3630_archive_margin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);runtime=self.runtime();runtime['deadline_utc']='2099-01-01T02:00:00Z'
            path=root/'runtime.json';path.write_text(json.dumps(runtime))
            from cloud_control import utc
            now=utc(runtime['deadline_utc']).timestamp()-3630
            with patch('cloud_worker.time.time',return_value=now),patch('cloud_worker.time.sleep') as sleep, \
                 patch('cloud_worker.subprocess.run') as command:
                self.assertEqual(main(['watchdog','--runtime',str(path),'--work',str(root/'work')]),0)
            sleep.assert_called_once_with(3600)
            self.assertEqual([c.args[0] for c in command.call_args_list],[
                ['systemctl','kill','--signal=SIGTERM','dams-pipeline.service'],
                ['systemctl','start','--no-block','dams-upload.service'],['systemctl','poweroff']])

    def test_explicit_poststop_upload_is_noop_for_valid_final_fence_without_archival_success_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);work=root/'work';output=work/'output';output.mkdir(parents=True)
            (output/'exact-interrupted-evidence').write_bytes(b'unchanged raw')
            runtime=self.runtime()|{'bucket':'synthetic','prefix':'offline','max_storage_requests':100,'max_upload_bytes':100_000,
                                    'final_storage_requests_reserved':80,'final_storage_bytes_reserved':80_000}
            path=root/'runtime.json';path.write_text(json.dumps(runtime))
            request=work/'storage-requests.json';request.write_text('{"requests_upper":12}')
            phase=work/'storage-requests.phase.json';phase.write_text('{"version":1,"phase":"final"}')
            before={p.relative_to(work).as_posix():p.read_bytes() for p in work.rglob('*') if p.is_file()}
            stdout=io.StringIO()
            with patch('cloud_worker.urllib.request.urlopen',side_effect=AssertionError('poststop HTTP forbidden')), \
                 patch('cloud_worker.snapshot',side_effect=AssertionError('cannot overwrite forensic pointer')),patch('sys.stdout',stdout):
                self.assertEqual(main(['upload','--runtime',str(path),'--work',str(work)]),0)
            self.assertEqual(before,{p.relative_to(work).as_posix():p.read_bytes() for p in work.rglob('*') if p.is_file()})
            message=json.loads(stdout.getvalue());self.assertEqual(message,{'event':'periodic_persistence_skipped','reason':'owned_final_phase'})
            self.assertNotIn('science_complete',message)
            for invalid in ('{"version":true,"phase":"final"}','{"version":1,"phase":"wrong"}','broken'):
                phase.write_text(invalid)
                with self.assertRaises(GuardError):main(['upload','--runtime',str(path),'--work',str(work)])


if __name__=='__main__':unittest.main()
