"""Publication analysis for checked spec-v2 runs; historical evidence stays separate."""
from __future__ import annotations

import csv
import dataclasses
import json
import math
from pathlib import Path
import re
import statistics

from dams_sim.config import Config
from dams_sim.design import POLICIES, FACTORS, stage_tasks
from dams_sim.pipeline import driver_hash
from dams_sim.spec import RESOURCE_FIELDS, case_key, resolve_spec
from dams_sim.storage import atomic_csv, atomic_json, canonical, digest, file_digest, provenance, source_hash
from research_tools.figure_style import FACTOR_LABELS, POLICY_NAMES, POLICY_STYLE, apply_style, attack_figure, dynamics_figure, style_metadata
from research_tools.study import interval

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = ('schema_version', 'source_sha256', 'spec_sha256', 'pipeline_driver_sha256')


class _JSONStream:
    """Bounded-memory structural reader for the producer's complete JSON state.

    Arrays are consumed item by item; raw decoding is limited to small individual
    records. This does not load a multi-million-person final state into memory.
    """
    def __init__(self, stream):
        self.stream, self.buffer, self.pos, self.eof = stream, '', 0, False
        self.decoder = json.JSONDecoder()

    def fill(self):
        self.buffer = self.buffer[self.pos:]
        self.pos = 0
        chunk = self.stream.read(65536)
        self.buffer += chunk
        self.eof = not chunk

    def peek(self):
        while self.pos == len(self.buffer) and not self.eof:
            self.fill()
        return self.buffer[self.pos] if self.pos < len(self.buffer) else ''

    def take(self, expected=None):
        c = self.peek()
        if not c or expected is not None and c != expected:
            raise ValueError('invalid or truncated raw state JSON')
        self.pos += 1
        return c

    def white(self):
        while self.peek() and self.peek().isspace(): self.pos += 1

    def raw(self):
        self.white()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.pos)
                # A numeric token ending at a chunk boundary may be incomplete.
                if end == len(self.buffer) and not self.eof:
                    self.fill(); continue
                self.pos = end
                return value
            except json.JSONDecodeError:
                if self.eof: raise ValueError('invalid or truncated raw state value')
                if len(self.buffer)-self.pos > 4_000_000:
                    raise ValueError('unexpected oversized individual state record')
                self.fill()

    def skip(self):
        self.white()
        if self.peek() not in ('[', '{'):
            self.raw(); return
        opening = self.take(); stack = [']' if opening == '[' else '}']
        quoted = False
        structural = re.compile(r'[\[\]{}"]')
        string_special = re.compile(r'["\\]')
        while stack:
            if not self.peek(): raise ValueError('truncated raw state container')
            match = (string_special if quoted else structural).search(self.buffer,self.pos)
            if match is None:
                self.pos=len(self.buffer)
                continue
            self.pos=match.end(); c=match.group()
            if quoted:
                if c == '\\': self.take()  # one escaped character, including chunk boundaries
                elif c == '"': quoted = False
            elif c == '"': quoted = True
            elif c in '[{': stack.append(']' if c == '[' else '}')
            elif c in ']}':
                if c != stack.pop(): raise ValueError('mismatched raw state delimiters')

    def array(self):
        self.white(); self.take('['); self.white()
        if self.peek() == ']': self.take(']'); return
        while True:
            yield self.raw()
            self.white(); c = self.take()
            if c == ']': return
            if c != ',': raise ValueError('invalid raw state array separator')


def state_identity(path, config):
    with path.open() as stream:
        reader = _JSONStream(stream); reader.white(); reader.take('{')
        seen, count, agent_ids = set(), 0, True
        while True:
            reader.white()
            if reader.peek() == '}': reader.take('}'); break
            key = reader.raw()
            if key in seen: raise ValueError('duplicate raw state field')
            seen.add(key); reader.white(); reader.take(':')
            if key == 'agents':
                for a in reader.array():
                    agent_ids = agent_ids and a.get('id') == count
                    count += 1
            elif key == 'config':
                if canonical(reader.raw()) != canonical(config.to_dict()):
                    raise ValueError('state configuration differs from locked case')
            elif key == 'day':
                if reader.raw() != config.days: raise ValueError('incomplete state horizon')
            else: reader.skip()
            reader.white()
            if reader.peek() == ',': reader.take(',')
            elif reader.peek() != '}': raise ValueError('invalid raw state object separator')
        reader.white()
        if reader.peek() or not {'agents','config','day'}.issubset(seen):
            raise ValueError('missing state identity or trailing raw state content')
        if count != config.n or not agent_ids: raise ValueError('state population or agent IDs differ')


class CheckedStudy:
    """Independent manifest/inventory/raw-summary/aggregate audit before plotting."""
    def __init__(self, root, *, pipeline_in_progress=False):
        self.root = Path(root).resolve()
        self.inputs, self.case_cache, self.stages = {}, {}, {}
        self.spec_manifest = self.load_json(self.root/'spec_manifest.json')
        self.pipeline = self.load_json(self.root/'pipeline_manifest.json')
        self.protocol = self.load_json(self.root/'protocol.json')
        self.identity = {k:self.spec_manifest[k] for k in IDENTITY}
        if self.identity['schema_version'] != 2: raise ValueError('unsupported scientific schema')
        if self.identity['source_sha256'] != source_hash() or self.identity['pipeline_driver_sha256'] != driver_hash():
            raise ValueError('spec-v2 source/driver differs from current frozen implementation')
        self.spec = resolve_spec(self.spec_manifest['spec']['name'], self.spec_manifest['spec']['n'])
        if canonical(self.spec.to_dict()) != canonical(self.spec_manifest['spec']) or self.spec.sha256 != self.identity['spec_sha256']:
            raise ValueError('scientific specification is not the current declared design')
        self.base = Config.from_dict(self.spec_manifest['base'])
        expected_base=self.spec.base(**{k:getattr(self.base,k) for k in RESOURCE_FIELDS})
        if canonical(expected_base.to_dict()) != canonical(self.base.to_dict()): raise ValueError('base coefficients differ from scientific spec')
        for record in (self.pipeline, self.protocol):
            self.check_identity(record)
            if canonical(record['spec']) != canonical(self.spec.to_dict()) or canonical(record['base']) != canonical(self.base.to_dict()):
                raise ValueError('pipeline/protocol scientific settings differ')
        allowed = ('complete','running') if pipeline_in_progress else ('complete',)
        if self.pipeline['status'] not in allowed or self.pipeline['status']=='complete' and self.pipeline.get('exit_code') != 0:
            raise ValueError('scientific pipeline is not complete')
        self.worlds = len(self.protocol['confirm_worlds'])
        if self.protocol['worlds'] != self.worlds or self.protocol['confirm_worlds'] != list(range(1000,1000+self.worlds)):
            raise ValueError('confirmation worlds differ from prespecified sequence')
        if self.protocol['policies'] != list(POLICIES) or self.protocol['backends'] != list(self.spec.backends) or self.protocol['cadences_days'] != list(self.spec.cadences):
            raise ValueError('protocol cell factors differ from declared spec')
        if self.protocol['mc_target_halfwidth'] != self.spec.mc_target_halfwidth or self.protocol['substantive_effect_threshold_work_per_member_day'] != self.spec.substantive_threshold:
            raise ValueError('protocol precision/effect threshold differs')
        recorded = {r['stage']:r for r in self.pipeline['stages']}
        if len(recorded) != len(self.pipeline['stages']): raise ValueError('duplicate pipeline stage record')
        if set(recorded)!=set(self.spec.stages)|{'analysis'}: raise ValueError('pipeline stage roster differs from prespecified stages plus analysis')
        for stage in self.spec.stages:
            manifest = self.load_json(self.root/stage/'manifest.json')
            self.check_identity(manifest)
            if manifest['status'] != 'complete': raise ValueError('required scientific stage is incomplete: '+stage)
            if stage not in recorded or recorded[stage]['status'] != 'complete' or recorded[stage]['manifest_sha256'] != self.inputs[str(self.root/stage/'manifest.json')]:
                raise ValueError('pipeline stage reference differs: '+stage)
            self.verify_outputs(self.root/stage,manifest)
            if stage not in ('mechanisms','recovery'):
                self.stages[stage] = self.check_stage(stage,stage_tasks(stage,self.spec,self.base,self.worlds))
        if 'pilot' in self.spec.stages:
            pilot = {(r['world'],r['regime']):r for r in self.stages['pilot']}
            diffs = [(pilot[w,'sublinear']['produced_work_units']-pilot[w,'linear']['produced_work_units'])/(self.spec.n*self.spec.days) for w in range(self.spec.pilot_worlds)]
            sd = statistics.stdev(diffs); count, requested = self.spec.confirmation_count(sd)
            if self.protocol['pilot_sd'] != sd or self.worlds != count or self.protocol['requested_worlds'] != requested:
                raise ValueError('pilot precision rule differs from raw paired worlds')
            if self.protocol['pilot_manifest_sha256'] != self.inputs[str(self.root/'pilot/manifest.json')]:
                raise ValueError('pilot protocol origin differs')
        elif self.worlds != self.spec.confirmation_min or self.protocol['pilot_manifest_sha256'] is not None:
            raise ValueError('fixed confirmation count differs or unexpected pilot')
        if 'recovery' in self.spec.stages: self.check_recovery()
        self.analysis_manifest = self.load_json(self.root/'analysis/manifest.json'); self.check_identity(self.analysis_manifest)
        if self.analysis_manifest['status'] != 'complete': raise ValueError('pipeline analysis incomplete')
        if recorded['analysis']['manifest_sha256']!=self.inputs[str(self.root/'analysis/manifest.json')]: raise ValueError('pipeline analysis reference differs')
        self.verify_outputs(self.root/'analysis',self.analysis_manifest)
        self.claims = self.load_json(self.root/'analysis/claims.json')
        if self.claims['analysis_inputs'] != {'protocol':self.inputs[str(self.root/'protocol.json')], 'confirmation_manifest':self.inputs[str(self.root/'confirmation/manifest.json')]}:
            raise ValueError('pipeline analysis inputs differ')
        self.primary = contrast(self.select('confirmation',regime='sublinear',backend='central',update_interval_days=1),
                                self.select('confirmation',regime='linear',backend='central',update_interval_days=1))
        # Match the producer's paired subtraction order, while independently
        # recomputing each world from raw summaries.
        lookup = {(r['world'],r['regime']):r for r in self.select('confirmation',backend='central',update_interval_days=1)}
        same_order = interval([(lookup[w,'sublinear']['produced_work_units']-lookup[w,'linear']['produced_work_units'])/(self.spec.n*self.spec.days) for w in self.protocol['confirm_worlds']])
        if self.claims['primary'] != same_order: raise ValueError('pipeline primary calculation differs from raw worlds')
        self.primary = same_order
        if self.pipeline['status'] == 'complete':
            if self.pipeline['protocol_sha256'] != self.inputs[str(self.root/'protocol.json')]: raise ValueError('final protocol hash differs')
            self.verify_file(self.root/'report.md',self.pipeline['report_sha256'])

    def check_identity(self, record):
        if any(record.get(k) != v for k,v in self.identity.items()): raise ValueError('scientific identity differs')

    def verify_file(self,path,expected=None):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root) or path.is_symlink() or not path.is_file(): raise ValueError('unsafe or missing analysis input')
        actual = file_digest(path)
        if expected is not None and actual != expected: raise ValueError('analysis input hash differs: '+str(path))
        if str(path) in self.inputs and self.inputs[str(path)] != actual: raise ValueError('input changed during analysis')
        self.inputs[str(path)] = actual
        return actual

    def load_json(self,path):
        self.verify_file(path)
        return json.loads(Path(path).read_text())

    def verify_outputs(self,path,manifest):
        hashes = manifest.get('output_sha256')
        if not isinstance(hashes,dict) or not hashes: raise ValueError('missing output hashes')
        for name,expected in hashes.items(): self.verify_file(path/name,expected)

    def check_stage(self,stage,tasks):
        path = self.root/stage; manifest = self.load_json(path/'manifest.json'); self.check_identity(manifest)
        self.verify_outputs(path,manifest)
        inventory = self.load_json(path/'case_inventory.json')
        expected = [{'case_id':case_key(c),'config':c.to_dict(),'tags':tags} for c,tags in tasks]
        if canonical(inventory) != canonical(expected) or manifest['inventory_sha256'] != digest(canonical(expected)):
            raise ValueError('declared stage inventory differs: '+stage)
        refs = self.load_json(path/'case_references.json')
        with (path/'world_summary.csv').open(newline='') as stream:
            reader=csv.DictReader(stream); columns=reader.fieldnames; csv_rows=list(reader)
        if not columns or any(None in row or any(value is None for value in row.values()) for row in csv_rows):
            raise ValueError('ragged aggregate CSV rows: '+stage)
        if len(refs) != len(tasks) or len(csv_rows) != len(tasks) or any(manifest[k] != len(tasks) for k in ('expected_rows','completed_rows')):
            raise ValueError('stage row count differs')
        if manifest['status'] != 'complete' or manifest.get('exit_code') != 0 or manifest['unique_cases'] != len({case_key(c) for c,_ in tasks}):
            raise ValueError('stage is incomplete or has wrong case count')
        expected_rows=[]
        for (config,tags),ref in zip(tasks,refs):
            key=case_key(config)
            if ref['case_id'] != key: raise ValueError('stage reference order/identity differs')
            attempt = (self.root/ref['attempt']).resolve()
            if not attempt.is_relative_to(self.root/'cases'/key) or attempt.parent != self.root/'cases'/key or not attempt.name.startswith('attempt-'):
                raise ValueError('case reference outside declared case')
            self.verify_file(attempt/'manifest.json',ref['manifest_sha256'])
            if str(attempt) not in self.case_cache:
                cm=self.load_json(attempt/'manifest.json')
                for k in ('source_sha256','pipeline_driver_sha256'):
                    if cm.get(k) != self.identity[k]: raise ValueError('case source/driver differs')
                if cm['status'] != 'complete' or cm.get('exit_code') != 0 or cm['scientific_case_id'] != key or cm['config_sha256'] != digest(canonical(config.to_dict())) or canonical(cm['config']) != canonical(config.to_dict()):
                    raise ValueError('case status/configuration differs')
                required={'summary.json','timeseries.csv','final_state.json','report.md','report.svg','checkpoint-index.json'}
                roster={p.name for p in attempt.iterdir() if p.is_file() and p.name!='manifest.json'}
                if roster != set(cm['output_sha256']) or not required.issubset(roster): raise ValueError('case output roster differs')
                self.verify_outputs(attempt,cm)
                raw=self.load_json(attempt/'summary.json')
                for k in ('n','regime','backend','world','attack','update_interval_days'):
                    if raw[k] != getattr(config,k): raise ValueError('raw summary configuration differs')
                if raw['days_completed'] != config.days: raise ValueError('raw summary horizon differs')
                state_identity(attempt/'final_state.json',config)
                self.case_cache[str(attempt)]=(config.to_dict(),raw)
            cfg,raw=self.case_cache[str(attempt)]
            if canonical(cfg) != canonical(config.to_dict()): raise ValueError('cached case has different runtime configuration')
            row={**raw,**tags,'case_id':key,'config_sha256':digest(canonical(config.to_dict()))}
            expected_rows.append(row)
        expected_columns=set().union(*(set(r) for r in expected_rows))
        if len(columns)!=len(set(columns)) or set(columns)!=expected_columns: raise ValueError('aggregate columns differ from raw planned cases')
        for observed,expected_row in zip(csv_rows,expected_rows):
            for k in columns:
                v=expected_row.get(k)
                serialized='' if v is None else json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else str(v)
                if observed[k] != serialized: raise ValueError('aggregate differs from raw summary/tag: '+stage+'/'+k)
        return expected_rows

    def check_recovery(self):
        rules=('linear_response','satisficing','reinforcement')
        grid=[dict(behavior_rule=r,autonomy_response=a,review_capacity_per_member_day=c) for r in rules for a in (0.,.25,.5) for c in (.6,.9,1.2)]
        conditions=[dict(behavior_rule=r,autonomy_response=a,review_capacity_per_member_day=.9) for r in rules for a in (0.,.25)]
        tasks=[]
        for i,c in enumerate(grid):
            for w in range(7100,7100+self.spec.recovery_train_worlds): tasks.append((dataclasses.replace(self.base,world=w,**c),dict(kind='candidate',candidate=i,**c)))
        for i,c in enumerate(conditions):
            for w in [*range(7000,7000+self.spec.recovery_train_worlds),*range(7008,7008+self.spec.recovery_holdout_worlds)]: tasks.append((dataclasses.replace(self.base,world=w,**c),dict(kind='observation',case=i,**c)))
        self.stages['recovery-training']=self.check_stage('recovery-training',tasks)
        recovery=self.load_json(self.root/'recovery/recovery_results.json')
        if len(recovery)!=len(conditions): raise ValueError('recovery generating-case count differs')
        held=[]
        for i,r in enumerate(recovery):
            if r['case']!=i or r['true']!=conditions[i] or r['chosen'] not in grid: raise ValueError('recovery condition/grid differs')
            if r['exact_grid_parameter_recovery']!=(r['true']==r['chosen']) or r['behavior_rule_recovered']!=(r['true']['behavior_rule']==r['chosen']['behavior_rule']):
                raise ValueError('recovery count indicators differ')
            for w in range(7108,7108+self.spec.recovery_holdout_worlds): held.append((dataclasses.replace(self.base,world=w,**r['chosen']),dict(kind='holdout',case=i,**r['chosen'])))
        for w in range(7200,7200+self.spec.recovery_train_worlds):
            for regime in ('linear','sublinear'): held.append((dataclasses.replace(self.base,world=w,regime=regime,autonomy_response=0),dict(kind='null',regime=regime)))
        self.stages['recovery-holdout']=self.check_stage('recovery-holdout',held)
        rm=self.load_json(self.root/'recovery/manifest.json')
        for stage,key in [('recovery-training','training_stage_manifest_sha256'),('recovery-holdout','holdout_stage_manifest_sha256')]:
            if rm[key]!=self.inputs[str(self.root/stage/'manifest.json')]: raise ValueError('recovery raw-stage origin differs')
        self.recovery=recovery
        from research_tools.recovery_checks import validate_recovery
        validate_recovery(self)

    def select(self,stage,**criteria):
        return [r for r in self.stages[stage] if all(r.get(k)==v for k,v in criteria.items())]

    def trace(self,row):
        ref_path=self.root/'cases'/row['case_id']
        candidates=[(Path(k),v) for k,v in self.case_cache.items() if Path(k).parent==ref_path and v[0]['world']==row['world']]
        if len(candidates)!=1: raise ValueError('ambiguous verified trace reference')
        with (candidates[0][0]/'timeseries.csv').open(newline='') as stream:
            reader=csv.DictReader(stream); columns=reader.fieldnames; raw=list(reader)
        if not columns or len(columns)!=len(set(columns)) or any(None in r or any(v is None for v in r.values()) for r in raw):
            raise ValueError('invalid or ragged raw trace CSV')
        rows=[{k:float(v) for k,v in r.items()} for r in raw]
        config=candidates[0][1][0]
        if [r.get('day') for r in rows]!=list(range(config['days'])) or any(not math.isfinite(v) for r in rows for v in r.values()):
            raise ValueError('raw trace horizon or finite values differ')
        return rows

    def guard(self):
        if self.identity['source_sha256'] != source_hash() or self.identity['pipeline_driver_sha256'] != driver_hash(): raise ValueError('scientific implementation changed during analysis')
        for p,h in self.inputs.items():
            if file_digest(Path(p)) != h: raise ValueError('scientific input changed during analysis: '+p)


def work(r): return r['produced_work_units']/(r['n']*r['days_completed'])
def contrast(a,b,metric=work):
    aa={r['world']:metric(r) for r in a}; bb={r['world']:metric(r) for r in b}
    if not aa or len(aa)!=len(a) or len(bb)!=len(b) or set(aa)!=set(bb): raise ValueError('empty, duplicate or unmatched paired worlds')
    if metric is work:
        ar={r['world']:r for r in a}; br={r['world']:r for r in b}
        if any((ar[w]['n'],ar[w]['days_completed'])!=(br[w]['n'],br[w]['days_completed']) for w in ar):
            raise ValueError('paired work denominators differ')
        return interval([(ar[w]['produced_work_units']-br[w]['produced_work_units'])/(ar[w]['n']*ar[w]['days_completed']) for w in sorted(ar)])
    return interval([aa[w]-bb[w] for w in sorted(aa)])


def morris_screen(study):
    """Recompute full-range elementary effects from matched raw stage worlds.

    The finite change is the difference of step means divided by the step's
    fraction of its declared factor range. One trajectory has no estimable
    across-trajectory standard deviation; this remains None, not zero.
    """
    rows=study.stages['sensitivity']; effects=[]
    for trajectory in range(study.spec.trajectories):
        previous=None; seen=[]
        for step in range(len(FACTORS)+1):
            current=[r for r in rows if r.get('trajectory')==trajectory and r.get('step')==step]
            expected_worlds=set(range(4000+trajectory*study.spec.trajectory_worlds,4000+(trajectory+1)*study.spec.trajectory_worlds))
            if len(current)!=len(expected_worlds) or {r['world'] for r in current}!=expected_worlds:
                raise ValueError('Morris trajectory step worlds differ')
            factors=current[0]['factors']
            if any(r['factors']!=factors or r['changed_factor']!=current[0]['changed_factor'] for r in current):
                raise ValueError('Morris step factor tags differ across worlds')
            mean=statistics.fmean(work(r) for r in current)
            if previous is not None:
                factor=current[0]['changed_factor']; changed=[k for k in FACTORS if factors[k]!=previous[0][k]]
                if changed!=[factor] or factor in seen:
                    raise ValueError('Morris step does not change one unused factor')
                width=FACTORS[factor][-1]-FACTORS[factor][0]
                dx=(factors[factor]-previous[0][factor])/width
                if dx<=0: raise ValueError('Morris step fraction is nonpositive')
                effects.append(dict(trajectory=trajectory,factor=factor,effect_per_full_design_range=(mean-previous[1])/dx))
                seen.append(factor)
            previous=(factors,mean)
        if set(seen)!=set(FACTORS): raise ValueError('Morris trajectory factor roster differs')
    screen=[]
    for factor in FACTORS:
        values=[r['effect_per_full_design_range'] for r in effects if r['factor']==factor]
        if len(values)!=study.spec.trajectories or not values: raise ValueError('Morris factor trajectory count differs')
        screen.append(dict(factor=factor,mu_star=statistics.fmean(abs(v) for v in values),
                           sigma=statistics.stdev(values) if len(values)>1 else None,paths=len(values)))
    return effects,screen


def generate(runs, out, *, pipeline_in_progress=False):
    """Generate only the stages prespecified and actually completed in this spec."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from research_tools.analyze import figtex, table, fmt, ci
    study=CheckedStudy(runs,pipeline_in_progress=pipeline_in_progress)
    spec=study.spec; out=Path(out); figdir=out/'figures/results'; texdir=out/'generated'; analysis=texdir/'analysis'
    for p in (figdir,texdir,analysis): p.mkdir(parents=True,exist_ok=True)
    apply_style(plt); style_start=style_metadata(); analyzer_sha=file_digest(Path(__file__)); entrypoint_sha=file_digest(ROOT/'research_tools/analyze.py'); recovery_checker_sha=file_digest(ROOT/'research_tools/recovery_checks.py')
    outputs=[]; data={}; parts={k:'' for k in ('dynamic','stress','scale','recovery')}
    scope=f'{spec.n:,} synthetic members, {spec.guilds} guilds, {spec.days} days; {study.worlds} independent paired worlds'
    def save(fig,name):
        for ext in ('pdf','svg'):
            path=figdir/(name+'.'+ext); fig.savefig(path,bbox_inches='tight',metadata={'CreationDate':None,'ModDate':None} if ext=='pdf' else {'Date':None}); outputs.append(path)
        plt.close(fig)
    center=study.select('confirmation',backend='central',update_interval_days=1)
    linear=[r for r in center if r['regime']=='linear']
    effects=[dict(policy=p,outcome='work_per_member_day',**contrast([r for r in center if r['regime']==p],linear)) for p in POLICIES]
    data['allocation_effects']=effects
    fig,ax=plt.subplots(figsize=(6.4,2.8))
    for i,r in enumerate(effects):
        color,_,marker=POLICY_STYLE[r['policy']]
        ax.errorbar(r['mean'],i,xerr=[[r['mean']-r['low']],[r['high']-r['mean']]],fmt=marker,color=color,capsize=2,markersize=5)
    ax.axvline(0,color='.6',linewidth=.7); ax.set_yticks(range(len(POLICIES)),[POLICY_NAMES[p] for p in POLICIES]); ax.invert_yaxis()
    ax.set_xlabel('Paired work difference from linear (units/member-day)'); fig.tight_layout(); save(fig,'allocation-effects')
    body=[]
    for p in POLICIES:
        rr=[r for r in center if r['regime']==p]
        body.append([POLICY_NAMES[p],fmt(statistics.fmean(work(r) for r in rr)),fmt(statistics.fmean(r['decision_regret_units']/(spec.guilds*r['days_completed']) for r in rr)),fmt(statistics.fmean(r['confirmation_mean_days'] for r in rr if r['confirmation_mean_days'] is not None),2),fmt(statistics.fmean(r['unfinished_records'] for r in rr),0)])
    parts['dynamic']+=table('dynamic-results','Organizational outcomes under daily updates and central records.',['Policy','Work/member-day','Regret/guild-day','Mean days','Unfinished'],body,scope+'. Work and regret are model units; confirmation delay includes completed records only. Entries are world means.')
    parts['dynamic']+=f'The prespecified DAMS--linear contrast is {ci(study.primary)} work units per member-day. The fixed substantive threshold is {spec.substantive_threshold:.2f}; intervals quantify conditional Monte Carlo uncertainty.\n'
    parts['dynamic']+=figtex('allocation-effects','allocation-effects',scope+'. Daily central records; bars are 95\\% normal-approximation Monte Carlo intervals for paired means.')
    dynamics={}
    for p in POLICIES:
        traces=[study.trace(r) for r in center if r['regime']==p]
        days=[r['day'] for r in traces[0]]
        if any([r['day'] for r in t]!=days for t in traces): raise ValueError('trace days differ across matched worlds')
        dynamics[p]={}
        for key in ('output_work_units','review_backlog_records'):
            ivs=[interval([t[j][key]/spec.n for t in traces]) for j in range(len(days))]
            dynamics[p][key]={'days':days,**{k:[v[k] for v in ivs] for k in ('mean','low','high')}}
    data['dynamics']=dynamics
    shock=(study.base.fault_start_day,study.base.fault_stop_day) if study.base.fault_stop_day>study.base.fault_start_day else None
    save(dynamics_figure(plt,dynamics,shock_window=shock),'governance-dynamics')
    shock_note=f' Shading marks days {shock[0]}--{min(shock[1],spec.days)-1}.' if shock and shock[0]<spec.days else ''
    parts['dynamic']+=figtex('governance-dynamics','governance-dynamics',scope+'. Each metric has a common vertical scale across policies. Bands are pointwise 95\\% normal-approximation Monte Carlo intervals; queue stocks exclude appeals.'+shock_note)
    # Other stages are optional only when the scientific spec explicitly omits them.
    if 'stress' in study.stages:
        rows=study.stages['stress']; budgets=sorted({r['attack_budget_setting'] for r in rows if 'attack_budget_setting' in r}); effects=[]
        for p in POLICIES:
            for attack in ('forge','freeride'):
                for b in budgets: effects.append(dict(policy=p,attack=attack,budget_hours_day=b,**contrast([r for r in rows if r['regime']==p and r['attack']==attack and r.get('attack_budget_setting')==b],[r for r in rows if r['regime']==p and r['attack']=='none' and r.get('attack_budget_setting')==b])))
        data['attack_effects']=effects; save(attack_figure(plt,effects,use_saved_endpoints=True),'attack-cost-effects')
        parts['stress']+=figtex('attack-cost-effects','attack-cost-effects',f'{spec.exploratory_worlds} paired worlds per cell, {spec.n:,} members, {spec.days} days. Effects use each policy\'s own unattacked control at the same budget. Bars are 95\\% normal-approximation Monte Carlo intervals; budgets are actual hour-equivalents per day.')
    if 'scenarios' in study.stages:
        rows=study.stages['scenarios']; contexts=[]
        for name in dict.fromkeys(r['scenario'] for r in rows): contexts.append(dict(context=name,**contrast([r for r in rows if r['scenario']==name and r['regime']=='sublinear'],[r for r in rows if r['scenario']==name and r['regime']=='linear'])))
        data['context_effects']=contexts
        parts['stress']+=table('context-results','Paired work contrasts across mechanistic contexts.',['Synthetic context','Work difference [MC interval]'],[[r['context'].replace('_',' '),ci(r)] for r in contexts],f'{spec.exploratory_worlds} paired worlds per context, {spec.n:,} members, {spec.days} days. Descriptive 95\\% normal-approximation Monte Carlo intervals; contexts are uncalibrated design changes.')
    if 'sensitivity' in study.stages:
        structural=[r for r in study.stages['sensitivity'] if r.get('structural')]; boundary=[]
        fig,axs=plt.subplots(1,3,figsize=(6.4,2.5),sharey=True)
        for ax,rule in zip(axs,('linear_response','satisficing','reinforcement')):
            values=[]
            for response in (-.25,0.,.25):
                v=contrast([r for r in structural if r['behavior_rule']==rule and r['autonomy_response']==response and r['regime']=='sublinear'],[r for r in structural if r['behavior_rule']==rule and r['autonomy_response']==response and r['regime']=='linear']); values.append(v); boundary.append(dict(behavior_rule=rule,response=response,**v))
            ax.errorbar([-.25,0,.25],[v['mean'] for v in values],yerr=[[v['mean']-v['low'] for v in values],[v['high']-v['mean'] for v in values]],color='.2',marker='o',capsize=2)
            ax.axhline(0,color='.7',linewidth=.6); ax.set_title(rule.replace('_',' ').capitalize()); ax.set_xlabel('Share-response coefficient')
        axs[0].set_ylabel('DAMS minus linear\n(work units/member-day)'); fig.tight_layout(); save(fig,'response-boundaries'); data['boundary_effects']=boundary
        parts['stress']+=figtex('response-boundaries','response-boundaries',f'{spec.exploratory_worlds} paired worlds per point, {spec.n:,} members, {spec.days} days. Bars are descriptive 95\\% normal-approximation Monte Carlo intervals; lines connect sampled coefficients.')
        elementary,screen=morris_screen(study)
        data['elementary_effects']=elementary; data['sensitivity_screen']=screen
        fig,ax=plt.subplots(figsize=(6.4,2.8))
        if spec.trajectories>1:
            for r in screen:
                factor=r['factor']; ax.scatter(r['mu_star'],r['sigma'],color='.15',s=18)
                ax.annotate(FACTOR_LABELS[factor],(r['mu_star'],r['sigma']),xytext=(7,-13 if factor in ('alpha','review_error_sd') else 7),textcoords='offset points',fontsize=9)
            ax.set_xlabel('Mean absolute elementary effect (work/member-day)'); ax.set_ylabel('SD across trajectories\n(work/member-day)')
            ax.set_xlim(0,(max(r['mu_star'] for r in screen) or 1)*1.5); ax.set_ylim(0,(max(r['sigma'] for r in screen) or 1)*1.4)
            dispersion='Vertical position is the standard deviation across trajectories.'
        else:
            ax.scatter([r['mu_star'] for r in screen],range(len(screen)),color='.15',s=22)
            ax.set_yticks(range(len(screen)),[FACTOR_LABELS[r['factor']] for r in screen]); ax.invert_yaxis()
            ax.set_xlabel('Absolute elementary effect (work/member-day)'); ax.set_title('Single-path screen',loc='left')
            dispersion='One trajectory cannot estimate across-trajectory dispersion; no standard deviation is shown.'
        fig.tight_layout(); save(fig,'sensitivity-screen')
        parts['stress']+=figtex('sensitivity-screen','sensitivity-screen',f'Finite-difference screen over six independent design factors: {spec.trajectories} randomized paths, {spec.trajectory_worlds} matched worlds per step, four declared levels per factor. Horizontal position uses the absolute full-range elementary effect. '+dispersion+' Effects are recomputed from verified raw step worlds; this is a structural diagnostic, not a population variance decomposition. Factor ranges and every path remain in the repository.')
    if 'extended' in study.stages:
        rows=study.stages['extended']; data['scale_effects']=[dict(population_n=spec.n,policy=p,**contrast([r for r in rows if r['context']=='fixed_four_guild_scale' and r['regime']==p],[r for r in rows if r['context']=='fixed_four_guild_scale' and r['regime']=='linear'])) for p in POLICIES]
        parts['scale']+=table('organizational-scale','Exploratory work contrasts at the observed population.',['Policy','Members','Work difference [MC interval]'],[[POLICY_NAMES[r['policy']],str(spec.n),ci(r)] for r in data['scale_effects']],f'{spec.exploratory_worlds} paired worlds per policy, {spec.days} days, four fixed guilds. This spec measures one population; no cross-scale pooling or extrapolated organizational benefits.')
        shock_rows=[r for r in rows if r['context']=='shock']; control_rows=[r for r in rows if r['context']=='no_shock']; recovery_times=[]; trajectory={}
        for p in POLICIES:
            aa={r['world']:study.trace(r) for r in shock_rows if r['regime']==p}; bb={r['world']:study.trace(r) for r in control_rows if r['regime']==p}
            if not aa or set(aa)!=set(bb): raise ValueError('shock worlds unmatched')
            differences=[]
            for w in sorted(aa):
                a,b=aa[w],bb[w]
                if [r['day'] for r in a]!=[r['day'] for r in b]: raise ValueError('shock trace days differ')
                differences.append([(x['output_work_units']-y['output_work_units'])/spec.n for x,y in zip(a,b)])
                tau=None
                for j in range(study.base.fault_stop_day,len(a)-2):
                    if all(a[k]['output_work_units']>=.99*b[k]['output_work_units'] for k in range(j,j+3)): tau=j-study.base.fault_stop_day; break
                evaluable=spec.days-30>=3
                recovery_times.append(dict(policy=p,world=w,recovery_days=tau,recovery_evaluable=evaluable,unrecovered=(tau is None) if evaluable else None))
            ivs=[interval([r[j] for r in differences]) for j in range(len(differences[0]))]
            trajectory[p]={'days':[r['day'] for r in aa[next(iter(aa))]],**{k:[v[k] for v in ivs] for k in ('mean','low','high')}}
        fig,axs=plt.subplots(len(POLICIES),1,figsize=(6.4,5.4),sharex=True,sharey=True)
        for ax,p in zip(axs,POLICIES):
            r=trajectory[p]; color,line,marker=POLICY_STYLE[p]
            ax.plot(r['days'],r['mean'],color=color,linestyle=line,marker=marker,markevery=max(1,len(r['days'])//6),markersize=3)
            ax.fill_between(r['days'],r['low'],r['high'],color=color,alpha=.12,linewidth=0); ax.axhline(0,color='.7',linewidth=.6)
            if shock: ax.axvspan(*shock,color='.94',zorder=-2)
            ax.text(.99,.85,POLICY_NAMES[p],transform=ax.transAxes,ha='right',va='top'); ax.set_ylabel('Work difference')
        axs[-1].set_xlabel('Day'); fig.tight_layout(); save(fig,'shock-recovery')
        data['shock_recovery_times']=recovery_times; data['shock_trajectories']=trajectory
        postshock_days=spec.days-30
        recovery_scope=('The observation horizon ends with the shock; post-shock recovery is not evaluable.' if postshock_days<=0 else
                        'Fewer than three post-shock days are observed; the three-day recovery criterion is not evaluable.' if postshock_days<3 else
                        'Recovery is the first post-shock start of three consecutive days at least 99\\% of own control; unrecovered worlds are retained.')
        parts['scale']+=figtex('shock-recovery','shock-recovery',f'{spec.exploratory_worlds} matched shock/no-shock world pairs per policy, {spec.n:,} members, {spec.days} days. Curves show work differences (units/member-day); bands are pointwise 95\\% normal-approximation Monte Carlo intervals. '+recovery_scope)
    if hasattr(study,'recovery'):
        recovery=study.recovery; exact=sum(r['exact_grid_parameter_recovery'] for r in recovery); rules=sum(r['behavior_rule_recovered'] for r in recovery)
        parts['recovery']+=table('synthetic-recovery','Synthetic parameter and behavioral-model recovery.',['Generating rule','Response','Chosen rule','Response','Capacity','Holdout loss'],[[r['true']['behavior_rule'].replace('_',' '),fmt(r['true']['autonomy_response'],2),r['chosen']['behavior_rule'].replace('_',' '),fmt(r['chosen']['autonomy_response'],2),fmt(r['chosen']['review_capacity_per_member_day'],1),fmt(r['heldout_distance'],2)] for r in recovery],f'{len(recovery)} generating cases; {spec.recovery_train_worlds} independent worlds per training stream and {spec.recovery_holdout_worlds} fresh worlds per held-out stream. Selection uses training only; all populations and horizons match this spec.')
        parts['recovery']+=f'Exact grid parameters are recovered in {exact} of {len(recovery)} cases, and the generating effort-rule class in {rules} of {len(recovery)}.\n'
    else: exact=rules=None
    claims={'schema_version':2,'spec':spec.to_dict(),'spec_sha256':spec.sha256,'primary':study.primary,'worlds':study.worlds,'source_sha256':study.identity['source_sha256'],'pipeline_driver_sha256':study.identity['pipeline_driver_sha256'],'analysis_driver_sha256':analyzer_sha,'exact_parameter_recovery':exact,'rule_recovery':rules,'recovery_cases':len(study.recovery) if hasattr(study,'recovery') else None,'inferential_scope':spec.inferential_scope,'omitted_stages':sorted(set(('pilot','confirmation','mechanisms','stress','sensitivity','scenarios','extended','recovery'))-set(spec.stages))}
    for name,rows in data.items():
        path=analysis/(name+('.json' if isinstance(rows,dict) else '.csv'))
        (atomic_json if isinstance(rows,dict) else atomic_csv)(path,rows); outputs.append(path)
    atomic_json(analysis/'claims.json',claims); outputs.append(analysis/'claims.json')
    for name,text in parts.items():
        path=texdir/f'revision_{name}.tex'; path.write_text(text or '% This scientific spec omits this stage; no result is synthesized.\n'); outputs.append(path)
    (texdir/'revision_results.tex').write_text(''.join(parts.values())); outputs.append(texdir/'revision_results.tex')
    macros={'StudyPrimaryMean':fmt(study.primary['mean'],5),'StudyPrimaryLow':fmt(study.primary['low'],5),'StudyPrimaryHigh':fmt(study.primary['high'],5),'StudyWorlds':str(study.worlds),'StudyPopulation':str(spec.n),'StudyDays':str(spec.days),'StudyExactRecovery':str(exact) if exact is not None else 'not evaluated','StudyRuleRecovery':str(rules) if rules is not None else 'not evaluated','StudyRecoveryCases':str(len(study.recovery)) if hasattr(study,'recovery') else 'not evaluated'}
    (texdir/'revision_macros.tex').write_text(''.join('\\newcommand{\\'+k+'}{'+v+'}\n' for k,v in macros.items())); outputs.append(texdir/'revision_macros.tex')
    study.guard()
    if analyzer_sha!=file_digest(Path(__file__)) or entrypoint_sha!=file_digest(ROOT/'research_tools/analyze.py') or recovery_checker_sha!=file_digest(ROOT/'research_tools/recovery_checks.py') or style_start!=style_metadata(): raise ValueError('publication analyzer/style/checker changed during generation')
    atomic_json(analysis/'generation_manifest.json',{**provenance(),'status':'complete','artifact_kind':'spec-v2 publication postprocessing','pipeline_status_at_generation':study.pipeline['status'],'pipeline_manifest_sha256_at_generation':study.inputs[str(study.root/'pipeline_manifest.json')],'scientific_spec':spec.to_dict(),'spec_sha256':spec.sha256,'source_sha256':study.identity['source_sha256'],'pipeline_driver_sha256':study.identity['pipeline_driver_sha256'],'analysis_driver_sha256':analyzer_sha,'analysis_entrypoint_sha256':entrypoint_sha,'recovery_checker_sha256':recovery_checker_sha,'interval_rendering':'stored low/high endpoints; no rounded critical-value recomputation','figure_style':style_start,'graphics_library':matplotlib.__version__,'dependency_lock_sha256':file_digest(ROOT/'uv.lock'),'inputs':{str(Path(k).relative_to(study.root)):v for k,v in study.inputs.items() if Path(k)!=study.root/'pipeline_manifest.json'},'outputs':{str(p.relative_to(out)):file_digest(p) for p in outputs},'scope':'Complete postprocessing of this declared spec; complete scientific pipeline requires its separate final pipeline manifest. No calibration or field effect is established.'})
    return claims
