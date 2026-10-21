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
        for key, low, high in (('schema_version',1,1),('department_count',1,10000),('management_span',1,1000),
                               ('collaboration_contacts',0,16),('verification_contacts',1,16),('reporting_delay_days_per_layer',0,365)):
            v=getattr(self,key)
            if type(v) is not int or not low<=v<=high:raise ValueError(key+' must be a bounded integer')
        if outer is not None:
            if outer.longitudinal is None:raise ValueError('organization_tasks requires the longitudinal engine')
            if self.department_count>outer.n:raise ValueError('task departments exceed initial people')
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


def topology(ids, config):
    """Rebuild from the actual active roster after entries/exits/M&A, no RNG."""
    ids=sorted(ids)
    if len(ids)!=len(set(ids)) or any(type(i) is not int or i<0 for i in ids):raise ValueError('organization active identities must be distinct nonnegative integers')
    groups={d:[] for d in range(config.department_count)}
    for i in ids:groups[i%config.department_count].append(i)
    reporting={};depth={};collaboration={};verification={}
    for group in groups.values():
        for rank,i in enumerate(group):
            parent=None if rank==0 else group[(rank-1)//config.management_span]
            reporting[str(i)]=parent;depth[str(i)]=0 if parent is None else depth[str(parent)]+1
    nonempty=[d for d in groups if groups[d]];positions={d:k for k,d in enumerate(nonempty)}
    def contacts(i,count,independent=False):
        found=[];d=i%config.department_count
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
        self.completed_days=0
        self.stocks={str(d):self.c.initial_dependency_units_per_department for d in range(self.c.department_count)}
        self.generated={str(d):0. for d in range(self.c.department_count)}
        self.consumed={str(d):0. for d in range(self.c.department_count)}
        self.last_plan=None
        self.last_task=None
        self.review_remaining={}

    def prepare(self,day,active,present,available,cooperation,*,funding):
        if type(day) is not int or day!=self.completed_days or self.last_plan is not None and self.last_plan['day']>=day:raise ValueError('organization plan day is not sequential')
        _number(funding,0,1,'funding fraction')
        if len(present)!=len(set(present)) or not set(present)<=set(active):raise ValueError('organization present roster not active')
        if set(available)!=set(present) or set(cooperation)!=set(present):raise ValueError('organization planning input roster differs')
        for i in present:_number(available[i],0,1,'available time');_number(cooperation[i],0,1,'cooperation')
        links=topology(active,self.c);present=set(present)
        fixed={};choices={};quality={};learning={};production={};help_input={};readiness={}
        self.review_remaining={}
        for i in sorted(present):
            key=str(i);depth=links['reporting_depth'][key]
            peers=[j for j in links['collaboration_peers'][key] if j in present]
            request=depth*self.c.reporting_hours_per_layer+len(peers)*self.c.collaboration_hours_per_contact+self.c.verification_hours_per_member_workday
            fixed[i]=min(max(0.,available[i]),request)
            residual=max(0.,available[i]-fixed[i])
            objectives=self.c.department_objectives[i%self.c.department_count]
            production[i]=residual*objectives[0];quality[i]=residual*objectives[1];learning[i]=residual*objectives[2]
            choices[key]={'available_hours':available[i],'requested_network_hours':request,'network_hours':fixed[i],
                'production_cap_hours':production[i],'quality_hours':quality[i],'learning_hours':learning[i]}
            help_input[i]=math.fsum(cooperation.get(j,0.) for j in peers)/len(peers) if peers else 0.
            readiness[i]=1/(1+self.c.reporting_readiness_penalty_per_layer*depth)
            # Network work serves reporting/collaboration first; only residual funds verification.
            verification=max(0.,fixed[i]-depth*self.c.reporting_hours_per_layer-len(peers)*self.c.collaboration_hours_per_contact)*funding
            self.review_remaining[key]=int((verification+1e-12)/self.c.verification_hours_per_claim)
        self.last_plan={'day':day,'topology':links,'present_people':sorted(present),'funding_fraction':funding,'cooperation_by_person':{str(i):v for i,v in sorted(cooperation.items())},'allocations':choices}
        return fixed,quality,learning,production,help_input,readiness,copy.deepcopy(self.last_plan)

    def task_allocation(self,potentials):
        """Only morning inventory can enable work; no same-day circular bootstrap."""
        if self.last_plan is None or self.last_plan['day']!=self.completed_days or self.last_task is not None:raise ValueError('organization task phase is not sequential')
        if not set(potentials)<=set(self.last_plan['present_people']):raise ValueError('organization task actor not present')
        for v in potentials.values():_number(v,0,1e300,'task potential')
        parts={str(d):[] for d in range(self.c.department_count)}
        for i,v in potentials.items():parts[str(i%self.c.department_count)].append(v)
        totals={d:math.fsum(v) for d,v in parts.items()}
        ratio=self.c.dependency_input_units_per_output_work_unit
        factors={str(d):(1. if d==0 or ratio==0 or totals[str(d)]==0 else min(1.,self.stocks[str(d-1)]/(ratio*totals[str(d)]))) for d in range(self.c.department_count)}
        self.last_task={'morning_stocks':dict(self.stocks),'potential_by_department':totals,'dependency_factors':factors}
        return {i:v*factors[str(i%self.c.department_count)] for i,v in potentials.items()}

    def finish(self,day,produced):
        if type(day) is not int or day!=self.completed_days or self.last_plan is None or self.last_plan['day']!=day:raise ValueError('organization close phase is not sequential')
        if not set(produced)<=set(self.last_plan['present_people']):raise ValueError('organization task output actor not present')
        for v in produced.values():_number(v,0,1e300,'task output')
        if self.last_task is None:self.task_allocation({})
        parts={str(d):[] for d in range(self.c.department_count)}
        for i,v in produced.items():parts[str(i%self.c.department_count)].append(v)
        totals={d:math.fsum(v) for d,v in parts.items()}
        used={str(d):(totals[str(d+1)]*self.c.dependency_input_units_per_output_work_unit if d+1<self.c.department_count else 0.) for d in range(self.c.department_count)}
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
            candidates=[i for i in sorted(present) if i!=claimant and i%self.c.department_count!=claimant%self.c.department_count]
        for i in candidates:
            if i in present and self.review_remaining.get(str(i),0)>0:
                self.review_remaining[str(i)]-=1;return i
        return None

    def to_dict(self):
        return {'schema_version':1,'completed_days':self.completed_days,'stocks':dict(self.stocks),
                'generated':dict(self.generated),'consumed':dict(self.consumed),'last_plan':copy.deepcopy(self.last_plan),
                'review_remaining':dict(self.review_remaining)}

    def validate(self,day):
        if type(day) is not int or day<0 or type(self.completed_days) is not int or self.completed_days!=day:raise ValueError('organization days differ')
        keys={str(d) for d in range(self.c.department_count)}
        for mapping in (self.stocks,self.generated,self.consumed):
            if set(mapping)!=keys:raise ValueError('organization inventory roster differs')
            for value in mapping.values():_number(value,0,1e300,'organization inventory')
        for key in keys:
            if not math.isclose(self.stocks[key],self.c.initial_dependency_units_per_department+self.generated[key]-self.consumed[key],rel_tol=1e-11,abs_tol=1e-8):raise ValueError('organization dependency inventory does not conserve')
        if day==0 and (any(v!=0 for v in self.generated.values()) or any(v!=0 for v in self.consumed.values()) or any(v!=self.c.initial_dependency_units_per_department for v in self.stocks.values())):raise ValueError('organization initial inventory differs from configuration')
        if self.last_plan is None:
            if day!=0 or self.review_remaining:raise ValueError('organization plan missing')
        else:
            if type(self.last_plan['day']) is not int or self.last_plan['day']!=day-1:raise ValueError('organization retained plan day differs')
            if self.last_plan['topology']!=topology(self.last_plan['topology']['active_people'],self.c):raise ValueError('organization topology differs from active roster')
            present=self.last_plan['present_people']
            if present!=sorted(set(present)) or not set(present)<=set(self.last_plan['topology']['active_people']):raise ValueError('organization present roster differs')
            _number(self.last_plan['funding_fraction'],0,1,'organization funding')
            if set(self.last_plan['allocations'])!={str(i) for i in present}:raise ValueError('organization allocation roster differs')
            for i in present:
                key=str(i);a=self.last_plan['allocations'][key];links=self.last_plan['topology']
                available=a['available_hours'];_number(available,0,1,'organization available capacity')
                peers=[j for j in links['collaboration_peers'][key] if j in present]
                request=links['reporting_depth'][key]*self.c.reporting_hours_per_layer+len(peers)*self.c.collaboration_hours_per_contact+self.c.verification_hours_per_member_workday
                fixed=min(available,request)
                expected={'requested_network_hours':request,'network_hours':fixed,**dict(zip(('production_cap_hours','quality_hours','learning_hours'),((available-fixed)*w for w in self.c.department_objectives[i%self.c.department_count])))}
                if set(a)!={'available_hours'}|set(expected) or any(not isinstance(a[k],(int,float)) or isinstance(a[k],bool) or not math.isfinite(a[k]) or not math.isclose(a[k],v,rel_tol=1e-12,abs_tol=1e-12) for k,v in expected.items()):raise ValueError('organization allocation arithmetic differs')
                verification=max(0.,fixed-links['reporting_depth'][key]*self.c.reporting_hours_per_layer-len(peers)*self.c.collaboration_hours_per_contact)*self.last_plan['funding_fraction']
                if self.review_remaining.get(key,0)>int((verification+1e-12)/self.c.verification_hours_per_claim):raise ValueError('organization retained review quota exceeds paid capacity')
            if set(self.review_remaining)!={str(i) for i in self.last_plan['present_people']}:raise ValueError('organization reviewer capacity roster differs')
            if any(type(v) is not int or v<0 for v in self.review_remaining.values()):raise ValueError('organization reviewer capacity invalid')

    @classmethod
    def restore(cls,outer,value,day):
        obj=cls(outer)
        if not isinstance(value,dict) or set(value)!=set(obj.to_dict()) or type(value['schema_version']) is not int or value['schema_version']!=1:raise ValueError('organization state schema differs')
        for key in ('completed_days','stocks','generated','consumed','last_plan','review_remaining'):setattr(obj,key,copy.deepcopy(value[key]))
        obj.validate(day);return obj
