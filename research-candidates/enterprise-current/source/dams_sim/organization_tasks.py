"""Opt-in r7 organization/task mechanism; conditional assumptions, not calibration.

Departments are controlled task groups, independent of guilds/sites. A workday is
one normalized member-work-hour, as in the inherited engine. Reporting is a
within-department tree, collaboration a cross-department peer layer, and
verification a directed independent-review assignment. No demographic variable
enters any path. Dependencies consume prior-day deliverable tokens, not cash or
additional saleable products; tokens carry no balance-sheet asset value.
"""
from __future__ import annotations
import bisect
import copy
import dataclasses
import math

VERSION = 'longitudinal-enterprise-integrated-7-networks-tasks-1'
STRUCTURE_VERSION = 'longitudinal-enterprise-task-structure-10'


def model_version(config):
    return STRUCTURE_VERSION if config.organization_tasks.schema_version==2 else VERSION


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(name+' must be finite in its declared range')


@dataclasses.dataclass(frozen=True)
class OrganizationTasksConfig:
    schema_version: int = 1
    department_count: int = 2
    management_span: int = 3
    reporting_hours_per_layer: float = .01
    reporting_delay_days_per_layer: int = 1
    reporting_readiness_penalty_per_layer: float = .05
    collaboration_contacts: int = 1
    collaboration_hours_per_contact: float = .01
    verification_contacts: int = 2
    verification_hours_per_member_workday: float = .025
    verification_hours_per_claim: float = .025
    dependency_input_units_per_output_work_unit: float = .25
    initial_dependency_units_per_department: float = 1.
    quality_measurement_efficiency: float = 2.
    # (production, quality, learning); local goals compete for the SAME residual time.
    department_objectives: tuple[tuple[float,float,float], ...] = ((.8,.15,.05),(.6,.3,.1))

    def validate(self, outer=None):
        for key, low, high in (('schema_version',1,2),('department_count',1,10000),('management_span',1,1000),
                               ('collaboration_contacts',0,16),('verification_contacts',1,16),('reporting_delay_days_per_layer',0,365)):
            v=getattr(self,key)
            if type(v) is not int or not low<=v<=high:raise ValueError(key+' must be a bounded integer')
        if outer is not None:
            if outer.longitudinal is None:raise ValueError('organization_tasks requires the longitudinal engine')
            if self.department_count>outer.n:raise ValueError('task departments exceed initial people')
            if self.schema_version==2:
                if outer.enterprise_growth is None or outer.enterprise_operations is None:
                    raise ValueError('dynamic tasks require paid growth and operating training mechanisms')
                if outer.enterprise_acquisition is not None:
                    raise ValueError('dynamic task structures require actual subsidiary engines, not legacy target capsules')
                if outer.enterprise_growth.department_setup_resource_units<=0 or outer.enterprise_operations.entrant_training_workday_units<=0:
                    raise ValueError('dynamic task expansion requires positive capital and training costs')
        for key,low,high in (('reporting_hours_per_layer',0,.1),('reporting_readiness_penalty_per_layer',0,1),
              ('collaboration_hours_per_contact',0,.1),('verification_hours_per_member_workday',0,.2),
              ('verification_hours_per_claim',.001,.2),('dependency_input_units_per_output_work_unit',0,100),
              ('initial_dependency_units_per_department',0,1e9),('quality_measurement_efficiency',0,100)):
            _number(getattr(self,key),low,high,key)
        if len(self.department_objectives)!=self.department_count:raise ValueError('one objective vector per task department is required')
        for row in self.department_objectives:
            if len(row)!=3:raise ValueError('objective vector must be production/quality/learning')
            for v in row:_number(v,0,1,'objective weight')
            if not math.isclose(math.fsum(row),1.,rel_tol=0,abs_tol=1e-12):raise ValueError('objective weights must sum to one')
        return self

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict) or set(value)-{f.name for f in dataclasses.fields(cls)}:raise ValueError('unknown organization_tasks configuration')
        v=dict(value)
        if 'department_objectives' in v:v['department_objectives']=tuple(tuple(r) for r in v['department_objectives'])
        return cls(**v).validate()


def topology(ids, config, assignments=None):
    """Rebuild from the actual active roster after entries/exits/M&A, no RNG."""
    ids=sorted(ids)
    if len(ids)!=len(set(ids)) or any(type(i) is not int or i<0 for i in ids):raise ValueError('organization active identities must be distinct nonnegative integers')
    groups={d:[] for d in range(config.department_count)}
    department=lambda i: i%config.department_count if assignments is None else assignments[str(i)]
    for i in ids:groups[department(i)].append(i)
    reporting={};depth={};collaboration={};verification={}
    for group in groups.values():
        for rank,i in enumerate(group):
            parent=None if rank==0 else group[(rank-1)//config.management_span]
            reporting[str(i)]=parent;depth[str(i)]=0 if parent is None else depth[str(parent)]+1
    nonempty=[d for d in groups if groups[d]];positions={d:k for k,d in enumerate(nonempty)}
    def contacts(i,count,independent=False):
        found=[];d=department(i)
        for q in range(4*count+4):
            if len(nonempty)>1:
                other=nonempty[(positions[d]+1+q%(len(nonempty)-1))%len(nonempty)]
                group=groups[other];offset=q//(len(nonempty)-1)
            else:group=groups[d];offset=q
            j=group[(bisect.bisect_right(group,i)+offset)%len(group)]
            if j==i or j in found:continue
            if independent and (j==reporting[str(i)] or reporting[str(j)]==i):continue
            found.append(j)
            if len(found)==count:break
        return found if count else []
    for i in ids:
        collaboration[str(i)]=contacts(i,config.collaboration_contacts)
        verification[str(i)]=contacts(i,config.verification_contacts,True)
    return {'active_people':ids,'department_members':{str(d):v for d,v in groups.items()},
            'reporting_parent':reporting,'reporting_depth':depth,
            'collaboration_peers':collaboration,'verification_candidates':verification}


class OrganizationTasksState:
    def __init__(self, outer):
        self.c=outer.organization_tasks
        self.base_c=self.c
        self.growth=outer.enterprise_growth
        self.reorganization_hours=(outer.enterprise_operations.entrant_training_workday_units
                                  if self.c.schema_version==2 else 0.)
        self.structure=None
        if self.c.schema_version==2:
            self.structure={'department_count':self.c.department_count,
                'observed_installed_departments':self.growth.initial_departments,
                'initial_units':{str(d):self.c.initial_dependency_units_per_department for d in range(self.c.department_count)},
                'objectives':[list(w) for w in self.c.department_objectives],
                'task_families':{str(d):d for d in range(self.c.department_count)},
                'assignments':{str(i):i%self.c.department_count for i in range(outer.n)},
                'training_remaining':{},'pending':[],'history':[],'replans':[]}
        self.completed_days=0
        self.stocks={str(d):self.c.initial_dependency_units_per_department for d in range(self.c.department_count)}
        self.generated={str(d):0. for d in range(self.c.department_count)}
        self.consumed={str(d):0. for d in range(self.c.department_count)}
        self.last_plan=None
        self.last_task=None
        self.review_remaining={}

    def department(self,person):
        return person%self.c.department_count if self.structure is None else self.structure['assignments'][str(person)]

    def _enroll(self,ids):
        if self.structure is not None:
            for i in ids:self.structure['assignments'].setdefault(str(i),i%self.c.department_count)

    def completed_installation(self,day,value):
        """Only completed, already paid growth investments request task splits.

        Capital is a named subset of original setup spending, never charged twice.
        Hiring delay and entrant-training time are explicit synthetic proxies
        for reorganization delay/work, not estimated department parameters.
        """
        if self.structure is None:return None
        g=self.growth;s=self.structure;installed=value.get('departments')
        if type(day) is not int or day!=self.completed_days or type(installed) is not int or installed<s['observed_installed_departments']:
            raise ValueError('dynamic tasks cannot discard installed departments/history')
        if (type(value.get('requested_day')) is not int or value['requested_day']<0
                or value.get('ready_day')!=value['requested_day']+g.construction_delay_days or day<value['ready_day']):
            raise ValueError('task remapping requires a complete delayed installation')
        delta=installed-s['observed_installed_departments']
        if not delta:return None
        previous=s['pending'][-1]['target_department_count'] if s['pending'] else self.c.department_count
        target=previous+delta
        if target>10000:raise ValueError('dynamic task department resource bound exceeded; preserve pending investment')
        q={'installation':copy.deepcopy(value),'completed_installation_day':day,
           'previous_department_count':previous,'target_department_count':target,
           'ready_day':day+g.hiring_delay_days,
           'setup_cost_subset_resource_units':delta*g.department_setup_resource_units}
        s['observed_installed_departments']=installed;s['pending'].append(q)
        return copy.deepcopy(q)

    @staticmethod
    def _local_split_plan(assignments,active,old_count,new_count):
        working=dict(assignments);splits=[]
        for child in range(old_count,new_count):
            groups={d:sorted(i for i in active if working[str(i)]==d) for d in range(child)}
            source=min(groups,key=lambda d:(-len(groups[d]),d));members=groups[source]
            if len(members)<2:return None
            moving=members[(len(members)+1)//2:]
            splits.append({'source_department':source,'target_department':child,
                           'source_member_count':len(members),'moved_people':moving})
            for i in moving:working[str(i)]=child
        return {'splits':splits,'moved_people':sorted({i for row in splits for i in row['moved_people']})}

    @staticmethod
    def _redistribute(values,splits):
        result=dict(values)
        for row in splits:
            source=str(row['source_department']);child=str(row['target_department'])
            moved=result[source]*len(row['moved_people'])/row['source_member_count']
            result[child]=moved;result[source]-=moved
        return result

    def advance_structure(self,day,active,*,operating):
        if self.structure is None:return []
        if type(day) is not int or day!=self.completed_days or type(operating) is not bool:
            raise ValueError('task restructuring requires a complete day boundary')
        self._enroll(active);s=self.structure;events=[]
        while operating and s['pending'] and day>=max(s['pending'][0]['ready_day'],s['pending'][0].get('replan_not_before',0)):
            q=s['pending'][0];old=self.c.department_count;new=q['target_department_count']
            if q['previous_department_count']!=old or new<=old:raise ValueError('task restructuring queue changed')
            if 'split_plan' not in q:
                plan=self._local_split_plan(s['assignments'],active,old,new)
                if plan is None:break
                plan['planning_day']=day;q['split_plan']=plan
                for i in plan['moved_people']:
                    token=str(i);s['training_remaining'][token]=s['training_remaining'].get(token,0.)+self.reorganization_hours
                events.append(('task_reorganization_planned',copy.deepcopy(q)))
                break
            plan=q['split_plan']
            departed=sorted(set(plan['moved_people'])-set(active))
            if departed:
                # An interrupted attempt is not completed training. Preserve
                # sunk service and explicitly cancel every old obligation;
                # after another integration delay a fresh local plan must pay
                # the full training work again, even for surviving movers.
                record={'day':day,'installation_requested_day':q['installation']['requested_day'],
                    'previous_plan':copy.deepcopy(plan),'departed_people':departed,
                    'served_work_hours_by_person':{str(i):self.reorganization_hours-s['training_remaining'][str(i)] for i in plan['moved_people']},
                    'cancelled_unserved_hours_by_person':{str(i):s['training_remaining'][str(i)] for i in plan['moved_people']},
                    'replan_not_before':day+self.growth.hiring_delay_days}
                for i in plan['moved_people']:s['training_remaining'].pop(str(i))
                s['replans'].append(copy.deepcopy(record));q.pop('split_plan');q['replan_not_before']=record['replan_not_before']
                events.append(('task_reorganization_replan',record));break
            # Capital readiness is not an already completed reorganization.
            # All movers must still be active, and prior-day funded work must
            # have discharged every obligation before roles/stocks move.
            if (day<=plan['planning_day'] or not set(plan['moved_people'])<=set(active)
                    or any(s['training_remaining'][str(i)]>1e-12 for i in plan['moved_people'])):break
            before={k:copy.deepcopy(getattr(self,k)) for k in ('stocks','generated','consumed')}
            basis=copy.deepcopy(s['initial_units']);assignments=dict(s['assignments']);families=dict(s['task_families'])
            for k in before:setattr(self,k,self._redistribute(before[k],plan['splits']))
            s['initial_units']=self._redistribute(basis,plan['splits'])
            objectives=list(self.c.department_objectives)
            for row in plan['splits']:
                objectives.append(objectives[row['source_department']])
                s['task_families'][str(row['target_department'])]=s['task_families'][str(row['source_department'])]
                for i in row['moved_people']:
                    if s['assignments'][str(i)]!=row['source_department']:raise ValueError('task split mover left its frozen source')
                    s['assignments'][str(i)]=row['target_department']
            self.c=dataclasses.replace(self.base_c,department_count=new,department_objectives=tuple(objectives))
            s['department_count']=new;s['objectives']=[list(w) for w in objectives]
            record={'day':day,**copy.deepcopy(q),'stocks_before':before,'initial_units_before':basis,
                    'assignments_before':assignments,'stocks_after':{k:copy.deepcopy(getattr(self,k)) for k in before},
                    'initial_units_after':dict(s['initial_units']),'assignments_after':dict(s['assignments']),
                    'task_families_before':families,'task_families_after':dict(s['task_families']),
                    'paid_work_hours':len(plan['moved_people'])*self.reorganization_hours}
            s['pending'].pop(0);s['history'].append(copy.deepcopy(record));events.append(('task_reorganization_complete',record))
        return events

    def prepare(self,day,active,present,available,cooperation,*,funding):
        if type(day) is not int or day!=self.completed_days or self.last_plan is not None and self.last_plan['day']>=day:raise ValueError('organization plan day is not sequential')
        _number(funding,0,1,'funding fraction')
        if len(present)!=len(set(present)) or not set(present)<=set(active):raise ValueError('organization present roster not active')
        if set(available)!=set(present) or set(cooperation)!=set(present):raise ValueError('organization planning input roster differs')
        for i in present:_number(available[i],0,1,'available time');_number(cooperation[i],0,1,'cooperation')
        self._enroll(active)
        links=topology(active,self.c,None if self.structure is None else self.structure['assignments']);present=set(present)
        fixed={};choices={};quality={};learning={};production={};help_input={};readiness={}
        self.review_remaining={}
        for i in sorted(present):
            key=str(i);depth=links['reporting_depth'][key]
            peers=[j for j in links['collaboration_peers'][key] if j in present]
            request=depth*self.c.reporting_hours_per_layer+len(peers)*self.c.collaboration_hours_per_contact+self.c.verification_hours_per_member_workday
            reorganization=0.
            if self.structure is not None:
                reorganization=min(available[i],self.structure['training_remaining'].get(key,0.))
                remaining=max(0.,self.structure['training_remaining'].get(key,0.)-reorganization*funding)
                if key in self.structure['training_remaining']:self.structure['training_remaining'][key]=remaining
            network=min(max(0.,available[i]-reorganization),request)
            fixed[i]=network+reorganization
            residual=max(0.,available[i]-fixed[i])
            objectives=self.c.department_objectives[self.department(i)]
            production[i]=residual*objectives[0];quality[i]=residual*objectives[1];learning[i]=residual*objectives[2]
            choices[key]={'available_hours':available[i],'requested_network_hours':request,'network_hours':fixed[i],
                'production_cap_hours':production[i],'quality_hours':quality[i],'learning_hours':learning[i]}
            if self.structure is not None:
                choices[key]['reorganization_hours']=reorganization
                choices[key]['remaining_reorganization_hours']=remaining
            help_input[i]=math.fsum(cooperation.get(j,0.) for j in peers)/len(peers) if peers else 0.
            readiness[i]=1/(1+self.c.reporting_readiness_penalty_per_layer*depth)
            # Network work serves reporting/collaboration first; only residual funds verification.
            verification=max(0.,network-depth*self.c.reporting_hours_per_layer-len(peers)*self.c.collaboration_hours_per_contact)*funding
            self.review_remaining[key]=int((verification+1e-12)/self.c.verification_hours_per_claim)
        self.last_plan={'day':day,'topology':links,'present_people':sorted(present),'funding_fraction':funding,'cooperation_by_person':{str(i):v for i,v in sorted(cooperation.items())},'allocations':choices}
        return fixed,quality,learning,production,help_input,readiness,copy.deepcopy(self.last_plan)

    def task_allocation(self,potentials):
        """Only morning inventory can enable work; no same-day circular bootstrap."""
        if self.last_plan is None or self.last_plan['day']!=self.completed_days or self.last_task is not None:raise ValueError('organization task phase is not sequential')
        if not set(potentials)<=set(self.last_plan['present_people']):raise ValueError('organization task actor not present')
        for v in potentials.values():_number(v,0,1e300,'task potential')
        parts={str(d):[] for d in range(self.c.department_count)}
        for i,v in potentials.items():parts[str(self.department(i))].append(v)
        totals={d:math.fsum(v) for d,v in parts.items()}
        ratio=self.c.dependency_input_units_per_output_work_unit
        if self.structure is None:
            factors={str(d):(1. if d==0 or ratio==0 or totals[str(d)]==0 else min(1.,self.stocks[str(d-1)]/(ratio*totals[str(d)]))) for d in range(self.c.department_count)}
        else:
            families=self.structure['task_families'];families_n=self.base_c.department_count
            demand={f:math.fsum(totals[d] for d in totals if families[d]==f) for f in range(families_n)}
            pools={f:math.fsum(self.stocks[d] for d in self.stocks if families[d]==f) for f in range(families_n)}
            factor={f:1. if f==0 or ratio==0 or demand[f]==0 else min(1.,pools[f-1]/(ratio*demand[f])) for f in range(families_n)}
            factors={d:factor[families[d]] for d in totals}
        self.last_task={'morning_stocks':dict(self.stocks),'potential_by_department':totals,'dependency_factors':factors}
        if self.structure is not None:self.last_task['task_families']=dict(self.structure['task_families'])
        return {i:v*factors[str(self.department(i))] for i,v in potentials.items()}

    def finish(self,day,produced):
        if type(day) is not int or day!=self.completed_days or self.last_plan is None or self.last_plan['day']!=day:raise ValueError('organization close phase is not sequential')
        if not set(produced)<=set(self.last_plan['present_people']):raise ValueError('organization task output actor not present')
        for v in produced.values():_number(v,0,1e300,'task output')
        if self.last_task is None:self.task_allocation({})
        parts={str(d):[] for d in range(self.c.department_count)}
        for i,v in produced.items():parts[str(self.department(i))].append(v)
        totals={d:math.fsum(v) for d,v in parts.items()}
        if self.structure is None:
            used={str(d):(totals[str(d+1)]*self.c.dependency_input_units_per_output_work_unit if d+1<self.c.department_count else 0.) for d in range(self.c.department_count)}
        else:
            # Administrative splits inherit technology. Downstream family work
            # draws proportionally from the shared upstream MORNING pool;
            # no new production stage or same-day deliverable is introduced.
            families=self.structure['task_families'];used={d:0. for d in totals}
            for f in range(self.base_c.department_count-1):
                donors=sorted(d for d in totals if families[d]==f)
                pool=math.fsum(self.stocks[d] for d in donors)
                required=math.fsum(totals[d] for d in totals if families[d]==f+1)*self.c.dependency_input_units_per_output_work_unit
                for d in donors:used[d]=required*self.stocks[d]/pool if pool else 0.
        for key,total in totals.items():
            if total>self.last_task['potential_by_department'][key]*self.last_task['dependency_factors'][key]+1e-8:raise ValueError('organization task output exceeds planned potential')
        before=dict(self.stocks)
        for key in self.stocks:
            if used[key]>before[key]+1e-8:raise ValueError('dependent tasks exceed previous-day deliverable inventory')
            self.stocks[key]=max(0.,before[key]+totals[key]-used[key]);self.generated[key]+=totals[key];self.consumed[key]+=used[key]
        self.completed_days=day+1
        record={'day':day,**self.last_task,'produced_by_department':totals,'consumed_input_units':used,'closing_stocks':dict(self.stocks)}
        self.last_task=None
        return record

    def reviewer(self,claimant,present):
        candidates=self.last_plan['topology']['verification_candidates'].get(str(claimant),[])
        # A departed claimant's retained work can be reviewed by a present outsider.
        if str(claimant) not in self.last_plan['topology']['verification_candidates']:
            candidates=[i for i in sorted(present) if i!=claimant and self.department(i)!=self.department(claimant)]
        for i in candidates:
            if i in present and self.review_remaining.get(str(i),0)>0:
                self.review_remaining[str(i)]-=1;return i
        return None

    def to_dict(self):
        value={'schema_version':self.base_c.schema_version,'completed_days':self.completed_days,'stocks':dict(self.stocks),
                'generated':dict(self.generated),'consumed':dict(self.consumed),'last_plan':copy.deepcopy(self.last_plan),
                'review_remaining':dict(self.review_remaining)}
        if self.structure is not None:value['structure']=copy.deepcopy(self.structure)
        return value

    def validate(self,day):
        if type(day) is not int or day<0 or type(self.completed_days) is not int or self.completed_days!=day:raise ValueError('organization days differ')
        if self.structure is not None:
            s=self.structure
            if set(s)!={'department_count','observed_installed_departments','initial_units','objectives','task_families','assignments','training_remaining','pending','history','replans'}:
                raise ValueError('dynamic task structure schema differs')
            if type(s['department_count']) is not int or s['department_count']!=self.c.department_count or not self.base_c.department_count<=s['department_count']<=10000:
                raise ValueError('dynamic task count differs')
            self.c.validate()
            if type(s['observed_installed_departments']) is not int or s['observed_installed_departments']<self.growth.initial_departments:
                raise ValueError('installed task provenance differs')
            if set(s['initial_units'])!={str(d) for d in range(self.c.department_count)}:
                raise ValueError('dynamic initial knowledge roster differs')
            for v in s['initial_units'].values():_number(v,0,1e300,'dynamic initial knowledge')
            if set(s['task_families'])!=set(s['initial_units']) or any(type(f) is not int or not 0<=f<self.base_c.department_count for f in s['task_families'].values()):
                raise ValueError('administrative split changed task technology families')
            for d in range(self.base_c.department_count):
                if s['task_families'][str(d)]!=d:raise ValueError('original task family identity changed')
            if not math.isclose(math.fsum(s['initial_units'].values()),self.base_c.department_count*self.base_c.initial_dependency_units_per_department,rel_tol=1e-12,abs_tol=1e-8):
                raise ValueError('task restructuring created initial knowledge')
            for token,department in s['assignments'].items():
                if not isinstance(token,str) or not token.isdecimal() or str(int(token))!=token or type(department) is not int or not 0<=department<self.c.department_count:
                    raise ValueError('dynamic task person assignment differs')
            if not set(s['training_remaining'])<=set(s['assignments']):raise ValueError('reorganization training person absent')
            for v in s['training_remaining'].values():_number(v,0,1e300,'reorganization training obligation')
            previous=self.c.department_count
            for q in s['pending']:
                if (q['previous_department_count']!=previous or type(q['target_department_count']) is not int
                        or not previous<q['target_department_count']<=10000
                        or q['ready_day']!=q['completed_installation_day']+self.growth.hiring_delay_days
                        or not 0<=q['completed_installation_day']<day):raise ValueError('task restructuring pending queue differs')
                previous=q['target_department_count']
            if previous!=self.base_c.department_count+s['observed_installed_departments']-self.growth.initial_departments:
                raise ValueError('task count is not backed by installed investment')
            for record in s['history']:
                if not 0<=record['day']<day or record['day']<record['ready_day']:
                    raise ValueError('task remapping precedes its integration delay')
            for record in s['replans']:
                if not 0<=record['day']<day or record['replan_not_before']!=record['day']+self.growth.hiring_delay_days:
                    raise ValueError('task replanning precedes its new integration delay')
                for k,v in record['served_work_hours_by_person'].items():
                    _number(v,0,self.reorganization_hours,'sunk reorganization time')
                    if not math.isclose(v+record['cancelled_unserved_hours_by_person'][k],self.reorganization_hours,abs_tol=1e-12):raise ValueError('replanning lost unpaid or sunk obligations')
        keys={str(d) for d in range(self.c.department_count)}
        for mapping in (self.stocks,self.generated,self.consumed):
            if set(mapping)!=keys:raise ValueError('organization inventory roster differs')
            for value in mapping.values():_number(value,0,1e300,'organization inventory')
        for key in keys:
            initial=self.c.initial_dependency_units_per_department if self.structure is None else self.structure['initial_units'][key]
            # One update per day of stock, generated and consumed: the rounding of the
            # three running sums is bounded by about 3*(day+2)*2**-53 of the gross flow.
            gross=initial+self.generated[key]+self.consumed[key]
            if abs(self.stocks[key]-(initial+self.generated[key]-self.consumed[key]))>3*(day+2)*2.**-53*gross+1e-8:raise ValueError('organization dependency inventory does not conserve')
        if day==0 and (any(v!=0 for v in self.generated.values()) or any(v!=0 for v in self.consumed.values()) or any(v!=self.c.initial_dependency_units_per_department for v in self.stocks.values())):raise ValueError('organization initial inventory differs from configuration')
        if self.last_plan is None:
            if day!=0 or self.review_remaining:raise ValueError('organization plan missing')
        else:
            if type(self.last_plan['day']) is not int or self.last_plan['day']!=day-1:raise ValueError('organization retained plan day differs')
            if self.last_plan['topology']!=topology(self.last_plan['topology']['active_people'],self.c,None if self.structure is None else self.structure['assignments']):raise ValueError('organization topology differs from active roster')
            present=self.last_plan['present_people']
            if present!=sorted(set(present)) or not set(present)<=set(self.last_plan['topology']['active_people']):raise ValueError('organization present roster differs')
            _number(self.last_plan['funding_fraction'],0,1,'organization funding')
            if set(self.last_plan['allocations'])!={str(i) for i in present}:raise ValueError('organization allocation roster differs')
            for i in present:
                key=str(i);a=self.last_plan['allocations'][key];links=self.last_plan['topology']
                available=a['available_hours'];_number(available,0,1,'organization available capacity')
                peers=[j for j in links['collaboration_peers'][key] if j in present]
                request=links['reporting_depth'][key]*self.c.reporting_hours_per_layer+len(peers)*self.c.collaboration_hours_per_contact+self.c.verification_hours_per_member_workday
                reorganization=a.get('reorganization_hours',0.) if self.structure is not None else 0.
                _number(reorganization,0,available,'reorganization paid reservation')
                network=min(available-reorganization,request);fixed=network+reorganization
                expected={'requested_network_hours':request,'network_hours':fixed,**dict(zip(('production_cap_hours','quality_hours','learning_hours'),((available-fixed)*w for w in self.c.department_objectives[self.department(i)])))}
                if self.structure is not None:
                    remaining=a['remaining_reorganization_hours'];_number(remaining,0,1e300,'remaining reorganization time')
                    expected.update(reorganization_hours=reorganization,remaining_reorganization_hours=remaining)
                if set(a)!={'available_hours'}|set(expected) or any(not isinstance(a[k],(int,float)) or isinstance(a[k],bool) or not math.isfinite(a[k]) or not math.isclose(a[k],v,rel_tol=1e-12,abs_tol=1e-12) for k,v in expected.items()):raise ValueError('organization allocation arithmetic differs')
                verification=max(0.,network-links['reporting_depth'][key]*self.c.reporting_hours_per_layer-len(peers)*self.c.collaboration_hours_per_contact)*self.last_plan['funding_fraction']
                if self.review_remaining.get(key,0)>int((verification+1e-12)/self.c.verification_hours_per_claim):raise ValueError('organization retained review quota exceeds paid capacity')
            if set(self.review_remaining)!={str(i) for i in self.last_plan['present_people']}:raise ValueError('organization reviewer capacity roster differs')
            if any(type(v) is not int or v<0 for v in self.review_remaining.values()):raise ValueError('organization reviewer capacity invalid')

    @classmethod
    def restore(cls,outer,value,day):
        obj=cls(outer)
        if not isinstance(value,dict) or set(value)!=set(obj.to_dict()) or type(value['schema_version']) is not int or value['schema_version']!=obj.base_c.schema_version:raise ValueError('organization state schema differs')
        if obj.structure is not None:
            obj.structure=copy.deepcopy(value['structure'])
            obj.c=dataclasses.replace(obj.base_c,department_count=obj.structure['department_count'],department_objectives=tuple(tuple(w) for w in obj.structure['objectives']))
        for key in ('completed_days','stocks','generated','consumed','last_plan','review_remaining'):setattr(obj,key,copy.deepcopy(value[key]))
        obj.validate(day);return obj
