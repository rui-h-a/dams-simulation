"""Single-entry compute-only wiring. No paid/provider fallback or new budget.

Root prepares immutable inputs and retains the existing Collector head. This
module handles local planning/preparation and one sealed collection operation;
provider creation/enrolment/activation/ACK delivery/closeout remain explicit.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import stat
import time

from dams_sim._committed_pages import Directory
from dams_sim.storage import digest, source_hash
from dams_sim.longitudinal_pipeline import driver_hash
from research_tools.compute_only_control import Collector, blob, parse, read, require
from research_tools.compute_only_transport import IAPTransport, ProcessStream

SCHEMA = 'dams-compute-only-entry-v1'
ROOT = Path(__file__).absolute().parents[1]
COMPONENTS = {'run.sh', 'cloud/prepare-compute-only.sh', 'cloud/compute-only-services.sh', 'cloud/compute-only-wait-source.sh',
    'research_tools/cloud_control.py', 'research_tools/cloud_worker.py',
    'research_tools/cloud_archive.py', 'research_tools/compute_only_worker.py',
    'research_tools/compute_only_control.py', 'research_tools/compute_only_transport.py',
    'research_tools/compute_only_entry.py', 'research_tools/compute_only_lifecycle.py', 'research_tools/compute_only_remote.py',
    'research_tools/compute_only_archive.py'}
ARCHIVE_COMPONENTS = {'research_tools/compute_only_backend_factory.py',
    'research_tools/compute_only_persistent_backends.py', 'research_tools/persistent_backend_budget_meter.py'}
FIELDS = {'schema', 'phase', 'stage_id', 'operation_id', 'package_manifest',
          'component_sha256', 'prepare'}
PREPARE_FIELDS = {'deadline_utc', 'max_seconds', 'max_source_bytes', 'max_source_files',
                  'max_log_bytes', 'receipt_file', 'offline', 'planned_termination_utc'}
COLLECT_FIELDS = {'admission', 'assignment', 'transport', 'minimum_head', 'head_receipt_dir',
                  'generation', 'max_manifest_bytes', 'max_seal_bytes',
                  'max_frames', 'max_payload_bytes', 'parent_generations'}
UNWIRED = {'launch': 'root paid-admission/provider create and fixed-expiry readback',
           'activate': 'root guest doctor/provider enrolment/runtime seal/controller lease and service activation',
           'ack': 'root exact ACK delivery to the frozen guest ack namespace',
           'closeout': 'root terminal non-case census/output retrieval and provider VM/disk absence/cost reconciliation'}


def natural(value, maximum):
    require(type(value) is int and 1 <= value <= maximum, 'entry bound must be an explicit positive integer')
    return value


class Entry:
    def __init__(self, c, *, root=ROOT, state=None):
        self.stack = ExitStack(); self.c = c; self.frozen = blob(c); self.root = self.stack.enter_context(_directory(root))
        self.state = None if state is None else self.stack.enter_context(_directory(state))
        self.observed = []; self.source_directories = {}; self.phase_guards = []
        try:
            value = c['compute_only']; self.options = value
            require(isinstance(value, dict) and value.get('schema') == SCHEMA, 'compute-only entry shape differs')
            phase = value.get('phase')
            require(phase in {'plan', 'prepare', 'collect', 'lifecycle', *UNWIRED}, 'unknown compute-only phase; no GCS fallback')
            factory_fields = {'archive_backend_factory'} if 'archive_backend_factory' in value else set()
            continuation_fields = {'phase_continuation'} if 'phase_continuation' in value else set()
            require(not factory_fields or phase in {'collect','lifecycle'}, 'archive factory belongs to an explicit collection/lifecycle operation')
            require(not continuation_fields or factory_fields, 'phase continuation belongs to explicit archive operations')
            require(set(value) == FIELDS | factory_fields | continuation_fields | ({'collect'} if phase == 'collect' else {'lifecycle'} if phase == 'lifecycle' else set()), 'entry fields differ')
            require(isinstance(value['operation_id'], str) and re.fullmatch('[0-9a-f]{64}', value['operation_id']),
                    'entry operation requires a retained immutable identity')
            stages = [s for s in c['stages'] if s['id'] == value['stage_id']]
            require(len(stages) == 1 and value['stage_id'] in c.get('execution_stage_ids', [s['id'] for s in c['stages']]),
                    'entry stage is not in the original reserved scientific plan')
            self.stage = stages[0]
            require((self.stage['spec'], self.stage['scale']) == (c['requested_spec'], c['requested_scale']),
                    'entry cannot alter scientific selection')
            from research_tools import cloud_control
            cloud_control.stage_capabilities(c, self.stage)
            p = value['prepare']; require(isinstance(p, dict) and set(p) == PREPARE_FIELDS, 'prepare options differ')
            natural(p['max_seconds'], 3600); require(p['max_seconds']>=7,'prepare bound must cover helper and independent reaping'); natural(p['max_source_bytes'], 1024**3)
            natural(p['max_source_files'], 32768); natural(p['max_log_bytes'], 8*1024**2)
            require(type(p['offline']) is bool, 'prepare offline selection must be boolean')
            self.allow_expired = phase=='lifecycle' and parse(self.reference(value['lifecycle'])).get('phase')=='closeout'
            self.termination = cloud_control.utc(p['planned_termination_utc'])
            self.prepare_deadline = cloud_control.utc(p['deadline_utc'])
            require(self.prepare_deadline <= self.termination <= cloud_control.utc(c['global_deadline_utc'])
                    and (self.allow_expired or datetime.now(timezone.utc) < (self.termination if phase=='lifecycle' else self.prepare_deadline)), 'entry fixed deadline expired or extends the original deadline')
            require(self.prepare_deadline.utcoffset().total_seconds() == 0, 'entry prepare deadline must be UTC')
            components = self.components = COMPONENTS | (ARCHIVE_COMPONENTS if factory_fields else set())
            pins = value['component_sha256']; require(isinstance(pins, dict) and set(pins) == components, 'entry component pins incomplete')
            for name, sha in pins.items():
                require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha), 'component SHA type differs')
                raw, anchor = _read_relative(self.root, name, p['max_source_bytes'], self.stack)
                require(digest(raw) == sha, 'entry component bytes differ')
                self.observed.append((name, raw, anchor))
            manifest_raw = self.reference(value['package_manifest']); manifest = parse(manifest_raw)
            require(isinstance(manifest, dict) and set(manifest) == {'commit', 'source_files_sha256'}
                    and manifest['commit'] == c['source_commit'], 'entry approved source manifest differs')
            files = manifest['source_files_sha256']
            require(isinstance(files, dict) and components <= set(files) and len(files) <= p['max_source_files'], 'source roster incomplete')
            require({x.relative_to(self.root.path).as_posix() for x in (self.root.path/'dams_sim').glob('*.py')}
                    == {x for x in files if x.startswith('dams_sim/') and '/' not in x[9:] and x.endswith('.py')},
                    'source core roster differs')
            count = 0
            for name, sha in files.items():
                require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha), 'manifest SHA differs')
                raw, anchor = _read_relative(self.root, name, p['max_source_bytes']-count, self.stack)
                count += len(raw); require(count <= p['max_source_bytes'] and digest(raw) == sha, 'listed source bytes differ')
                self.observed.append((name, raw, anchor))
            require(all(files[name] == pins[name] for name in components), 'package and component pins disagree')
            require(Path(__file__).absolute() == self.root.path/'research_tools/compute_only_entry.py', 'entry executes another source root')
            self.manifest_sha = digest(manifest_raw)
            for item in self.observed:
                if isinstance(item[0],str):
                    directory=(self.root.path/item[0]).parent
                    while directory!=self.root.path:
                        info=directory.lstat(); require(stat.S_ISDIR(info.st_mode),'entry source ancestor is not regular directory')
                        self.source_directories[directory]=(info.st_dev,info.st_ino);directory=directory.parent
            self.check()
        except BaseException:
            self.stack.close(); raise

    def reference(self, ref):
        require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'} and isinstance(ref['path'], str)
                and isinstance(ref['sha256'], str) and re.fullmatch('[0-9a-f]{64}', ref['sha256']), 'entry reference shape differs')
        path = Path(ref['path']); require(path.is_absolute() and all(x not in ('.','..') for x in path.parts), 'entry references must be absolute')
        directory = self.stack.enter_context(_directory(path.parent)); raw, anchor = read(directory, path.name)
        require(digest(raw) == ref['sha256'], 'entry retained reference bytes differ')
        self.observed.append((directory, path.name, raw, anchor)); return raw

    def check(self, *, full=False, allow_expired=False):
        require(blob(self.c) == self.frozen, 'entry frozen private plan changed')
        self.root.check()
        if self.state is not None: self.state.check()
        from research_tools.cloud_control import utc
        require(allow_expired or getattr(self,'allow_expired',False) or datetime.now(timezone.utc) < utc(self.c['global_deadline_utc']), 'entry original global deadline')
        for item in self.observed:
            if isinstance(item[0], Directory):
                directory, name, raw, anchor = item; current, current_anchor = read(directory, name)
            else:
                name, raw, anchor = item
                path=self.root.path/name
                info=path.lstat(); require(stat.S_ISREG(info.st_mode) and info.st_nlink==1, 'entry source leaf type changed')
                from dams_sim._committed_pages import identity
                current_anchor=identity(info); current=raw
                if full: current,current_anchor=_read_relative(self.root,name,len(raw),self.stack)
            require(current == raw and current_anchor == anchor, 'entry approved source/input drift')
        for path, old in self.source_directories.items():
            info=path.lstat(); require(stat.S_ISDIR(info.st_mode) and (info.st_dev,info.st_ino)==old,'entry source ancestor changed')
        for guard in self.phase_guards: guard.check()

    def prepare_argv(self):
        p = self.options['prepare']
        argv = ['/bin/bash', str(self.root.path/'cloud/prepare-compute-only.sh'),
                '--bootstrap-sha256', self.options['component_sha256']['cloud/prepare-compute-only.sh'],
                '--source-root', str(self.root.path), '--manifest-sha256', self.manifest_sha,
                '--source-commit', self.c['source_commit'], '--spec', self.stage['spec'], '--scale', str(self.stage['scale'])]
        for key in ('deadline_utc', 'max_seconds', 'max_source_bytes', 'max_source_files', 'max_log_bytes', 'receipt_file'):
            argv.extend(['--'+key.replace('_','-'), str(p[key])])
        if p['offline']: argv.append('--offline')
        return argv

    def plan(self):
        from research_tools.cloud_control import create_compute_only_command, stage_cost, digest as plan_digest
        s = self.stage
        e = {'instance_name':'dams-'+plan_digest([self.c['authorization_id'], s['id']])[:24],
             'termination_utc':self.options['prepare']['planned_termination_utc']}
        # No provider command or ledger.reserve occurs. Creation intent is just argv.
        argv = create_compute_only_command(self.c, s, e, self.c['zones'][0],
            self.root.path/'cloud/compute-only-wait-source.sh',
            startup_sha256=self.options['component_sha256']['cloud/compute-only-wait-source.sh'])
        self.check()
        return {'mode':'compute-only-local-plan-no-reservation', 'stage_id':s['id'],
                'source_commit':self.c['source_commit'], 'source_sha256':source_hash(), 'pipeline_driver_sha256':driver_hash(),
                'global_deadline_utc':self.c['global_deadline_utc'], 'stage_reserved_usd_upper':str(stage_cost(self.c,s)),
                'create_argv_intent_only':argv, 'prepare_argv':self.prepare_argv(),
                'startup_only_creates_private_namespaces_no_science':True,
                'source_must_be_uploaded_before_prepare':True, 'remaining_root_phases':UNWIRED,
                'science_complete':False, 'paid_action_performed':False}

    def prepare(self):
        require(Path(self.options['package_manifest']['path'])==self.root.path/'source-manifest.json','guest prepare requires its approved uploaded manifest at the fixed source root')
        self.check()
        env = dict(os.environ)
        for name in ('DAMS_CLOUD_PRIVATE_CONFIG','DAMS_CLOUD_STATE_DIR','DAMS_TRANSFER_CONFIG','GOOGLE_APPLICATION_CREDENTIALS'):
            env.pop(name,None)
        # The frozen standalone helper owns deadline/PG/output cleanup and never starts science.
        remaining=min(self.options['prepare']['max_seconds'],self.prepare_deadline.timestamp()-time.time())
        require(remaining>2,'prepare deadline cannot cover reaping')
        require(remaining>=7,'prepare remaining deadline cannot cover nested cleanup')
        argv=self.prepare_argv()
        # Helper owns its separately created dependency process group. Give it
        # an earlier FIXED deadline, leaving four seconds before outer cutoff
        # for its two-second reap and the outer supervisor. Never extend D.
        helper_deadline=datetime.fromtimestamp(time.time()+remaining-4,timezone.utc).isoformat()
        argv[argv.index('--deadline-utc')+1]=helper_deadline
        argv[argv.index('--max-seconds')+1]=str(self.options['prepare']['max_seconds']-4)
        results=[]
        def popen(argv,**kwargs): return subprocess.Popen(argv,env=env,**kwargs)
        stream=ProcessStream(argv,bound=self.options['prepare']['max_log_bytes'],stderr_bound=65536,
            cutoff=time.monotonic()+remaining-2,check=self.check,completed=results.append,popen=popen)
        try:
            while stream.read(65536): pass
        finally: stream.close()
        require(results and results[-1]['status']=='accepted-stream','bounded prepare-only helper failed')
        self.check(full=True)
        path = Path(self.options['prepare']['receipt_file'])
        directory = self.stack.enter_context(_directory(path.parent)); raw, _ = read(directory, path.name)
        value = parse(raw)
        require(value.get('source_manifest_sha256') == self.manifest_sha and value.get('source_commit') == self.c['source_commit'],
                'prepared source receipt differs')
        return {'mode':'compute-only-prepared-no-science', 'receipt_sha256':digest(raw),
                'helper_deadline_utc':helper_deadline,'original_prepare_deadline_utc':self.options['prepare']['deadline_utc'],
                'helper_bounds_shortened_only_for_nested_reaping':True,'science_complete':False}

    def collect(self, *, archive_backends=None):
        require(self.c.get('paid_actions_authorized') is True, 'compute-only IAP collection requires root paid-action authorization for the original existing stage')
        require(self.state is not None, 'compute-only collection needs the existing root state directory')
        o = self.options['collect']; require(isinstance(o,dict) and set(o)==COLLECT_FIELDS, 'collection options differ')
        natural(o['max_frames'],1024); natural(o['max_payload_bytes'],1024**3)
        natural(o['max_manifest_bytes'],32*1024**2); natural(o['max_seal_bytes'],32*1024**2)
        require(isinstance(o['generation'],str) and re.fullmatch('[0-9a-f]{64}',o['generation']), 'sealed generation differs')
        require(isinstance(o['parent_generations'],dict) and all(isinstance(k,str) and isinstance(v,str)
                and re.fullmatch('[0-9a-f]{64}',k) and re.fullmatch('[0-9a-f]{64}',v) for k,v in o['parent_generations'].items()),
                'completed parent generation references differ')
        admission_raw=self.reference(o['admission']); assignment_raw=self.reference(o['assignment']); transport_raw=self.reference(o['transport'])
        admission=parse(admission_raw); transport=parse(transport_raw)
        require(all(admission[k]==self.stage[k] for k in ('assignment_sha256','spec_sha256','inventory_sha256')),
                'collector assignment/spec/inventory differs from the root stage pins')
        require(admission['stage_id']==self.stage['id'] and admission['source_sha256']==source_hash()
                and admission['deadline_utc']==transport['deadline_utc']
                and transport['project']==self.c['project'] and transport['zone'] in self.c['zones'], 'collector/transport stage source or target differs')
        from research_tools.cloud_control import utc
        require(utc(admission['deadline_utc'])<=self.termination<=utc(self.c['global_deadline_utc']), 'collection deadline extends fixed node expiry')
        minimum=None if o['minimum_head'] is None else parse(self.reference(o['minimum_head']))
        if admission.get('schema') == 'dams-compute-only-archive-collector-v1':
            continuation = self.phase_continuation(o['admission'], assignment_raw)
            require(transport['remote_runtime_sha256'] == continuation.runtime_sha
                    and transport['provider_identity_sha256'] == continuation.provider_sha
                    and transport['instance_id'] == str(continuation.vm['id'])
                    and transport['remote_transfer_config_sha256'] == continuation.transfer_sha,
                    'archive collection transport differs from actual enrolled continuation')
        if archive_backends is None: archive_backends=self.archive_backends(o['admission'])
        retained=self.stack.enter_context(_directory(o['head_receipt_dir']))
        name=self.options['operation_id']+'-BEGIN.json'
        self.check(); self.state.write_new(name,blob({'config_sha256':digest(self.frozen),'generation':o['generation'],'science_complete':False}))
        def retain(head,attempt):
            self.check(); raw=blob({'collector_admission_sha256':o['admission']['sha256'],'head':head,'attempt':attempt})
            name=f'{head["sequence"]:08d}.json'; retained.write_new(name,raw)
            require(read(retained,name)[0]==raw,'retained transport head readback differs')
            self.check(); return dict(head)
        with Collector(o['admission']['path'],admission_sha256=o['admission']['sha256'],assignment_raw=assignment_raw,minimum_head=minimum,archive_backends=archive_backends) as collector:
            require(collector.archive is None or collector.archive.p['mode']=='external-persistent','root production collection cannot use fixture backups')
            with IAPTransport(o['transport']['path'],config_sha256=o['transport']['sha256'],collector=collector,retain=retain) as iap:
                manifest,t1=iap.metadata(o['generation'],'manifest.json',o['max_manifest_bytes'])
                seal,t2=iap.metadata(o['generation'],'seal.json',o['max_seal_bytes'])
                iap.bind_group(manifest,seal)
                with iap.batch_chunks(max_frames=o['max_frames'],max_payload_bytes=o['max_payload_bytes']) as batch:
                    accepted=collector.collect(manifest,seal,batch.callback,metadata_tickets=[t1,t2],parent_generations=o['parent_generations'])
                self.check(); head=collector.head
                result={'schema':'dams-compute-only-entry-collected-v1','collector_admission_sha256':collector.admission_sha,
                        'generation':o['generation'],'head':head,
                        'accepted':{k:v for k,v in accepted.items() if k!='ack_bytes'},
                        'ack_sha256':digest(accepted['ack_bytes']),'ack_bytes':len(accepted['ack_bytes']),
                        'ack_delivery_performed':False,'whole_pipeline_non_case_outputs_collected':False,'science_complete':False}
                raw=blob(result); self.state.write_new(self.options['operation_id']+'-COLLECTED.json',raw)
                require(read(self.state,self.options['operation_id']+'-COLLECTED.json')[0]==raw,'entry publication readback differs')
                self.check(full=True); return result

    def archive_backends(self, admission_ref):
        from research_tools.compute_only_control import ARCHIVE_COLLECTOR_SCHEMA
        admission = parse(self.reference(admission_ref))
        ref = self.options.get('archive_backend_factory')
        if admission.get('schema') != ARCHIVE_COLLECTOR_SCHEMA:
            require(ref is None, 'legacy Collector cannot enable an archive factory')
            return None
        require(ref is not None, 'archive Collector needs an explicit pinned backend factory')
        def construct():
            from research_tools.compute_only_backend_factory import Factory
            factory = Factory(self, ref, admission_ref)
            self.backend_factory = factory; self.stack.callback(factory.close)
            return factory
        if self.options['phase'] == 'lifecycle':
            operation = parse(self.reference(self.options['lifecycle']))
            require(operation.get('phase') != 'launch', 'launch requires its original metadata Collector; actual archive identity does not exist yet')
            def resolve():
                require(getattr(self, 'lifecycle_admitted', None) == admission_ref['sha256'],
                        'actual lifecycle provider/runtime and phase continuation must precede archive construction')
                self.check(full=True)
                return construct()()
            return resolve
        return construct()

    def phase_continuation(self, admission_ref, assignment_raw):
        ref = self.options.get('phase_continuation')
        require(ref is not None, 'archive phase requires retained launch continuation; no new pool')
        value = PhaseContinuation(self, ref, admission_ref, assignment_raw)
        self.stack.callback(value.close); self.phase_guards.append(value)
        return value

    def close(self): self.stack.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()


def _directory(path):
    class Managed:
        def __enter__(self): self.value=Directory(Path(path)); return self.value
        def __exit__(self,*args): self.value.close()
    return Managed()


def _read_relative(root,name,bound,stack):
    require(isinstance(name,str) and not name.startswith('/') and all(x not in ('','.','..') for x in name.split('/')), 'unsafe source name')
    with ExitStack() as temporary:
        directory=root
        for part in name.split('/')[:-1]: directory=temporary.enter_context(_directory(directory.path/part))
        raw,anchor=read(directory,name.split('/')[-1],bound)
        directory.check()
        return raw,anchor


class PhaseContinuation:
    """One original launch pool may bind exactly one reduced archive pool.

    The externally pinned latest launch head is not replaced by a fresh local
    baseline. A native reservation binds the bridge before publication; losing
    that publication fails closed rather than inventing a new allowance.
    """
    FIELDS = {'schema', 'launch_admission', 'launch_head_receipt', 'approval',
              'ledger_path', 'archive_admission_sha256', 'provider', 'doctor',
              'runtime', 'transfer'}

    def __init__(self, entry, ref, admission_ref, assignment_raw):
        self.entry = entry; self.stack = ExitStack(); self.closed = False
        try:
            self.raw = entry.reference(ref); self.value = v = parse(self.raw)
            require(isinstance(v, dict) and set(v) == self.FIELDS
                    and v['schema'] == 'dams-compute-launch-collector-continuation-v1'
                    and v['archive_admission_sha256'] == admission_ref['sha256'], 'phase continuation exact admission binding differs')
            self.new = new = parse(entry.reference(admission_ref))
            old = parse(entry.reference(v['launch_admission']))
            from research_tools.compute_only_control import COLLECTOR_SCHEMA, ARCHIVE_COLLECTOR_SCHEMA
            require(old.get('schema') == COLLECTOR_SCHEMA and new.get('schema') == ARCHIVE_COLLECTOR_SCHEMA,
                    'phase continuation needs an original legacy launch pool and new archive admission')
            same = ('stage_id','source_sha256','pipeline_driver_sha256','spec_sha256','inventory_sha256',
                    'assignment_sha256','codec_source_sha256','codec_dependencies_sha256','collector_source_sha256','deadline_utc')
            require(all(old[k] == new[k] for k in same) and old['stage_id'] == entry.stage['id']
                    and old['source_sha256'] == source_hash() and old['pipeline_driver_sha256'] == driver_hash()
                    and all(old[k] == entry.stage[k] for k in ('assignment_sha256','spec_sha256','inventory_sha256')),
                    'phase source/assignment/codec/deadline cannot change')
            require(digest(assignment_raw) == old['assignment_sha256'], 'phase assignment differs')
            launch_paths = [Path(old[k]) for k in ('state_dir','cache_dir')]
            archive_paths = [Path(new[k]) for k in ('state_dir','cache_dir')]
            require(all(a != b and a not in b.parents and b not in a.parents
                        for a in launch_paths for b in archive_paths),
                    'legacy Collector namespace cannot be relabelled or nested into archive')
            receipt = parse(entry.reference(v['launch_head_receipt']))
            require(isinstance(receipt,dict) and set(receipt)=={'head','attempt'}, 'retained launch head receipt shape differs')
            self.floor = floor = receipt['head']
            require(isinstance(floor,dict) and set(floor)=={'sequence','sha256','requests','bytes'}
                    and all(type(floor[k]) is int and floor[k]>=0 for k in ('sequence','requests','bytes'))
                    and floor['sequence']==floor['requests'] and floor['sequence']>0
                    and isinstance(floor['sha256'],str) and re.fullmatch('[0-9a-f]{64}',floor['sha256']), 'retained launch head is not an exact typed native floor')
            self.approval = parse(entry.reference(v['approval']))
            self.ledger_dir = self.stack.enter_context(_directory(Path(v['ledger_path']).parent))
            self.ledger_name = Path(v['ledger_path']).name
            self._hold()
            self.vm, self.runtime_sha, self.provider_sha, self.transfer_sha = self._enrollment()
            self.launch = self.stack.enter_context(Collector(v['launch_admission']['path'],
                admission_sha256=v['launch_admission']['sha256'], assignment_raw=assignment_raw, minimum_head=floor))
            self.commit = blob({'schema':'dams-compute-launch-archive-bridge-v1', 'continuation_sha256':digest(self.raw),
                'archive_admission_sha256':admission_ref['sha256'], 'launch_admission_sha256':v['launch_admission']['sha256'],
                'launch_head':floor})
            self.marker = 'archive-continuation.json'; self.marker_bound = len(self.commit)+1
            self.expected = {'requests':floor['requests']+1, 'bytes':floor['bytes']+self.marker_bound}
            require(all(type(old[k]) is int and type(new[k]) is int and 0 < new[k] <= old[k]-self.expected[n]
                        for k,n in (('max_fetch_requests','requests'),('max_fetch_bytes','bytes'))),
                    'archive allowance exceeds original launch pool remainder')
            with self.launch.locked():
                current = self.launch.head
                present = self.marker in os.listdir(self.launch.state.fd)
                if current == floor:
                    require(not present, 'phase bridge reservation history missing; no reset')
                    self.launch._reserve('metadata',self.marker_bound,digest(self.commit))
                    self.launch.state.write_new(self.marker,self.commit)
                else:
                    require(present, 'phase bridge publication missing; no new pool or automatic retry')
                record = parse(read(self.launch.records,f'{floor["sequence"]+1:08d}.json')[0])
                require(record == {'sequence':floor['sequence']+1,'previous_sha256':floor['sha256'],
                    'kind':'metadata','object_sha256':digest(self.commit),'reserved_bytes':self.marker_bound,
                    'admission_sha256':v['launch_admission']['sha256']}, 'phase bridge native reservation differs')
                self.committed_head = self.launch.head
            self.check()
        except BaseException:
            self.close(); raise

    def _hold(self):
        from research_tools import cloud_control as cc
        d = parse(read(self.ledger_dir,self.ledger_name)[0]); c=self.entry.c; s=self.entry.stage; a=self.approval
        require(c.get('paid_actions_authorized') is True, 'phase requires original root paid authorization')
        require(isinstance(d,dict) and d['authorization_id']==c['authorization_id'] and d['deadline_utc']==c['global_deadline_utc']
            and all(cc.money(d[k])==cc.money(c[v]) for k,v in (('cap_usd','budget_cap_usd'),('reserve_usd','reserve_usd'),('prior_spend_usd','prior_spend_usd')))
            and s['id'] in d['entries'], 'phase original budget/hold/deadline missing or changed')
        plan=cc.digest({'stages':c['stages'],'source':c['source_commit'],
            'control':{k:c.get(k) for k in ('project','region','zones','bucket','service_account','subnet','image','image_id',
                'network_mode','gcloud_configuration','gcloud_account','max_create_attempts','cost_margin')}})
        require(d.get('frozen_plan_sha256')==plan, 'phase original frozen plan differs')
        e=d['entries'][s['id']]
        intent=cc.digest({'source':c['source_commit'],'stage':s,'cloud_resource_identity':{k:c[k] for k in
            ('project','region','bucket','service_account','subnet','image','image_id','network_mode','gcloud_configuration','gcloud_account')}})
        require(set(a)=={'schema','authorization_id','stage_id','intent_sha256','reserved_usd','termination_utc','source_manifest_sha256','paid_actions_authorized'}
            and a['schema']=='dams-root-compute-live-approval-v1' and a['paid_actions_authorized'] is True
            and a['authorization_id']==c['authorization_id'] and a['stage_id']==s['id']
            and a['source_manifest_sha256']==self.entry.manifest_sha and e['intent_sha256']==intent
            and all(a[k]==e[k] for k in ('intent_sha256','reserved_usd','termination_utc'))
            and cc.utc(e['termination_utc'])==self.entry.termination and cc.stage_cost(c,s)<=cc.money(e['reserved_usd'])
            and cc.utc(self.new['deadline_utc'])<=self.entry.termination<=cc.utc(d['deadline_utc']),
            'phase retained intent/hold/source/node expiry differs')
        self.hold=e

    def _enrollment(self):
        from research_tools import cloud_control as cc
        from research_tools.compute_only_worker import validate_runtime, verify_identity
        p=parse(self.entry.reference(self.value['provider'])); doctor=parse(self.entry.reference(self.value['doctor']))
        runtime_raw=self.entry.reference(self.value['runtime']);transfer_raw=self.entry.reference(self.value['transfer'])
        r=parse(runtime_raw);t=parse(transfer_raw);vm=p['vm'];disk=p['disk']
        require(runtime_raw==blob(r) and transfer_raw==blob(t), 'phase runtime/transfer canonical bytes differ')
        require(p['termination_utc']==self.hold['termination_utc'] and p['approval_sha256']==self.value['approval']['sha256'],
                'phase provider original approval/expiry differs')
        cc.validate_compute_only_instance(self.entry.c,self.entry.stage,self.hold,vm)
        validate_runtime(r); provider_sha=digest(blob({'vm':vm,'disk':disk})); runtime_sha=digest(runtime_raw)
        require(r['source_commit']==self.entry.c['source_commit'] and r['source_manifest_sha256']==self.entry.manifest_sha
            and r['spec']==self.entry.stage['spec'] and r['scale']==self.entry.stage['scale']
            and r['provider_identity_sha256']==provider_sha and r['deadline_utc']==self.hold['termination_utc']
            and r['transfer_config_sha256']==digest(transfer_raw) and t['stage_id']==self.entry.stage['id']
            and t['expected_source_sha256']==source_hash() and t['deadline_utc']==r['deadline_utc']
            and doctor['provider_identity_sha256']==provider_sha and doctor['source_manifest_sha256']==self.entry.manifest_sha
            and str(doctor['measurements']['instance_id'])==str(vm['id']), 'phase actual provider/runtime/source/transfer differs')
        verify_identity(r,doctor['measurements'])
        s=self.entry.stage;c=self.entry.c
        require(r['machine_type']==s['machine_type'] and r['purchase_mode']==cc.stage_capabilities(c,s)['purchase_mode']
            and r['expected_guest']==s.get('expected_guest',{}) and r.get('preservation_profile')==s.get('preservation_profile'),
            'phase frozen hardware/preservation differs')
        dates=cc.stage_science_deadlines(s,self.hold['termination_utc'])
        if 'preservation_profile' in s:
            require(set(dates)=={'deadline_utc','stop_cutoff_utc'}
                and all(s['runtime_limits'].get(k)==v for k,v in dates.items()), 'phase original scientific dates differ')
            limits=dict(s['runtime_limits'])
        else:limits={**s['runtime_limits'],**dates}
        require(r['runtime_limits']==limits and r['pipeline_stop_grace_seconds']==s.get('pipeline_stop_grace_seconds',60)
            and cc.utc(r['watchdog_shutdown_utc']).timestamp()==cc.utc(self.hold['termination_utc']).timestamp()-30,
            'phase runtime changes admitted resources/science dates/grace/watchdog')
        require(0<t['max_spool_bytes']<=int(cc.money(s['max_result_gib'])*2**30)
            and 0<t['max_transfer_bytes']<=int(cc.money(s['max_egress_gib'])*2**30)
            and r['controller_lease_timeout_seconds']<=s['max_seconds'], 'phase runtime exceeds original spool/transfer/lease envelope')
        self.runtime_value=r;self.provider_value=p
        return vm,runtime_sha,provider_sha,digest(transfer_raw)

    def check(self):
        require(not self.closed,'phase continuation closed'); self._hold()
        self.launch.check();require(self.launch.head==self.committed_head
            and self.committed_head['requests']==self.expected['requests'] and self.committed_head['bytes']==self.expected['bytes'],
            'launch latest head changed after phase continuation')
        require(read(self.launch.state,self.marker)[0]==self.commit,'phase bridge publication changed')

    def close(self):
        if not self.closed:self.closed=True;self.stack.close()



def execute(c,folder):
    with Entry(c,state=folder) as entry:
        succeeded=False
        try:
            phase=entry.options['phase']
            if phase=='plan': result=entry.plan()
            elif phase=='prepare': result=entry.prepare()
            elif phase=='collect': result=entry.collect()
            elif phase=='lifecycle':
                from research_tools.compute_only_lifecycle import execute as live_execute
                options=parse(entry.reference(entry.options['lifecycle']))
                result=live_execute(entry, options, archive_backends=entry.archive_backends(options['collector_admission']))
            else: raise ValueError('COMPUTE_ONLY_PHASE_UNWIRED:'+phase+':'+UNWIRED[phase])
            succeeded=True
            return result
        finally:
            factory=getattr(entry,'backend_factory',None)
            if factory is not None and factory.meter is not None:
                raw=blob({'schema':'dams-compute-archive-operation-meter-v1',
                    'operation_id':entry.options['operation_id'],'operation_succeeded':succeeded,
                    'meter':factory.snapshot(),'science_complete':False})
                name=entry.options['operation_id']+'-BACKEND-METER.json'
                entry.check(full=True); entry.state.write_new(name,raw)
                require(read(entry.state,name)[0]==raw,'archive operation meter readback differs')
                entry.check(full=True)
