"""Opt-in synthetic acquisition transactions; never empirical M&A calibration.

An offer is a separately identified conditional target state, not a generated
future workforce curve. Parent decisions use realised past margin and contracts.
Buying a target changes ownership and financial liabilities, not usable labour
or capacity. Paid workday integration precedes resource and employee transfer.
Original evidence and incompatible contribution units remain in a quarantine
inventory; neither a purchase nor a guild alias grants historical authority.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, fields, asdict
from datetime import date
import math
import re

VERSION = 'longitudinal-enterprise-integrated-6'


def number(value, label, low=0., high=1e12):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('invalid acquisition '+label)
    return value


def identifier(value):
    return isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',value) is not None


@dataclass(frozen=True)
class TargetEmployee:
    key: str
    birth_date: str = '1990-01-01'
    skill: float = 1.
    tenure: float = 1.
    care_hours: float = .1
    autonomy: float = .5
    reciprocity: float = .5
    learning: float = .2
    trust: float = .7
    fatigue: float = .1


@dataclass(frozen=True)
class TargetEvidence:
    event_id: str
    person_key: str
    amount: float
    unit: str
    evidence_quality: float
    # Preserve source meaning, do not represent the source score as our credit.
    origin_day: int = 0


@dataclass(frozen=True)
class AcquisitionTarget:
    key: str
    seller: str
    available_day: int
    expires_day: int
    employees: tuple[TargetEmployee, ...]
    installed_seats: int
    guilds: int
    departments: int
    sites: int
    cash_resource_units: float = 0.
    financial_debt_resource_units: float = 0.
    consideration_resource_units: float = 10.
    historical_age_years: float = 1.
    organizational_knowledge: float = .2
    future_orders_per_workday: float = 0.
    prior_regime: str = 'hierarchy'
    prior_backend: str = 'central'
    evidence: tuple[TargetEvidence, ...] = ()
    pending_evidence: tuple[TargetEvidence, ...] = ()
    # A finite immutable source capsule, with an explicit unit and provenance.
    history: tuple[tuple[str, str, float], ...] = ()


@dataclass(frozen=True)
class EnterpriseAcquisitionConfig:
    schema_version: int = 1
    targets: tuple[AcquisitionTarget, ...] = ()
    integration_mode: str = 'preserve-autonomy'
    minimum_past_margin: float = .1
    require_unmet_orders: bool = True
    reserve_workdays: float = 10.
    transaction_resource_units: float = 1.
    integration_workdays: int = 10
    integration_resource_units_per_employee_workday: float = .1
    integration_payroll_resource_units_per_employee_workday: float = .5
    integration_exit_probability_per_workday: float = 0.
    maximum_unfunded_workdays: int = 5
    maximum_consolidated_debt_resource_units: float = 0.
    maximum_acquisition_financing_resource_units: float = 0.
    centralization_learning_retention: float = .8
    autonomy_coordination_workday_units: float = .05
    centralized_coordination_workday_units: float = .01
    evidence_resolution_workdays: int = 5
    evidence_review_resource_units_per_record: float = .1

    @classmethod
    def from_dict(cls,value):
        if isinstance(value,cls):return value
        if not isinstance(value,dict) or set(value)-{f.name for f in fields(cls)}:
            raise ValueError('unknown acquisition configuration')
        value=copy.deepcopy(value)
        if 'targets' in value:
            targets=[]
            for row in value['targets']:
                row=dict(row)
                row['employees']=tuple(TargetEmployee(**v) for v in row['employees'])
                for key in ('evidence','pending_evidence'):
                    if key in row:row[key]=tuple(TargetEvidence(**v) for v in row[key])
                if 'history' in row:row['history']=tuple(tuple(v) for v in row['history'])
                targets.append(AcquisitionTarget(**row))
            value['targets']=tuple(targets)
        return cls(**value)

    def validate(self,config):
        if config.enterprise_operations is None or config.enterprise_growth is None:
            raise ValueError('acquisitions require explicit causal operations and growth')
        if type(self.schema_version) is not int or self.schema_version!=1:
            raise ValueError('unknown acquisition schema')
        if self.integration_mode not in ('preserve-autonomy','centralize') or type(self.require_unmet_orders) is not bool:
            raise ValueError('invalid acquisition integration/decision mode')
        if not isinstance(self.targets,tuple) or len(self.targets)>100 or any(not isinstance(t,AcquisitionTarget) for t in self.targets):
            raise ValueError('acquisition targets must be a bounded typed roster')
        if len({t.key for t in self.targets})!=len(self.targets):raise ValueError('target identity repeated')
        for f in fields(self):
            if f.name in ('schema_version','targets','integration_mode','require_unmet_orders'):continue
            v=getattr(self,f.name)
            if f.name in ('integration_workdays','maximum_unfunded_workdays','evidence_resolution_workdays'):
                if type(v) is not int or not 1<=v<=100000:raise ValueError('invalid acquisition delay')
            else:number(v,f.name,low=-1. if f.name=='minimum_past_margin' else 0.)
        if not -1<=self.minimum_past_margin<=1 or self.reserve_workdays<=0:
            raise ValueError('invalid acquisition margin/reserve')
        if self.transaction_resource_units<=0 or self.integration_resource_units_per_employee_workday<=0:
            raise ValueError('acquisition and integration require nonzero resource costs')
        for key in ('integration_exit_probability_per_workday','centralization_learning_retention'):
            if getattr(self,key)>1:raise ValueError('invalid acquisition probability/retention')
        if max(self.autonomy_coordination_workday_units,self.centralized_coordination_workday_units)>.2:
            raise ValueError('acquisition coordination exceeds declared workday fraction')
        g=config.enterprise_growth;l=config.longitudinal
        for t in self.targets:
            if not identifier(t.key) or not identifier(t.seller) or t.seller=='parent':raise ValueError('invalid target/seller identity')
            if type(t.available_day) is not int or type(t.expires_day) is not int or not 0<=t.available_day<t.expires_day<=config.days:
                raise ValueError('target availability outside calendar horizon')
            if not t.employees or len(t.employees)>l.max_active_members or len({p.key for p in t.employees})!=len(t.employees):
                raise ValueError('target employee identity roster invalid')
            for name in ('installed_seats','guilds','departments','sites'):
                if type(getattr(t,name)) is not int or not 1<=getattr(t,name)<=100000000:raise ValueError('target infrastructure invalid')
            if not len(t.employees)<=t.installed_seats<=g.maximum_organization_capacity:
                raise ValueError('target employee/seat resource capacity differs')
            if t.installed_seats>t.guilds*g.members_per_guild or t.installed_seats>t.sites*g.members_per_site or t.guilds>t.departments*g.guilds_per_department:
                raise ValueError('target installed infrastructure overbooked')
            for name in ('cash_resource_units','financial_debt_resource_units','consideration_resource_units','historical_age_years','organizational_knowledge','future_orders_per_workday'):
                number(getattr(t,name),'target '+name)
            if t.consideration_resource_units<=0 or t.organizational_knowledge>2 or t.historical_age_years>500:
                raise ValueError('invalid target consideration/knowledge/history')
            if t.prior_regime not in {'equal','linear','sublinear','hierarchy','hierarchy_tenure'} or t.prior_backend not in {'central','witness','consensus'}:
                raise ValueError('invalid target prior governance')
            for p in t.employees:
                if not isinstance(p,TargetEmployee) or not identifier(p.key):raise ValueError('invalid target employee')
                birth=date.fromisoformat(p.birth_date)
                if birth>=date.fromisoformat(l.calendar_start):raise ValueError('target birth follows founding calendar')
                for name in ('skill','tenure','care_hours','autonomy','reciprocity','learning','trust','fatigue'):
                    number(getattr(p,name),'target person '+name)
                if p.skill<=0 or p.care_hours>.3 or p.learning>2 or max(p.autonomy,p.reciprocity,p.trust,p.fatigue)>1:
                    raise ValueError('target person behavioral bounds differ')
            all_evidence=t.evidence+t.pending_evidence
            if len(all_evidence)>10000 or len({v.event_id for v in all_evidence})!=len(all_evidence):raise ValueError('target evidence event replayed')
            for v in all_evidence:
                if not isinstance(v,TargetEvidence) or not identifier(v.event_id) or v.person_key not in {p.key for p in t.employees} or not identifier(v.unit):raise ValueError('target evidence identity/unit invalid')
                number(v.amount,'target score');number(v.evidence_quality,'target evidence quality',high=1.)
                if type(v.origin_day) is not int or not 0<=v.origin_day<=t.available_day:raise ValueError('target evidence date invalid')
            if len(t.history)>1000 or any(len(v)!=3 or not identifier(v[0]) or not identifier(v[1]) for v in t.history):raise ValueError('target history source/unit invalid')
            for v in t.history:number(v[2],'target historical observable')
        # Bound additional demand under the original worst-case price/shock
        # envelope by reusing its configuration validation, never clipping data.
        from dataclasses import replace
        replace(config.enterprise_operations,base_orders_per_workday=config.enterprise_operations.base_orders_per_workday+math.fsum(t.future_orders_per_workday for t in self.targets)).validate(config)
        return self


class EnterpriseAcquisitionState:
    def __init__(self,config):
        self.config=config
        self.c=config.enterprise_acquisition
        self.targets={t.key:{'owner':t.seller,'status':'available','closed_day':None,
            'integration_workdays':0,'unfunded_workdays':0,'ready_day':None,
            'employee_status':{p.key:'target' for p in t.employees},'employee_ids':{},
            'retained_evidence':list(asdict(t)['evidence']),'pending_evidence':list(asdict(t)['pending_evidence']),
            'resolved_evidence':[],'evidence_workdays':0,'guilds':[],
            'transferred_cash':0.,'assumed_debt':0.,'consideration_paid':0.,'financing':0.,
            'transaction_paid':0.,'integration_paid':0.,'evidence_paid':0.} for t in self.c.targets}
        for t in self.c.targets:
            self.targets[t.key].update(target_cash_remaining=t.cash_resource_units,seller_consideration_received=0.)
        self.last_day=-1

    def to_dict(self):return copy.deepcopy({'targets':self.targets,'last_day':self.last_day})

    def demand_addition(self):
        return math.fsum(t.future_orders_per_workday for t in self.c.targets if self.targets[t.key]['status']=='integrated')

    def policy(self,guild):
        if self.c.integration_mode!='preserve-autonomy':return None
        for t in self.c.targets:
            s=self.targets[t.key]
            if s['status']=='integrated' and guild in s['guilds']:return t.prior_regime,t.prior_backend
        return None

    def coordination(self,person):
        if not any(person in s['employee_ids'].values() for s in self.targets.values()):return 0.
        return self.c.autonomy_coordination_workday_units if self.c.integration_mode=='preserve-autonomy' else self.c.centralized_coordination_workday_units

    def begin_day(self,day,*,working,operating,cash,growth,debt,ever_people,active,tokens,people_cap,unmet_orders,exit_draw):
        """Plan against copies: any refusal leaves every target state unchanged.

        Effects are committed to the engine inside its existing daily SQLite
        transaction. A later failed day can only resume from its last checkpoint.
        Acquired employees are held in the target inventory until paid integration.
        """
        if type(day) is not int or day!=self.last_day+1 or type(working) is not bool or type(operating) is not bool:
            raise ValueError('acquisition calendar day replayed or skipped')
        number(cash,'cash',high=math.inf);number(debt,'debt',high=math.inf)
        if growth.last_observed_day is not None and growth.last_observed_day>=day:
            raise ValueError('acquisition decision observes present or future margin')
        before=self.to_dict();events=[];effects={'cash_delta':0.,'financing':0.,'assumed_debt':0.,'target_cash':0.,'consideration':0.,'transaction_cost':0.,'integration_cost':0.,'evidence_cost':0.,'transfers':[]}
        try:
            for t in self.c.targets:
                s=self.targets[t.key];available_cash=cash+effects['cash_delta'];c=self.c
                if s['status']=='available' and day>=t.expires_day:s['status']='expired'
                if s['status']=='available' and t.available_day<=day<t.expires_day and working and operating and not effects['transfers'] and not effects['consideration'] and not any(v['status']=='integrating' for v in self.targets.values()):
                    margin=(growth.revenue_signal-growth.cost_signal)/max(growth.revenue_signal,growth.cost_signal,1e-12)
                    capacity=growth.capacity+t.installed_seats
                    inventory={'capacity':capacity,'guilds':growth.guilds+t.guilds,'departments':growth.departments+t.departments,'sites':growth.sites+t.sites,'target':growth.target+len(t.employees)}
                    from .enterprise_growth import validate_installed_inventory
                    try:validate_installed_inventory(self.config,**inventory)
                    except ValueError as error:
                        events.append(('acquisition_inventory_refused',{'target':t.key,'reason':str(error),'proposed_installed_inventory':inventory,'money_paid':0.,'ownership_changed':False}))
                        continue
                    if (not growth.observed_workdays or margin<c.minimum_past_margin or growth.pending_construction is not None or growth.pending_hiring is not None
                        or ever_people+len(t.employees)>people_cap
                        or len(set(tokens)|{f'acquired:{t.key}:{p.key}' for p in t.employees})!=len(set(tokens))+len(t.employees)):
                        continue
                    if c.require_unmet_orders and not unmet_orders:continue
                    # Full paid integration and payroll exposure reserved before
                    # closing. Target treasury is not spendable before ownership.
                    reserve=max(growth.cost_signal,growth.target)*c.reserve_workdays
                    reserve+=len(t.employees)*c.integration_workdays*(c.integration_resource_units_per_employee_workday+c.integration_payroll_resource_units_per_employee_workday)
                    needed=t.consideration_resource_units+c.transaction_resource_units+reserve
                    draw=max(0.,needed-available_cash)
                    if draw>c.maximum_acquisition_financing_resource_units or debt+effects['financing']+effects['assumed_debt']+t.financial_debt_resource_units+draw>c.maximum_consolidated_debt_resource_units:
                        continue
                    s.update(owner='parent',status='integrating',closed_day=day,
                        transferred_cash=t.cash_resource_units,assumed_debt=t.financial_debt_resource_units,
                        consideration_paid=t.consideration_resource_units,financing=draw,transaction_paid=c.transaction_resource_units,
                        target_cash_remaining=0.,seller_consideration_received=t.consideration_resource_units)
                    effects['cash_delta']+=draw+t.cash_resource_units-t.consideration_resource_units-c.transaction_resource_units
                    for key,value in (('financing',draw),('assumed_debt',t.financial_debt_resource_units),('target_cash',t.cash_resource_units),('consideration',t.consideration_resource_units),('transaction_cost',c.transaction_resource_units)):effects[key]+=value
                    events.append(('acquisition_close',{'target':t.key,'seller':t.seller,'owner_after':'parent','cash_received':t.cash_resource_units,'debt_assumed':t.financial_debt_resource_units,'financing':draw,'consideration':t.consideration_resource_units,'transaction_cost':c.transaction_resource_units,'reserve':reserve,'past_margin':margin,'employees':len(t.employees),'installed_assets':{'seats':t.installed_seats,'guilds':t.guilds,'departments':t.departments,'sites':t.sites},'evidence_unit_conversion':False}))
                if s['status']=='integrating' and s['integration_workdays']>=c.integration_workdays and day>s['closed_day'] and operating:
                    if growth.pending_construction is None and growth.pending_hiring is None:
                        retained=[p for p in t.employees if s['employee_status'][p.key]=='target']
                        from .enterprise_growth import validate_installed_inventory
                        validate_installed_inventory(self.config,capacity=growth.capacity+t.installed_seats,guilds=growth.guilds+t.guilds,departments=growth.departments+t.departments,sites=growth.sites+t.sites,target=growth.target+len(retained))
                        if ever_people+len(retained)>people_cap:
                            raise RuntimeError('acquisition employee resource capacity changed; retain checkpoint')
                        effects['transfers'].append({'target':t.key,'employees':[asdict(p) for p in retained],
                            'seats':t.installed_seats,'guilds':t.guilds,'departments':t.departments,'sites':t.sites,
                            'knowledge':t.organizational_knowledge,'mode':c.integration_mode})
                        s['status']='integrated';s['ready_day']=day
                        events.append(('acquisition_integration_complete',{'target':t.key,'employees_transferred':len(retained),'assets':{'seats':t.installed_seats,'guilds':t.guilds,'departments':t.departments,'sites':t.sites},'mode':c.integration_mode,'evidence_unit_conversion':False}))
                if s['status']=='integrating' and working:
                    retained=[p for p in t.employees if s['employee_status'][p.key]=='target']
                    # Integration work on owned systems/assets remains necessary
                    # even if target staff leave; only their payroll disappears.
                    cost=len(t.employees)*c.integration_resource_units_per_employee_workday+len(retained)*c.integration_payroll_resource_units_per_employee_workday
                    if not operating or cash+effects['cash_delta']<cost:
                        s['unfunded_workdays']+=1
                        events.append(('acquisition_integration_stalled',{'target':t.key,'due':cost,'unfunded_workdays':s['unfunded_workdays']}))
                        if s['unfunded_workdays']>=c.maximum_unfunded_workdays:
                            s['status']='failed'
                            for p in retained:s['employee_status'][p.key]='departed'
                            events.append(('acquisition_integration_failed',{'target':t.key,'departed':len(retained),'assets_owned_but_idle':True,'historical_evidence_retained':True}))
                        continue
                    effects['cash_delta']-=cost;effects['integration_cost']+=cost;s['integration_paid']+=cost
                    s['integration_workdays']+=1;s['unfunded_workdays']=0
                    for p in retained:
                        draw=number(exit_draw(t.key,p.key),'employee exit draw',high=1.)
                        if draw<c.integration_exit_probability_per_workday:
                            s['employee_status'][p.key]='departed';events.append(('acquisition_integration_departure',{'target':t.key,'person_key':p.key}))
                    events.append(('acquisition_integration_paid',{'target':t.key,'paid':cost,'workdays':s['integration_workdays']}))
                if s['status']=='integrated' and working and operating and s['pending_evidence']:
                    s['evidence_workdays']+=1
                    if s['evidence_workdays']>=c.evidence_resolution_workdays:
                        cost=len(s['pending_evidence'])*c.evidence_review_resource_units_per_record
                        if cash+effects['cash_delta']>=cost:
                            effects['cash_delta']-=cost;effects['evidence_cost']+=cost;s['evidence_paid']+=cost
                            s['resolved_evidence']=s['pending_evidence'];s['pending_evidence']=[]
                            events.append(('acquisition_evidence_quarantined',{'target':t.key,'records':len(s['resolved_evidence']),'paid':cost,'parent_credit_added':0.,'reason':'source units and evidence quality are not identified with parent authority'}))
            self.last_day=day
            return effects,events
        except BaseException:
            self.targets=before['targets'];self.last_day=before['last_day'];raise

    def validate(self,config,day):
        if type(self.last_day) is not int or self.last_day!=day-1 or set(self.targets)!={t.key for t in self.c.targets}:
            raise ValueError('acquisition state/calendar roster differs')
        original=EnterpriseAcquisitionState(config)
        for t in self.c.targets:
            s=self.targets[t.key]
            if set(s)!=set(original.targets[t.key]) or s['status'] not in {'available','expired','integrating','integrated','failed'}:
                raise ValueError('acquisition target state schema differs')
            if set(s['employee_status'])!={p.key for p in t.employees} or any(v not in {'target','parent','departed'} for v in s['employee_status'].values()):raise ValueError('target employee ownership roster differs')
            if set(s['employee_ids'])!={key for key,value in s['employee_status'].items() if value=='parent'} or len(set(s['employee_ids'].values()))!=len(s['employee_ids']):raise ValueError('target transferred identities reused/lost')
            if s['retained_evidence']!=list(asdict(t)['evidence']) or s['pending_evidence']+s['resolved_evidence']!=list(asdict(t)['pending_evidence']):
                raise ValueError('target historical or pending evidence lost/relabelled')
            for key in ('transferred_cash','assumed_debt','consideration_paid','financing','transaction_paid','integration_paid','evidence_paid','target_cash_remaining','seller_consideration_received'):number(s[key],key)
            for key in ('integration_workdays','unfunded_workdays','evidence_workdays'):
                if type(s[key]) is not int or not 0<=s[key]<=day:raise ValueError('target integration exposure invalid')
            if s['closed_day'] is None:
                if s['owner']!=t.seller or s['status'] not in {'available','expired'} or any(s[k] for k in ('transferred_cash','assumed_debt','consideration_paid','financing','transaction_paid','integration_paid','evidence_paid')) or any(v!='target' for v in s['employee_status'].values()):raise ValueError('unbought target ownership changed')
                if s['target_cash_remaining']!=t.cash_resource_units or s['seller_consideration_received']!=0.:raise ValueError('unbought target/seller cash changed')
            else:
                if type(s['closed_day']) is not int or not t.available_day<=s['closed_day']<min(day,t.expires_day) or s['owner']!='parent' or s['status'] in {'available','expired'}:raise ValueError('acquisition ownership date differs')
                if (s['transferred_cash'],s['assumed_debt'],s['consideration_paid'],s['transaction_paid'])!=(t.cash_resource_units,t.financial_debt_resource_units,t.consideration_resource_units,self.c.transaction_resource_units):raise ValueError('acquisition identifiable financial stocks differ')
                if s['target_cash_remaining']!=0. or s['seller_consideration_received']!=s['consideration_paid']:raise ValueError('acquisition target/seller cash conservation differs')
                if s['financing']>self.c.maximum_acquisition_financing_resource_units:raise ValueError('acquisition financing exceeds declared lender capacity')
            if s['status']=='integrated':
                if type(s['ready_day']) is not int or not s['closed_day']<s['ready_day']<day or s['integration_workdays']!=self.c.integration_workdays or len(s['guilds'])!=t.guilds or len(set(s['guilds']))!=len(s['guilds']) or any(type(g) is not int or g<0 for g in s['guilds']):raise ValueError('acquisition integration not completed')
            elif s['ready_day'] is not None or s['employee_ids'] or s['guilds']:raise ValueError('target usable before integration')

    @classmethod
    def restore(cls,config,value,day):
        obj=cls(config)
        if not isinstance(value,dict) or set(value)!=set(obj.to_dict()):raise ValueError('acquisition state inventory differs')
        obj.targets=copy.deepcopy(value['targets']);obj.last_day=value['last_day'];obj.validate(config,day);return obj
