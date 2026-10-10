"""Versioned longitudinal design assumptions, Gregorian clock and rate conversion.

All rates, cohorts, histories and transition costs below are synthetic design or
stress assumptions. They are not empirical calibration or real-company forecasts.
A tick remains one calendar day; work is restricted by the declared calendar.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, fields
from datetime import date, timedelta
import calendar
import math


def interval_probability(probability: float, exposure: float, reference_exposure: float) -> float:
    """Independent constant hazard: 1-(1-p)**(exposure/reference_exposure)."""
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in (probability,exposure,reference_exposure)):
        raise ValueError('probability/exposure must be finite numeric values')
    if not 0<=probability<=1 or exposure<0 or reference_exposure<=0:
        raise ValueError('invalid probability or exposure unit')
    if exposure==0 or probability==0:return 0.
    if probability==1:return 1.
    return -math.expm1(math.log1p(-probability)*exposure/reference_exposure)


def retention(half_life_days: float, elapsed_days: float=1.) -> float:
    if isinstance(half_life_days,bool) or not isinstance(half_life_days,(int,float)) or not math.isfinite(half_life_days) or half_life_days<=0:
        raise ValueError('half life must be positive finite calendar days')
    if isinstance(elapsed_days,bool) or not isinstance(elapsed_days,(int,float)) or not math.isfinite(elapsed_days) or elapsed_days<0:
        raise ValueError('elapsed calendar days must be nonnegative finite')
    return math.exp(-math.log(2)*elapsed_days/half_life_days)


def anniversary(value: date, years: int) -> date:
    if type(years) is not int or years<0:raise ValueError('years must be a nonnegative integer')
    year=value.year+years
    return value.replace(year=year,day=min(value.day,calendar.monthrange(year,value.month)[1]))


def birth_for_age(current: date, age_years: float) -> date:
    """Synthetic age to a day-resolution birthday, using real calendar years.

    Integer ages use the same month/day in the earlier year (February 29 clamps
    to February 28). Fractional ages interpolate the two preceding calendar
    anniversaries and round to one day; no average 365.2425-day year is used.
    """
    if isinstance(age_years,bool) or not isinstance(age_years,(int,float)) or not math.isfinite(age_years) or age_years<0:raise ValueError('age must be finite nonnegative calendar years')
    whole=math.floor(age_years)
    def back(years):
        year=current.year-years
        return current.replace(year=year,day=min(current.day,calendar.monthrange(year,current.month)[1]))
    recent,older=back(whole),back(whole+1)
    return recent-timedelta(days=round((age_years-whole)*(recent-older).days))


@dataclass(frozen=True)
class LongitudinalConfig:
    schema_version: int = 1
    world_context: str | None = None
    calendar_start: str = '2020-01-01'
    work_weekdays: tuple[int,...] = (0,1,2,3,4)
    holidays: tuple[str,...] = ()
    fiscal_year_start_month: int = 1
    organization_initial_age_years: float = 0.
    history_mode: str = 'conditional-synthetic-state'
    initial_memory: float = .2
    initial_routine_strength: float = .2
    historical_verified_credit_per_member: float = 0.
    initial_age_min_years: float = 25.
    initial_age_max_years: float = 60.
    entrant_age_years: float = 25.
    retirement_age_years: float = 65.
    annual_exit_probability: float = .12
    annual_vacancy_fill_probability: float = .99
    annual_leave_workdays: float = 0.
    credit_half_life_days: float = 730.
    skill_forgetting_half_life_days: float = 730.
    skill_learning_per_work_year: float = 1.
    memory_half_life_days: float = 3650.
    memory_transfer_fraction: float = .4
    memory_learning_per_work_year: float = .2
    routine_adjustment_half_life_days: float = 180.
    max_active_members: int | None = None
    max_people_ever: int | None = None
    workforce_targets: tuple[tuple[int,int],...] = ()
    exit_schedule: tuple[tuple[int,int,str],...] = ()
    guild_moves: tuple[tuple[int,int,int],...] = ()
    guild_mergers: tuple[tuple[int,int,int],...] = ()
    demand_schedule: tuple[tuple[int,float],...] = ()
    pre_adoption_regime: str = 'hierarchy'
    pre_adoption_backend: str = 'central'
    adoption_mode: str = 'fixed'
    adoption_day: int = 0
    guild_adoption_days: tuple[tuple[int,int],...] = ()
    observable_trigger: str = 'review_backlog_per_active_member'
    trigger_threshold: float = 2.
    trigger_not_before_day: int = 0
    transition_days: int = 90
    training_hours_per_workday: float = .1
    migration_resource_units_per_member: float = 2.
    dual_run_resource_units_per_member_workday: float = .05
    maintenance_resource_units_per_member_workday: float = .01
    initial_cash_per_member: float = 100.
    payroll_resource_units_per_member_workday: float = .5
    revenue_resource_units_per_work_unit: float = 1.
    recruitment_resource_units_per_person: float = 1.
    closure_after_unfunded_workdays: int = 20
    closure_day: int | None = None
    suspension_day: int | None = None
    work_creation_stop_day: int | None = None

    def to_dict(self):return asdict(self)

    @classmethod
    def from_dict(cls,value):
        if isinstance(value,cls):return value
        if not isinstance(value,dict):raise ValueError('longitudinal configuration must be an object')
        unknown=set(value)-{f.name for f in fields(cls)}
        if unknown:raise ValueError('unknown longitudinal fields: '+str(sorted(unknown)))
        data=dict(value)
        for name in ('work_weekdays','holidays'):
            if name in data:data[name]=tuple(data[name])
        for name in ('workforce_targets','exit_schedule','guild_moves','guild_mergers','demand_schedule','guild_adoption_days'):
            if name in data:data[name]=tuple(tuple(row) for row in data[name])
        return cls(**data)

    def validate(self,initial_n:int,horizon:int,guilds:int):
        if self.schema_version!=1 or type(self.schema_version) is not int:raise ValueError('unknown longitudinal schema')
        if self.world_context is not None and (not isinstance(self.world_context,str)
                or not self.world_context or len(self.world_context)>256):
            raise ValueError('world_context must be a nonempty string of at most 256 characters')
        if not isinstance(self.calendar_start,str):raise ValueError('calendar_start must be an ISO date string')
        start=date.fromisoformat(self.calendar_start)
        if start+timedelta(days=horizon)>date(9998,12,31):raise ValueError('calendar horizon overflows')
        if not self.work_weekdays or len(set(self.work_weekdays))!=len(self.work_weekdays) or any(type(v) is not int or not 0<=v<=6 for v in self.work_weekdays):raise ValueError('work_weekdays must be unique weekday integers')
        if len(set(self.holidays))!=len(self.holidays):raise ValueError('holidays must be unique explicit Gregorian dates')
        for value in self.holidays:
            if not isinstance(value,str):raise ValueError('holidays must be ISO date strings')
            date.fromisoformat(value)
        if self.history_mode!='conditional-synthetic-state':raise ValueError('historical generation is not implemented; provide explicit conditional state assumptions')
        integers={'fiscal_year_start_month':(1,12),'adoption_day':(0,horizon),'trigger_not_before_day':(0,horizon),'transition_days':(1,50000),'closure_after_unfunded_workdays':(1,50000)}
        for k,(lo,hi) in integers.items():
            v=getattr(self,k)
            if type(v) is not int or not lo<=v<=hi:raise ValueError(k+' outside permitted integer range')
        for k in ('closure_day','suspension_day','work_creation_stop_day'):
            v=getattr(self,k)
            if v is not None and (type(v) is not int or not 0<=v<=horizon):raise ValueError(k+' outside horizon')
        caps={'max_active_members':(initial_n,100_000_000),'max_people_ever':(initial_n,500_000_000)}
        for k,(lo,hi) in caps.items():
            v=getattr(self,k)
            if v is not None and (type(v) is not int or not lo<=v<=hi):raise ValueError(k+' outside capacity range')
        active_cap=self.max_active_members or initial_n
        ever_cap=self.max_people_ever or initial_n*5
        if ever_cap<active_cap:raise ValueError('ever-person capacity is below active capacity')
        bounds={'organization_initial_age_years':(0,500),'initial_memory':(0,2),'initial_routine_strength':(0,1),'historical_verified_credit_per_member':(0,1e9),'initial_age_min_years':(16,90),'initial_age_max_years':(16,90),'entrant_age_years':(16,90),'retirement_age_years':(17,100),'annual_exit_probability':(0,1),'annual_vacancy_fill_probability':(0,1),'annual_leave_workdays':(0,250),'credit_half_life_days':(.1,1e6),'skill_forgetting_half_life_days':(.1,1e6),'skill_learning_per_work_year':(0,10),'memory_half_life_days':(.1,1e6),'memory_transfer_fraction':(0,1),'memory_learning_per_work_year':(0,10),'routine_adjustment_half_life_days':(.1,1e6),'trigger_threshold':(0,1e12),'training_hours_per_workday':(0,1),'migration_resource_units_per_member':(0,1e6),'dual_run_resource_units_per_member_workday':(0,1e6),'maintenance_resource_units_per_member_workday':(0,1e6),'initial_cash_per_member':(0,1e9),'payroll_resource_units_per_member_workday':(0,1e6),'revenue_resource_units_per_work_unit':(0,1e6),'recruitment_resource_units_per_person':(0,1e6)}
        for k,(lo,hi) in bounds.items():
            v=getattr(self,k)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError(k+' outside finite range')
        if self.organization_initial_age_years>0 and not any((self.initial_memory,self.initial_routine_strength,self.historical_verified_credit_per_member)):
            raise ValueError('organization age requires explicit nonzero conditional history mechanisms')
        if self.initial_age_min_years>self.initial_age_max_years or self.entrant_age_years>=self.retirement_age_years:raise ValueError('invalid entrant/initial cohort ages')
        if self.pre_adoption_regime not in {'equal','linear','sublinear','hierarchy','hierarchy_tenure'} or self.pre_adoption_backend not in {'central','witness','consensus'}:raise ValueError('unknown pre-adoption policy/backend')
        if self.adoption_mode not in {'never','fixed','staged','observable'}:raise ValueError('unknown adoption mode')
        if self.observable_trigger not in {'review_backlog_per_active_member','active_members','last_work_per_present_member'}:raise ValueError('trigger must use declared current/past observables')
        for name,width in [('workforce_targets',2),('exit_schedule',3),('guild_moves',3),('guild_mergers',3),('demand_schedule',2)]:
            rows=getattr(self,name)
            if any(len(r)!=width or type(r[0]) is not int or not 0<=r[0]<horizon for r in rows):raise ValueError(name+' has invalid date/shape')
            if tuple(sorted(rows,key=lambda r:r[0]))!=rows:raise ValueError(name+' must be sorted by day')
        if any(type(n) is not int or not 0<=n<=active_cap for _,n in self.workforce_targets):raise ValueError('workforce target exceeds active capacity')
        if any(type(i) is not int or not 0<=i<ever_cap or reason not in {'exit','retirement'} for _,i,reason in self.exit_schedule):raise ValueError('invalid scheduled exit')
        if any(type(i) is not int or not 0<=i<ever_cap or type(g) is not int or not 0<=g<guilds for _,i,g in self.guild_moves):raise ValueError('guild moves require bounded person/domain')
        for name in ('workforce_targets','demand_schedule'):
            if len({r[0] for r in getattr(self,name)})!=len(getattr(self,name)):raise ValueError(name+' has duplicate dates')
        if any(type(a) is not int or type(b) is not int or not 0<=a<guilds or not 0<=b<guilds or a==b for _,a,b in self.guild_mergers):raise ValueError('guild mergers require distinct known domains')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=10 for _,v in self.demand_schedule):raise ValueError('demand multiplier must be finite in [0,10]')
        if any(len(r)!=2 or type(r[0]) is not int or not 0<=r[0]<guilds or type(r[1]) is not int or not 0<=r[1]<=horizon for r in self.guild_adoption_days):raise ValueError('invalid staged adoption dates')
        if len({r[0] for r in self.guild_adoption_days})!=len(self.guild_adoption_days):raise ValueError('duplicate guild adoption date')
        if self.adoption_mode=='staged' and {g for g,_ in self.guild_adoption_days}!=set(range(guilds)):raise ValueError('staged adoption must prescribe every guild')
        return self


class GregorianClock:
    def __init__(self,config:LongitudinalConfig):
        self.config=config;self.start=date.fromisoformat(config.calendar_start);self.holidays={date.fromisoformat(v) for v in config.holidays};self._workdays={}
    def at(self,day:int)->date:return self.start+timedelta(days=day)
    def is_workday(self,day:int)->bool:
        value=self.at(day);return value.weekday() in self.config.work_weekdays and value not in self.holidays
    def days_in_year(self,day:int)->int:return 366 if calendar.isleap(self.at(day).year) else 365
    def workdays_in_year(self,day:int)->int:
        year=self.at(day).year
        if year not in self._workdays:
            d=date(year,1,1);last=date(year+1,1,1);n=0
            while d<last:
                n+=d.weekday() in self.config.work_weekdays and d not in self.holidays;d+=timedelta(days=1)
            self._workdays[year]=n
        return self._workdays[year]
    def anniversary_day(self,day:int,years:int)->int:return (anniversary(self.at(day),years)-self.start).days
    def fiscal_year(self,day:int)->int:
        d=self.at(day);return d.year-int(d.month<self.config.fiscal_year_start_month)


def estimate_longitudinal(config):
    """Conservative planning estimates, explicitly not measured certification.

    The event bound uses the maximum declared active capacity and real workdays;
    diagnostic cadence does not reduce scientifically persisted event evidence.
    Four full ledger/state generations cover working data, two checkpoints and
    final serialization. SQLite overhead estimates must be checked by pilot.
    """
    l=config.longitudinal
    if l is None:raise ValueError('longitudinal estimate requires explicit opt-in')
    clock=GregorianClock(l)
    stop=config.days if l.work_creation_stop_day is None else l.work_creation_stop_day
    workdays=sum(clock.is_workday(d) for d in range(stop))
    active=l.max_active_members or config.n;ever=l.max_people_ever or config.n*5
    events=active*workdays
    growth=config.enterprise_growth
    guilds=max(config.guilds,math.ceil(growth.maximum_organization_capacity/growth.members_per_guild)) if growth is not None else config.guilds
    ledger=1_048_576+events*2400+guilds*config.days*600+ever*800
    state=1_048_576+ever*2400+config.days*4000
    return {'work_events':events,'creation_workdays':workdays,'maximum_active_members':active,'maximum_people_ever':ever,
            'estimated_peak_rss_bytes':268_435_456+ever*4096+active*3000,
            'estimated_single_snapshot_bytes':ledger+state,'estimated_output_bytes':4*(ledger+state),
            'estimate_status':'unvalidated conservative design estimate; pilot and actual guards remain mandatory',
            'persisted_event_bytes_assumption':2400,'retained_generations_assumption':4}
