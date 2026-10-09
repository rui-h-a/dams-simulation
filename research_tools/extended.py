"""Bounded scale and recovery-of-output contrasts for DAMS; descriptive only."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import dataclasses
import multiprocessing
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dams_sim.config import Config
from dams_sim.storage import atomic_json,atomic_csv,digest,provenance
from research_tools.study import BASE,POLICIES,saved_case,seal_ensemble

def execute(task):
    path,config,context=task
    row=saved_case(path,config);row['context']=context
    return row

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--workers',type=int,default=2);args=ap.parse_args()
    if not 1<=args.workers<=4:ap.error('workers must be in 1..4')
    root=args.output;root.mkdir(parents=True,exist_ok=True)
    physical=Path(shutil.which('python3')).resolve();multiprocessing.set_executable(str(physical))
    tasks=[]
    for n in (500,1000,5000,10000):
      for w in range(8000,8008):
       for policy in POLICIES:
        p=dataclasses.replace(BASE,n=n,days=30,world=w,regime=policy,guilds=4,
          max_wall_seconds=180,max_output_mb=120,max_events=400000,max_rss_mb=2048)
        tasks.append((root,p,'fixed_four_guild_scale'))
    # A true paired no-shock world supplies a recovery comparator; all other
    # exogenous keys and policy settings match the shocked world.
    for w in range(8100,8108):
     for policy in POLICIES:
      for fault in (False,True):
       p=dataclasses.replace(BASE,world=w,regime=policy,
          fault_start_day=25 if fault else 0,fault_stop_day=30 if fault else 0)
       tasks.append((root,p,'shock' if fault else 'no_shock'))
    rows=[];meta={**provenance(),'driver_sha256':digest(Path(__file__).read_bytes()),
      'status':'running','workers':args.workers,'child_interpreter':str(physical),
      'design':'eight independent worlds per scale and eight paired shock worlds; exploratory, not primary confirmation',
      'scale':'same four guilds, two sites, team size five, full individual reference; not aggregate agents',
      'max_world_rss_mb':2048,'max_world_seconds':180,'expected_cases':len(tasks)}
    atomic_json(root/'extended_protocol.json',meta)
    try:
      with ProcessPoolExecutor(max_workers=args.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
       pending={pool.submit(execute,t):t for t in tasks}
       for i,future in enumerate(as_completed(pending),1):
        rows.append(future.result())
        atomic_csv(root/'extended_summary.csv',sorted(rows,key=lambda r:(r['context'],r['n'],r['world'],r['regime'])))
        if i%10==0:print('extended complete',i,'/',len(tasks),flush=True)
    except Exception as e:
      seal_ensemble(root,{**meta,'status':'failed','error':repr(e),'completed_cases':len(rows)})
      raise
    seal_ensemble(root,{**meta,'status':'complete','policy_runs':len(rows)})

if __name__=='__main__':main()
