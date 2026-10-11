"""Source-only native cursor, Collector history and budget counterexamples.

No provider, IAM, VM, scientific Model, real study state or real budget is used.
The constructor fixtures use real held Directorys and unchanged Collector gates.
"""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

# Portable source-location bootstrap only; the ten control bodies are unchanged.
import shutil
PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE/'source' if (PACKAGE/'source/research_tools/compute_only_control.py').is_file() else PACKAGE
ROSTER_PATH = PACKAGE/'released-roster.json' if (PACKAGE/'released-roster.json').is_file() else PACKAGE/'cloud/n2-collector-threadsafe-source-roster.json'
_roster = json.loads(ROSTER_PATH.read_bytes())
_sandbox = tempfile.TemporaryDirectory(prefix='dams-collector-thread-controls-')
HERE = Path(_sandbox.name).resolve()
for _rel, _sha in _roster['files_sha256'].items():
    _src = SOURCE/_rel
    assert __import__('hashlib').sha256(_src.read_bytes()).hexdigest() == _sha
    _dst = HERE/'source'/_rel
    _dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(_src,_dst)
_current = (HERE/'source/research_tools/compute_only_control.py').read_bytes()
_before = _current.replace(b'import threading\n',b'',1).replace(b'        self._history_lock = threading.RLock()\n',b'',1)
_wrapper = b"""    def _history(self, minimum=None):
        # listdir(fd) uses a shared native directory cursor. Serialize concurrent
        # lifecycle read gates as well as cursor validation/publication.
        with self._history_lock:
            return self._history_locked(minimum)

    def _history_locked(self, minimum=None):
"""
_before = _before.replace(_wrapper,b'    def _history(self, minimum=None):\n',1)
assert __import__('hashlib').sha256(_before).hexdigest() == '62f584262ecccf9c79e05842212669f14666520b439e0fb712715f9b0ba2c791'
(HERE/'compute_only_control.before.py').write_bytes(_before)
sys.path[:0] = [str(HERE/'source'), str(HERE/'source/research_tools')]
sys.dont_write_bytecode = True
from research_tools import compute_only_control as c
from dams_sim.config import Config
from dams_sim.spec import case_key
from dams_sim.storage import canonical, digest, source_hash
from dams_sim.longitudinal_pipeline import driver_hash
from dams_sim.transfer_spool import _codec


class Fixture:
    def __init__(self, root):
        config = Config().to_dict()
        rows = [{'case_id':case_key(Config.from_dict(config)), 'config':config,'tags':{'parent_case_id':None}}]
        spec = {'control_fixture':'native-thread-guard'}
        assignment = {'schema':'dams-compute-only-assignment-v1','source_sha256':source_hash(),
            'pipeline_driver_sha256':driver_hash(),'spec_sha256':digest(canonical(spec)),
            'spec':spec,'inventory_sha256':digest(canonical(rows)),'cases':rows}
        self.assignment = c.blob(assignment)
        codec = _codec(); deps = {n:digest((Path(codec.__file__).parent/n).read_bytes()) for n in ('cloud_control.py','cloud_archive.py')}
        paths = [root/n for n in ('state','cache','copy1','copy2')]
        for p in paths:p.mkdir()
        value = {'schema':c.COLLECTOR_SCHEMA,'stage_id':'source-only-thread-guard',
            **{k:assignment[k] for k in ('source_sha256','pipeline_driver_sha256','spec_sha256','inventory_sha256')},
            'assignment_sha256':digest(self.assignment),'codec_source_sha256':deps['cloud_archive.py'],
            'codec_dependencies_sha256':deps,'collector_source_sha256':c.COLLECTOR_IMPORT_SHA256,
            'deadline_utc':'2030-01-01T00:00:00Z','max_fetch_requests':256,'max_fetch_bytes':1024**2,
            'max_group_raw_bytes':1024**2,'min_free_bytes':0,'state_dir':str(paths[0]),'cache_dir':str(paths[1]),
            'copy_dirs':[str(x) for x in paths[2:]]}
        self.raw = c.blob(value); self.admission = root/'admission.json';self.admission.write_bytes(self.raw)
        self.collector = c.Collector(self.admission,admission_sha256=digest(self.raw),assignment_raw=self.assignment)
    def close(self):self.collector.close()
    def reopen(self, head):
        self.collector.close()
        self.collector = c.Collector(self.admission,admission_sha256=digest(self.raw),assignment_raw=self.assignment,minimum_head=head)
        return self.collector


class Controls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HERE);self.root = Path(self.temp.name);self.fix=Fixture(self.root)
    def tearDown(self):
        self.fix.close();self.temp.cleanup()
    def test_history_guard_original_body_ast_exact(self):
        original=ast.parse((HERE/'compute_only_control.before.py').read_text())
        candidate=ast.parse((HERE/'source/research_tools/compute_only_control.py').read_text())
        original_cls=next(n for n in original.body if isinstance(n,ast.ClassDef) and n.name=='Collector')
        candidate_cls=next(n for n in candidate.body if isinstance(n,ast.ClassDef) and n.name=='Collector')
        before=next(n for n in original_cls.body if isinstance(n,ast.FunctionDef) and n.name=='_history')
        after=next(n for n in candidate_cls.body if isinstance(n,ast.FunctionDef) and n.name=='_history_locked')
        self.assertEqual([ast.dump(n,include_attributes=False) for n in before.body],
                         [ast.dump(n,include_attributes=False) for n in after.body])
    def test_native_two_threads_history_no_missing_prefix(self):
        collector=self.fix.collector;ticket=collector.reserve_metadata_attempt(4096);head=ticket['head']
        barrier=threading.Barrier(2);fail=[]
        def read_head():
            for i in range(128):
                barrier.wait()
                try:self.assertEqual(collector.head,head)
                except BaseException as error:fail.append((i,repr(error)))
        threads=[threading.Thread(target=read_head) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(30);self.assertFalse(t.is_alive())
        self.assertEqual(fail,[]);self.assertEqual(collector.head,head)
        self.assertEqual(os.listdir(collector.results.fd),[])
    def test_current_pending_ticket_full_validation_no_refund(self):
        collector=self.fix.collector;ticket=collector.reserve_metadata_attempt(256)
        with collector.locked():collector._accept_metadata(ticket,b'bounded valid metadata')
        self.assertEqual(collector.head,ticket['head']);self.assertEqual(collector.head['bytes'],257)
    def test_other_pending_head_does_not_masquerade_as_own_ticket(self):
        collector=self.fix.collector;first=collector.reserve_metadata_attempt(256);second=collector.reserve_metadata_attempt(256)
        # Original Live gate is exact self.head == immutable retained ticket.head.
        self.assertNotEqual(collector.head,first['head']);self.assertEqual(collector.head,second['head'])
        with self.assertRaisesRegex(ValueError,'another pending'):
            c.require(collector.head==first['head'],'another pending reservation changed current ticket head')
        self.assertEqual(collector.head['requests'],2);self.assertEqual(collector.head['bytes'],514)
    def test_record_rollback_durable_prefix_refused(self):
        collector=self.fix.collector;collector.reserve_metadata_attempt(256)
        os.unlink('00000001.json',dir_fd=collector.records.fd)
        with self.assertRaisesRegex(ValueError,'durable known prefix missing'):collector.head
    def test_durable_cursor_tamper_refused(self):
        collector=self.fix.collector;collector.reserve_metadata_attempt(256)
        raw=c.blob({'sequence':1,'sha256':'f'*64,'requests':1,'bytes':257})
        (Path(collector.state.path)/'head.json').write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'durable known prefix missing'):collector.head
    def test_external_minimum_rollback_refused(self):
        collector=self.fix.collector;ticket=collector.reserve_metadata_attempt(256)
        bad=copy.deepcopy(ticket['head']);bad['sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'known head rolled back'):collector._history(bad)
    def test_external_collectors_original_flock_exclusion_preserved(self):
        collector=self.fix.collector;head=collector.head
        other=c.Collector(self.fix.admission,admission_sha256=digest(self.fix.raw),assignment_raw=self.fix.assignment,minimum_head=head)
        try:
            with collector.locked():
                with self.assertRaisesRegex(ValueError,'already active'):
                    with other.locked():pass
        finally:other.close()
    def test_valid_resume_head_and_held_reservations_preserved(self):
        ticket=self.fix.collector.reserve_metadata_attempt(256)
        reopened=self.fix.reopen(ticket['head'])
        self.assertEqual(reopened.head,ticket['head']);self.assertEqual(os.listdir(reopened.results.fd),[])
    def test_result_without_reservation_refused(self):
        collector=self.fix.collector;collector.results.write_new('00000001.json',b'{}\n')
        with self.assertRaisesRegex(ValueError,'has no reservation'):collector.head


if __name__=='__main__':unittest.main(verbosity=2)
