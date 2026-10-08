"""Scientific inventory/statistical boundary tests; no pretend cloud execution."""
import csv
import dataclasses
from contextlib import closing
from datetime import date,timedelta
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dams_sim.config import Config
from dams_sim.longitudinal import LongitudinalConfig,GregorianClock
from dams_sim.longitudinal_design import (LONGITUDINAL_NAMES,PRIMARY_ENDPOINTS,declared_contrasts,
    resolve_longitudinal_spec,freeze_precision,world_plan)
from dams_sim.longitudinal_outputs import endpoint_value,snapshot_rows,iter_timeseries
from dams_sim.longitudinal_storage import journal_json
from dams_sim.longitudinal_statistics import student_quantile,exact_binomial_interval,paired_interval
from dams_sim.spec import resolve_spec,case_key
from dams_sim.storage import atomic_json,atomic_csv,canonical,file_digest,source_hash


def zero_pilot(spec):
    return [{'contrast_id':c['contrast_id'],'endpoint':name,'world':world,'effect':0.}
            for c in declared_contrasts(spec) if c['primary']
            for name,_,_ in PRIMARY_ENDPOINTS for world in spec.pilot_ids()]


def raw_accounting_fixture(directory,*,cash_per_member=100.,stop=None,effort=.25):
    """Small accounting fixture, not a Model run or a complete study/snapshot.

    Fixed zero care/leave, one guild, unit work and known wrong votes make the
    expected totals explicit. Zero cash closes on the second observed workday;
    the exposure of that day is retained even though its end-present count is 0.
    """
    from dams_sim.longitudinal_outputs import INTEGRALS
    config=Config(n=12,guilds=1,days=3,longitudinal=LongitudinalConfig(adoption_mode='never',
        initial_cash_per_member=cash_per_member,closure_after_unfunded_workdays=2,work_creation_stop_day=stop)).validate()
    path=Path(directory)/'trace.csv';database=Path(directory)/'fixture.sqlite';clock=GregorianClock(config.longitudinal)
    people=[{'id':i,'slot':i,'token':f'initial:{i}','entered_day':0,'exited_day':None,
        'active_calendar_days':2 if cash_per_member==0 else 3,'active_workdays':2 if cash_per_member==0 else 3,
        'present_workdays':2 if cash_per_member==0 else 3,'available_work_hours':2. if cash_per_member==0 else 3.,
        'post_adoption_available_work_hours':0.} for i in range(12)]
    agents=[{'id':i,'care_hours':0.,'reciprocity':0.} for i in range(12)]
    metrics={};rows=[];cash=12*cash_per_member;closed=None
    def add(key,amount):metrics[key]=metrics.get(key,0.)+amount
    with closing(sqlite3.connect(database)) as connection,connection:
        connection.execute('CREATE TABLE journal(seq INTEGER PRIMARY KEY,day INTEGER,kind TEXT,event TEXT,payload TEXT)')
        def emit(day,kind,event,value):
            connection.execute('INSERT INTO journal(day,kind,event,payload) VALUES(?,?,?,?)',(day,kind,event,canonical(value).decode()))
        for i in range(12):emit(0,'entry',f'initial:{i}',{'person':i,'slot':i,'guild':0,'initial':True})
        for day in range(3):
            before=dict(metrics);active=12 if closed is None else 0;present=active
            creating=stop is None or day<stop;due=present*.51;funding=min(1.,cash/due) if due else 1.
            paid=due*funding;cash-=paid
            add('calendar_days_observed',1);add('calendar_workdays_observed',1)
            for key in ('active_member_calendar_days','active_member_workdays','present_member_workdays'):
                add(key,active)
            for _ in range(active):
                for key in ('available_member_work_hours','present_member_work_hours',
                    'observation_available_member_work_hours' if creating else 'tail_available_member_work_hours'):add(key,1.)
            for key,value in (('operating_resource_units',paid),('operating_unfunded_resource_units',due-paid),
                ('payroll_resource_units',present*.5*funding),('maintenance_resource_units',present*.01*funding),('dual_run_resource_units',0.)):
                add(key,value)
            if cash_per_member==0 and day==1:
                closed=day;present=0;emit(day,'closure',f'closure:{day}',{'cause':'consecutive_unfunded_workdays','cash':cash})
            if creating and closed is None:
                for i in range(12):
                    flag=funding>0;share=1./12
                    emit(day,'vote',f'vote:{day}:initial:{i}:g:0',{'person':i,'guild':0,'observed_signal':1.,'participates':flag,
                        'formal_share':share,'effective_weight':share if flag else 0.,'vote':1.})
                emit(day,'decision',f'decision:{day}:0',{'guild':0,'proposal_truth_research_only':-1.,'loss':1.,
                    'participation':12 if funding else 0,'effective_mass':1. if funding else 0.})
                add('decisions_completed' if funding else 'decisions_unresolved',1.)
                if funding:add('decision_errors',1.)
                add('decision_regret_units',1.)
            else:add('decision_regret_units',0.)
            producing=creating and funding>0 and present>0;output=12. if producing else 0.
            for i in range(present):
                add('training_hours',0.);add('governance_hours',.05 if funding else 0.)
                if producing:
                    emit(day,'work',f'work:{day}:initial:{i}:g:0',{'person':i,'guild':0,'produced_research_only':1.,'effort':effort,
                        'cooperation':0.,'training':0.,'attack_hours':0.,'funding_fraction':funding,'fraudulent':False,'censored':False,
                        'morning_observed_state':{'share':1./12,'trust':.5,'fatigue':0.,'learning':0.}})
                    add('work_events',1.)
            add('produced_work_units',output);add('effort_hours',12*effort if producing else 0.);add('cooperation_units',0.)
            revenue=output;cash+=revenue;add('revenue_resource_units',revenue)
            if day==1 and present and funding:
                emit(day,'review','work:0:initial:0:g:0',{'backend':'central','accepted':True,'observed':1.,'audit_detected':False})
                add('review_hours',.1);add('verification_resource_units',0.)
            if day==2 and present and funding:
                emit(day,'commit','work:0:initial:0:g:0',{'person':0,'guild':0,'credit':1.,'fraudulent':False,'correction':False})
                add('confirmed_records',1.);add('fraudulent_records_accepted',0.)
            row={'day':day,'calendar_date':clock.at(day).isoformat(),'is_workday':True,'active_members':active,
                'present_members':present,'cash_resource_units':cash,'output_work_units':output,'review_backlog_records':0,
                'appeal_backlog_records':0,'closed':closed is not None,'suspended':False,'adoption_days':'{"0":null}',
                'work_creation_enabled':creating}
            for key in INTEGRALS:
                row[key+'_daily']=metrics.get(key,0.)-before.get(key,0.);row[key+'_cumulative']=metrics.get(key,0.)
            rows.append(row)
            emit(day,'day_end',f'day:{day}',{'date':row['calendar_date'],'active':active,'present':present,'cash':cash,'output':output,
                'review_backlog_records':0,'appeal_backlog_records':0,'closed_day':closed,'suspended_day':None,
                'adoption_days':{'0':None},'metrics_daily':{k:v-before.get(k,0.) for k,v in metrics.items()},'metrics_cumulative':dict(metrics)})
    atomic_csv(path,rows)
    return config,{'day':3,'history':rows,'people':people,'agents':agents,'slots':{str(i):i for i in range(12)}},path,database


def publication_fixture(directory):
    """Tests just the publication contract, without asserting a complete study."""
    from research_tools.validate_longitudinal import CheckedLongitudinalStudy
    from dams_sim.longitudinal_pipeline import driver_hash
    from dams_sim.longitudinal_design import declared_contrasts
    from research_tools import longitudinal_derived as derived
    from types import SimpleNamespace
    checked=CheckedLongitudinalStudy.__new__(CheckedLongitudinalStudy);checked.root=Path(directory).resolve()
    checked.spec=resolve_spec(LONGITUDINAL_NAMES[0],12)
    checked.identity={'source_sha256':source_hash(),'pipeline_driver_sha256':driver_hash(),'spec_sha256':checked.spec.sha256}
    checked.protocol={'confirmation_worlds':2,'confirmation_world_ids':[30000,30001]};checked.observations=[{'world':30000,'value':1.}]
    checked.endpoints=[{'stage':'confirmation','status':'defined'}]
    checked.paired_intervals=[{'primary':True,'precision_met':False,'mean_effect':1.}]
    for name in ('protocol.json','spec_manifest.json','retained-raw.bin','pipeline_manifest.json'):
        (checked.root/name).write_text(name)
    checked.inputs={name:file_digest(checked.root/name) for name in ('protocol.json','spec_manifest.json','retained-raw.bin','pipeline_manifest.json')}
    (checked.root/'publication-analysis.log').write_text('offline fixture; no Model executions')
    pub=checked.root/'publication';required={'generated/analysis/'+name for name in ('observations.csv','longitudinal_endpoints.csv',
        'paired_intervals.csv','cohort_and_memory_descriptives.csv','numerical_claims.json','derived_definitions.json',
        'paired_trajectory.csv','relative_adoption_world_coverage.csv','relative_adoption_trajectory.csv')}
    required.update('generated/'+name for name in ('longitudinal_effects.tex','longitudinal_trajectories.tex','longitudinal_results.tex'))
    for endpoint,_,_ in PRIMARY_ENDPOINTS:
        required.update('figures/results/longitudinal-'+endpoint.replace('_','-')+'.'+ext for ext in ('pdf','svg'))
    required.update('figures/results/'+name+'.'+ext for name in ('longitudinal-paired-trajectory','longitudinal-relative-adoption-trajectory') for ext in ('pdf','svg'))
    for name in required:
        (pub/name).parent.mkdir(parents=True,exist_ok=True);(pub/name).write_text('contract fixture '+name)
    for name,rows in (('observations',checked.observations),('longitudinal_endpoints',checked.endpoints),('paired_intervals',checked.paired_intervals)):
        atomic_csv(pub/('generated/analysis/'+name+'.csv'),rows)
    claims={'schema_version':3,'source_sha256':checked.identity['source_sha256'],'spec':checked.spec.to_dict(),
        'confirmation_worlds':2,'independent_unit':'paired world','mc_precision_met':False,'primary':checked.paired_intervals,
        'post_window_status_counts':{'defined':1},'limitations':derived.LIMITATIONS}
    atomic_json(pub/'generated/analysis/numerical_claims.json',claims)
    root=Path(__file__).resolve().parents[1]
    code=('research_tools/longitudinal_analysis.py','research_tools/analyze.py','research_tools/figure_style.py','research_tools/validate_longitudinal.py','research_tools/longitudinal_derived.py')
    # These handwritten inputs exercise only the publication recipe. The
    # complete raw-study loader is deliberately not bypassed in integration.
    contrast=next(c for c in declared_contrasts(checked.spec) if c['primary'])
    cases=[];state_by_case={};journal_by_case={};series_by_case={}
    for world in checked.protocol['confirmation_world_ids']:
        for arm in (contrast['treatment_arm'],contrast['reference_arm']):
            ident=f'{arm}:{world}';days=checked.spec.common_end_day
            config=SimpleNamespace(days=days,world=world,longitudinal=SimpleNamespace(organization_initial_age_years=0))
            cases.append({'case_id':ident,'config':config,'tags':{'context':'startup','arm_id':arm},
                          'summary':{'adoption_days':{'0':1 if world==30000 else None},'closure_day':None,'suspension_day':None}})
            state_by_case[ident]={'state':{'people':[{'id':0,'entered_day':0,'first_positive_authority_day':None,
                'exited_day':None,'exit_reason':None,'active_calendar_days':days,'active_workdays':days,
                'present_workdays':days,'available_work_hours':float(days)}],
                'agents':[{'id':0,'care_hours':0.}], 'closed_day':None,'suspended_day':None}}
            journal_by_case[ident]=[(0,'entry','initial:0',json.dumps({'initial':True,'person':0}))]+[
                (day,'day_end',f'day:{day}',json.dumps({'memory':{'0':.5},'routine':{'0':.4}})) for day in range(days)]
            series_by_case[ident]=[{'day':day,'active_members':1,'cash_resource_units':10.+day,'review_backlog_records':day} for day in range(days)]
    checked.select=lambda **kwargs:cases
    checked.read_snapshot=lambda ident:(state_by_case[ident],None)
    checked.iter_journal=lambda ident:iter(journal_by_case[ident])
    checked.iter_timeseries=lambda ident:iter(series_by_case[ident])
    cohort=[]
    for case in cases:cohort.extend(derived.derive_case(case,state_by_case[case['case_id']],journal_by_case[case['case_id']]))
    atomic_csv(pub/'generated/analysis/cohort_and_memory_descriptives.csv',cohort)
    atomic_json(pub/'generated/analysis/derived_definitions.json',derived.DERIVED_DEFINITIONS)
    case_map={(case['tags']['arm_id'],case['config'].world):case for case in cases}
    common=derived.common_trajectory_data(checked,contrast,case_map)
    relative=derived.relative_trajectory_data(checked,contrast,case_map,common['paired'])
    for filename,rows in (('paired_trajectory',common['rows']),('relative_adoption_world_coverage',relative['coverage']),('relative_adoption_trajectory',relative['rows'])):
        atomic_csv(pub/('generated/analysis/'+filename+'.csv'),rows)
    generation={'status':'complete','schema_version':3,'version':'DAMS-longitudinal-publication-2','model_executions':0,**checked.identity,
        'protocol_sha256':checked.inputs['protocol.json'],'analysis_sources_sha256':{p:file_digest(root/p) for p in code},
        'inputs':{k:v for k,v in checked.inputs.items() if k!='pipeline_manifest.json'},
        'outputs':{name:file_digest(pub/name) for name in required}}
    checked.pipeline={'publication_log_sha256':file_digest(checked.root/'publication-analysis.log')}
    def seal():
        atomic_json(pub/'generated/analysis/generation_manifest.json',generation)
        checked.pipeline['publication_generation_manifest_sha256']=file_digest(pub/'generated/analysis/generation_manifest.json')
    seal();return checked,generation,seal


def nonwork_guild_fixture(directory):
    """N16/guild12 key-order boundary, pure I/O without a Model."""
    from dams_sim.longitudinal_outputs import INTEGRALS
    config=Config(n=16,guilds=12,days=2,longitudinal=LongitudinalConfig(adoption_mode='never',
        holidays=('2020-01-01','2020-01-02'),initial_cash_per_member=6.25)).validate()
    path=Path(directory)/'trace.csv';database=Path(directory)/'fixture.sqlite';rows=[]
    clock=GregorianClock(config.longitudinal);adoption={g:None for g in range(12)}
    with closing(sqlite3.connect(database)) as connection,connection:
        connection.execute('CREATE TABLE journal(seq INTEGER PRIMARY KEY,day INTEGER,kind TEXT,event TEXT,payload TEXT)')
        for i in range(16):
            value={'person':i,'slot':i,'guild':i%12,'initial':True}
            connection.execute('INSERT INTO journal(day,kind,event,payload) VALUES(?,?,?,?)',
                (0,'entry',f'initial:{i}',journal_json(value,kind='entry')))
        for day in range(2):
            row={'day':day,'calendar_date':clock.at(day).isoformat(),'is_workday':False,
                'active_members':16,'present_members':0,'cash_resource_units':100.,'output_work_units':0.,
                'review_backlog_records':0,'appeal_backlog_records':0,'closed':False,'suspended':False,
                'adoption_days':canonical(adoption).decode(),'work_creation_enabled':True}
            for key in INTEGRALS:row[key+'_daily']=0.;row[key+'_cumulative']=0.
            row['active_member_calendar_days_daily']=16.;row['active_member_calendar_days_cumulative']=16.*(day+1)
            rows.append(row)
            value={'date':row['calendar_date'],'active':16,'present':0,'cash':100.,'output':0.,
                'review_backlog_records':0,'appeal_backlog_records':0,'closed_day':None,'suspended_day':None,
                'adoption_days':adoption,'metrics_daily':{'active_member_calendar_days':16.,'calendar_days_observed':1.},
                'metrics_cumulative':{'active_member_calendar_days':16.*(day+1),'calendar_days_observed':float(day+1)}}
            connection.execute('INSERT INTO journal(day,kind,event,payload) VALUES(?,?,?,?)',
                (day,'day_end',f'day:{day}',journal_json(value,kind='day_end')))
    state={'day':2,'history':json.loads(json.dumps(rows)),
        'people':[{'id':i,'slot':i,'token':f'initial:{i}','entered_day':0,'exited_day':None,
            'active_calendar_days':2,'active_workdays':0,'present_workdays':0,'available_work_hours':0.,
            'post_adoption_available_work_hours':0.} for i in range(16)],
        'slots':{str(i):i for i in range(16)},'agents':[{'id':i,'care_hours':0.,'reciprocity':0.} for i in range(16)]}
    atomic_csv(path,rows)
    return config,state,path,database


class LongitudinalDesignTests(unittest.TestCase):
    def test_true_calendar_horizons_and_tail(self):
        for name,years,horizon in ((LONGITUDINAL_NAMES[0],5,3743),(LONGITUDINAL_NAMES[1],10,5569)):
            spec=resolve_spec(name,12)
            self.assertEqual(spec.days,horizon)
            self.assertEqual(spec.days-spec.common_end_day,90)
            self.assertEqual(spec.post_adoption_years,years)
            self.assertGreater(spec.days,3650)
            self.assertEqual(spec.base(max_events=10**12).longitudinal.work_creation_stop_day,spec.common_end_day)

    def test_declared_incremental_roster_and_prefix_graph(self):
        for name,prefixes,arms in ((LONGITUDINAL_NAMES[0],9,27),(LONGITUDINAL_NAMES[1],4,10)):
            spec=resolve_spec(name,12);parent,child=world_plan(spec,spec.base(max_events=10**12),20000)
            self.assertEqual((len(parent),len(child)),(prefixes,arms))
            known={case_key(c) for c,_ in parent}
            self.assertTrue(all(t['parent_case_id'] is None or t['parent_case_id'] in known for _,t in child))
            by={(t['context'],t['branch_day']):case_key(c) for c,t in parent}
            central=next(t for _,t in child if t['arm_id']=='startup-dams')
            self.assertEqual(central['parent_case_id'],by['startup',366])
            if years:=spec.post_adoption_years==5:
                alternate=next(t for _,t in child if t['arm_id']=='startup-witness')
                self.assertEqual(central['parent_case_id'],alternate['parent_case_id'])
                century=next(c for c,t in child if t['arm_id']=='century-dams')
                self.assertEqual(century.longitudinal.organization_initial_age_years,100)
                self.assertLess(century.longitudinal.initial_age_max_years,100)

    def test_disjoint_protocol_and_zero_closure_not_wald(self):
        spec=resolve_spec(LONGITUDINAL_NAMES[0],12)
        protocol=freeze_precision(spec,zero_pilot(spec),pilot_inventory_sha256='0'*64,source_sha256='1'*64)
        self.assertEqual(protocol['confirmation_worlds'],77)
        self.assertFalse(set(protocol['pilot_world_ids'])&set(protocol['confirmation_world_ids']))
        small=paired_interval([0]*16,'closed_by_common_end',primary_tests=40,target_halfwidth=.1)
        self.assertLess(small['lower'],0);self.assertGreater(small['upper'],0);self.assertFalse(small['precision_met'])
        enough=paired_interval([0]*77,'closed_by_common_end',primary_tests=40,target_halfwidth=.1)
        self.assertTrue(enough['precision_met'])

    def test_precision_failure_does_not_clip_or_drop_worlds(self):
        spec=resolve_spec(LONGITUDINAL_NAMES[0],12);rows=zero_pilot(spec)
        rows[0]['effect']=None
        protocol=freeze_precision(spec,rows,pilot_inventory_sha256='0'*64,source_sha256='1'*64)
        self.assertEqual(protocol['status'],'precision-refused');self.assertEqual(protocol['confirmation_worlds'],0)
        rows=zero_pilot(spec)
        for row in rows:
            if row['endpoint']=='work_per_present_member_workday':row['effect']=row['world']%2*1000.
        protocol=freeze_precision(spec,rows,pilot_inventory_sha256='0'*64,source_sha256='1'*64)
        self.assertGreater(protocol['requested_confirmation_worlds'],128)
        self.assertEqual(protocol['confirmation_worlds'],0)
        with self.assertRaises(ValueError):freeze_precision(spec,zero_pilot(spec)[:-1],pilot_inventory_sha256='0'*64,source_sha256='1'*64)
        rows=zero_pilot(spec);rows.append(rows[0])
        with self.assertRaises(ValueError):freeze_precision(spec,rows,pilot_inventory_sha256='0'*64,source_sha256='1'*64)

    def test_small_n_student_and_exact_binomial_edges(self):
        self.assertAlmostEqual(student_quantile(.975,1),math.tan(math.pi*.475),places=9)
        self.assertAlmostEqual(student_quantile(.975,15),2.131449545559323,places=11)
        lower,upper=exact_binomial_interval(0,16,.05)
        self.assertEqual(lower,0.);self.assertAlmostEqual(upper,1-.025**(1/16),places=12)
        reverse=exact_binomial_interval(16,16,.05)
        self.assertAlmostEqual(reverse[0],1-upper,places=12);self.assertEqual(reverse[1],1.)
        missing=paired_interval([1.,None,2.],'work_per_present_member_workday',target_halfwidth=.01)
        self.assertEqual(missing['assigned_worlds'],3);self.assertEqual(missing['undefined_pairs'],1)
        self.assertIsNone(missing['mean_effect']);self.assertFalse(missing['precision_met'])

    def test_actual_exposure_and_zero_denominator(self):
        obs={'window_status':'complete','produced_work_units':12.,'present_member_workdays':3.,
             'decisions_completed':2.,'decision_regret_units':1.,'net_resource_gain_units':-3.,'closed':True}
        self.assertEqual(endpoint_value(obs,'work_per_present_member_workday'),(4.,3.))
        self.assertEqual(endpoint_value(obs,'net_resource_gain_per_present_member_workday'),(-1.,3.))
        self.assertEqual(endpoint_value(obs,'closed_by_common_end'),(1,1))
        obs['present_member_workdays']=0
        self.assertEqual(endpoint_value(obs,'work_per_present_member_workday'),(None,0))
        obs['window_status']='right-censored-window'
        self.assertEqual(endpoint_value(obs,'closed_by_common_end'),(None,None))

    def test_daily_clock_and_corrupt_csv_rejected(self):
        config=Config(n=12,days=10,longitudinal=LongitudinalConfig()).validate();clock=GregorianClock(config.longitudinal)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'trace.csv';fields=['day','calendar_date','is_workday','closed','suspended']
            rows=[dict(day=d,calendar_date=clock.at(d).isoformat(),is_workday=clock.is_workday(d),closed=False,suspended=False) for d in range(10)]
            def write(data):
                with path.open('w',newline='') as stream:
                    w=csv.DictWriter(stream,fieldnames=fields);w.writeheader();w.writerows(data)
            write(rows);selected,_=snapshot_rows(path,config,{0,9});self.assertEqual(set(selected),{0,9})
            rows[5]['day']=6;write(rows)
            with self.assertRaises(ValueError):snapshot_rows(path,config,{0,9})
            path.write_text('day,day\n1,2\n')
            with self.assertRaises(ValueError):list(iter_timeseries(path))
            path.write_text('day,calendar_date\n0,2020-01-01,extra\n')
            with self.assertRaises(ValueError):list(iter_timeseries(path))

    def test_partial_self_report_cannot_be_validated(self):
        from research_tools.validate_longitudinal import validate_longitudinal_output
        with tempfile.TemporaryDirectory() as directory:
            atomic_json(Path(directory)/'pipeline_manifest.json',{'schema_version':3,'status':'failed','unique_complete_cases':9999})
            with self.assertRaisesRegex(ValueError,'not complete'):validate_longitudinal_output(directory)

    def test_rehashed_intermediate_trace_still_requires_original_journal(self):
        from research_tools.validate_longitudinal import verify_daily_evidence
        from dams_sim.longitudinal_outputs import INTEGRALS
        config=Config(n=12,days=2,longitudinal=LongitudinalConfig(adoption_mode='never',
            holidays=('2020-01-01','2020-01-02'),initial_cash_per_member=100./12)).validate()
        clock=GregorianClock(config.longitudinal)
        # This is a consistency fixture, not a generated simulation or complete
        # snapshot. The helper is tested independently of the full raw gate.
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'trace.csv';database=Path(directory)/'fixture.sqlite'
            rows=[]
            with closing(sqlite3.connect(database)) as connection,connection:
                connection.execute('CREATE TABLE journal(seq INTEGER PRIMARY KEY,day INTEGER,kind TEXT,event TEXT,payload TEXT)')
                for i in range(12):
                    connection.execute('INSERT INTO journal(day,kind,event,payload) VALUES(?,?,?,?)',
                        (0,'entry',f'initial:{i}',canonical({'person':i,'slot':i,'guild':i%4,'initial':True}).decode()))
                for day in range(2):
                    row={'day':day,'calendar_date':clock.at(day).isoformat(),'is_workday':clock.is_workday(day),
                         'active_members':12,'present_members':0,'cash_resource_units':100.,'output_work_units':0.,
                         'review_backlog_records':0,'appeal_backlog_records':0,'closed':False,'suspended':False,
                         'adoption_days':'{"0":null,"1":null,"2":null,"3":null}','work_creation_enabled':True}
                    for key in INTEGRALS:row[key+'_daily']=0.;row[key+'_cumulative']=0.
                    row['active_member_calendar_days_daily']=12.;row['active_member_calendar_days_cumulative']=12.*(day+1)
                    rows.append(row)
                    value={'date':row['calendar_date'],'active':12,'present':0,'cash':100.,'output':0.,
                           'review_backlog_records':0,'appeal_backlog_records':0,'closed_day':None,'suspended_day':None,
                           'adoption_days':{'0':None,'1':None,'2':None,'3':None},
                           'metrics_daily':{'active_member_calendar_days':12.,'calendar_days_observed':1.},
                           'metrics_cumulative':{'active_member_calendar_days':12.*(day+1),'calendar_days_observed':float(day+1)}}
                    connection.execute('INSERT INTO journal(day,kind,event,payload) VALUES(?,?,?,?)',
                        (day,'day_end',f'day:{day}',canonical(value).decode()))
            state={'day':2,'history':json.loads(json.dumps(rows)),
                   'people':[{'id':i,'slot':i,'token':f'initial:{i}','entered_day':0,'exited_day':None,
                       'active_calendar_days':2,'active_workdays':0,'present_workdays':0,'available_work_hours':0.,
                       'post_adoption_available_work_hours':0.} for i in range(12)],'slots':{str(i):i for i in range(12)},
                   'agents':[{'id':i,'care_hours':0.,'reciprocity':0.} for i in range(12)]};atomic_csv(path,rows)
            self.assertEqual(verify_daily_evidence(path,config,state,database)['day'],1)
            rows[0]['cash_resource_units']=999.;state['history'][0]['cash_resource_units']=999.;atomic_csv(path,rows)
            with self.assertRaisesRegex(ValueError,'observed stock'):verify_daily_evidence(path,config,state,database)
            rows[0]['cash_resource_units']=100.;state['history'][0]['cash_resource_units']=100.
            rows[0]['present_member_workdays_cumulative']=7.;state['history'][0]['present_member_workdays_cumulative']=7.;atomic_csv(path,rows)
            with self.assertRaisesRegex(ValueError,'exposure/cost'):verify_daily_evidence(path,config,state,database)

    def test_summary_lifecycle_cannot_relabel_verified_state(self):
        from research_tools.validate_longitudinal import verify_summary_evidence
        config=Config(n=12,days=2,longitudinal=LongitudinalConfig()).validate()
        clock=GregorianClock(config.longitudinal)
        state={'metrics':{'work_events':0.},'model_version':'longitudinal-1','people':[{}]*12,
               'slots':{str(i):i for i in range(12)},'closed_day':None,'suspended_day':None,
               'cash':100.,'adoption':{'0':None,'1':None,'2':None,'3':None},'branch_origin':None}
        summary={'work_events':0.,'model_version':'longitudinal-1','world':config.world,'regime':config.regime,
                 'backend':config.backend,'attack':config.attack,'n':12,'days_completed':2,
                 'calendar_start':config.longitudinal.calendar_start,'calendar_end_exclusive':clock.at(2).isoformat(),
                 'ever_people':12,'closure_day':None,'suspension_day':None,'cash_resource_units':100.,
                 'adoption_days':dict(state['adoption']),'active_members_final':12,'branch_origin':None,
                 'unfinished_records':1,'unfinished_appeals':2}
        with tempfile.TemporaryDirectory() as directory:
            database=Path(directory)/'fixture.sqlite'
            with closing(sqlite3.connect(database)) as connection,connection:
                connection.execute('CREATE TABLE pending(kind TEXT)')
                connection.executemany('INSERT INTO pending VALUES(?)',[('review',),('appeal',),('appeal',)])
            verify_summary_evidence(summary,config,state,database)
            summary['adoption_days']['0']=0
            with self.assertRaisesRegex(ValueError,'lifecycle'):verify_summary_evidence(summary,config,state,database)
            summary['adoption_days']=dict(state['adoption']);summary['work_events']=1.
            with self.assertRaisesRegex(ValueError,'summary metric'):verify_summary_evidence(summary,config,state,database)

    def test_raw_event_accounting_accepts_work_tail_and_zero_funding_closure(self):
        from research_tools.validate_longitudinal import verify_daily_evidence
        for kwargs in ({},{'stop':1},{'cash_per_member':0.}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as directory:
                config,state,path,database=raw_accounting_fixture(directory,**kwargs)
                with patch('dams_sim.model.Model.__init__',side_effect=AssertionError('Model construction forbidden')):
                    self.assertEqual(verify_daily_evidence(path,config,state,database)['day'],2)
                if kwargs.get('cash_per_member')==0:
                    self.assertEqual(state['history'][1]['present_members'],0)
                    self.assertEqual(state['history'][1]['present_member_workdays_daily'],12)

    def test_resealed_lowlevel_output_and_decision_mutations_are_rejected(self):
        from research_tools.validate_longitudinal import verify_daily_evidence
        mutations=(('work','produced_research_only',124.,'work/resource'),
            ('work','effort',.5,'effort_hours'),('work','funding_fraction',.5,'funding/training'),
            ('work','effort',1.,'time capacity'),('work','effort',0.,'zero-effort'),
            ('work','fraudulent',True,'declared attack'),('work','cooperation',.25,'cooperation'),
            ('decision','loss',123.,'decision fields'),('decision','effective_mass',.5,'decision fields'),
            ('decision','participation',11,'decision fields'),('vote','effective_weight',.5,'signal/weight'),
            ('vote','observed_signal',-1.,'signal/weight'),('commit','person',7,'source person/domain'))
        for kind,key,value,message in mutations:
            with self.subTest(kind=kind,key=key),tempfile.TemporaryDirectory() as directory:
                config,state,path,database=raw_accounting_fixture(directory)
                before=canonical(state);csv_before=path.read_bytes()
                with closing(sqlite3.connect(database)) as connection,connection:
                    seq,payload=connection.execute('SELECT seq,payload FROM journal WHERE kind=? ORDER BY seq LIMIT 1',(kind,)).fetchone()
                    event=json.loads(payload);event[key]=value
                    connection.execute('UPDATE journal SET payload=? WHERE seq=?',(canonical(event).decode(),seq))
                with patch('dams_sim.model.Model.__init__',side_effect=AssertionError('Model construction forbidden')):
                    with self.assertRaisesRegex(ValueError,message):verify_daily_evidence(path,config,state,database)
                self.assertEqual(canonical(state),before);self.assertEqual(path.read_bytes(),csv_before)

    def test_raw_prefix_uses_the_actual_parent_time_parameters(self):
        from research_tools.validate_longitudinal import verify_daily_evidence
        with tempfile.TemporaryDirectory() as directory:
            parent,state,path,database=raw_accounting_fixture(directory,effort=.5)
            child=dataclasses.replace(parent,review_capacity_per_member_day=5.5).validate()
            with self.assertRaisesRegex(ValueError,'time capacity'):verify_daily_evidence(path,child,state,database)
            self.assertEqual(verify_daily_evidence(path,child,state,database,config_history=((3,parent),))['day'],2)

    def test_untriggered_zero_metrics_and_cumulative_differences_are_verified(self):
        from research_tools.validate_longitudinal import verify_daily_evidence
        for key,daily,cumulative,message in (
            ('fraudulent_records_submitted',100.,100.,'accounting differs'),
            ('quorum_unavailable_guild_days',0.,100.,'cumulative difference')):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as directory:
                config,state,path,database=raw_accounting_fixture(directory)
                with closing(sqlite3.connect(database)) as connection,connection:
                    seq,payload=connection.execute("SELECT seq,payload FROM journal WHERE kind='day_end' AND day=0").fetchone()
                    event=json.loads(payload);event['metrics_daily'][key]=daily;event['metrics_cumulative'][key]=cumulative
                    connection.execute('UPDATE journal SET payload=? WHERE seq=?',(canonical(event).decode(),seq))
                with self.assertRaisesRegex(ValueError,message):verify_daily_evidence(path,config,state,database)

    def test_final_person_inventory_and_individual_exposure_are_tied_to_events(self):
        from research_tools.validate_longitudinal import verify_daily_evidence
        for target in ('slots','present_workdays'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as directory:
                config,state,path,database=raw_accounting_fixture(directory)
                if target=='slots':del state['slots']['0']
                else:state['people'][0]['present_workdays']+=1
                with self.assertRaisesRegex(ValueError,'retained person roster|individual exposure'):
                    verify_daily_evidence(path,config,state,database)

    def test_unfunded_participation_and_canonical_event_identity_are_rejected(self):
        from research_tools.validate_longitudinal import verify_daily_evidence
        for target in ('unfunded','identity'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as directory:
                config,state,path,database=raw_accounting_fixture(directory,cash_per_member=0. if target=='unfunded' else 100.)
                with closing(sqlite3.connect(database)) as connection,connection:
                    kind='vote' if target=='unfunded' else 'work'
                    seq,payload=connection.execute('SELECT seq,payload FROM journal WHERE kind=? ORDER BY seq LIMIT 1',(kind,)).fetchone()
                    if target=='unfunded':
                        event=json.loads(payload);event.update(participates=True,effective_weight=1./12)
                        connection.execute('UPDATE journal SET payload=? WHERE seq=?',(canonical(event).decode(),seq))
                    else:connection.execute('UPDATE journal SET event=? WHERE seq=?',('forged-work-id',seq))
                with self.assertRaisesRegex(ValueError,'unfunded person|event identity'):verify_daily_evidence(path,config,state,database)

    def test_closure_and_review_capacity_cannot_be_fabricated(self):
        from research_tools.validate_longitudinal import verify_daily_evidence
        for target in ('funded-closure','duplicate-closure','review-capacity'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as directory:
                config,state,path,database=raw_accounting_fixture(directory,cash_per_member=0. if target=='duplicate-closure' else 100.)
                with closing(sqlite3.connect(database)) as connection,connection:
                    def insert(day,kind,event,payload):
                        seq=connection.execute("SELECT seq FROM journal WHERE day=? AND kind='day_end'",(day,)).fetchone()[0]
                        connection.execute('UPDATE journal SET seq=-seq WHERE seq>=?',(seq,))
                        connection.execute('UPDATE journal SET seq=-seq+1 WHERE seq<0')
                        connection.execute('INSERT INTO journal VALUES(?,?,?,?,?)',(seq,day,kind,event,canonical(payload).decode()))
                    if target=='funded-closure':insert(0,'closure','closure:0',{'cause':'consecutive_unfunded_workdays','cash':1193.88})
                    elif target=='duplicate-closure':insert(1,'closure','closure:1',{'cause':'consecutive_unfunded_workdays','cash':0.})
                    else:
                        payload={'backend':'central','accepted':True,'observed':1.,'audit_detected':False}
                        for _ in range(11):insert(1,'review','work:0:initial:0:g:0',payload)
                with self.assertRaisesRegex(ValueError,'unfunded workdays|automatic closure|funded domain capacity'):
                    verify_daily_evidence(path,config,state,database)

    def test_publication_contract_accepts_exact_scientific_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            checked,_generation,_seal=publication_fixture(directory)
            with patch('dams_sim.model.Model.__init__',side_effect=AssertionError('Model construction forbidden')):
                checked._publication()

    def test_daily_adoption_key_order_boundary_keeps_exact_numeric_csv(self):
        from research_tools.validate_longitudinal import verify_daily_evidence
        with tempfile.TemporaryDirectory() as directory:
            config,state,path,database=nonwork_guild_fixture(directory)
            with closing(sqlite3.connect(database)) as connection:
                record=json.loads(connection.execute("SELECT payload FROM journal WHERE kind='day_end' LIMIT 1").fetchone()[0])
            numeric=state['history'][0]['adoption_days'];lexical=canonical(record['adoption_days']).decode()
            self.assertLess(numeric.index('"2"'),numeric.index('"10"'))
            self.assertLess(lexical.index('"10"'),lexical.index('"2"'))
            with patch('dams_sim.model.Model.__init__',side_effect=AssertionError('Model construction forbidden')) as constructor:
                self.assertEqual(verify_daily_evidence(path,config,state,database)['day'],1)
                self.assertEqual(constructor.call_count,0)
            state['history'][0]['adoption_days']=lexical;atomic_csv(path,state['history'])
            with self.assertRaisesRegex(ValueError,'daily adoption differs'):verify_daily_evidence(path,config,state,database)

    def test_daily_adoption_aliases_duplicate_ids_and_changed_dates_fail(self):
        from research_tools.validate_longitudinal import verify_daily_evidence
        mutations=(('01','integer state key'),('+1','integer state key'),('-0','integer state key'),
                   ('-1','negative'),('missing','guild roster'),('boolean-date','adoption date'),
                   ('future-date','adoption date'),('unrecorded-date','lifecycle/adoption'),('duplicate','duplicate JSON key'))
        for mutation,message in mutations:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                config,state,path,database=nonwork_guild_fixture(directory)
                with closing(sqlite3.connect(database)) as connection,connection:
                    seq,payload=connection.execute("SELECT seq,payload FROM journal WHERE kind='day_end' LIMIT 1").fetchone()
                    value=json.loads(payload)
                    if mutation=='duplicate':payload=payload.replace('"adoption_days":{','"adoption_days":{"0":null,',1)
                    else:
                        if mutation=='missing':del value['adoption_days']['0']
                        elif mutation=='boolean-date':value['adoption_days']['0']=False
                        elif mutation=='future-date':value['adoption_days']['0']=1
                        elif mutation=='unrecorded-date':
                            value['adoption_days']['0']=0
                            days={int(k):v for k,v in value['adoption_days'].items()}
                            state['history'][0]['adoption_days']=canonical(days).decode();atomic_csv(path,state['history'])
                        else:value['adoption_days'][mutation]=value['adoption_days'].pop('0')
                        payload=canonical(value).decode()
                    connection.execute('UPDATE journal SET payload=? WHERE seq=?',(payload,seq))
                with patch('dams_sim.model.Model.__init__',side_effect=AssertionError('Model construction forbidden')):
                    with self.assertRaisesRegex(ValueError,message):verify_daily_evidence(path,config,state,database)

    def test_publication_rejects_all_symlink_nodes_including_dangling(self):
        for target in ('dangling','unrecorded-directory','recorded-file','publication-root'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as directory:
                checked,_generation,_seal=publication_fixture(directory);pub=checked.root/'publication'
                if target=='publication-root':
                    retained=checked.root/'retained-publication';pub.rename(retained);pub.symlink_to(retained,target_is_directory=True)
                elif target=='recorded-file':
                    path=pub/'generated/longitudinal_results.tex';retained=checked.root/'retained-result.tex'
                    path.rename(retained);path.symlink_to(retained)
                else:(pub/'unexpected-link').symlink_to(checked.root/('absent' if target=='dangling' else 'publication'),target_is_directory=target!='dangling')
                with patch('dams_sim.model.Model.__init__',side_effect=AssertionError('Model construction forbidden')):
                    with self.assertRaisesRegex(ValueError,'publication tree.*symlinked'):checked._publication()

    def test_validator_import_identity_cannot_be_relabelled_by_disk_changes(self):
        import research_tools.validate_longitudinal as validator
        original=validator.file_digest
        with tempfile.TemporaryDirectory() as directory:
            checked,_generation,_seal=publication_fixture(directory)
            def changed(path):return 'd'*64 if Path(path)==Path(validator.__file__) else original(path)
            with patch.object(validator,'file_digest',side_effect=changed):
                with self.assertRaisesRegex(ValueError,'validator source changed after import'):checked._publication()
                with self.assertRaisesRegex(ValueError,'validator source changed after import'):validator.CheckedLongitudinalStudy(directory)
            with patch.object(validator,'source_hash',return_value='e'*64):
                with self.assertRaisesRegex(ValueError,'scientific source changed after import'):checked._publication()

    def test_resealed_publication_identity_inputs_and_artifact_omissions_fail(self):
        for target in ('driver','protocol','analyzer','inputs','required'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as directory:
                checked,generation,seal=publication_fixture(directory)
                if target=='driver':generation['pipeline_driver_sha256']='0'*64
                elif target=='protocol':generation['protocol_sha256']='0'*64
                elif target=='analyzer':generation['analysis_sources_sha256']['research_tools/longitudinal_analysis.py']='0'*64
                elif target=='inputs':del generation['inputs']['retained-raw.bin']
                else:
                    name='generated/longitudinal_results.tex';del generation['outputs'][name];(checked.root/'publication'/name).unlink()
                seal()
                with self.assertRaises(ValueError):checked._publication()

    def test_resealed_descriptive_definitions_cohorts_and_alignment_are_rebuilt(self):
        targets=('definitions','limitations','cohort','calendar','coverage','relative','relative-roster')
        for target in targets:
            with self.subTest(target=target),tempfile.TemporaryDirectory() as directory:
                checked,generation,seal=publication_fixture(directory)
                if target in ('definitions','limitations'):
                    name='generated/analysis/'+('derived_definitions.json' if target=='definitions' else 'numerical_claims.json')
                    path=checked.root/'publication'/name;value=json.loads(path.read_text())
                    if target=='definitions':value['formal_allocation']='empirically validated actual influence'
                    else:value['limitations']=[]
                    atomic_json(path,value)
                elif target=='relative-roster':
                    name='figures/results/longitudinal-relative-adoption-trajectory.svg'
                    path=checked.root/'publication'/name;path.unlink();del generation['outputs'][name]
                else:
                    name='generated/analysis/'+{'cohort':'cohort_and_memory_descriptives','calendar':'paired_trajectory',
                        'coverage':'relative_adoption_world_coverage','relative':'relative_adoption_trajectory'}[target]+'.csv'
                    path=checked.root/'publication'/name
                    with path.open(newline='') as stream:rows=list(csv.DictReader(stream))
                    key={'cohort':'assigned_people','calendar':'mean_paired_difference','coverage':'actual_last_guild_adoption_day',
                         'relative':'mean_paired_difference'}[target]
                    rows[0][key]='12345';atomic_csv(path,rows)
                if target!='relative-roster':generation['outputs'][name]=file_digest(path)
                seal()
                with patch('dams_sim.model.Model.__init__',side_effect=AssertionError('Model construction forbidden')) as constructor:
                    with self.assertRaises(ValueError):checked._publication()
                    self.assertEqual(constructor.call_count,0)

    def test_resealed_numerical_claims_cannot_change_raw_results(self):
        for key,value in (('confirmation_worlds',3),('mc_precision_met',True),('primary',[]),('spec',{})):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as directory:
                checked,generation,seal=publication_fixture(directory)
                name='generated/analysis/numerical_claims.json';path=checked.root/'publication'/name
                claims=json.loads(path.read_text());claims[key]=value;atomic_json(path,claims)
                generation['outputs'][name]=file_digest(path);seal()
                with self.assertRaisesRegex(ValueError,'numerical claims'):checked._publication()


if __name__=='__main__':unittest.main()
