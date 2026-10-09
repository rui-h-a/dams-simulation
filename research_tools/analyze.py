"""Generate formal DAMS tables and vector figures from verified executed runs."""
from __future__ import annotations
import argparse
import csv
import dataclasses
import json
import math
import re
from pathlib import Path
import statistics
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dams_sim.storage import verify_outputs,source_hash,digest,atomic_json,atomic_csv,canonical,provenance
from dams_sim.config import Config
from research_tools.study import interval,POLICIES,LABELS,DRIVER_SHA
from research_tools.inventory import verify_inventory,validate_protocol
from research_tools.figure_style import FACTOR_LABELS, POLICY_STYLE, apply_style, style_metadata, dynamics_figure, attack_figure, grid

IMPORTED_MODULE_SHA256 = __import__('hashlib').sha256(Path(__file__).read_bytes()).hexdigest()

SHORT={'equal':'Equal','linear':'Linear','sublinear':'DAMS','hierarchy':'Performance tiers','hierarchy_tenure':'Tenure tiers'}
STYLE=POLICY_STYLE
plt=None

def read_csv(p):
    with p.open(newline='') as f: rows=list(csv.DictReader(f))
    for r in rows:
      for k,v in r.items():
       if v=='':r[k]=None
       else:
        try:r[k]=float(v)
        except (ValueError,TypeError):pass
    return rows

def evidence(path,filename):
    m=json.loads((path/'ensemble_manifest.json').read_text())
    if m['status']!='complete' or m['source_sha256']!=source_hash():raise ValueError('incomplete or changed model ensemble: '+str(path))
    if m.get('research_driver_sha256')!=DRIVER_SHA:raise ValueError('changed study driver')
    verify_outputs(path,m,required=(filename,))
    summaries=[]
    for c in complete_cases(path):
      cm=json.loads((c/'manifest.json').read_text())
      if cm['source_sha256']!=source_hash() or cm.get('research_driver_sha256')!=DRIVER_SHA:raise ValueError('child source/driver differs')
      config=Config.from_dict(cm['config'])
      if cm['config_sha256']!=digest(canonical(config.to_dict())):raise ValueError('child config hash differs')
      verify_outputs(c,cm,required=('summary.json','timeseries.csv','final_state.json'))
      state=json.loads((c/'final_state.json').read_text())
      if canonical(state['config'])!=canonical(cm['config']):raise ValueError('state/manifest configuration differs')
      if state['day']!=config.days or len(state['agents'])!=config.n:raise ValueError('state horizon/population differs')
      summary=json.loads((c/'summary.json').read_text())
      for key in ('world','regime','backend','attack','n','update_interval_days'):
        if summary[key]!=cm['config'][key]:raise ValueError('summary contradicts configuration')
      if summary['days_completed']!=config.days:raise ValueError('incomplete child horizon')
      # The sensitivity stage owns two disjoint world ranges and CSVs.
      if filename=='sensitivity_runs.csv' and config.world>=4100:continue
      if filename=='structural_alternatives.csv' and config.world<4100:continue
      summaries.append((c.name,cm['config'],summary))
    verify_inventory(path,filename,summaries,m)
    rows=read_csv(path/filename)
    keys=sorted(set().union(*(set(s) for _,_,s in summaries)))
    indexed={}
    for name,config,s in summaries:
      indexed.setdefault(tuple(s.get(k) for k in keys),[]).append((name,config))
    consumed=set()
    for row in rows:
      candidates=indexed.get(tuple(row.get(k) for k in keys),[])
      treatment_keys=set(row)&{f.name for f in dataclasses.fields(Config)}
      candidates=[(n,c) for n,c in candidates if all(row[k] is None or row[k]==c[k] for k in treatment_keys)]
      if row.get('attack_budget_setting') is not None:
        candidates=[(n,c) for n,c in candidates if c['attack_budget_hours_per_day']==row['attack_budget_setting']]
      if row.get('protection_assumption') is not None:
        candidates=[(n,c) for n,c in candidates if c['administrative_censorship_exposure']==json.loads(row['protection_assumption'])]
      if row.get('scenario') is not None:
        definitions=json.loads((path/'scenario_definitions.json').read_text())['contexts']
        settings=definitions[row['scenario']]
        candidates=[(n,c) for n,c in candidates if all(c[k]==v for k,v in settings.items())]
      if row.get('context') is not None:
        context=row['context']
        def context_matches(c):
          if context=='fixed_four_guild_scale':return c['n'] in (500,1000,5000,10000) and c['guilds']==4 and c['days']==30 and 8000<=c['world']<8008
          if context=='shock':return c['fault_start_day']==25 and c['fault_stop_day']==30 and c['days']==60 and 8100<=c['world']<8108
          if context=='no_shock':return c['fault_start_day']==c['fault_stop_day']==0 and c['days']==60 and 8100<=c['world']<8108
          return False
        candidates=[(n,c) for n,c in candidates if context_matches(c)]
      remaining=[n for n,c in candidates if n not in consumed]
      if not remaining:raise ValueError('aggregate has missing, altered or repeated child summary')
      consumed.add(remaining[0])
    if consumed!={name for name,_,_ in summaries}:raise ValueError('aggregate omits complete child cases')
    if m.get('driver_sha256') is not None and m['driver_sha256']!=digest((ROOT/'research_tools/extended.py').read_bytes()):raise ValueError('extended driver differs')
    return rows,m

def complete_cases(path):
    result=[]
    for c in sorted(path.glob('case-*')):
      if not (c/'manifest.json').exists():
       if (c/'interruption.json').exists():continue
       raise ValueError('unrecorded orphan case')
      cm=json.loads((c/'manifest.json').read_text())
      if cm['status']=='complete':result.append(c)
    return result

def select(rows,**criteria):return [r for r in rows if all(r.get(k)==v for k,v in criteria.items())]
def avg(rows,key):return statistics.fmean(r[key] for r in rows if r[key] is not None)
def work(r):return r['produced_work_units']/(r['n']*r['days_completed'])
def regret(r,guilds=4):return r['decision_regret_units']/(guilds*r['days_completed'])
def paired(a,b,fun):
    am={r['world']:fun(r) for r in a};bm={r['world']:fun(r) for r in b}
    if len(am)!=len(a) or len(bm)!=len(b):raise ValueError('duplicate world in paired cell')
    if set(am)!=set(bm):raise ValueError('unmatched independent worlds')
    return interval([am[w]-bm[w] for w in sorted(am)])
def fmt(x,d=3):return '--' if x is None else f'{x:.{d}f}'
def ci(x):return f"{fmt(x['mean'],4)} [{fmt(x['low'],4)}, {fmt(x['high'],4)}]"

def savefig(fig,name,figdir):
    fig.savefig(figdir/(name+'.pdf'),bbox_inches='tight',metadata={'CreationDate':None,'ModDate':None})
    fig.savefig(figdir/(name+'.svg'),bbox_inches='tight',metadata={'Date':None})
    plt.close(fig)

def figtex(name,label,caption):
    placement='H' if name in ('governance-dynamics','outcome-tradeoffs') else 'htbp'
    return '\\begin{figure}['+placement+']\n\\centering\n\\includegraphics[width=\\textwidth]{figures/results/'+name+'.pdf}\n\\caption{'+caption+'}\n\\label{fig:'+label+'}\n\\end{figure}\n'

def table(label,caption,head,body,notes):
    cols='l'+'r'*(len(head)-1)
    return '\\begin{table}[tbp]\n\\centering\\small\n\\caption{'+caption+'}\n\\label{tab:'+label+'}\n\\begin{tabular}{'+cols+'}\n\\toprule\n'+' & '.join(head)+r' \\'+'\n\\midrule\n'+'\n'.join(' & '.join(row)+r' \\' for row in body)+'\n\\bottomrule\n\\end{tabular}\n\\par\\smallskip\\begin{minipage}{0.96\\textwidth}\\footnotesize '+notes+'\\end{minipage}\n\\end{table}\n'

def main():
    global plt
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--runs',type=Path,required=True)
    ap.add_argument('--thesis-dir',type=Path,required=True)
    ap.add_argument('--pipeline-in-progress',action='store_true',help='Internal versioned postprocessing after every scientific stage has completed; whole-pipeline completion remains separately gated')
    args=ap.parse_args()
    if (args.runs/'spec_manifest.json').exists():
        version=json.loads((args.runs/'spec_manifest.json').read_text()).get('schema_version')
        if version==3:
            from research_tools.longitudinal_analysis import analyze
            claims=analyze(args.runs,args.thesis_dir,pipeline_in_progress=args.pipeline_in_progress)
            print(json.dumps({'schema_version':3,'primary':claims['primary'],'worlds':claims['confirmation_worlds'],'spec':claims['spec']['name'],'mc_precision_met':claims['mc_precision_met']},indent=2))
            return
        if version!=2:
            raise ValueError('unknown scientific specification schema')
        from research_tools.analysis_v2 import generate
        claims=generate(args.runs,args.thesis_dir,pipeline_in_progress=args.pipeline_in_progress)
        print(json.dumps({'schema_version':2,'primary':claims['primary'],'worlds':claims['worlds'],'spec':claims['spec']['name']},indent=2))
        return
    if args.pipeline_in_progress:
        raise ValueError('--pipeline-in-progress is restricted to spec-v2; historical evidence requires its original strict loader')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as pyplot
    plt=pyplot
    apply_style(plt)
    style_start_metadata=style_metadata()
    analysis_start_hash=digest(Path(__file__).read_bytes())
    inventory_start_hash=digest((ROOT/'research_tools/inventory.py').read_bytes())
    p=args.runs;out=args.thesis_dir;figdir=out/'figures/results';texdir=out/'generated'
    figdir.mkdir(parents=True,exist_ok=True);texdir.mkdir(parents=True,exist_ok=True)
    confirmation,cm=evidence(p/'confirmation','world_summary.csv')
    stress,sm=evidence(p/'stress','stress_summary.csv')
    sense,sem=evidence(p/'sensitivity','sensitivity_runs.csv')
    scenarios,scm=evidence(p/'scenarios','scenario_summary.csv')
    extended,em=evidence(p/'extended','extended_summary.csv')
    rm=json.loads((p/'recovery/manifest.json').read_text());verify_outputs(p/'recovery',rm,required=('recovery_results.json',))
    if rm['status']!='complete' or rm['source_sha256']!=source_hash() or rm['driver_sha256']!=digest((ROOT/'research_tools/recovery.py').read_bytes()):raise ValueError('recovery source/status/driver')
    recovery=json.loads((p/'recovery/recovery_results.json').read_text())
    protocol=json.loads((p/'confirmation/protocol.json').read_text())
    if (p/'confirmation/protocol.json').read_bytes()!=(p/'pilot/protocol.json').read_bytes():raise ValueError('copied protocol differs from pilot origin')
    pilot_rows=read_csv(p/'pilot/pilot_summary.csv')
    pilot_summaries=[]
    for c in complete_cases(p/'pilot'):
      pm=json.loads((c/'manifest.json').read_text());verify_outputs(c,pm,required=('summary.json','final_state.json','timeseries.csv'))
      if pm['source_sha256']!=source_hash() or pm.get('research_driver_sha256')!=DRIVER_SHA:raise ValueError('pilot child source differs')
      pilot_summaries.append((c.name,pm['config'],json.loads((c/'summary.json').read_text())))
    verify_inventory(p/'pilot','pilot_summary.csv',pilot_summaries)
    pilot_keys=set().union(*(set(s) for _,_,s in pilot_summaries))
    raw_pilot=[{k:s.get(k) for k in pilot_keys} for _,_,s in pilot_summaries]
    if any(set(r)!=pilot_keys for r in pilot_rows) or sorted(pilot_rows,key=lambda r:(r['world'],r['regime']))!=sorted(raw_pilot,key=lambda r:(r['world'],r['regime'])):raise ValueError('pilot aggregate differs')
    worlds=validate_protocol(protocol,pilot_rows)
    expected={(w,c,k,b) for w in protocol['confirm_worlds'] for c in protocol['cadences_days'] for k in protocol['policies'] for b in protocol['backends']}
    observed=[(r['world'],r['update_interval_days'],r['regime'],r['backend']) for r in confirmation]
    if len(observed)!=len(set(observed)) or set(observed)!=expected:raise ValueError('confirmation cell coverage or duplicate')
    if cm['protocol_sha256']!=digest((p/'pilot/protocol.json').read_bytes()):raise ValueError('confirmation protocol origin differs')
    if protocol['source_sha256']!=source_hash() or protocol['research_driver_sha256']!=DRIVER_SHA:raise ValueError('protocol source/driver')
    if em['driver_sha256']!=digest((ROOT/'research_tools/extended.py').read_bytes()):raise ValueError('extended driver')
    center=select(confirmation,backend='central',update_interval_days=1)
    linear=select(center,regime='linear');dams=select(center,regime='sublinear')
    primary=paired(dams,linear,work)
    basebody=[]
    for pol in POLICIES:
      rows=select(center,regime=pol)
      basebody.append([SHORT[pol],fmt(statistics.fmean(work(r) for r in rows)),fmt(statistics.fmean(regret(r) for r in rows)),
        fmt(avg(rows,'confirmation_mean_days'),2),fmt(avg(rows,'confirmation_p95_days'),2),fmt(avg(rows,'unfinished_records'),0)])
    tex=table('dynamic-results','Matched-world organizational outcomes under a central record and daily updates.',
      ['Policy','Work/member-day','Regret/guild-day','Mean days','P95 days','Unfinished'],basebody,
      f'Synthetic DAMS-ABM 0.1.0, source \\texttt{{{source_hash()[:12]}}}; {worlds} independent worlds, 120 members, four guilds, 60 days. Work and regret are model units. Delay is conditional on completed confirmations; unfinished records are the horizon stock. Entries are world means, not member-level independent samples.')
    effects=[]
    fig,ax=plt.subplots(figsize=(6.4,3.1));y=list(range(5))
    for i,pol in enumerate(POLICIES):
      v=paired(select(center,regime=pol),linear,work);effects.append(dict(policy=pol,outcome='work_per_member_day',**v))
      color,line,marker=STYLE[pol]
      ax.errorbar(v['mean'],i,xerr=1.96*v['se'] if v['se'] is not None else 0,fmt=marker,color=color,capsize=3.5,markersize=7.5)
    ax.axvline(0,color='.6',linewidth=.6);ax.set_yticks(y,[SHORT[k] for k in POLICIES]);ax.invert_yaxis();ax.spines['left'].set_visible(False);grid(ax,'x')
    ax.set_xlabel('Work difference from linear (units/member-day)');fig.tight_layout();savefig(fig,'allocation-effects',figdir)
    tex+=figtex('allocation-effects','allocation-effects',f'Policy work differences from linear credit in {worlds} matched synthetic worlds. Symbols identify policies; bars are conditional 95\\% normal Monte Carlo intervals. The zero linear contrast is retained. Daily updates, central record, 120 members and 60 days; source \\texttt{{{source_hash()[:12]}}}.')
    # Dynamics: world-level means and interval bands, no smoothing/interpolation.
    traces={}
    for case in complete_cases(p/'confirmation'):
      m=json.loads((case/'manifest.json').read_text());c=m['config']
      if c['backend']=='central' and c['update_interval_days']==1:
       traces[(c['regime'],c['world'])]=read_csv(case/'timeseries.csv')
    dynamics={}
    for pol in POLICIES:
      runs=[v for (k,w),v in traces.items() if k==pol]
      dynamics[pol]={}
      for key,normalizer in (('output_work_units',120),('review_backlog_records',120)):
       days=[r['day'] for r in runs[0]];ivs=[interval([r[j][key]/normalizer for r in runs]) for j in range(len(days))]
       dynamics[pol][key]={'days':days,'mean':[v['mean'] for v in ivs],'low':[v['low'] for v in ivs],'high':[v['high'] for v in ivs]}
    fig=dynamics_figure(plt,dynamics);savefig(fig,'governance-dynamics',figdir)
    tex+=figtex('governance-dynamics','governance-dynamics',f'Daily work and review queues for five policies in {worlds} matched synthetic worlds. Scales are shared within each column; bands are pointwise 95\\% normal Monte Carlo intervals. Shading marks the site-0 shock on days 25--29. Queues exclude appeals. Daily updates, central record, 120 members and 60 days; source \\texttt{{{source_hash()[:12]}}}.')
    # Average within-domain Lorenz curves at world level (no pooled domain rights).
    lorenz=[];fig,ax=plt.subplots(figsize=(6.4,3.1))
    for pol in POLICIES:
      worlds_l=[]
      for case in complete_cases(p/'confirmation'):
       m=json.loads((case/'manifest.json').read_text());c=m['config']
       if c['backend']!='central' or c['update_interval_days']!=1 or c['regime']!=pol:continue
       st=json.loads((case/'final_state.json').read_text());gs=[]
       for guild in range(4):
        a=sorted(r['share'] for r in st['agents'] if r['guild']==guild);total=math.fsum(a)
        gs.append([0]+[math.fsum(a[:j])/total for j in range(1,len(a)+1)])
       worlds_l.append([statistics.fmean(g[j] for g in gs) for j in range(31)])
      xs=[j/30 for j in range(31)];vs=[interval([g[j] for g in worlds_l]) for j in range(31)]
      color,line,marker=STYLE[pol];ax.plot(xs,[v['mean'] for v in vs],color=color,linestyle=line,marker=marker,markevery=(list(POLICIES).index(pol),5),markersize=5.8,label=SHORT[pol])
      lorenz.extend(dict(policy=pol,member_fraction=x,**v) for x,v in zip(xs,vs))
    ax.plot([0,1],[0,1],color='.7',linewidth=.5);ax.set_xlim(0,1);ax.set_ylim(0,1)
    ax.set_xlabel('Member fraction within a guild');ax.set_ylabel('Cumulative formal share');ax.set_xticks([0,.25,.5,.75,1]);ax.set_yticks([0,.25,.5,.75,1]);grid(ax);ax.legend(frameon=False,loc='upper left');fig.tight_layout();savefig(fig,'formal-authority-distribution',figdir)
    tex+=figtex('formal-authority-distribution','formal-authority-distribution',f'Frozen formal-authority Lorenz curves, averaged within four 30-member guilds and then across {worlds} synthetic worlds. The two tier policies have identical sorted-share curves; staggered symbols reveal both without moving their data. The diagonal is equal allocation. Domain rights remain separate; equality does not establish fairness or legitimacy. Daily updates, central record, 60 days; source \\texttt{{{source_hash()[:12]}}}.')
    backendbody=[]
    for backend in ('central','witness','consensus'):
      rows=select(confirmation,regime='sublinear',backend=backend,update_interval_days=1)
      backendbody.append([backend.title(),fmt(statistics.fmean(work(r) for r in rows)),fmt(avg(rows,'confirmation_mean_days'),2),fmt(avg(rows,'unfinished_records'),0),fmt(avg(rows,'review_hours'),1),fmt(avg(rows,'verification_resource_units'),1)])
    tex+=table('ledger-stress','Same DAMS policy under three conditional evidence-process settings.',
      ['Record','Work/member-day','Mean days','Unfinished','Review hours','Verify units'],backendbody,
      f'{worlds} independent synthetic worlds per setting. Default cost multipliers and settlement delays are design assumptions, not measured database/log/consensus performance. Human review hours and extra verification units have different units. No monetary or total-cost equivalence is assumed.')
    # Completed-record CDF at equal model horizon, with world-specific denominator.
    fig,ax=plt.subplots(figsize=(6.4,3.1));cdfrows=[]
    for bi,backend in enumerate(('central','witness','consensus')):
      data=[];unfinished=[]
      for case in complete_cases(p/'confirmation'):
       m=json.loads((case/'manifest.json').read_text());c=m['config']
       if c['regime']=='sublinear' and c['backend']==backend and c['update_interval_days']==1:
        s=json.loads((case/'final_state.json').read_text());data.append(s['confirmation_delays'])
        unfinished.append(sum(len(q) for q in s['queues'].values())+len(s['commits']))
      xs=list(range(0,61));ivs=[interval([sum(v<=x for v in d)/len(d) for d in data]) for x in xs]
      ax.step(xs,[v['mean'] for v in ivs],where='post',color=str(.05+.18*bi),linestyle=('-', '--',':')[bi],label=f'{backend.title()} ({statistics.fmean(unfinished):.0f} unfinished)')
      cdfrows.extend(dict(backend=backend,days=x,**v) for x,v in zip(xs,ivs))
    ax.set_xlabel('Confirmation delay (days)');ax.set_ylabel('Completed-record fraction');ax.set_ylim(0,1.02);ax.set_xticks([0,15,30,45,60]);ax.set_yticks([0,.5,1]);grid(ax);ax.legend(frameon=False,loc='lower right');fig.tight_layout();savefig(fig,'confirmation-cdf',figdir)
    tex+=figtex('confirmation-cdf','confirmation-cdf',f'Completed-record delay distributions for DAMS: means of {worlds} synthetic-world CDFs. Legend counts are rounded world means of unfinished records at day 60. Incomplete records are excluded, so these are conditional distributions rather than all-submission survival estimates. Central, witness and consensus assumptions retain full daily steps; source \\texttt{{{source_hash()[:12]}}}.')
    dynamic_tex=tex;tex=''
    # Equal-budget within-policy attacks, including the zero-cost baseline origin.
    attackrows=[]
    for pol in POLICIES:
      for attack in ('forge','freeride'):
       for b in (2,8):
        a=select(stress,regime=pol,attack=attack,attack_budget_setting=b)
        base=select(stress,regime=pol,attack='none',attack_budget_setting=b)
        v=paired(a,base,work);attackrows.append(dict(policy=pol,attack=attack,budget_hours_day=b,**v))
    fig=attack_figure(plt,attackrows);savefig(fig,'attack-cost-effects',figdir)
    tex+=figtex('attack-cost-effects','attack-cost-effects',f"Work effects of attacks relative to each policy's own unattacked world. Rows identify attacks; columns identify daily budgets of 2 or 8 synthetic hour-equivalents. Symbols are paired means and bars descriptive 95\\% normal Monte Carlo intervals; zero is no attack. Eight pairs per cell, 120 members, 60 days, central record and ten active attack days; source \\texttt{{{source_hash()[:12]}}}.")
    known=('fraudulent_records_accepted','fraudulent_records_detected','honest_records_rejected','duplicate_records_rejected','censored_records')
    threatbody=[];threatrows=[]
    for attack in ('none','forge','duplicate','freeride','censor'):
      rr=select(stress,regime='sublinear',attack=attack,attack_budget_setting=8)
      counts={k:statistics.fmean(r.get(k) or 0 for r in rr) for k in known}
      threatbody.append([attack.title(),*[fmt(counts[k],1) for k in known],f"{avg(rr,'unfinished_records'):.0f}/{avg(rr,'unfinished_appeals'):.1f}"])
      threatrows.append(dict(attack=attack,budget_hours_day=8,**counts,unfinished_records=avg(rr,'unfinished_records'),unfinished_appeals=avg(rr,'unfinished_appeals')))
    tex+=table('threat-diagnostics','DAMS threat diagnostics under equal active-day time budgets.',
      ['Attack',r'\shortstack{Fraud\\committed}',r'\shortstack{Fraud initial\\rejections}',r'\shortstack{Honest review\\rejections}',r'\shortstack{Duplicate\\rejected}','Censored',r'\shortstack{Pending\\claims/appeals}'],threatbody,
      'Eight matched independent worlds, central record, 120 members, 60 days, ten attack days at eight hour-equivalents/day. Entries are mean record counts; zero means no corresponding event, not missing data. Initial fraud rejections can later be appealed and committed; columns are nonexclusive events, not complementary detection rates. Honest review rejections count review events and can include duplicate submissions. Horizon stocks remain separate. Other policies and lower budgets are retained in the raw repository output.')
    counterbody=[]
    for backend in ('central','witness','consensus'):
      equal=select(stress,backend=backend,protection_assumption='[1.0, 1.0, 1.0]')
      protected=select(stress,backend=backend,protection_assumption='[1.0, 0.5, 0.0]')
      outage=select(stress,backend=backend,stress_kind='quorum_unavailable')
      counterbody.append([backend.title(),fmt(statistics.fmean(r.get('censored_records') or 0 for r in equal),1),fmt(statistics.fmean(r.get('censored_records') or 0 for r in protected),1),fmt(avg(outage,'unfinished_records'),0),fmt(avg(outage,'confirmation_mean_days'),2)])
    tex+=table('record-countercases','Equal-exposure and availability countercases for the same DAMS policy.',
      ['Record','Equal exposure','Default exposure','Outage pending','Outage mean days'],counterbody,
      'Eight independent worlds per setting. The two censorship columns count censored records: equal exposure sets every substrate to 1; default sets central/witness/consensus to 1/.5/0. These are imposed assumptions. The outage lasts days 20--34 and only the stylized consensus commit path requires its quorum; central/witness worlds with the same dates are controls. Pending stock is measured at day 60; completed-only delay is in days. No actual distributed-client benchmark is represented.')
    # Structural alternatives directly test a positive, null and adverse response.
    alternatives,_=evidence(p/'sensitivity','structural_alternatives.csv')
    fig,axs=plt.subplots(1,3,figsize=(6.4,3.1),sharey=True);boundary=[]
    for ax,rule in zip(axs,('linear_response','satisficing','reinforcement')):
      vs=[]
      for response in (-.25,0.,.25):
       a=select(alternatives,behavior_rule=rule,autonomy_response=response,regime='sublinear')
       b=select(alternatives,behavior_rule=rule,autonomy_response=response,regime='linear')
       v=paired(a,b,work);vs.append(v);boundary.append(dict(behavior_rule=rule,response=response,**v))
      ax.errorbar([-.25,0,.25],[v['mean'] for v in vs],yerr=[1.96*v['se'] for v in vs],color='.1',marker='o',markersize=6,capsize=3)
      ax.axhline(0,color='.6',linewidth=.6);ax.set_title({'linear_response':'Baseline effort','satisficing':'Satisficing','reinforcement':'Reinforcement'}[rule]);ax.set_xticks([-.25,0,.25],['-0.25','0','0.25']);grid(ax)
    axs[0].set_ylabel('Work difference from linear\n(units/member-day)');fig.supxlabel('Share-response coefficient',fontsize=12,y=.015);fig.subplots_adjust(left=.135,right=.975,bottom=.19,top=.88,wspace=.19);savefig(fig,'response-boundaries',figdir)
    tex+=figtex('response-boundaries','response-boundaries',f'DAMS--linear work contrasts under three effort rules and response coefficients. Eight synthetic world pairs per point; bars are descriptive 95\\% normal Monte Carlo intervals. Lines connect observed settings without fitting. Zero response is a mechanism null; coefficients are unestimated assumptions. 120 members, 60 days; source \\texttt{{{source_hash()[:12]}}}.')
    # Morris-style elementary effects: explicitly not fitted/independent real input distributions.
    ees=read_csv(p/'sensitivity/elementary_effects.csv');factors=list(dict.fromkeys(r['factor'] for r in ees))
    fig,ax=plt.subplots(figsize=(6.4,3.1));screen=[]
    for i,k in enumerate(factors):
      values=[r['effect_per_full_design_range'] for r in ees if r['factor']==k]
      mu=statistics.fmean(abs(x) for x in values);sigma=statistics.stdev(values);screen.append(dict(factor=k,mu_star=mu,sigma=sigma,paths=len(values)))
      ax.scatter(mu,sigma,color='.15',marker='o',s=50)
      # Leaders separate closely spaced factors without moving observations.
      label_positions={'alpha':(.03,.05),'update_interval_days':(.03,.40),
        'review_error_sd':(.33,.17),'review_capacity_per_member_day':(.20,.90),
        'autonomy_response':(.48,.64),'cooperation_strength':(.69,.05)}
      ax.annotate(FACTOR_LABELS[k],(mu,sigma),xytext=label_positions[k],textcoords='axes fraction',
        fontsize=12,ha='left',va='center',arrowprops={'arrowstyle':'-','color':'.45','linewidth':.65},
        bbox={'boxstyle':'square,pad=.15','facecolor':'white','edgecolor':'none'})
    ax.set_xlabel('Mean absolute effect\n(work/member-day)');ax.set_ylabel('Effect dispersion\n(work/member-day)');grid(ax);ax.xaxis.set_major_locator(plt.MaxNLocator(4));ax.yaxis.set_major_locator(plt.MaxNLocator(4));ax.set_xlim(0,max(r['mu_star'] for r in screen)*1.5);ax.set_ylim(0,max(r['sigma'] for r in screen)*1.4);fig.tight_layout();savefig(fig,'sensitivity-screen',figdir)
    tex+=figtex('sensitivity-screen','sensitivity-screen',f'Structural screening across six design factors: eight randomized paths, three paired worlds per step and four levels. Coordinates show mean absolute full-range elementary effect and its path dispersion, in work/member-day. This is not a Sobol index or population variance decomposition. Full input ranges and paths are retained; source \\texttt{{{source_hash()[:12]}}}.')
    stress_tex=tex;tex=''
    # Population effects use full individual worlds, not benchmark extrapolation.
    scales=select(extended,context='fixed_four_guild_scale');fig,axs=plt.subplots(1,2,figsize=(6.4,3.2));scale_effects=[]
    for pol in POLICIES:
      color,line,marker=STYLE[pol];ns=(500,1000,5000,10000);vs=[];cost=[]
      for n in ns:
       r=select(scales,n=n,regime=pol);base=select(scales,n=n,regime='linear');v=paired(r,base,work);vs.append(v)
       cost.append(interval([(x['review_hours']+x['appeal_hours'])/(n*x['days_completed']) for x in r]));scale_effects.append(dict(population_n=n,policy=pol,**v))
      axs[0].errorbar(ns,[v['mean'] for v in vs],yerr=[1.96*v['se'] for v in vs],color=color,linestyle=line,marker=marker,markersize=5.8,capsize=2,label=SHORT[pol])
      axs[1].plot(ns,[v['mean'] for v in cost],color=color,linestyle=line,marker=marker,markersize=5.8)
    for ax in axs:ax.set_xscale('log');ax.set_xlabel('Members');ax.set_xticks([500,1000,5000,10000],['500','1k','5k','10k']);ax.yaxis.set_major_locator(plt.MaxNLocator(4));grid(ax)
    axs[0].set_ylabel('Work difference from linear\n(units/member-day)');axs[0].axhline(0,color='.6',linewidth=.6)
    axs[1].set_ylabel('Review and appeal service\n(hours/member-day)');axs[1].ticklabel_format(axis='y',style='plain',useOffset=False)
    handles,labels=axs[0].get_legend_handles_labels();fig.legend(handles,labels,loc='lower center',ncol=3,frameon=False,bbox_to_anchor=(.5,-.04));fig.tight_layout(rect=(0,.16,1,1));savefig(fig,'organizational-scale',figdir)
    tex+=figtex('organizational-scale','organizational-scale',f'Exploratory comparisons of 500--10,000 full individual members on a logarithmic population axis: eight independent worlds per size, four guilds, two sites and 30 days. Left: paired work contrasts with descriptive 95\\% normal Monte Carlo intervals. Right: consumed review and appeal service in synthetic hour-equivalents, excluding unused reservations. Lines connect executed sizes; guilds grow with population. Source \\texttt{{{source_hash()[:12]}}}.')
    # Shock recovery is compared with the same policy's matched no-shock world.
    shock_traces={}
    for case in complete_cases(p/'extended'):
      m=json.loads((case/'manifest.json').read_text());c=m['config']
      if 8100<=c['world']<8108:
       shock_traces[(c['regime'],c['world'],c['fault_stop_day']>0)]=read_csv(case/'timeseries.csv')
    fig,axs=plt.subplots(len(POLICIES),1,figsize=(6.4,5.4),sharex=True,sharey=True);recovery_times=[]
    for ax,pol in zip(axs,POLICIES):
      differences=[]
      for w in range(8100,8108):
       a=shock_traces[(pol,w,True)];b=shock_traces[(pol,w,False)]
       differences.append([(x['output_work_units']-y['output_work_units'])/120 for x,y in zip(a,b)])
       tau=None
       for t in range(30,58):
        if all(a[j]['output_work_units']>=.99*b[j]['output_work_units'] for j in range(t,t+3)):
         tau=t-30;break
       recovery_times.append(dict(policy=pol,world=w,recovery_days=tau,unrecovered=tau is None))
      vs=[interval([r[j] for r in differences]) for j in range(60)]
      color,line,marker=STYLE[pol];ax.plot(range(60),[v['mean'] for v in vs],color=color,linestyle=line,marker=marker,markevery=10,markersize=5.8)
      ax.fill_between(range(60),[v['low'] for v in vs],[v['high'] for v in vs],color=color,alpha=.05,linewidth=0)
      ax.axvspan(25,30,color='#F0F1F2',zorder=-2);ax.axhline(0,color='.6',linewidth=.6);ax.text(.99,.12,SHORT[pol],transform=ax.transAxes,ha='right',va='bottom',fontsize=12);ax.set_yticks([-.05,0]);grid(ax);ax.spines['bottom'].set_visible(pol==POLICIES[-1]);ax.tick_params(axis='x',bottom=pol==POLICIES[-1])
    axs[-1].set_xlabel('Day');axs[-1].set_xticks([0,15,30,45,60]);fig.supylabel('Work difference from own control (units/member-day)',fontsize=12);fig.tight_layout(rect=(.025,0,1,1),h_pad=1.2);savefig(fig,'shock-recovery',figdir)
    tex+=figtex('shock-recovery','shock-recovery',f"Work differences from each policy's matched no-shock control: eight synthetic world pairs, 120 members and 60 days. Rows share a scale; bands are pointwise 95\\% normal Monte Carlo intervals. Shading marks site 0's 0.7 output multiplier on days 25--29. Zero denotes the matched control, not a forecast. Recovery criteria and unrecovered worlds are reported separately; source \\texttt{{{source_hash()[:12]}}}.")
    scale_tex=tex;tex='' 
    # Recovery reports failed identification as evidence, not a forced success.
    recovered=sum(r['exact_grid_parameter_recovery'] for r in recovery);rules=sum(r['behavior_rule_recovered'] for r in recovery)
    tex+=table('synthetic-recovery','Synthetic parameter and behavioral-model recovery with held-out worlds.',
      ['Generating rule','Response','Chosen rule','Chosen response','Capacity','Holdout loss'],
      [[{'linear_response':'Linear','satisficing':'Satisficing','reinforcement':'Memory'}[r['true']['behavior_rule']],fmt(r['true']['autonomy_response'],2),{'linear_response':'Linear','satisficing':'Satisficing','reinforcement':'Memory'}[r['chosen']['behavior_rule']],fmt(r['chosen']['autonomy_response'],2),fmt(r['chosen']['review_capacity_per_member_day'],1),fmt(r['heldout_distance'],2)] for r in recovery],
      'Six synthetic generating conditions; eight observation-training worlds and eight disjoint candidate-prediction worlds, with four fresh worlds in each held-out set. Distance combines work, regret, queue stock and trust after declared standardization. Selection uses training only. It is a procedure diagnostic with a finite candidate grid, not external calibration or statistical identification in real firms.')
    tex+=f'Exact grid parameters were recovered in {recovered} of {len(recovery)} generating conditions and the effort-rule class in {rules} of {len(recovery)}. Multiple settings within the declared diagnostic tolerance remain in the retained feasible sets; their multiplicity is part of the result. Output-only linear and sublinear worlds with zero share response are exactly observationally equivalent in the dedicated null check.\n'
    recovery_tex=tex;tex=''
    # Descriptive context coverage retained as effects, not fitted industry labels.
    context_rows=[]
    for name in dict.fromkeys(r['scenario'] for r in scenarios):
      a=select(scenarios,scenario=name,regime='sublinear');b=select(scenarios,scenario=name,regime='linear')
      v=paired(a,b,work);context_rows.append(dict(context=name,**v))
    tex+=table('context-results','DAMS--linear work contrasts across mechanistic stress contexts.',
      ['Synthetic context','Work difference [MC interval]'],[[r['context'].replace('_',' '),ci(r)] for r in context_rows],
      'Eight paired worlds per context, 120 members and 60 days. Intervals are descriptive 95\\% normal Monte Carlo intervals; they do not include parameter or model uncertainty. Contexts change information, interdependence, site shocks, observation error, review resources, record assumptions, cadence or response. They are not calibrated industry, legal, demographic or sustainability cases.')
    context_tex=tex;tex=''
    # Outcome means: aligned policy rows and record facets preserve both units.
    fig,axs=plt.subplots(3,2,figsize=(6.4,5.5),sharex='col',sharey=True);tradeoffs=[]
    for i,pol in enumerate(POLICIES):
      color,line,marker=STYLE[pol]
      for row,backend in enumerate(('central','witness','consensus')):
       r=select(confirmation,regime=pol,backend=backend,update_interval_days=1)
       x=statistics.fmean(work(a) for a in r);y=statistics.fmean(regret(a) for a in r)
       axs[row,0].scatter(x,i,color=color,marker=marker,s=56)
       axs[row,1].scatter(y,i,color=color,marker=marker,s=56)
       tradeoffs.append(dict(policy=pol,backend=backend,work_per_member_day=x,regret_per_guild_day=y))
    for row,backend in enumerate(('central','witness','consensus')):
      for col in range(2):
       ax=axs[row,col];ax.set_yticks(range(len(POLICIES)),[SHORT[p] for p in POLICIES]);ax.set_ylim(len(POLICIES)-.5,-.5);grid(ax,'x');ax.spines['left'].set_visible(False);ax.set_title(backend.title() if col==0 else '',loc='left');ax.margins(x=.15);ax.xaxis.set_major_locator(plt.MaxNLocator(4))
    axs[-1,0].set_xlabel('Work (units/member-day)');axs[-1,1].set_xlabel('Regret (units/guild-day)');fig.tight_layout(h_pad=1.5,w_pad=2.0);savefig(fig,'outcome-tradeoffs',figdir)
    tex+=figtex('outcome-tradeoffs','outcome-tradeoffs',f'Policy outcome means under three evidence settings, with shared scales within each column. Work is higher-preferred; regret is lower-preferred. These descriptive means are separate from paired effects and their uncertainty. {worlds} independent synthetic worlds, daily updates, 120 members and 60 days; source \\texttt{{{source_hash()[:12]}}}.')
    # Emit machine-readable calculations used by the text and figures.
    analysis=out/'generated/analysis';analysis.mkdir(parents=True,exist_ok=True)
    for name,rows in [('allocation_effects',effects),('cdf',cdfrows),('attack_effects',attackrows),('boundary_effects',boundary),('sensitivity_screen',screen),('scale_effects',scale_effects),('context_effects',context_rows),('tradeoffs',tradeoffs),('lorenz',lorenz),('threat_diagnostics',threatrows),('shock_recovery_times',recovery_times)]:atomic_csv(analysis/(name+'.csv'),rows)
    atomic_json(analysis/'claims.json',{'primary':primary,'worlds':worlds,'source_sha256':source_hash(),'study_driver_sha256':DRIVER_SHA,
      'analysis_driver_sha256':digest(Path(__file__).read_bytes()),'exact_parameter_recovery':recovered,'rule_recovery':rules,'recovery_cases':len(recovery),
      'noncomplete_attempts':{k:m.get('noncomplete_attempts',0) for k,m in [('confirmation',cm),('stress',sm),('sensitivity',sem),('scenarios',scm),('extended',em)]}})
    tradeoff_tex=tex
    parts={'dynamic':dynamic_tex+tradeoff_tex,'stress':stress_tex+context_tex,'scale':scale_tex,'recovery':recovery_tex}
    for key,part in parts.items():(texdir/f'revision_{key}.tex').write_text(part)
    float_paths=[]
    for block in re.finditer(r'(\\begin\{(figure|table)\}.*?\\end\{\2\})', ''.join(parts.values()), re.S):
        label=re.search(r'\\label\{(?:fig|tab):([^}]+)\}',block.group(1))
        if label is None:raise ValueError('generated float has no stable label')
        path=texdir/f'revision_{block.group(2)}_{label.group(1)}.tex'
        path.write_text(block.group(1)+'\n');float_paths.append(path)
    (texdir/'revision_results.tex').write_text(''.join(parts.values()))
    macros={'StudyPrimaryMean':fmt(primary['mean'],5),'StudyPrimaryLow':fmt(primary['low'],5),'StudyPrimaryHigh':fmt(primary['high'],5),'StudyWorlds':str(worlds),'StudyExactRecovery':str(recovered),'StudyRuleRecovery':str(rules),'StudyRecoveryCases':str(len(recovery))}
    (texdir/'revision_macros.tex').write_text(''.join('\\newcommand{\\'+k+'}{'+v+'}\n' for k,v in macros.items()))
    if analysis_start_hash!=digest(Path(__file__).read_bytes()) or inventory_start_hash!=digest((ROOT/'research_tools/inventory.py').read_bytes()):raise RuntimeError('analysis source changed while generating; outputs not valid for final use')
    if style_start_metadata!=style_metadata():raise RuntimeError('figure style changed while generating')
    atomic_json(analysis/'generation_manifest.json',{**provenance(),'status':'complete','source_sha256':source_hash(),
      'analysis_driver_sha256':digest(Path(__file__).read_bytes()),'inventory_driver_sha256':digest((ROOT/'research_tools/inventory.py').read_bytes()),'inputs':{str(x.relative_to(p)):digest(x.read_bytes()) for x in [*p.glob('*/ensemble_manifest.json'),p/'recovery/manifest.json',p/'pilot/protocol.json']},
      'outputs':{str(x.relative_to(out)):digest(x.read_bytes()) for x in [texdir/'revision_results.tex',texdir/'revision_macros.tex',*float_paths,*[texdir/f'revision_{k}.tex' for k in parts],analysis/'claims.json',*sorted(analysis.glob('*.csv')),*sorted(figdir.glob('*.pdf')),*sorted(figdir.glob('*.svg'))]},
      'recovery_driver_sha256':rm['driver_sha256'],'extended_driver_sha256':em['driver_sha256'],
      'dependency_lock_sha256':digest((ROOT/'uv.lock').read_bytes()),
      'figure_style':style_metadata(),'graphics_library':matplotlib.__version__,'font':'bundled Computer Modern cmr10 and mathtext cm; embedded TrueType PDF; same family as LaTeX Latin Modern'})
    print(json.dumps({'primary':primary,'recovered':recovered,'rules':rules,'figures':len(list(figdir.glob('*.pdf')))},indent=2))

if __name__=='__main__':main()
