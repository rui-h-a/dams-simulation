"""One entry point for the complete versioned DAMS scientific study.

No paid services or remote compute. Existing completed cases are verified and
reused; failed/orphaned cases remain on disk. This is the full study, not a smoke
sample. The standard-library `python3 -m dams_sim smoke` is the bounded quick path.
"""
from __future__ import annotations
import argparse
import html
import json
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dams_sim.cli import doctor
from dams_sim.storage import atomic_json,digest,provenance,source_hash,verify_outputs
from research_tools.study import DRIVER_SHA
from research_tools.inventory import validate_protocol

def full_report(directory):
    claims=json.loads((directory/'generated/analysis/claims.json').read_text())
    primary=claims['primary']
    text=f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>DAMS research reproduction</title>
<style>body{{max-width:1000px;margin:3rem auto;padding:0 1rem;color:#171717;background:white;font:18px Georgia,serif;line-height:1.5}}svg{{width:100%;height:auto}}pre{{overflow:auto;font:14px monospace}}h1,h2{{font-weight:normal}}figure{{margin:2rem 0}}figcaption{{font-size:15px}}</style>
<h1>DAMS contribution-contingent authority: research reproduction</h1>
<p>All data are synthetic model outputs. Independent worlds are the inference units. This report uses the complete fixed design; it makes no claim of field effectiveness, real consensus-client performance or empirically calibrated human behavior.</p>
<p>Prespecified DAMS minus linear work contrast: {primary['mean']:.5f} units/member-day; conditional normal 95% Monte Carlo interval [{primary['low']:.5f}, {primary['high']:.5f}], {primary['n']} independent matched worlds. The substantive design threshold is 0.02; a resolved small positive effect is not a substantive or universal DAMS advantage.</p>
<p>Source: <code>{html.escape(claims['source_sha256'])}</code>. Exact finite-grid parameters recovered in {claims['exact_parameter_recovery']} of {claims['rule_recovery']} generating conditions; behavioral class recovery alone does not imply unique identification.</p>
<p>Full methods, metrics, sample definitions, captions and limitations accompany the generated TeX fragments. Machine-readable calculations are in <code>generated/analysis/</code>; manifests retain provenance and output hashes. Hardware timing belongs to a separately measured host-specific benchmark, not this outcome report.</p>'''
    captions={
      'allocation-effects':'Matched-world effects on generated work, relative to linear credit; bars are conditional normal 95% Monte Carlo intervals.',
      'governance-dynamics':'Daily synthetic work and review/settlement queue stock; pointwise world-level Monte Carlo bands.',
      'formal-authority-distribution':'Within-guild formal-share Lorenz curves averaged across worlds. Equality is not a fairness claim.',
      'confirmation-cdf':'Conditional completed-record delay distribution; unfinished records are excluded from the CDF and reported separately.',
      'attack-cost-effects':'Own-policy attacked minus no-attack work, by declared daily attack cost; eight independent worlds per cell.',
      'response-boundaries':'Behavioral structural alternatives and response-sign boundaries; exploratory matched-world comparisons.',
      'sensitivity-screen':'Finite-difference screening of six independent design dimensions. These are not Sobol indices.',
      'organizational-scale':'Full individual simulation under fixed four-guild organizational scaling; eight independent worlds per cell.',
      'shock-recovery':'True matched shock minus own no-shock work trajectory; descriptive pointwise Monte Carlo bands.',
      'outcome-tradeoffs':'Modeled work and decision-regret means, retaining competing objectives without a welfare conversion.'}
    for name,caption in captions.items():
        svg=directory/'figures/results'/f'{name}.svg'
        if not svg.exists():raise ValueError('missing report figure '+name)
        content=svg.read_text();content=content[content.index('<svg'):]
        text+='<figure>'+content+'<figcaption>'+html.escape(caption)+'</figcaption></figure>'
    text+='<h2>Machine-readable primary claims</h2><pre>'+html.escape(json.dumps(claims,indent=2))+'</pre></html>\n'
    (directory/'report.html').write_text(text)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True,help='immutable-case study directory; resumable on matching sources')
    ap.add_argument('--analysis-dir',type=Path,help='generated LaTeX/vector/CSV/report directory; default OUTPUT/report')
    ap.add_argument('--workers',type=int,default=2,choices=range(1,5),help='bounded scale-stage workers; timing benchmarks run separately')
    args=ap.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    report=(args.analysis_dir or out/'report').resolve();report.mkdir(parents=True,exist_ok=True)
    marker=out/'pipeline_manifest.json';started=time.monotonic()
    metadata={**provenance(),'status':'running','driver_sha256':digest(Path(__file__).read_bytes()),
      'study_driver_sha256':DRIVER_SHA,'inventory_driver_sha256':digest((ROOT/'research_tools/inventory.py').read_bytes()),
      'dependency_lock_sha256':digest((ROOT/'uv.lock').read_bytes()),'doctor':doctor(),'workers':args.workers,'stages':[],
      'scope':'complete fixed synthetic study; hardware benchmarks must be measured separately on the host'}
    if metadata['doctor']['free_disk_bytes']<8_000_000_000:raise RuntimeError('full study requires at least 8 GB free disk; no smaller model is substituted')
    if metadata['doctor']['physical_ram_bytes'] is not None and metadata['doctor']['physical_ram_bytes']<4_000_000_000:raise RuntimeError('full study reference requires at least 4 GB host RAM')
    atomic_json(marker,metadata)
    def call(name,command):
        with (out/f'{name}.log').open('a') as log:
            result=subprocess.run([sys.executable,*command],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError(name+' failed; inspect '+str(out/f'{name}.log'))
        metadata['stages'].append({'stage':name,'status':'passed','log_sha256':digest((out/f'{name}.log').read_bytes())})
        atomic_json(marker,metadata);print(name+' complete',flush=True)
    def existing(stage,manifest_name):
        path=out/stage;mfile=path/manifest_name
        if not mfile.exists():return False
        m=json.loads(mfile.read_text())
        if m['source_sha256']!=source_hash():raise ValueError('existing '+stage+' source differs; use a new output directory')
        if m.get('research_driver_sha256',DRIVER_SHA)!=DRIVER_SHA:raise ValueError('existing stage study driver differs')
        if m['status']!='complete':return False
        verify_outputs(path,m,required=())
        if stage in ('extended','recovery') and m['driver_sha256']!=digest((ROOT/'research_tools'/f'{stage}.py').read_bytes()):raise ValueError(stage+' driver differs')
        metadata['stages'].append({'stage':stage,'status':'cached-complete-output-hashes-verified','manifest_sha256':digest(mfile.read_bytes())})
        atomic_json(marker,metadata);print(stage+' verified cache',flush=True);return True
    try:
        call('tests',['-m','unittest','discover','-s','tests','-v'])
        call('smoke',['-m','dams_sim','smoke','--output',str(out/'smoke')])
        pilot=out/'pilot/protocol.json'
        if pilot.exists():validate_protocol(json.loads(pilot.read_text()))
        else:call('pilot',['research_tools/study.py','pilot','--output',str(out/'pilot')])
        for stage in ('confirmation','mechanisms','stress','sensitivity','scenarios'):
            if existing(stage,'manifest.json' if stage=='mechanisms' else 'ensemble_manifest.json'):continue
            command=['research_tools/study.py',stage,'--output',str(out/stage)]
            if stage=='confirmation':command+=['--protocol',str(pilot)]
            call(stage,command)
        if not existing('extended','ensemble_manifest.json'):call('extended',['research_tools/extended.py','--output',str(out/'extended'),'--workers',str(args.workers)])
        if not existing('recovery','manifest.json'):call('recovery',['research_tools/recovery.py','--output',str(out/'recovery')])
        call('analysis',['research_tools/analyze.py','--runs',str(out),'--thesis-dir',str(report)])
        full_report(report)
        metadata.update(status='complete',exit_code=0,wall_seconds=time.monotonic()-started,
          analysis_generation_manifest_sha256=digest((report/'generated/analysis/generation_manifest.json').read_bytes()),
          self_contained_report_sha256=digest((report/'report.html').read_bytes()))
        atomic_json(marker,metadata);print('complete scientific reproduction: '+str(report/'report.html'),flush=True)
    except BaseException as error:
        metadata.update(status='failed',error_type=type(error).__name__,error=str(error),wall_seconds=time.monotonic()-started)
        atomic_json(marker,metadata);raise

if __name__=='__main__':main()
