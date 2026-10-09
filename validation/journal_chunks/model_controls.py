"""Bounded engineering controls; these are not scientific experiment samples."""
from __future__ import annotations
import ast
import dataclasses
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT
sys.path.insert(0,str(SOURCE))
from dams_sim.config import Config
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.model import Model
from dams_sim.runtime import RuntimeLimits
from dams_sim.cli import run_world
from dams_sim.longitudinal_model import verify_snapshot
from dams_sim.longitudinal_storage import TABLE_COLUMNS
from dams_sim.storage import atomic_csv,canonical,file_digest,source_hash
from research_tools.validate_longitudinal import verify_daily_evidence,iter_journal_rows
from research_tools.longitudinal_benchmark import ledger_observation

OUT=ROOT/'runs'/'journal-chunks'/'model-controls'
OUT.parent.mkdir(parents=True,exist_ok=True)
OUT.mkdir(exist_ok=False)
RECORDS=[]
SOURCE_BEFORE={str(p.relative_to(SOURCE)):file_digest(p) for p in SOURCE.rglob('*.py')}

def checked(name,call):
    start=time.monotonic()
    value=call()
    RECORDS.append({'name':name,'status':'PASS','wall_seconds':time.monotonic()-start,'result':value})

def refused(call):
    try:call()
    except ValueError:return
    raise AssertionError('invalid option was accepted')

def equal(a,b):
    assert canonical(a._long.header())==canonical(b._long.header())
    assert [tuple(float(v).hex() for v in dataclasses.asdict(p).values() if type(v) is float) for p in a.agents]==[tuple(float(v).hex() for v in dataclasses.asdict(p).values() if type(v) is float) for p in b.agents]
    for table in TABLE_COLUMNS:
        assert list(a.ledger.rows(table))==list(b.ledger.rows(table)),table
    assert list(a.ledger.db.execute('SELECT name,seq FROM sqlite_sequence ORDER BY name'))==list(b.ledger.db.execute('SELECT name,seq FROM sqlite_sequence ORDER BY name'))
    assert a._long.semantic_digest()==b._long.semantic_digest()

def config(**changes):
    l=LongitudinalConfig(calendar_start='2020-02-24',initial_age_min_years=30.,initial_age_max_years=30.,annual_exit_probability=.4,
        max_active_members=64,max_people_ever=256,adoption_day=3,transition_days=4)
    return dataclasses.replace(Config(n=24,days=16,guilds=4,team_size=3,sites=2,trace_every_days=1,longitudinal=l,max_output_mb=100.,max_wall_seconds=300.),**changes).validate()

def paired(name,p):
    a=Model(p,storage_dir=OUT/(name+'-flat'))
    b=Model(p,storage_dir=OUT/(name+'-chunk'),journal_chunk_bytes=1024)
    try:
        equal(a,b)
        for day in range(p.days):
            a.step();b.step();equal(a,b)
            if day==7:
                cp_a=OUT/(name+'-flat')/'checkpoint.json';cp_b=OUT/(name+'-chunk')/'checkpoint.json'
                a.write_checkpoint(cp_a);b.write_checkpoint(cp_b)
                ea,_=verify_snapshot(cp_a,expected_config=p);eb,_=verify_snapshot(cp_b,expected_config=p)
                assert ea['ledger']['schema_version']==1 and eb['ledger']['schema_version']==2
                assert ea['state_semantic_sha256']==eb['state_semantic_sha256']
                assert ea['ledger']['row_counts']==eb['ledger']['row_counts']
                pins={str(q):file_digest(q) for cp,e in ((cp_a,ea),(cp_b,eb)) for q in (cp,cp.parent/e['ledger']['file'])}
                ra=Model.restore_checkpoint(cp_a,storage_dir=OUT/(name+'-flat-restored'))
                rb=Model.restore_checkpoint(cp_b,storage_dir=OUT/(name+'-chunk-restored'))
                a.ledger.close();b.ledger.close();a,b=ra,rb
                assert b.ledger.journal_chunk_bytes==1024
                equal(a,b)
                assert pins=={q:file_digest(Path(q)) for q in pins}
        return {'days':p.days,'state_sha256':a._long.semantic_digest(),'logical_row_counts':a.ledger.logical_row_counts(),
                'flat_working_bytes':a.ledger.path.stat().st_size,'chunk_working_bytes':b.ledger.path.stat().st_size}
    finally:a.ledger.close();b.ledger.close()

def forks():
    p=config(days=6,longitudinal=dataclasses.replace(config().longitudinal,adoption_mode='never'))
    a=Model(p,storage_dir=OUT/'fork-parent-flat');b=Model(p,storage_dir=OUT/'fork-parent-chunk',journal_chunk_bytes=1024)
    a.run();b.run();equal(a,b)
    child=dataclasses.replace(p,days=16,regime='equal',longitudinal=dataclasses.replace(p.longitudinal,adoption_mode='fixed',adoption_day=6))
    ca=a.fork(child,storage_dir=OUT/'fork-child-flat');cb=b.fork(child,storage_dir=OUT/'fork-child-chunk')
    try:
        assert cb.ledger.journal_chunk_bytes==1024
        assert list(a.ledger.rows('journal'))==list(cb.ledger.rows('journal'))
        equal(ca,cb)
        while ca.day<child.days:ca.step();cb.step();equal(ca,cb)
        assert list(a.ledger.rows('journal'))==list(b.ledger.rows('journal'))
        return {'shared_parent_day':p.days,'child_day':ca.day,'state_sha256':ca._long.semantic_digest()}
    finally:
        for m in (a,b,ca,cb):m.ledger.close()

def raw_readers():
    p=config(days=8)
    values=[]
    for name,target in (('flat',None),('chunk',1024)):
        path=OUT/('raw-'+name);path.mkdir()
        manifest=run_world(p,path,checkpoint_interval_days=4,checkpoint_interval_seconds=300.,journal_chunk_bytes=target)
        assert manifest['status']=='complete'
        envelope,database=verify_snapshot(path/'final_state.json',expected_config=p)
        verify_daily_evidence(path/'timeseries.csv',p,envelope['state'],database)
        with sqlite3.connect(database.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as connection:
            rows=list(iter_journal_rows(connection,include_sequence=True))
        values.append((envelope,rows,path))
    assert values[0][0]['state_semantic_sha256']==values[1][0]['state_semantic_sha256']
    assert values[0][1]==values[1][1]
    assert (values[0][2]/'timeseries.csv').read_bytes()==(values[1][2]/'timeseries.csv').read_bytes()
    observation=ledger_observation(values[1][2])
    assert observation['journal_rows'] is None and observation['last_committed_day_end'] is None
    assert observation['physical_journal_tail_rows']==0 and observation['ledger_physical_schema_version']==2
    return {'rows':len(values[0][1]),'same_csv_sha256':file_digest(values[0][2]/'timeseries.csv'),
            'state_sha256':values[0][0]['state_semantic_sha256'],'compressed_telemetry':observation}

def option_controls():
    for value in (True,0,127,4*2**20+1,'1024'):
        refused(lambda:RuntimeLimits.from_dict({'journal_chunk_bytes':value}))
    for value in (None,128,4*2**20):
        assert RuntimeLimits.from_dict({'journal_chunk_bytes':value}).journal_chunk_bytes==value
    refused(lambda:Model(Config(),journal_chunk_bytes=1024))
    refused(lambda:Model(config(),storage_dir=OUT/'native-refusal',page_options=object(),journal_chunk_bytes=1024))
    p=config(days=4);m=Model(p,storage_dir=OUT/'mismatch-model')
    destination=OUT/'mismatch-run';destination.mkdir()
    try:
        refused(lambda:run_world(p,destination,restored=m,journal_chunk_bytes=1024))
        assert not (destination/'manifest.json').exists()
    finally:m.ledger.close()
    return {'invalid_values_refused':5,'valid_values':3,'legacy_native_restore_mismatch_refused':3}

started=datetime.now(timezone.utc).isoformat()
try:
    checked('paired-baseline-daily-checkpoint-restore',lambda:paired('baseline',config()))
    checked('paired-duplicate-backlog',lambda:paired('duplicate',config(attack='duplicate',attack_start_day=0,attack_stop_day=16,review_capacity_per_member_day=.2)))
    checked('paired-lifecycle-merger-shock',lambda:paired('lifecycle',config(longitudinal=dataclasses.replace(config().longitudinal,
        exit_schedule=((5,0,'exit'),),guild_mergers=((6,0,1),),demand_schedule=((8,.5),)))))
    checked('exact-shared-history-fork',forks)
    checked('run-world-full-raw-reader-csv-and-telemetry',raw_readers)
    checked('runtime-default-option-and-prewrite-refusal',option_controls)
    assert SOURCE_BEFORE=={str(p.relative_to(SOURCE)):file_digest(p) for p in SOURCE.rglob('*.py')}
    result={'status':'PASS','started_utc':started,'finished_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':source_hash(),
            'python':sys.version,'controls':RECORDS,'engineering_controls_only':True,'accepted_scientific_worlds_added':0,
            'large_population_or_linux_admission':False,'source_files_sha256':SOURCE_BEFORE}
except BaseException as error:
    result={'status':'FAIL','started_utc':started,'finished_utc':datetime.now(timezone.utc).isoformat(),'controls':RECORDS,
            'error_type':type(error).__name__,'error':str(error),'engineering_controls_only':True}
    (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    raise
(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='source_files_sha256'},indent=2))
