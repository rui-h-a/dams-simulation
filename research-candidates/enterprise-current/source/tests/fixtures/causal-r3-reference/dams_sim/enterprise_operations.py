"""Synthetic operating mechanism on detailed agents, never a firm reconstruction.

Orders are priced contracts, actual agent production fulfils them, deliveries
settle later, and only past settled cash flows permit financed growth. Country
contexts describe calendars, compliance and logistics, never individual traits.
All numeric coefficients are scenario assumptions pending empirical validation.
"""
from __future__ import annotations

import copy
import dataclasses
from datetime import datetime, time, timedelta
import math

VERSION = 'longitudinal-enterprise-causal-3'


@dataclasses.dataclass(frozen=True)
class CountryContext:
    key: str = 'context-a'
    utc_offset_hours: int = 0
    work_start_local: int = 9
    work_end_local: int = 17
    legal_resource_units_per_worker_workday: float = 0.
    logistics_delay_days: int = 1
    logistics_resource_units_per_unit: float = 0.
    communication_workday_units: float = 0.
    # This is an explicit scenario applicability flag, not a compliance claim.
    export_allowed: bool = True


@dataclasses.dataclass(frozen=True)
class EnterpriseOperatingConfig:
    schema_version: int = 1
    base_orders_per_workday: float = 30.
    initial_price_resource_units: float = 1.
    competitor_price_resource_units: float = 1.
    demand_price_elasticity: float = 1.
    market_noise_log_sd: float = .1
    price_adjustment_rate: float = .01
    minimum_price_resource_units: float = .1
    maximum_price_resource_units: float = 100.
    backlog_target_workdays: float = 2.
    order_expiry_days: int = 60
    productive_units_per_installed_seat_workday: float = 1.
    material_resource_units_per_product_unit: float = .05
    management_span: int = 8
    coordination_workday_units_per_layer: float = .02
    management_overload_workday_units_per_report: float = .005
    recruitment_lead_days: int = 14
    training_lead_days: int = 20
    entrant_training_workday_units: float = .15
    capital_credit_limit_resource_units: float = 0.
    capital_draw_resource_units: float = 0.
    annual_interest_rate: float = .05
    finance_minimum_past_margin: float = .1
    countries: tuple[CountryContext, ...] = (CountryContext(),)

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, cls):return value
        if not isinstance(value, dict) or set(value)-{f.name for f in dataclasses.fields(cls)}:
            raise ValueError('unknown enterprise operating configuration')
        value=dict(value)
        if 'countries' in value:value['countries']=tuple(CountryContext(**v) for v in value['countries'])
        return cls(**value)

    def validate(self, config):
        if type(self.schema_version) is not int or self.schema_version != 1 or config.enterprise_growth is None:
            raise ValueError('causal operations require explicit enterprise growth mode')
        if not self.countries or len({c.key for c in self.countries}) != len(self.countries):
            raise ValueError('country contexts must be distinct, declared and nonempty')
        for name in ('order_expiry_days','management_span','recruitment_lead_days','training_lead_days'):
            v=getattr(self,name)
            if type(v) is not int or not 1<=v<=100_000:raise ValueError('invalid operation delay/span: '+name)
        for f in dataclasses.fields(self):
            v=getattr(self,f.name)
            if f.name in ('schema_version','countries','order_expiry_days','management_span','recruitment_lead_days','training_lead_days'):continue
            if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1e9:raise ValueError('invalid operating coefficient: '+f.name)
        if not 0<self.minimum_price_resource_units<=self.initial_price_resource_units<=self.maximum_price_resource_units or self.competitor_price_resource_units<=0:
            raise ValueError('prices need a positive resource unit and bounded interval')
        if self.productive_units_per_installed_seat_workday<=0 or self.backlog_target_workdays<=0 or self.market_noise_log_sd>5 or self.entrant_training_workday_units>.4 or self.price_adjustment_rate>1:
            raise ValueError('invalid productive capacity, price response or training reservation')
        if not 0<=self.finance_minimum_past_margin<=1 or self.annual_interest_rate>1:
            raise ValueError('invalid credit mechanism')
        for c in self.countries:
            if not isinstance(c,CountryContext) or not isinstance(c.key,str) or not c.key or type(c.export_allowed) is not bool:raise ValueError('invalid country context')
            if any(type(v) is not int for v in (c.utc_offset_hours,c.work_start_local,c.work_end_local,c.logistics_delay_days)) or not -12<=c.utc_offset_hours<=14 or not 0<=c.work_start_local<c.work_end_local<=24 or not 1<=c.logistics_delay_days<=100_000:
                raise ValueError('invalid contextual calendar/logistics delay')
            for v in (c.legal_resource_units_per_worker_workday,c.logistics_resource_units_per_unit,c.communication_workday_units):
                if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1e6:raise ValueError('invalid contextual resource cost')
            if c.communication_workday_units>.4:raise ValueError('communication consumes a bounded day fraction')
        # WorldRandom.normal has |z| <= sqrt(108 ln 2): its smallest
        # SHA-256-derived uniform is 2^-54. Bound every declared price, the
        # allowed demand multiplier (<=10), and horizon-wide resource stocks.
        # This rejects unrepresentable configurations; it never clips orders.
        log_float_max = math.log(float.fromhex('0x1.fffffffffffffp+1023'))
        ratio_log = math.log(self.competitor_price_resource_units)-math.log(self.minimum_price_resource_units)
        price_log = self.demand_price_elasticity*max(0.,ratio_log)
        if ratio_log>log_float_max-math.log(16) or price_log>log_float_max-math.log(16):
            raise ValueError('price-elasticity order envelope exceeds finite arithmetic')
        if self.base_orders_per_workday:
            noise_log = self.market_noise_log_sd*math.sqrt(108*math.log(2))-.5*self.market_noise_log_sd**2
            unit_bound = max(1.,self.maximum_price_resource_units,
                self.material_resource_units_per_product_unit+max(c.logistics_resource_units_per_unit for c in self.countries))
            log_stock = (math.log(self.base_orders_per_workday)+math.log(10)+price_log+
                noise_log+math.log(max(1,config.days))+math.log(unit_bound))
            if log_stock>log_float_max-math.log(16):
                raise ValueError('horizon-wide order/resource envelope exceeds finite arithmetic')
        return self


class EnterpriseOperatingState:
    def __init__(self, config):
        self.c=config.enterprise_operations
        self.price=self.c.initial_price_resource_units
        self.orders=[]
        self.shipments=[]
        self.vacancy_since={}
        self.debt=0.
        self.borrowed=0.
        self.interest_accrued=0.
        self.interest_paid=0.
        self.orders_arrived=0.
        self.orders_expired=0.
        self.product_units=0.
        self.delivered_units=0.
        self.delivered_revenue=0.
        self.material_paid=0.
        self.logistics_paid=0.
        self.last_day=-1
        self.day_inputs=None

    def backlog(self):return math.fsum(v['remaining'] for v in self.orders)

    def expansion_allowed(self, growth):
        # Observed contract stock and installed capacity, not latent agent skill.
        return self.backlog()>growth.capacity*self.c.productive_units_per_installed_seat_workday*self.c.backlog_target_workdays

    def start_day(self, day, *, working, creating, operating, cash, demand_multiplier, capacity, growth, noise, year_days):
        if type(day) is not int or day!=self.last_day+1:raise ValueError('operating day replayed or skipped')
        if self.day_inputs is not None:raise ValueError('previous operating day incomplete')
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in (cash,demand_multiplier,capacity)):
            raise ValueError('invalid operating day input')
        if type(noise) not in (int,float) or not math.isfinite(noise) or type(year_days) is not int or not 365<=year_days<=366:
            raise ValueError('invalid market/calendar input')
        received=math.fsum(v['revenue'] for v in self.shipments if v['ready_day']<=day)
        delivered=math.fsum(v['units'] for v in self.shipments if v['ready_day']<=day)
        self.shipments=[v for v in self.shipments if v['ready_day']>day]
        self.delivered_units+=delivered;self.delivered_revenue+=received
        expired=math.fsum(v['remaining'] for v in self.orders if day>=v['expiry_day'])
        self.orders=[v for v in self.orders if day<v['expiry_day']];self.orders_expired+=expired
        interest=self.debt*self.c.annual_interest_rate/year_days
        self.debt+=interest;self.interest_accrued+=interest
        arrivals=0.
        if working and creating and operating:
            # Same public market shock across policies; zero demand is really zero.
            arrivals=self.c.base_orders_per_workday*demand_multiplier*(self.c.competitor_price_resource_units/self.price)**self.c.demand_price_elasticity*math.exp(self.c.market_noise_log_sd*noise-.5*self.c.market_noise_log_sd**2)
            if arrivals:
                self.orders.append({'created_day':day,'expiry_day':day+self.c.order_expiry_days,'price':self.price,'remaining':arrivals})
            self.orders_arrived+=arrivals
        margin=(growth.revenue_signal-growth.cost_signal)/max(growth.revenue_signal,growth.cost_signal,1e-12)
        borrowed=0.
        if (operating and working and day%growth.c.review_interval_days==0 and growth.observed_workdays
                and margin>=self.c.finance_minimum_past_margin and self.expansion_allowed(growth)
                and cash+received<max(growth.cost_signal,growth.target)*growth.c.reserve_workdays):
            borrowed=min(self.c.capital_draw_resource_units,max(0.,self.c.capital_credit_limit_resource_units-self.debt))
            self.borrowed+=borrowed;self.debt+=borrowed
        self.day_inputs={'day':day,'working':working,'creating':creating,'operating':operating,'cash_before':cash,'demand_multiplier':demand_multiplier,'capacity':capacity,'noise':noise,'year_days':year_days,
                         'arrivals':arrivals,'expired':expired,'received':received,'delivered':delivered,'borrowed':borrowed,'interest_due':interest,'backlog':self.backlog(),'quoted_price':self.price}
        return received,borrowed,copy.deepcopy(self.day_inputs)

    def context(self, site):return self.c.countries[site%len(self.c.countries)]

    def calendar_fraction(self, clock, site, day):
        # Reuses the prior enterprise allocator's local-clock mapping. Country
        # names never enter random keys or individual ability/credibility.
        c=self.context(site);available=0
        for hour in range(day*24,(day+1)*24):
            when=datetime.combine(clock.at(0),time())+timedelta(hours=hour+c.utc_offset_hours)
            if when.weekday() in clock.config.work_weekdays and when.date().isoformat() not in clock.config.holidays and c.work_start_local<=when.hour<c.work_end_local:available+=1
        return available/(c.work_end_local-c.work_start_local) if c.export_allowed else 0.

    def coordination(self, active, guilds, departments, sites, site):
        c=self.context(site)
        managers=max(1,guilds)
        reports=active/managers
        layers=1+math.ceil(math.log(max(1,departments),self.c.management_span)) if self.c.management_span>1 else departments
        return min(.4,self.c.coordination_workday_units_per_layer*layers+self.c.management_overload_workday_units_per_report*max(0,reports-self.c.management_span)+c.communication_workday_units*(sites>1))

    def training(self, person, day):
        return self.c.entrant_training_workday_units if person['entered_day']>0 and day-person['entered_day']<self.c.training_lead_days else 0.

    def recruit_ready(self, slot, day):
        if str(slot) not in self.vacancy_since:self.vacancy_since[str(slot)]=day
        return day-self.vacancy_since[str(slot)]>=self.c.recruitment_lead_days

    def filled(self,slot):self.vacancy_since.pop(str(slot),None)

    def productive_limit(self, capacity, cash, sites):
        cost=self.c.material_resource_units_per_product_unit+max(self.context(s).logistics_resource_units_per_unit for s in range(sites))
        return min(self.backlog(),capacity*self.c.productive_units_per_installed_seat_workday,cash/cost if cost else math.inf)

    def finish_day(self, day, produced_by_site, cash, *, interest_paid):
        if self.day_inputs is None or self.day_inputs['day']!=day:raise ValueError('operating settlement missing begin phase')
        output=math.fsum(produced_by_site.values());material=output*self.c.material_resource_units_per_product_unit
        logistics=math.fsum(amount*self.context(site).logistics_resource_units_per_unit for site,amount in produced_by_site.items())
        if min(output,material,logistics,interest_paid)<0 or material+logistics>cash+1e-8 or output>self.backlog()+1e-8 or interest_paid>self.debt+1e-8:
            raise ValueError('operating resource/contract overbooking')
        self.debt=max(0.,self.debt-interest_paid);self.interest_paid+=interest_paid
        shipments=[]
        for site,amount in sorted(produced_by_site.items()):
            context=self.context(site);remaining=amount
            while remaining>1e-12:
                order=self.orders[0];take=min(remaining,order['remaining'])
                shipment={'dispatch_day':day,'ready_day':day+context.logistics_delay_days,'site':site,'units':take,'revenue':take*order['price']}
                self.shipments.append(shipment);shipments.append(copy.deepcopy(shipment))
                order['remaining']-=take;remaining-=take
                if order['remaining']<=1e-12:self.orders.pop(0)
        self.product_units+=output;self.material_paid+=material;self.logistics_paid+=logistics
        if self.day_inputs['working'] and self.day_inputs['operating']:
            target=max(1e-12,self.day_inputs['capacity']*self.c.productive_units_per_installed_seat_workday*self.c.backlog_target_workdays)
            direction=max(-1.,min(1.,self.backlog()/target-1.))
            self.price=max(self.c.minimum_price_resource_units,min(self.c.maximum_price_resource_units,self.price*math.exp(self.c.price_adjustment_rate*direction)))
        evidence={'inputs':copy.deepcopy(self.day_inputs),'produced_by_site':[[s,v] for s,v in sorted(produced_by_site.items())],'material':material,'logistics':logistics,'interest_paid':interest_paid,'shipments_created':shipments,'price_next':self.price}
        self.day_inputs=None;self.last_day=day
        return material+logistics,evidence

    def to_dict(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='c'})

    @classmethod
    def restore(cls,config,value,day):
        obj=cls(config)
        if not isinstance(value,dict) or set(value)!=set(obj.to_dict()):raise ValueError('operating state inventory differs')
        for k,v in value.items():setattr(obj,k,copy.deepcopy(v))
        obj.validate(day);return obj

    def validate(self,day):
        if type(self.last_day) is not int or self.last_day!=day-1 or self.day_inputs is not None:raise ValueError('operating checkpoint not committed')
        for key in ('price','debt','borrowed','interest_accrued','interest_paid','orders_arrived','orders_expired','product_units','delivered_units','delivered_revenue','material_paid','logistics_paid'):
            v=getattr(self,key)
            if type(v) not in (int,float) or not math.isfinite(v) or v<0:raise ValueError('invalid operating stock: '+key)
        if not self.c.minimum_price_resource_units<=self.price<=self.c.maximum_price_resource_units:raise ValueError('operating price outside interval')
        if not math.isclose(self.debt,self.borrowed+self.interest_accrued-self.interest_paid,rel_tol=1e-10,abs_tol=1e-8):raise ValueError('credit balance not conserved')
        if not math.isclose(self.orders_arrived,self.orders_expired+self.product_units+self.backlog(),rel_tol=1e-10,abs_tol=1e-8):raise ValueError('order units not conserved')
        if not math.isclose(self.product_units,self.delivered_units+math.fsum(v['units'] for v in self.shipments),rel_tol=1e-10,abs_tol=1e-8):raise ValueError('product delivery units not conserved')
        for v in self.orders:
            if set(v)!={'created_day','expiry_day','price','remaining'} or type(v['created_day']) is not int or type(v['expiry_day']) is not int or not 0<=v['created_day']<day or v['expiry_day']!=v['created_day']+self.c.order_expiry_days or v['remaining']<=0 or not math.isfinite(v['remaining']) or not self.c.minimum_price_resource_units<=v['price']<=self.c.maximum_price_resource_units:raise ValueError('invalid retained order')
        for v in self.shipments:
            if set(v)!={'dispatch_day','ready_day','site','units','revenue'} or any(type(v[k]) is not int for k in ('dispatch_day','ready_day','site')) or not 0<=v['dispatch_day']<day or v['ready_day']!=v['dispatch_day']+self.context(v['site']).logistics_delay_days or v['ready_day']<day or any(type(v[k]) not in (int,float) or not math.isfinite(v[k]) or v[k]<0 for k in ('units','revenue')):raise ValueError('invalid retained shipment')
        for slot,when in self.vacancy_since.items():
            if not isinstance(slot,str) or not slot.isdecimal() or str(int(slot))!=slot or type(when) is not int or not 0<=when<day:raise ValueError('invalid vacancy lead-time stock')
