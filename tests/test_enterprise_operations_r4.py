"""Independent engineering controls, never formal samples or empirical calibration."""
from __future__ import annotations
import copy
import dataclasses
from decimal import Decimal, localcontext
import importlib
import json
import math
import os
from fractions import Fraction
from pathlib import Path
import random
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest

from dams_sim.enterprise_operations import CountryContext, EnterpriseOperatingConfig, EnterpriseOperatingState, VERSION
from dams_sim.longitudinal import GregorianClock, LongitudinalConfig
from dams_sim.model import Model
from dams_sim.storage import atomic_csv, canonical
from dams_sim.longitudinal_model import verify_snapshot
from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence, _RawDailyAccounting
from test_enterprise_operations import config as operating_config

OWN=Path(os.environ.get('DAMS_OPS_CONTROL_TEMP_ROOT',str(Path(__file__).resolve().parents[1]/'candidate-evidence')))
LEGACY=Path(__file__).resolve().parent/'fixtures/operations-r3-reference/dams_sim'

def state(**kwargs):
    c=EnterpriseOperatingConfig(base_orders_per_workday=100,market_noise_log_sd=0,price_adjustment_rate=0,**kwargs)
    c.validate(SimpleNamespace(enterprise_growth=True,days=200))
    return EnterpriseOperatingState(SimpleNamespace(enterprise_operations=c))

def begin(s, day=0, *, cash=1000, capacity=100, working=True, creating=True):
    g=SimpleNamespace(revenue_signal=0,cost_signal=0,observed_workdays=0,capacity=capacity,target=capacity,
        c=SimpleNamespace(review_interval_days=30,reserve_workdays=20))
    return s.start_day(day,working=working,creating=creating,operating=True,cash=cash,demand_multiplier=1,
        capacity=capacity,growth=g,noise=0,year_days=365)

class ScalarControls(unittest.TestCase):
    def test_domestic_work_survives_export_ban(self):
        s=state(countries=(CountryContext(export_allowed=False),))
        clock=GregorianClock(LongitudinalConfig(calendar_start='2026-10-05'))
        self.assertEqual(s.calendar_fraction(clock,0,0),1)
        begin(s);a=s.allocate_production({0:10.},100,100,1)
        self.assertEqual(a,{0:10.})
        _,e=s.finish_day(0,a,100,interest_paid=0);s.validate(1)
        self.assertTrue(all(v['destination_context']==0 for v in e['shipments_created']))
        self.assertEqual(s.product_units,10)
    def test_export_only_demand_is_blocked_not_erased(self):
        s=state(countries=(CountryContext(key='a',export_allowed=False),CountryContext(key='b')),
            destination_market_shares=(0.,1.))
        begin(s);a=s.allocate_production({0:10.},100,100,2)
        self.assertEqual(a,{0:0.});s.finish_day(0,a,100,interest_paid=0)
        self.assertEqual(s.backlog(),100);s.validate(1)
    def test_blocked_export_orders_expire_as_declared(self):
        s=state(countries=(CountryContext(key='a',export_allowed=False),CountryContext(key='b')),
            destination_market_shares=(0.,1.),order_expiry_days=2)
        for day in range(3):
            _,_,inputs=begin(s,day,creating=day==0)
            s.finish_day(day,{},1000,interest_paid=0)
        self.assertEqual(inputs['expired'],100);self.assertEqual(s.orders_expired,100);s.validate(3)
    def test_market_shares_and_origin_destination_are_used(self):
        s=state(countries=(CountryContext(key='a',export_allowed=False),CountryContext(key='b',export_allowed=False)),
            destination_market_shares=(.4,.6),material_resource_units_per_product_unit=0)
        begin(s);a=s.allocate_production({0:100.,1:100.},1000,100,2)
        self.assertAlmostEqual(a[0],40);self.assertAlmostEqual(a[1],60)
        _,e=s.finish_day(0,a,100,interest_paid=0);s.validate(1)
        self.assertTrue(all(v['site']%2==v['destination_context'] for v in e['shipments_created']))
    def test_restricted_orders_reserved_before_exporter(self):
        s=state(countries=(CountryContext(key='a'),CountryContext(key='b',export_allowed=False)),
            destination_market_shares=(.5,.5),material_resource_units_per_product_unit=0)
        begin(s);a=s.allocate_production({0:10.,1:100.},1000,100,2)
        _,e=s.finish_day(0,a,100,interest_paid=0);s.validate(1)
        self.assertAlmostEqual(a[1],50)
        self.assertTrue(all(v['destination_context']==1 for v in e['shipments_created'] if v['site']==1))
    def test_illegal_crossborder_settlement_refuses_before_mutation(self):
        s=state(countries=(CountryContext(key='a',export_allowed=False),CountryContext(key='b')),
            destination_market_shares=(0.,1.))
        begin(s);before=s.to_dict()
        with self.assertRaisesRegex(ValueError,'legally eligible'):s.finish_day(0,{0:1.},100,interest_paid=0)
        self.assertEqual(before,s.to_dict())
    def test_idle_high_cost_site_cannot_constrain_active_cheap_site(self):
        s=state(countries=(CountryContext(key='a'),CountryContext(key='b',logistics_resource_units_per_unit=99)),
            material_resource_units_per_product_unit=1)
        begin(s,cash=10)
        self.assertEqual(s.allocate_production({0:10.,1:0.},100,10,2),{0:10.,1:0.})
        paid,_=s.finish_day(0,{0:10.,1:0.},10,interest_paid=0)
        self.assertEqual(paid,10);s.validate(1)
    def test_weighted_cash_constraint_independent_decimal_oracle(self):
        s=state(countries=(CountryContext(key='a'),CountryContext(key='b',logistics_resource_units_per_unit=2)),
            material_resource_units_per_product_unit=1)
        begin(s);a=s.allocate_production({0:10.,1:20.},100,5,2)
        with localcontext() as ctx:
            ctx.prec=60
            expected=Decimal(5)/(Decimal(10)+Decimal(20)*3)
            for site,potential in ((0,10),(1,20)):
                self.assertLessEqual(Decimal.from_float(a[site]),Decimal(potential)*expected)
                self.assertAlmostEqual(a[site],float(Decimal(potential)*expected),places=12)
        paid,_=s.finish_day(0,a,5,interest_paid=0)
        self.assertLessEqual(paid,5);s.validate(1)
    def test_seeded_scalar_budget_controls_two_hundred_cases(self):
        # Oracle checks all constraints from decimal input data, not shared methods.
        rng=random.Random(720104)
        for k in range(200):
            costs=[rng.randrange(0,10) for _ in range(4)]
            material=rng.randrange(0,5);cash=rng.randrange(0,1000);capacity=rng.randrange(0,200)
            potentials={s:float(rng.randrange(0,100)) for s in range(4)}
            s=state(countries=tuple(CountryContext(key=str(i),logistics_resource_units_per_unit=c) for i,c in enumerate(costs)),
                material_resource_units_per_product_unit=material)
            begin(s);out=s.allocate_production(potentials,capacity,cash,4)
            d={i:Decimal.from_float(v) for i,v in out.items()}
            self.assertLessEqual(sum(d.values()),min(Decimal(100),Decimal(capacity)))
            self.assertLessEqual(sum(d[i]*Decimal(material+costs[i]) for i in d),Decimal(cash))
            self.assertTrue(all(0<=out[i]<=potentials[i] for i in out))
            s.finish_day(0,out,cash,interest_paid=0);s.validate(1)
    def test_zero_potential_and_zero_cost_have_no_invalid_division(self):
        s=state(material_resource_units_per_product_unit=0);begin(s)
        self.assertEqual(s.allocate_production({0:0.},100,0,1),{0:0.})
        self.assertEqual(s.allocate_production({0:20.},100,0,1),{0:20.})
    def test_zero_cash_positive_cost_stops_output(self):
        s=state(material_resource_units_per_product_unit=1);begin(s)
        self.assertEqual(s.allocate_production({0:20.},100,0,1),{0:0.})
    def test_actual_cash_settlement_overbooking_is_still_refused(self):
        s=state(material_resource_units_per_product_unit=1);begin(s,cash=1)
        with self.assertRaisesRegex(ValueError,'overbooking'):s.finish_day(0,{0:2.},1,interest_paid=0)
    def test_invalid_market_config_and_potentials_refuse(self):
        for shares in ((1.,1.),(-1.,2.),(float('nan'),),(True,),[1.]):
            with self.subTest(shares=shares),self.assertRaises(ValueError):state(destination_market_shares=shares)
        s=state();begin(s)
        for potentials in ({True:1.},{-1:1.},{0:-1.},{0:float('nan')}):
            with self.subTest(potentials=potentials),self.assertRaises(ValueError):s.allocate_production(potentials,100,10,1)
    def test_no_workday_demand_calendar_or_service_is_created(self):
        s=state();clock=GregorianClock(LongitudinalConfig(calendar_start='2026-10-10'))
        self.assertEqual(s.calendar_fraction(clock,0,0),0)
        _,_,e=begin(s,working=False)
        self.assertEqual(e['arrivals'],0)
        _,_,plan=s.prepare_coordination(0,active=400,guilds=4,departments=1,sites=1,people=[],funding=1)
        self.assertEqual(plan['sites'],[]);s.finish_day(0,{},100,interest_paid=0);s.validate(1)
    def test_coordination_overload_retains_debt_and_lowers_readiness(self):
        s=state();rows=[]
        for day in range(2):
            begin(s,day)
            served,ready,plan=s.prepare_coordination(day,active=400,guilds=4,departments=1,sites=1,
                people=[(i,0,.4) for i in range(10)],funding=1)
            rows.append(ready[0]);s.finish_day(day,{},1000,interest_paid=0);s.validate(day+1)
        self.assertAlmostEqual(s.coordination_pending['0'],1.6)
        self.assertLess(rows[1],rows[0]);self.assertLess(rows[0],1)
        self.assertAlmostEqual(s.coordination_requested_units,s.coordination_served_units+sum(s.coordination_pending.values()))
    def test_old_debt_is_serviced_with_real_extra_time(self):
        s=state();begin(s)
        s.prepare_coordination(0,active=400,guilds=4,departments=1,sites=1,people=[(0,0,.4)],funding=1)
        s.finish_day(0,{},1000,interest_paid=0)
        begin(s,1);service,ready,_=s.prepare_coordination(1,active=40,guilds=4,departments=1,sites=1,people=[(0,0,.4)],funding=1)
        self.assertGreater(service[0],s.coordination_required(40,4,1,1,0));self.assertEqual(ready[0],1)
        s.finish_day(1,{},1000,interest_paid=0);self.assertEqual(s.coordination_pending,{})
    def test_service_capacity_never_makes_available_time_negative(self):
        s=state();begin(s)
        self.assertEqual(s.coordination_capacity(.95,.2),0)
        service,ready,_=s.prepare_coordination(0,active=400,guilds=4,departments=1,sites=1,people=[(0,0,0)],funding=1)
        self.assertEqual(service[0],0);self.assertEqual(ready[0],0)
    def test_partial_funding_no_free_coordination_and_debt_persists(self):
        s=state();begin(s)
        service,ready,plan=s.prepare_coordination(0,active=120,guilds=4,departments=1,sites=1,people=[(0,0,.4)],funding=.5)
        self.assertAlmostEqual(plan['sites'][0]['served'],service[0]*.5)
        self.assertAlmostEqual(ready[0],.5);s.finish_day(0,{},1000,interest_paid=0);s.validate(1)
        self.assertGreater(s.coordination_pending['0'],0)
    def test_coordination_phase_cannot_be_replayed(self):
        s=state();begin(s);kw=dict(active=30,guilds=1,departments=1,sites=1,people=[],funding=1)
        s.prepare_coordination(0,**kw)
        with self.assertRaises(ValueError):s.prepare_coordination(0,**kw)
    def test_coordination_backlog_checkpoint_exact_and_corruption_refused(self):
        s=state();begin(s);s.prepare_coordination(0,active=400,guilds=4,departments=1,sites=1,people=[(0,0,.4)],funding=1)
        s.finish_day(0,{},1000,interest_paid=0)
        config=SimpleNamespace(enterprise_operations=s.c)
        restored=EnterpriseOperatingState.restore(config,s.to_dict(),1)
        self.assertEqual(s.to_dict(),restored.to_dict())
        for amount in (-1.,float('nan'),10.):
            v=s.to_dict();v['coordination_pending']['0']=amount
            with self.assertRaises(ValueError):EnterpriseOperatingState.restore(config,v,1)
    def test_fractional_fifo_budget_rounds_down_without_erasing_contracts(self):
        s=state();s.c=dataclasses.replace(s.c,base_orders_per_workday=.1)
        begin(s,0);s.finish_day(0,{},100,interest_paid=0)
        s.c=dataclasses.replace(s.c,base_orders_per_workday=.2)
        begin(s,1)
        exact=sum((Fraction(v['remaining']) for v in s.orders),Fraction())
        out=s.allocate_production({0:s.backlog()},100,100,1)
        self.assertLessEqual(Fraction(out[0]),exact)
        _,e=s.finish_day(1,out,100,interest_paid=0);s.validate(2)
        self.assertGreater(s.backlog(),0)  # the representational residual is retained
        self.assertEqual(Fraction(out[0])+sum((Fraction(v['remaining']) for v in s.orders),Fraction()),exact)
        self.assertEqual(sum((Fraction(v['units']) for v in e['shipments_created']),Fraction()),Fraction(out[0]))
    def test_fractional_multisite_and_person_allocations_fit_exact_bounds(self):
        s=state(material_resource_units_per_product_unit=.1,
            countries=(CountryContext(key='a'),CountryContext(key='b',logistics_resource_units_per_unit=.2)))
        begin(s);pot={i:(i+1)*.1 for i in range(80)};sites={i:i%2 for i in pot}
        by_site={site:math.fsum(v for i,v in pot.items() if sites[i]==site) for site in range(2)}
        out=s.allocate_production(by_site,1.1,.1,2);people=s.allocate_people(pot,sites,out)
        actual={site:math.fsum(v for i,v in people.items() if sites[i]==site) for site in range(2)}
        self.assertTrue(all(sum((Fraction(v) for i,v in people.items() if sites[i]==site),Fraction())<=Fraction(out[site]) for site in out))
        self.assertLessEqual(sum((Fraction(v) for v in actual.values()),Fraction()),Fraction(1.1))
        exact_cost=Fraction(actual[0])*Fraction(.1)+Fraction(actual[1])*(Fraction(.1)+Fraction(.2))
        self.assertLessEqual(exact_cost,Fraction(.1));s.finish_day(0,actual,.1,interest_paid=0);s.validate(1)
    def test_recovery_rejects_ghost_site_and_allows_explicit_dynamic_inventory(self):
        s=state();begin(s);s.prepare_coordination(0,active=400,guilds=4,departments=1,sites=1,people=[(0,0,.4)],funding=1)
        s.finish_day(0,{},100,interest_paid=0);v=s.to_dict();v['coordination_pending']={'99':v['coordination_pending']['0']}
        config=SimpleNamespace(enterprise_operations=s.c,sites=1)
        with self.assertRaisesRegex(ValueError,'installed site'):EnterpriseOperatingState.restore(config,v,1)
        v=s.to_dict();v['coordination_pending']={'1':v['coordination_pending']['0']}
        with self.assertRaises(ValueError):EnterpriseOperatingState.restore(config,v,1)
        restored=EnterpriseOperatingState.restore(config,v,1,sites=2)
        self.assertEqual(restored.to_dict(),v)
        begin(restored,1);_,_,plan=restored.prepare_coordination(1,active=40,guilds=4,departments=1,sites=2,people=[(0,1,.4)],funding=1)
        self.assertEqual([v['site'] for v in plan['sites']],[1])
    def test_legacy_schema_is_not_silently_adopted(self):
        with self.assertRaises(ValueError):dataclasses.replace(state().c,schema_version=1).validate(SimpleNamespace(enterprise_growth=True,days=10))

class IntegrationControls(unittest.TestCase):
    def setUp(self):
        (OWN/'test-tmp').mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=OWN/'test-tmp');self.root=Path(self.tmp.name);self.models=[]
    def tearDown(self):
        for model in self.models:model.ledger.close()
        self.tmp.cleanup()
    def model(self,p,name):
        m=Model(p,storage_dir=self.root/name,journal_chunk_bytes=1024);self.models.append(m);return m
    def validate(self,m,name='full'):
        p=self.root/(name+'.json');m.write_final_state(p);snapshot,db=verify_snapshot(p,expected_config=m.config)
        csv=self.root/(name+'.csv');atomic_csv(csv,m.history)
        verify_daily_evidence(csv,m.config,snapshot['state'],db)
        verify_summary_evidence(json.loads(canonical(m.summary())),m.config,snapshot['state'],db)
    def test_banned_exports_do_not_stop_domestic_model_work(self):
        m=self.model(operating_config(days=12,countries=(CountryContext(export_allowed=False),)),'domestic')
        m.run();self.assertGreater(m.metrics['produced_work_units'],0);self.validate(m)
    def test_two_restricted_origins_full_market_route(self):
        p=operating_config(days=15,countries=(CountryContext(key='a',export_allowed=False),CountryContext(key='b',export_allowed=False)),destination_market_shares=(.4,.6))
        p=dataclasses.replace(p,sites=2)
        m=self.model(p,'markets');m.run();self.validate(m)
        self.assertTrue(all(v['site']%2==v['destination_context'] for v in m._long.operations.shipments))
    def test_overload_reduces_actual_output_and_persists_unmet_debt(self):
        a=self.model(operating_config(days=15,coordination_workday_units_per_layer=.4,management_overload_workday_units_per_report=.5),'debt')
        b=self.model(operating_config(days=15,coordination_workday_units_per_layer=0.,management_overload_workday_units_per_report=0.),'no-debt')
        a.run();b.run()
        self.assertGreater(sum(a._long.operations.coordination_pending.values()),0)
        self.assertLess(a.metrics['produced_work_units'],b.metrics['produced_work_units'])
        self.assertLessEqual(a.metrics['max_individual_time_booked_hours'],1+1e-12);self.validate(a)
    def test_full_model_cp_exact_with_routing_and_debt(self):
        p=operating_config(days=20,coordination_workday_units_per_layer=.5,countries=(CountryContext(export_allowed=False),))
        a=self.model(p,'full');b=self.model(p,'split');a.run();b.run(7)
        cp=self.root/'checkpoint.json';b.write_checkpoint(cp);verify_snapshot(cp,expected_config=p)
        c=Model.restore_checkpoint(cp,storage_dir=self.root/'resume',expected_config=p);self.models.append(c);c.run()
        self.assertEqual(a.semantic_digest(),c.semantic_digest());self.assertEqual(a.summary(),c.summary());self.validate(c)
    def test_partial_funding_full_model_checkpoint_exact(self):
        p=operating_config(days=8,coordination_workday_units_per_layer=.5)
        p=dataclasses.replace(p,longitudinal=dataclasses.replace(p.longitudinal,initial_cash_per_member=.02))
        a=self.model(p,'partial-full');b=self.model(p,'partial-split');a.run();b.run(1)
        phase=next(json.loads(row[-1]) for row in b.ledger.rows('journal') if row[2]=='enterprise_coordination_begin')
        self.assertGreater(phase['plan']['funding_fraction'],0);self.assertLess(phase['plan']['funding_fraction'],1)
        self.assertGreater(sum(b._long.operations.coordination_pending.values()),0)
        cp=self.root/'partial-checkpoint.json';b.write_checkpoint(cp);verify_snapshot(cp,expected_config=p)
        c=Model.restore_checkpoint(cp,storage_dir=self.root/'partial-resume',expected_config=p);self.models.append(c);c.run()
        self.assertEqual(a.semantic_digest(),c.semantic_digest());self.assertEqual(a.summary(),c.summary());self.validate(c)
    def test_nonworkday_and_creation_stop_tail_full_gate(self):
        p=operating_config(days=15,coordination_workday_units_per_layer=.5)
        p=dataclasses.replace(p,longitudinal=dataclasses.replace(p.longitudinal,calendar_start='2020-01-04',work_creation_stop_day=7))
        m=self.model(p,'tail');m.run()
        self.assertEqual(m.history[0]['output_work_units'],0);self.assertEqual(m.history[1]['output_work_units'],0)
        self.assertTrue(all(v['output_work_units']==0 for v in m.history[7:]));self.validate(m)
    def test_tiny_attack_tolerance_cannot_create_negative_actual_effort(self):
        p=operating_config(days=1,coordination_workday_units_per_layer=.4,management_overload_workday_units_per_report=0.)
        p=dataclasses.replace(p,review_capacity_per_member_day=5.,appeal_capacity_per_member_day=0.,
            attack='forge',attack_budget_hours_per_day=2e-12,attack_cohort_fraction=.5,attack_start_day=0,attack_stop_day=1)
        m=self.model(p,'time-edge')
        for a in m.agents:a.care_hours=.3;a.reciprocity=0.
        m.run()
        work=[json.loads(row[-1]) for row in m.ledger.rows('journal') if row[2]=='work']
        self.assertTrue(all(v['effort']>=0 and v['produced_research_only']>=0 for v in work))
        self.assertTrue(any(v['attack_hours']>0 for v in work));self.assertLessEqual(m.metrics['max_individual_time_booked_hours'],1+1e-12)
        self.validate(m)
    def test_tampered_service_or_production_plan_is_refused(self):
        m=self.model(operating_config(days=1),'tamper');m.run()
        rows=[(r[2],r[3],json.loads(r[4])) for r in m.ledger.rows('journal') if r[1]==0]
        record=next(v for k,e,v in rows if k=='day_end')
        for kind in ('enterprise_coordination_begin','enterprise_production_plan'):
            changed=copy.deepcopy(rows);value=next(v for k,e,v in changed if k==kind)
            if kind=='enterprise_coordination_begin':value['plan']['sites'][0]['pending_after']+=1
            else:value['allocated_by_site'][0][1]+=1
            with self.subTest(kind=kind),self.assertRaises(ValueError):_RawDailyAccounting(m.config,m.state()).verify(0,changed,record)
    def test_two_single_person_sites_unbound_case_numeric_differential_exact(self):
        # Old frozen code is imported read-only under an alias; all output is owned scratch.
        alias=ModuleType('ops_legacy_r3');alias.__path__=[str(LEGACY)];sys.modules[alias.__name__]=alias
        old_config=importlib.import_module('ops_legacy_r3.config').Config
        old_model=importlib.import_module('ops_legacy_r3.model').Model
        p=operating_config(days=12)
        p=dataclasses.replace(p,n=2,guilds=2,sites=2,team_size=1,enterprise_growth=dataclasses.replace(p.enterprise_growth,initial_organization_capacity=2,maximum_organization_capacity=2,members_per_site=1,members_per_guild=1))
        q=p.to_dict();q['enterprise_operations'].pop('destination_market_shares');q['enterprise_operations'].pop('coordination_service_budget_workday_fraction');q['enterprise_operations']['schema_version']=1
        old=old_model(old_config.from_dict(q),storage_dir=self.root/'old',journal_chunk_bytes=1024);self.models.append(old)
        new=self.model(p,'new');old.run();new.run()
        self.assertEqual([dataclasses.asdict(a) for a in old.agents],[dataclasses.asdict(a) for a in new.agents])
        self.assertEqual(old.people,new.people)
        self.assertEqual(old.history,[{k:v for k,v in row.items() if k!='coordination_pending_workdays'} for row in new.history])
        for key,value in old.metrics.items():self.assertEqual(value,new.metrics[key],key)
        old_state=old._long.operations.to_dict();new_state=new._long.operations.to_dict()
        for key,value in old_state.items():
            if key in ('orders','shipments'):self.assertEqual(value,[{k:v for k,v in row.items() if k!='destination_context'} for row in new_state[key]])
            else:self.assertEqual(value,new_state[key],key)
        self.validate(new)

if __name__=='__main__':unittest.main()
