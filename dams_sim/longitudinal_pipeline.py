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
import re
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


def stopped_case_census(out, inventory, identity, *, expected_provenance=None):
    """Rebuild partial completion from actual raw after scheduler reaping.

    No Model is constructed and no older checkpoint substitutes for an invalid
    latest generation. A case count is not a complete-stage or study gate.
    """
    from .config import Config
    from .longitudinal_model import verify_snapshot
    from research_tools.validate_longitudinal import CheckedLongitudinalCases
    out = Path(out).absolute()
    if identity.get('schema_version') != 3:
        raise ValueError('partial census requires schema three identity')
    reader = CheckedLongitudinalCases(out, {k: identity[k] for k in
        ('source_sha256', 'pipeline_driver_sha256', 'spec_sha256')})
    by_id = {r['case_id']: r for r in inventory}
    if len(by_id) != len(inventory):
        raise ValueError('partial census inventory duplicates')
    configs = {key: Config.from_dict(row['config']) for key, row in by_id.items()}
    if any(case_key(configs[key]) != key for key in by_id):
        raise ValueError('partial census case identity differs')
    def history(row):
        values = []; seen = {row['case_id']}; parent = row['tags']['parent_case_id']
        while parent is not None:
            if parent in seen or parent not in by_id:
                raise ValueError('partial census parent graph is cyclic/incomplete')
            seen.add(parent); config = configs[parent]
            values.append((config.days, config)); parent = by_id[parent]['tags']['parent_case_id']
        return tuple(sorted(values, key=lambda v: v[0]))
    histories = {key: history(row) for key, row in by_id.items()}
    complete = {}; records = {}; checkpoints = {}
    import hashlib
    def trajectory_hash(rows):
        value = hashlib.sha256()
        for row in rows:
            value.update(canonical(row)); value.update(b'\n')
        return value.hexdigest()
    for key, row in by_id.items():
        record = {'case_id': key, 'classification': 'unstarted'}
        case = out/'cases'/key
        try:
            if case.is_symlink():
                raise ValueError('case directory is a symlink')
            if not case.exists():
                records[key] = record; continue
            if not case.is_dir() or (out/'cases').is_symlink():
                raise ValueError('case directory is unsafe')
            attempts = sorted(p for p in case.iterdir() if p.name.startswith('attempt-')
                              and not p.name.endswith('-exit.json'))
            if any(p.is_symlink() or not p.is_dir() or re.fullmatch(r'attempt-[0-9]{3,}', p.name) is None
                   for p in attempts):
                raise ValueError('case attempt namespace is unsafe')
            if not attempts:
                records[key] = record; continue
            attempt = attempts[-1]
            record['attempt'] = str(attempt.relative_to(out))
            marker = attempt/'manifest.json'
            if marker.is_symlink():
                raise ValueError('attempt manifest is a symlink')
            manifest = json.loads(marker.read_text()) if marker.exists() else None
            if manifest is None:
                raise ValueError('started attempt has no source/driver manifest')
            if manifest is not None:
                if (manifest.get('source_sha256') != identity['source_sha256']
                        or manifest.get('pipeline_driver_sha256') != identity['pipeline_driver_sha256']
                        or canonical(manifest.get('config')) != canonical(configs[key].to_dict())
                        or manifest.get('config_sha256') != digest(canonical(configs[key].to_dict()))):
                    raise ValueError('attempt source/driver/config differs')
                record['manifest_sha256'] = file_digest(marker)
                for field, value in (expected_provenance or {}).items():
                    if manifest.get('execution_provenance', {}).get(field) != value:
                        raise ValueError('attempt execution provenance differs: '+field)
                if manifest.get('status') == 'complete':
                    complete[key] = reader.validate_case(row, attempt,
                        expected_provenance=expected_provenance, config_history=histories[key])
                    record['classification'] = 'complete'
                    records[key] = record; continue
            index_path = attempt/'checkpoint-index.json'
            if not index_path.exists():
                record['classification'] = 'failed'
                record['reason'] = 'started attempt has no valid latest checkpoint'
                records[key] = record; continue
            if index_path.is_symlink():
                raise ValueError('checkpoint index is a symlink')
            index = json.loads(index_path.read_text())
            snapshots = index.get('snapshots')
            if (index.get('source_sha256') != identity['source_sha256']
                    or index.get('config_sha256') != digest(canonical(configs[key].to_dict()))
                    or not isinstance(snapshots, list) or not 1 <= len(snapshots) <= 2):
                raise ValueError('latest checkpoint index identity/retention differs')
            latest = snapshots[0]
            parts = latest.get('files', [])
            name = latest['file']
            if (Path(name).name != name or len(parts) != 2
                    or {v['file'] for v in parts} != {name, str(Path(name).with_suffix('.sqlite'))}):
                raise ValueError('latest checkpoint group roster differs')
            for part in parts:
                path = attempt/part['file']
                if (path.is_symlink() or not path.is_file() or path.stat().st_size != part['bytes']
                        or file_digest(path) != part['sha256']):
                    raise ValueError('latest checkpoint group bytes differ')
            if file_digest(attempt/name) != latest['sha256']:
                raise ValueError('latest checkpoint pointer differs')
            envelope, database = verify_snapshot(attempt/name, expected_config=configs[key])
            if (envelope['state']['day'] != latest['day']
                    or envelope['state_semantic_sha256'] != latest['state_semantic_sha256']):
                raise ValueError('latest checkpoint state/day differs')
            parent_id = row['tags']['parent_case_id']
            checkpoints[key] = {'state_origin': envelope['state']['branch_origin'],
                                'manifest_origin': manifest.get('branch_origin'),
                                'ledger_path': database,
                                'past_trajectory_sha256': trajectory_hash(
                                    envelope['state']['history'][:configs[parent_id].days])
                                    if parent_id is not None else None}
            record.update(classification='valid-latest-checkpoint', checkpoint_file=name,
                          checkpoint_index_sha256=file_digest(index_path),
                          checkpoint_sha256=latest['sha256'], checkpoint_day=latest['day'],
                          state_semantic_sha256=latest['state_semantic_sha256'])
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            record.update(classification='failed', reason=str(error), error_type=type(error).__name__)
        records[key] = record
    # A child's complete raw cannot certify its assigned parent or shared past.
    from itertools import islice, zip_longest
    from .longitudinal_outputs import iter_timeseries
    pending = set(complete)
    while pending:
        ready = [key for key in pending if by_id[key]['tags']['parent_case_id'] not in pending]
        if not ready:
            raise ValueError('partial census complete-case parent graph is cyclic')
        for key in ready:
            child = complete[key]; parent_id = by_id[key]['tags']['parent_case_id']
            try:
                if parent_id is None:
                    if child['manifest'].get('branch_origin') is not None:
                        raise ValueError('root case has an unexpected branch origin')
                else:
                    if parent_id not in complete or records[parent_id]['classification'] != 'complete':
                        raise ValueError('assigned complete parent is unavailable')
                    parent = complete[parent_id]
                    origin = {'parent_case_id': parent_id, 'parent_source_sha256': identity['source_sha256'],
                              'parent_config_sha256': parent['manifest']['config_sha256'],
                              'parent_day': parent['config'].days,
                              'parent_state_semantic_sha256': parent['state_semantic_sha256']}
                    if child['manifest'].get('branch_origin') != origin or child['branch_origin'] != origin:
                        raise ValueError('shared parent complete-state identity differs')
                    parent_series = iter_timeseries(parent['attempt']/'timeseries.csv')
                    child_series = islice(iter_timeseries(child['attempt']/'timeseries.csv'), parent['config'].days)
                    if any(a != b for a, b in zip_longest(parent_series, child_series)):
                        raise ValueError('child rewrites shared pre-adoption trajectory')
            except (OSError, ValueError, KeyError, TypeError) as error:
                records[key].update(classification='failed', reason=str(error), error_type=type(error).__name__)
            pending.remove(key)
    # A full-state checkpoint must also belong to its assigned shared parent.
    # Preserve only the small ancestry/prefix receipt, not every agent envelope.
    import sqlite3
    for key, checkpoint in checkpoints.items():
        if records[key]['classification'] != 'valid-latest-checkpoint':
            continue
        parent_id = by_id[key]['tags']['parent_case_id']
        try:
            if parent_id is None:
                if checkpoint['state_origin'] is not None or checkpoint['manifest_origin'] is not None:
                    raise ValueError('root checkpoint has an unexpected branch origin')
                continue
            if parent_id not in complete or records[parent_id]['classification'] != 'complete':
                raise ValueError('checkpoint assigned complete parent is unavailable')
            parent = complete[parent_id]
            origin = {'parent_case_id': parent_id, 'parent_source_sha256': identity['source_sha256'],
                      'parent_config_sha256': parent['manifest']['config_sha256'],
                      'parent_day': parent['config'].days,
                      'parent_state_semantic_sha256': parent['state_semantic_sha256']}
            if checkpoint['state_origin'] != origin or checkpoint['manifest_origin'] != origin:
                raise ValueError('checkpoint assigned parent complete-state identity differs')
            if records[key]['checkpoint_day'] < parent['config'].days:
                raise ValueError('checkpoint precedes its assigned parent boundary')
            if checkpoint['past_trajectory_sha256'] != trajectory_hash(
                    iter_timeseries(parent['attempt']/'timeseries.csv')):
                raise ValueError('checkpoint rewrites shared pre-adoption trajectory')
            left = sqlite3.connect(Path(parent['ledger_path']).as_uri()+'?mode=ro', uri=True)
            right = None
            try:
                right = sqlite3.connect(Path(checkpoint['ledger_path']).as_uri()+'?mode=ro', uri=True)
                query = 'SELECT seq,day,kind,event,payload FROM journal WHERE day < ? ORDER BY seq'
                parent_rows = left.execute(query, (parent['config'].days,))
                child_rows = right.execute(query, (parent['config'].days,))
                if any(a != b for a, b in zip_longest(parent_rows, child_rows)):
                    raise ValueError('checkpoint rewrites shared pre-adoption journal')
            finally:
                left.close()
                if right is not None:
                    right.close()
        except (OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError) as error:
            records[key].update(classification='failed', reason=str(error), error_type=type(error).__name__)
    groups = {name: sorted(key for key, r in records.items() if r['classification'] == name)
              for name in ('complete', 'valid-latest-checkpoint', 'failed', 'unstarted')}
    if sum(map(len, groups.values())) != len(inventory) or set().union(*map(set, groups.values())) != set(by_id):
        raise ValueError('partial census is not an exact disjoint inventory partition')
    unexpected = sorted(p.name for p in (out/'cases').iterdir() if p.name not in by_id) if (out/'cases').is_dir() else []
    return {'schema': 'DAMS-stopped-case-census-1', 'identity': identity,
            'inventory_sha256': digest(canonical(inventory)), 'expected_cases': len(inventory),
            'full_study_gate': False, 'groups': groups,
            'remaining_case_ids': sorted(set(by_id)-set(groups['complete'])),
            'unexpected_case_ids': unexpected, 'records': [records[key] for key in sorted(records)]}


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
        failure = meta|{'status':'censored' if isinstance(error,InterruptedError) else 'failed',
                       'exit_code':1,'error_type':type(error).__name__,'error':str(error)}
        try:
            if getattr(scheduler, 'owned_workers_absent', False) is not True:
                raise RuntimeError('owned workers are not verified absent; stopped census refused')
            census = stopped_case_census(out, inventory, identity,
                                        expected_provenance=scheduler.limits.provenance)
            atomic_json(stage/'case_census.json', census)
            failure.update(completed_rows=len(census['groups']['complete']),
                           remaining_case_ids=census['remaining_case_ids'],
                           case_census_sha256=file_digest(stage/'case_census.json'),
                           partial_census_exact=True)
        except Exception as census_error:
            # Preserve the primary failure; unresolved raw is never counted zero
            # or promoted from in-memory results to exact completion.
            failure.update(completed_rows=None, remaining_case_ids=sorted(by['case_id'] for by in inventory),
                           partial_census_exact=False, census_error_type=type(census_error).__name__,
                           census_error=str(census_error))
        atomic_json(stage/'manifest.json',failure)
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
