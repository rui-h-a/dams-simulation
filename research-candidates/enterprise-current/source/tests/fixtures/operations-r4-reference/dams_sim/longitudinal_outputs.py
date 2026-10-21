"""Pure-I/O window integrals and paired-world tables for schema 3.

Daily trajectories remain in each complete world's original timeseries.csv.
Window tables use explicit half-open calendar intervals and recorded cumulative
exposure. Undefined rates remain rows; they never become zero or disappear.
"""
from __future__ import annotations
import csv
from datetime import date,timedelta
import json
import math
from pathlib import Path

from .config import Config
from .longitudinal import GregorianClock,anniversary
from .longitudinal_design import PRIMARY_ENDPOINTS,declared_contrasts
from .longitudinal_statistics import paired_interval

INTEGRALS=('active_member_calendar_days','active_member_workdays','present_member_workdays',
           'present_member_work_hours','available_member_work_hours','produced_work_units',
           'decision_regret_units','decisions_completed','review_hours','appeal_hours','training_hours',
           'migration_resource_units','dual_run_resource_units','maintenance_resource_units',
           'payroll_resource_units','recruitment_resource_units','operating_resource_units',
           'operating_unfunded_resource_units','revenue_resource_units')
INTEGER_COLUMNS={'day','fiscal_year','active_members','present_members','ever_people','adopted_guilds',
                 'review_backlog_records','appeal_backlog_records'}
BOOLEAN_COLUMNS={'is_workday','closed','suspended','work_creation_enabled'}
TEXT_COLUMNS={'calendar_date','adoption_days'}


def iter_timeseries(path):
    with Path(path).open(newline='') as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames is None or len(set(reader.fieldnames))!=len(reader.fieldnames):raise ValueError('invalid/duplicate trajectory header')
        for raw in reader:
            if None in raw or any(v is None for v in raw.values()):raise ValueError('trajectory row has missing/extra columns')
            row={}
            for key,value in raw.items():
                if key in BOOLEAN_COLUMNS:
                    if value not in ('True','False'):raise ValueError('invalid trajectory boolean')
                    row[key]=value=='True'
                elif key in INTEGER_COLUMNS:row[key]=int(value)
                elif key in TEXT_COLUMNS:row[key]=value
                elif value=='':row[key]=None
                else:
                    row[key]=float(value)
                    if not math.isfinite(row[key]):raise ValueError('nonfinite trajectory observation')
            yield row


def snapshot_rows(path,config,requested):
    """Validate the complete daily/calendar roster and retain only needed rows."""
    clock=GregorianClock(config.longitudinal);selected={};count=0;last=None
    for count,row in enumerate(iter_timeseries(path),1):
        if row['day']!=count-1 or row['calendar_date']!=clock.at(count-1).isoformat():raise ValueError('trajectory calendar/day roster differs')
        if row['is_workday']!=clock.is_workday(count-1):raise ValueError('trajectory work calendar differs')
        if row['day'] in requested:selected[row['day']]=row
        last=row
    if count!=config.days:raise ValueError('trajectory incomplete or oversized horizon')
    if not set(requested).issubset(selected):raise ValueError('window boundary snapshot missing')
    return selected,last


def window_observation(spec,case,rows,start,end,window_id,status='complete'):
    config=case['config'];tags=case['tags'];summary=case['summary']
    if config.enterprise_growth is not None:
        raise ValueError('endogenous growth requires a separately frozen dynamic-domain observation-window protocol')
    lc=config.longitudinal;clock=GregorianClock(lc)
    if status!='complete':
        begin=finish=None;values={k:None for k in INTEGRALS};gain=None
    else:
        if not 0<=start<end<=spec.days:raise ValueError('window outside complete horizon')
        begin=rows.get(start-1) if start else None;finish=rows[end-1]
        values={}
        for key in INTEGRALS:
            column=key+'_cumulative'
            if column not in finish:raise ValueError('required cumulative trajectory metric missing: '+column)
            values[key]=finish[column]-(begin[column] if begin else 0.)
            if values[key]<-1e-9:raise ValueError('cumulative exposure/cost decreased: '+key)
        gain=finish['cash_resource_units']-(begin['cash_resource_units'] if begin else config.n*lc.initial_cash_per_member)
    adoption={int(k):v for k,v in summary['adoption_days'].items()}
    observed=[v for v in adoption.values() if v is not None]
    actual_last=max(observed) if len(observed)==config.guilds else None
    age_end=None
    if end is not None:
        current=clock.at(end);origin=clock.at(0);years=current.year-origin.year
        if anniversary(origin,years)>current:years-=1
        boundary=anniversary(origin,years);following=anniversary(origin,years+1)
        age_end=lc.organization_initial_age_years+years+(current-boundary).days/(following-boundary).days
    return {'case_id':case['case_id'],'arm_id':tags.get('arm_id'),'context':tags['context'],'world':config.world,
            'window_id':window_id,'window_start_day':start,'window_end_day_exclusive':end,
            'observed_end_day_exclusive':(end if status=='complete' else min(end,spec.common_end_day)) if end is not None else None,
            'calendar_start':clock.at(start).isoformat() if start is not None else None,
            'calendar_end_exclusive':clock.at(end).isoformat() if end is not None else None,
            'organization_initial_age_years':lc.organization_initial_age_years,
            'organization_age_end_years':age_end,
            'actual_first_adoption_day':min(observed) if observed else None,'actual_last_guild_adoption_day':actual_last,
            'window_status':status,'closed':finish['closed'] if finish else summary['closure_day'] is not None,
            'closure_day':summary['closure_day'],'suspended':finish['suspended'] if finish else summary['suspension_day'] is not None,
            'unfinished_records':finish['review_backlog_records'] if finish else None,
            'unfinished_appeals':finish['appeal_backlog_records'] if finish else None,
            **values,'net_resource_gain_units':gain}


def endpoint_value(observation,name):
    if observation['window_status']!='complete':return None,None
    if name=='closed_by_common_end':return int(observation['closed']),1
    denominator=observation['decisions_completed'] if name=='decision_regret_per_decision' else observation['present_member_workdays']
    numerator={'work_per_present_member_workday':'produced_work_units',
               'decision_regret_per_decision':'decision_regret_units',
               'net_resource_gain_per_present_member_workday':'net_resource_gain_units'}[name]
    return (observation[numerator]/denominator if denominator>0 else None),denominator


def build_tables(spec,cases):
    """Return deterministic observation/effect rows from original raw snapshots.

    Cases are {case_id, config:Config, tags, summary, attempt:Path}. The caller
    verifies each manifest and snapshot group before passing them here.
    """
    if any(c['config'].enterprise_growth is not None for c in cases):
        raise ValueError('endogenous growth candidate has no admitted paired study protocol; fixed-domain tables are unavailable')
    lookup={(c['tags']['arm_id'],c['config'].world):c for c in cases if c['tags']['role']=='strategy'}
    worlds=sorted({c['config'].world for c in cases});plans=[];needed={c['case_id']:set() for c in cases}
    for contrast in declared_contrasts(spec):
        for world in worlds:
            treatment=lookup[(contrast['treatment_arm'],world)];reference=lookup[(contrast['reference_arm'],world)]
            windows=[('common-end',0,spec.common_end_day,'complete'),('tail',spec.common_end_day,spec.days,'complete')]
            adopted=[v for v in treatment['summary']['adoption_days'].values() if v is not None]
            last=max(adopted) if len(adopted)==treatment['config'].guilds else None
            for years in spec.observation_years:
                end=(anniversary(date.fromisoformat(spec.calendar_start)+timedelta(days=last),years)-date.fromisoformat(spec.calendar_start)).days if last is not None else None
                status='not-fully-adopted' if last is None else ('complete' if end<=spec.common_end_day else 'right-censored-window')
                windows.append((f'post-{years}y',last,end,status))
            for window,start,end,status in windows:
                plans.append((contrast,world,treatment,reference,window,start,end,status))
                if status=='complete':
                    for case in (treatment,reference):
                        needed[case['case_id']].add(end-1)
                        if start:needed[case['case_id']].add(start-1)
    snapshots={}
    for case in cases:
        snapshots[case['case_id']],_=snapshot_rows(case['attempt']/'timeseries.csv',case['config'],needed[case['case_id']])
    observations=[];effects=[]
    for contrast,world,treatment,reference,window,start,end,status in plans:
        pair=[]
        for role,case in (('treatment',treatment),('reference',reference)):
            obs=window_observation(spec,case,snapshots[case['case_id']],start,end,window,status)
            obs.update(contrast_id=contrast['contrast_id'],paired_role=role)
            observations.append(obs);pair.append(obs)
        for endpoint,epsilon,unit in PRIMARY_ENDPOINTS:
            if endpoint=='closed_by_common_end' and window!='common-end':continue
            a,ad=endpoint_value(pair[0],endpoint);b,bd=endpoint_value(pair[1],endpoint)
            effects.append({'contrast_id':contrast['contrast_id'],'world':world,'window_id':window,
                'endpoint':endpoint,'unit':unit,'window_start_day':start,'window_end_day_exclusive':end,
                'treatment_arm':contrast['treatment_arm'],'reference_arm':contrast['reference_arm'],
                'treatment_case_id':treatment['case_id'],'reference_case_id':reference['case_id'],
                'treatment_value':a,'reference_value':b,'effect':a-b if a is not None and b is not None else None,
                'treatment_denominator':ad,'reference_denominator':bd,'status':status if status!='complete' else ('defined' if a is not None and b is not None else 'undefined-zero-exposure'),
                'treatment_closed':pair[0]['closed'],'reference_closed':pair[1]['closed'],
                'actual_last_guild_adoption_day':pair[0]['actual_last_guild_adoption_day'],
                'primary':contrast['primary'] and window=='common-end'})
    return observations,effects


def interval_table(spec,effects):
    groups={}
    for row in effects:groups.setdefault((row['contrast_id'],row['window_id'],row['endpoint']),[]).append(row)
    tests=sum(c['primary'] for c in declared_contrasts(spec))*len(PRIMARY_ENDPOINTS)
    targets={name:eps for name,eps,_ in PRIMARY_ENDPOINTS};result=[]
    for (contrast,window,endpoint),rows in sorted(groups.items()):
        rows=sorted(rows,key=lambda x:x['world']);primary=rows[0]['primary']
        value=paired_interval([r['effect'] for r in rows],endpoint,
                              family_alpha=spec.family_alpha,primary_tests=tests if primary else 1,
                              target_halfwidth=targets[endpoint] if primary else None)
        result.append({'contrast_id':contrast,'window_id':window,'endpoint':endpoint,'unit':rows[0]['unit'],
                       'primary':primary,'independent_unit':'paired world','world_ids':json.dumps([r['world'] for r in rows]),**value})
    return result
