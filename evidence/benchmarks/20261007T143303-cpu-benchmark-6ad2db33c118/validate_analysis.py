"""Independent arithmetic/output verification; needs optional NumPy/SciPy."""
from pathlib import Path
import csv,hashlib,json,sys
import numpy as np
import scipy
from scipy.optimize import nnls
batch=Path(__file__).resolve().parent
root=batch.parents[2]
sys.path.insert(0,str(root))
from dams_sim.storage import verify_outputs,source_hash,canonical,digest
from research_tools.performance_figures import load,fit
scales,parallel,plan=load(batch)
assert source_hash()==plan['source_sha256']
rows=list(csv.DictReader((batch/'measurements.csv').open()))
assert len(rows)==102 and all(r['status']=='complete' for r in rows)
verified=0
for row in rows:
    run=root/row['run_path']
    manifest=json.loads((run/'manifest.json').read_text())
    assert manifest['status']=='complete' and manifest['source_sha256']==plan['source_sha256']
    assert digest(canonical(manifest['config']))==row['config_sha256']
    verify_outputs(run,manifest,required=('summary.json','final_state.json','timeseries.csv','report.svg'))
    for field,file in [('summary_sha256','summary.json'),('final_state_sha256','final_state.json')]:
        assert hashlib.sha256((run/file).read_bytes()).hexdigest()==row[field]
    verified+=1
cold=[s for s in scales if s['temperature']=='fresh_interpreter']
points=[{'n':int(s['n']),'days':int(s['days']),'rss_mb':s['complete_child_sampled_peak_rss_mb_median'],'output_mb':s['output_bytes_median']/1e6} for s in cold]
a=np.array([[1,p['n']/100000,p['n']*p['days']/300000] for p in points])
errors={}
for target in ('rss_mb','output_mb'):
    ours,_=fit(points,target)
    oracle,_=nnls(a,np.array([p[target] for p in points]))
    error=float(np.max(np.abs(np.array(ours)-oracle)))
    assert np.allclose(ours,oracle,rtol=1e-10,atol=1e-9)
    errors[target]=error
failure=json.loads((batch/'partial-n1000000-t1/watchdog.json').read_text())
assert failure['status']=='terminated' and failure['resource_stop']=='external_RSS_watchdog'
assert not (batch/'partial-n1000000-t1/child.json').exists()
refusal=json.loads((batch/'preflight-10000000.json').read_text())
assert refusal['status']=='preflight_refused' and refusal['actual_agent_allocation'] is False
record={'status':'passed','complete_raw_worlds_output_integrity_verified':verified,'derived_csv_checked_against_raw_json':True,'source_sha256':plan['source_sha256'],'measurement_tool_sha256':plan['benchmark_tool_sha256'],'measurement_tool_snapshot_matches_plan':hashlib.sha256((batch/'measurement-tool.py').read_bytes()).hexdigest()==plan['benchmark_tool_sha256'],'postprocessor_sha256':hashlib.sha256((root/'research_tools/performance_figures.py').read_bytes()).hexdigest(),'summarizer_sha256':hashlib.sha256((root/'research_tools/benchmark.py').read_bytes()).hexdigest(),'scipy_oracle_version':scipy.__version__,'numpy_version':np.__version__,'maximum_absolute_nnls_coefficient_errors':errors,'external_hardware_predictive_validation':False,'failure_and_refusal_retained':True}
assert record['measurement_tool_snapshot_matches_plan']
(batch/'analysis-validation.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
print(json.dumps(record,indent=2))
