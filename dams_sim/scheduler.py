"""Resource-bounded independent-world scheduler with durable automatic resume."""
from __future__ import annotations
import dataclasses
import json
import multiprocessing
import os
from pathlib import Path
import signal
import time

from .cli import run_world
from .config import Config
from .model import Model
from .runtime import RuntimeLimits, available_memory
from .spec import case_key, scientific_config, workload
from .storage import atomic_json, canonical, digest, file_digest, source_hash, verify_outputs


def restore_checkpoint(attempt: Path, config: Config, *, storage_dir=None,page_options=None,known_latest_floor=None,native_owner_dir=None):
    index_path = attempt/'checkpoint-index.json'
    if not index_path.is_file(): return None, None
    index = json.loads(index_path.read_text())
    if index['source_sha256'] != source_hash() or index['config_sha256'] != digest(canonical(config.to_dict())):
        raise ValueError('checkpoint source/config mismatch')
    # An invalid latest snapshot is never silently treated as a new full run.
    item = index['snapshots'][0]
    path = attempt/item['file']
    if not path.resolve().is_relative_to(attempt.resolve()) or file_digest(path) != item['sha256']:
        raise ValueError('checkpoint integrity mismatch')
    value = json.loads(path.read_text())
    if config.longitudinal is not None:
        native=value.get('native_checkpoint')
        if native is not None:
            if page_options is None:raise ValueError('native checkpoint options and external owner record required')
            from .native_checkpoint_owner import CheckpointOwner,default_owner_directory
            owner=CheckpointOwner(native_owner_dir or default_owner_directory(page_options,index['config_sha256']),
                source_sha256=index['source_sha256'],config_sha256=index['config_sha256'])
            try:
                latest=owner.latest(minimum_floor=known_latest_floor)
                if latest is None:raise ValueError('native checkpoint external latest owner is missing')
                # The latest index must bind the separately published latest CP;
                # an incomplete generation never becomes an older recovery.
                if latest['descriptor']!=item:raise ValueError('native checkpoint index differs from latest external owner')
                model=Model.restore_checkpoint(path,storage_dir=storage_dir,expected_config=config,page_options=page_options,
                    known_latest_floor=latest['floor'],native_owner_dir=owner.path)
                if model.day!=item['day']:raise ValueError('longitudinal checkpoint day differs from index')
                return model,{'parent_attempt':attempt.name,'checkpoint_file':item['file'],'checkpoint_sha256':item['sha256'],'parent_index_sha256':file_digest(index_path)}
            finally:owner.close()
        for part in item.get('files',[]):
            file=attempt/part['file']
            if not file.resolve().is_relative_to(attempt.resolve()) or file_digest(file)!=part['sha256'] or file.stat().st_size!=part['bytes']:
                raise ValueError('longitudinal checkpoint group integrity mismatch')
        if len(item.get('files',[]))!=2:raise ValueError('longitudinal checkpoint sidecar inventory missing')
        if {part['file'] for part in item['files']}!={item['file'],value['ledger']['file']}:raise ValueError('longitudinal checkpoint index group roster differs')
        model=Model.restore_checkpoint(path,storage_dir=storage_dir,expected_config=config)
        if model.day!=item['day']:raise ValueError('longitudinal checkpoint day differs from index')
        return model,{'parent_attempt':attempt.name,'checkpoint_file':item['file'],'checkpoint_sha256':item['sha256'],'parent_index_sha256':file_digest(index_path)}
    if value['source_sha256'] != source_hash() or value['config_sha256'] != index['config_sha256'] or value['state']['config'] != json.loads(canonical(config.to_dict())):
        raise ValueError('checkpoint envelope/state mismatch')
    model = Model.restore(value['state'])
    if model.day != item['day']: raise ValueError('checkpoint day differs from index')
    return model, {'parent_attempt':attempt.name, 'checkpoint_file':item['file'],
                   'checkpoint_sha256':item['sha256'], 'parent_index_sha256':file_digest(index_path)}


def branch_identity(origin):
    if origin is None:return None
    required={'parent_checkpoint','parent_case_id','parent_source_sha256','parent_config_sha256','parent_day','parent_state_semantic_sha256'}
    if set(origin)!=required:raise ValueError('branch origin descriptor fields differ')
    return {k:v for k,v in origin.items() if k!='parent_checkpoint'}


def _worker(config_value, attempt_value, previous_value, limits_value, driver_hash, remaining_seconds, branch_origin=None,page_options=None,known_latest_floor=None):
    # Fresh process per world, so another world's RSS high-water cannot reject it.
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[key] = '1'
    requested = False
    worker_started = time.monotonic()
    def stop(signum, frame):
        nonlocal requested
        requested = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    attempt = Path(attempt_value)
    config = Config.from_dict(config_value)
    limits = RuntimeLimits.from_dict(limits_value)
    if page_options is not None:
        from .native_checkpoint_owner import CheckpointOwner,default_owner_directory
        from .native_page_backend import handle
        owner=CheckpointOwner(default_owner_directory(page_options,digest(canonical(config.to_dict()))),source_sha256=source_hash(),config_sha256=digest(canonical(config.to_dict())))
        try:
            latest=owner.latest(minimum_floor=known_latest_floor)
            if latest is not None:known_latest_floor=latest['floor']
        finally:owner.close()
        # Runtime branch names distinguish attempt recovery from a scientific
        # fork. They never alter Config, context, world identity or branch_origin.
        page_options=dataclasses.replace(page_options,branch='run-'+digest(canonical(str(attempt.absolute())))[:32])
    try:
        model, origin = restore_checkpoint(Path(previous_value),config,storage_dir=attempt if config.longitudinal is not None else None,
            **({'page_options':page_options,'known_latest_floor':known_latest_floor} if page_options is not None else {})) if previous_value else (None,None)
        identity=branch_identity(branch_origin)
        if identity is not None:
            if config.longitudinal is None:raise ValueError('branch origin requires a longitudinal case')
            if model is None:
                import tempfile
                with tempfile.TemporaryDirectory(prefix='parent-',dir=attempt) as working:
                    parent_options=None
                    parent_floor=None
                    if page_options is not None:
                        from .longitudinal_model import load_snapshot_envelope
                        envelope,_=load_snapshot_envelope(branch_origin['parent_checkpoint'])
                        parent_options=dataclasses.replace(page_options,branch='par-'+digest(canonical(str(attempt.absolute())))[:32])
                        owner=CheckpointOwner(default_owner_directory(parent_options,envelope['config_sha256']),source_sha256=source_hash(),config_sha256=envelope['config_sha256'])
                        try:parent_floor=owner.for_checkpoint(branch_origin['parent_checkpoint'],minimum_floor=known_latest_floor)['floor']
                        finally:owner.close()
                    parent=Model.restore_checkpoint(branch_origin['parent_checkpoint'],storage_dir=working,
                        **({'page_options':parent_options,'known_latest_floor':parent_floor} if parent_options is not None else {}))
                    actual={'parent_case_id':case_key(parent.config),'parent_source_sha256':source_hash(),'parent_config_sha256':digest(canonical(parent.config.to_dict())),
                            'parent_day':parent.day,'parent_state_semantic_sha256':parent._long.semantic_digest()}
                    if any(identity[k]!=v for k,v in actual.items()):raise ValueError('branch origin full parent state differs')
                    model=parent.fork(config,storage_dir=attempt,**({'page_options':page_options} if page_options is not None else {}));model.branch_origin.update(identity);parent.ledger.close()
                    if page_options is not None:parent._long._native_owner.close()
            if any(model.branch_origin.get(k)!=v for k,v in identity.items() if k!='parent_case_id'):raise ValueError('restored child branch identity differs')
        run_world(config,attempt,restored=model,restart_origin=origin,
                  checkpoint_interval_days=limits.checkpoint_interval_days,
                  checkpoint_interval_seconds=limits.checkpoint_interval_seconds,
                  **({'page_options':page_options,'known_latest_floor':known_latest_floor} if page_options is not None else {}),
                  stop_requested=lambda: requested or time.time()>=limits.deadline or time.monotonic()-worker_started>=remaining_seconds,
                  metadata_extra={'pipeline_driver_sha256':driver_hash,'scientific_case_id':case_key(config),
                                  **({'branch_origin':identity} if identity is not None else {}),
                                  'execution_provenance':limits.provenance,'worker_pid':os.getpid()})
    except BaseException as error:
        if not (attempt/'manifest.json').exists():
            atomic_json(attempt/'manifest.json',{'status':'failed','source_sha256':source_hash(),
                'pipeline_driver_sha256':driver_hash,'config':config.to_dict(),
                'config_sha256':digest(canonical(config.to_dict())),
                'error_type':type(error).__name__,'error':str(error),'exit_code':1})
        raise


def disk_bytes(path):
    total=0
    for p in path.rglob('*'):
        try:
            if p.is_file():total+=p.stat().st_size
        except FileNotFoundError:
            # A concurrently atomically renamed temporary file is already
            # represented by the reservation; integrity readers never ignore it.
            continue
    return total


def output_files(path):
    """All regular scientific/evidence files, including hidden and temporary."""
    total=0
    for file in path.rglob('*'):
        if file.is_symlink():raise RuntimeError('symlink in bounded pipeline output')
        try:
            if file.is_file():total+=1
        except FileNotFoundError:
            # Atomic publication can remove a name; active reservations still
            # cover the complete owned attempt until its process is reaped.
            continue
    return total


class Scheduler:
    def __init__(self, root: Path, limits: RuntimeLimits, driver_hash: str,*,page_options=None,known_latest_floor=None):
        if limits.memory_budget_bytes>available_memory():
            raise MemoryError('declared batch memory exceeds available host/cgroup RAM')
        self.root, self.limits, self.driver_hash = root, limits, driver_hash
        self.worker_limit = min(limits.max_workers,limits.cpu_budget)
        self.stopped = False
        self.owned_workers_absent = True
        self.peak_active = 0
        self.ctx = multiprocessing.get_context('spawn')
        self.page_options=page_options;self.known_latest_floor=known_latest_floor
        if page_options is not None:
            page_options.validate()
            if self.worker_limit!=1:
                raise ValueError('native shared CAS requires explicit single-worker engineering admission; concurrent world scheduling is not admitted')
    def stop(self): self.stopped = True

    def _reap_cooperatively(self,processes):
        """SIGTERM requests a completed-day checkpoint, within one absolute cutoff."""
        processes=list(processes)
        for proc in processes:
            if proc.is_alive():proc.terminate()
        end=time.monotonic()+max(0.,min(self.limits.cooperative_stop_grace_seconds,
                                      self.limits.stop_cutoff-time.time()))
        for proc in processes:proc.join(max(0.,end-time.monotonic()))
        for proc in processes:
            if proc.is_alive():proc.kill()
        reap_end=time.monotonic()+max(0.,min(5.,self.limits.stop_cutoff-time.time()))
        for proc in processes:proc.join(max(0.,reap_end-time.monotonic()))
        if any(proc.is_alive() for proc in processes):
            raise RuntimeError('owned worker reaping is unverified at the absolute stop cutoff')

    def _inspect(self, config, branch_origin=None):
        case = self.root/'cases'/case_key(config)
        attempts = sorted(p for p in case.glob('attempt-*') if p.is_dir())
        for attempt in attempts:
            mpath = attempt/'manifest.json'
            if not mpath.exists(): continue
            m = json.loads(mpath.read_text())
            if m.get('branch_origin')!=branch_identity(branch_origin):raise ValueError('cached case shared-history branch identity differs')
            if m.get('source_sha256') != source_hash() or m.get('pipeline_driver_sha256') != self.driver_hash:
                raise ValueError('case source/driver differs; use a new output directory')
            if m.get('config') != json.loads(canonical(config.to_dict())):
                raise ValueError('case runtime/config differs from locked protocol')
            if m['status']=='complete':
                recorded=m.get('execution_provenance',{})
                expected=self.limits.provenance
                for field in ('project_hash','packaged_commit','environment','task_hash'):
                    if field in expected and recorded.get(field)!=expected[field]:
                        raise ValueError('cached execution provenance differs: '+field)
                if 'instance_id' in expected and any(not recorded.get(k) for k in ('instance_id','machine_type','zone')):
                    raise ValueError('local evidence cannot be reused as a new cloud execution')
                if m.get('config_sha256')!=digest(canonical(config.to_dict())) or m.get('scientific_case_id')!=case_key(config):
                    raise ValueError('complete case identity/config digest mismatch')
                verify_outputs(attempt,m,required=('summary.json','timeseries.csv','final_state.json','report.md','report.svg','checkpoint-index.json'))
                roster={p.name for p in attempt.iterdir() if p.is_file() and p.name!='manifest.json'}
                if roster!=set(m['output_sha256']):
                    raise ValueError('complete case output roster mismatch')
                summary = json.loads((attempt/'summary.json').read_text())
                if summary['days_completed']!=config.days or summary['n']!=config.n:
                    raise ValueError('complete case has wrong population/horizon')
                if self.page_options is not None:
                    from .native_checkpoint_owner import CheckpointOwner,default_owner_directory,handle_dict
                    from .longitudinal_model import verify_snapshot
                    import tempfile
                    owner=CheckpointOwner(default_owner_directory(self.page_options,m['config_sha256']),source_sha256=source_hash(),config_sha256=m['config_sha256'])
                    try:
                        latest=owner.for_checkpoint(attempt/'final_state.json')
                        floor=latest['floor']
                        if self.known_latest_floor is not None and handle_dict(self.known_latest_floor)['closure_count']>floor['closure_count']:
                            floor=self.known_latest_floor
                        def retain_floor(observed):
                            owner.publish(attempt/'final_state.json',latest['descriptor'],observed)
                            self.known_latest_floor=observed
                        with tempfile.TemporaryDirectory(prefix='native-scheduler-verify-',dir=Path(self.page_options.store_root).parent) as exported:
                            verify_snapshot(attempt/'final_state.json',expected_config=config,page_options=self.page_options,
                                known_latest_floor=floor,export_dir=exported,native_floor_observer=retain_floor)
                    finally:owner.close()
                return {'summary':summary,'attempt':str(attempt.relative_to(self.root)),
                        'manifest_sha256':file_digest(mpath)}, attempts
        return None, attempts

    def _record_exit(self, proc, config, attempt, start):
        marker=attempt/'manifest.json'
        if marker.exists():
            m=json.loads(marker.read_text())
            if m['status']=='running':
                m.update(status='failed',exit_code=proc.exitcode,error_type='WorkerExit',
                         error='worker ended without completing output',
                         days_completed=json.loads((attempt/'checkpoint-index.json').read_text())['snapshots'][0]['day'] if (attempt/'checkpoint-index.json').exists() else 0,
                         total_wall_seconds=time.monotonic()-start)
                atomic_json(marker,m)
        exit_path=attempt.parent/(attempt.name+'-exit.json')
        if not exit_path.exists():
            atomic_json(exit_path,{'process_exit_code':proc.exitcode,'attempt_wall_seconds':time.monotonic()-start})

    def _elapsed(self, attempts):
        total=0.
        for attempt in attempts:
            sidecar=attempt.parent/(attempt.name+'-exit.json')
            if sidecar.exists(): value=json.loads(sidecar.read_text())['attempt_wall_seconds']
            elif (attempt/'checkpoint-index.json').exists():
                value=json.loads((attempt/'checkpoint-index.json').read_text()).get('attempt_elapsed_wall_seconds',0.)
            elif (attempt/'manifest.json').exists(): value=json.loads((attempt/'manifest.json').read_text()).get('total_wall_seconds',0.)
            else: value=0.
            if not isinstance(value,(int,float)) or value<0 or not __import__('math').isfinite(value):
                raise ValueError('attempt elapsed record is invalid')
            total+=value
        return total

    def _write_status(self):
        rows=[]
        for case in sorted((self.root/'cases').glob('*')):
            for attempt in sorted(p for p in case.glob('attempt-*') if p.is_dir()):
                marker=attempt/'manifest.json'
                if marker.exists():
                    m=json.loads(marker.read_text())
                    rows.append({'case_id':case.name,'attempt':attempt.name,'status':m['status'],
                                 'error_type':m.get('error_type'),'days_completed':m.get('days_completed')})
                else:
                    rows.append({'case_id':case.name,'attempt':attempt.name,'status':'interrupted-before-manifest'})
        atomic_json(self.root/'case_statuses.json',rows)

    def run(self, configs, branch_origins=None):
        """Results use input order; completed-order cannot change samples/reduction."""
        self.owned_workers_absent = False
        unique={case_key(c):c for c in configs}
        branch_origins={} if branch_origins is None else branch_origins
        if not set(branch_origins).issubset(unique):raise ValueError('branch descriptor has an unplanned case')
        if not self.limits.workers_auto and unique and self.worker_limit*max(c.max_rss_mb*1024*1024 for c in unique.values())>self.limits.memory_budget_bytes:
            raise MemoryError('explicit worker count exceeds aggregate memory budget')
        queue=sorted(unique)
        results={};active={};elapsed={};reserved=0
        l=self.limits
        from .phase_storage import from_environment
        phase=from_environment(self.root) if l.phase_max_output_bytes else None
        if l.phase_max_output_bytes and phase is None:
            raise ValueError('bounded phase requires its real mounted filesystem binding')
        if phase is not None and (phase.value['raw_bytes']!=l.phase_max_output_bytes
                or phase.value['checkpoint_headroom_bytes']!=l.phase_checkpoint_headroom_bytes):
            raise ValueError('bounded phase runtime/filesystem binding differs')
        import shutil
        old_handlers={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
        def stop(sig,frame): self.stop()
        for sig in old_handlers: signal.signal(sig,stop)
        try:
            for ident in list(queue):
                c=unique[ident];plan=workload(c)
                if plan['work_events']>l.max_events: raise ValueError('work-event count exceeds runtime max_events')
                rss=int(c.max_rss_mb*1024*1024)
                if rss>l.memory_budget_bytes: raise MemoryError('one world exceeds batch RAM budget')
                if c.max_output_mb*1_000_000>l.batch_max_output_bytes:
                    raise RuntimeError('one world output bound exceeds batch disk budget')
                cached,_=self._inspect(c,branch_origins.get(ident))
                if cached is not None: results[ident]=cached;queue.remove(ident)
            while queue or active:
                expired=time.time()>=l.deadline
                if self.stopped or expired:
                    self._reap_cooperatively(proc for proc,*_ in active.values())
                    self._write_status()
                    raise InterruptedError('batch interrupted or absolute deadline reached; complete/checkpoint evidence retained')
                used=disk_bytes(self.root)
                if phase is not None:phase.before_step(active=bool(active))
                if used>l.batch_max_output_bytes: raise RuntimeError('batch output budget exceeded')
                files=output_files(self.root) if l.batch_max_output_files else 0
                if l.batch_max_output_files:
                    for proc,c,attempt,start in active.values():
                        actual_files=output_files(attempt)
                        if actual_files+1>l.per_world_output_files:
                            raise InterruptedError('owned attempt exceeds its prospective file reservation')
                        files+=max(0,l.per_world_output_files-actual_files)
                    if files>l.batch_max_output_files:
                        raise InterruptedError('whole pipeline file reservation exhausted; assigned cases retained')
                free=shutil.disk_usage(self.root).free
                outstanding=sum(max(0,(l.phase_checkpoint_headroom_bytes if phase is not None else int(c.max_output_mb*1_000_000))-disk_bytes(attempt)) for proc,c,attempt,start in active.values())
                free-=outstanding;used+=outstanding
                if free<l.min_free_disk_bytes: raise RuntimeError('minimum free disk guard failed')
                launched=False;finished=False
                for ident in list(queue):
                    if len(active)>=self.worker_limit: break
                    c=unique[ident];rss=int(c.max_rss_mb*1024*1024)
                    if reserved+rss>l.memory_budget_bytes: continue
                    if l.batch_max_output_files and files+l.per_world_output_files>l.batch_max_output_files:
                        if active:break
                        raise InterruptedError('insufficient whole pipeline file slots for a complete owned attempt')
                    # Reserve latest+previous checkpoint and final serialization
                    # before starting; no silent suppression of complete state.
                    required=l.phase_checkpoint_headroom_bytes if phase is not None else int(c.max_output_mb*1_000_000)
                    if free<l.min_free_disk_bytes+required or used+required>l.batch_max_output_bytes:
                        if active:
                            # Current complete-output reservations can be released
                            # after owned workers finish. Wait; never overbook or
                            # abort those workers merely to fill CPU slots.
                            break
                        raise RuntimeError('insufficient disk for the declared complete world outputs')
                    _,attempts=self._inspect(c,branch_origins.get(ident))
                    if len(attempts)>l.max_retries:
                        raise RuntimeError('case exhausted finite attempt budget: '+ident)
                    elapsed[ident]=self._elapsed(attempts)
                    remaining=l.world_timeout_seconds-elapsed[ident]
                    if remaining<=0:raise TimeoutError('cumulative world-attempt time budget exhausted: '+ident)
                    case=self.root/'cases'/ident;case.mkdir(parents=True,exist_ok=True)
                    attempt=case/f'attempt-{len(attempts):03d}';attempt.mkdir()
                    previous=next((a for a in reversed(attempts) if (a/'checkpoint-index.json').exists()),None)
                    args=(c.to_dict(),str(attempt),str(previous) if previous else None,l.to_dict(),self.driver_hash,remaining,branch_origins.get(ident))
                    if self.page_options is not None:args+=self.page_options,self.known_latest_floor
                    proc=self.ctx.Process(target=_worker,args=args)
                    proc.start();active[ident]=(proc,c,attempt,time.monotonic());reserved+=rss
                    queue.remove(ident);launched=True
                    self.peak_active=max(self.peak_active,len(active));free-=required;used+=required
                    if l.batch_max_output_files:files+=l.per_world_output_files
                for ident,(proc,c,attempt,start) in list(active.items()):
                    if proc.is_alive() and time.monotonic()-start>l.world_timeout_seconds-elapsed[ident]:
                        self._reap_cooperatively([proc])
                    if proc.is_alive():continue
                    finished=True
                    proc.join();reserved-=int(c.max_rss_mb*1024*1024);del active[ident]
                    self._record_exit(proc,c,attempt,start)
                    cached,attempts=self._inspect(c,branch_origins.get(ident))
                    if cached is not None: results[ident]=cached
                    elif len(attempts)<=l.max_retries: queue.append(ident);queue.sort()
                    else: raise RuntimeError('case failed after finite retries: '+ident)
                    self._write_status()
                if queue and not active and not launched and not finished:
                    raise RuntimeError('no case fits the declared CPU/RAM/disk envelope')
                if active: time.sleep(.05)
            return [results[case_key(c)] for c in configs]
        finally:
            self._reap_cooperatively(proc for proc,*_ in active.values())
            # Completed workers were already joined before removal from active.
            # If cooperative reaping raises, this remains false and no stopped
            # raw census may read a still-running worker's output.
            self.owned_workers_absent = True
            for proc,c,attempt,start in active.values():self._record_exit(proc,c,attempt,start)
            self._write_status()
            for sig,handler in old_handlers.items():signal.signal(sig,handler)
