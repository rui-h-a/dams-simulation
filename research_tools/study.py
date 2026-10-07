"""Versioned DAMS experiments. Run from the new research repository.

No empirical calibration is asserted. Independent world IDs are the units of
inference. Completed raw outputs are immutable; semantic case IDs support retry.
"""
from __future__ import annotations
import argparse
import dataclasses
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.cli import run_world
from dams_sim.storage import atomic_json, atomic_csv, canonical, digest, provenance, source_hash
from dams_sim.authority import authority, tier_authority, total_variation, gini, split_gain
from dams_sim.randomness import WorldRandom

POLICIES = ('equal','linear','sublinear','hierarchy','hierarchy_tenure')
BACKENDS = ('central','witness','consensus')
LABELS = dict(equal='Equal eligible',linear='Linear credit',sublinear='DAMS sublinear',hierarchy='Performance tiers',hierarchy_tenure='Tenure tiers')
BASE = Config(n=120, days=60, guilds=4, team_size=5, sites=2, seed=20261007, trace_every_days=1, max_wall_seconds=120, max_output_mb=20)
METRICS = ('produced_work_units','decision_regret_units','confirmation_mean_days','confirmation_p95_days','unfinished_records','unfinished_appeals','review_hours','appeal_hours','verification_resource_units','last_authority_contribution_tv','last_signed_gini_gap')

def case_id(config):
    return digest(canonical(config.to_dict()))[:20]

def saved_case(base, config):
    ident = case_id(config)
    case = base/('case-'+ident)
    if case.exists():
        manifest = json.loads((case/'manifest.json').read_text())
        if manifest['source_sha256'] != source_hash():
            raise RuntimeError('existing case source changed; create a new experiment directory')
        if manifest['status'] == 'complete' and manifest['config'] == json.loads(canonical(config.to_dict())):
            return json.loads((case/'summary.json').read_text())
        # Keep failed and checkpointed evidence. Retry never overwrites it.
        suffix = 1
        while (base/f'case-{ident}-retry-{suffix}').exists(): suffix += 1
        case = base/f'case-{ident}-retry-{suffix}'
    case.mkdir(parents=True,exist_ok=False)
    result = run_world(config,case)
    if result['status'] != 'complete': raise RuntimeError('incomplete horizon cannot enter ensemble')
    return result['summary']

def records_for(base):
    rows=[]
    for case in sorted(base.glob('case-*')):
        m=json.loads((case/'manifest.json').read_text())
        if m['status']!='complete': continue
        r=json.loads((case/'summary.json').read_text())
        r.update(case=case.name, config_sha256=m['config_sha256'], source_sha256=m['source_sha256'])
        rows.append(r)
    return rows

def interval(values):
    """Mean, independent-world SE and fixed normal Monte Carlo interval.

    This describes Monte Carlo uncertainty conditional on a model, not uncertainty
    about real organizations. Small exploratory cells are reported as descriptive.
    """
    x=[v for v in values if v is not None]
    if not x: return dict(n=0,mean=None,se=None,low=None,high=None)
    mean=statistics.fmean(x)
    if len(x)<2: return dict(n=1,mean=mean,se=None,low=None,high=None)
    se=statistics.stdev(x)/math.sqrt(len(x))
    return dict(n=len(x),mean=mean,se=se,low=mean-1.959963984540054*se,high=mean+1.959963984540054*se)

def pilot(path):
    path.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    rows=[]
    for w in range(4):
        for policy in POLICIES:
            p=dataclasses.replace(BASE,world=w,regime=policy)
            rows.append(saved_case(path,p))
    atomic_csv(path/'pilot_summary.csv',rows)
    diffs=[]
    for w in range(4):
        a=next(r for r in rows if r['world']==w and r['regime']=='sublinear')
        b=next(r for r in rows if r['world']==w and r['regime']=='linear')
        diffs.append((a['produced_work_units']-b['produced_work_units'])/(BASE.n*BASE.days))
    # Fixed-world design based only on pilot variability, never significance.
    sd=statistics.stdev(diffs)
    target_halfwidth=0.01
    requested=math.ceil((1.959963984540054*sd/target_halfwidth)**2)
    worlds=min(64,max(32,requested))
    protocol={
      **provenance(), 'status':'preregistered-after-pilot-before-confirmation',
      'base':BASE.to_dict(),'pilot_worlds':list(range(4)),
      'confirm_worlds':list(range(1000,1000+worlds)),
      'primary_contrast':'sublinear minus linear, central, interval 1',
      'primary_outcome':'produced_work_units/(n*days), generated work units per member-day',
      'secondary_outcomes':list(METRICS),
      'mc_interval':'mean +/- 1.959963984540054 * paired-world SE; conditional model uncertainty',
      'mc_target_halfwidth':target_halfwidth,'substantive_effect_threshold_work_per_member_day':0.02,
      'world_count_rule':'ceil((1.96 * pilot paired SD / .01)^2), minimum 32, maximum 64; no post-outcome significance stopping',
      'pilot_sd':sd,'requested_worlds':requested,'worlds':worlds,
      'cadences_days':[1,14],'backends':list(BACKENDS),'policies':list(POLICIES),
      'failed_cases':'excluded from complete estimates only with explicit report and retry record; never relabeled successful',
      'scope':'synthetic behavioral design assumptions; no observational calibration or population prediction',
      'total_pilot_wall_seconds':time.monotonic()-started,
    }
    atomic_json(path/'protocol.json',protocol)
    print(json.dumps({'pilot':str(path),'worlds_fixed':worlds,'pilot_sd':sd,'source_sha256':source_hash()},indent=2),flush=True)


def confirmation(path, protocol_path):
    protocol=json.loads(protocol_path.read_text())
    if protocol['source_sha256']!=source_hash(): raise RuntimeError('protocol source differs; rerun a new pilot before confirmation')
    path.mkdir(parents=True,exist_ok=True)
    atomic_json(path/'protocol.json',protocol)
    p0=Config.from_dict(protocol['base'])
    rows=[]
    for w in protocol['confirm_worlds']:
        for cadence in protocol['cadences_days']:
            for policy in POLICIES:
                for backend in BACKENDS:
                    p=dataclasses.replace(p0,world=w,regime=policy,backend=backend,update_interval_days=cadence)
                    rows.append(saved_case(path,p))
        atomic_csv(path/'world_summary.csv',rows)
        print('completed independent world',w,flush=True)
    atomic_json(path/'ensemble_manifest.json',{**provenance(),'status':'complete','protocol_sha256':digest(protocol_path.read_bytes()),'policy_runs':len(rows),'worlds':len(protocol['confirm_worlds'])})


def stress(path):
    path.mkdir(parents=True,exist_ok=True)
    rows=[]
    # Declared equal cost is enforced in Model, not merely counted by this driver.
    for w in range(2000,2008):
        for policy in POLICIES:
            for budget in (2.0,8.0):
                for attack in ('none','forge','duplicate','freeride','censor'):
                    p=dataclasses.replace(BASE,world=w,regime=policy,attack=attack,attack_budget_hours_per_day=budget)
                    r=saved_case(path,p);r['attack_budget_setting']=budget;rows.append(r)
    for w in range(2100,2108):
        for backend in BACKENDS:
            for protection in ((1.,1.,1.),(1.,.5,0.),(0.,0.,0.)):
                p=dataclasses.replace(BASE,world=w,backend=backend,attack='censor',attack_budget_hours_per_day=8.,administrative_censorship_exposure=protection)
                r=saved_case(path,p);r['protection_assumption']=json.dumps(protection);rows.append(r)
            p=dataclasses.replace(BASE,world=w,backend=backend,quorum_unavailable_start_day=20,quorum_unavailable_stop_day=35)
            r=saved_case(path,p);r['stress_kind']='quorum_unavailable';rows.append(r)
    atomic_csv(path/'stress_summary.csv',rows)
    atomic_json(path/'ensemble_manifest.json',{**provenance(),'status':'complete','policy_runs':len(rows),'independent_world_ranges':[[2000,2007],[2100,2107]],'descriptive_only':True})
    print('stress runs',len(rows),flush=True)


def mechanisms(path):
    path.mkdir(parents=True,exist_ok=True)
    rows=[]
    # Same exogenous credit stream: no endogenous output comparisons here.
    for n in (120,500,1000,5000,10000):
        rng=WorldRandom(20261007,3000)
        credit=[math.exp(.4*rng.normal('fixed-credit',i)) for i in range(n)]
        tenure=[rng.uniform('fixed-tenure',i) for i in range(n)]
        reference=authority(credit,1,zero_policy='equal')
        for alpha in (0.,.2,.5,.8,.95,1.):
            shares=authority(credit,alpha,zero_policy='equal')
            rows.append(dict(n=n,alpha=alpha,policy='power',tv=total_variation(shares,reference),gini=gini(shares),signed_gini_gap=gini(shares)-gini(credit)))
        for basis,scores in (('performance',credit),('tenure',tenure)):
            shares=tier_authority(scores,(1,2,4,8))
            rows.append(dict(n=n,policy=basis+' tiers',alpha=None,tv=total_variation(shares,reference),gini=gini(shares),signed_gini_gap=gini(shares)-gini(credit)))
    atomic_csv(path/'fixed_stream.csv',rows)
    splits=[]
    for alpha in (.2,.5,.8,.95,1.):
        for k in (1,2,5,10,20):
            for credit in ([1.,3.,5.,9.],[100.,1.,1.,1.]):
                result=split_gain(credit,0,k,alpha)
                a=result['authority_before'];m=k**(1-alpha)
                exact=m*a/(1+(m-1)*a)
                if not math.isclose(result['authority_after'],exact,abs_tol=1e-12):raise AssertionError('independent normalized split expression differs')
                splits.append(dict(alpha=alpha,accounts=k,actor_credit=credit[0],**result,raw_weight_multiplier=m,normalized_oracle=exact))
    atomic_csv(path/'split_gain.csv',splits)
    # Repeated distributions: TV invariant, old MAD falls mechanically with N.
    replication=[]
    for copies in (1,10,100,1000):
        a=[.1,.2,.3,.4]*copies;b=[.2,.2,.2,.4]*copies
        a=[x/copies for x in a];b=[x/copies for x in b]
        tv=total_variation(a,b);old=math.fsum(abs(x-y) for x,y in zip(a,b))/len(a)
        replication.append(dict(n=len(a),tv=tv,legacy_mean_absolute_deviation=old))
    atomic_csv(path/'metric_replication.csv',replication)
    atomic_json(path/'manifest.json',{**provenance(),'status':'complete','evidence_type':'analytical and fixed-stream synthetic diagnostic','not_dynamic_experiment':True})
    print('mechanism and metric checks complete',flush=True)


def sensitivity(path):
    path.mkdir(parents=True,exist_ok=True)
    # Independent design dimensions, not a fitted joint population distribution.
    factors={
      'alpha':(.4,.6,.8,.95), 'review_capacity_per_member_day':(.4,.8,1.2,1.6),
      'review_error_sd':(0.,.2,.4,.6), 'autonomy_response':(-.5,0.,.5,1.),
      'cooperation_strength':(0.,.3,.6,.9), 'update_interval_days':(1,5,10,20)}
    rng=WorldRandom(20261007,4000);rows=[];effects=[]
    names=list(factors)
    for trajectory in range(8):
        levels={k:min(2,int(rng.uniform('level',trajectory,k)*3)) for k in names}
        order=sorted(names,key=lambda k:rng.uniform('factor-order',trajectory,k))
        def evaluate(step):
            vals={k:factors[k][levels[k]] for k in names}
            metrics=[]
            for w in range(4000+trajectory*3,4003+trajectory*3):
                p=dataclasses.replace(BASE,world=w,**vals)
                r=saved_case(path,p);r.update(trajectory=trajectory,step=step,**vals);rows.append(r)
                metrics.append(r['produced_work_units']/(p.n*p.days))
            return statistics.fmean(metrics)
        previous=evaluate(0)
        for step,k in enumerate(order,1):
            before=factors[k][levels[k]];levels[k]+=1;after=factors[k][levels[k]]
            current=evaluate(step)
            # Normalize the finite step by the configured design-space width.
            dx=(after-before)/(factors[k][-1]-factors[k][0])
            effects.append(dict(trajectory=trajectory,factor=k,effect_per_full_design_range=(current-previous)/dx))
            previous=current
        print('sensitivity trajectory',trajectory,flush=True)
    atomic_csv(path/'sensitivity_runs.csv',rows);atomic_csv(path/'elementary_effects.csv',effects)
    alternatives=[]
    for w in range(4100,4108):
        for behavior in ('linear_response','satisficing','reinforcement'):
            for policy in ('linear','sublinear'):
                for response in (-.25,0.,.25):
                    p=dataclasses.replace(BASE,world=w,regime=policy,behavior_rule=behavior,autonomy_response=response)
                    r=saved_case(path,p);r.update(behavior_rule=behavior,autonomy_response=response);alternatives.append(r)
    atomic_csv(path/'structural_alternatives.csv',alternatives)
    atomic_json(path/'ensemble_manifest.json',{**provenance(),'status':'complete','design':'finite-difference elementary effects; eight randomized paths, three paired worlds per path, independent design factors; not Sobol indices','structural_runs':len(alternatives)})


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage',choices=['pilot','confirmation','stress','mechanisms','sensitivity'])
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--protocol',type=Path)
    args=ap.parse_args()
    if args.stage=='confirmation':
        if args.protocol is None:ap.error('confirmation needs --protocol from a completed pilot')
        confirmation(args.output,args.protocol)
    else:globals()[args.stage](args.output)

if __name__=='__main__':main()
