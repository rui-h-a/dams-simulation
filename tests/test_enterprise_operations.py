"""Internal causal controls. Synthetic fixtures are never empirical evidence."""
import copy
import dataclasses
import json
from pathlib import Path
import tempfile
import unittest

from dams_sim.config import Config
from dams_sim.enterprise_operations import EnterpriseOperatingConfig, CountryContext, VERSION
from dams_sim.model import Model
from dams_sim.longitudinal_model import verify_snapshot
from dams_sim.storage import canonical, atomic_csv
from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence, _RawDailyAccounting
from test_enterprise_growth import config as growth_config


def config(days=80, **operations):
    c=growth_config(days=days,minimum_target_members=2)
    c=dataclasses.replace(c,enterprise_growth=dataclasses.replace(c.enterprise_growth,maximum_organization_capacity=32),longitudinal=dataclasses.replace(c.longitudinal,max_active_members=40,max_people_ever=160,revenue_resource_units_per_work_unit=999.))
    o={'base_orders_per_workday':60.,'market_noise_log_sd':0.,'initial_price_resource_units':10.,'competitor_price_resource_units':10.,'price_adjustment_rate':0.,'productive_units_per_installed_seat_workday':1.,'recruitment_lead_days':2,'training_lead_days':4,'entrant_training_workday_units':.1,'coordination_workday_units_per_layer':.02}
    o.update(operations)
    return dataclasses.replace(c,enterprise_operations=EnterpriseOperatingConfig(**o)).validate()


class OperatingTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parent.parent/'candidate-evidence/test-tmp';root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.root=Path(self.temp.name);self.models=[]
    def tearDown(self):
        for m in self.models:m.ledger.close()
        self.temp.cleanup()
    def model(self,p,name):
        m=Model(p,storage_dir=self.root/name,journal_chunk_bytes=1024);self.models.append(m);return m
    def validate(self,m,name='final',config_history=()):
        p=self.root/(name+'.json');m.write_final_state(p);state,db=verify_snapshot(p,expected_config=m.config)
        t=self.root/(name+'.csv');atomic_csv(t,m.history)
        verify_daily_evidence(t,m.config,state['state'],db,config_history=config_history)
        verify_summary_evidence(json.loads(canonical(m.summary())),m.config,state['state'],db)
        return state,db
    def test_config_disabled_serialization_and_strict_bounds(self):
        self.assertNotIn('enterprise_operations',Config().to_dict())
        self.assertEqual(Config.from_dict(config().to_dict()),config())
        for kw in ({'recruitment_lead_days':0},{'demand_price_elasticity':float('nan')},{'countries':(CountryContext(),CountryContext())},{'countries':(CountryContext(utc_offset_hours=True),)},{'material_resource_units_per_product_unit':-1}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):config(**kw)
        with self.assertRaises(ValueError):dataclasses.replace(config(),enterprise_growth=None).validate()
    def test_orders_delivery_and_cash_are_distinct_not_instant_revenue(self):
        p=config(days=10,countries=(CountryContext(logistics_delay_days=4),))
        m=self.model(p,'world');m.run(1)
        self.assertGreater(m.metrics['produced_work_units'],0)
        self.assertEqual(m.metrics['revenue_resource_units'],0)
        self.assertGreater(len(m._long.operations.shipments),0)
        m.run();self.assertGreater(m.metrics['revenue_resource_units'],0)
        self.assertNotEqual(m.metrics['produced_work_units']*p.longitudinal.revenue_resource_units_per_work_unit,m.metrics['revenue_resource_units'])
        self.validate(m)
    def test_zero_demand_stops_actual_work_credit_input_and_growth(self):
        m=self.model(config(days=20,base_orders_per_workday=0.),'zero');m.run()
        self.assertEqual(m.metrics['produced_work_units'],0)
        self.assertEqual(m.metrics['effort_hours'],0)
        self.assertEqual(m._long.enterprise.capacity,8)
        self.assertTrue(all(json.loads(row[-1])['produced_research_only']==0 for row in m.ledger.rows('journal') if row[2]=='work'))
        self.validate(m)
    def test_demand_reduces_workload_not_innate_skill(self):
        a=self.model(config(days=5,base_orders_per_workday=.01),'low');b=self.model(config(days=5),'high')
        self.assertEqual([x.skill for x in a.agents],[x.skill for x in b.agents]);a.run();b.run()
        self.assertLess(a.metrics['produced_work_units'],b.metrics['produced_work_units'])
        self.assertLess(a.metrics['effort_hours'],b.metrics['effort_hours'])
    def test_competitor_price_and_demand_shock_cause_real_contract_change(self):
        a=self.model(config(days=12,competitor_price_resource_units=10),'a');b=self.model(config(days=12,competitor_price_resource_units=1),'b');a.run();b.run()
        self.assertGreater(a._long.operations.orders_arrived,b._long.operations.orders_arrived)
        p=config(days=18);p=dataclasses.replace(p,longitudinal=dataclasses.replace(p.longitudinal,demand_schedule=((5,0.),)))
        m=self.model(p,'shock');m.run();starts=[json.loads(r[-1]) for r in m.ledger.rows('journal') if r[2]=='enterprise_market_begin']
        self.assertTrue(all(v['arrivals']==0 for v in starts[5:]));self.validate(m)
    def test_capacity_material_and_contract_constraints_conserve_resources(self):
        m=self.model(config(days=12,productive_units_per_installed_seat_workday=.01,material_resource_units_per_product_unit=3.),'cap');m.run()
        self.assertTrue(all(r['output_work_units']<=r['organization_capacity']*.01+1e-8 for r in m.history))
        s=m._long.operations
        self.assertAlmostEqual(s.orders_arrived,s.orders_expired+s.product_units+s.backlog())
        self.assertAlmostEqual(s.material_paid,3*s.product_units)
        m._long.validate_semantics();self.validate(m)
    def test_growth_above_twice_initial_is_delayed_and_paid(self):
        m=self.model(config(days=80),'growth');m.run()
        self.assertGreater(len(m.active),2*m.config.n)
        self.assertGreater(m._long.enterprise.capacity,2*m.config.n)
        self.assertGreater(m._long.guild_count,m.config.guilds)
        self.assertGreater(m._long.enterprise.departments,m.config.enterprise_growth.initial_departments)
        self.assertGreater(m._long.site_count,m.config.sites)
        self.assertGreater(m.metrics['infrastructure_setup_resource_units'],0)
        for r in m.people[m.config.n:]:
            self.assertGreater(r['entered_day'],m.config.enterprise_growth.construction_delay_days)
        self.validate(m)
    def test_training_and_management_consume_actual_agent_time(self):
        a=self.model(config(days=24,coordination_workday_units_per_layer=0.,management_overload_workday_units_per_report=0.,entrant_training_workday_units=0.),'none')
        b=self.model(config(days=24,coordination_workday_units_per_layer=.1,entrant_training_workday_units=.15),'cost');a.run();b.run()
        self.assertGreater(b.metrics['enterprise_coordination_hours'],a.metrics['enterprise_coordination_hours'])
        self.assertGreater(b.metrics['training_hours'],a.metrics['training_hours'])
        self.assertLess(b.metrics['produced_work_units'],a.metrics['produced_work_units'])
        self.assertLessEqual(b.metrics['max_individual_time_booked_hours'],1+1e-12)
        self.validate(b)
    def test_country_name_has_no_trait_or_output_effect_context_intervention_does(self):
        p=config(days=10,countries=(CountryContext(key='a'),));q=dataclasses.replace(p,enterprise_operations=dataclasses.replace(p.enterprise_operations,countries=(CountryContext(key='unrelated-nationality-label'),)))
        a=self.model(p,'a');b=self.model(q,'b');a.run();b.run()
        self.assertEqual(a.summary(),b.summary())
        self.assertEqual([dataclasses.asdict(v) for v in a.agents],[dataclasses.asdict(v) for v in b.agents])
        c=self.model(config(days=10,countries=(CountryContext(logistics_delay_days=9,legal_resource_units_per_worker_workday=2.),)),'context');c.run()
        self.assertGreater(c.metrics['operating_resource_units'],a.metrics['operating_resource_units'])
        self.assertLess(c.metrics['revenue_resource_units'],a.metrics['revenue_resource_units'])
        self.validate(c)
    def test_same_context_ablation_is_exact(self):
        p=config(days=8,coordination_workday_units_per_layer=0.,management_overload_workday_units_per_report=0.)
        p=dataclasses.replace(p,enterprise_growth=dataclasses.replace(p.enterprise_growth,maximum_organization_capacity=8))
        a=self.model(p,'a');b=self.model(dataclasses.replace(p,enterprise_operations=dataclasses.replace(p.enterprise_operations,countries=(CountryContext(communication_workday_units=.3),))),'b')
        a.run();b.run() # one site: cross-site communication has no activation
        self.assertEqual(a.summary(),b.summary());self.assertEqual(a.history,b.history)
    def test_checkpoint_full_persisted_gate_resume_and_policy_fork(self):
        p=config(days=20);a=self.model(p,'full');b=self.model(p,'split');a.run();b.run(7)
        cp=self.root/'cp.json';b.write_checkpoint(cp);verify_snapshot(cp,expected_config=p)
        c=Model.restore_checkpoint(cp,storage_dir=self.root/'resumed',expected_config=p);self.models.append(c);c.run()
        self.assertEqual(a.semantic_digest(),c.semantic_digest());self.assertEqual(a.summary(),c.summary())
        self.assertEqual(a.summary()['model_version'],VERSION);self.validate(c)
        parent=self.model(p,'parent');parent.run(5)
        branch=parent.fork(dataclasses.replace(p,regime='sublinear',longitudinal=dataclasses.replace(p.longitudinal,adoption_mode='fixed',adoption_day=5)),storage_dir=self.root/'branch');self.models.append(branch)
        self.assertEqual(branch._long.operations.to_dict(),parent._long.operations.to_dict())
        branch.run();self.validate(branch,'branch',config_history=((5,p),))
    def test_corrupt_state_resource_stock_recovery_is_refused(self):
        m=self.model(config(days=4),'x');m.run()
        for key in ('orders_arrived','debt','delivered_units'):
            v=m.state();v['enterprise_operating_state'][key]+=1
            with self.subTest(key=key),self.assertRaises(ValueError):Model.restore(v,storage_dir=self.root/('bad-'+key))
    def test_nonzero_leave_and_creation_tail_are_fully_reconstructed(self):
        p=config(days=25);p=dataclasses.replace(p,longitudinal=dataclasses.replace(p.longitudinal,annual_leave_workdays=30.,work_creation_stop_day=10))
        m=self.model(p,'leave-tail');m.run();self.validate(m)
        self.assertLess(m.metrics['present_member_workdays'],m.metrics['active_member_workdays'])
        self.assertTrue(all(r['output_work_units']==0 for r in m.history[10:]))
    def test_market_begin_tampering_is_not_a_valid_accounting_phase(self):
        m=self.model(config(days=2),'phase');m.run()
        rows=[(r[2],r[3],json.loads(r[4])) for r in m.ledger.rows('journal') if r[1]==0]
        record=next(v for k,e,v in rows if k=='day_end')
        altered=copy.deepcopy(rows)
        next(v for k,e,v in altered if k=='enterprise_market_begin')['received']+=1.
        with self.assertRaises(ValueError):_RawDailyAccounting(m.config,m.state()).verify(0,altered,record)
    def test_partial_operating_day_cannot_be_continued_as_a_checkpoint(self):
        m=self.model(config(days=4),'partial');m.run(1);cp=self.root/'safe.json';m.write_checkpoint(cp)
        original=m._long.operations.finish_day
        def fail(*args,**kwargs):raise RuntimeError('controlled phase failure')
        m._long.operations.finish_day=fail
        with self.assertRaises(RuntimeError):m.step()
        m._long.operations.finish_day=original
        with self.assertRaises(RuntimeError):m.step()
        with self.assertRaises(ValueError):m.write_checkpoint(self.root/'invalid.json')
        restored=Model.restore_checkpoint(cp,storage_dir=self.root/'safe-resumed',expected_config=m.config);self.models.append(restored)
        restored.run();self.validate(restored,'safe-final')
    def test_finance_has_explicit_debt_and_interest_no_free_capital(self):
        p=config(days=30,capital_credit_limit_resource_units=100,capital_draw_resource_units=20,finance_minimum_past_margin=0.)
        p=dataclasses.replace(p,enterprise_growth=dataclasses.replace(p.enterprise_growth,reserve_workdays=20.),longitudinal=dataclasses.replace(p.longitudinal,initial_cash_per_member=1.))
        m=self.model(p,'finance');m.run();s=m._long.operations
        self.assertGreater(s.borrowed,0);self.assertGreater(s.interest_accrued,0)
        self.assertAlmostEqual(s.debt,s.borrowed+s.interest_accrued-s.interest_paid)
        self.assertEqual(m.metrics['financing_inflow_resource_units'],s.borrowed);self.validate(m)


if __name__=='__main__':unittest.main()
