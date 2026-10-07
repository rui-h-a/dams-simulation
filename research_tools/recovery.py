"""Synthetic calibration/recovery procedure, deliberately separate from field validation.

Training pseudo-observations and simulation predictions use disjoint world IDs.
Model selection uses four patterns and never selects on the held-out worlds.
"""
from __future__ import annotations
import argparse
import dataclasses
import json
import math
from pathlib import Path
import statistics
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.storage import atomic_json,atomic_csv,provenance,digest,output_hashes

BASE=Config(n=24,days=30,guilds=3,team_size=4,seed=20261007,trace_every_days=1)
RULES=('linear_response','satisficing','reinforcement')
PATTERNS=('work_per_member_day','regret_per_guild_day','unfinished_per_member','mean_trust')
FLOORS=(.02,.02,.1,.01)

def simulate(config):
    m=Model(config).run();s=m.summary()
    return {'work_per_member_day':s['produced_work_units']/(config.n*config.days),
       'regret_per_guild_day':s['decision_regret_units']/(config.guilds*config.days),
       'unfinished_per_member':s['unfinished_records']/config.n,
       'mean_trust':m.history[-1]['mean_trust']}

def moments(rows):
    return {k:statistics.fmean(r[k] for r in rows) for k in PATTERNS}

def distance(observed,predicted,scales):
    a=moments(observed);b=moments(predicted)
    return math.fsum(((a[k]-b[k])/scales[k])**2 for k in PATTERNS)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=False)
    metadata={**provenance(),'driver_sha256':digest(Path(__file__).read_bytes()),'status':'running',
      'evidence':'synthetic procedure check, no acquired observational data',
      'patterns':list(PATTERNS),'training_worlds':list(range(7000,7008)),
      'prediction_worlds':list(range(7100,7108)),'holdout_observation_worlds':list(range(7008,7012)),
      'holdout_prediction_worlds':list(range(7108,7112)),
      'fit':'equal pattern weights; diagonal standardized squared mean distance; uncertainty floors declared before fitting',
      'feasible_set':'distance <= best distance + 1; diagnostic tolerance, not a confidence region',
      'limits':'small synthetic grid and four moments do not establish empirical identification or calibrated prediction'}
    atomic_json(out/'manifest.json',metadata)
    grid=[];pred=[]
    for rule in RULES:
      for response in (0.,.25,.5):
       for capacity in (.6,.9,1.2):
        c=dict(behavior_rule=rule,autonomy_response=response,review_capacity_per_member_day=capacity)
        idx=len(grid);grid.append(c)
        for w in range(7100,7108):
          pred.append(dict(candidate=idx,world=w,**c,**simulate(dataclasses.replace(BASE,world=w,**c))))
    atomic_csv(out/'candidate_training_patterns.csv',pred)
    observed=[];scores=[];selected=[];heldout=[];null=[]
    for rule in RULES:
     for response in (0.,.25):
      true=dict(behavior_rule=rule,autonomy_response=response,review_capacity_per_member_day=.9)
      case=f'{rule}-response-{response}'
      train=[dict(case=case,world=w,**true,**simulate(dataclasses.replace(BASE,world=w,**true))) for w in range(7000,7008)]
      test=[dict(case=case,world=w,**true,**simulate(dataclasses.replace(BASE,world=w,**true))) for w in range(7008,7012)]
      observed.extend(train+test)
      scales={k:max(FLOORS[i],statistics.stdev(r[k] for r in train)) for i,k in enumerate(PATTERNS)}
      local=[]
      for idx,c in enumerate(grid):
        prediction=[r for r in pred if r['candidate']==idx]
        score=distance(train,prediction,scales)
        local.append(dict(case=case,candidate=idx,distance=score,**c))
      local.sort(key=lambda r:(r['distance'],r['candidate']));best=local[0]
      feasible=[r for r in local if r['distance']<=best['distance']+1]
      scores.extend(local)
      choice=grid[best['candidate']]
      prediction=[dict(case=case,world=w,**choice,**simulate(dataclasses.replace(BASE,world=w,**choice))) for w in range(7108,7112)]
      heldout.extend(prediction)
      selected.append(dict(case=case,true=true,chosen=choice,training_distance=best['distance'],
        heldout_distance=distance(test,prediction,scales),feasible_candidates=[r['candidate'] for r in feasible],
        exact_grid_parameter_recovery=choice==true,behavior_rule_recovered=choice['behavior_rule']==rule,
        scales=scales,observed_train=moments(train),observed_holdout=moments(test),predicted_holdout=moments(prediction)))
      print('synthetic recovery',case,'chosen',choice,'feasible',len(feasible),flush=True)
    # An explicit observational-equivalence diagnostic: output-only observations
    # cannot identify allocation curvature when the response channel is zero.
    for w in range(7200,7208):
      a=simulate(dataclasses.replace(BASE,world=w,regime='linear',autonomy_response=0))
      b=simulate(dataclasses.replace(BASE,world=w,regime='sublinear',autonomy_response=0))
      null.append(dict(world=w,linear_work=a['work_per_member_day'],sublinear_work=b['work_per_member_day'],
        exact_output_equivalence=a['work_per_member_day']==b['work_per_member_day']))
    atomic_csv(out/'synthetic_observation_patterns.csv',observed)
    atomic_csv(out/'training_distances.csv',scores)
    atomic_csv(out/'heldout_prediction_patterns.csv',heldout)
    atomic_csv(out/'output_identification_null.csv',null)
    atomic_json(out/'recovery_results.json',selected)
    metadata.update(status='complete',exit_code=0,candidate_grid=grid,case_count=len(selected),
      source_configuration=BASE.to_dict(),output_sha256=output_hashes(out))
    atomic_json(out/'manifest.json',metadata)

if __name__=='__main__':main()
