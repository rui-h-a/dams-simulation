"""Provider-free archive-backed Collector closure, explicit operational profile.

Two persistent backends are independently read and losslessly decoded before
ACK, on every ACK delivery, and at terminal/delete closure. A scientific gate
is never inferred from successful decompression. New generations first pass
the original Collector gate on a bounded raw lease. A prior complete case may
reuse only the exact root-whitelisted original raw gate and reaped exit.
Only successful, still-unchanged raw leases created here may be retired.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
import hashlib
import io
import os
from pathlib import Path
import stat
import time
import resource
import sys
import zlib

from dams_sim._committed_pages import Directory, identity
from dams_sim.storage import canonical, digest
from dams_sim.transfer_spool import SCHEMA, MAX_METADATA_BYTES, _name, _sha, _natural
from research_tools.compute_only_control import blob, parse, read, require, child, leaf, exact_tree, file_hash

PROFILE_SCHEMA = 'dams-compute-archive-backed-profile-v1'
INDEX_SCHEMA = 'dams-compute-archive-backed-index-v1'
GATE_SCHEMA = 'dams-compute-archive-scientific-gate-v1'
RECEIPT_SCHEMA = 'dams-compute-archive-restoration-v1'
PROFILE_FIELDS = {'schema', 'mode', 'collector_binding_sha256', 'helper_sha256', 'job',
                  'lease_dir', 'max_lease_raw_bytes', 'max_archive_encoded_bytes',
                  'backup_bindings', 'prior_cases', 'max_operation_seconds', 'max_rss_bytes'}
JOB_FIELDS = {'stage_id', 'source_sha256', 'pipeline_driver_sha256', 'spec_sha256',
              'inventory_sha256', 'assignment_sha256', 'runtime_sha256', 'provider_identity_sha256'}
BACKUP_FIELDS = {'backup_id', 'domain', 'locator', 'enrollment_sha256'}
IMPORT_SHA = digest(Path(__file__).read_bytes())


class DirectoryBackend:
    """Actual private on-disk fixture backend; never attests off-device storage.

    Production uses root-enrolled persistent binary transports with the same
    publish(key, bytes), read(key)->binary stream and check() API. The Collector
    owns all bounds/reservations, reads EOF, and verifies every returned byte.
    This adapter deliberately cannot be enrolled in external-persistent mode.
    """
    kind = 'local-fixture'

    def __init__(self, root, binding, enrollment_raw):
        self.root = Directory(root); self.binding = dict(binding)
        self.enrollment_raw = enrollment_raw
        require(binding['locator'] == str(self.root.path), 'fixture locator differs from actual directory')

    def check(self):
        self.root.check()

    def publish(self, key, raw):
        self.check(); parent, name, opened = leaf(self.root, key)
        try:
            if name in os.listdir(parent.fd):
                require(read(parent, name, len(raw))[0] == raw, 'archive immutable object differs')
            else:
                parent.write_new(name, raw)
        finally:
            for d in reversed(opened): d.close()

    def read(self, key):
        self.check(); parent, name, opened = leaf(self.root, key)
        try:
            fd = parent.open(name, os.O_RDONLY | os.O_NONBLOCK)
            info = os.fstat(fd)
            require(info.st_nlink == 1, 'archive object hardlinked')
            expected = identity(info)
            owner = self
            class Stream:
                def read(self, count):
                    owner.check(); parent.matches(name, fd)
                    require(identity(os.fstat(fd)) == expected, 'archive object changed')
                    return os.read(fd, count)
                def close(self):
                    try:
                        parent.matches(name, fd)
                        require(identity(os.fstat(fd)) == expected, 'archive closed object changed')
                    finally:
                        os.close(fd)
                        for d in reversed(opened): d.close()
            return Stream()
        except BaseException:
            if 'fd' in locals(): os.close(fd)
            for d in reversed(opened): d.close()
            raise

    def close(self): self.root.close()


class ArchiveClosure:
    def __init__(self, collector, profile_ref, backends):
        self.c = collector; self.opened = []; self.leases = []
        self.in_collect = False; self.planned = {}
        self.source_raw = Path(__file__).read_bytes()
        require(digest(self.source_raw) == IMPORT_SHA, 'archive imported helper differs')
        raw = self.reference(profile_ref)
        self.profile_raw = raw; self.profile_sha = digest(raw); self.p = p = parse(raw)
        require(isinstance(p, dict) and set(p) == PROFILE_FIELDS and p['schema'] == PROFILE_SCHEMA
                and p['mode'] in ('external-persistent', 'local-fixture'), 'archive profile shape/mode differs')
        require(p['collector_binding_sha256'] == digest(canonical({k:v for k,v in self.c.config.items() if k!='storage_profile'})) and p['helper_sha256'] == IMPORT_SHA,
                'archive profile admission/helper differs')
        job = p['job']; require(isinstance(job, dict) and set(job) == JOB_FIELDS, 'archive job exact pins missing')
        for key in JOB_FIELDS - {'stage_id'}: _sha(job[key])
        require(all(job[k] == self.c.config[k] for k in JOB_FIELDS - {'runtime_sha256', 'provider_identity_sha256'}),
                'archive job source/inventory/assignment differs')
        self.job_sha = digest(canonical(job))
        _natural(p['max_lease_raw_bytes'], True); _natural(p['max_archive_encoded_bytes'], True)
        require(type(p['max_operation_seconds']) is int and 0<p['max_operation_seconds']<=5400, 'archive finite operation engineering cap differs')
        _natural(p['max_rss_bytes'],True); self.operation_started=time.monotonic()
        require(isinstance(backends, (tuple, list)) and len(backends) == 2, 'archive requires two enrolled binary backends')
        bindings = p['backup_bindings']
        require(isinstance(bindings, list) and len(bindings) == 2, 'archive backup bindings differ')
        for binding, backend in zip(bindings, backends):
            require(isinstance(binding, dict) and set(binding) == BACKUP_FIELDS
                    and all(isinstance(binding[k], str) and 0 < len(binding[k]) <= 4096 for k in BACKUP_FIELDS),
                    'archive backend binding shape differs')
            _name(binding['backup_id']); _sha(binding['enrollment_sha256'])
            require(Path(binding['backup_id']).name==binding['backup_id'],'archive backup ID must be an exact leaf')
            require(backend.binding == binding and digest(backend.enrollment_raw) == binding['enrollment_sha256'],
                    'archive backend root enrollment differs')
            enrolled = parse(backend.enrollment_raw)
            require(isinstance(enrolled, dict) and set(enrolled) == {'schema', 'job_sha256', 'backup_id', 'domain', 'locator', 'kind'}
                    and enrolled['schema'] == 'dams-compute-persistent-backup-enrollment-v1'
                    and enrolled['job_sha256'] == self.job_sha and enrolled['kind'] == backend.kind
                    and all(enrolled[k] == binding[k] for k in ('backup_id', 'domain', 'locator')),
                    'archive enrollment job/locator differs')
            require(backend.kind == ('local-fixture' if p['mode'] == 'local-fixture' else 'external-persistent'),
                    'local bytes cannot attest external persistent backups')
            backend.check()
        require(bindings[0]['backup_id'] != bindings[1]['backup_id']
                and bindings[0]['locator'] != bindings[1]['locator'] and backends[0] is not backends[1],
                'archive backup locators are not independently restorable')
        self.backends = list(backends)
        if p['mode']=='local-fixture':
            require(len({identity(os.fstat(b.root.fd))[:2] for b in backends})==2,
                    'fixture backups share actual directory identity')
        require(isinstance(p['prior_cases'], dict), 'archive prior-case whitelist differs')
        for cid, refs in p['prior_cases'].items():
            _sha(cid)
            require(cid in self.c.rows and isinstance(refs, dict) and set(refs) == {'result', 'exit', 'root_acceptance'},
                    'archive prior accepted case is outside assignment')
            for ref in refs.values(): self.reference(ref)
        path = Path(p['lease_dir'])
        require(path.is_absolute(), 'archive raw lease must be absolute')
        forbidden = [self.c.state.path, self.c.cache.path, self.c.source_directory.path, *[d.path for d in self.c.copies]]
        require(all(path != q and path not in q.parents and q not in path.parents for q in forbidden),
                'archive lease namespace overlaps source/storage')
        self.lease_root = Directory(path); self.c.opened.append(self.lease_root)
        self.holds = child(self.c.state, 'archive-holds'); self.c.opened.append(self.holds)
        self.groups = child(self.c.state, 'archive-groups'); self.c.opened.append(self.groups)
        self.published = child(self.c.state, 'archive-publications'); self.c.opened.append(self.published)
        self._hold_history(); self.check()

    def reference(self, ref):
        require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}, 'archive exact reference differs')
        _sha(ref['sha256']); path = Path(ref['path']); require(path.is_absolute(), 'archive reference must be absolute')
        directory = Directory(path.parent); self.c.opened.append(directory)
        raw, observed = read(directory, path.name)
        require(digest(raw) == ref['sha256'], 'archive external reference bytes differ')
        self.opened.append((directory, path.name, raw, observed)); return raw

    def check(self):
        require(time.monotonic()-self.operation_started<=self.p['max_operation_seconds'], 'archive engineering processing cap reached')
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        require(rss<=self.p['max_rss_bytes'], 'archive observed peak RSS cap reached')
        require(Path(__file__).read_bytes() == self.source_raw == Path(__file__).read_bytes(), 'archive helper changed')
        for directory, name, raw, observed in self.opened:
            require(read(directory, name) == (raw, observed), 'archive pinned external input changed')
        for binding,backend in zip(self.p['backup_bindings'],self.backends):
            require(backend.binding==binding and digest(backend.enrollment_raw)==binding['enrollment_sha256'],
                    'archive configured backend drifted')
            backend.check()
        if hasattr(self, 'lease_root'): self.lease_root.check()

    def _hold_history(self):
        total = 0; previous = None
        for i, name in enumerate(sorted(os.listdir(self.holds.fd)), 1):
            require(name == f'{i:08d}.json', 'archive durable allocation prefix differs')
            raw = read(self.holds, name)[0]; row = parse(raw)
            require(set(row) == {'sequence', 'previous_sha256', 'profile_sha256', 'backend_id', 'object_sha256', 'bytes'}
                    and type(row['sequence']) is int and row['sequence'] == i and row['previous_sha256'] == previous
                    and row['profile_sha256'] == self.profile_sha
                    and row['backend_id'] in {b['backup_id'] for b in self.p['backup_bindings']}, 'archive durable allocation changed')
            _sha(row['object_sha256']); total += _natural(row['bytes']); previous = digest(raw)
        require(total <= self.p['max_archive_encoded_bytes'], 'archive original encoded allowance exceeded')
        return total, previous, len(os.listdir(self.holds.fd))

    @staticmethod
    def native_roster_matches(manifest, files):
        # An older accepted generation is not proof that the guest's CURRENT
        # case bytes still match it. Final closure requires the same whole
        # roster; a latest checkpoint requires its complete group as a subset.
        prefix='cases/'+manifest['case_id']+'/'+manifest['attempt']+'/'
        expected={prefix+p['name']:(p['codec']['raw_bytes'],p['codec']['raw_sha256'])for p in manifest['files']}
        actual={n:(f['bytes'],f['sha256'])for n,f in files.items()if n.startswith(prefix)}
        return (set(actual)==set(expected) if manifest['kind']=='final' else set(expected)<=set(actual)) and all(actual.get(n)==v for n,v in expected.items())

    @contextmanager
    def collection_lock(self):
        require(not self.in_collect,'archive collection already active')
        with self.c.locked():
            self.in_collect=True
            try:yield
            finally:self.in_collect=False;self.planned={}

    def plan(self, requests):
        # Full prefix validation before and after publication of ALL individual
        # holds. Unused holds remain charged; no callback is issued beforehand.
        require(requests and len(requests)<=65536,'archive callback batch bound')
        with (nullcontext() if self.in_collect else self.c.locked()):
            tickets=self.c._reserve_batch([('chunk',size+1,sha)for op,bid,key,size,sha in requests])
        self.planned={}
        for request,ticket in zip(requests,tickets):self.planned.setdefault(request,[]).append(ticket)

    def reservation(self, operation, backend, key, size, sha):
        item=(operation,backend.binding['backup_id'],key,size,sha)
        queued=self.planned.get(item)
        if queued:return queued.pop(0)
        with (nullcontext() if self.in_collect else self.c.locked()):
            return self.c._reserve('chunk',size+1,sha)

    def publish(self, backend, key, raw):
        self.c.check(); record_name=digest(canonical([backend.binding['backup_id'],key]))+'.json'
        record={'profile_sha256':self.profile_sha,'backend_id':backend.binding['backup_id'],'key':key,'sha256':digest(raw),'bytes':len(raw)}
        if record_name in os.listdir(self.published.fd):
            require(parse(read(self.published,record_name)[0])==record,'archive prior published object pin differs')
            self.fetch(backend,key,len(raw),digest(raw));return
        total, previous, count = self._hold_history()
        # Conservative attempted allocation holds survive failed publication.
        require(total + len(raw) <= self.p['max_archive_encoded_bytes'], 'archive persistent allocation bound exhausted')
        self.holds.write_new(f'{count+1:08d}.json', blob({'sequence': count+1, 'previous_sha256': previous,
            'profile_sha256': self.profile_sha, 'backend_id': backend.binding['backup_id'],
            'object_sha256': digest(raw), 'bytes': len(raw)}))
        self.reservation('publish',backend,key,len(raw),digest(raw))
        if backend.kind=='local-fixture':self.c.codec.require_capacity(backend.root.path,len(raw)+8192,self.c.config['min_free_bytes'])
        backend.publish(_name(key), raw); self.c.check()
        self.fetch(backend,key,len(raw),digest(raw))
        self.published.write_new(record_name,blob(record))

    def fetch(self, backend, key, size, sha):
        self.c.check(); _sha(sha); _natural(size)
        number, rsha = self.reservation('fetch',backend,key,size,sha); status = 'failed'; raw = None
        try:
            raw, observed = self.c._stream(lambda: backend.read(_name(key)), size)
            require(len(raw) == size and digest(raw) == sha, 'archive fresh binary readback differs')
            status = 'verified'; return raw
        finally:
            self.c.results.write_new(f'{number:08d}.json', blob({'reservation_sha256': rsha,
                'observed_bytes': self.c.last_observed, 'status': status,
                'payload_sha256': digest(raw) if status == 'verified' else None}))

    def roster(self, m):
        return {p['name']: {'bytes': p['codec']['raw_bytes'], 'sha256': p['codec']['raw_sha256']} for p in m['files']}

    @contextmanager
    def constructor_guard(self):
        """Pure-I/O validation may use readers/__new__, never simulation init."""
        from dams_sim.model import Model
        from dams_sim.longitudinal_model import LongitudinalEngine
        from dams_sim.longitudinal_storage import ExactLedger
        originals=[];counts={}
        for cls in (Model,LongitudinalEngine,ExactLedger):
            name=cls.__name__+'.__init__';counts[name]=0;original=cls.__init__
            def deny(*args,_name=name,**kwargs):
                counts[_name]+=1;raise ValueError('archive scientific gate attempted simulation constructor '+_name)
            originals.append((cls,original,deny));cls.__init__=deny
        try:
            yield counts
            require(all(type(v)is int and v==0 for v in counts.values()),'archive constructor attempts are not zero')
        finally:
            changed=any(cls.__init__ is not deny for cls,original,deny in originals)
            for cls,original,deny in originals:cls.__init__=original
            require(not changed,'archive constructor guard replaced during validation')

    def prior_gate(self, m):
        cid = m['case_id']; require(m['kind'] == 'final' and cid in self.p['prior_cases'], 'archive prior case not explicitly admitted')
        refs = self.p['prior_cases'][cid]
        r = parse(self.reference(refs['result'])); ex = parse(self.reference(refs['exit'])); acceptance = parse(self.reference(refs['root_acceptance']))
        expected_identity = {k: self.c.config[k] for k in ('source_sha256', 'pipeline_driver_sha256', 'spec_sha256')}
        require(isinstance(acceptance, dict) and set(acceptance) == {'schema', 'case_id', 'job_sha256', 'result_sha256', 'exit_sha256', 'root_accepted'}
                and acceptance['schema'] == 'dams-compute-archive-prior-case-admission-v1'
                and acceptance['case_id'] == cid and acceptance['job_sha256'] == self.job_sha
                and acceptance['result_sha256'] == refs['result']['sha256'] and acceptance['exit_sha256'] == refs['exit']['sha256']
                and acceptance['root_accepted'] is True, 'archive prior root acceptance contract differs')
        require(r.get('status') == 'PASS_ACTUAL_ORIGINAL_CLOSED_COMPLETE_CASE_RAW_GATE'
                and r.get('original_validator') == 'research_tools.validate_longitudinal.CheckedLongitudinalCases.validate_case'
                and r.get('case_id') == cid and r.get('config_sha256') == m['config_sha256'] and type(r.get('day')) is int and r['day'] == m['day']
                and r.get('original_identity') == expected_identity
                and r.get('actual_validator_result') == {'status': 'individual-case-raw-validation', 'unique_complete_cases': 1,
                    'full_study_gate': False, 'identity': expected_identity}, 'archive prior original full gate differs')
        row=self.c.rows[cid];parent=row['tags']['parent_case_id']
        closure='canonical_fixed3y_strategy_parent_closure_checked' if parent is not None else (
            'canonical_root_prehistory_prefix_parent_closure_checked' if row['tags'].get('role')=='prehistory-prefix' else 'canonical_root_founding_strategy_parent_closure_checked')
        require(r.get(closure) is True
                and r.get(f'full{m["day"]}daily_CSV_history_events_stocks_state_accounting_summary_and_required_output_roster_checked') is True
                and r.get('full_study_gate') is False and type(r.get('new_paired_worlds_added')) is int and r['new_paired_worlds_added']==0
                and r.get('whole_goal_complete') is False,'archive prior canonical stage/full daily closure differs')
        if parent is not None:
            require(r.get('parent_case_id')==parent
                    and r.get(f'parent_all{self.c.configs[parent].days}_CSV_rows_equal_child_prefix') is True
                    and r.get('parent_original_full_raw_acceptance_reused_not_reexecuted') is True,
                    'archive prior complete parent CSV-prefix proof differs')
        require(r.get('full_raw_before') == r.get('full_raw_after') == self.roster(m)
                and r.get('producer_manifest_sha256') == self.roster(m)['manifest.json']['sha256']
                and r.get('all_final_and_retained_checkpoint_full_bytes_source_config_semantic_state_ledger_and_day_history_checked') is True
                and r.get('source56_full_bytes_stat_before_after_equal') is True
                and r.get('original_source_raw_written') is False and r.get('Model_or_simulation_execution') is False
                and r.get('Model_Engine_ExactLedger___init___attempt_counts') == {'Model.__init__': 0, 'LongitudinalEngine.__init__': 0, 'ExactLedger.__init__': 0},
                'archive prior raw/source/constructor closure differs')
        require(all(type(v) is int for v in r['Model_Engine_ExactLedger___init___attempt_counts'].values()),
                'archive prior constructor counters are not typed integers')
        require(ex.get('status') == 'ACTUAL_DIRECT_CHILD_RETURNED' and type(ex.get('exit_code')) is int and ex['exit_code'] == 0
                and ex.get('resource_guard_stop_reason') is None and ex.get('supervisor_error') is None
                and ex.get('terminal_result_present') is True and ex.get('failure_receipt_present') is False
                and isinstance(ex.get('direct_child_cleanup'), dict) and type(ex['direct_child_cleanup'].get('exit_code')) is int
                and ex['direct_child_cleanup']['exit_code'] == 0 and ex['direct_child_cleanup'].get('reaped') is True
                and ex['direct_child_cleanup'].get('TERM_sent') is False and ex['direct_child_cleanup'].get('KILL_sent') is False,
                'archive prior actual normal terminal/reap differs')
        _sha(r['state_semantic_sha256'])
        return {'schema': GATE_SCHEMA, 'profile_sha256': self.profile_sha, 'job_sha256': self.job_sha,
                'generation': m['generation'], 'gate': 'final-full-raw', 'files': self.roster(m),
                'scientific_binding': {'case_id': cid, **expected_identity, 'config_sha256': m['config_sha256'], 'day': m['day'],
                    'state_semantic_sha256': r['state_semantic_sha256'], 'branch_origin': m['sealed']['manifest'].get('branch_origin'),
                    'inventory_sha256': self.c.config['inventory_sha256']},
                'prior_original_full_gate': refs, 'individual_case_only': True, 'full_study_gate': False, 'science_complete': False}

    def group(self, generation):
        _sha(generation); return child(self.groups, generation)

    def index(self, m, seal_raw, gate_raw):
        return {'schema': INDEX_SCHEMA, 'profile_sha256': self.profile_sha, 'job_sha256': self.job_sha,
                'generation': m['generation'], 'manifest_sha256': digest(blob(m)), 'seal_sha256': digest(seal_raw),
                'group_sha256': m['group_sha256'], 'files': m['files'], 'scientific_gate_sha256': digest(gate_raw)}

    def verify(self, generation):
        """Fresh complete binary reads from both enrolled backends, every call."""
        _sha(generation)
        with ExitStack() as stack:
            group = stack.enter_context(_owned(self.groups.path/generation))
            mr = read(group, 'manifest.json')[0]; sr = read(group, 'seal.json')[0]; m, _ = self.c._validate_group(mr, sr)
            gr = read(group, 'scientific-gate.json')[0]; gate = parse(gr)
            require(gate['schema'] == GATE_SCHEMA and gate['profile_sha256'] == self.profile_sha and gate['job_sha256'] == self.job_sha
                    and gate['generation'] == generation and gate['files'] == self.roster(m)
                    and gate['gate'] == ('final-full-raw' if m['kind'] == 'final' else 'checkpoint-full-state')
                    and gate['individual_case_only'] is True and gate['full_study_gate'] is False and gate['science_complete'] is False,
                    'archive immutable scientific gate binding differs')
            ir = read(group, 'index.json')[0]
            pin=parse(read(group,'binding.json')[0]);self.c._history(self.c._head)
            require(set(pin)=={'reservation','index_sha256','scientific_gate_sha256'} and pin['index_sha256']==digest(ir)
                and pin['scientific_gate_sha256']==digest(gr),'archive gate/index reservation pin differs')
            r=read(self.c.records,f'{pin["reservation"]:08d}.json')[0]
            require(parse(r)['object_sha256']==digest(ir),'archive gate/index durable reservation differs')
            require(parse(ir) == self.index(m, sr, gr), 'archive exact index differs')
            requests=[]
            for backend in self.backends:
                bid=backend.binding['backup_id']
                for name,raw in [('manifest.json',mr),('seal.json',sr),('scientific-gate.json',gr),('index.json',ir)]:
                    requests.append(('fetch',bid,generation+'/'+name,len(raw),digest(raw)))
                for part in m['files']:
                    for chunk in part['codec']['chunks']:
                        requests.append(('fetch',bid,'objects/'+chunk['encoded_sha256']+'.z',chunk['encoded_bytes'],chunk['encoded_sha256']))
            self.plan(requests)
            copies = []
            for backend in self.backends:
                for name, raw in (('manifest.json', mr), ('seal.json', sr), ('scientific-gate.json', gr), ('index.json', ir)):
                    require(self.fetch(backend, generation+'/'+name, len(raw), digest(raw)) == raw, 'archive metadata full readback differs')
                decoded = {}
                for part in m['files']:
                    h = hashlib.sha256(); length = 0
                    for chunk in part['codec']['chunks']:
                        encoded = self.fetch(backend, 'objects/'+chunk['encoded_sha256']+'.z', chunk['encoded_bytes'], chunk['encoded_sha256'])
                        raw = self.c.codec.decode_chunk(encoded, chunk); h.update(raw); length += len(raw)
                    require(length == part['codec']['raw_bytes'] and h.hexdigest() == part['codec']['raw_sha256'],
                            'archive decoded whole file differs')
                    decoded[part['name']] = {'bytes': length, 'sha256': h.hexdigest()}
                require(decoded == gate['files'], 'archive decoded roster differs from full scientific gate')
                bid = backend.binding['backup_id']
                receipt = {'schema': RECEIPT_SCHEMA, 'profile_sha256': self.profile_sha, 'job_sha256': self.job_sha,
                    'generation': generation, 'restoration_id': generation+'-'+bid, 'backup_binding': backend.binding,
                    'index_sha256': digest(ir), 'scientific_gate_sha256': digest(gr), 'files': decoded,
                    'gate': gate['gate'], 'group_sha256': m['group_sha256'], 'manifest_sha256': digest(mr),
                    'verification': 'fresh-full-decode-to-sink-and-original-scientific-gate', 'physical_raw_copy_retained': False,
                    'individual_case_only': True, 'full_study_gate': False, 'science_complete': False}
                rr = blob(receipt); name = bid+'.receipt.json'
                if name in os.listdir(group.fd): require(read(group, name)[0] == rr, 'archive restoration receipt changed')
                else: group.write_new(name, rr)
                copies.append({'restoration_id': receipt['restoration_id'], 'receipt_sha256': digest(rr),
                               'group_sha256': m['group_sha256'], 'gate': gate['gate']})
            require(copies[0]['restoration_id'] != copies[1]['restoration_id'] and copies[0]['receipt_sha256'] != copies[1]['receipt_sha256'],
                    'archive independent restore receipts differ')
            self.c.check(); return m, copies

    def _lease(self, m, *, backend=None):
        current = sum(p.stat().st_size for p in self.lease_root.path.rglob('*') if p.is_file())
        needed = sum(p['codec']['raw_bytes'] for p in m['files'])
        require(current + needed <= self.p['max_lease_raw_bytes'], 'archive temporary raw lease cap exceeded')
        group = child(self.lease_root, m['generation']+'-'+str(time.time_ns())); self.leases.append((group, None, None))
        group.write_new('manifest.json', blob(m))
        if backend is None:
            attempt = self.c._restore(group, m)
        else:
            dirs = []; d = group
            try:
                for name in ('cases', m['case_id'], m['attempt']): d = child(d, name); dirs.append(d)
                self.c.codec.require_capacity(group.path, needed+2*self.c.codec.MAX_ENCODED_CHUNK+len(m['files'])*8192,
                                              self.c.config['min_free_bytes'])
                for part in m['files']:
                    target, name, opened = leaf(d, part['name'])
                    fd = target.open(name, os.O_WRONLY|os.O_CREAT|os.O_EXCL)
                    try:
                        h = hashlib.sha256(); length = 0
                        for chunk in part['codec']['chunks']:
                            encoded = self.fetch(backend, 'objects/'+chunk['encoded_sha256']+'.z', chunk['encoded_bytes'], chunk['encoded_sha256'])
                            raw = self.c.codec.decode_chunk(encoded, chunk); h.update(raw); length += len(raw); view = memoryview(raw)
                            while view:
                                n = os.write(fd, view); require(n > 0, 'archive leased restore short write'); view = view[n:]
                        require(length == part['codec']['raw_bytes'] and h.hexdigest() == part['codec']['raw_sha256'], 'archive leased logical file differs')
                        os.fsync(fd); target.matches(name, fd); os.fsync(target.fd)
                    finally:
                        os.close(fd)
                        for item in reversed(opened): item.close()
                exact_tree(d, self.roster(m)); attempt = d.path
            finally:
                for item in reversed(dirs): item.close()
        files = self.c._observations(group, attempt, m); self.leases[-1] = (group, attempt, files)
        return group, attempt, files

    def parent_context(self, parent, generation):
        from dams_sim.longitudinal_model import verify_snapshot
        require(parent in self.c.rows, 'archive parent outside exact assignment')
        m, copies = self.verify(generation)
        require(m['case_id'] == parent and m['kind'] == 'final', 'archive parent generation is not completed assigned parent')
        ack = parse(read(self.c.state, generation+'.ACK.json')[0])
        require(ack['copies'] == copies and ack['gate'] == 'final-full-raw', 'archive parent lacks retained accepted ACK')
        group, attempt, files = self._lease(m, backend=self.backends[0])
        envelope, _ = verify_snapshot(attempt/'final_state.json', expected_config=self.c.configs[parent],
                                      expected_source_sha256=self.c.config['source_sha256'])
        with _owned(self.groups.path/generation) as accepted:
            binding=parse(read(accepted,'scientific-gate.json')[0])['scientific_binding']
        require(envelope['state_semantic_sha256']==binding['state_semantic_sha256']
                and envelope['state']['day']==binding['day'] and envelope['config_sha256']==binding['config_sha256']
                and envelope['state']['branch_origin']==m['sealed']['manifest'].get('branch_origin'),
                'archive parent actual state differs from pinned original full gate')
        self.c._guard_files(attempt, files)
        return envelope, attempt

    def collect(self, manifest_raw, seal_raw, fetch, *, metadata_tickets, parent_generations=None):
        self.operation_started=time.monotonic()
        m, seal = self.c._validate_group(manifest_raw, seal_raw)
        require(manifest_raw == blob(m), 'archive group requires exact canonical manifest bytes')
        parent_generations = {} if parent_generations is None else parent_generations
        require(isinstance(parent_generations, dict), 'archive assigned parent generation map differs')
        with self.collection_lock():
            require(isinstance(metadata_tickets,list) and len(metadata_tickets)==2 and metadata_tickets[0]['sequence']!=metadata_tickets[1]['sequence'],
                    'archive metadata attempts must be distinct')
            for ticket, raw in zip(metadata_tickets,(manifest_raw,seal_raw)): self.c._accept_metadata(ticket,raw)
            for part in m['files']:
                for chunk in part['codec']['chunks']: self.c._fetch_chunk(part,chunk,fetch)
            group = self.group(m['generation'])
            try:
                require('index.json' not in os.listdir(group.fd), 'archive generation already collected; use fresh verify/delivery')
                if m['kind']=='final' and m['case_id'] in self.p['prior_cases']:
                    gate = self.prior_gate(m)
                else:
                    leased, attempt, files = self._lease(m)
                    with self.constructor_guard() as counts:
                        gate_name = self.c._gate(leased,attempt,m,parent_generations,self.groups)
                    require(self.c._observations(leased,attempt,m)==files,'archive raw changed across original scientific gate')
                    gate = {'schema':GATE_SCHEMA,'profile_sha256':self.profile_sha,'job_sha256':self.job_sha,
                        'generation':m['generation'],'gate':gate_name,'files':self.roster(m),'scientific_binding':self.c.gate_details,
                        'original_validator':'research_tools.validate_longitudinal.CheckedLongitudinalCases.validate_case' if m['kind']=='final' else 'dams_sim.longitudinal_model.verify_snapshot',
                        'constructor_attempts':counts,'prior_original_full_gate':None,'individual_case_only':True,'full_study_gate':False,'science_complete':False}
                gr=blob(gate);ir=blob(self.index(m,seal_raw,gr))
                number,_=self.c._reserve('metadata',len(ir)+1,digest(ir))
                group.write_new('binding.json',blob({'reservation':number,'index_sha256':digest(ir),'scientific_gate_sha256':digest(gr)}))
                requests=[]
                for name,raw in [('manifest.json',manifest_raw),('seal.json',seal_raw),('scientific-gate.json',gr),('index.json',ir)]:
                    for backend in self.backends:
                        for op in ['publish','fetch']:requests.append((op,backend.binding['backup_id'],m['generation']+'/'+name,len(raw),digest(raw)))
                for part in m['files']:
                    for chunk in part['codec']['chunks']:
                        for backend in self.backends:
                            for op in ['publish','fetch']:requests.append((op,backend.binding['backup_id'],'objects/'+chunk['encoded_sha256']+'.z',chunk['encoded_bytes'],chunk['encoded_sha256']))
                self.plan(requests)
                for name,raw in [('manifest.json',manifest_raw),('seal.json',seal_raw),('scientific-gate.json',gr),('index.json',ir)]:
                    group.write_new(name,raw)
                    for backend in self.backends:self.publish(backend,m['generation']+'/'+name,raw)
                for part in m['files']:
                    for chunk in part['codec']['chunks']:
                        encoded=read(self.c.cache,chunk['encoded_sha256']+'.z',self.c.codec.MAX_ENCODED_CHUNK)[0]
                        self.c.codec.decode_chunk(encoded,chunk)
                        for backend in self.backends:self.publish(backend,'objects/'+chunk['encoded_sha256']+'.z',encoded)
                _,copies=self.verify(m['generation'])
                ack={'schema':SCHEMA,'stage_id':m['stage_id'],'source_sha256':m['source_sha256'],'generation':m['generation'],
                     'manifest_sha256':seal['manifest_sha256'],'group_sha256':seal['group_sha256'],'gate':copies[0]['gate'],'copies':copies}
                ar=blob(ack);self.c.state.write_new(m['generation']+'.ACK.json',ar)
                require(read(self.c.state,m['generation']+'.ACK.json')[0]==ar,'archive durable ACK changed')
                _,after=self.verify(m['generation']);require(after==copies,'archive after-ACK closure changed')
                # Keep failure evidence, but successful validated temporary raw
                # payloads do not accumulate across every generation.
                self.retire_leases()
                return {'ack':ack,'ack_bytes':ar,'head':self.c.head,'storage_profile':PROFILE_SCHEMA,
                    'external_persistence':self.p['mode']=='external-persistent','individual_case_only':True,'full_study_gate':False,'science_complete':False}
            finally:group.close()

    def retire_leases(self):
        for group, attempt, files in self.leases:
            require(attempt is not None and files is not None and group.path.parent==self.lease_root.path,'partial raw lease retained')
            self.c._guard_files(attempt,files)
            with ExitStack() as stack:
                root=stack.enter_context(_owned(attempt))
                for name,value in files.items():
                    target,n,opened=leaf(root,name)
                    try:
                        require(list(identity(os.stat(n,dir_fd=target.fd,follow_symlinks=False)))==value['identity'],'lease retirement byte identity changed')
                        self.c.check();os.unlink(n,dir_fd=target.fd);os.fsync(target.fd)
                    finally:
                        for d in reversed(opened):d.close()
            raw,obs=read(group,'manifest.json');self.c.check()
            require(identity(os.stat('manifest.json',dir_fd=group.fd,follow_symlinks=False))==obs,'lease metadata changed')
            os.unlink('manifest.json',dir_fd=group.fd);os.fsync(group.fd)
            for p in sorted((p for p in group.path.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):os.rmdir(p)
            require(os.listdir(group.fd)==[],'foreign leased output retained');path=group.path;group.close();os.rmdir(path)
        self.leases=[]

    def ack_guard(self, generation):
        m,copies=self.verify(generation);raw=read(self.c.state,generation+'.ACK.json')[0];a=parse(raw)
        require(a=={'schema':SCHEMA,'stage_id':m['stage_id'],'source_sha256':m['source_sha256'],'generation':generation,
            'manifest_sha256':digest(blob(m)),'group_sha256':m['group_sha256'],'gate':copies[0]['gate'],'copies':copies},'archive exact retained ACK differs')
        return m,raw

    def terminal_closure(self, snapshot, *, source_sha256, runtime_sha256):
        require(snapshot.get('schema')=='dams-compute-live-poll-v1' and snapshot.get('science_complete') is False
            and snapshot.get('source_sha256')==source_sha256==self.p['job']['source_sha256']
            and snapshot.get('runtime_sha256')==runtime_sha256==self.p['job']['runtime_sha256'],'archive terminal job/runtime differs')
        ids=snapshot.get('reservation_generations');sealed=snapshot.get('sealed_generations');head=snapshot.get('spool_high_water')
        require(isinstance(ids,list) and len(ids)==len(set(ids)) and isinstance(sealed,list) and snapshot.get('unsealed_generations')==[]
            and isinstance(head,dict) and type(head.get('sequence')) is int and head['sequence']==len(ids)
            and len(sealed)==len(ids) and {r['generation'] for r in sealed}==set(ids),'archive terminal reserved/sealed coverage differs')
        known={n[:-9] for n in os.listdir(self.c.state.fd) if n.endswith('.ACK.json')}
        require(known==set(ids),'archive terminal ACK coverage/rollback differs')
        manifests={}
        for row in sealed:
            g=row['generation'];m,ar=self.ack_guard(g)
            with _owned(self.groups.path/g) as group:
                sr=read(group,'seal.json')[0];mr=read(group,'manifest.json')[0]
            require(row.get('ack_present') is True and digest(ar)==row.get('ack_sha256')
                and digest(sr)==row.get('seal_sha256') and len(sr)==row.get('seal_bytes')
                and digest(mr)==row.get('manifest_sha256') and len(mr)==row.get('manifest_bytes')
                and all(m[k]==row[k] for k in ('generation','case_id','attempt','kind','day','config_sha256','group_sha256')),
                'archive terminal original sealed/ACK pins differ')
            manifests[g]=m
        return manifests

    def terminal_archive(self, roster_raw, read_range):
        """Preserve every terminal byte, including failed/unvalidated evidence.

        Already accepted files reuse exact descriptors; others are streamed in
        fixed codec chunks from the closed guest. No full raw duplicate is
        constructed. This is an evidence gate, never a case/science gate.
        """
        self.operation_started=time.monotonic();roster=parse(roster_raw)
        require(roster.get('schema')=='dams-compute-terminal-roster-v1' and roster.get('source_sha256')==self.p['job']['source_sha256']
            and roster.get('runtime_sha256')==self.p['job']['runtime_sha256'] and roster.get('science_complete') is False
            and roster.get('terminal',{}).get('owned_pipeline_group_verification',{}).get('owned_pipeline_group_absent') is True,
            'archive terminal closed-owner/job proof differs')
        files=roster.get('files');require(isinstance(files,list) and len(files)<=8192,'archive terminal file cap differs')
        require(len({f['path'] for f in files})==len(files),'archive terminal duplicate file paths')
        reusable={}
        for generation in sorted(os.listdir(self.groups.fd)):
            m,_=self.ack_guard(generation)
            for part in m['files']:
                n='cases/'+m['case_id']+'/'+m['attempt']+'/'+part['name']
                reusable[(n,part['codec']['raw_bytes'],part['codec']['raw_sha256'])]=part['codec']
        archive_files=[]
        for f in files:
            n=_name(f['path']);size=_natural(f['bytes']);sha=_sha(f['sha256'])
            descriptor=reusable.get((n,size,sha))
            if descriptor is None:
                chunks=[];h=hashlib.sha256();offset=encoded_bytes=0
                while offset<size:
                    self.c.check();length=min(self.c.codec.CHUNK_BYTES,size-offset)
                    raw=read_range(f,offset,length)
                    require(isinstance(raw,bytes) and len(raw)==length,'archive terminal raw range truncated/oversized')
                    encoded=zlib.compress(raw,level=1);require(len(encoded)<=self.c.codec.MAX_ENCODED_CHUNK,'archive terminal encoded chunk cap differs')
                    part={'offset':offset,'raw_bytes':length,'raw_sha256':digest(raw),'encoded_bytes':len(encoded),'encoded_sha256':digest(encoded)}
                    self.c.codec.decode_chunk(encoded,part)
                    for backend in self.backends:self.publish(backend,'objects/'+part['encoded_sha256']+'.z',encoded)
                    chunks.append(part);h.update(raw);offset+=length;encoded_bytes+=len(encoded)
                require(offset==size and h.hexdigest()==sha,'archive terminal full file SHA/bytes differ')
                descriptor=self.c.codec.validate_manifest({'schema':1,'codec':self.c.codec.CODEC,'chunk_bytes':self.c.codec.CHUNK_BYTES,
                    'raw_bytes':size,'raw_sha256':sha,'encoded_bytes':encoded_bytes,'chunks':chunks})
            archive_files.append({'path':n,'bytes':size,'sha256':sha,'descriptor':descriptor,
                'scientific_status':'separately-accepted-generation' if (n,size,sha) in reusable else 'unvalidated-terminal-evidence'})
        index={'schema':'dams-compute-terminal-archive-index-v1','profile_sha256':self.profile_sha,'job_sha256':self.job_sha,
            'roster_sha256':digest(roster_raw),'files':archive_files,'science_complete':False,'full_study_gate':False}
        ir=blob(index);require(len(ir)<=MAX_METADATA_BYTES,'archive terminal index bound exceeded')
        number,_=self.c._reserve('metadata',len(ir)+1,digest(ir))
        self.c.state.write_new('terminal-archive-binding.json',blob({'reservation':number,'index_sha256':digest(ir)}))
        self.c.state.write_new('terminal-archive-index.json',ir)
        self.c.state.write_new('terminal-archive-roster.json',roster_raw)
        for backend in self.backends:
            self.publish(backend,'terminal/index.json',ir);self.publish(backend,'terminal/roster.json',roster_raw)
        self.terminal_guard();return index

    def terminal_guard(self):
        ir=read(self.c.state,'terminal-archive-index.json')[0];rr=read(self.c.state,'terminal-archive-roster.json')[0]
        p=parse(read(self.c.state,'terminal-archive-binding.json')[0]);index=parse(ir);roster=parse(rr)
        require(set(p)=={'reservation','index_sha256'} and p['index_sha256']==digest(ir)
            and parse(read(self.c.records,f'{p["reservation"]:08d}.json')[0])['object_sha256']==digest(ir),
            'archive terminal durable index reservation differs')
        self.c._history(self.c._head)
        require(index['schema']=='dams-compute-terminal-archive-index-v1' and index['profile_sha256']==self.profile_sha
            and index['job_sha256']==self.job_sha and index['roster_sha256']==digest(rr)
            and index['science_complete'] is False and index['full_study_gate'] is False,
            'archive terminal exact job/index differs')
        require([(f['path'],f['bytes'],f['sha256']) for f in index['files']]
            ==[(f['path'],f['bytes'],f['sha256']) for f in roster['files']],'archive terminal whole inventory differs')
        requests=[]
        for backend in self.backends:
            bid=backend.binding['backup_id']
            requests.extend([('fetch',bid,'terminal/index.json',len(ir),digest(ir)),('fetch',bid,'terminal/roster.json',len(rr),digest(rr))])
            for file in index['files']:
                for part in file['descriptor']['chunks']:
                    requests.append(('fetch',bid,'objects/'+part['encoded_sha256']+'.z',part['encoded_bytes'],part['encoded_sha256']))
        self.plan(requests)
        for backend in self.backends:
            self.fetch(backend,'terminal/index.json',len(ir),digest(ir));self.fetch(backend,'terminal/roster.json',len(rr),digest(rr))
            for file in index['files']:
                d=file['descriptor'];self.c.codec.validate_manifest(d)
                require(d['raw_bytes']==file['bytes'] and d['raw_sha256']==file['sha256'],'archive terminal descriptor differs')
                h=hashlib.sha256();size=0
                for part in d['chunks']:
                    raw=self.c.codec.decode_chunk(self.fetch(backend,'objects/'+part['encoded_sha256']+'.z',part['encoded_bytes'],part['encoded_sha256']),part)
                    h.update(raw);size+=len(raw)
                require(size==file['bytes'] and h.hexdigest()==file['sha256'],'archive terminal full decode differs')
        self.c.check();return {'storage_engineering_version':PROFILE_SCHEMA,'roster_sha256':digest(rr),
            'archive_index_sha256':digest(ir),'all_terminal_bytes_full_decode_verified':True,
            'two_independently_restorable_backups':True,'external_persistence':self.p['mode']=='external-persistent',
            'full_study_gate':False,'science_complete':False}


class _owned:
    def __init__(self,path):self.d=Directory(path)
    def __enter__(self):return self.d
    def __exit__(self,*args):self.d.close()
