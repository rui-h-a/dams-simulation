"""Optional endogenous capacity investment, synthetic and not empirically fitted.

The controller sees past realised cash revenue and operating costs, not latent
proposal values, future work, or a desired policy ranking. Installed seats,
people, cumulative identities, departments, guild domains and sites are distinct.
Construction is paid before use; recruitment waits for completion and a hiring
lead time. Installed infrastructure is irreversible in v1: contraction leaves
stranded capacity and its maintenance cost. All coefficients are design choices.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, fields
import copy
import math

MODEL_VERSION = 'longitudinal-endogenous-growth-1'


@dataclass(frozen=True)
class EnterpriseGrowthConfig:
    schema_version: int = 1
    initial_organization_capacity: int = 30
    maximum_organization_capacity: int = 300
    initial_departments: int = 1
    members_per_guild: int = 30
    guilds_per_department: int = 4
    members_per_site: int = 120
    review_interval_days: int = 30
    signal_half_life_workdays: float = 20.
    expansion_step_members: int = 5
    contraction_step_members: int = 5
    minimum_target_members: int = 2
    expansion_profit_margin: float = .1
    contraction_profit_margin: float = -.1
    reserve_workdays: float = 20.
    construction_delay_days: int = 30
    hiring_delay_days: int = 14
    seat_setup_resource_units: float = 2.
    guild_setup_resource_units: float = 10.
    department_setup_resource_units: float = 10.
    site_setup_resource_units: float = 20.
    infrastructure_maintenance_resource_units_per_unit_workday: float = .05

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, cls): return value
        if not isinstance(value, dict) or set(value)-{f.name for f in fields(cls)}:
            raise ValueError('unknown/invalid enterprise growth configuration')
        return cls(**value)

    def validate(self, config):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('unknown enterprise growth schema')
        if config.longitudinal is None:
            raise ValueError('enterprise growth requires longitudinal mode')
        l = config.longitudinal
        if l.max_active_members is None or l.max_people_ever is None:
            raise ValueError('growth requires explicit active and ever-person resource caps')
        for name in ('initial_organization_capacity', 'maximum_organization_capacity',
                     'initial_departments', 'members_per_guild', 'guilds_per_department',
                     'members_per_site', 'review_interval_days', 'expansion_step_members',
                     'contraction_step_members', 'minimum_target_members',
                     'construction_delay_days', 'hiring_delay_days'):
            v = getattr(self, name)
            if type(v) is not int or not 1 <= v <= 100_000_000:
                raise ValueError(name+' must be a bounded positive integer')
        if not config.n <= self.initial_organization_capacity <= self.maximum_organization_capacity <= l.max_active_members:
            raise ValueError('initial people, installed seats, organization maximum and active resource cap differ')
        if not 2 <= self.minimum_target_members <= config.n:
            raise ValueError('minimum target must be between two and initial population')
        if self.initial_departments < math.ceil(config.guilds/self.guilds_per_department):
            raise ValueError('initial departments cannot contain the initial guild inventory')
        initial_sites=[0]*config.sites
        for g in range(config.guilds):initial_sites[g%config.sites]+=(config.n+config.guilds-1-g)//config.guilds
        if max(initial_sites)>self.members_per_site:
            raise ValueError('initial fixed world site allocation exceeds installed capacity')
        if self.initial_organization_capacity > config.guilds*self.members_per_guild or self.initial_organization_capacity > config.sites*self.members_per_site:
            raise ValueError('initial infrastructure cannot support the installed seat capacity')
        for name in ('signal_half_life_workdays', 'reserve_workdays', 'seat_setup_resource_units',
                     'guild_setup_resource_units', 'department_setup_resource_units',
                     'site_setup_resource_units', 'infrastructure_maintenance_resource_units_per_unit_workday'):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1e9:
                raise ValueError(name+' must be bounded finite resource/time units')
        if self.signal_half_life_workdays <= 0 or self.reserve_workdays <= 0:
            raise ValueError('signal half-life and cash reserve exposure must be positive')
        for name in ('expansion_profit_margin', 'contraction_profit_margin'):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not -1 <= v <= 1:
                raise ValueError('profit margin must be finite in [-1,1]')
        if self.contraction_profit_margin >= self.expansion_profit_margin:
            raise ValueError('growth/contraction rules require separate hysteresis thresholds')
        if l.workforce_targets or l.guild_mergers or l.guild_moves:
            raise ValueError('growth v1 cannot mix endogenous allocation with prescribed workforce/domain changes')
        if l.adoption_mode == 'staged':
            raise ValueError('staged adoption has no declared policy for future guilds in growth v1')
        return self


class EnterpriseGrowthState:
    def __init__(self, config):
        self.c = config.enterprise_growth
        self.capacity = self.c.initial_organization_capacity
        self.guilds = config.guilds
        self.departments = self.c.initial_departments
        self.sites = config.sites
        self.target = config.n
        self.pending_construction = None
        self.pending_hiring = None
        self.revenue_signal = 0.
        self.cost_signal = 0.
        self.observed_workdays = 0
        self.last_observed_day = None
        if config.enterprise_operations is not None:
            self.pending_revenue = 0.
            self.pending_operating_cost = 0.
            self.last_cash_observed_day = None

    def inventory_for(self, capacity):
        guilds = max(self.guilds, math.ceil(capacity/self.c.members_per_guild))
        departments = max(self.departments, math.ceil(guilds/self.c.guilds_per_department))
        sites = max(self.sites, math.ceil(capacity/self.c.members_per_site))
        return {'capacity': capacity, 'guilds': guilds, 'departments': departments, 'sites': sites}

    def maintenance_due(self):
        return (self.guilds+self.departments+self.sites)*self.c.infrastructure_maintenance_resource_units_per_unit_workday

    def begin_day(self, day, cash, *, active, operating, payroll_per_member, expansion_allowed=True):
        """Return paid investment and auditable transitions using past data only."""
        if type(day) is not int or day<0 or type(active) is not int or active<0 or type(operating) is not bool:
            raise ValueError('enterprise decision day/exposure invalid')
        if self.last_observed_day is not None and self.last_observed_day>=day:
            raise ValueError('enterprise decision cannot observe present or future cash')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in (cash,payroll_per_member)):
            raise ValueError('enterprise decision resource input invalid')
        events = []; paid = 0.; c = self.c
        # Construction/hiring can only take effect at committed future boundaries.
        if self.pending_construction is not None and day >= self.pending_construction['ready_day']:
            request = self.pending_construction
            for key in ('capacity', 'guilds', 'departments', 'sites'):setattr(self, key, request[key])
            self.pending_hiring = {'target': request['target'], 'ready_day': day+c.hiring_delay_days}
            self.pending_construction = None
            events.append(('infrastructure_complete', copy.deepcopy(request)))
        if self.pending_hiring is not None and day >= self.pending_hiring['ready_day']:
            if operating:self.target = self.pending_hiring['target']
            events.append(('hiring_target_ready', {**self.pending_hiring, 'operating': operating}))
            self.pending_hiring = None
        if not operating or not self.observed_workdays or day % c.review_interval_days:
            return paid, events
        margin = (self.revenue_signal-self.cost_signal)/max(self.revenue_signal, self.cost_signal, 1e-12)
        reserve = max(self.cost_signal, self.target*payroll_per_member)*c.reserve_workdays
        decision = {'margin': margin, 'cash_before': cash, 'reserve': reserve,
                    'previous_observed_day': self.last_observed_day, 'target_before': self.target}
        if margin < c.contraction_profit_margin:
            self.target = max(c.minimum_target_members, self.target-c.contraction_step_members)
            # A queued hire does not override a later adverse observed decision.
            self.pending_hiring = None
            if self.pending_construction is not None:self.pending_construction['target'] = self.target
            events.append(('workforce_contraction', {**decision, 'target': self.target}))
        elif (expansion_allowed and margin > c.expansion_profit_margin and active >= self.target
              and self.pending_hiring is None and self.pending_construction is None):
            target = min(c.maximum_organization_capacity, self.target+c.expansion_step_members)
            if target > self.target:
                inventory = self.inventory_for(max(self.capacity, target))
                cost = ((inventory['capacity']-self.capacity)*c.seat_setup_resource_units
                        +(inventory['guilds']-self.guilds)*c.guild_setup_resource_units
                        +(inventory['departments']-self.departments)*c.department_setup_resource_units
                        +(inventory['sites']-self.sites)*c.site_setup_resource_units)
                # Also reserve the incremental payroll before requesting hires.
                reserve += (target-self.target)*payroll_per_member*c.reserve_workdays
                decision.update({'requested_target': target, 'setup_cost': cost, 'reserve': reserve})
                if cash >= cost+reserve:
                    if inventory['capacity'] > self.capacity:
                        self.pending_construction = {**inventory, 'target': target,
                            'requested_day': day, 'ready_day': day+c.construction_delay_days}
                        paid = cost
                        events.append(('infrastructure_requested', {**decision, **self.pending_construction}))
                    else:
                        self.pending_hiring = {'target': target, 'ready_day': day+c.hiring_delay_days}
                        events.append(('hiring_requested', {**decision, **self.pending_hiring}))
                else:events.append(('growth_unfunded', decision))
        return paid, events

    def observe(self, day, revenue, operating_cost, *, working):
        if type(day) is not int or day<0 or type(working) is not bool:raise ValueError('enterprise observation date/calendar invalid')
        if hasattr(self,'last_cash_observed_day'):
            if self.last_cash_observed_day is not None and day<=self.last_cash_observed_day:
                raise ValueError('enterprise calendar cash observation replayed or backdated')
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in (revenue,operating_cost)):
                raise ValueError('enterprise observations must be realised nonnegative resources')
            revenue += self.pending_revenue
            operating_cost += self.pending_operating_cost
            if not math.isfinite(revenue) or not math.isfinite(operating_cost):
                raise ValueError('enterprise calendar cash carry exceeds finite arithmetic')
            self.last_cash_observed_day = day
            self.pending_revenue = revenue
            self.pending_operating_cost = operating_cost
            if working:self.pending_revenue = self.pending_operating_cost = 0.
        if not working:return
        if self.last_observed_day is not None and day <= self.last_observed_day:
            raise ValueError('enterprise cash observation replayed or backdated')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in (revenue,operating_cost)):
            raise ValueError('enterprise observations must be realised nonnegative resources')
        weight = 1-math.exp(-math.log(2)/self.c.signal_half_life_workdays)
        self.revenue_signal += weight*(revenue-self.revenue_signal)
        self.cost_signal += weight*(operating_cost-self.cost_signal)
        self.observed_workdays += 1;self.last_observed_day = day

    def to_dict(self):
        return copy.deepcopy({key:value for key,value in self.__dict__.items() if key != 'c'})

    @classmethod
    def restore(cls, config, value, day):
        obj = cls(config)
        if not isinstance(value,dict) or set(value) != set(obj.to_dict()):
            raise ValueError('enterprise state inventory differs')
        for key,v in value.items():setattr(obj,key,copy.deepcopy(v))
        obj.validate(config,day)
        return obj

    def validate(self, config, day):
        c = self.c
        for key in ('capacity','guilds','departments','sites','target','observed_workdays'):
            if type(getattr(self,key)) is not int or getattr(self,key)<0:
                raise ValueError('enterprise integer stock invalid: '+key)
        if not c.initial_organization_capacity <= self.capacity <= c.maximum_organization_capacity or not c.minimum_target_members <= self.target <= self.capacity:
            raise ValueError('enterprise seat/target bounds differ')
        if not config.guilds <= self.guilds <= max(config.guilds,math.ceil(c.maximum_organization_capacity/c.members_per_guild)) or not config.sites <= self.sites <= max(config.sites,math.ceil(c.maximum_organization_capacity/c.members_per_site)):
            raise ValueError('enterprise domain/site bounds differ')
        if not c.initial_departments <= self.departments <= max(c.initial_departments,math.ceil(self.guilds/c.guilds_per_department)):
            raise ValueError('enterprise department bounds differ')
        if self.capacity > self.guilds*c.members_per_guild or self.capacity > self.sites*c.members_per_site or self.guilds > self.departments*c.guilds_per_department:
            raise ValueError('enterprise installed units cannot support capacity')
        for key in ('revenue_signal','cost_signal'):
            v=getattr(self,key)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0:raise ValueError('enterprise resource signal invalid')
        if config.enterprise_operations is not None:
            for key in ('pending_revenue','pending_operating_cost'):
                v=getattr(self,key)
                if type(v) not in (int,float) or not math.isfinite(v) or v<0:
                    raise ValueError('enterprise calendar cash carry invalid')
            if (day==0 and self.last_cash_observed_day is not None) or (day>0 and
                    (type(self.last_cash_observed_day) is not int or self.last_cash_observed_day!=day-1)):
                raise ValueError('enterprise calendar cash exposure differs')
        if (self.last_observed_day is None) != (self.observed_workdays==0) or self.observed_workdays > day:
            raise ValueError('enterprise observation exposure differs')
        if self.last_observed_day is not None and (type(self.last_observed_day) is not int or not 0 <= self.last_observed_day < day):
            raise ValueError('enterprise signal observes future work')
        if self.pending_hiring is not None:
            p=self.pending_hiring
            if set(p)!= {'target','ready_day'} or any(type(v) is not int for v in p.values()) or not c.minimum_target_members <= p['target'] <= self.capacity or p['ready_day'] < day:
                raise ValueError('enterprise hiring request invalid')
        if self.pending_construction is not None:
            p=self.pending_construction
            if set(p)!= {'capacity','guilds','departments','sites','target','requested_day','ready_day'} or any(type(v) is not int for v in p.values()):
                raise ValueError('enterprise construction inventory invalid')
            expected=self.inventory_for(p['capacity'])
            if any(p[k]!=expected[k] for k in expected) or not self.capacity < p['capacity'] <= c.maximum_organization_capacity or not c.minimum_target_members <= p['target'] <= p['capacity'] or not 0 <= p['requested_day'] < day or p['ready_day'] != p['requested_day']+c.construction_delay_days or p['ready_day'] < day:
                raise ValueError('enterprise construction schedule/capacity invalid')
