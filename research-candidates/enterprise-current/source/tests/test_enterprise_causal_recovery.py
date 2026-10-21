"""Adversarial causal-r3 recovery/calendar controls; no empirical claims."""
import copy
import dataclasses
import json
import math
from pathlib import Path
import tempfile
import unittest
from dams_sim.config import Config
from dams_sim.enterprise_growth import EnterpriseGrowthConfig
from dams_sim.enterprise_operations import EnterpriseOperatingConfig
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.model import Model
from dams_sim.longitudinal_model import verify_snapshot,validate_persisted_state
from dams_sim.storage import atomic_csv,canonical
from research_tools.validate_longitudinal import verify_daily_evidence,verify_summary_evidence


def declared_config(*,interest=False,days=8):
    return Config(n=8,days=days,guilds=2,sites=1,team_size=2,trace_every_days=1,
        max_wall_seconds=30.,max_output_mb=20.,max_rss_mb=128.,max_events=4000,
        longitudinal=LongitudinalConfig(annual_exit_probability=0.,annual_vacancy_fill_probability=1.,
            adoption_mode='never',max_active_members=8,max_people_ever=32,
            payroll_resource_units_per_member_workday=.01,maintenance_resource_units_per_member_workday=0.,
            initial_cash_per_member=1. if interest else 1000.,
            work_creation_stop_day=None if interest else min(3,days),
            demand_schedule=() if interest else tuple(v for v in ((0,0.),(2,1.),(3,0.)) if v[0]<days)),
        enterprise_growth=EnterpriseGrowthConfig(initial_organization_capacity=8,
            maximum_organization_capacity=8,members_per_guild=4,guilds_per_department=2,members_per_site=8,
            review_interval_days=2 if interest else 30,signal_half_life_workdays=1.,
            reserve_workdays=20. if interest else 1.,minimum_target_members=2),
        enterprise_operations=EnterpriseOperatingConfig(base_orders_per_workday=60.,market_noise_log_sd=0.,
            initial_price_resource_units=10.,competitor_price_resource_units=10.,price_adjustment_rate=0.,
            recruitment_lead_days=2,training_lead_days=4,entrant_training_workday_units=.1,
            capital_credit_limit_resource_units=100. if interest else 0.,
            capital_draw_resource_units=20. if interest else 0.,annual_interest_rate=.5,
            finance_minimum_past_margin=0.)).validate()


class CausalRecoveryTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parent.parent/'candidate-evidence/test-tmp';root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.root=Path(self.temp.name);self.models=[]
    def tearDown(self):
        for m in self.models:m.ledger.close()
        self.temp.cleanup()
    def model(self,p,name,chunk=1024):
        m=Model(p,storage_dir=self.root/name,journal_chunk_bytes=chunk);self.models.append(m);return m
    def gate(self,m,name):
        final=self.root/(name+'.json');m.write_final_state(final);s,db=verify_snapshot(final,expected_config=m.config)
        t=self.root/(name+'.csv');atomic_csv(t,m.history)
        verify_daily_evidence(t,m.config,s['state'],db)
        verify_summary_evidence(json.loads(canonical(m.summary())),m.config,s['state'],db)
    def test_friday_delivery_settled_weekend_enters_next_workday_once(self):
        m=self.model(declared_config(),'weekend');signals=[]
        for _ in range(m.config.days):m.step();signals.append(m._long.enterprise.to_dict())
        revenue=m.metrics['revenue_resource_units'];self.assertAlmostEqual(revenue,46.37874827457962)
        self.assertEqual(signals[3]['revenue_signal'],0.)
        self.assertEqual(signals[3]['pending_revenue'],revenue)
        self.assertEqual(signals[4]['pending_revenue'],revenue)
        for day,fraction in ((5,.5),(6,.25),(7,.125)):
            self.assertAlmostEqual(signals[day]['revenue_signal'],revenue*fraction)
            self.assertEqual(signals[day]['pending_revenue'],0.)
        self.gate(m,'weekend-final')
    def test_weekend_interest_all_paid_costs_carried_with_workday_decay(self):
        m=self.model(declared_config(interest=True),'interest');expected=0.;pending=0.;weekend_interest=0.
        for day in range(m.config.days):
            old=m.metrics.get('operating_resource_units',0.)
            m.step();paid=m.metrics['operating_resource_units']-old
            pending+=paid
            rows=[json.loads(r[-1]) for r in m.ledger.rows('journal') if r[1]==day]
            end=next(v for r,v in zip([r for r in m.ledger.rows('journal') if r[1]==day],rows) if r[2]=='enterprise_market_end')
            if not m.history[-1]['is_workday']:weekend_interest+=end['interest_paid']
            if m.history[-1]['is_workday']:expected+=.5*(pending-expected);pending=0.
            self.assertAlmostEqual(m._long.enterprise.cost_signal,expected)
            self.assertAlmostEqual(m._long.enterprise.pending_operating_cost,pending)
        self.assertAlmostEqual(weekend_interest,.0546448087431694)
        self.gate(m,'interest-final')
    def test_pending_calendar_cash_checkpoint_and_policy_fork_no_double_count(self):
        p=declared_config();full=self.model(p,'full');split=self.model(p,'split');full.run();split.run(4)
        cp=self.root/'weekend-cp.json';split.write_checkpoint(cp);verify_snapshot(cp,expected_config=p)
        resumed=Model.restore_checkpoint(cp,storage_dir=self.root/'resumed',expected_config=p);self.models.append(resumed)
        self.assertGreater(resumed._long.enterprise.pending_revenue,0.);resumed.run()
        self.assertEqual(full.semantic_digest(),resumed.semantic_digest())
        self.gate(resumed,'resumed-final')
        branch=split.fork(dataclasses.replace(p,regime='sublinear',longitudinal=dataclasses.replace(p.longitudinal,adoption_mode='fixed',adoption_day=4)),storage_dir=self.root/'branch');self.models.append(branch)
        branch.run();self.assertEqual(branch._long.enterprise.pending_revenue,0.)
        self.assertAlmostEqual(branch._long.enterprise.revenue_signal,full._long.enterprise.revenue_signal)
    def test_delivered_revenue_forgery_restore_and_snapshot_refused(self):
        m=self.model(declared_config(),'forged');m.run();v=m.state();v['enterprise_operating_state']['delivered_revenue']+=1000.
        with self.assertRaisesRegex(ValueError,'operating stock'):Model.restore(v,storage_dir=self.root/'bad-restore')
        m._long.operations.delivered_revenue+=1000.
        with self.assertRaisesRegex(ValueError,'operating stock'):m.write_checkpoint(self.root/'bad-cp.json')
        self.assertFalse((self.root/'bad-cp.json').exists())
    def test_coherent_cash_metric_and_ops_forgery_still_not_committed(self):
        m=self.model(declared_config(),'coherent');m.run();v=m.state()
        v['enterprise_operating_state']['delivered_revenue']+=1000.;v['metrics']['revenue_resource_units']+=1000.;v['cash']+=1000.
        with self.assertRaisesRegex(ValueError,'final committed journal'):Model.restore(v,storage_dir=self.root/'bad-coherent')
    def test_growth_and_nonfinancial_operating_state_forgery_refused(self):
        m=self.model(declared_config(),'stocks');m.run()
        for scope,field in (('enterprise_growth_state','pending_operating_cost'),('enterprise_growth_state','revenue_signal'),('enterprise_operating_state','price')):
            v=m.state();v[scope][field]+=.1
            with self.subTest(scope=scope,field=field),self.assertRaisesRegex(ValueError,'final committed journal'):Model.restore(v,storage_dir=self.root/('bad-'+field))
    def test_unsafe_price_elasticity_fails_configuration_before_day_zero(self):
        p=declared_config(days=1)
        for values in ({'initial_price_resource_units':.1,'competitor_price_resource_units':100.,'demand_price_elasticity':1000.,'base_orders_per_workday':1.},
                       {'minimum_price_resource_units':1e-320,'initial_price_resource_units':.1,'competitor_price_resource_units':100.}):
            with self.subTest(values=values),self.assertRaisesRegex(ValueError,'envelope exceeds finite arithmetic'):
                dataclasses.replace(p,enterprise_operations=dataclasses.replace(p.enterprise_operations,**values)).validate()
        safe=dataclasses.replace(p,enterprise_operations=dataclasses.replace(p.enterprise_operations,demand_price_elasticity=10.)).validate()
        self.model(safe,'safe').run()
    def test_last_committed_row_matches_full_history_for_all_physical_layouts(self):
        for chunk in (None,128,1024,1048576):
            m=self.model(declared_config(),str(chunk),chunk)
            self.assertEqual(tuple(m.ledger.latest_journal_row()),list(m.ledger.rows('journal'))[-1])
            m._long.validate_semantics()
            for _ in range(8):
                m.step();self.assertEqual(tuple(m.ledger.latest_journal_row()),list(m.ledger.rows('journal'))[-1])
            m._long.validate_semantics();self.gate(m,'layout-'+str(chunk))
    def test_calendar_carry_replay_or_nonfinite_is_refused_without_change(self):
        m=self.model(declared_config(),'observed');m.run(4);s=m._long.enterprise;before=s.to_dict()
        for day,revenue in ((3,0.),(4,float('inf'))):
            with self.assertRaises(ValueError):s.observe(day,revenue,0.,working=False)
            self.assertEqual(s.to_dict(),before)
    def test_persisted_gate_refuses_coherent_header_edits_against_original_journal(self):
        m=self.model(declared_config(),'persisted');m.run()
        final=self.root/'original.json';m.write_final_state(final);envelope,db=verify_snapshot(final,expected_config=m.config)
        for stock,metric in (('delivered_revenue','revenue_resource_units'),('borrowed','financing_inflow_resource_units')):
            state=copy.deepcopy(envelope['state']);state['enterprise_operating_state'][stock]+=1000.
            state['metrics'][metric]+=1000.;state['cash']+=1000.
            if stock=='borrowed':state['enterprise_operating_state']['debt']+=1000.
            with self.subTest(stock=stock),self.assertRaisesRegex(ValueError,'final committed journal'):
                validate_persisted_state(state,db)
    def test_corrupt_tail_fragment_is_not_a_committed_recovery_witness(self):
        m=self.model(declared_config(),'corrupt',128);m.run()
        row=m.ledger.db.execute('SELECT chunk,payload FROM journal_chunks ORDER BY chunk DESC LIMIT 1').fetchone()
        m.ledger.db.execute('UPDATE journal_chunks SET payload=? WHERE chunk=?',(bytes([row[1][0]^1])+row[1][1:],row[0]))
        with self.assertRaisesRegex(ValueError,'integrity'):
            m._long.validate_semantics()

if __name__=='__main__':unittest.main()
