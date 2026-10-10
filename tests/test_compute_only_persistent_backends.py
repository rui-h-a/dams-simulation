"""Offline toy bytes/mock REST only: no GCS, Model, SQL or paid resources."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

from research_tools import compute_only_persistent_backends as pb


class Meter:
    def __init__(self): self.before_events=[]; self.after_events=[]; self.refuse=False
    def before(self,event):
        if self.refuse: raise pb.Refusal('original ledger exhausted')
        self.before_events.append(copy.deepcopy(event)); return len(self.before_events)
    def after(self,ticket,event):self.after_events.append((ticket,copy.deepcopy(event)))


class Response:
    def __init__(self,status,body=b'',headers=None):
        self.status=status;self.body=io.BytesIO(body);self.headers=headers or {};self.closed=False
    def read(self,count):return self.body.read(count)
    def close(self):self.closed=True;self.body.close()


class MockGoogle:
    def __init__(self):
        self.objects={};self.calls=[];self.responses=[];self.next_generation=1;self.mode=None
        self.policy={'name':'fixture-bucket','location':'US-CENTRAL1','versioning':{'enabled':True},
                     'iamConfiguration':{'uniformBucketLevelAccess':{'enabled':True},'publicAccessPrevention':'enforced'},
                     'softDeletePolicy':{'retentionDurationSeconds':'604800'}}
        self.iam={'bindings':[{'role':'roles/storage.objectAdmin','members':['serviceAccount:fixture.invalid']} ]}
    def request(self,method,path,body,headers,timeout):
        assert headers['Authorization']=='Bearer fixture-token' and 0<timeout<=300
        self.calls.append((method,path,len(body) if body else 0));query=parse_qs(urlsplit(path).query);base=urlsplit(path).path
        if self.mode=='token_exception':raise RuntimeError('fixture-token confidential failure')
        if self.mode=='delay':time.sleep(.025)
        if '/upload/' in path:
            assert query['ifGenerationMatch']==['0'];name=query['name'][0]
            if name in self.objects:r=Response(412,b'{}')
            else:
                gen=str(self.next_generation);self.next_generation+=1;self.objects[name]=(body,gen)
                if self.mode=='partial':self.objects[name]=(body[:1],gen);r=Response(503,b'{}')
                else:r=Response(200,pb.blob({'bucket':'fixture-bucket','name':name,'size':str(len(body)),'generation':gen,'contentType':'application/octet-stream'}))
        elif base.endswith('/iam'):r=Response(200,pb.blob(self.iam))
        elif '/o/' not in base:r=Response(200,pb.blob(self.policy))
        else:
            name=unquote(base.split('/o/')[1]);raw,gen=self.objects[name]
            if query.get('alt')==['media']:
                assert query['generation']==query['ifGenerationMatch']
                if query['generation'] != [gen]:r=Response(404,b'')
                else:
                    out=raw[:-1] if self.mode=='truncate' else raw
                    h={'x-goog-generation':'999' if self.mode=='wrong_generation' else gen,'content-length':str(len(raw))}
                    r=Response(200,out,h)
                    if self.mode=='no_eof':
                        r.read=lambda count:b'x'*count
            else:r=Response(200,pb.blob({'bucket':'fixture-bucket','name':name,'size':str(len(raw)),'generation':gen,'contentType':'application/octet-stream',**({'contentEncoding':'gzip'} if self.mode=='encoded_metadata' else {})}))
        self.responses.append(r);return r


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='persistent-adapter-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.serial=0;self.job='a'*64;self.google=MockGoogle();self.meter=Meter()
        self.mount=mock.patch.object(pb.os.path,'ismount',return_value=True);self.mount.start();self.addCleanup(self.mount.stop)
        self.raw=b'exact encoded bytes\x00'*150;self.key='objects/'+pb.digest(self.raw)+'.z';self.meta='b'*64+'/index.json'
    def config(self,kind='external-cas',changes=None,enrollment_changes=None):
        self.serial+=1;root=self.root/str(self.serial);root.mkdir();state=root/'state';state.mkdir();configdir=root/'config';configdir.mkdir()
        identity=lambda p:[p.stat().st_dev,p.stat().st_ino]
        if kind=='external-cas':
            cas=root/'cas';cas.mkdir();domain='filesystem-device:'+str(cas.stat().st_dev);locator=str(cas)
            extra={'root':str(cas),'root_identity':identity(cas),'volume':str(self.root),'volume_identity':identity(self.root),'min_free_bytes':0}
        else:
            domain='provider:google-cloud-storage';locator='gs://fixture-bucket/enrolled/job'
            extra={'bucket':'fixture-bucket','prefix':'enrolled/job','expected_location':'US-CENTRAL1','expected_versioning':True,
                   'expected_soft_delete_seconds':604800,'expected_ubla':True,'expected_public_access_prevention':'enforced'}
        e={'schema':'dams-compute-persistent-backup-enrollment-v1','job_sha256':self.job,'backup_id':'backup-'+str(self.serial),
           'domain':domain,'locator':locator,'kind':'external-persistent'}
        if enrollment_changes:e.update(enrollment_changes)
        enrolled=pb.blob(e);binding={k:e[k] for k in ['backup_id','domain','locator']};binding['enrollment_sha256']=pb.digest(enrolled)
        c={'schema':'dams-compute-persistent-adapter-v1','kind':kind,'binding':binding,'job_sha256':self.job,'source_sha256':pb.IMPORT_SHA,
           'state_root':str(state),'state_identity':identity(state),'max_object_bytes':65536,'timeout_seconds':2,'max_retries':0,**extra}
        if changes:c.update(changes)
        path=configdir/'config.json';path.write_bytes(pb.blob(c));return path,pb.digest(path.read_bytes()),enrolled,c
    def make(self,kind='external-cas',changes=None,enrollment_changes=None,cancel=None):
        path,sha,e,c=self.config(kind,changes,enrollment_changes)
        args=(path,sha,e,self.job)
        b=pb.ExternalCASBackend(*args,cancel=cancel) if kind=='external-cas' else pb.GCSBackend(*args,token_provider=lambda:'fixture-token',meter=self.meter,transport=self.google,cancel=cancel)
        self.addCleanup(b.close)
        if kind=='gcs':b.admit()
        return b,path,c
    def full(self,stream):
        try:
            out=b''
            while chunk:=stream.read(65536):out+=chunk
            return out
        finally:stream.close()
    def test_cas_full_publish_read_reuse_restart(self):
        b,p,c=self.make();b.publish(self.key,self.raw);b.publish(self.key,self.raw);self.assertEqual(self.full(b.read(self.key)),self.raw)
        sha=pb.digest(p.read_bytes());e=b.enrollment_raw;b.close();restored=pb.ExternalCASBackend(p,sha,e,self.job);self.addCleanup(restored.close)
        self.assertEqual(self.full(restored.read(self.key)),self.raw)
    def test_cas_metadata_immutable_collision(self):
        b,_,_=self.make();b.publish(self.meta,b'{}')
        with self.assertRaises(ValueError):b.publish(self.meta,b'different')
        self.assertEqual(self.full(b.read(self.meta)),b'{}')
    def test_cas_hash_key_mismatch(self):
        b,_,_=self.make()
        with self.assertRaises(ValueError):b.publish(self.key,b'wrong')
    def test_unsafe_keys_both_transports(self):
        for kind in ['external-cas','gcs']:
            b,_,_=self.make(kind)
            for key in ['../outside','objects/x.z','/absolute','terminal/../index.json','terminal/index.json?generation=9','c'*64+'/other.json']:
                with self.subTest(kind=kind,key=key),self.assertRaises(ValueError):b.publish(key,b'x')
    def test_cas_symlink_payload_refused(self):
        b,_,c=self.make();obj=Path(c['root'])/'objects';obj.mkdir();outside=self.root/'outside';outside.write_bytes(self.raw);(obj/self.key.split('/')[1]).symlink_to(outside)
        with self.assertRaises((ValueError,OSError,RuntimeError)):b.publish(self.key,self.raw)
        self.assertEqual(outside.read_bytes(),self.raw)
    def test_cas_toctou_root_swap_refused(self):
        b,_,c=self.make();root=Path(c['root']);moved=root.with_name('moved');root.rename(moved);outside=self.root/'outside';outside.mkdir();root.symlink_to(outside,target_is_directory=True)
        with self.assertRaises((ValueError,OSError,RuntimeError)):b.publish(self.key,self.raw)
        self.assertEqual(list(outside.iterdir()),[])
    def test_cas_raw_change_after_open_refused(self):
        b,_,c=self.make();b.publish(self.key,self.raw);stream=b.read(self.key);stream.read(1);(Path(c['root'])/self.key).write_bytes(b'x'*len(self.raw))
        with self.assertRaises((ValueError,RuntimeError)):stream.read(1)
        stream.close()  # Preserve the first failure while still releasing all FDs.
    def test_cas_no_eof_rejected(self):
        b,_,_=self.make();b.publish(self.key,self.raw);s=b.read(self.key);s.read(1)
        with self.assertRaises(ValueError):s.close()
    def test_cas_partial_write_no_published_object(self):
        b,_,c=self.make()
        with mock.patch.object(pb.os,'write',return_value=0),self.assertRaises(ValueError):b.publish(self.key,self.raw)
        self.assertFalse((Path(c['root'])/self.key).exists());self.assertEqual(list(Path(c['root']).rglob('.pending-*')),[])
    def test_cas_space_floor_refused(self):
        b,_,c=self.make()
        with mock.patch.object(pb.shutil,'disk_usage',return_value=type('D',(),{'free':10})()),self.assertRaises(ValueError):b.publish(self.key,self.raw)
        self.assertFalse((Path(c['root'])/self.key).exists())
    def test_cas_false_domain_and_same_device_pair_refused(self):
        with self.assertRaises(ValueError):self.make(enrollment_changes={'domain':'pretend-another-device'})
        a,_,_=self.make();b,_,_=self.make()
        self.assertEqual(a.binding['domain'],b.binding['domain'])
        with self.assertRaises(ValueError):pb.admit_pair([a,b])
    def test_config_mutation_refused(self):
        b,p,c=self.make();p.write_bytes(pb.blob({**c,'max_object_bytes':999}))
        with self.assertRaises(ValueError):b.check()
    def test_fifo_metadata_refused_without_blocking(self):
        for leaf in ['config','binding']:
            b,p,c=self.make();target=p if leaf=='config' else Path(c['state_root'])/'binding.json';target.unlink();os.mkfifo(target)
            started=time.monotonic()
            with self.assertRaises((ValueError,RuntimeError)):b.check()
            self.assertLess(time.monotonic()-started,1)
    def test_enrollment_wrong_job_refused(self):
        with self.assertRaises(ValueError):self.make(enrollment_changes={'job_sha256':'f'*64})
    def test_volume_identity_refused(self):
        with self.assertRaises(ValueError):self.make(changes={'volume_identity':[0,0]})
    def test_cancellation_prevents_publish(self):
        state=[False];b,_,_=self.make(cancel=lambda:state[0]);state[0]=True
        with self.assertRaises(ValueError):b.publish(self.key,self.raw)
    def test_gcs_full_publish_read_create_only(self):
        b,_,_=self.make('gcs');b.publish(self.key,self.raw);self.assertEqual(self.full(b.read(self.key)),self.raw)
        posts=[p for m,p,_ in self.google.calls if m=='POST'];self.assertTrue(posts and all('ifGenerationMatch=0' in p for p in posts))
        self.assertEqual(len(self.google.calls),len(self.meter.before_events));self.assertEqual(len(self.meter.before_events),len(self.meter.after_events))
        self.assertTrue(all(r.closed for r in self.google.responses));self.assertNotIn('fixture-token',str(self.meter.before_events)+str(self.meter.after_events))
    def test_gcs_check_no_remote_requests(self):
        b,_,_=self.make('gcs');before=len(self.google.calls)
        for _ in range(50):b.check()
        self.assertEqual(len(self.google.calls),before)
    def test_gcs_failed_readmission_cannot_reuse_old_policy_admission(self):
        b,_,_=self.make('gcs');self.google.policy['versioning']={'enabled':False}
        with self.assertRaises(pb.Refusal):b.admit()
        with self.assertRaisesRegex(pb.Refusal,'fresh actual GCS policy'):b.check()
    def test_gcs_collision_equal_fullbytes_reused(self):
        b,_,_=self.make('gcs');b.publish(self.key,self.raw);b.publish(self.key,self.raw);self.assertEqual(self.full(b.read(self.key)),self.raw)
    def test_gcs_collision_content_encoding_refused(self):
        b,_,_=self.make('gcs');b.publish(self.key,self.raw);self.google.mode='encoded_metadata'
        with self.assertRaises(pb.Refusal):b.publish(self.key,self.raw)
    def test_gcs_collision_different_fullbytes_refused(self):
        b,_,_=self.make('gcs');b.publish(self.meta,b'{}')
        with self.assertRaises(ValueError):b.publish(self.meta,b'xx')
    def test_gcs_partial_upload_not_committed_in_client(self):
        b,_,_=self.make('gcs');self.google.mode='partial'
        with self.assertRaises(ValueError):b.publish(self.key,self.raw)
        with self.assertRaises(FileNotFoundError):b.read(self.key)
        self.assertTrue(all(r.closed for r in self.google.responses))
    def test_gcs_truncated_read_refused(self):
        b,_,_=self.make('gcs');b.publish(self.key,self.raw);self.google.mode='truncate'
        with self.assertRaises(ValueError):self.full(b.read(self.key))
        self.assertTrue(all(r.closed for r in self.google.responses))
    def test_gcs_no_eof_refused(self):
        b,_,_=self.make('gcs');b.publish(self.key,self.raw);self.google.mode='no_eof'
        with self.assertRaises(ValueError):self.full(b.read(self.key))
        self.assertTrue(all(r.closed for r in self.google.responses))
    def test_gcs_generation_drift_refused(self):
        b,_,_=self.make('gcs');b.publish(self.key,self.raw);self.google.mode='wrong_generation'
        with self.assertRaises(ValueError):b.read(self.key)
        self.google.mode=None;name=b.config['prefix']+'/'+self.key;self.google.objects[name]=(self.raw,'77')
        with self.assertRaises(ValueError):b.publish(self.key,self.raw)
    def test_gcs_deadline_refused_and_response_closed(self):
        b,_,_=self.make('gcs',changes={'timeout_seconds':.01});self.google.mode='delay'
        with self.assertRaises(ValueError):b.publish(self.key,self.raw)
        self.assertTrue(all(r.closed for r in self.google.responses))
    def test_gcs_public_policy_refused(self):
        self.google.iam['bindings'][0]['members'].append('allUsers')
        with self.assertRaises(ValueError):self.make('gcs')
    def test_gcs_policy_softdelete_versioning_refused(self):
        for field in ['softDeletePolicy','versioning']:
            saved=self.google.policy[field];self.google.policy[field]={}
            with self.assertRaises(ValueError):self.make('gcs')
            self.google.policy[field]=saved
    def test_gcs_explicit_disabled_version_and_softdelete_policy(self):
        self.google.policy['versioning']={'enabled':False};self.google.policy['softDeletePolicy']={'retentionDurationSeconds':'0'}
        b,_,_=self.make('gcs',changes={'expected_versioning':False,'expected_soft_delete_seconds':0})
        b.publish(self.key,self.raw);self.assertEqual(self.full(b.read(self.key)),self.raw)
    def test_gcs_disabled_expected_version_rejects_enabled_actual(self):
        with self.assertRaises(ValueError):self.make('gcs',changes={'expected_versioning':False})
    def test_gcs_non2xx_attempts_are_metered_failed_without_refund(self):
        b,_,_=self.make('gcs');b.publish(self.key,self.raw);b.publish(self.key,self.raw)
        failures=[row for _,row in self.meter.after_events if row['status']==412]
        self.assertEqual(len(failures),1);self.assertTrue(failures[0]['failed']);self.assertTrue(failures[0]['normal_eof'])
        self.assertEqual(len(self.meter.before_events),len(self.meter.after_events))
    def test_gcs_retry_each_attempt_charged_and_bounded(self):
        b,_,_=self.make('gcs',changes={'max_retries':2});before=len(self.google.calls);original=self.google.request;left=[2]
        def transient(method,path,body,headers,timeout):
            if method=='POST' and left[0]:
                left[0]-=1;self.google.calls.append((method,path,len(body)));r=Response(503,b'{}');self.google.responses.append(r);return r
            return original(method,path,body,headers,timeout)
        self.google.request=transient;b.publish(self.key,self.raw)
        self.assertEqual(len(self.google.calls)-before,4)
        self.assertEqual(len(self.meter.before_events),len(self.meter.after_events))
        self.assertEqual(sum(row['failed'] for _,row in self.meter.after_events),2)
    def test_gcs_read_exception_preserved_and_counter_closed(self):
        b,_,_=self.make('gcs');b.publish(self.key,self.raw);s=b.read(self.key)
        def broken(count):raise RuntimeError('fixture-token secret response error')
        self.google.responses[-1].read=broken
        with self.assertRaisesRegex(pb.Refusal,'bounded Google response failed'):s.read(1)
        s.close();self.assertTrue(self.google.responses[-1].closed)
        self.assertEqual(self.meter.after_events[-1][1]['failed'],True)
        self.assertNotIn('fixture-token',str(self.meter.after_events))
    def test_gcs_cancellation_during_stream_releases_response(self):
        cancelled=[False];b,_,_=self.make('gcs',cancel=lambda:cancelled[0]);b.publish(self.key,self.raw);s=b.read(self.key);s.read(1);cancelled[0]=True
        with self.assertRaises(pb.Refusal):s.read(1)
        s.close();self.assertTrue(self.google.responses[-1].closed)
        self.assertTrue(self.meter.after_events[-1][1]['failed'])
    def test_cas_open_anchor_failure_releases_payload_fd(self):
        b,_,c=self.make();b.publish(self.key,self.raw);(Path(c['root'])/self.key).write_bytes(b'changed')
        original=pb.os.close;closed=[]
        def observe(fd):closed.append(fd);return original(fd)
        with mock.patch.object(pb.os,'close',side_effect=observe),self.assertRaises(pb.Refusal):b.read(self.key)
        self.assertGreaterEqual(len(closed),2)
    def test_gcs_durable_meter_before_network(self):
        b,_,_=self.make('gcs');before=len(self.google.calls);self.meter.refuse=True
        with self.assertRaises(ValueError):b.publish(self.key,self.raw)
        self.assertEqual(len(self.google.calls),before)
    def test_gcs_meter_refusal_exception_frame_clears_token(self):
        b,_,_=self.make('gcs');self.meter.refuse=True
        try:b.publish(self.key,self.raw)
        except pb.Refusal as error:
            trace=error.__traceback__;found=False
            while trace:
                if trace.tb_frame.f_code.co_name=='_request':
                    found=True;self.assertIsNone(trace.tb_frame.f_locals['token'])
                trace=trace.tb_next
            self.assertTrue(found);self.assertNotIn('fixture-token',str(error))
        else:self.fail('meter refusal did not block request')
    def test_gcs_credentials_not_in_failure_or_meter(self):
        b,_,_=self.make('gcs');self.google.mode='token_exception'
        with self.assertRaises(ValueError) as caught:b.publish(self.key,self.raw)
        self.assertNotIn('fixture-token',str(caught.exception));self.assertNotIn('fixture-token',str(self.meter.after_events))
    def test_gcs_requires_actual_admission(self):
        p,sha,e,c=self.config('gcs');b=pb.GCSBackend(p,sha,e,self.job,token_provider=lambda:'fixture-token',meter=self.meter,transport=self.google);self.addCleanup(b.close)
        with self.assertRaises(ValueError):b.check()
        self.assertEqual(len(self.google.calls),0)
    def test_gcs_restart_keeps_generation_and_requires_fresh_policy(self):
        b,p,c=self.make('gcs');b.publish(self.key,self.raw);e=b.enrollment_raw;b.close();before=len(self.google.calls)
        new=pb.GCSBackend(p,pb.digest(p.read_bytes()),e,self.job,token_provider=lambda:'fixture-token',meter=self.meter,transport=self.google);self.addCleanup(new.close);new.admit()
        self.assertEqual(len(self.google.calls),before+2);self.assertEqual(self.full(new.read(self.key)),self.raw)
    def test_pair_one_actual_fs_and_one_mock_Google(self):
        a,_,_=self.make();b,_,_=self.make('gcs');self.assertEqual(pb.admit_pair([a,b]),(a,b))


if __name__=='__main__':unittest.main()
