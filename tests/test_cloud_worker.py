import hashlib
from copy import deepcopy
from contextlib import closing
import io
import json
from pathlib import Path
import sys
import tempfile
import os
import signal
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'research_tools'))
from cloud_control import GuardError
from cloud_worker import (snapshot, final_snapshot, download_snapshot, Store, PersistenceError, persistence_error_facts,
                          guest_memory, guest_metadata, root_block_interface, verify_guest, run_guest)
from cloud_worker import wait_pipeline_group_absent, archive_options, main
from cloud_archive import CODEC, RAW_CODEC, CHUNK_BYTES, MAX_DESCRIPTOR_BYTES


class ProtocolResponse(io.BytesIO):
    def __init__(self, body=b'', headers=None, status=200):
        super().__init__(body)
        self.headers, self.status = headers or {}, status


class ProtocolTransport:
    """Synthetic GCS boundaries through Store's real request/cap handling."""
    def __init__(self, payload, mode):
        self.payload, self.mode, self.remote = payload, mode, None
        self.calls, self.verifications = [], 0

    def __call__(self, request, timeout):
        method = request.get_method()
        phase = 'initiate' if method == 'POST' else ('chunk' if method == 'PUT' else 'verify')
        span = request.get_header('Content-range')
        self.calls.append({'phase': phase, 'range': span, 'bytes': len(request.data or b'')})

        def fail(status, headers=None):
            raise urllib.error.HTTPError('https://synthetic.invalid/private-session-do-not-log', status,
                                         'synthetic-token-do-not-log', headers or {}, io.BytesIO(b'private-response'))

        if phase == 'initiate':
            if self.mode.startswith('post_412'):
                self.remote = self.payload if self.mode == 'post_412_match' else b'z' * len(self.payload)
                fail(412)
            return ProtocolResponse(headers={'Location': 'https://synthetic.invalid/private-session-do-not-log'})
        if phase == 'chunk':
            if self.mode.startswith('put_412'):
                self.remote = (None if self.mode == 'put_412_missing' else
                               (self.payload if self.mode == 'put_412_match' else b'z' * len(self.payload)))
                fail(412)
            if self.mode == 'verify_429_then_put_412' and self.remote is not None:
                fail(412)
            if self.mode == 'put_403':
                fail(403)
            if self.mode in ('partial_308', 'backward_308', 'nonprogressing_308'):
                if self.remote is None:
                    self.remote = request.data[:4 * 1024**2]
                    fail(308, {'Range': 'bytes=0-4194303'})
                if self.mode == 'backward_308':
                    fail(308, {'Range': 'bytes=0-2097151'})
                if self.mode == 'nonprogressing_308':
                    fail(308, {'Range': 'bytes=0-4194303'})
                if not span.startswith('bytes 4194304-'):
                    fail(400)
                self.remote += request.data
                return ProtocolResponse()
            if self.mode == 'missing_range_308':
                fail(308)
            if self.mode == 'invalid_range_308':
                fail(308, {'Range': 'bytes=5-9'})
            if self.mode == 'negative_range_308':
                fail(308, {'Range': 'bytes=0--1'})
            if self.mode == 'excess_range_308':
                fail(308, {'Range': 'bytes=0-' + str(len(request.data))})
            if self.mode == 'incomplete_final_308':
                fail(308, {'Range': 'bytes=0-' + str(len(self.payload) - 1)})
            if self.mode == 'response_object_308' and self.remote is None:
                self.remote = request.data[:4 * 1024**2]
                return ProtocolResponse(headers={'Range': 'bytes=0-4194303'}, status=308)
            self.remote = (self.remote or b'') + (request.data or b'')
            return ProtocolResponse()
        self.verifications += 1
        if self.mode == 'verify_429_then_put_412' and self.verifications == 1:
            fail(429)
        if self.remote is None:
            fail(404)
        return ProtocolResponse(self.remote, {'Content-Length': str(len(self.remote))})


class StoreProtocolTests(unittest.TestCase):
    @staticmethod
    def store(root, **kwargs):
        store = Store('synthetic-private-bucket', 'synthetic-private-prefix', **kwargs)
        # Prevent auth/metadata/network calls; urllib's storage transport is
        # patched in every fixture, but request and byte reservations are real.
        store.token, store.token_until = 'synthetic-token-do-not-log', float('inf')
        return store

    def upload(self, root, store, transport, payload, immutable=True):
        path = root / 'payload'
        path.write_bytes(payload)
        sha = hashlib.sha256(payload).hexdigest()
        with patch('cloud_worker.urllib.request.urlopen', transport):
            store.put_file('blobs/' + sha, path, sha, immutable)

    def test_successful_small_and_zero_uploads_verify_actual_bytes(self):
        for payload in (b'', b'confirmed-bytes'):
            with self.subTest(bytes=len(payload)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); transport = ProtocolTransport(payload, 'success')
                self.upload(root, self.store(root), transport, payload)
                self.assertEqual(transport.remote, payload)
                self.assertEqual(transport.verifications, 1)
                self.assertEqual([c['phase'] for c in transport.calls], ['initiate', 'chunk', 'verify'])

    def test_post_and_put_412_require_matching_downloaded_bytes(self):
        payload = b'confirmed-bytes'
        for phase in ('post', 'put'):
            for matching in (True, False):
                with self.subTest(phase=phase, matching=matching), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    transport = ProtocolTransport(payload, phase + '_412_' + ('match' if matching else 'mismatch'))
                    if matching:
                        self.upload(root, self.store(root), transport, payload)
                    else:
                        with self.assertRaises(PersistenceError) as raised:
                            self.upload(root, self.store(root), transport, payload)
                        self.assertEqual(raised.exception.facts['reason'], 'remote_byte_verification_mismatch')
                        self.assertEqual(raised.exception.facts['phase'], 'verify')
                    self.assertEqual(transport.verifications, 1)

    def test_conflict_size_checked_even_if_download_claims_expected_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); payload = b'confirmed-bytes'; store = self.store(root)
            transport = ProtocolTransport(payload, 'put_412_match')
            sha = hashlib.sha256(payload).hexdigest()
            def lying_digest(key, path, **kwargs):
                Path(path).write_bytes(payload + b'extra')
                return sha
            with patch.object(store, 'download', lying_digest), self.assertRaises(PersistenceError) as raised:
                self.upload(root, store, transport, payload)
            self.assertEqual(raised.exception.facts['reason'], 'remote_byte_verification_mismatch')

    def test_commit_412_without_existing_remote_bytes_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); payload = b'confirmed-bytes'; transport = ProtocolTransport(payload, 'put_412_missing')
            with self.assertRaises(PersistenceError) as raised:
                self.upload(root, self.store(root), transport, payload)
            self.assertEqual((raised.exception.facts['phase'], raised.exception.facts['http_status']), ('verify', 404))
            self.assertEqual(transport.verifications, 1)

    def test_verify_429_then_reopened_commit_412_converges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); payload = b'confirmed-bytes'
            transport = ProtocolTransport(payload, 'verify_429_then_put_412')
            request_state = root / 'requests.json'
            with self.assertRaises(PersistenceError) as raised:
                self.upload(root, self.store(root, request_state=request_state, max_requests=6), transport, payload)
            self.assertEqual((raised.exception.facts['phase'], raised.exception.facts['http_status']), ('verify', 429))
            self.upload(root, self.store(root, request_state=request_state, max_requests=6), transport, payload)
            self.assertEqual(transport.verifications, 2)
            self.assertEqual(json.loads(request_state.read_text())['requests_upper'], 6)
            self.assertEqual([c['phase'] for c in transport.calls],
                             ['initiate', 'chunk', 'verify', 'initiate', 'chunk', 'verify'])

    def test_partial_308_seeks_actual_four_mib_persisted_offset(self):
        payload = b'x' * (8 * 1024**2 + 64)
        for mode in ('partial_308', 'response_object_308'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); transport = ProtocolTransport(payload, mode)
                self.upload(root, self.store(root), transport, payload)
                chunks = [c for c in transport.calls if c['phase'] == 'chunk']
                self.assertEqual(chunks[0]['range'], 'bytes 0-8388607/8388672')
                self.assertEqual(chunks[1]['range'], 'bytes 4194304-8388671/8388672')
                self.assertEqual(chunks[1]['bytes'], 4194368)
                self.assertEqual(transport.remote, payload)
                self.assertEqual(transport.verifications, 1)

    def test_invalid_missing_excess_backward_or_nonprogressing_range_refused(self):
        payload = b'x' * (8 * 1024**2 + 64)
        for mode in ('missing_range_308', 'invalid_range_308', 'negative_range_308', 'excess_range_308',
                     'backward_308', 'nonprogressing_308'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); transport = ProtocolTransport(payload, mode)
                with self.assertRaises(PersistenceError) as raised:
                    self.upload(root, self.store(root), transport, payload)
                self.assertEqual(raised.exception.facts['http_status'], 308)
                self.assertIn(raised.exception.facts['reason'], ('invalid_persisted_range', 'invalid_persisted_offset'))
                self.assertEqual(transport.verifications, 0)

    def test_premature_200_and_incomplete_final_308_are_not_verified(self):
        for payload, mode in ((b'x' * (8 * 1024**2 + 64), 'premature_200'),
                              (b'confirmed-bytes', 'incomplete_final_308')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); transport = ProtocolTransport(payload, mode)
                with self.assertRaises(PersistenceError) as raised:
                    self.upload(root, self.store(root), transport, payload)
                self.assertIn(raised.exception.facts['reason'],
                              ('unexpected_upload_completion', 'incomplete_final_upload_response'))
                self.assertEqual(transport.verifications, 0)

    def test_mutable_412_and_403_fail_without_reconciliation_or_secret_logs(self):
        payload = b'confirmed-bytes'
        for mode, immutable, status in (('post_412_match', False, 412), ('put_412_match', False, 412), ('put_403', True, 403)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); transport = ProtocolTransport(payload, mode)
                with self.assertRaises(PersistenceError) as raised:
                    self.upload(root, self.store(root), transport, payload, immutable)
                facts = persistence_error_facts(raised.exception)
                self.assertEqual((facts['phase'], facts['http_status']),
                                 ('initiate' if mode.startswith('post') else 'chunk', status))
                self.assertEqual(facts['object_sha256'], hashlib.sha256(payload).hexdigest())
                self.assertEqual(facts['object_bytes'], len(payload))
                encoded = json.dumps(facts)
                for secret in ('synthetic-token', 'private-session', 'private-response', 'synthetic-private-bucket',
                               'synthetic-private-prefix', 'https://', 'Authorization'):
                    self.assertNotIn(secret, encoded)
                self.assertEqual(transport.verifications, 0)
                self.assertEqual(len(transport.calls), 1 if mode.startswith('post') else 2)

    def test_expired_deadline_and_request_cap_make_no_extra_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); payload = b'confirmed-bytes'; transport = ProtocolTransport(payload, 'success')
            expired = self.store(root, deadline='2000-01-01T00:00:00Z',
                                 request_state=root/'expired.json', max_requests=10)
            with self.assertRaises(PersistenceError):
                self.upload(root, expired, transport, payload)
            self.assertEqual(transport.calls, [])
            self.assertFalse((root/'expired.json').exists())
            capped = self.store(root, request_state=root/'capped.json', max_requests=2)
            with self.assertRaises(PersistenceError) as raised:
                self.upload(root, capped, transport, payload)
            self.assertEqual(raised.exception.facts['phase'], 'verify')
            self.assertEqual(len(transport.calls), 2)
            reopened = self.store(root, request_state=root/'capped.json', max_requests=2)
            with self.assertRaises(PersistenceError):
                self.upload(root, reopened, transport, payload)
            self.assertEqual(len(transport.calls), 2)
            self.assertEqual(json.loads((root/'capped.json').read_text())['requests_upper'], 2)

    def test_transfer_cap_blocks_verification_and_preserves_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); payload = b'confirmed-bytes'; transport = ProtocolTransport(payload, 'success')
            store = self.store(root, transfer_state=root/'transfer.json', max_transfer_bytes=len(payload) - 1)
            with self.assertRaises(PersistenceError) as raised:
                self.upload(root, store, transport, payload)
            self.assertEqual(raised.exception.facts['phase'], 'verify')
            self.assertFalse((root/'transfer.json').exists())
            self.assertEqual(transport.verifications, 1)

    def test_actual_download_bounds_declared_and_unknown_length_before_excess_write(self):
        for headers in ({'Content-Length':'100'},{}):
            with self.subTest(headers=headers),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);store=self.store(root);target=root/'received'
                with patch('cloud_worker.urllib.request.urlopen',return_value=ProtocolResponse(b'x'*100,headers)):
                    with self.assertRaises(GuardError):store.download('bounded',target,max_bytes=3)
                self.assertEqual(target.stat().st_size,0)

    def test_remote_json_duplicate_binding_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=self.store(root);raw=b'{"codec":"raw-v1","codec":"deflate-chunks-v1"}'
            with patch('cloud_worker.urllib.request.urlopen',return_value=ProtocolResponse(raw,{'Content-Length':str(len(raw))})):
                with self.assertRaisesRegex(GuardError,'duplicate key'):store.get_json('descriptor')

    def test_periodic_cannot_spend_final_reserve_and_phase_fences_reopened_uploaders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);kwargs={'request_state':root/'requests.json','max_requests':12,'final_storage_requests_reserved':8}
            periodic=self.store(root,**kwargs)
            for _ in range(4):periodic.charge_request()
            with self.assertRaises(GuardError):periodic.charge_request()
            self.assertEqual(json.loads((root/'requests.json').read_text())['requests_upper'],4)
            final=self.store(root,**kwargs);final.begin_final()
            reopened=self.store(root,**kwargs)
            for uploader in (periodic,reopened):
                with self.assertRaises(GuardError):uploader.check_deadline()
                with self.assertRaises(GuardError):uploader.charge_request()
            for _ in range(8):final.charge_request()
            with self.assertRaises(GuardError):final.charge_request()
            self.assertEqual(json.loads((root/'requests.json').read_text())['requests_upper'],12)

    def test_final_byte_reserve_reuses_original_cap_and_survives_reopened_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);kwargs={'request_state':root/'requests.json','max_requests':12,
                                  'final_storage_requests_reserved':8,'final_storage_bytes_reserved':80}
            periodic=self.store(root,**kwargs);self.assertEqual(periodic.storage_byte_limit(100),20)
            reopened=self.store(root,**kwargs);self.assertEqual(reopened.storage_byte_limit(100),20)
            for invalid in (True,-1):
                with self.assertRaises(GuardError):self.store(root,**(kwargs|{'final_storage_bytes_reserved':invalid}))
            with self.assertRaises(GuardError):periodic.storage_byte_limit(80)
            final=self.store(root,**kwargs);final.begin_final();self.assertEqual(final.storage_byte_limit(100),100)
            with self.assertRaises(GuardError):reopened.storage_byte_limit(100)

    def test_metadata_hard_bound_writer_and_reservation_match_reader(self):
        from cloud_worker import reserve_metadata
        state={'metadata_sizes':{},'bytes':0};value={'large':'x'*MAX_DESCRIPTOR_BYTES}
        with self.assertRaises(GuardError):reserve_metadata(state,'snapshots/test.json',value,100_000_000)
        self.assertEqual(state,{'metadata_sizes':{},'bytes':0})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=self.store(root)
            with patch.object(store,'put_file',side_effect=AssertionError('upload before metadata admission')):
                with self.assertRaises(GuardError):store.put_json('oversize.json',value)

    def test_conflicting_snapshot_retains_ambiguous_bytes_without_latest_or_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'source'; source.mkdir(); payload = b'confirmed-bytes'
            (source/'final_state.json').write_bytes(payload)
            store = self.store(root); transport = ProtocolTransport(payload, 'put_412_mismatch')
            with patch('cloud_worker.urllib.request.urlopen', transport), self.assertRaises(PersistenceError):
                snapshot(source, store, root/'upload-state.json', 10000)
            state = json.loads((root/'upload-state.json').read_text())
            self.assertEqual(state['uploaded'], {hashlib.sha256(payload).hexdigest(): len(payload)})
            self.assertEqual(state['bytes'], len(payload))
            self.assertEqual(state['verified'], [])
            self.assertEqual(state['metadata_sizes'], {})
            self.assertEqual([c['phase'] for c in transport.calls], ['initiate', 'chunk', 'verify'])


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
    def reconcile_usage(self,state,max_bytes): return state
    def begin_final(self):pass
    def download(self,key,path,*,max_bytes=None):
        data=self.files[key]+(b'corruption' if self.corrupt else b'')
        if max_bytes is not None and len(data)>max_bytes:raise GuardError('fake bounded download refuses oversize data')
        Path(path).write_bytes(data)
        return hashlib.sha256(data).hexdigest()


class CloudWorkerTests(unittest.TestCase):
    def test_final_snapshot_retains_zero_length_lock_and_stable_working_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';case=source/'case';case.mkdir(parents=True);store=FakeStore()
            (source/'.pipeline.lock').touch()
            working=case/'.longitudinal-working.sqlite';working.write_bytes(b'stable-completed-working-state')
            (case/'final_state.sqlite').write_bytes(b'final-state')
            hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in case.iterdir()}
            (case/'manifest.json').write_text(json.dumps({'status':'running','output_sha256':hashes}))
            periodic=snapshot(source,store,root/'state.json',100_000)
            partial=store.get_json('snapshots/'+periodic+'.json')['files']
            self.assertNotIn('case/.longitudinal-working.sqlite',partial)
            self.assertEqual(partial['.pipeline.lock']['bytes'],0)
            final=final_snapshot(source,store,root/'state.json',100_000,'2099-01-01T00:00:00Z')
            descriptor=download_snapshot(store,final,root/'restored')
            self.assertEqual(descriptor['files']['case/.longitudinal-working.sqlite']['sha256'],hashes[working.name])
            self.assertEqual((root/'restored/case/.longitudinal-working.sqlite').read_bytes(),working.read_bytes())
            self.assertEqual((root/'restored/.pipeline.lock').stat().st_size,0)
            self.assertNotEqual(periodic,final)

    def test_periodic_snapshot_keeps_complete_case_exact_roster_and_excludes_running_working(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';complete=source/'complete';running=source/'running'
            complete.mkdir(parents=True);running.mkdir();store=FakeStore();(source/'.pipeline.lock').touch()
            working=complete/'.longitudinal-working.sqlite'
            with closing(sqlite3.connect(working)) as db,db:
                db.execute('CREATE TABLE fixture(value INTEGER)');db.execute('INSERT INTO fixture VALUES(42)')
            (complete/'final_state.sqlite').write_bytes(working.read_bytes())
            for name in ('summary.json','timeseries.csv','final_state.json','report.md','report.svg'):
                (complete/name).write_text('offline raw roster fixture\n')
            checkpoint=complete/'checkpoint-day-000003.json';checkpoint.write_text('{"day":3}')
            checkpoint.with_suffix('.sqlite').write_bytes(working.read_bytes())
            files=[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
                   for p in (checkpoint,checkpoint.with_suffix('.sqlite'))]
            (complete/'checkpoint-index.json').write_text(json.dumps({'snapshots':[{'file':checkpoint.name,
                                                    'sha256':files[0]['sha256'],'files':files}]}))
            hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in complete.iterdir()}
            (complete/'manifest.json').write_text(json.dumps({'status':'complete','output_sha256':hashes}))
            (running/'.longitudinal-working.sqlite').write_bytes(b'active-state-do-not-copy')
            (running/'manifest.json').write_text(json.dumps({'status':'running'}))
            sid=snapshot(source,store,root/'state.json',1_000_000)
            descriptor=download_snapshot(store,sid,root/'restored')
            self.assertIn('complete/.longitudinal-working.sqlite',descriptor['files'])
            self.assertNotIn('running/.longitudinal-working.sqlite',descriptor['files'])
            self.assertEqual(descriptor['working_databases'],{
                'complete_manifest_bound':['complete/.longitudinal-working.sqlite'],
                'include_after_pipeline_reaped':False,
                'included_uncompleted_after_reap':[],
                'excluded_active_or_uncompleted':['running/.longitudinal-working.sqlite']})
            restored=root/'restored/complete'
            self.assertEqual({p.name for p in restored.iterdir()},set(hashes)|{'manifest.json'})
            for name in set(hashes)|{'manifest.json'}:
                self.assertEqual((restored/name).read_bytes(),(complete/name).read_bytes())
            with closing(sqlite3.connect(f'file:{restored/working.name}?mode=ro',uri=True)) as db:
                self.assertEqual(db.execute('SELECT value FROM fixture').fetchone(),(42,))

    def test_periodic_complete_working_requires_precopy_complete_manifest_hash_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();store=FakeStore()
            (source/'.longitudinal-working.sqlite').write_bytes(b'complete-state')
            manifest=source/'manifest.json'
            for hashes in ({},{'.longitudinal-working.sqlite':'0'*64}):
                manifest.write_text(json.dumps({'status':'complete','output_sha256':hashes}))
                with self.subTest(hashes=hashes),self.assertRaises(GuardError):
                    snapshot(source,store,root/'state.json',100_000)
                self.assertNotIn('latest.json',store.files)

    def test_required_unsupported_hidden_output_and_sqlite_journals_cannot_publish_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();store=FakeStore()
            hidden=source/'.required-state.sqlite';hidden.write_bytes(b'needed')
            manifest=source/'manifest.json'
            manifest.write_text(json.dumps({'output_sha256':{hidden.name:hashlib.sha256(hidden.read_bytes()).hexdigest()}}))
            with self.assertRaisesRegex(GuardError,'required hidden'):
                snapshot(source,store,root/'state.json',100_000)
            self.assertNotIn('latest.json',store.files)
            manifest.unlink();hidden.unlink();(source/'summary.json').write_text('{}')
            (source/'.longitudinal-working.sqlite-wal').write_bytes(b'active-journal')
            sid=snapshot(source,store,root/'state.json',100_000)
            before=store.files['latest.json']
            self.assertNotIn('.longitudinal-working.sqlite-wal',store.get_json('snapshots/'+sid+'.json')['files'])
            with self.assertRaisesRegex(GuardError,'journal sidecars'):
                final_snapshot(source,store,root/'state.json',100_000,'2099-01-01T00:00:00Z')
            self.assertEqual(store.files['latest.json'],before)

    def test_checkpoint_group_requires_both_exact_hashes_and_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();store=FakeStore()
            primary=source/'checkpoint-day-000003.json';primary.write_text('{"day":3}')
            sidecar=primary.with_suffix('.sqlite');sidecar.write_bytes(b'immutable-ledger')
            files=[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
                   for p in (primary,sidecar)]
            record={'file':primary.name,'sha256':files[0]['sha256'],'files':files}
            index=source/'checkpoint-index.json'
            for bad in ('hash','bytes','missing','unexpected'):
                altered=deepcopy(record)
                if bad=='hash':altered['files'][1]['sha256']='0'*64
                if bad=='bytes':altered['files'][1]['bytes']+=1
                if bad=='missing':altered['files'].pop()
                if bad=='unexpected':altered['files'][1]['file']='different.sqlite'
                index.write_text(json.dumps({'snapshots':[altered]}))
                with self.subTest(bad=bad),self.assertRaises(GuardError):
                    snapshot(source,store,root/'state.json',100_000)
                self.assertNotIn('latest.json',store.files)
            index.write_text(json.dumps({'snapshots':[record]}))
            sid=snapshot(source,store,root/'state.json',100_000)
            self.assertIn(sidecar.name,store.get_json('snapshots/'+sid+'.json')['files'])

    def test_rewrite_after_copy_rejects_latest_even_if_size_and_mtime_are_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();path=source/'state.json';path.write_bytes(b'old-state')
            store=FakeStore();original=store.put_file;info=path.stat()
            def mutate(key,copied,sha,immutable=True):
                original(key,copied,sha,immutable)
                path.write_bytes(b'new-state');os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
            store.put_file=mutate
            with self.assertRaisesRegex(GuardError,'source (file|hash) changed'):
                snapshot(source,store,root/'state.json',100_000)
            self.assertNotIn('latest.json',store.files)

    def test_restore_upload_race_cannot_replace_complete_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();store=FakeStore()
            (source/'a').write_bytes(b'a');(source/'b').write_bytes(b'b')
            sid=snapshot(source,store,root/'first-ledger.json',10000);before=store.files['latest.json']
            original=store.download
            def concurrent_download(key,path,**kwargs):
                result=original(key,path,**kwargs)
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


class CompressedWorkerTests(unittest.TestCase):
    options={'transport_codec':CODEC,'archive_max_raw_bytes':32*1024**2,'archive_min_free_bytes':0}

    def fixture(self,root):
        source=root/'source';case=source/'case';case.mkdir(parents=True)
        (source/'.pipeline.lock').touch()
        for name in ('.longitudinal-working.sqlite','final_state.sqlite','checkpoint-day-000003.sqlite'):
            (case/name).write_bytes(b'exact-SQLite-fixture\x00'*1000)
        cp=case/'checkpoint-day-000003.json';cp.write_text('{"day":3}')
        files=[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
               for p in (cp,cp.with_suffix('.sqlite'))]
        (case/'checkpoint-index.json').write_text(json.dumps({'snapshots':[{'file':cp.name,'sha256':files[0]['sha256'],'files':files}]}))
        outputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in case.iterdir()}
        (case/'manifest.json').write_text(json.dumps({'status':'complete','output_sha256':outputs}))
        return source

    def test_compressed_complete_checkpoint_and_hidden_roster_restore_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root);store=FakeStore()
            sid=snapshot(source,store,root/'state.json',10_000_000,**self.options)
            desc=download_snapshot(store,sid,root/'restored',**self.options)
            self.assertEqual(desc['version'],2);self.assertEqual(desc['transport_codec'],CODEC)
            self.assertEqual(desc['working_databases']['complete_manifest_bound'],['case/.longitudinal-working.sqlite'])
            original={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}
            restored={p.relative_to(root/'restored').as_posix():p.read_bytes() for p in (root/'restored').rglob('*') if p.is_file()}
            self.assertEqual(original,restored)
            self.assertTrue((root/'restored-download-verification.json').is_file())
            self.assertFalse(any(key.startswith('blobs/') for key in store.files))

    def test_default_legacy_and_explicit_compressed_restore_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root);store=FakeStore()
            sid=snapshot(source,store,root/'state.json',10_000_000)
            self.assertEqual(store.get_json('snapshots/'+sid+'.json')['version'],1)
            download_snapshot(store,sid,root/'restored',**self.options)
            self.assertEqual((root/'restored/case/final_state.sqlite').read_bytes(),(source/'case/final_state.sqlite').read_bytes())
            self.assertFalse(any(key.startswith('chunks/') for key in store.files))

    def test_chunk_dedup_generations_and_partial_retry_reserve_actual_encoded_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();store=FakeStore();state=root/'state.json'
            (source/'a').write_bytes(b'x'*CHUNK_BYTES+b'a');(source/'b').write_bytes(b'x'*CHUNK_BYTES+b'b')
            store.fail=True
            with self.assertRaises(OSError):snapshot(source,store,state,10_000_000,**self.options)
            first_state=json.loads(state.read_text());self.assertEqual(len(first_state['uploaded']),1)
            self.assertEqual(first_state['verified'],[]);self.assertNotIn('latest.json',store.files)
            store.fail=False;sid=snapshot(source,store,state,10_000_000,**self.options)
            chunks=[key for key in store.files if key.startswith('chunks/')]
            self.assertEqual(len(chunks),3)
            calls=store.calls;same=snapshot(source,store,state,10_000_000,**self.options)
            self.assertEqual(sid,same);self.assertEqual(store.calls,calls)
            (source/'b').write_bytes(b'x'*CHUNK_BYTES+b'c')
            new=snapshot(source,store,state,10_000_000,**self.options)
            self.assertNotEqual(new,sid);self.assertIn('snapshots/'+sid+'.json',store.files)
            self.assertEqual(len([key for key in store.files if key.startswith('chunks/')]),4)
            download_snapshot(store,sid,root/'old',**self.options)
            self.assertTrue((root/'old/b').read_bytes().endswith(b'b'))
            used=json.loads(state.read_text());self.assertTrue(all(key.startswith(CODEC+':') for key in used['uploaded']))
            self.assertEqual(sum(used['uploaded'].values()),sum(len(store.files[key]) for key in store.files if key.startswith('chunks/')))

    def test_compressed_restore_rejects_missing_corrupt_and_wrong_codec_without_touching_old_tree(self):
        for fault in ('missing','corrupt','codec'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=self.fixture(root);store=FakeStore()
                sid=snapshot(source,store,root/'state.json',10_000_000,**self.options)
                destination=root/'old';destination.mkdir();(destination/'previous').write_bytes(b'committed')
                chunk=next(key for key in store.files if key.startswith('chunks/'))
                if fault=='missing':store.files.pop(chunk)
                if fault=='corrupt':store.files[chunk]=b'z'*len(store.files[chunk])
                options={} if fault=='codec' else self.options
                with self.assertRaises((GuardError,KeyError)):download_snapshot(store,sid,destination,**options)
                self.assertEqual(list(destination.iterdir()),[destination/'previous'])
                self.assertEqual((destination/'previous').read_bytes(),b'committed')
                self.assertFalse((root/'old-snapshot-history').exists())
                self.assertFalse(list(root.glob('old-restore-*')))

    def test_unknown_manifest_paths_offsets_extra_and_raw_cap_rejected_before_chunk_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root);store=FakeStore()
            sid=snapshot(source,store,root/'state.json',10_000_000,**self.options)
            good=store.get_json('snapshots/'+sid+'.json')
            mutations=[lambda d:d.update(transport_codec='unknown'),lambda d:d.update(version=3),
                       lambda d:d.update(extra=1),lambda d:d['files'].update({'../escape':next(iter(d['files'].values()))}),
                       lambda d:next(v for v in d['files'].values() if v['archive']['chunks'])['archive']['chunks'][0].update(offset=1)]
            from cloud_control import digest
            for mutate in mutations:
                bad=deepcopy(good);mutate(bad);bad['inventory_sha256']=digest(bad['files']);bad_id=digest(bad)
                store.files['snapshots/'+bad_id+'.json']=json.dumps(bad).encode()
                with patch.object(store,'download',side_effect=AssertionError('fetch occurred before admission')):
                    with self.assertRaises(GuardError):download_snapshot(store,bad_id,root/'bad',**self.options)
                self.assertFalse((root/'bad').exists())
            limited={**self.options,'archive_max_raw_bytes':1}
            with patch.object(store,'download',side_effect=AssertionError('fetch occurred before raw admission')):
                with self.assertRaises(GuardError):download_snapshot(store,sid,root/'bad',**limited)

    def test_capacity_and_bad_path_roster_do_not_mutate_existing_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root);store=FakeStore()
            sid=snapshot(source,store,root/'state.json',10_000_000,**self.options)
            destination=root/'old';destination.mkdir();(destination/'previous').write_bytes(b'committed')
            with patch('cloud_archive.shutil.disk_usage',return_value=type('Usage',(),{'free':0})()),patch.object(store,'download',side_effect=AssertionError('fetch before disk admission')):
                with self.assertRaises(GuardError):download_snapshot(store,sid,destination,**self.options)
            self.assertEqual((destination/'previous').read_bytes(),b'committed')
            self.assertFalse(list(root.glob('old-restore-*')))

    def test_restore_history_receipt_and_lock_symlinks_refused_before_io_without_external_changes(self):
        for suffix,kind in (('-snapshot-history','directory'),('-snapshot.lock','file'),('-download-verification.json','file')):
            with self.subTest(suffix=suffix),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=self.fixture(root);store=FakeStore()
                sid=snapshot(source,store,root/'state.json',10_000_000,**self.options)
                destination=root/'old';destination.mkdir();(destination/'previous').write_bytes(b'committed')
                external=root/'external'
                if kind=='directory':external.mkdir();(external/'sentinel').write_bytes(b'external')
                else:external.write_bytes(b'external');external.chmod(0o644)
                link=destination.with_name(destination.name+suffix);link.symlink_to(external,target_is_directory=kind=='directory')
                before=external.stat()
                with patch.object(store,'get_json',side_effect=AssertionError('remote I/O before local path admission')):
                    with self.assertRaises(GuardError):download_snapshot(store,sid,destination,**self.options)
                self.assertEqual((destination/'previous').read_bytes(),b'committed');self.assertTrue(link.is_symlink())
                if kind=='directory':self.assertEqual(list(external.iterdir()),[external/'sentinel'])
                else:self.assertEqual(external.read_bytes(),b'external')
                self.assertEqual(external.stat().st_mode,before.st_mode)
                self.assertEqual(external.stat().st_mtime_ns,before.st_mtime_ns)

    def test_snapshot_state_and_lock_symlinks_refused_without_external_chmod_or_upload(self):
        for label in ('state','ledger-lock','source-lock'):
            with self.subTest(label=label),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=self.fixture(root);store=FakeStore();state=root/'state.json'
                external=root/'external';external.write_bytes(b'external');external.chmod(0o644)
                paths={'state':state,'ledger-lock':state.with_suffix('.lock'),'source-lock':source.with_name(source.name+'-snapshot.lock')}
                paths[label].symlink_to(external);before=external.stat()
                with self.assertRaises(GuardError):snapshot(source,store,state,10_000_000,**self.options)
                self.assertEqual(store.files,{});self.assertEqual(external.read_bytes(),b'external')
                self.assertEqual(external.stat().st_mode,before.st_mode)

    def test_ancestor_symlink_refused_before_resolve_for_restore_source_state_and_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root);store=FakeStore()
            sid=snapshot(source,store,root/'state.json',10_000_000,**self.options)
            outside=root/'external';outside.mkdir();alias=root/'alias';alias.symlink_to(outside,target_is_directory=True)
            destination=outside/'restored';destination.mkdir();(destination/'old').write_bytes(b'committed')
            with patch.object(store,'get_json',side_effect=AssertionError('remote I/O before ancestor admission')):
                with self.assertRaises(GuardError):download_snapshot(store,sid,alias/'restored',**self.options)
            self.assertEqual(list(outside.iterdir()),[destination]);self.assertEqual((destination/'old').read_bytes(),b'committed')
            outside_source=self.fixture(outside);lock=outside_source.with_name(outside_source.name+'-snapshot.lock');lock.write_bytes(b'');lock.chmod(0o644)
            before=lock.stat();other=FakeStore()
            with self.assertRaises(GuardError):snapshot(alias/'source',other,alias/'state.json',10_000_000,**self.options)
            self.assertEqual(other.files,{});self.assertEqual(lock.stat().st_mode,before.st_mode);self.assertFalse((outside/'state.json').exists())

    def test_whole_roster_addition_during_upload_refuses_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();(source/'a').write_bytes(b'a');store=FakeStore();original=store.put_file
            def add(key,path,sha,immutable=True):
                original(key,path,sha,immutable);(source/'b').write_bytes(b'b')
            with patch.object(store,'put_file',add),self.assertRaisesRegex(GuardError,'roster changed'):
                snapshot(source,store,root/'state.json',10_000_000,**self.options)
            self.assertNotIn('latest.json',store.files)

    def test_zero_inodes_and_file_roster_cap_refuse_before_download_or_upload(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root);store=FakeStore()
            with self.assertRaises(GuardError):snapshot(source,store,root/'too-small.json',10_000_000,**(self.options|{'archive_max_files':1}))
            self.assertEqual(store.files,{})
            sid=snapshot(source,store,root/'state.json',10_000_000,**self.options)
            with patch('cloud_worker.os.statvfs',return_value=SimpleNamespace(f_favail=0,f_frsize=4096)),patch.object(store,'download',side_effect=AssertionError('download with zero inodes')):
                with self.assertRaisesRegex(GuardError,'inode capacity'):download_snapshot(store,sid,root/'restored',**self.options)
            self.assertFalse((root/'restored').exists())

    def test_snapshot_source_rewrite_keeps_previous_pointer_and_retains_ambiguous_chunk_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();path=source/'raw';path.write_bytes(b'previous')
            store=FakeStore();state=root/'state.json';sid=snapshot(source,store,state,10_000_000,**self.options)
            pointer=store.files['latest.json'];original=store.put_file
            path.write_bytes(b'new-content')
            def mutate(key,encoded,sha,immutable=True):
                original(key,encoded,sha,immutable)
                before=path.stat();path.write_bytes(b'z'*before.st_size);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
            with patch.object(store,'put_file',mutate),self.assertRaises(GuardError):
                snapshot(source,store,state,10_000_000,**self.options)
            self.assertEqual(store.files['latest.json'],pointer)
            download_snapshot(store,sid,root/'old',**self.options)
            self.assertEqual((root/'old/raw').read_bytes(),b'previous')
            self.assertGreater(len(json.loads(state.read_text())['uploaded']),1)

    def test_frozen_options_cli_selection_and_upload_real_codec(self):
        for runtime in ({'transport_codec':'unknown'},{'transport_codec':CODEC},
                        {'transport_codec':CODEC,'archive_max_raw_bytes':100,'archive_min_free_bytes':True}):
            with self.assertRaises(GuardError):archive_options(runtime)
        self.assertEqual(archive_options({}),{'transport_codec':RAW_CODEC})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'work';output=work/'output';output.mkdir(parents=True);(output/'raw').write_bytes(b'raw bytes')
            runtime={**self.options,'bucket':'fake','prefix':'fake','deadline_utc':'2099-01-01T00:00:00Z',
                     'max_storage_requests':100,'max_upload_bytes':1_000_000}
            path=root/'runtime.json';path.write_text(json.dumps(runtime));store=FakeStore()
            with patch('cloud_worker.Store',return_value=store):
                self.assertEqual(main(['upload','--runtime',str(path),'--work',str(work),'--transport-codec',CODEC]),0)
                with self.assertRaises(GuardError):main(['upload','--runtime',str(path),'--work',str(work),'--transport-codec',RAW_CODEC])
            sid=store.get_json('latest.json')['snapshot'];self.assertEqual(store.get_json('snapshots/'+sid+'.json')['transport_codec'],CODEC)

    def test_final_partial_compressed_envelope_preserves_incomplete_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root);case=source/'case';store=FakeStore()
            manifest=json.loads((case/'manifest.json').read_text());manifest['status']='failed';(case/'manifest.json').write_text(json.dumps(manifest))
            periodic=snapshot(source,store,root/'state.json',10_000_000,**self.options)
            self.assertNotIn('case/.longitudinal-working.sqlite',store.get_json('snapshots/'+periodic+'.json')['files'])
            sid=final_snapshot(source,store,root/'state.json',10_000_000,'2099-01-01T00:00:00Z',**self.options)
            desc=download_snapshot(store,sid,root/'restored',**self.options)
            self.assertEqual(desc['working_databases']['included_uncompleted_after_reap'],['case/.longitudinal-working.sqlite'])
            self.assertEqual(json.loads((root/'restored/case/manifest.json').read_text())['status'],'failed')


class InterruptedEvidenceTests(unittest.TestCase):
    options={'transport_codec':CODEC,'archive_max_raw_bytes':32*1024**2,'archive_min_free_bytes':0,'archive_max_files':8192}

    def fixture(self,root):
        source=CompressedWorkerTests().fixture(root)
        failed=source/'interrupted';failed.mkdir()
        (failed/'.longitudinal-working.sqlite').write_bytes(b'not-a-certified-database')
        for suffix in ('-wal','-shm','-journal'):
            (failed/('.longitudinal-working.sqlite'+suffix)).write_bytes(b'stable-hot-sidecar'+suffix.encode())
        (failed/'checkpoint-index.json').write_bytes(b'{interrupted invalid JSON')
        (failed/'manifest.json').write_text(json.dumps({'status':'failed','output_sha256':{'.longitudinal-working.sqlite':'0'*64}}))
        (failed/'.partial.tmp').write_bytes(b'exact hidden temporary evidence')
        (failed/'output.atomic').write_bytes(b'exact atomic temporary evidence')
        return source

    def publish(self,source,store,state,**options):
        with patch('cloud_worker.os.killpg',side_effect=ProcessLookupError):
            return final_snapshot(source,store,state,10_000_000,'2099-01-01T00:00:00Z',
                                  preserve_interrupted_evidence=True,owned_pipeline_group_id=12345,**options)

    def test_corrupted_metadata_hot_sidecars_and_valid_prior_checkpoint_are_exact_evidence(self):
        for options in ({},self.options):
            with self.subTest(codec=options.get('transport_codec',RAW_CODEC)),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=self.fixture(root);store=FakeStore()
                original={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}
                sid=self.publish(source,store,root/'state.json',**options)
                descriptor=store.get_json('snapshots/'+sid+'.json')
                self.assertEqual(descriptor['kind'],'interrupted-evidence-v1')
                self.assertIs(descriptor['science_complete'],False)
                self.assertEqual(descriptor['sqlite_evidence_status'],'UNVALIDATED_SQLITE_EVIDENCE')
                self.assertNotIn('working_databases',descriptor)
                self.assertIn('interrupted/checkpoint-index.json',descriptor['unvalidated_metadata'])
                self.assertIn('interrupted/.longitudinal-working.sqlite-wal',descriptor['unvalidated_sqlite_evidence'])
                self.assertIn('case/checkpoint-day-000003.sqlite',descriptor['unvalidated_sqlite_evidence'])
                destination=root/'restored';destination.mkdir();(destination/'old-tree').write_bytes(b'keep until successful explicit collection')
                with self.assertRaisesRegex(GuardError,'cannot be automatically resumed'):
                    download_snapshot(store,sid,destination,**options)
                self.assertEqual((destination/'old-tree').read_bytes(),b'keep until successful explicit collection')
                download_snapshot(store,sid,destination,allow_interrupted_evidence=True,**options)
                restored={p.relative_to(destination).as_posix():p.read_bytes() for p in destination.rglob('*') if p.is_file()}
                self.assertEqual(original,restored)
                self.assertTrue(json.loads((root/'restored-download-verification.json').read_text())['evidence_only'])

    def test_forensic_capture_requires_actual_owned_group_absence_and_exact_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root)
            for outcome in (None,PermissionError()):
                store=FakeStore()
                with patch('cloud_worker.os.killpg',side_effect=outcome) as probe:
                    with self.assertRaises(GuardError):
                        final_snapshot(source,store,root/'state.json',10_000_000,'2099-01-01T00:00:00Z',
                                       preserve_interrupted_evidence=True,owned_pipeline_group_id=12345,**self.options)
                probe.assert_called_once_with(12345,0);self.assertEqual(store.files,{})
            for invalid in ('true',1,None):
                with self.assertRaises(GuardError):
                    final_snapshot(source,FakeStore(),root/'state.json',10_000_000,'2099-01-01T00:00:00Z',
                                   preserve_interrupted_evidence=invalid,owned_pipeline_group_id=12345,**self.options)
            with self.assertRaises(GuardError):
                snapshot(source,FakeStore(),root/'state.json',10_000_000,preserve_interrupted_evidence=True,**self.options)

    def test_real_owned_subprocess_is_absent_before_evidence_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root);store=FakeStore()
            process=subprocess.Popen([sys.executable,'-c','pass'],start_new_session=True)
            process.wait(timeout=5)
            sid=final_snapshot(source,store,root/'state.json',10_000_000,'2099-01-01T00:00:00Z',
                               preserve_interrupted_evidence=True,owned_pipeline_group_id=process.pid,**self.options)
            proof=store.get_json('snapshots/'+sid+'.json')['owned_pipeline_group_verification']
            self.assertEqual(proof['owned_pipeline_group_id'],process.pid);self.assertTrue(proof['owned_pipeline_group_absent'])

    def test_forensic_roster_and_raw_caps_include_temporary_hidden_and_journal_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root);actual=[p for p in source.rglob('*') if p.is_file()]
            for extra in ({'archive_max_files':len(actual)-1},{'archive_max_raw_bytes':sum(p.stat().st_size for p in actual)-1}):
                store=FakeStore()
                with self.assertRaises(GuardError):self.publish(source,store,root/'state.json',**(self.options|extra))
                self.assertEqual(store.files,{})

    def test_evidence_bindings_cannot_drop_unvalidated_markers_or_claim_complete(self):
        from cloud_control import digest
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.fixture(root);store=FakeStore();sid=self.publish(source,store,root/'state.json',**self.options)
            descriptor=store.get_json('snapshots/'+sid+'.json')
            for mutation in ({'science_complete':True},{'unvalidated_sqlite_evidence':[]},{'unvalidated_metadata':[]},
                             {'sqlite_evidence_status':'VALID_STATE'}):
                bad=descriptor|mutation;bad_sid=digest(bad);store.put_json('snapshots/'+bad_sid+'.json',bad)
                with self.assertRaises(GuardError):download_snapshot(store,bad_sid,root/'bad',allow_interrupted_evidence=True,**self.options)
                self.assertFalse((root/'bad').exists())
            for invalid in ('true',1):
                with self.assertRaises(GuardError):download_snapshot(store,sid,root/'bad',allow_interrupted_evidence=invalid,**self.options)

    def test_periodic_snapshot_cannot_consume_encoded_final_byte_share(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();(source/'raw').write_bytes(b'x'*30_000)
            guard=Store('synthetic','offline',request_state=root/'requests.json',max_requests=100,
                        final_storage_requests_reserved=80,final_storage_bytes_reserved=80_000)
            store=FakeStore();store.storage_byte_limit=guard.storage_byte_limit;store.check_deadline=guard.check_deadline;store.begin_final=guard.begin_final
            with self.assertRaisesRegex(GuardError,'immutable upload bytes'):
                snapshot(source,store,root/'state.json',100_000)
            self.assertEqual(store.files,{})
            sid=final_snapshot(source,store,root/'state.json',100_000,'2099-01-01T00:00:00Z')
            self.assertEqual(store.get_json('snapshots/'+sid+'.json')['files']['raw']['bytes'],30_000)
            self.assertLessEqual(json.loads((root/'state.json').read_text())['bytes'],100_000)

    def test_inflight_periodic_snapshot_releases_tree_when_final_phase_begins(self):
        for options in ({},self.options):
            with self.subTest(codec=options.get('transport_codec',RAW_CODEC)),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=CompressedWorkerTests().fixture(root);kwargs={'request_state':root/'requests.json','max_requests':100}
                periodic=Store('synthetic','offline',**kwargs);final=Store('synthetic','offline',**kwargs)
                store=FakeStore();store.check_deadline=periodic.check_deadline;state=root/'state.json'
                old=snapshot(source,store,state,30_000_000,**options)
                (source/'new-raw').write_bytes(b'fresh bounded stream'*(CHUNK_BYTES//10))
                original_put=store.put_file
                def start_final(*args,**kwargs):
                    original_put(*args,**kwargs);final.begin_final()
                store.put_file=start_final
                with self.assertRaisesRegex(GuardError,'fenced periodic'):
                    snapshot(source,store,state,30_000_000,**options)
                self.assertEqual(store.get_json('latest.json')['snapshot'],old)
                store.put_file=original_put;store.check_deadline=final.check_deadline;store.begin_final=final.begin_final
                sid=final_snapshot(source,store,state,30_000_000,'2099-01-01T00:00:00Z',**options)
                self.assertNotEqual(sid,old)


class GuestCapabilityTests(unittest.TestCase):
    def profile(self):
        return {'purchase_mode':'STANDARD','machine_type':'m3-ultramem-32',
                'expected_guest':{'architecture':'x86_64','vcpus':32,'memory_gib_min':'970','memory_gib_max':'976'},
                'runtime_limits':{'provenance':{'machine_type':'m3-ultramem-32'}}}

    def measurements(self):
        return {'instance_id':'12345','machine_type':'m3-ultramem-32','zone':'us-central1-a',
                'guest_scheduling':{'preemptible':'FALSE','automatic_restart':'FALSE','on_host_maintenance':'TERMINATE'},
                'guest_architecture':'x86_64','guest_cpu_count':32,'guest_cpu_affinity':list(range(32)),
                'guest_memory_total_bytes':973*2**30,'guest_memory_total_source':'Linux MemTotal',
                'guest_boot_disk_metadata_interface':'NVME',
                'guest_root_block':{'root_device':'259:1','interface':'NVME','nvme_namespaces':['nvme0n1']}}

    def test_frozen_standard_receipt_contains_actual_measurements(self):
        result=verify_guest(self.profile(),self.measurements())
        self.assertTrue(result['hardware_frozen_verified'])
        self.assertEqual(result['guest_memory_total_bytes'],973*2**30)
        self.assertEqual(result['guest_cpu_affinity_count'],32)
        self.assertEqual(result['guest_root_block']['nvme_namespaces'],['nvme0n1'])
        self.assertIn('coordinator',result['mode_evidence'])

    def test_mode_machine_architecture_cpu_affinity_ram_and_disk_mismatches_refuse(self):
        changes=[('guest_scheduling',{'preemptible':'TRUE'}),('machine_type','m3-ultramem-128'),
                 ('guest_architecture','aarch64'),('guest_cpu_count',31),('guest_cpu_affinity',list(range(31))),
                 ('guest_cpu_affinity',[0]*32),('guest_memory_total_bytes',969*2**30),
                 ('guest_memory_total_bytes',977*2**30),('guest_boot_disk_metadata_interface','SCSI'),
                 ('guest_root_block',{'interface':'unverified'})]
        for key,value in changes:
            measurements=self.measurements();measurements[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(GuardError):
                verify_guest(self.profile(),measurements)
        for key,value in [('automatic_restart','TRUE'),('on_host_maintenance','MIGRATE')]:
            measurements=self.measurements();measurements['guest_scheduling'][key]=value
            with self.subTest(key=key),self.assertRaises(GuardError):verify_guest(self.profile(),measurements)

    def test_explicit_standard_cannot_omit_frozen_guest_and_legacy_spot_is_unbound(self):
        runtime=self.profile();runtime.pop('expected_guest')
        with self.assertRaises(GuardError):verify_guest(runtime,self.measurements())
        measurements=self.measurements();measurements['machine_type']='c4d-highmem-4'
        measurements['guest_scheduling']['preemptible']='TRUE'
        legacy={'runtime_limits':{'provenance':{'machine_type':'c4d-highmem-4'}}}
        self.assertFalse(verify_guest(legacy,measurements)['hardware_frozen_verified'])
        measurements['guest_scheduling']['preemptible']='FALSE'
        with self.assertRaises(GuardError):verify_guest(legacy,measurements)
        unsupported={'purchase_mode':'SPOT','machine_type':'m3-ultramem-32'}
        measurements['guest_scheduling']['preemptible']='TRUE';measurements['machine_type']='m3-ultramem-32'
        with self.assertRaises(GuardError):verify_guest(unsupported,measurements)

    def test_memory_uses_physical_memtotal_and_refuses_ambiguous_or_invalid_data(self):
        self.assertEqual(guest_memory('MemTotal: 1024 kB\nMemAvailable: 512 kB\n'),(2**20,'Linux MemTotal'))
        for value in ('MemAvailable: 1024 kB','MemTotal: 1 MB','MemTotal: 0 kB','MemTotal: 1 kB\nMemTotal: 2 kB'):
            with self.subTest(value=value),self.assertRaises(GuardError):guest_memory(value)
        with patch('cloud_worker.os.sysconf',side_effect=[100,4096]):
            self.assertEqual(guest_memory(None),(409600,'sysconf physical pages'))

    def test_root_nvme_partition_and_all_mapper_slaves_require_controller_ancestry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sysdev=root/'dev/block';sysdev.mkdir(parents=True)
            namespace=root/'devices/pci/nvme/nvme0/nvme0n1';partition=namespace/'nvme0n1p1';partition.mkdir(parents=True)
            # Use a major representable on both Darwin (test host) and Linux.
            (sysdev/'75:1').symlink_to(partition)
            direct=root_block_interface(sysdev,os.makedev(75,1))
            self.assertEqual(direct['interface'],'NVME');self.assertEqual(direct['nvme_namespaces'],['nvme0n1'])
            mapper=root/'devices/virtual/block/dm-0';(mapper/'slaves').mkdir(parents=True)
            (mapper/'slaves/nvme0n1p1').symlink_to(partition);(sysdev/'253:0').symlink_to(mapper)
            self.assertEqual(root_block_interface(sysdev,os.makedev(253,0))['interface'],'NVME')
            scsi=root/'devices/pci/host0/block/sda';scsi.mkdir(parents=True)
            (mapper/'slaves/sda').symlink_to(scsi)
            self.assertEqual(root_block_interface(sysdev,os.makedev(253,0))['interface'],'unverified')
            self.assertEqual(root_block_interface(sysdev,os.makedev(99,99))['interface'],'unverified')
            impostor=root/'devices/virtual/block/nvme1n1';impostor.mkdir(parents=True)
            (sysdev/'75:2').symlink_to(impostor)
            self.assertEqual(root_block_interface(sysdev,os.makedev(75,2))['interface'],'unverified')

    def test_guest_metadata_collects_documented_scheduling_and_actual_hardware(self):
        values={'id':'12345','machine-type':'projects/0/machineTypes/m3-ultramem-32','zone':'projects/0/zones/us-central1-a',
                'scheduling/preemptible':'FALSE','scheduling/automatic-restart':'FALSE',
                'scheduling/on-host-maintenance':'TERMINATE','disks/0/interface':'NVME'}
        with patch('cloud_worker.instance_metadata',side_effect=values.__getitem__) as metadata, \
             patch('cloud_worker.platform.machine',return_value='x86_64'), \
             patch('cloud_worker.os.cpu_count',return_value=32), \
             patch('cloud_worker.os.sched_getaffinity',return_value=set(range(32)),create=True), \
             patch('cloud_worker.guest_memory',return_value=(973*2**30,'Linux MemTotal')), \
             patch('cloud_worker.root_block_interface',return_value=self.measurements()['guest_root_block']), \
             patch('cloud_worker.subprocess.run',return_value=Mock(returncode=0,stdout='{}')):
            measured=guest_metadata()
        self.assertEqual(set(c.args[0] for c in metadata.call_args_list),set(values))
        self.assertTrue(verify_guest(self.profile(),measured)['hardware_frozen_verified'])

    def test_invalid_hardware_never_constructs_store_or_launches_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            measurements=self.measurements();measurements['guest_cpu_count']=4
            with patch('cloud_worker.guest_metadata',return_value=measurements), \
                 patch('cloud_worker.Store') as store,patch('cloud_worker.subprocess.Popen') as launch:
                with self.assertRaises(GuardError):run_guest(self.profile(),ROOT,Path(tmp)/'work')
            store.assert_not_called();launch.assert_not_called()
            self.assertFalse((Path(tmp)/'work/guest-hardware-verification.json').exists())

    def test_valid_guest_receipt_is_written_before_fake_pipeline_and_final_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp)/'work';runtime=self.profile();store=FakeStore()
            runtime.update(bucket='synthetic',prefix='offline',deadline_utc='2099-01-01T00:00:00Z',
                           max_storage_requests=100,max_upload_bytes=1_000_000,source_commit='a'*40,
                           spec='longitudinal-adoption-5y',scale=1000)
            process=Mock(pid=12345);process.poll.return_value=0;process.wait.return_value=0
            def launch(*args,**kwargs):
                receipt=json.loads((work/'output/cloud-execution.json').read_text())
                self.assertTrue(receipt['guest_hardware_verification']['hardware_frozen_verified'])
                self.assertEqual(receipt['guest_memory_total_bytes'],973*2**30)
                self.assertEqual(args[0][args[0].index('--spec')+1],'longitudinal-adoption-5y')
                return process
            with patch('cloud_worker.guest_metadata',return_value=self.measurements()), \
                 patch('cloud_worker.Store',return_value=store),patch('cloud_worker.subprocess.Popen',side_effect=launch), \
                 patch('cloud_worker.signal.signal'),patch('cloud_worker.io_cpu_snapshot',return_value={}), \
                 patch('cloud_worker.os.killpg',side_effect=ProcessLookupError):
                self.assertEqual(run_guest(runtime,ROOT,work),0)
            terminal=json.loads((work/'output/cloud-terminal.json').read_text())
            self.assertTrue(terminal['guest_hardware_verification']['hardware_frozen_verified'])
            self.assertTrue(terminal['owned_pipeline_group_verification']['owned_pipeline_group_absent'])
            self.assertEqual(store.get_json('terminal.json')['exit_code'],0)
            self.assertEqual(set(runtime['runtime_limits']['provenance']),{'machine_type','instance_id','zone','environment'})

    def test_failed_child_is_reaped_before_final_raw_snapshot_and_retains_partial_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp)/'work';runtime=self.profile();store=FakeStore()
            runtime.update(bucket='synthetic',prefix='offline',deadline_utc='2099-01-01T00:00:00Z',
                           max_storage_requests=100,max_upload_bytes=1_000_000,source_commit='a'*40,
                           spec='longitudinal-adoption-5y',scale=1000)
            process=Mock(pid=12345);process.poll.return_value=1;process.wait.return_value=1
            def launch(*args,**kwargs):
                case=work/'output/failed-case';case.mkdir()
                (case/'.longitudinal-working.sqlite').write_bytes(b'failed-child-stable-raw')
                (case/'manifest.json').write_text(json.dumps({'status':'failed','exit_code':1}))
                return process
            def final(*args,**kwargs):
                process.wait.assert_called()
                return final_snapshot(*args,**kwargs)
            with patch('cloud_worker.guest_metadata',return_value=self.measurements()), \
                 patch('cloud_worker.Store',return_value=store),patch('cloud_worker.subprocess.Popen',side_effect=launch), \
                 patch('cloud_worker.signal.signal'),patch('cloud_worker.io_cpu_snapshot',return_value={}), \
                 patch('cloud_worker.final_snapshot',side_effect=final), \
                 patch('cloud_worker.os.killpg',side_effect=ProcessLookupError):
                self.assertEqual(run_guest(runtime,ROOT,work),1)
            terminal=store.get_json('terminal.json');self.assertEqual(terminal['exit_code'],1)
            descriptor=store.get_json('snapshots/'+terminal['snapshot']+'.json')
            self.assertEqual(descriptor['working_databases']['included_uncompleted_after_reap'],
                             ['failed-case/.longitudinal-working.sqlite'])
            self.assertEqual(descriptor['working_databases']['complete_manifest_bound'],[])
            self.assertEqual(json.loads((work/'output/failed-case/manifest.json').read_text())['status'],'failed')

    def test_explicit_failed_attempt_preserves_forensics_but_guest_never_auto_resumes_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp)/'work';runtime=self.profile();store=FakeStore()
            runtime.update(bucket='synthetic',prefix='offline',deadline_utc='2099-01-01T00:00:00Z',
                           max_storage_requests=100,max_upload_bytes=1_000_000,source_commit='a'*40,
                           spec='longitudinal-adoption-5y',scale=1000,preserve_interrupted_evidence=True,
                           final_storage_requests_reserved=80,final_storage_bytes_reserved=800_000)
            process=Mock(pid=12345);process.poll.return_value=1;process.wait.return_value=1
            def launch(*args,**kwargs):
                case=work/'output/failed-case';case.mkdir()
                (case/'.longitudinal-working.sqlite').write_bytes(b'failed-child-stable-raw')
                (case/'.longitudinal-working.sqlite-journal').write_bytes(b'hot-journal-never-valid-state')
                (case/'checkpoint-index.json').write_bytes(b'broken-index')
                return process
            with patch('cloud_worker.guest_metadata',return_value=self.measurements()), \
                 patch('cloud_worker.Store',return_value=store),patch('cloud_worker.subprocess.Popen',side_effect=launch), \
                 patch('cloud_worker.signal.signal'),patch('cloud_worker.io_cpu_snapshot',return_value={}), \
                 patch('cloud_worker.os.killpg',side_effect=ProcessLookupError):
                self.assertEqual(run_guest(runtime,ROOT,work),1)
            terminal=store.get_json('terminal.json');descriptor=store.get_json('snapshots/'+terminal['snapshot']+'.json')
            self.assertEqual(descriptor['kind'],'interrupted-evidence-v1');self.assertIs(descriptor['science_complete'],False)
            original={p.relative_to(work/'output').as_posix():p.read_bytes() for p in (work/'output').rglob('*') if p.is_file()}
            with patch('cloud_worker.guest_metadata',return_value=self.measurements()), \
                 patch('cloud_worker.Store',return_value=store),patch('cloud_worker.subprocess.Popen') as launch:
                with self.assertRaisesRegex(GuardError,'cannot be automatically resumed'):
                    run_guest(runtime,ROOT,work)
                launch.assert_not_called()
            self.assertEqual(original,{p.relative_to(work/'output').as_posix():p.read_bytes() for p in (work/'output').rglob('*') if p.is_file()})

    def test_explicit_pipeline_grace_uses_scientific_soft_deadline_and_absolute_reap_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp)/'work';runtime=self.profile();store=FakeStore()
            runtime.update(bucket='synthetic',prefix='offline',deadline_utc='2099-01-01T02:00:00Z',
                           max_storage_requests=100,max_upload_bytes=1_000_000,source_commit='a'*40,
                           spec='longitudinal-adoption-5y',scale=1000,shutdown_margin_seconds=3600,
                           pipeline_stop_grace_seconds=360,preserve_interrupted_evidence=True,
                           final_storage_requests_reserved=80,final_storage_bytes_reserved=800_000)
            runtime['runtime_limits'].update(deadline_utc='2099-01-01T00:54:00Z',stop_cutoff_utc='2099-01-01T01:00:00Z',
                                             cooperative_stop_grace_seconds=300)
            from cloud_control import utc
            now=utc('2099-01-01T00:54:00Z').timestamp();process=Mock(pid=12345)
            process.poll.return_value=None;process.wait.side_effect=[subprocess.TimeoutExpired('owned pipeline',355),1,1]
            def signal_group(pid,sig):
                if sig==0:raise ProcessLookupError
            with patch('cloud_worker.guest_metadata',return_value=self.measurements()), \
                 patch('cloud_worker.Store',return_value=store),patch('cloud_worker.subprocess.Popen',return_value=process), \
                 patch('cloud_worker.signal.signal'),patch('cloud_worker.io_cpu_snapshot',return_value={}), \
                 patch('cloud_worker.time.time',return_value=now),patch('cloud_worker.os.killpg',side_effect=signal_group) as signals:
                self.assertEqual(run_guest(runtime,ROOT,work),1)
            self.assertEqual(process.wait.call_args_list[0].kwargs['timeout'],355)
            self.assertEqual(process.wait.call_args_list[1].kwargs['timeout'],5)
            self.assertEqual([(c.args[0],c.args[1]) for c in signals.call_args_list[:2]],[(12345,signal.SIGTERM),(12345,signal.SIGKILL)])

    def test_invalid_final_shares_or_stop_deadlines_refuse_before_pipeline_and_store(self):
        runtime=self.profile();runtime.update(deadline_utc='2099-01-01T02:00:00Z',shutdown_margin_seconds=3600,
            pipeline_stop_grace_seconds=360,preserve_interrupted_evidence=True,final_storage_requests_reserved=80,
            final_storage_bytes_reserved=800_000,max_storage_requests=100,max_upload_bytes=1_000_000)
        runtime['runtime_limits'].update(deadline_utc='2099-01-01T00:54:00Z',stop_cutoff_utc='2099-01-01T01:00:00Z',cooperative_stop_grace_seconds=300)
        invalids=[{'final_storage_bytes_reserved':0},{'final_storage_requests_reserved':True},
                  {'final_storage_bytes_reserved':1_000_000},{'preserve_interrupted_evidence':'true'},
                  {'runtime_limits':runtime['runtime_limits']|{'stop_cutoff_utc':'2099-01-01T01:00:01Z'}},
                  {'runtime_limits':runtime['runtime_limits']|{'deadline_utc':'2099-01-01T00:54:01Z'}},
                  {'runtime_limits':runtime['runtime_limits']|{'stop_cutoff_utc':None}}]
        with tempfile.TemporaryDirectory() as tmp:
            for invalid in invalids:
                with self.subTest(invalid=invalid),patch('cloud_worker.Store') as store,patch('cloud_worker.subprocess.Popen') as launch:
                    with self.assertRaises(GuardError):run_guest(runtime|invalid,ROOT,Path(tmp)/'work')
                    launch.assert_not_called();store.assert_not_called()

    def test_manual_term_before_soft_deadline_starts_bounded_checkpoint_grace_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp)/'work';runtime=self.profile();store=FakeStore()
            runtime.update(bucket='synthetic',prefix='offline',deadline_utc='2099-01-01T02:00:00Z',
                           max_storage_requests=100,max_upload_bytes=1_000_000,source_commit='a'*40,
                           spec='longitudinal-adoption-5y',scale=1000,shutdown_margin_seconds=3630,pipeline_stop_grace_seconds=360)
            runtime['runtime_limits'].update(deadline_utc='2099-01-01T00:53:30Z',stop_cutoff_utc='2099-01-01T00:59:30Z',cooperative_stop_grace_seconds=300)
            process=Mock(pid=12345);process.poll.return_value=None;process.wait.return_value=1
            from cloud_control import utc
            def register(sig,handler):
                if sig==signal.SIGTERM:handler(sig,None)
            def probe(pid,sig):
                if sig==0:raise ProcessLookupError
            with patch('cloud_worker.guest_metadata',return_value=self.measurements()), \
                 patch('cloud_worker.Store',return_value=store),patch('cloud_worker.subprocess.Popen',return_value=process), \
                 patch('cloud_worker.signal.signal',side_effect=register),patch('cloud_worker.io_cpu_snapshot',return_value={}), \
                 patch('cloud_worker.time.time',return_value=utc('2099-01-01T00:01:00Z').timestamp()), \
                 patch('cloud_worker.time.sleep',side_effect=AssertionError('manual stop must not wait for scientific soft deadline')), \
                 patch('cloud_worker.os.killpg',side_effect=probe) as signals:
                self.assertEqual(run_guest(runtime,ROOT,work),1)
            self.assertEqual(process.wait.call_args_list[0].kwargs['timeout'],360)
            self.assertFalse(any(c.args[1]==signal.SIGKILL for c in signals.call_args_list))

    def test_owned_group_absence_check_uses_only_signal_zero_and_records_scoped_receipt(self):
        with patch('cloud_worker.os.killpg',side_effect=ProcessLookupError) as probe,patch('cloud_worker.time.sleep') as pause:
            receipt=wait_pipeline_group_absent(12345,'2099-01-01T00:00:00Z')
        probe.assert_called_once_with(12345,0);pause.assert_not_called()
        self.assertTrue(receipt['owned_pipeline_group_absent'])
        self.assertEqual(receipt['owned_pipeline_group_id'],12345)
        self.assertNotIn('all_processes_absent',receipt)

    def test_owned_orphan_group_persistence_waits_short_bound_then_refuses(self):
        current=[0.0]
        def advance(seconds):current[0]+=seconds
        with patch('cloud_worker.os.killpg',return_value=None) as probe, \
             patch('cloud_worker.time.monotonic',side_effect=lambda:current[0]), \
             patch('cloud_worker.time.sleep',side_effect=advance):
            with self.assertRaisesRegex(GuardError,'still exists'):
                wait_pipeline_group_absent(12345,'2099-01-01T00:00:00Z',max_wait_seconds=.25)
        self.assertEqual(current[0],.25)
        self.assertGreater(len(probe.call_args_list),1)
        self.assertTrue(all(c.args==(12345,0) for c in probe.call_args_list))

    def test_owned_group_permission_unknown_and_expired_deadline_refuse_without_wait(self):
        with patch('cloud_worker.os.killpg',side_effect=PermissionError) as probe,patch('cloud_worker.time.sleep') as pause:
            with self.assertRaisesRegex(GuardError,'cannot verify'):
                wait_pipeline_group_absent(12345,'2099-01-01T00:00:00Z')
        probe.assert_called_once_with(12345,0);pause.assert_not_called()
        with patch('cloud_worker.os.killpg',return_value=None),patch('cloud_worker.time.sleep') as pause:
            with self.assertRaisesRegex(GuardError,'still exists'):
                wait_pipeline_group_absent(12345,'2000-01-01T00:00:00Z')
        pause.assert_not_called()

    def test_surviving_owned_group_blocks_final_snapshot_and_terminal_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp)/'work';runtime=self.profile();store=FakeStore()
            runtime.update(bucket='synthetic',prefix='offline',deadline_utc='2099-01-01T00:00:00Z',
                           max_storage_requests=100,max_upload_bytes=1_000_000,source_commit='a'*40,
                           spec='longitudinal-adoption-5y',scale=1000)
            process=Mock(pid=12345);process.poll.return_value=1;process.wait.return_value=1
            with patch('cloud_worker.guest_metadata',return_value=self.measurements()), \
                 patch('cloud_worker.Store',return_value=store),patch('cloud_worker.subprocess.Popen',return_value=process), \
                 patch('cloud_worker.signal.signal'),patch('cloud_worker.io_cpu_snapshot',return_value={}), \
                 patch('cloud_worker.wait_pipeline_group_absent',side_effect=GuardError('owned group still exists')), \
                 patch('cloud_worker.final_snapshot') as final:
                with self.assertRaises(GuardError):run_guest(runtime,ROOT,work)
            process.wait.assert_called();final.assert_not_called()
            self.assertNotIn('terminal.json',store.files)
            self.assertFalse((work/'process-group-verification.json').exists())


if __name__ == '__main__': unittest.main()
