"""r8 actual two-firm ownership integration, synthetic and not calibrated.

Whole operating units move ownership; no agent, ledger or outstanding obligation
is flattened into a capsule. Local IDs remain scoped by company. The private
target evolves independently, and is exposed only at the completed offer-day
boundary. This is a subsidiary acquisition, not a claim of a flattened merger.
"""
from __future__ import annotations
import copy
from dataclasses import dataclass, asdict
from pathlib import Path
import math
import json
import re
from .storage import canonical, digest, atomic_json, file_digest, source_hash

VERSION = 'longitudinal-enterprise-joint-integration-8'


def finite(value, name, low=0., high=1e15):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('joint '+name+' outside declared finite bound')
    return value


@dataclass(frozen=True)
class JointTerms:
    global_world: int = 91000
    offer_day: int = 30
    integration_mode: str = 'preserve-autonomy'
    consideration: float = 10.
    transaction_cost: float = 1.
    integration_workdays: int = 3
    integration_cost_per_employee_workday: float = .1
    maximum_unfunded_workdays: int = 3
    minimum_past_margin: float = -1.
    require_unmet_orders: bool = False
    parent_reserve: float = 10.
    subsidiary_daily_cash_budget: float = 20.
    autonomy_coordination_hours: float = .03
    centralized_coordination_hours: float = .01
    max_active_people: int = 256
    max_ever_people: int = 1024
    max_installed_seats: int = 256

    def validate(self, horizon):
        if type(self.offer_day) is not int or not 1 <= self.offer_day < horizon:
            raise ValueError('joint offer outside completed-day calendar')
        if self.integration_mode not in ('preserve-autonomy', 'centralize') or type(self.require_unmet_orders) is not bool:
            raise ValueError('joint integration mode invalid')
        for key in ('global_world','integration_workdays','maximum_unfunded_workdays','max_active_people','max_ever_people','max_installed_seats'):
            if type(getattr(self,key)) is not int or not 1 <= getattr(self,key) <= 100000000:
                raise ValueError('joint positive integer resource bound invalid')
        for key in ('consideration','transaction_cost','integration_cost_per_employee_workday','parent_reserve','subsidiary_daily_cash_budget'):
            finite(getattr(self,key),key)
        if min(self.consideration,self.transaction_cost,self.integration_cost_per_employee_workday) <= 0:
            raise ValueError('joint transfer/integration requires actual nonzero costs')
        finite(self.minimum_past_margin,'margin',-1.,1.)
        finite(self.autonomy_coordination_hours,'autonomy hours',0.,.2)
        finite(self.centralized_coordination_hours,'central hours',0.,.2)
        return self


CONTROL_FIELDS = {'day','company','owner','mode','cash_delta','consideration','provider_cost','coordination_hours','policy'}


def validate_control(control, day):
    if control is None: return None
    if not isinstance(control,dict) or set(control) != CONTROL_FIELDS or type(control['day']) is not int or control['day'] != day:
        raise ValueError('joint day control schema/calendar differs')
    for key in ('company','owner'):
        if not isinstance(control[key],str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}',control[key]):
            raise ValueError('joint company identity invalid')
    if control['mode'] not in ('integrating','preserve-autonomy','centralize','failed-owned'):
        raise ValueError('joint control mode invalid')
    finite(control['cash_delta'],'cash delta',-1e15,1e15)
    finite(control['consideration'],'consideration'); finite(control['provider_cost'],'provider cost')
    finite(control['coordination_hours'],'coordination hours',0.,.2)
    p = control['policy']
    if p is not None and (not isinstance(p,list) or len(p)!=2 or p[0] not in {'equal','linear','sublinear','hierarchy','hierarchy_tenure'} or p[1] not in {'central','witness','consensus'}):
        raise ValueError('joint actual policy invalid')
    if p is not None and control['mode']!='centralize':
        raise ValueError('joint autonomy cannot silently change governance')
    return copy.deepcopy(control)


def references(company, engine):
    """Injective namespaced references; histories retain their native local IDs.

    Never concatenate department arrays: r7 has entity-local dependency edges.
    Every operation continues against its original engine/ledger namespace.
    """
    return {'company':company,
        'people':{str(p['id']):f'{company}/person/{p["id"]}' for p in engine.people},
        'guilds':{str(g):f'{company}/guild/{g}' for g in engine.guild_alias},
        'sites':{str(s):f'{company}/site/{s}' for s in range(engine.site_count)},
        'departments':{str(d):f'{company}/department/{d}' for d in range(engine.organization.c.department_count)},
        'ledger_event_scope':f'{company}/ledger', 'dependency_scope':f'{company}/department',
        'order_scope':f'{company}/order', 'shipment_scope':f'{company}/shipment'}


def full_inventory(engine):
    """Exact dynamic state inventory, not a projected target or future values."""
    return {'agents':[asdict(a) for a in engine.agents], 'people':copy.deepcopy(engine.people),
        'slots':dict(engine.slots), 'slot_guild':dict(engine.slot_guild),
        'guild_alias':dict(engine.guild_alias), 'memory':dict(engine.memory),
        'routine':dict(engine.routine), 'adoption':dict(engine.adoption),
        'migration_paid':dict(engine.migration_paid), 'cash':engine.cash,
        'growth':engine.enterprise.to_dict(), 'operations':engine.operations.to_dict(),
        'organization':engine.organization.to_dict(),
        'acquisitions':engine.acquisitions.to_dict() if engine.acquisitions is not None else None,
        'history':copy.deepcopy(engine.history), 'ledger_semantic_sha256':engine.ledger.semantic_digest(),
        'state_semantic_sha256':engine.semantic_digest()}


def inventory_from_snapshot(envelope):
    state=envelope['state']
    return {**{k:copy.deepcopy(state[k]) for k in ('agents','people','slots','slot_guild','guild_alias','memory','routine','adoption','migration_paid','cash','history')},
        'growth':state['enterprise_growth_state'],'operations':state['enterprise_operating_state'],
        'organization':state['organization_tasks_state'],'acquisitions':state.get('enterprise_acquisition_state'),
        'ledger_semantic_sha256':envelope['ledger']['semantic_sha256'],
        'state_semantic_sha256':envelope['state_semantic_sha256']}


def inventory_digest(value):
    # Native int-key maps and restored JSON str-key maps have different
    # sort order at IDs >=10. Use an explicit JSON/string-key canonical form
    # for this NEW provenance digest; original engine/state codecs are intact.
    return digest(canonical(json.loads(canonical(value))))


def references_from_snapshot(company,envelope):
    state=envelope['state']
    return {'company':company,
        'people':{str(p['id']):f'{company}/person/{p["id"]}' for p in state['people']},
        'guilds':{str(g):f'{company}/guild/{g}' for g in state['guild_alias']},
        'sites':{str(s):f'{company}/site/{s}' for s in range(state['enterprise_growth_state']['sites'])},
        'departments':{str(d):f'{company}/department/{d}' for d in range(state['config']['organization_tasks']['department_count'])},
        'ledger_event_scope':f'{company}/ledger','dependency_scope':f'{company}/department',
        'order_scope':f'{company}/order','shipment_scope':f'{company}/shipment'}


def verify_cash_records(records):
    """Independent scalar cash accountant; no engine transition function import."""
    costs=('recruitment_resource_units','migration_resource_units','operating_resource_units','infrastructure_setup_resource_units')
    for expected, row in enumerate(records):
        if row['day']!=expected: raise ValueError('joint accountant nonsequential day')
        ordinary=[]; deltas=[]; internal=[]; consideration=[]; fees=[]
        for company,f in row['firms'].items():
            a,b=f['metrics_before'],f['metrics_after']
            net=math.fsum(b.get(k,0.)-a.get(k,0.) for k in ('revenue_resource_units','financing_inflow_resource_units'))
            net-=math.fsum(b.get(k,0.)-a.get(k,0.) for k in costs)
            # Legacy capsule mechanisms are refused by JointRun admission.
            control=f['control']
            transfer=control['cash_delta'] if control is not None else 0.
            sold=control['consideration'] if control is not None else 0.
            fee=control['provider_cost'] if control is not None else 0.
            actual=f['cash_after']-f['cash_before']
            if not math.isclose(actual,net+transfer-sold-fee,rel_tol=1e-11,abs_tol=1e-8):
                raise ValueError('independent joint entity cash does not conserve: '+company)
            ordinary.append(net);deltas.append(actual);internal.append(transfer);consideration.append(sold);fees.append(fee)
        if not math.isclose(math.fsum(internal),0.,rel_tol=0,abs_tol=1e-8):
            raise ValueError('joint treasury transfer is not zero-sum')
        if not math.isclose(math.fsum(deltas)+math.fsum(consideration)+math.fsum(fees),math.fsum(ordinary),rel_tol=1e-11,abs_tol=1e-8):
            raise ValueError('joint parent/subsidiary/seller/provider cash does not conserve')
    return {'status':'INDEPENDENT_TWO_FIRM_CASH_ACCOUNTING_PASS','days':len(records),'independent_transition_functions':True}


class JointRun:
    """One two-firm world, before/after actual ownership of an operating unit.

    The private seller is not an argument to buyer.step/policy. At offer a fresh
    complete snapshot is generated, validated, and exposed; its engine ownership
    is moved, not copied. Both unchanged entity histories continue exactly once.
    """
    def __init__(self,buyer_config,target_config,root,terms):
        from .model import Model
        root=Path(root)
        if root.exists(): raise ValueError('joint output must be fresh')
        terms.validate(buyer_config.days)
        for config in (buyer_config,target_config):
            config.validate()
            if config.longitudinal is None or config.enterprise_operations is None or config.enterprise_growth is None or config.organization_tasks is None:
                raise ValueError('joint requires complete existing operations/growth/organization state')
            if config.enterprise_acquisition is not None:
                raise ValueError('joint does not project legacy acquisition capsules')
        if buyer_config.days!=target_config.days or buyer_config.longitudinal.calendar_start!=target_config.longitudinal.calendar_start or buyer_config.longitudinal.holidays!=target_config.longitudinal.holidays:
            raise ValueError('joint firm Gregorian calendars/horizons must match')
        if (buyer_config.seed,buyer_config.world,buyer_config.longitudinal.world_context)==(target_config.seed,target_config.world,target_config.longitudinal.world_context):
            raise ValueError('joint target requires its own independently evolved company identity')
        root.mkdir(mode=0o700,parents=True)
        self.root=root; self.terms=terms; self.day=0; self.source=source_hash(); self.failed=False
        self.units={'buyer':Model(buyer_config,storage_dir=root/'buyer',journal_chunk_bytes=1048576)}
        self._private={'target':Model(target_config,storage_dir=root/'target',journal_chunk_bytes=1048576)}
        self.owners={'buyer':'buyer','target':'seller'}; self.status='unoffered'; self.integration_paid_days=0; self.unfunded_days=0
        self.seller_account=0.;self.provider_account=0.;self.records=[];self.events=[];self.offer=None
        self._terms_sha=digest(canonical(asdict(terms)))
        try:self._capacity()
        except BaseException:self.close();raise

    @property
    def horizon(self):return self.units['buyer'].config.days

    def buyer_public_state(self):
        # No target snapshot, raw private state, or future private inputs here.
        return self.units['buyer'].state()

    def _all(self):return {**self.units,**self._private}

    def _capacity(self):
        all_firms=self._all(); t=self.terms
        stocks={'active':sum(len(m.active) for m in all_firms.values()),
            'ever':sum(len(m.people) for m in all_firms.values()),
            'seats':sum(m.enterprise.capacity for m in all_firms.values())}
        if stocks['active']>t.max_active_people or stocks['ever']>t.max_ever_people or stocks['seats']>t.max_installed_seats:
            raise ValueError('joint complete acquired resource-capacity envelope refused')
        # Pending construction is a real future commitment, not free headroom.
        future=sum(m.enterprise.pending_construction['capacity'] if m.enterprise.pending_construction else m.enterprise.capacity for m in all_firms.values())
        if future>t.max_installed_seats:raise ValueError('joint pending construction capacity refused')
        return stocks

    def _expose_offer(self):
        from .longitudinal_model import verify_snapshot
        target=self._private['target']; buyer=self.units['buyer']; t=self.terms
        if self.day!=t.offer_day or self.status!='unoffered' or target.day!=self.day or target.failed_day or buyer.day!=self.day:
            raise ValueError('joint offer must be a completed same-calendar boundary')
        path=self.root/'offer-target.json'
        target.write_checkpoint(path)
        env,_=verify_snapshot(path,expected_config=target.config,expected_source_sha256=self.source)
        self.offer={'day':self.day,'calendar_date':target.clock.at(self.day).isoformat(),
            'source_sha256':self.source,'target_config_sha256':digest(canonical(target.config.to_dict())),
            'target_world':target.config.world,'target_seed':target.config.seed,
            'snapshot_sha256':file_digest(path),'state_semantic_sha256':env['state_semantic_sha256'],
            'full_preoffer_history_days':self.day,'references':references('target',target),
            'inventory_sha256':inventory_digest(full_inventory(target))}
        self._capacity()
        growth=buyer.enterprise
        margin=(growth.revenue_signal-growth.cost_signal)/max(growth.revenue_signal,growth.cost_signal,1e-12)
        if (buyer.closed_day is not None or buyer.suspended_day is not None or not growth.observed_workdays or margin<t.minimum_past_margin
                or t.require_unmet_orders and not buyer.operations.expansion_allowed(growth)
                or buyer.cash<t.consideration+t.transaction_cost+t.parent_reserve):
            self.status='declined';self.events.append({'day':self.day,'kind':'offer-declined','target':self.offer});return 0.,0.
        before=full_inventory(target)
        moved=self._private.pop('target'); self.units['target']=moved;self.owners['target']='buyer';self.status='integrating'
        if before!=full_inventory(moved):raise ValueError('joint ownership move altered target state/history')
        self.events.append({'day':self.day,'kind':'ownership-transfer','previous_owner':'seller','owner':'buyer',
            'target':self.offer,'state_inventory_exact':True,'original_local_references_preserved':True,
            'consideration':t.consideration,'transaction_cost':t.transaction_cost})
        return t.consideration,t.transaction_cost

    def _controls(self,consideration,fees):
        firms=self._all(); b=firms['buyer']; t=self.terms
        result={name:None for name in firms}
        if self.owners['target']!='buyer':return result
        target=firms['target']; working=b.clock.is_workday(self.day)
        budget_fees=fees
        if self.status=='integrating' and working:
            due=len(target.active)*t.integration_cost_per_employee_workday
            if b.cash-consideration-budget_fees-due<t.parent_reserve:
                self.unfunded_days+=1
                self.events.append({'day':self.day,'kind':'integration-stalled','due':due})
                if self.unfunded_days>=t.maximum_unfunded_workdays:
                    self.status='failed-owned';self.events.append({'day':self.day,'kind':'integration-failed','ownership_retained':True,'pending_obligations_retained':True})
            else:
                budget_fees+=due;self.integration_paid_days+=1;self.unfunded_days=0
                if self.integration_paid_days>=t.integration_workdays:
                    self.status='integrated';self.events.append({'day':self.day,'kind':'integration-complete','mode':t.integration_mode})
        mode=t.integration_mode if self.status=='integrated' else self.status
        policy=None; transfer=0.
        if mode=='centralize':
            # Parent control sweeps surplus and funds only a finite declared daily
            # subsidiary operating budget. No future shipment revenue is spent.
            sweep=max(0.,target.cash-t.subsidiary_daily_cash_budget)
            transfer=-sweep
            available=b.cash-consideration-budget_fees+sweep-t.parent_reserve
            transfer+=min(max(0.,t.subsidiary_daily_cash_budget-target.cash+sweep),max(0.,available))
            policies={b.policy(g) for g in b.guild_alias}
            if len(policies)!=1:raise ValueError('joint central control cannot project mixed parent guild policies')
            policy=list(next(iter(policies)))
        elif mode=='preserve-autonomy':
            # Retain unit treasury/governance, but actual parent treasury can
            # finance a bounded payroll shortfall; this is not new lender money.
            due=len(target.active)*target.l.payroll_resource_units_per_member_workday
            transfer=min(max(0.,due-target.cash),t.subsidiary_daily_cash_budget,
                max(0.,b.cash-consideration-budget_fees-t.parent_reserve))
        hours=t.autonomy_coordination_hours if mode in ('preserve-autonomy','integrating') else t.centralized_coordination_hours if mode=='centralize' else 0.
        for company in firms:
            result[company]={'day':self.day,'company':company,'owner':'buyer','mode':mode,
                'cash_delta':transfer if company=='target' else -transfer,
                'consideration':consideration if company=='buyer' else 0.,
                'provider_cost':budget_fees if company=='buyer' else 0.,
                'coordination_hours':hours,'policy':policy if company=='target' else None}
        return result

    def step(self):
        if self.failed or self.day>=self.horizon:raise ValueError('joint failed/completed world cannot step')
        if source_hash()!=self.source:raise ValueError('joint source drift')
        try:
            if any(m.day!=self.day or m.failed_day for m in self._all().values()):raise ValueError('joint day or partially failed entity differs')
            consideration=fees=0.
            if self.day==self.terms.offer_day:consideration,fees=self._expose_offer()
            controls=self._controls(consideration,fees)
            self._capacity(); before={k:(m.cash,dict(m.metrics),len(m.active)) for k,m in self._all().items()}
            # Ownership routing enumerates each physical engine exactly once.
            firms=self._all()
            if len({id(m._long) for m in firms.values()})!=len(firms):raise ValueError('joint engine alias/double stepping')
            for company,m in firms.items():
                m._long.set_joint_control(controls[company]);m.step()
                if m.day!=self.day+1:raise ValueError('joint entity did not step exactly once')
            self._capacity()
            row={'day':self.day,'owners':dict(self.owners),'status':self.status,'firms':{
                k:{'cash_before':before[k][0],'cash_after':m.cash,'active_before':before[k][2],'metrics_before':before[k][1],
                    'metrics_after':dict(m.metrics),'control':controls[k]} for k,m in firms.items()}}
            self.records.append(row);verify_cash_records(self.records)
            self.seller_account+=consideration;self.provider_account+=sum(c['provider_cost'] for c in controls.values() if c)
            self.day+=1
        except BaseException:
            self.failed=True
            raise

    def run(self,stop_day=None):
        end=self.horizon if stop_day is None else stop_day
        if type(end) is not int or not self.day<=end<=self.horizon:raise ValueError('joint stop calendar invalid')
        while self.day<end:self.step()
        return self

    def snapshot(self,path):
        if self.failed:raise ValueError('joint partially failed transaction cannot checkpoint')
        self.validate_joint_metadata()
        from .longitudinal_model import verify_snapshot
        path=Path(path)
        if path.exists():raise ValueError('joint checkpoint immutable collision')
        offer_pin=None
        if self.offer is not None:
            from .longitudinal_model import verify_snapshot
            source=self.root/'offer-target.json'
            if file_digest(source)!=self.offer['snapshot_sha256']:raise ValueError('joint owned offer snapshot drift')
            env,_=verify_snapshot(source,expected_source_sha256=self.source)
            names=(source.name,env['ledger']['file'])
            import shutil
            for name in names:
                if Path(name).name!=name:raise ValueError('joint offer group unsafe reference')
                target=path.parent/name;original=self.root/name
                if target.absolute()!=original.absolute():
                    if target.exists():
                        if file_digest(target)!=file_digest(original):raise ValueError('joint offer group collision')
                    else:shutil.copyfile(original,target)
            offer_pin={'file':source.name,'sha256':file_digest(source),'bytes':source.stat().st_size}
        pins={}
        for name,m in self._all().items():
            p=path.parent/(path.stem+'-'+name+'.json');m.write_checkpoint(p)
            env,_=verify_snapshot(p,expected_config=m.config,expected_source_sha256=self.source)
            pins[name]={'file':p.name,'sha256':file_digest(p),'day':m.day,'config':m.config.to_dict(),
                'state_semantic_sha256':env['state_semantic_sha256']}
        value={'schema':'dams-two-firm-joint-checkpoint-1','model_version':VERSION,'day':self.day,
            'source_sha256':self.source,'terms':asdict(self.terms),'terms_sha256':self._terms_sha,
            'owners':self.owners,'status':self.status,'integration_paid_days':self.integration_paid_days,
            'unfunded_days':self.unfunded_days,'seller_account':self.seller_account,
            'provider_account':self.provider_account,'offer':self.offer,'offer_snapshot_pin':offer_pin,'events':self.events,
            'records':self.records,'firms':pins}
        atomic_json(path,value);return value

    @classmethod
    def restore(cls,path,root):
        from .model import Model
        from .config import Config
        path=Path(path);v=json.loads(path.read_bytes());root=Path(root)
        if root.exists() or v['schema']!='dams-two-firm-joint-checkpoint-1' or v['source_sha256']!=source_hash() or v['model_version']!=VERSION:
            raise ValueError('joint checkpoint source/schema/fresh destination differs')
        terms=JointTerms(**v['terms']);terms.validate(v['firms']['buyer']['config']['days'])
        if digest(canonical(asdict(terms)))!=v['terms_sha256']:raise ValueError('joint checkpoint terms altered')
        if set(v['firms'])!={'buyer','target'} or v['owners']['buyer']!='buyer' or v['owners']['target'] not in ('seller','buyer'):
            raise ValueError('joint checkpoint company/ownership differs')
        root.mkdir(mode=0o700,parents=True);obj=cls.__new__(cls)
        obj.root=root;obj.terms=terms;obj._terms_sha=v['terms_sha256'];obj.source=v['source_sha256'];obj.failed=False
        obj.units={};obj._private={}
        try:
            for name,pin in v['firms'].items():
                p=path.parent/pin['file']
                if Path(pin['file']).name!=pin['file'] or file_digest(p)!=pin['sha256'] or pin['day']!=v['day']:
                    raise ValueError('joint complete firm snapshot pin/calendar differs')
                m=Model.restore_checkpoint(p,storage_dir=root/name,expected_config=Config.from_dict(pin['config']))
                (obj._private if name=='target' and v['owners']['target']=='seller' else obj.units)[name]=m
                if m.semantic_digest()!=pin['state_semantic_sha256']:raise ValueError('joint firm restored state differs')
            for k in ('day','owners','status','integration_paid_days','unfunded_days','seller_account','provider_account','offer','events','records'):
                setattr(obj,k,copy.deepcopy(v[k]))
            obj._capacity();verify_cash_records(obj.records)
            if obj.day!=len(obj.records) or any(m.day!=obj.day for m in obj._all().values()):raise ValueError('joint restore day inventory differs')
            if obj.owners['target']=='buyer' and (obj.offer is None or obj.offer['day']!=obj.terms.offer_day or obj.day<=obj.terms.offer_day):
                raise ValueError('joint ownership requires actual offer-boundary provenance')
            if obj.offer is not None:
                from .longitudinal_model import verify_snapshot
                offer_pin=v['offer_snapshot_pin']
                if not isinstance(offer_pin,dict) or set(offer_pin)!={'file','sha256','bytes'} or offer_pin['file']!='offer-target.json':raise ValueError('joint immutable offer group pin absent')
                p=path.parent/offer_pin['file']
                if p.stat().st_size!=offer_pin['bytes'] or file_digest(p)!=offer_pin['sha256']:raise ValueError('joint immutable offer pin differs')
                if file_digest(p)!=obj.offer['snapshot_sha256']:raise ValueError('joint original offer snapshot bytes differ')
                env,_=verify_snapshot(p,expected_source_sha256=obj.source)
                state=env['state'];config=state['config']
                if (env['state_semantic_sha256']!=obj.offer['state_semantic_sha256'] or state['day']!=obj.terms.offer_day
                        or obj.offer['source_sha256']!=obj.source or obj.offer['target_config_sha256']!=digest(canonical(config))
                        or obj.offer['target_world']!=config['world'] or obj.offer['target_seed']!=config['seed']
                        or obj.offer['full_preoffer_history_days']!=state['day']
                        or obj.offer['calendar_date']!=obj.units['buyer'].clock.at(obj.terms.offer_day).isoformat()):
                    raise ValueError('joint original offer source/history boundary differs')
                if obj.offer['references']!=references_from_snapshot('target',env) or obj.offer['inventory_sha256']!=inventory_digest(inventory_from_snapshot(env)):
                    raise ValueError('joint namespace/inventory proof differs from complete original offer state')
                import shutil
                shutil.copyfile(p,root/'offer-target.json')
                db=env['ledger']['file']
                if Path(db).name!=db:raise ValueError('joint offer database reference unsafe')
                shutil.copyfile(p.parent/db,root/db)
            obj.validate_joint_metadata()
            return obj
        except BaseException:
            obj.close()
            raise

    def close(self):
        for m in self._all().values():m.ledger.close()

    def validate_joint_metadata(self):
        """Reconcile checkpoint metadata with genuine original firm journals.

        No JSON status or receipt flag can grant early integration, change paid
        counters, mint seller cash or detach the actual owned engine.
        """
        firms=self._all();t=self.terms;status='unoffered';owners={'buyer':'buyer','target':'seller'}
        paid=unfunded=0;sold=fees=0.;previous={}
        events={day:[e for e in self.events if e['day']==day] for day in range(self.day)}
        if any(type(e.get('day')) is not int or not 0<=e['day']<self.day for e in self.events):raise ValueError('joint event outside completed history')
        journals={};eligible_rosters={}
        for company,m in firms.items():
            rows={};controls={};lifecycle={}
            for seq,when,kind,event,payload in m.ledger.rows('journal'):
                value=json.loads(payload)
                if kind=='day_end':rows[when]=value
                if kind=='joint_owned_control':
                    if when in controls:raise ValueError('joint duplicate actual day control')
                    controls[when]=value
                if kind in ('entry','exit','retirement','layoff'):
                    lifecycle.setdefault(when,[]).append((kind,value))
            if set(rows)!=set(range(self.day)):raise ValueError('joint actual complete company day roster differs')
            journals[company]=(rows,controls)
            eligible=set(range(m.config.n));preday={}
            for day in range(self.day):
                preday[day]=set(eligible)
                for kind,value in lifecycle.get(day,[]):
                    person=value['person']
                    if kind=='entry':
                        if value['initial']:
                            if day!=0 or person not in eligible:raise ValueError('joint initial eligible identity differs')
                        else:
                            if person in eligible:raise ValueError('joint eligible entry replayed')
                            eligible.add(person)
                    else:
                        if person not in eligible:raise ValueError('joint eligible exit replayed')
                        eligible.remove(person)
            if eligible!=set(m.slots.values()):raise ValueError('joint full eligible roster does not match actual closed state')
            eligible_rosters[company]=preday
        for day,r in enumerate(self.records):
            if set(r['firms'])!={'buyer','target'}:raise ValueError('joint record entity scope differs')
            if day==t.offer_day:
                e=events[day]
                closes=[x for x in e if x['kind']=='ownership-transfer'];declines=[x for x in e if x['kind']=='offer-declined']
                if len(closes)+len(declines)!=1:raise ValueError('joint actual offer outcome missing/repeated')
                decision=(closes or declines)[0]
                if self.offer is None or decision['target']!=self.offer:raise ValueError('joint offer metadata disconnected from history')
                if closes:
                    if decision['previous_owner']!='seller' or decision['owner']!='buyer' or decision['consideration']!=t.consideration or decision['transaction_cost']!=t.transaction_cost or decision['state_inventory_exact'] is not True:
                        raise ValueError('joint actual ownership transaction terms differ')
                    owners['target']='buyer';status='integrating'
                else:status='declined'
            consideration=t.consideration if day==t.offer_day and owners['target']=='buyer' else 0.
            fee=t.transaction_cost if consideration else 0.
            working=firms['buyer'].clock.is_workday(day)
            if status=='integrating' and working:
                due=r['firms']['target']['active_before']*t.integration_cost_per_employee_workday
                if r['firms']['buyer']['cash_before']-consideration-fee-due<t.parent_reserve:
                    unfunded+=1
                    if not any(e['kind']=='integration-stalled' and e['due']==due for e in events[day]):raise ValueError('joint unpaid integration event missing')
                    if unfunded>=t.maximum_unfunded_workdays:status='failed-owned'
                else:
                    fee+=due;paid+=1;unfunded=0
                    if paid>=t.integration_workdays:status='integrated'
            mode=t.integration_mode if status=='integrated' else status
            for company,f in r['firms'].items():
                raw,controls=journals[company];actual=raw[day]
                preactive=len(eligible_rosters[company][day])
                if type(f['active_before']) is not int or f['active_before']!=preactive:
                    raise ValueError('joint integration staffing differs from completed preday roster')
                if (f['cash_after']!=actual['cash'] or canonical(f['metrics_after'])!=canonical(actual['metrics_cumulative'])
                        or f['control']!=controls.get(day) or f['control']!=actual.get('joint_control')):
                    raise ValueError('joint metadata differs from actual firm cash/metrics/control journal')
                if day:
                    if f['cash_before']!=previous[company]['cash_after'] or f['metrics_before']!=previous[company]['metrics_after']:
                        raise ValueError('joint metadata previous committed balance differs')
                elif f['cash_before']!=firms[company].config.n*firms[company].l.initial_cash_per_member:
                    raise ValueError('joint metadata initial cash differs')
                c=f['control']
                if owners['target']=='buyer':
                    hours=t.autonomy_coordination_hours if mode in ('preserve-autonomy','integrating') else t.centralized_coordination_hours if mode=='centralize' else 0.
                    if c is None or c['day']!=day or c['company']!=company or c['owner']!='buyer' or c['mode']!=mode or c['coordination_hours']!=hours or c['consideration']!=(consideration if company=='buyer' else 0.) or c['provider_cost']!=(fee if company=='buyer' else 0.):
                        raise ValueError('joint integration policy/cost/paid-state does not follow actual history')
                elif c is not None:raise ValueError('joint private firm was controlled before purchase')
            if r['owners']!=owners or r['status']!=status:raise ValueError('joint recorded ownership/status differs from paid history')
            sold+=consideration;fees+=fee;previous=r['firms']
        if (self.owners!=owners or self.status!=status or self.integration_paid_days!=paid or self.unfunded_days!=unfunded
                or not math.isclose(self.seller_account,sold,rel_tol=0,abs_tol=1e-8)
                or not math.isclose(self.provider_account,fees,rel_tol=0,abs_tol=1e-8)):
            raise ValueError('joint checkpoint ownership/status/counters/accounts are not derivable from actual paid history')
        if self.day<=t.offer_day and self.offer is not None:raise ValueError('joint future private offer exposed in checkpoint')
        return verify_cash_records(self.records)


def run_joint_world(buyer_config,target_config,path,terms,*,checkpoint_days=()):
    """Existing CLI integration API; no provider, private future target disclosure.

    Two entity histories constitute ONE two-firm engineering world. Repeated
    restore tails are recovery controls, never additional independent worlds.
    """
    run=JointRun(buyer_config,target_config,path,terms)
    try:
        atomic_json(run.root/'predeclared-scenario.json',{'source_sha256':run.source,'terms':asdict(terms),
            'buyer_config':buyer_config.to_dict(),'target_config':target_config.to_dict(),
            'engineering_only':True,'formal_worlds_added':0,'calibration_OOS':False})
        for day in sorted(set((*checkpoint_days,run.horizon))):
            run.run(day);run.snapshot(run.root/f'joint-day-{day:06d}.json')
        run.validate_joint_metadata()
        result={'status':'ACTUAL_TWO_EVOLVED_FIRM_HISTORY_ENGINEERING_COMPLETED',
            'initial_two_firm_global_world':terms.global_world,'corporate_operating_unit_acquisition':True,
            'day':run.day,'source_sha256':run.source,'owners':run.owners,'integration_status':run.status,
            'offer':run.offer,'events':run.events,'cash_accounting':verify_cash_records(run.records),
            'seller_account':run.seller_account,'provider_account':run.provider_account,
            'firm_summaries':{k:m.summary() for k,m in run._all().items()},
            'formal_worlds_added':0,'calibration_OOS':False}
        atomic_json(run.root/'joint-result.json',result);return result
    finally:run.close()
