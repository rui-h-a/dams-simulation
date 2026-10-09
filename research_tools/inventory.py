"""Frozen design inventories, independent of which output folders happen to exist.

The executed study/extended drivers remain immutable. This read-only inventory
reconstructs their declared Config cells, including resource and timing fields,
so dropping a whole world cannot silently reduce an eight-world comparison.
"""
from __future__ import annotations
import dataclasses
import json
import math
import statistics
from pathlib import Path
from dams_sim.config import Config
from dams_sim.randomness import WorldRandom
from dams_sim.storage import canonical, digest, source_hash
from research_tools.study import BASE, POLICIES, BACKENDS, DRIVER_SHA

FACTORS = {
    'alpha':(.4,.6,.8,.95), 'review_capacity_per_member_day':(.4,.8,1.2,1.6),
    'review_error_sd':(0.,.2,.4,.6), 'autonomy_response':(-.5,0.,.5,1.),
    'cooperation_strength':(0.,.3,.6,.9), 'update_interval_days':(1,5,10,20)}
CONTEXTS = {
    'noisy_knowledge':dict(decision_noise_sd=1.2,shared_signal_sd=.4,cooperation_strength=.6),
    'interdependent_delivery':dict(cooperation_strength=.9,review_capacity_per_member_day=1.2,review_error_sd=.3),
    'distributed_disruption':dict(guilds=8,sites=4,fault_start_day=10,fault_stop_day=25,review_capacity_per_member_day=.7),
    'high_observation_error':dict(review_error_sd=.6,review_capacity_per_member_day=1.2,appeal_capacity_per_member_day=.2),
    'cross_principal_confirmation':dict(backend='witness',review_capacity_per_member_day=.7,backend_review_multipliers=(1.,1.3,1.8)),
    'review_scarcity':dict(review_capacity_per_member_day=.4,appeal_capacity_per_member_day=.05,cooperation_strength=.6),
    'delayed_accountability':dict(update_interval_days=14,appeal_delay_days=7,appeal_capacity_per_member_day=.2),
    'zero_response':dict(autonomy_response=0.), 'reversed_response':dict(autonomy_response=-.5)}

def validate_protocol(protocol, pilot_rows=None):
    if protocol['source_sha256']!=source_hash() or protocol['research_driver_sha256']!=DRIVER_SHA:
        raise ValueError('protocol source/driver mismatch')
    if canonical(protocol['base'])!=canonical(BASE.to_dict()):raise ValueError('protocol base differs from frozen design')
    if protocol['pilot_worlds']!=list(range(4)) or protocol['policies']!=list(POLICIES) or protocol['backends']!=list(BACKENDS) or protocol['cadences_days']!=[1,14]:
        raise ValueError('protocol factors differ from frozen design')
    if protocol['mc_target_halfwidth']!=.01 or protocol['substantive_effect_threshold_work_per_member_day']!=.02:
        raise ValueError('protocol thresholds differ')
    sd=protocol['pilot_sd']
    if pilot_rows is not None:
        differences=[]
        for w in range(4):
            a=[r for r in pilot_rows if r['world']==w and r['regime']=='sublinear']
            b=[r for r in pilot_rows if r['world']==w and r['regime']=='linear']
            if len(a)!=1 or len(b)!=1:raise ValueError('pilot pair missing or duplicated')
            differences.append((a[0]['produced_work_units']-b[0]['produced_work_units'])/(BASE.n*BASE.days))
        actual=statistics.stdev(differences)
        if not math.isclose(sd,actual,rel_tol=1e-12,abs_tol=1e-14):raise ValueError('protocol pilot variance differs')
    requested=math.ceil((1.959963984540054*sd/.01)**2)
    worlds=min(64,max(32,requested))
    if protocol['requested_worlds']!=requested or protocol['worlds']!=worlds or protocol['confirm_worlds']!=list(range(1000,1000+worlds)):
        raise ValueError('protocol world cardinality/stopping rule differs')
    if protocol['status']!='preregistered-after-pilot-before-confirmation':raise ValueError('protocol status differs')
    return worlds

def expected_configs(stage, filename, protocol=None):
    configs=[]
    def add(**kwargs):configs.append(dataclasses.replace(BASE,**kwargs))
    if stage=='pilot':
        for w in range(4):
            for k in POLICIES:add(world=w,regime=k)
    elif stage=='confirmation':
        validate_protocol(protocol)
        for w in protocol['confirm_worlds']:
            for c in (1,14):
                for k in POLICIES:
                    for b in BACKENDS:add(world=w,regime=k,backend=b,update_interval_days=c)
    elif stage=='stress':
        for w in range(2000,2008):
            for k in POLICIES:
                for cost in (2.,8.):
                    for attack in ('none','forge','duplicate','freeride','censor'):
                        add(world=w,regime=k,attack=attack,attack_budget_hours_per_day=cost)
        for w in range(2100,2108):
            for b in BACKENDS:
                for protection in ((1.,1.,1.),(1.,.5,0.),(0.,0.,0.)):
                    add(world=w,backend=b,attack='censor',attack_budget_hours_per_day=8.,administrative_censorship_exposure=protection)
                add(world=w,backend=b,quorum_unavailable_start_day=20,quorum_unavailable_stop_day=35)
    elif stage=='sensitivity':
        if filename=='sensitivity_runs.csv':
            rng=WorldRandom(20261007,4000);names=list(FACTORS)
            for t in range(8):
                levels={k:min(2,int(rng.uniform('level',t,k)*3)) for k in names}
                order=sorted(names,key=lambda k:rng.uniform('factor-order',t,k))
                for step in range(7):
                    if step:levels[order[step-1]]+=1
                    vals={k:FACTORS[k][levels[k]] for k in names}
                    for w in range(4000+t*3,4003+t*3):add(world=w,**vals)
        elif filename=='structural_alternatives.csv':
            for w in range(4100,4108):
                for rule in ('linear_response','satisficing','reinforcement'):
                    for k in ('linear','sublinear'):
                        for response in (-.25,0.,.25):add(world=w,regime=k,behavior_rule=rule,autonomy_response=response)
        else:raise ValueError('unknown sensitivity output')
    elif stage=='scenarios':
        for changes in CONTEXTS.values():
            for w in range(5000,5008):
                for k in POLICIES:add(world=w,regime=k,**changes)
    elif stage=='extended':
        for n in (500,1000,5000,10000):
            for w in range(8000,8008):
                for k in POLICIES:add(n=n,days=30,world=w,regime=k,guilds=4,max_wall_seconds=180,max_output_mb=120,max_events=400000,max_rss_mb=2048)
        for w in range(8100,8108):
            for k in POLICIES:
                for fault in (False,True):add(world=w,regime=k,fault_start_day=25 if fault else 0,fault_stop_day=30 if fault else 0)
    else:raise ValueError('unknown stage '+stage)
    return configs

def verify_inventory(path, filename, summaries, manifest=None):
    stage=path.name
    protocol=json.loads((path/'protocol.json').read_text()) if stage=='confirmation' else None
    expected=[digest(canonical(c.to_dict())) for c in expected_configs(stage,filename,protocol)]
    observed=[digest(canonical(config)) for name,config,summary in summaries]
    if len(observed)!=len(set(observed)) or sorted(observed)!=sorted(expected):
        raise ValueError(stage+' planned Config inventory missing, altered or duplicated')
    if manifest is not None:
        if 'policy_runs' in manifest and manifest['policy_runs']!=len(expected):raise ValueError('ensemble policy count contradicts inventory')
        if stage=='confirmation' and manifest.get('worlds')!=protocol['worlds']:raise ValueError('ensemble world count differs')
        if stage=='extended' and manifest.get('expected_cases')!=len(expected):raise ValueError('extended expected case count differs')
        if stage=='sensitivity' and manifest.get('structural_runs')!=144:raise ValueError('structural count differs')
    if stage=='scenarios':
        definitions=json.loads((path/'scenario_definitions.json').read_text())['contexts']
        if canonical(definitions)!=canonical(CONTEXTS):raise ValueError('scenario definitions differ from frozen design')
    return len(expected)
