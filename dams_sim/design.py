"""Declarative inventories for spec-v2 stages, independent of existing outputs."""
from __future__ import annotations
import dataclasses
from .randomness import WorldRandom
from .spec import ScientificSpec

POLICIES=('equal','linear','sublinear','hierarchy','hierarchy_tenure')
FACTORS={
 'alpha':(.4,.6,.8,.95),'review_capacity_per_member_day':(.4,.8,1.2,1.6),
 'review_error_sd':(0.,.2,.4,.6),'autonomy_response':(-.5,0.,.5,1.),
 'cooperation_strength':(0.,.3,.6,.9),'update_interval_days':(1,5,10,20)}
CONTEXTS={
 'noisy_knowledge':dict(decision_noise_sd=1.2,shared_signal_sd=.4,cooperation_strength=.6),
 'interdependent_delivery':dict(cooperation_strength=.9,review_capacity_per_member_day=1.2,review_error_sd=.3),
 'distributed_disruption':dict(guilds=8,sites=4,fault_start_day=10,fault_stop_day=25,review_capacity_per_member_day=.7),
 'high_observation_error':dict(review_error_sd=.6,review_capacity_per_member_day=1.2,appeal_capacity_per_member_day=.2),
 'cross_principal_confirmation':dict(backend='witness',review_capacity_per_member_day=.7,backend_review_multipliers=(1.,1.3,1.8)),
 'review_scarcity':dict(review_capacity_per_member_day=.4,appeal_capacity_per_member_day=.05,cooperation_strength=.6),
 'delayed_accountability':dict(update_interval_days=14,appeal_delay_days=7,appeal_capacity_per_member_day=.2),
 'zero_response':dict(autonomy_response=0.),'reversed_response':dict(autonomy_response=-.5)}


def stage_tasks(stage: str, spec: ScientificSpec, base, confirmation_worlds=0):
    tasks=[]
    def add(tags=None,**kw):
        p=dataclasses.replace(base,**kw).validate()
        tasks.append((p,tags or {}))
    if stage=='pilot':
        for w in range(spec.pilot_worlds):
            for k in POLICIES:add(world=w,regime=k)
    elif stage=='confirmation':
        for w in range(1000,1000+confirmation_worlds):
            for cadence in spec.cadences:
                for k in POLICIES:
                    for b in spec.backends:add(world=w,regime=k,backend=b,update_interval_days=cadence)
    elif stage=='stress':
        for w in range(2000,2000+spec.exploratory_worlds):
            for k in POLICIES:
                for per_person in (2/120,8/120):
                    cost=spec.n*per_person
                    for attack in ('none','forge','duplicate','freeride','censor'):
                        add({'attack_budget_setting':cost},world=w,regime=k,attack=attack,attack_budget_hours_per_day=cost)
        if spec.name!='governance-scale':
            for w in range(2100,2100+spec.exploratory_worlds):
                for b in spec.backends:
                    for protection in ((1.,1.,1.),(1.,.5,0.),(0.,0.,0.)):
                        add({'protection_assumption':list(protection)},world=w,backend=b,attack='censor',
                            attack_budget_hours_per_day=spec.n*8/120,administrative_censorship_exposure=protection)
                    add({'stress_kind':'quorum_unavailable'},world=w,backend=b,
                        quorum_unavailable_start_day=20,quorum_unavailable_stop_day=35)
    elif stage=='sensitivity':
        rng=WorldRandom(spec.seed,4000);names=list(FACTORS)
        for t in range(spec.trajectories):
            levels={k:min(2,int(rng.uniform('level',t,k)*3)) for k in names}
            order=sorted(names,key=lambda k:rng.uniform('factor-order',t,k))
            for step in range(7):
                if step: levels[order[step-1]]+=1
                vals={k:FACTORS[k][levels[k]] for k in names}
                for w in range(4000+t*spec.trajectory_worlds,4000+(t+1)*spec.trajectory_worlds):
                    add({'trajectory':t,'step':step,'factors':vals,'changed_factor':order[step-1] if step else None},world=w,**vals)
        for w in range(4100,4100+spec.exploratory_worlds):
            for rule in ('linear_response','satisficing','reinforcement'):
                for k in ('linear','sublinear'):
                    for response in (-.25,0.,.25):
                        add({'structural':True,'behavior_rule':rule,'autonomy_response':response},world=w,regime=k,behavior_rule=rule,autonomy_response=response)
    elif stage=='scenarios':
        for name,changes in CONTEXTS.items():
            for w in range(5000,5000+spec.exploratory_worlds):
                for k in POLICIES:add({'scenario':name},world=w,regime=k,**changes)
    elif stage=='extended':
        for w in range(8000,8000+spec.exploratory_worlds):
            for k in POLICIES:add({'context':'fixed_four_guild_scale'},world=w,regime=k)
        for w in range(8100,8100+spec.exploratory_worlds):
            for k in POLICIES:
                for fault in (False,True):
                    add({'context':'shock' if fault else 'no_shock'},world=w,regime=k,
                        fault_start_day=25 if fault else 0,fault_stop_day=30 if fault else 0)
    else:raise ValueError('unknown dynamic stage '+stage)
    return tasks
