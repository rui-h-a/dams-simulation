"""Declared calendar-linked adoption experiments (scientific schema 3).

The roster is an incremental design, not a Cartesian product. All life-cycle
rates, cohort histories, resource amounts and precision tolerances are synthetic
design assumptions. Organization age does not determine member age or success.
Only a completed, source-frozen run can supply longitudinal scientific evidence.
"""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta
import math
from statistics import NormalDist, stdev

from .longitudinal import LongitudinalConfig, anniversary
from .storage import canonical, digest


LONGITUDINAL_NAMES = ('longitudinal-adoption-5y', 'longitudinal-adoption-10y')
PRIMARY_ENDPOINTS = (
    ('work_per_present_member_workday', .01, 'synthetic work units / actual present-member workday'),
    ('decision_regret_per_decision', .02, 'synthetic regret units / actual decisions'),
    ('net_resource_gain_per_present_member_workday', .05, 'synthetic resource units / actual present-member workday'),
    ('closed_by_common_end', .10, 'probability, world-level indicator'),
)


@dataclasses.dataclass(frozen=True)
class AdoptionArm:
    arm_id: str
    context: str
    strategy: str
    intervention: str = 'dams'
    regime: str = 'sublinear'
    backend: str = 'central'

    def to_dict(self): return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class LongitudinalSpec:
    name: str
    n: int = 10_000
    post_adoption_years: int = 5
    calendar_start: str = '2020-01-01'
    latest_fixed_adoption_year: int = 5
    followup_calendar_days: int = 90
    pilot_worlds: int = 4
    confirmation_min: int = 16
    confirmation_max: int = 128
    seed: int = 20261008
    guilds: int = 4
    sites: int = 2
    team_size: int = 5
    family_alpha: float = .05
    pilot_world_start: int = 20_000
    confirmation_world_start: int = 30_000
    schema_version: int = 3
    precision_rule: str = 'Bonferroni Student-t continuous planning and exact-binomial paired closure planning; disjoint pilot; fixed confirmation roster'
    inferential_scope: str = 'conditional fixed-model paired-world strategy comparison; no empirical calibration'
    history_mode: str = 'conditional-synthetic-state; organization age is not simulated prehistory'
    calendar_scope: str = 'Gregorian weekdays; January 1 and December 25 are declared synthetic nonwork holidays'
    tail_scope: str = 'common 90-calendar-day settlement followup; no new work/claims/votes; unfinished stocks retained'

    @property
    def common_end_day(self):
        start = date.fromisoformat(self.calendar_start)
        return (anniversary(start, self.latest_fixed_adoption_year+self.post_adoption_years)-start).days

    @property
    def days(self): return self.common_end_day+self.followup_calendar_days

    @property
    def observation_years(self): return (1, 3, 5) if self.post_adoption_years == 5 else (1, 3, 5, 10)

    @property
    def stages(self): return ('precision-pilot', 'confirmation', 'longitudinal-analysis', 'publication')

    def validate(self):
        if self.name not in LONGITUDINAL_NAMES or self.schema_version != 3:
            raise ValueError('unknown longitudinal scientific version')
        if self.post_adoption_years != (5 if self.name.endswith('5y') else 10):
            raise ValueError('name and complete post-adoption years differ')
        if type(self.n) is not int or not 12 <= self.n <= 10_000_000:
            raise ValueError('scale must be integer people in [12,10000000]')
        for key, low, high in (('pilot_worlds',4,1000), ('confirmation_min',16,1000),
                               ('confirmation_max',16,1000), ('followup_calendar_days',90,3650)):
            v = getattr(self, key)
            if type(v) is not int or not low <= v <= high: raise ValueError('invalid '+key)
        if self.confirmation_max < self.confirmation_min: raise ValueError('confirmation bounds reversed')
        if not 0 < self.family_alpha < 1: raise ValueError('invalid family error level')
        if set(self.pilot_ids()) & set(self.confirmation_ids(self.confirmation_max)):
            raise ValueError('formative and confirmation worlds overlap')
        return self

    def pilot_ids(self): return tuple(range(self.pilot_world_start,self.pilot_world_start+self.pilot_worlds))
    def confirmation_ids(self, count):
        if type(count) is not int or not self.confirmation_min <= count <= self.confirmation_max:
            raise ValueError('confirmation count outside frozen scientific bounds')
        return tuple(range(self.confirmation_world_start,self.confirmation_world_start+count))

    def to_dict(self):
        value = dataclasses.asdict(self)
        value.update(days=self.days, common_end_day=self.common_end_day, observation_years=self.observation_years,
                     stages=self.stages, arms=[a.to_dict() for a in declared_arms(self)],
                     contexts=context_assumptions(self), contrasts=declared_contrasts(self),
                     primary_endpoints=[{'name':k,'target_halfwidth':e,'unit':u} for k,e,u in PRIMARY_ENDPOINTS],
                     exposure_rule='actual recorded member-time; no N*days denominator; zero exposure is undefined and retained',
                     missing_rule='no world is discarded; unavailable post windows and closed/not-adopted cases remain explicit',
                     independent_unit='world ID, paired within context; years, guilds, branches and machines are not independent samples')
        return value

    @property
    def sha256(self): return digest(canonical(self.to_dict()))

    def base(self, **resources):
        from .config import Config
        return Config(n=self.n,days=self.days,guilds=self.guilds,sites=self.sites,
                      team_size=self.team_size,seed=self.seed,trace_every_days=1,
                      longitudinal=LongitudinalConfig(work_creation_stop_day=self.common_end_day,
                          max_active_members=self.n*2,max_people_ever=self.n*8),**resources).validate()


def resolve_longitudinal_spec(name, scale=None):
    if name not in LONGITUDINAL_NAMES: raise ValueError('unknown longitudinal spec: '+str(name))
    return LongitudinalSpec(name,n=10_000 if scale is None else scale,
                            post_adoption_years=5 if name.endswith('5y') else 10).validate()


def declared_arms(spec):
    contexts = ('startup','growth','financing','listed','traditional','century','federation')
    if spec.post_adoption_years == 10: contexts = ('startup','listed','federation')
    result=[]
    for context in contexts:
        strategy = 'fixed-1y' if context in ('startup','growth','financing') else 'fixed-3y'
        if context == 'federation': strategy='staged-3y'
        result += [AdoptionArm(context+'-existing',context,'never','existing','hierarchy'),
                   AdoptionArm(context+'-dams',context,strategy)]
    if spec.post_adoption_years == 5:
        result += [AdoptionArm('startup-'+s,'startup',s) for s in
                   ('founding','fixed-3y','fixed-5y','staged-1y','growth-trigger','crisis-trigger')]
        result += [AdoptionArm('startup-'+k,'startup','fixed-1y','allocation-control',k)
                   for k in ('equal','linear','hierarchy_tenure')]
        result += [AdoptionArm(c+'-process',c,'fixed-1y' if c=='startup' else 'fixed-3y','process-improvement','hierarchy')
                   for c in ('startup','listed')]
        result += [AdoptionArm('startup-'+b,'startup','fixed-1y',backend=b) for b in ('witness','consensus')]
    else:
        result += [AdoptionArm('startup-founding','startup','founding'),
                   AdoptionArm('startup-fixed-5y','startup','fixed-5y'),
                   AdoptionArm('startup-witness','startup','fixed-1y',backend='witness'),
                   AdoptionArm('listed-process','listed','fixed-3y','process-improvement','hierarchy')]
    if len({a.arm_id for a in result}) != len(result): raise ValueError('duplicate declared arm')
    return tuple(result)


def declared_contrasts(spec):
    arms = declared_arms(spec); known={a.arm_id for a in arms}; contrasts=[]
    for arm in arms:
        if arm.intervention == 'existing': continue
        contrasts.append({'contrast_id':arm.arm_id+'--existing','treatment_arm':arm.arm_id,
                          'reference_arm':arm.context+'-existing','primary':arm.arm_id==arm.context+'-dams'})
    for reference in ('startup-linear','startup-process','listed-process','startup-witness','startup-consensus'):
        if reference in known:
            treatment='listed-dams' if reference.startswith('listed') else 'startup-dams'
            contrasts.append({'contrast_id':treatment+'--'+reference,'treatment_arm':treatment,
                              'reference_arm':reference,'primary':reference.endswith(('linear','process'))})
    return contrasts


def day_at_year(spec, years):
    start=date.fromisoformat(spec.calendar_start)
    return (anniversary(start,years)-start).days


def context_assumptions(spec):
    """Conditional life-cycle inputs, not measured descriptions of organizations."""
    n=spec.n
    values={
        'startup':dict(age=0,memory=.1,routine=.1,verified_credit=0.,exit=.16,initial_cash=200.,growth=(1.,1.25,1.5,1.5)),
        'growth':dict(age=2,memory=.25,routine=.25,verified_credit=2.,exit=.20,initial_cash=250.,growth=(1.,1.5,2.,2.)),
        'financing':dict(age=4,memory=.3,routine=.35,verified_credit=3.,exit=.18,initial_cash=100.,growth=(1.,1.5,1.25,1.)),
        'listed':dict(age=30,memory=.7,routine=.75,verified_credit=10.,exit=.08,initial_cash=300.,growth=(1.,1.,1.1,1.1)),
        'traditional':dict(age=50,memory=.8,routine=.85,verified_credit=12.,exit=.06,initial_cash=200.,growth=(1.,1.,.9,.9)),
        'century':dict(age=100,memory=.9,routine=.9,verified_credit=15.,exit=.05,initial_cash=250.,growth=(1.,1.,1.,1.)),
        'federation':dict(age=20,memory=.6,routine=.6,verified_credit=6.,exit=.10,initial_cash=250.,growth=(1.,1.1,1.2,1.2)),
    }
    used={a.context for a in declared_arms(spec)}
    out={}
    for name in sorted(used):
        v=values[name]
        out[name]=dict(v,initial_n=n,workforce_targets=[(day_at_year(spec,y),max(1,round(n*f))) for y,f in zip((0,1,3,5),v['growth'])],
                       initial_member_age_years=[25.,60.],history_mode='conditional-synthetic-state',
                       listing_and_financing='context assumptions; no modelled IPO, valuation or unicorn probability')
    return out


def _long_config(spec, context, strategy, *, horizon=None):
    v=context_assumptions(spec)[context];end=spec.days if horizon is None else horizon
    targets=tuple((d,n) for d,n in v['workforce_targets'] if d<end)
    holidays=tuple(date(y,m,d).isoformat() for y in range(date.fromisoformat(spec.calendar_start).year,
                  (date.fromisoformat(spec.calendar_start)+timedelta(days=end)).year+1) for m,d in ((1,1),(12,25)))
    demand=[]
    for y in range(date.fromisoformat(spec.calendar_start).year,(date.fromisoformat(spec.calendar_start)+timedelta(days=end)).year+1):
        for m,f in ((1,1.),(4,.9),(7,1.1),(10,1.)):
            d=(date(y,m,1)-date.fromisoformat(spec.calendar_start)).days
            if 0<=d<end:demand.append((d,f))
    adopt=0;mode='never';guild_days=();trigger='active_members';threshold=spec.n*1.4;earliest=day_at_year(spec,1)
    if strategy=='founding':mode='fixed'
    elif strategy.startswith('fixed-'):mode='fixed';adopt=day_at_year(spec,int(strategy.split('-')[1][:-1]))
    elif strategy.startswith('staged-'):
        mode='staged';adopt=day_at_year(spec,int(strategy.split('-')[1][:-1]));guild_days=tuple((g,adopt+g*30) for g in range(spec.guilds))
    elif strategy.endswith('-trigger'):
        mode='observable'
        if strategy=='crisis-trigger':trigger='review_backlog_per_active_member';threshold=2.
    elif strategy!='never':raise ValueError('unknown adoption strategy')
    return LongitudinalConfig(calendar_start=spec.calendar_start,holidays=holidays,
        organization_initial_age_years=v['age'],initial_memory=v['memory'],initial_routine_strength=v['routine'],
        historical_verified_credit_per_member=v['verified_credit'],annual_exit_probability=v['exit'],
        initial_cash_per_member=v['initial_cash'],max_active_members=spec.n*2,max_people_ever=spec.n*8,
        workforce_targets=targets,demand_schedule=tuple(demand),adoption_mode=mode,adoption_day=adopt,
        guild_adoption_days=guild_days,observable_trigger=trigger,trigger_threshold=threshold,
        trigger_not_before_day=earliest if mode=='observable' else 0,
        transition_days=90,training_hours_per_workday=.1,migration_resource_units_per_member=2.,
        dual_run_resource_units_per_member_workday=.05,maintenance_resource_units_per_member_workday=.01,
        work_creation_stop_day=spec.common_end_day if horizon is None else None)


def branch_day(spec, arm):
    if arm.strategy in ('founding','never'):return 0
    if arm.strategy.endswith('-trigger'):return day_at_year(spec,1)
    return day_at_year(spec,int(arm.strategy.split('-')[1][:-1]))


def world_plan(spec, base, world):
    """Pure inventory. Parent prefixes are states, never independent MC samples.

    Prefixes form a chain within a context/world; each child receives the exact
    complete pre-adoption state. Baselines continue the latest prefix. Founding
    strategies evolve independently from the same generated founding conditions.
    """
    from .spec import case_key
    arms=declared_arms(spec);parents=[];children=[]
    for context in sorted({a.context for a in arms}):
        days=sorted({branch_day(spec,a) for a in arms if a.context==context and branch_day(spec,a)>0})
        previous=None
        for day in days:
            c=dataclasses.replace(base,world=world,days=day,regime='hierarchy',backend='central',
                                  longitudinal=_long_config(spec,context,'never',horizon=day)).validate()
            tags={'role':'prehistory-prefix','context':context,'world':world,'parent_case_id':previous,
                  'branch_day':day,'counts_as_additional_mc_sample':False}
            parents.append((c,tags));previous=case_key(c)
        for arm in (a for a in arms if a.context==context):
            lc=_long_config(spec,context,arm.strategy)
            c=dataclasses.replace(base,world=world,regime=arm.regime,backend=arm.backend,longitudinal=lc)
            if arm.intervention=='process-improvement':
                c=dataclasses.replace(c,review_capacity_per_member_day=base.review_capacity_per_member_day*1.25,
                                      review_error_sd=base.review_error_sd*.8)
            c=c.validate();day=branch_day(spec,arm)
            parent=previous if arm.intervention=='existing' else next((case_key(p) for p,t in parents if t['context']==context and t['branch_day']==day),None)
            tags={'role':'strategy','arm_id':arm.arm_id,'context':context,'world':world,
                  'strategy':arm.strategy,'intervention':arm.intervention,'branch_day':day,
                  'parent_case_id':parent,'independent_mc_unit':world,'counts_as_additional_mc_sample':False}
            children.append((c,tags))
    return parents,children


def freeze_precision(spec, pilot_effects, *, pilot_inventory_sha256, source_sha256):
    """Freeze the entire confirmation roster before observing confirmation.

    Planning uses disjoint paired-world pilot SD with a simultaneous Student-t
    critical value for continuous outcomes and exact-binomial discordance
    intervals for closure. This is a planning rule, not a coverage guarantee.
    A requested count beyond the preregistered ceiling or undefined primary
    endpoint refuses confirmation; it is never clipped and called adequate.
    """
    contrasts=[r for r in declared_contrasts(spec) if r['primary']]
    count=len(contrasts)*len(PRIMARY_ENDPOINTS)
    z=NormalDist().inv_cdf(1-spec.family_alpha/(2*count))
    estimates=[];requested=spec.confirmation_min;refusals=[]
    expected={(r['contrast_id'],e[0],w) for r in contrasts for e in PRIMARY_ENDPOINTS for w in spec.pilot_ids()}
    actual={}
    for row in pilot_effects:
        key=(row['contrast_id'],row['endpoint'],row['world'])
        if key in actual:raise ValueError('duplicate paired pilot effect')
        actual[key]=row['effect']
    if set(actual)!=expected:raise ValueError('pilot endpoint/world roster differs from declared primary contrasts')
    for c in contrasts:
        for name,epsilon,unit in PRIMARY_ENDPOINTS:
            vals=[actual[(c['contrast_id'],name,w)] for w in spec.pilot_ids()]
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in vals):
                refusals.append({'contrast_id':c['contrast_id'],'endpoint':name,'reason':'undefined or nonfinite endpoint; every world retained'})
                continue
            from .longitudinal_statistics import planned_world_count
            sd=stdev(vals);need=planned_world_count(vals,name,epsilon,spec.confirmation_min,spec.confirmation_max,spec.family_alpha,count)
            requested=max(requested,need)
            estimates.append({'contrast_id':c['contrast_id'],'endpoint':name,'unit':unit,'pilot_sd':sd,
                              'target_halfwidth':epsilon,'requested_worlds':need,
                              'planning_kind':'paired exact-binomial discordance interval' if name=='closed_by_common_end' else 'paired Student-t SD planning'})
    if requested>spec.confirmation_max:
        refusals.append({'reason':'requested precision exceeds frozen maximum; no silent sample reduction','requested':requested,'maximum':spec.confirmation_max})
    adequate=not refusals
    worlds=spec.confirmation_ids(requested) if adequate else ()
    return {'schema_version':3,'spec_sha256':spec.sha256,'source_sha256':source_sha256,
            'pilot_inventory_sha256':pilot_inventory_sha256,'pilot_world_ids':spec.pilot_ids(),
            'confirmation_world_ids':worlds,'requested_confirmation_worlds':requested,
            'confirmation_worlds':len(worlds),'status':'frozen' if adequate else 'precision-refused',
            'family_alpha':spec.family_alpha,'primary_tests':count,'normal_reference_critical_value':z,
            'continuous_interval_kind':'paired Student-t interval; approximate conditional Monte Carlo coverage',
            'closure_interval_kind':'paired discordance exact-binomial Bonferroni interval',
            'planning_assumptions':'pilot distribution used for planning only; no coverage or precision guarantee; closure all-zero interval is nondegenerate',
            'estimates':estimates,'refusals':refusals,'frozen_before_confirmation':True}
