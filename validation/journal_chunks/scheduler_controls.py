"""Actual spawned-worker storage routing and same-case resume controls."""
from __future__ import annotations
import dataclasses
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2];SOURCE=ROOT
sys.path.insert(0,str(SOURCE))
from dams_sim.config import Config
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.cli import run_world
from dams_sim.runtime import RuntimeLimits
from dams_sim.scheduler import Scheduler
from dams_sim.longitudinal_model import verify_snapshot
from dams_sim.longitudinal_pipeline import driver_hash
from dams_sim.spec import case_key
from dams_sim.storage import canonical,digest,file_digest,source_hash

def main():
    out=ROOT/'runs'/'journal-chunks'/'scheduler-controls';out.parent.mkdir(parents=True,exist_ok=True);out.mkdir(exist_ok=False)
    source_before=source_hash();driver=driver_hash()
    p=Config(n=8,days=4,guilds=2,team_size=2,sites=1,trace_every_days=1,max_output_mb=20,max_rss_mb=128,
        longitudinal=LongitudinalConfig(adoption_mode='never',annual_exit_probability=0.,annual_vacancy_fill_probability=0.))
    limits_value={'workers_auto':False,'max_workers':1,'cpu_budget':1,'memory_budget_bytes':512*2**20,
        'deadline_utc':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),'journal_chunk_bytes':1024,
        'max_events':100000,'max_output_bytes':20_000_000,'batch_max_output_bytes':100_000_000,
        'checkpoint_interval_days':2,'checkpoint_interval_seconds':300.,'max_retries':1}
    limits_path=out/'runtime-limits.json';limits_path.write_bytes(canonical(limits_value)+b'\n')
    limits=RuntimeLimits.from_dict(json.loads(limits_path.read_bytes()))
    attempt=out/'cases'/case_key(p)/'attempt-000';attempt.mkdir(parents=True)
    partial=run_world(p,attempt,checkpoint_day=2,checkpoint_interval_days=2,checkpoint_interval_seconds=300.,journal_chunk_bytes=1024,
        metadata_extra={'pipeline_driver_sha256':driver,'scientific_case_id':case_key(p),'execution_provenance':{}})
    assert partial['status']=='checkpointed'
    original_pins={q.name:file_digest(q) for q in attempt.iterdir() if q.is_file()}
    scheduler=Scheduler(out,limits,driver)
    result=scheduler.run([p])[0]
    assert result['attempt'].endswith('attempt-001') and scheduler.owned_workers_absent
    finished=out/result['attempt'];manifest=json.loads((finished/'manifest.json').read_bytes())
    state,_=verify_snapshot(finished/'final_state.json',expected_config=p)
    assert state['ledger']['schema_version']==2 and state['state']['day']==p.days
    assert manifest['restart_origin']['parent_attempt']=='attempt-000'
    assert original_pins=={q.name:file_digest(q) for q in attempt.iterdir() if q.is_file()}
    parent_cp=finished/json.loads((finished/'checkpoint-index.json').read_bytes())['snapshots'][0]['file']
    child=dataclasses.replace(p,days=8,regime='equal',longitudinal=dataclasses.replace(p.longitudinal,adoption_mode='fixed',adoption_day=4))
    origin={'parent_checkpoint':str(parent_cp),'parent_case_id':case_key(p),'parent_source_sha256':source_before,
        'parent_config_sha256':digest(canonical(p.to_dict())),'parent_day':p.days,'parent_state_semantic_sha256':state['state_semantic_sha256']}
    branched=scheduler.run([child],{case_key(child):origin})[0]
    child_state,_=verify_snapshot(out/branched['attempt']/'final_state.json',expected_config=child)
    assert child_state['ledger']['schema_version']==2
    assert child_state['state']['branch_origin']=={k:v for k,v in origin.items() if k!='parent_checkpoint'}
    assert canonical(child_state['state']['history'][:p.days])==canonical(state['state']['history'])
    assert source_before==source_hash() and driver==driver_hash() and scheduler.owned_workers_absent
    receipt={'status':'PASS','source_sha256':source_before,'pipeline_driver_sha256':driver,'runtime_file_sha256':file_digest(limits_path),
        'actual_spawned_worker_resume':True,'same_case_added_independent_samples':0,'original_attempt_bytes_preserved':True,
        'schema2_parent_fork_run_world_complete':True,'parent_days':p.days,'child_days':child.days,'engineering_controls_only':True,
        'large_population_linux_or_full_scientific_pipeline_admission':False,'GCP_called':False}
    (out/'result.json').write_bytes(canonical(receipt)+b'\n');print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
