"""Unified spec-v2 CPU research pipeline; never starts paid resources."""
from __future__ import annotations
import dataclasses
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import signal
import subprocess
import sys
import time

from .cli import doctor
from .design import POLICIES, FACTORS, CONTEXTS, stage_tasks
from .runtime import RuntimeLimits
from .scheduler import Scheduler
from .spec import resolve_spec, scientific_config, case_key, workload
from .storage import atomic_json, atomic_csv, canonical, digest, file_digest, provenance, source_hash, verify_outputs

ROOT=Path(__file__).resolve().parents[1]


def driver_hash():
    paths=[Path(__file__),Path(__file__).with_name('spec.py'),Path(__file__).with_name('design.py'),
           Path(__file__).with_name('scheduler.py'),Path(__file__).with_name('runtime.py'),
           ROOT/'research_tools/study.py',ROOT/'research_tools/recovery.py',ROOT/'research_tools/pipeline.py']
    return digest(b''.join(p.name.encode()+b'\0'+p.read_bytes() for p in paths))


def make_base(spec, limits):
    provisional=spec.base(max_events=limits.max_events)
    plan=workload(provisional)
    rss=limits.per_world_rss_bytes or plan['estimated_peak_rss_bytes']
    if rss<plan['estimated_peak_rss_bytes']:
        raise MemoryError('declared world RAM below conservative preflight bound; measure/revise bounds explicitly')
    copies=1 if getattr(spec,'schema_version',2)==3 else 4
    if plan['estimated_output_bytes']*copies>limits.max_output_bytes:
        raise RuntimeError('declared world output below checkpoint/final-state preflight bound')
    return spec.base(max_wall_seconds=limits.world_timeout_seconds,max_output_mb=limits.max_output_bytes/1_000_000,
                     max_rss_mb=rss/(1024*1024),max_events=limits.max_events)


def _stage(out, stage, tasks, scheduler, metadata):
    path=out/stage;path.mkdir(exist_ok=True)
    inventory=[{'case_id':case_key(c),'config':c.to_dict(),'tags':tags} for c,tags in tasks]
    inv_sha=digest(canonical(inventory))
    existing=path/'case_inventory.json'
    if existing.exists() and canonical(json.loads(existing.read_text()))!=canonical(inventory):
        raise ValueError('stage inventory changed: '+stage)
    atomic_json(existing,inventory)
    atomic_json(path/'manifest.json',{**metadata,'status':'running','inventory_sha256':inv_sha,'expected_rows':len(tasks)})
    try:
        results=scheduler.run([c for c,_ in tasks])
        rows=[];references=[]
        for (c,tags),result in zip(tasks,results):
            row={**result['summary'],**tags,'case_id':case_key(c),'config_sha256':digest(canonical(c.to_dict()))}
            row={k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()}
            rows.append(row)
            references.append({'case_id':case_key(c),**{k:v for k,v in result.items() if k!='summary'}})
        atomic_csv(path/'world_summary.csv',rows)
        atomic_json(path/'case_references.json',references)
        atomic_json(path/'manifest.json',{**metadata,'status':'complete','exit_code':0,'inventory_sha256':inv_sha,
            'expected_rows':len(tasks),'completed_rows':len(rows),'unique_cases':len({case_key(c) for c,_ in tasks}),
            'output_sha256':{p.name:file_digest(p) for p in path.iterdir() if p.is_file() and p.name!='manifest.json'}})
        return rows,results
    except BaseException as e:
        atomic_json(path/'manifest.json',{**metadata,'status':'censored' if isinstance(e,InterruptedError) else 'failed',
            'inventory_sha256':inv_sha,'expected_rows':len(tasks),'exit_code':1,'error_type':type(e).__name__,'error':str(e)})
        raise


def _recovery(out,spec,base,scheduler,metadata):
    # Four-pattern fitting retains disjoint train/prediction/holdout world keys.
    # All population/horizon settings come from the actual spec, never n=24.
    from research_tools.recovery import PATTERNS,FLOORS,moments,distance
    rules=('linear_response','satisficing','reinforcement')
    grid=[dict(behavior_rule=r,autonomy_response=a,review_capacity_per_member_day=c)
          for r in rules for a in (0.,.25,.5) for c in (.6,.9,1.2)]
    tasks=[]
    for idx,c in enumerate(grid):
        for w in range(7100,7100+spec.recovery_train_worlds):
            tasks.append((dataclasses.replace(base,world=w,**c),dict(kind='candidate',candidate=idx,**c)))
    conditions=[dict(behavior_rule=r,autonomy_response=a,review_capacity_per_member_day=.9) for r in rules for a in (0.,.25)]
    for idx,c in enumerate(conditions):
        for w in [*range(7000,7000+spec.recovery_train_worlds),*range(7008,7008+spec.recovery_holdout_worlds)]:
            tasks.append((dataclasses.replace(base,world=w,**c),dict(kind='observation',case=idx,**c)))
    rows,results=_stage(out,'recovery-training',tasks,scheduler,metadata)
    def patterns(task,result):
        import csv
        c,tags=task;s=result['summary']
        with (out/result['attempt']/'timeseries.csv').open(newline='') as stream:
            last=next(reversed(list(csv.DictReader(stream))))
        return {**tags,'world':c.world,'work_per_member_day':s['produced_work_units']/(c.n*c.days),
                'regret_per_guild_day':s['decision_regret_units']/(c.guilds*c.days),
                'unfinished_per_member':s['unfinished_records']/c.n,'mean_trust':float(last['mean_trust'])}
    raw=[patterns(t,r) for t,r in zip(tasks,results)]
    selected=[];distances=[];holdout_tasks=[]
    for idx,true in enumerate(conditions):
        train=[r for r in raw if r['kind']=='observation' and r['case']==idx and r['world']<7008]
        test=[r for r in raw if r['kind']=='observation' and r['case']==idx and r['world']>=7008]
        scales={k:max(FLOORS[i],statistics.stdev(r[k] for r in train)) for i,k in enumerate(PATTERNS)}
        scores=[{'case':idx,'candidate':j,'distance':distance(train,[r for r in raw if r['kind']=='candidate' and r['candidate']==j],scales)} for j in range(len(grid))]
        scores.sort(key=lambda r:(r['distance'],r['candidate']));best=scores[0];distances.extend(scores)
        chosen=grid[best['candidate']]
        selected.append(dict(case=idx,true=true,chosen=chosen,training_distance=best['distance'],scales=scales,
                             feasible_candidates=[r['candidate'] for r in scores if r['distance']<=best['distance']+1],
                             observed_train=moments(train),observed_holdout=moments(test)))
        for w in range(7108,7108+spec.recovery_holdout_worlds):
            holdout_tasks.append((dataclasses.replace(base,world=w,**chosen),dict(kind='holdout',case=idx,**chosen)))
    for w in range(7200,7200+spec.recovery_train_worlds):
        for regime in ('linear','sublinear'):
            holdout_tasks.append((dataclasses.replace(base,world=w,regime=regime,autonomy_response=0),dict(kind='null',regime=regime)))
    _,held_results=_stage(out,'recovery-holdout',holdout_tasks,scheduler,metadata)
    held=[patterns(t,r) for t,r in zip(holdout_tasks,held_results)]
    for row in selected:
        pred=[r for r in held if r['kind']=='holdout' and r['case']==row['case']]
        test=[r for r in raw if r['kind']=='observation' and r['case']==row['case'] and r['world']>=7008]
        row.update(predicted_holdout=moments(pred),heldout_distance=distance(test,pred,row['scales']),
                   exact_grid_parameter_recovery=row['true']==row['chosen'],behavior_rule_recovered=row['true']['behavior_rule']==row['chosen']['behavior_rule'])
    path=out/'recovery';path.mkdir(exist_ok=True)
    atomic_csv(path/'training_patterns.csv',raw);atomic_csv(path/'heldout_patterns.csv',held)
    atomic_csv(path/'training_distances.csv',distances);atomic_json(path/'recovery_results.json',selected)
    atomic_json(path/'manifest.json',{**metadata,'status':'complete','case_count':len(selected),
        'training_stage_manifest_sha256':file_digest(out/'recovery-training/manifest.json'),
        'holdout_stage_manifest_sha256':file_digest(out/'recovery-holdout/manifest.json'),
        'source_configuration':base.to_dict(),'output_sha256':{p.name:file_digest(p) for p in path.iterdir() if p.name!='manifest.json'}})


def _analysis(out,spec,protocol,rows,metadata):
    from research_tools.study import interval
    primary=[r for r in rows if r['backend']=='central' and r['update_interval_days']==1]
    by={(r['world'],r['regime']):r for r in primary}
    differences=[]
    for w in protocol['confirm_worlds']:
        differences.append((by[w,'sublinear']['produced_work_units']-by[w,'linear']['produced_work_units'])/(spec.n*spec.days))
    claims={**metadata,'spec':spec.to_dict(),'primary':interval(differences),
            'primary_units':'generated work units per member-day','threshold':spec.substantive_threshold,'inferential_scope':spec.inferential_scope,
            'scope':'synthetic model conditional Monte Carlo uncertainty; no empirical calibration',
            'analysis_inputs':{'protocol':file_digest(out/'protocol.json'),
                               'confirmation_manifest':file_digest(out/'confirmation/manifest.json')}}
    atomic_json(out/'analysis/claims.json',claims)
    for k in POLICIES:
        values=[(by[w,k]['produced_work_units']-by[w,'linear']['produced_work_units'])/(spec.n*spec.days) for w in protocol['confirm_worlds']]
        claims.setdefault('policy_contrasts',{})[k]=interval(values)
    atomic_json(out/'analysis/claims.json',claims)
    # A scientific data report remains usable without Matplotlib; publication
    # figures must use the same checked stage references, not legacy outputs.
    c=claims['primary']
    text=f"# DAMS {spec.name}\n\nSpec {spec.sha256}; {spec.n} synthetic people, {spec.days} days.\n\nPaired DAMS minus linear: {c['mean']:.8g} generated work units/member-day; {c['n']} independent worlds; conditional normal MC interval [{c['low']:.8g}, {c['high']:.8g}].\n\nPlanned stages: {', '.join(spec.stages)}. All five authority rules are retained. {spec.inferential_scope}. Low concentration does not establish fairness; throughput does not measure organizational efficiency. Calibration, consensus-client behavior and field effectiveness are not established.\n"
    atomic_json(out/'analysis/manifest.json',{**metadata,'status':'complete','input_sha256':claims['analysis_inputs'],
        'output_sha256':{'claims.json':file_digest(out/'analysis/claims.json')}})
    from .storage import atomic_bytes
    atomic_bytes(out/'report.md',text.encode())


def publication_analysis(out,spec,limits,scheduler,metadata):
    """Publication failure remains a failed pipeline, with raw cases retained."""
    analyzer=ROOT/'research_tools/analyze.py'
    initial_sha=file_digest(analyzer)
    destination=out/'publication'
    log=out/'publication-analysis.log'
    command=[sys.executable,str(analyzer),'--runs',str(out),'--thesis-dir',str(destination),'--pipeline-in-progress']
    with log.open('a') as stream:
        proc=subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        try:
            while proc.poll() is None:
                if scheduler.stopped or time.time()>=limits.deadline:
                    raise InterruptedError('publication analysis interrupted or absolute batch deadline reached')
                if sys.platform.startswith('linux'):
                    status=Path(f'/proc/{proc.pid}/status')
                    if status.exists():
                        for line in status.read_text().splitlines():
                            if line.startswith('VmRSS:') and int(line.split()[1])*1024>limits.memory_budget_bytes:
                                raise MemoryError('publication analysis exceeds aggregate RSS budget')
                time.sleep(.1)
            if proc.returncode:raise RuntimeError('publication analysis failed; see '+str(log))
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
    if file_digest(analyzer)!=initial_sha:raise ValueError('publication analysis source changed during execution')
    manifest=destination/'generated/analysis/generation_manifest.json'
    if not manifest.is_file():raise ValueError('publication analysis did not create its generation manifest')
    metadata['publication_analysis_driver_sha256']=initial_sha
    metadata['publication_generation_manifest_sha256']=file_digest(manifest)
    metadata['publication_log_sha256']=file_digest(log)


def run_pipeline(name,scale,output,runtime_path=None):
    if name in ('longitudinal-adoption-5y','longitudinal-adoption-10y'):
        from .longitudinal_pipeline import run_longitudinal_pipeline
        return run_longitudinal_pipeline(name,scale,output,runtime_path)
    if name=='historical-full-study':
        if scale not in (None,120):raise ValueError('historical design requires scale 120')
        # The published historical release is immutable. This entry retains the
        # original fixed inventory; exact old source must use that release.
        subprocess.run([sys.executable,str(ROOT/'research_tools/reproduce_thesis.py'),'--output',str(output)],check=True)
        return {'status':'complete','run_path':str(output),'spec':name}
    out=Path(output).resolve();out.mkdir(parents=True,exist_ok=True)
    import fcntl
    lock=(out/'.pipeline.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:lock.close();raise RuntimeError('another process owns this pipeline output')
    started=time.monotonic();marker=out/'pipeline_manifest.json'
    metadata={**provenance(),'schema_version':2,'status':'preflight','exit_code':None,'stages':[]}
    scheduler=None
    prior=None
    frozen_valid=False
    signal_handlers={}
    try:
        spec=resolve_spec(name,scale)
        prior=json.loads(marker.read_text()) if marker.exists() else None
        supplied=json.loads(Path(runtime_path).read_text()) if runtime_path else {}
        limits=RuntimeLimits.from_dict(supplied,deadline=prior.get('deadline_utc') if prior else None)
        if prior and limits.deadline>RuntimeLimits.from_dict(prior['runtime_limits']).deadline:
            raise ValueError('resume cannot extend the original absolute batch deadline')
        base=make_base(spec,limits);dh=driver_hash()
        if 'packaged_commit' in limits.provenance and limits.provenance['packaged_commit']!=metadata['git_commit']:
            raise ValueError('runtime packaged commit differs from verified actual source')
        if 'instance_id' in limits.provenance and metadata['git_dirty']:
            raise ValueError('cloud scientific execution requires a clean verified fixed source package')
        metadata.update(status='running',spec=spec.to_dict(),spec_sha256=spec.sha256,pipeline_driver_sha256=dh,
                        runtime_limits=limits.to_dict(),deadline_utc=limits.deadline_utc,base=base.to_dict(),
                        doctor=doctor(),workload=workload(base),stages=[])
        if prior:
            for field in ('source_sha256','spec_sha256','pipeline_driver_sha256','base'):
                if canonical(prior.get(field))!=canonical(metadata.get(field)):
                    raise ValueError('resume changes frozen '+field+'; use a new output directory')
        frozen_valid=True
        if time.time()>=limits.deadline:raise InterruptedError('absolute batch deadline has passed')
        atomic_json(marker,metadata)
        atomic_json(out/'spec_manifest.json',{k:metadata[k] for k in ('schema_version','spec','spec_sha256','source_sha256','pipeline_driver_sha256','base','workload')})
        scheduler=Scheduler(out,limits,dh)
        signal_handlers={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
        for sig in signal_handlers:signal.signal(sig,lambda sig,frame:scheduler.stop())
        common={k:metadata[k] for k in ('schema_version','spec_sha256','source_sha256','pipeline_driver_sha256')}
        if 'pilot' in spec.stages:
            pilot,_=_stage(out,'pilot',stage_tasks('pilot',spec,base),scheduler,common)
            lookup={(r['world'],r['regime']):r for r in pilot}
            diffs=[(lookup[w,'sublinear']['produced_work_units']-lookup[w,'linear']['produced_work_units'])/(spec.n*spec.days) for w in range(spec.pilot_worlds)]
            sd=statistics.stdev(diffs);worlds,requested=spec.confirmation_count(sd)
            pilot_manifest_sha=file_digest(out/'pilot/manifest.json')
        else:
            sd=None;worlds=spec.confirmation_min;requested=worlds;pilot_manifest_sha=None
        protocol={**common,'status':'preregistered-after-pilot-before-confirmation' if pilot_manifest_sha else 'preregistered-fixed-world-count','base':base.to_dict(),
                  'spec':spec.to_dict(),'pilot_sd':sd,'requested_worlds':requested,'worlds':worlds,
                  'pilot_worlds':list(range(spec.pilot_worlds)),'confirm_worlds':list(range(1000,1000+worlds)),
                  'policies':list(POLICIES),'backends':list(spec.backends),'cadences_days':list(spec.cadences),
                  'mc_target_halfwidth':spec.mc_target_halfwidth,'substantive_effect_threshold_work_per_member_day':spec.substantive_threshold,
                  'pilot_manifest_sha256':pilot_manifest_sha,
                  'world_count_rule':'bounded pilot-SD rule before confirmation' if pilot_manifest_sha else 'fixed 8 worlds at n<=1m; 10m explicitly exploratory 2 worlds; no post-outcome sample increase',
                  'inferential_scope':spec.inferential_scope,
                  'attack_cost_unit':'hour-equivalents/day; 2/120 and 8/120 per person, fixed 0.5 actor cohort'}
        if (out/'protocol.json').exists() and canonical(json.loads((out/'protocol.json').read_text()))!=canonical(protocol):
            raise ValueError('preregistered protocol differs from recovered pilot')
        atomic_json(out/'protocol.json',protocol)
        if pilot_manifest_sha:
            metadata['stages'].append({'stage':'pilot','status':'complete','manifest_sha256':pilot_manifest_sha})
        confirm=[]
        for stage in (stage for stage in spec.stages if stage!='pilot'):
            if stage=='mechanisms':
                from research_tools.study import mechanisms
                # Fixed-stream diagnostics expose requested scale rather than
                # silently recycling the historical 120..10000 inventory.
                from .authority import authority,gini,total_variation
                from .randomness import WorldRandom
                rng=WorldRandom(spec.seed,3000);credit=[math.exp(.4*rng.normal('fixed-credit',i)) for i in range(spec.n)]
                ref=authority(credit,1,zero_policy='equal');rows=[]
                for a in (0.,.2,.5,.8,.95,1.):
                    shares=authority(credit,a,zero_policy='equal')
                    rows.append(dict(n=spec.n,alpha=a,tv=total_variation(shares,ref),gini=gini(shares),signed_gini_gap=gini(shares)-gini(credit)))
                path=out/stage;path.mkdir(exist_ok=True);atomic_csv(path/'fixed_stream.csv',rows)
                atomic_json(path/'manifest.json',{**common,'status':'complete','not_dynamic_experiment':True,
                                                  'output_sha256':{'fixed_stream.csv':file_digest(path/'fixed_stream.csv')}})
            elif stage=='recovery':_recovery(out,spec,base,scheduler,common)
            else:
                rows,_=_stage(out,stage,stage_tasks(stage,spec,base,worlds),scheduler,common)
                if stage=='confirmation':confirm=rows
            metadata['stages'].append({'stage':stage,'status':'complete','manifest_sha256':file_digest(out/stage/'manifest.json')})
            atomic_json(marker,metadata);print(stage+' complete',flush=True)
        _analysis(out,spec,protocol,confirm,common)
        metadata['stages'].append({'stage':'analysis','status':'complete','manifest_sha256':file_digest(out/'analysis/manifest.json')})
        atomic_json(marker,metadata)
        publication_analysis(out,spec,limits,scheduler,metadata)
        if source_hash()!=metadata['source_sha256'] or driver_hash()!=dh:
            raise ValueError('source changed while executing the scientific pipeline')
        for stage in metadata['stages']:
            path=out/stage['stage'];m=json.loads((path/'manifest.json').read_text())
            if file_digest(path/'manifest.json')!=stage['manifest_sha256']:
                raise ValueError('stage manifest changed before completion')
            verify_outputs(path,m)
        metadata.update(status='complete',exit_code=0,wall_seconds=time.monotonic()-started,
                        peak_active_workers=scheduler.peak_active,
                        unique_complete_cases=len({r['case_id'] for ref in out.glob('*/case_references.json') for r in json.loads(ref.read_text())}),
                        logical_case_rows=sum(len(json.loads(ref.read_text())) for ref in out.glob('*/case_references.json')),
                        report_sha256=file_digest(out/'report.md'),protocol_sha256=file_digest(out/'protocol.json'))
        atomic_json(marker,metadata)
        return {'status':'complete','run_path':str(out),'spec':spec.name,'n':spec.n,'days':spec.days,'worlds':worlds}
    except BaseException as e:
        # Preserve the prior frozen identity if a new invocation is incompatible.
        failure={**metadata,'status':'censored' if isinstance(e,InterruptedError) else 'failed','exit_code':1,
                 'error_type':type(e).__name__,'error':str(e),'wall_seconds':time.monotonic()-started}
        if marker.exists() and prior and not frozen_valid:
            atomic_json(out/'rejected-invocation.json',failure)
        else:atomic_json(marker,failure)
        raise
    finally:
        for sig,handler in signal_handlers.items():signal.signal(sig,handler)
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
