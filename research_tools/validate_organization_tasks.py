"""Independent r7 raw arithmetic/roster verifier, not an empirical validation.

This module never imports or calls organization_tasks' topology, prepare,
task_allocation, finish, or reviewer transition functions. It reconstructs the
new paths from immutable work/plan/review evidence and checks accounting.
Inherited validators still own lifecycle, cash, queues, and operating accounts.
"""
from __future__ import annotations
import collections
import copy
import dataclasses
import json
import math
from pathlib import Path
import sqlite3
from dams_sim.randomness import WorldRandom
from dams_sim.longitudinal import GregorianClock
from dams_sim.storage import canonical,file_digest
IMPORT_SHA=file_digest(Path(__file__))

def _source():
    if file_digest(Path(__file__))!=IMPORT_SHA:raise ValueError("organization verifier source changed after import")


def _close(a,b,label):
    if not isinstance(a,(int,float)) or isinstance(a,bool) or not math.isfinite(a) or not math.isclose(a,b,rel_tol=1e-11,abs_tol=1e-9):raise ValueError('organization raw mismatch: '+label)


def _links(ids,c,assignments=None):
    # Independently implemented row construction (no production topology call).
    parents={};depths={};collab={};verify={};groups={str(d):[] for d in range(c.department_count)}
    membership=lambda i: i%c.department_count if assignments is None else assignments[str(i)]
    for i in ids:groups[str(membership(i))].append(i)
    for group in groups.values():
        for index,i in enumerate(group):
            parent=group[(index-1)//c.management_span] if index else None
            parents[str(i)]=parent;depths[str(i)]=depths[str(parent)]+1 if index else 0
    from bisect import bisect_right
    departments=[int(d) for d,g in groups.items() if g];indices={d:k for k,d in enumerate(departments)}
    for i in ids:
        for destination,count,independent in ((collab,c.collaboration_contacts,False),(verify,c.verification_contacts,True)):
            selected=[];own=membership(i)
            for attempt in range(4*count+4):
                if len(departments)==1:department=own;offset=attempt
                else:
                    department=departments[(indices[own]+1+attempt%(len(departments)-1))%len(departments)]
                    offset=attempt//(len(departments)-1)
                members=groups[str(department)];peer=members[(bisect_right(members,i)+offset)%len(members)]
                if peer==i or peer in selected or independent and (peer==parents[str(i)] or parents[str(peer)]==i):continue
                selected.append(peer)
                if len(selected)==count:break
            destination[str(i)]=selected if count else []
    return {'active_people':ids,'department_members':groups,'reporting_parent':parents,'reporting_depth':depths,
            'collaboration_peers':collab,'verification_candidates':verify}


def verify_organization_accounts(config,state,database):
    _source()
    c=config.organization_tasks
    pc=getattr(config,'person_context',None);pc_language={};pc_openness={};pc_ethnicity={};pc_fatigue_extra={};pc_education={}
    if c is None:
        if 'organization_tasks_state' in state:raise ValueError('disabled organization carries durable state')
        return {'valid':True,'enabled':False,'formal_worlds_added':0}
    from research_tools.validate_longitudinal import iter_journal_rows
    owned=not isinstance(database,sqlite3.Connection)
    db=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro&immutable=1',uri=True) if owned else database
    rng=WorldRandom(config.seed,config.world,world_context=config.longitudinal.world_context)
    stocks={str(d):c.initial_dependency_units_per_department for d in range(c.department_count)}
    generated={k:0. for k in stocks};consumed={k:0. for k in stocks};active=set();sites={};metrics=collections.defaultdict(float)
    base_c=c;structure=None;paid_installations={};completed_installations={}
    if c.schema_version==2:
        structure={'department_count':c.department_count,'observed_installed_departments':config.enterprise_growth.initial_departments,
            'initial_units':dict(stocks),'objectives':[list(w) for w in c.department_objectives],
            'task_families':{str(d):d for d in range(c.department_count)},
            'assignments':{str(i):i%c.department_count for i in range(config.n)},'training_remaining':{},'pending':[],'history':[],'replans':[]}
    def department(person):return person%c.department_count if structure is None else structure['assignments'][str(person)]
    def split_reference(values,splits):
        result=dict(values)
        for row in splits:
            key=str(row['source_department']);child=str(row['target_department'])
            portion=len(row['moved_people'])/row['source_member_count']
            transfer=result[key]*len(row['moved_people'])/row['source_member_count']
            result[child]=transfer;result[key]=result[key]-transfer
        return result
    def plan_reference(assignments,active,old,new):
        routing=dict(assignments);edges=[]
        for child in range(old,new):
            candidates=[]
            for department_id in range(child):
                members=sorted(i for i in active if routing[str(i)]==department_id)
                candidates.append((-len(members),department_id,members))
            _,source,members=min(candidates)
            if len(members)<2:return None
            moving=members[(len(members)+1)//2:]
            edges.append({'source_department':source,'target_department':child,
                          'source_member_count':len(members),'moved_people':moving})
            for person in moving:routing[str(person)]=child
        return {'splits':edges,'moved_people':sorted({i for row in edges for i in row['moved_people']})}

    people_by_token={p['token']:p['id'] for p in state['people']}
    day=0;events=[];last=None;last_plan=None;remaining={}
    l=config.longitudinal;clock=GregorianClock(l);learn={};fatigue={};guilds={};memory={str(g):l.initial_memory for g in range(config.guilds)};exit_sizes={};inventory_guilds=config.guilds
    routine={str(g):l.initial_routine_strength for g in range(config.guilds)};aliases={g:g for g in range(config.guilds)};adoptions={str(g):None for g in range(config.guilds)}
    rows=iter_journal_rows(db)
    try:
        for when,kind,ident,payload in rows:
            v=json.loads(payload)
            if when!=day:raise ValueError('organization raw day is not consecutive')
            if structure is not None:
                if kind=='infrastructure_requested':paid_installations[v['requested_day']]=copy.deepcopy(v)
                elif kind=='infrastructure_complete':completed_installations[day]=copy.deepcopy(v)
                elif kind=='task_reorganization_requested':
                    installed=completed_installations.get(day);g=config.enterprise_growth
                    if installed is None or paid_installations.get(installed['requested_day']) is None:
                        raise ValueError('task split lacks preceding paid installation history')
                    delta=installed['departments']-structure['observed_installed_departments']
                    previous=structure['pending'][-1]['target_department_count'] if structure['pending'] else c.department_count
                    expected={'installation':installed,'completed_installation_day':day,
                        'previous_department_count':previous,'target_department_count':previous+delta,
                        'ready_day':day+g.hiring_delay_days,'setup_cost_subset_resource_units':delta*g.department_setup_resource_units}
                    paid=paid_installations[installed['requested_day']]
                    if (delta<=0 or v!=expected or installed['ready_day']!=installed['requested_day']+g.construction_delay_days
                            or day<installed['ready_day'] or paid['setup_cost']<expected['setup_cost_subset_resource_units']):
                        raise ValueError('task split request/capital/delay differs from actual installation')
                    structure['observed_installed_departments']=installed['departments'];structure['pending'].append(copy.deepcopy(expected))
                    metrics['task_reorganization_setup_cost_subset_resource_units']+=expected['setup_cost_subset_resource_units']
                elif kind=='task_reorganization_planned':
                    if not structure['pending'] or 'split_plan' in structure['pending'][0]:raise ValueError('task split plan duplicated or unrequested')
                    q=structure['pending'][0]
                    if day<max(q['ready_day'],q.get('replan_not_before',0)) or not clock.is_workday(day):raise ValueError('task split planned before calendar readiness')
                    plan=plan_reference(structure['assignments'],active,c.department_count,q['target_department_count'])
                    if plan is None:raise ValueError('task split lacks a nonempty local source')
                    plan['planning_day']=day;expected={**q,'split_plan':plan}
                    if canonical(v)!=canonical(expected):raise ValueError('task split changes untouched departments/people')
                    q['split_plan']=plan
                    for person in plan['moved_people']:
                        token=str(person);structure['training_remaining'][token]=structure['training_remaining'].get(token,0.)+config.enterprise_operations.entrant_training_workday_units
                elif kind=='task_reorganization_replan':
                    if not structure['pending'] or 'split_plan' not in structure['pending'][0] or not clock.is_workday(day):raise ValueError('unplanned task cancellation')
                    q=structure['pending'][0];plan=q['split_plan'];departed=sorted(set(plan['moved_people'])-active)
                    if not departed:raise ValueError('task replanning has no departed mover')
                    expected={'day':day,'installation_requested_day':q['installation']['requested_day'],
                        'previous_plan':copy.deepcopy(plan),'departed_people':departed,
                        'served_work_hours_by_person':{str(i):config.enterprise_operations.entrant_training_workday_units-structure['training_remaining'][str(i)] for i in plan['moved_people']},
                        'cancelled_unserved_hours_by_person':{str(i):structure['training_remaining'][str(i)] for i in plan['moved_people']},
                        'replan_not_before':day+config.enterprise_growth.hiring_delay_days}
                    if canonical(v)!=canonical(expected):raise ValueError('task replanning loses sunk time or cancels unserved work as paid')
                    for person in plan['moved_people']:structure['training_remaining'].pop(str(person))
                    structure['replans'].append(copy.deepcopy(expected));q.pop('split_plan');q['replan_not_before']=expected['replan_not_before']
                    metrics['task_reorganization_sunk_work_hours']+=math.fsum(expected['served_work_hours_by_person'].values())
                    metrics['task_reorganization_cancelled_unserved_hours']+=math.fsum(expected['cancelled_unserved_hours_by_person'].values())
                elif kind=='task_reorganization_complete':
                    if not structure['pending'] or 'split_plan' not in structure['pending'][0] or not clock.is_workday(day):raise ValueError('task split without pending paid work plan')
                    q=structure['pending'][0];old=c.department_count;new=q['target_department_count'];plan=q['split_plan']
                    if (q['previous_department_count']!=old or day<=plan['planning_day'] or day<q['ready_day']
                            or not set(plan['moved_people'])<=active
                            or any(structure['training_remaining'][str(i)]>1e-12 for i in plan['moved_people'])):
                        raise ValueError('task split precedes actual funded work or changes departed persons')
                    before={'stocks':dict(stocks),'generated':dict(generated),'consumed':dict(consumed)}
                    basis=dict(structure['initial_units']);assignments=dict(structure['assignments']);families=dict(structure['task_families'])
                    after={k:split_reference(value,plan['splits']) for k,value in before.items()};new_basis=split_reference(basis,plan['splits'])
                    new_assignments=dict(assignments);objectives=list(c.department_objectives)
                    for edge in plan['splits']:
                        objectives.append(objectives[edge['source_department']])
                        structure['task_families'][str(edge['target_department'])]=structure['task_families'][str(edge['source_department'])]
                        for person in edge['moved_people']:
                            if new_assignments[str(person)]!=edge['source_department']:raise ValueError('task split source assignment changed')
                            new_assignments[str(person)]=edge['target_department']
                    expected={'day':day,**q,'stocks_before':before,'initial_units_before':basis,
                        'assignments_before':assignments,'stocks_after':after,'initial_units_after':new_basis,'assignments_after':new_assignments,
                        'task_families_before':families,'task_families_after':dict(structure['task_families']),
                        'paid_work_hours':len(plan['moved_people'])*config.enterprise_operations.entrant_training_workday_units}
                    if canonical(v)!=canonical(expected):raise ValueError('task split creates/drops knowledge or changes its closed history')
                    structure['pending'].pop(0);stocks=after['stocks'];generated=after['generated'];consumed=after['consumed']
                    structure['initial_units']=new_basis;structure['assignments']=new_assignments
                    c=dataclasses.replace(base_c,department_count=new,department_objectives=tuple(objectives))
                    structure['department_count']=new;structure['objectives']=[list(w) for w in objectives];structure['history'].append(copy.deepcopy(expected))
            if kind in ('entry','acquisition_entry'):
                person=v['person']
                if structure is not None:structure['assignments'][str(person)]=person%c.department_count
                if type(person) is not int or person in learn:raise ValueError('organization entry reuses identity')
                active.add(person);guilds[person]=v['guild'];sites[person]=v['organization_site'] if kind=='entry' else v['site']
                if pc is not None:
                    if 'person_context' not in v:raise ValueError('person-context entry witness missing')
                    pc_language[person]=v['person_context']['language'];pc_openness[person]=v['person_context']['traits']['openness']
                    pc_education[person]=v['person_context']['traits']['education']
                    pc_ethnicity[person]=v['person_context'].get('ethnicity')
                    w=v['person_context'];pc_fatigue_extra[person]=(pc.commute_fatigue_per_hour*w.get('commute_hours',0.)-(pc.physical_fatigue_coefficient*w['traits'].get('physical_capacity',0.) if w.get('physical_work') else 0.))
                if kind=='entry':
                    learn[person]=memory.get(str(v['guild']),0.)*l.memory_transfer_fraction;fatigue[person]=.1
                else:
                    target=next(t for t in config.enterprise_acquisition.targets if t.key==v['target'])
                    profile=next(p for p in target.employees if p.key==v['source_person'])
                    retained=1. if config.enterprise_acquisition.integration_mode=='preserve-autonomy' else config.enterprise_acquisition.centralization_learning_retention
                    memory.setdefault(str(v['guild']),target.organizational_knowledge*retained)
                    learn[person]=profile.learning*retained;fatigue[person]=profile.fatigue
            elif kind=='acquisition_integration_complete':
                target=next(t for t in config.enterprise_acquisition.targets if t.key==v['target'])
                retained=1. if config.enterprise_acquisition.integration_mode=='preserve-autonomy' else config.enterprise_acquisition.centralization_learning_retention
                for g in range(inventory_guilds,inventory_guilds+v['assets']['guilds']):memory[str(g)]=target.organizational_knowledge*retained;routine[str(g)]=0.;aliases[g]=g;adoptions[str(g)]=None
                inventory_guilds+=v['assets']['guilds']
            elif kind=='infrastructure_complete':
                for g in range(inventory_guilds,v['guilds']):memory[str(g)]=0.;routine[str(g)]=0.;aliases[g]=g;adoptions[str(g)]=None
                inventory_guilds=v['guilds']
            elif kind=='guild_merger':
                source,target=v['source'],v['target'];na=sum(guilds[i]==source for i in active);nb=sum(guilds[i]==target for i in active)
                memory[str(target)]=(memory[str(source)]*na+memory[str(target)]*nb)/(na+nb) if na+nb else (memory[str(source)]+memory[str(target)])/2
                routine[str(target)]=(routine[str(source)]*na+routine[str(target)]*nb)/(na+nb) if na+nb else (routine[str(source)]+routine[str(target)])/2
                for g,root in list(aliases.items()):
                    if root==source:aliases[g]=target
                for i in active:
                    if guilds[i]==source:guilds[i]=target
            elif kind=='adoption':
                g=str(v['guild']);_close(v['routine_retained'],routine[g],'adoption routine retained');adoptions[g]=day
            elif kind=='guild_move':sites[v['person']]=v['organization_site'];guilds[v['person']]=v['new']
            elif kind in ('exit','retirement','layoff','voluntary_exit'):
                if not exit_sizes:exit_sizes={g:sum(guilds[i]==g for i in active) for g in set(guilds[i] for i in active)}
                person=v['person'];g=guilds[person]
                loss=(1-l.memory_transfer_fraction)*min(memory[str(g)],learn[person]/max(1,exit_sizes[g]))
                _close(v['memory_loss'],loss,'exit knowledge loss');memory[str(g)]-=loss;active.remove(person)
            if kind!='day_end':events.append((kind,ident,v));continue
            plans=[p for k,e,p in events if k=='organization_plan'];tasks=[p for k,e,p in events if k=='organization_tasks']
            if len(plans)!=1 or len(tasks)!=1:raise ValueError('organization daily phases missing/duplicated')
            plan,task=plans[0],tasks[0];links=_links(sorted(active),c,None if structure is None else structure['assignments'])
            if type(plan['day']) is not int or plan['day']!=day or plan['topology']!=links:raise ValueError('organization active roster/topology differs from lifecycle')
            present=plan['present_people']
            if any(type(i) is not int for i in present) or present!=sorted(set(present)) or not set(present)<=active:raise ValueError('organization presence roster differs')
            if set(plan['allocations'])!={str(i) for i in present}:raise ValueError('organization allocation roster differs')
            funding=plan['funding_fraction'];_close(funding,min(1.,max(0.,funding)),'funding')
            remaining={};net=quality=learning=0.
            for i in present:
                key=str(i);a=plan['allocations'][key];peers=[j for j in links['collaboration_peers'][key] if j in present]
                request=links['reporting_depth'][key]*c.reporting_hours_per_layer+len(peers)*c.collaboration_hours_per_contact+c.verification_hours_per_member_workday
                _close(a['requested_network_hours'],request,'network request')
                available=a['available_hours'];_close(available,max(0.,available),'available capacity')
                reorganization=0.;still_training=False
                if structure is not None:
                    owed=structure['training_remaining'].get(key,0.);reorganization=min(available,owed)
                    remaining_work=max(0.,owed-reorganization*funding)
                    if key in structure['training_remaining']:structure['training_remaining'][key]=remaining_work
                    _close(a['reorganization_hours'],reorganization,'actual reorganization time')
                    _close(a['remaining_reorganization_hours'],remaining_work,'remaining reorganization obligation')
                    still_training=remaining_work>1e-12
                    metrics['task_reorganization_work_hours']+=reorganization*funding
                network=min(available-reorganization,request);fixed=network+reorganization
                _close(a['network_hours'],fixed,'network funded capacity')
                residual=available-fixed;weights=c.department_objectives[department(i)]
                for field,w in zip(('production_cap_hours','quality_hours','learning_hours'),weights):
                    _close(a[field],residual*w,field)
                net+=fixed*funding;quality+=a['quality_hours']*funding;learning+=a['learning_hours']*funding
                served=max(0.,network-links['reporting_depth'][key]*c.reporting_hours_per_layer-len(peers)*c.collaboration_hours_per_contact)*funding
                remaining[key]=int((served+1e-12)/c.verification_hours_per_claim)
            metrics['organization_network_hours']+=net;metrics['organization_quality_hours']+=quality;metrics['organization_learning_hours']+=learning
            work=[p for k,e,p in events if k=='work']
            for person in learn:learn[person]*=2**(-1/l.skill_forgetting_half_life_days)
            for g in v['memory']:memory.setdefault(str(g),0.)
            for g in memory:memory[g]*=2**(-1/l.memory_half_life_days)
            by_department={k:0. for k in stocks};potential={k:0. for k in stocks}
            cooperation=dict(plan['cooperation_by_person'])
            for p in work:cooperation[str(p['person'])]=p['cooperation']
            for p in work:
                i=p['person'];o=p['organization'];key=str(department(i));a=plan['allocations'][str(i)]
                if type(o['task_department']) is not int or o['task_department']!=department(i):raise ValueError('organization task department differs')
                _close(o['quality_hours'],a['quality_hours']*funding,'quality work reservation')
                _close(o['learning_hours'],a['learning_hours']*funding,'learning work reservation')
                if o['planned_effort']>a['production_cap_hours']*funding+1e-12:raise ValueError('organization production exceeds local objective time')
                readiness=1/(1+c.reporting_readiness_penalty_per_layer*links['reporting_depth'][str(i)])
                _close(o['reporting_readiness_fraction'],readiness,'reporting readiness')
                if type(o['claim_ready_day']) is not int or o['claim_ready_day']!=day+1+links['reporting_depth'][str(i)]*c.reporting_delay_days_per_layer:raise ValueError('organization report latency differs')
                peers=[j for j in links['collaboration_peers'][str(i)] if j in present]
                # R11: cross-language collaboration contacts carry the declared discount (independent re-statement).
                lang=(lambda j:1. if pc is None else (1. if pc_language[i]==pc_language[j] else 1-pc.cross_language_help_discount)*(1. if pc_ethnicity[i]==pc_ethnicity[j] else 1-pc.cross_ethnic_help_discount))
                help_input=math.fsum(cooperation[str(j)]*lang(j) for j in peers)/len(peers) if peers else 0.
                _close(p['help_input'],help_input,'collaboration input')
                agent=state['agents'][i];morning=p['morning_observed_state'];token=state['people'][i]['token']
                _close(morning['learning'],learn[i],'morning learning from complete history')
                _close(morning['fatigue'],fatigue[i],'morning fatigue from complete history')
                workload=p['effort']+p.get('enterprise_coordination',0.)+p['attack_hours']+p['training']+(a['network_hours']+a['quality_hours']+a['learning_hours'])*funding
                fatigue[i]=max(0.,min(1.,fatigue[i]+.15*workload-.1*(1-workload)-.03+(pc_fatigue_extra[i] if pc is not None else 0.)))
                learn[i]=max(0.,min(2.,learn[i]+(1. if pc is None else max(0.,1+pc.learning_openness_coefficient*pc_openness[i]+pc.learning_education_coefficient*pc_education[i]))*l.skill_learning_per_work_year*(p['effort']+o['learning_hours'])/max(1,clock.workdays_in_year(day))))
                shock=.7 if config.fault_start_day<=day<config.fault_stop_day and sites[i]==0 else 1.
                difficulty=.5+rng.uniform('task_difficulty',day,token);noise=math.exp(.1*rng.normal('task_output',day,token))
                g=str(guilds[i]);adopt=adoptions[g]
                transition=1. if adopt is None else 1-.2*routine[g]*(1-max(0.,min(1.,(day-adopt)/l.transition_days)))
                demand=1. if config.enterprise_operations is not None else next((amount for when,amount in reversed(l.demand_schedule) if when<=day),1.)
                _close(o['transition_fraction'],transition,'production adoption/routine transition')
                _close(o['demand_fraction'],demand,'production actual demand schedule')
                before=agent['skill']*(1+morning['learning'])*o['planned_effort']*(1-morning['fatigue'])*noise*shock*o['demand_fraction']*o['transition_fraction']/difficulty*(1+config.cooperation_strength*help_input)*readiness
                _close(o['potential_before_dependencies'],before,'production equation before dependencies')
                potential[key]+=before;by_department[key]+=p['produced_research_only']
                sd=config.review_error_sd/(1+c.quality_measurement_efficiency*o['quality_hours'])
                observed=max(0.,p['produced_research_only']+sd*rng.normal('observed_work',day,token))+(p['attack_hours'] if p['fraudulent'] else 0.)
                _close(p['observed'],observed,'quality-conditioned measurement')
            potential={key:math.fsum(p['organization']['potential_before_dependencies'] for p in work if str(department(p['person']))==key) for key in stocks}
            by_department={key:math.fsum(p['produced_research_only'] for p in work if str(department(p['person']))==key) for key in stocks}
            if type(task['day']) is not int or task['day']!=day or task['morning_stocks']!=stocks:raise ValueError('organization task morning inventory differs')
            used={};factors={}
            if structure is not None:
                family_members={family:sorted(k for k in stocks if structure['task_families'][k]==family) for family in range(base_c.department_count)}
                family_potential={family:math.fsum(potential[k] for k in group) for family,group in family_members.items()}
                family_stocks={family:math.fsum(stocks[k] for k in group) for family,group in family_members.items()}
                if task.get('task_families')!=structure['task_families']:raise ValueError('task administrative split changed technology family')
                ratio=c.dependency_input_units_per_output_work_unit
                for family,group in family_members.items():
                    factor=1. if family==0 or ratio==0 or family_potential[family]==0 else min(1.,family_stocks[family-1]/(ratio*family_potential[family]))
                    downstream=math.fsum(by_department[k] for k in family_members.get(family+1,[]))*ratio
                    for key in group:
                        factors[key]=factor;used[key]=downstream*stocks[key]/family_stocks[family] if family_stocks[family] else 0.
            for d in range(c.department_count):
                key=str(d);_close(task['potential_by_department'][key],potential[key],'department potential sum')
                f=factors[key] if structure is not None else (1. if d==0 or c.dependency_input_units_per_output_work_unit==0 or potential[key]==0 else min(1.,stocks[str(d-1)]/(c.dependency_input_units_per_output_work_unit*potential[key])))
                factors[key]=f;_close(task['dependency_factors'][key],f,'dependency fraction')
                _close(task['produced_by_department'][key],by_department[key],'department actual output')
                if by_department[key]>potential[key]*f+1e-8:raise ValueError('organization output exceeds dependencies')
                if structure is None:used[key]=by_department[str(d+1)]*c.dependency_input_units_per_output_work_unit if d+1<c.department_count else 0.
                _close(task['consumed_input_units'][key],used[key],'dependency input consumed')
                if used[key]>stocks[key]+1e-8:raise ValueError('organization consumed uncreated/same-day inputs')
            for p in work:
                i=p['person'];o=p['organization'];ready=o['potential_before_dependencies']*factors[str(department(i))]
                if config.enterprise_operations is not None:
                    _close(p['enterprise_unconstrained_potential'],ready,'task constrained enterprise potential')
                else:_close(p['produced_research_only'],ready,'task constrained output')
            for key in stocks:
                stocks[key]=max(0.,stocks[key]+by_department[key]-used[key]);generated[key]+=by_department[key];consumed[key]+=used[key]
                _close(task['closing_stocks'][key],stocks[key],'closing dependency inventory')
            metrics['organization_dependency_input_units']+=math.fsum(used.values())
            # Reconcile actual review assignments/deferrals in the original journal order.
            work_people_by_event={event:p['person'] for k,event,p in events if k=='work'}
            for kind,event,p in events:
                if kind not in ('review','organization_verification_deferred'):continue
                claimant=work_people_by_event.get(event)
                if claimant is None:
                    # event contains retained token, not a recyclable employee slot.
                    token=event.split(':g:')[0].split(':',2)[2]
                    claimant=people_by_token.get(token)
                if claimant is None:raise ValueError('organization verification event lacks retained claimant')
                candidates=links['verification_candidates'].get(str(claimant))
                if candidates is None:candidates=[i for i in present if i!=claimant and department(i)!=department(claimant)]
                reviewer=next((i for i in candidates if i in present and remaining.get(str(i),0)>0),None)
                if kind=='organization_verification_deferred':
                    if reviewer is not None or type(p['person']) is not int or p['person']!=claimant or type(p['ready']) is not int or p['ready']!=day+1:raise ValueError('organization deferral with available reviewer')
                    metrics['organization_verification_deferred_records']+=1
                else:
                    if reviewer is None or type(p.get('verification_reviewer')) is not int or p.get('verification_reviewer')!=reviewer:raise ValueError('organization independent verification assignment differs')
                    _close(p['verification_hours'],c.verification_hours_per_claim,'verification charged time')
                    remaining[str(reviewer)]-=1;metrics['organization_verification_hours']+=p['verification_hours']
            worked={p['person'] for p in work}
            for person in active-worked:fatigue[person]=max(0.,min(1.,fatigue[person]-.1))
            for g in set(guilds[i] for i in active):
                members={i for i in active if guilds[i]==g}
                mean_effort=math.fsum(p['effort'] for p in work if p['person'] in members)/max(1,len(members))
                memory[str(g)]=min(2.,memory[str(g)]+l.memory_learning_per_work_year*mean_effort/max(1,clock.workdays_in_year(day)))
            for g,amount in v['memory'].items():_close(amount,memory[str(g)],'organizational memory history')
            for g,root in aliases.items():
                if g!=root:continue
                key=str(g);decay=2**(-1/l.routine_adjustment_half_life_days)
                routine[key]=routine[key]*decay if adoptions[key] is not None else min(1.,routine[key]+(1-routine[key])*(1-decay))
            for g,amount in v['routine'].items():_close(amount,routine[str(g)],'organizational routine history')
            durable={'schema_version':base_c.schema_version,'completed_days':day+1,'stocks':dict(stocks),'generated':dict(generated),'consumed':dict(consumed),'last_plan':plan,'review_remaining':remaining}
            if structure is not None:
                installed=v['enterprise_growth_state']['departments']
                if (type(installed) is not int or structure['observed_installed_departments']!=installed):
                    raise ValueError('completed installed departments lack their required task reorganization obligation')
                durable['structure']=copy.deepcopy(structure)
            if canonical(v.get('organization_tasks_state'))!=canonical(durable):raise ValueError('organization day-end durable state differs from independent reconstruction')
            for key,expected in metrics.items():_close(v['metrics_cumulative'].get(key,0.),expected,key)
            last=durable;last_plan=plan;events=[];exit_sizes={};day+=1
        if day==0 and state['day']==0:
            if len(events)!=config.n or active!=set(range(config.n)) or any(k!='entry' or p.get('initial') is not True or e!=f"initial:{p['person']}" for k,e,p in events):raise ValueError('organization initial checkpoint lacks exact complete initial entries')
            events=[]
        if events or day!=state['day']:raise ValueError('organization raw history is incomplete')
        if day==0:last={'schema_version':base_c.schema_version,'completed_days':0,'stocks':stocks,'generated':generated,'consumed':consumed,'last_plan':None,'review_remaining':{}}
        if day==0 and structure is not None:last['structure']=copy.deepcopy(structure)
        for person in learn:
            _close(state['agents'][person]['learning'],learn[person],'final restored learning')
            _close(state['agents'][person]['fatigue'],fatigue[person],'final restored fatigue')
        if canonical(state['organization_tasks_state'])!=canonical(last):raise ValueError('organization final state differs from full raw reconstruction')
    finally:
        rows.close()
        if owned:db.close()
    _source()
    return {'valid':True,'enabled':True,'days_verified':day,'independent_transition_functions':True,
            'formal_worlds_added':0,'empirical_calibration_completed':False,'external_validation_completed':False}
