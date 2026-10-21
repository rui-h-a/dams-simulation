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
    'research_tools/compute_only_entry.py', 'research_tools/compute_only_lifecycle.py', 'research_tools/compute_only_remote.py'}
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
        self.observed = []; self.source_directories = {}
        try:
            value = c['compute_only']; self.options = value
            require(isinstance(value, dict) and value.get('schema') == SCHEMA, 'compute-only entry shape differs')
            phase = value.get('phase')
            require(phase in {'plan', 'prepare', 'collect', 'lifecycle', *UNWIRED}, 'unknown compute-only phase; no GCS fallback')
            require(set(value) == FIELDS | ({'collect'} if phase == 'collect' else {'lifecycle'} if phase == 'lifecycle' else set()), 'entry fields differ')
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
            pins = value['component_sha256']; require(isinstance(pins, dict) and set(pins) == COMPONENTS, 'entry component pins incomplete')
            for name, sha in pins.items():
                require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha), 'component SHA type differs')
                raw, anchor = _read_relative(self.root, name, p['max_source_bytes'], self.stack)
                require(digest(raw) == sha, 'entry component bytes differ')
                self.observed.append((name, raw, anchor))
            manifest_raw = self.reference(value['package_manifest']); manifest = parse(manifest_raw)
            require(isinstance(manifest, dict) and set(manifest) == {'commit', 'source_files_sha256'}
                    and manifest['commit'] == c['source_commit'], 'entry approved source manifest differs')
            files = manifest['source_files_sha256']
            require(isinstance(files, dict) and COMPONENTS <= set(files) and len(files) <= p['max_source_files'], 'source roster incomplete')
            require({x.relative_to(self.root.path).as_posix() for x in (self.root.path/'dams_sim').glob('*.py')}
                    == {x for x in files if x.startswith('dams_sim/') and '/' not in x[9:] and x.endswith('.py')},
                    'source core roster differs')
            count = 0
            for name, sha in files.items():
                require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha), 'manifest SHA differs')
                raw, anchor = _read_relative(self.root, name, p['max_source_bytes']-count, self.stack)
                count += len(raw); require(count <= p['max_source_bytes'] and digest(raw) == sha, 'listed source bytes differ')
                self.observed.append((name, raw, anchor))
            require(all(files[name] == pins[name] for name in COMPONENTS), 'package and component pins disagree')
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

    def collect(self):
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
        retained=self.stack.enter_context(_directory(o['head_receipt_dir']))
        name=self.options['operation_id']+'-BEGIN.json'
        self.check(); self.state.write_new(name,blob({'config_sha256':digest(self.frozen),'generation':o['generation'],'science_complete':False}))
        def retain(head,attempt):
            self.check(); raw=blob({'collector_admission_sha256':o['admission']['sha256'],'head':head,'attempt':attempt})
            name=f'{head["sequence"]:08d}.json'; retained.write_new(name,raw)
            require(read(retained,name)[0]==raw,'retained transport head readback differs')
            self.check(); return dict(head)
        with Collector(o['admission']['path'],admission_sha256=o['admission']['sha256'],assignment_raw=assignment_raw,minimum_head=minimum) as collector:
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



def execute(c,folder):
    with Entry(c,state=folder) as entry:
        phase=entry.options['phase']
        if phase=='plan': return entry.plan()
        if phase=='prepare': return entry.prepare()
        if phase=='collect': return entry.collect()
        if phase=='lifecycle':
            from research_tools.compute_only_lifecycle import execute as live_execute
            return live_execute(entry, parse(entry.reference(entry.options['lifecycle'])))
        raise ValueError('COMPUTE_ONLY_PHASE_UNWIRED:'+phase+':'+UNWIRED[phase])
