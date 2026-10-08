"""Schema-3 pipeline: precision pilot, frozen confirmation and publication.

No paid resources are started here. Resource limits stop or refuse execution;
they never alter a calendar, arm, world ID, population or endpoint inventory.
"""
from __future__ import annotations
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import shutil
import signal
import time

from .longitudinal_design import resolve_longitudinal_spec,world_plan,freeze_precision
from .longitudinal_outputs import build_tables,interval_table
from .runtime import RuntimeLimits
from .scheduler import Scheduler
from .spec import case_key,workload
from .storage import atomic_json,atomic_csv,canonical,digest,file_digest,provenance,source_hash,verify_outputs

ROOT=Path(__file__).resolve().parents[1]


def driver_hash():
    names=('longitudinal_pipeline.py','longitudinal_design.py','longitudinal_outputs.py',
           'longitudinal_statistics.py','spec.py','scheduler.py','runtime.py','cli.py','report.py')
    return digest(b''.join(n.encode()+b'\0'+Path(__file__).with_name(n).read_bytes() for n in names))


def stage_inventory(spec,base,world_ids):
    rows=[]
    for world in world_ids:
        parents,children=world_plan(spec,base,world)
        rows += [{'case_id':case_key(c),'config':c.to_dict(),'tags':tags} for c,tags in parents+children]
    if len({r['case_id'] for r in rows})!=len(rows):raise ValueError('longitudinal case inventory duplicates')
    return rows


def _origin(out,row,known):
    parent_id=row['tags']['parent_case_id']
    if parent_id is None:return None
    parent=known[parent_id];path=out/parent['attempt'];m=json.loads((path/'manifest.json').read_text())
    desc=m['final_state_descriptor']
    return {'parent_checkpoint':str(path/desc['file']),'parent_case_id':parent_id,
            'parent_source_sha256':m['source_sha256'],'parent_config_sha256':m['config_sha256'],
            'parent_day':desc['day'],'parent_state_semantic_sha256':desc['state_semantic_sha256']}


def execute_stage(out,name,spec,base,world_ids,scheduler,identity):
    from .config import Config
    stage=out/name;stage.mkdir(exist_ok=True)
    inventory=stage_inventory(spec,base,world_ids);inv=digest(canonical(inventory));ipath=stage/'case_inventory.json'
    if ipath.exists() and canonical(json.loads(ipath.read_text()))!=canonical(inventory):raise ValueError('locked longitudinal inventory changed')
    atomic_json(ipath,inventory)
    meta={**identity,'stage':name,'inventory_sha256':inv,'expected_rows':len(inventory),'independent_world_ids':list(world_ids)}
    atomic_json(stage/'manifest.json',meta|{'status':'running'})
    pending=list(inventory);known={};references=[]
    try:
        while pending:
            ready=[r for r in pending if r['tags']['parent_case_id'] is None or r['tags']['parent_case_id'] in known]
            if not ready:raise ValueError('longitudinal branch graph is cyclic or missing a parent')
            configs=[Config.from_dict(r['config']) for r in ready]
            origins={r['case_id']:v for r in ready if (v:=_origin(out,r,known)) is not None}
            results=scheduler.run(configs,branch_origins=origins)
            for row,result in zip(ready,results):
                known[row['case_id']]=result
                references.append({'case_id':row['case_id'],**{k:v for k,v in result.items() if k!='summary'}})
                pending.remove(row)
            atomic_json(stage/'case_references.json',sorted(references,key=lambda r:r['case_id']))
        cases=[];index=[]
        for row in inventory:
            result=known[row['case_id']];attempt=out/result['attempt'];m=json.loads((attempt/'manifest.json').read_text())
            cases.append({'case_id':row['case_id'],'config':Config.from_dict(row['config']),'tags':row['tags'],
                          'summary':result['summary'],'attempt':attempt})
            index.append({'stage':name,'case_id':row['case_id'],**row['tags'],'attempt':result['attempt'],
                          'manifest_sha256':file_digest(attempt/'manifest.json'),
                          'timeseries_sha256':file_digest(attempt/'timeseries.csv'),
                          'final_state_descriptor_sha256':file_digest(attempt/'final_state.json'),
                          'state_semantic_sha256':m['final_state_descriptor']['state_semantic_sha256']})
        observations,effects=build_tables(spec,[c for c in cases if c['tags']['role']=='strategy'])
        for rows in (observations,effects):
            for row in rows:row['stage']=name
        atomic_csv(stage/'case_index.csv',index)
        atomic_csv(stage/'observations.csv',observations)
        atomic_csv(stage/'longitudinal_endpoints.csv',effects)
        atomic_json(stage/'tables.json',{'observations':observations,'effects':effects})
        meta.update(status='complete',exit_code=0,completed_rows=len(cases),
                    strategy_trajectories=sum(c['tags']['role']=='strategy' for c in cases),
                    prefix_states=sum(c['tags']['role']=='prehistory-prefix' for c in cases),
                    independent_worlds=len(world_ids),
                    output_sha256={p.name:file_digest(p) for p in stage.iterdir() if p.is_file() and p.name!='manifest.json'})
        atomic_json(stage/'manifest.json',meta)
        return effects,meta
    except BaseException as error:
        atomic_json(stage/'manifest.json',meta|{'status':'censored' if isinstance(error,InterruptedError) else 'failed',
                    'exit_code':1,'error_type':type(error).__name__,'error':str(error),'completed_rows':len(known),
                    'remaining_case_ids':[r['case_id'] for r in pending]})
        raise


def _limits(spec,out,runtime_path,prior):
    if runtime_path is not None:value=json.loads(Path(runtime_path).read_text())
    elif prior is not None:value=prior['runtime_limits']
    else:
        # Automatic machine controls are bounded by current memory/disk. These
        # estimates are not certification; disk refusal may require a measured
        # resource revision before the full scientific version is frozen.
        plan=workload(spec.base(max_events=10**12))
        free=max(100_000,shutil.disk_usage(out).free-100_000_000)
        per_world=math.ceil(plan['estimated_output_bytes'])
        value={'max_events':plan['work_events'],'max_output_bytes':per_world,
               'batch_max_output_bytes':free,'world_timeout_seconds':3600.,
               'checkpoint_interval_days':90,'max_retries':1}
    limits=RuntimeLimits.from_dict(value)
    if prior is not None and canonical(prior['runtime_limits'])!=canonical(limits.to_dict()):
        raise ValueError('runtime controls or absolute deadline changed; cannot silently extend a locked task')
    return limits


def run_longitudinal_pipeline(name,scale,output,runtime_path=None):
    from .pipeline import make_base
    spec=resolve_longitudinal_spec(name,scale)
    out=Path(output).resolve();out.mkdir(parents=True,exist_ok=True)
    import fcntl
    lock=(out/'.pipeline.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:lock.close();raise RuntimeError('another process owns this pipeline output')
    start=time.monotonic();marker=out/'pipeline_manifest.json';prior=json.loads(marker.read_text()) if marker.exists() else None
    meta={**provenance(),'schema_version':3,'spec':spec.to_dict(),'spec_sha256':spec.sha256,
          'pipeline_driver_sha256':driver_hash(),'status':'preflight','exit_code':None,'stages':[]}
    old_handlers={};accepted=False
    try:
        if prior is not None and any(prior.get(k)!=meta[k] for k in ('schema_version','spec_sha256','source_sha256','pipeline_driver_sha256')):
            raise ValueError('longitudinal source/spec/driver changed; preserve this output and use a new task directory')
        limits=_limits(spec,out,runtime_path,prior);meta['runtime_limits']=limits.to_dict()
        accepted=True;atomic_json(marker,meta)
        if time.time()>=limits.deadline:raise InterruptedError('absolute task deadline reached; no new worlds')
        base=make_base(spec,limits)
        meta.update(base_config=base.to_dict(),status='running');atomic_json(marker,meta)
        atomic_json(out/'spec_manifest.json',spec.to_dict())
        scheduler=Scheduler(out,limits,meta['pipeline_driver_sha256'])
        for sig in (signal.SIGTERM,signal.SIGINT):
            old_handlers[sig]=signal.getsignal(sig);signal.signal(sig,lambda signum,frame:scheduler.stop())
        identity={k:meta[k] for k in ('schema_version','spec_sha256','source_sha256','pipeline_driver_sha256')}
        pilot,pilot_meta=execute_stage(out,'precision-pilot',spec,base,spec.pilot_ids(),scheduler,identity)
        meta['stages'].append({'name':'precision-pilot','manifest_sha256':file_digest(out/'precision-pilot/manifest.json')})
        primary=[{'contrast_id':r['contrast_id'],'endpoint':r['endpoint'],'world':r['world'],'effect':r['effect']} for r in pilot if r['primary']]
        protocol=freeze_precision(spec,primary,pilot_inventory_sha256=pilot_meta['inventory_sha256'],source_sha256=meta['source_sha256'])
        protocol['pipeline_driver_sha256']=meta['pipeline_driver_sha256']
        pp=out/'protocol.json'
        if pp.exists() and canonical(json.loads(pp.read_text()))!=canonical(protocol):raise ValueError('frozen confirmation protocol differs')
        atomic_json(pp,protocol);meta['protocol_sha256']=file_digest(pp);atomic_json(marker,meta)
        if protocol['status']!='frozen':raise RuntimeError('primary precision refused; retained complete pilot and explicit reasons, confirmation not started')
        effects,confirm_meta=execute_stage(out,'confirmation',spec,base,protocol['confirmation_world_ids'],scheduler,identity)
        meta['stages'].append({'name':'confirmation','manifest_sha256':file_digest(out/'confirmation/manifest.json')})
        intervals=interval_table(spec,effects)
        atomic_csv(out/'paired_intervals.csv',intervals);atomic_json(out/'paired_intervals.json',intervals)
        analysis=out/'analysis';analysis.mkdir(exist_ok=True)
        atomic_json(analysis/'manifest.json',identity|{'status':'complete','exit_code':0,
                    'input_sha256':{'protocol.json':file_digest(out/'protocol.json'),
                                    'confirmation/tables.json':file_digest(out/'confirmation/tables.json')},
                    'output_sha256':{'../paired_intervals.csv':file_digest(out/'paired_intervals.csv'),
                                     '../paired_intervals.json':file_digest(out/'paired_intervals.json')}})
        meta['stages'].append({'name':'longitudinal-analysis','manifest_sha256':file_digest(analysis/'manifest.json')})
        meta['mc_precision_met']=all(r['precision_met'] for r in intervals if r['primary'])
        meta['scientific_status']='complete';meta['status']='running';atomic_json(marker,meta)
        from research_tools.validate_longitudinal import validate_longitudinal_output
        checked=validate_longitudinal_output(out,name,spec.n,publication_required=False,producer_in_progress=True)
        meta['raw_validation']=checked;atomic_json(marker,meta)
        # Root-owned publication dispatch never instantiates a Model. Complete
        # raw stages remain resumable if the publication dependency fails.
        from .pipeline import publication_analysis
        publication_analysis(out,spec,limits,scheduler,meta)
        gp=out/'publication/generated/analysis/generation_manifest.json'
        if not gp.is_file():raise ValueError('longitudinal publication did not write its generation manifest')
        meta['publication_manifest_sha256']=file_digest(gp)
        meta['stages'].append({'name':'publication','manifest_sha256':file_digest(gp)})
        if source_hash()!=meta['source_sha256'] or driver_hash()!=meta['pipeline_driver_sha256']:
            raise ValueError('source changed during longitudinal execution')
        meta.update(status='complete',exit_code=0,unique_complete_cases=checked['unique_complete_cases'],
                    logical_case_rows=checked['logical_case_rows'],independent_primary_worlds=protocol['confirmation_worlds'],
                    peak_workers=scheduler.peak_active,total_wall_seconds=time.monotonic()-start,
                    completed_utc=datetime.now(timezone.utc).isoformat())
        atomic_json(marker,meta)
        validate_longitudinal_output(out,name,spec.n)
        return {'status':'complete','run_path':str(out),'spec':name,'confirmation_worlds':protocol['confirmation_worlds'],
                'mc_precision_met':meta['mc_precision_met'],'unique_complete_cases':meta['unique_complete_cases']}
    except BaseException as error:
        if accepted:
            meta.update(status='censored' if isinstance(error,InterruptedError) else 'failed',exit_code=1,
                        error_type=type(error).__name__,error=str(error),total_wall_seconds=time.monotonic()-start)
            atomic_json(marker,meta)
        else:
            atomic_json(out/'invocation-refusal.json',{'status':'refused','error_type':type(error).__name__,'error':str(error),
                        'existing_pipeline_manifest_preserved':prior is not None})
        raise
    finally:
        for sig,handler in old_handlers.items():signal.signal(sig,handler)
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
