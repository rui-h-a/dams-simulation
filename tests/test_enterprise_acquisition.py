"""Finite internal M&A controls. Fixtures are not formal or empirical worlds."""
import copy
import dataclasses
import importlib
import json
from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest.mock import patch

from dams_sim.config import Config
from dams_sim.enterprise_acquisition import (EnterpriseAcquisitionConfig,AcquisitionTarget,
    TargetEmployee,TargetEvidence,EnterpriseAcquisitionState)
from dams_sim.enterprise_growth import EnterpriseGrowthConfig,EnterpriseGrowthState
from dams_sim.enterprise_operations import EnterpriseOperatingConfig
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.longitudinal_model import verify_snapshot,validate_persisted_state
from dams_sim.longitudinal_storage import TABLE_COLUMNS
from dams_sim.model import Model
from dams_sim.storage import canonical,atomic_csv
from research_tools.validate_longitudinal import verify_daily_evidence,verify_summary_evidence
from research_tools.validate_acquisition_accounts import verify_acquisition_accounts


def configuration(days=30,**ma):
    employees=tuple(TargetEmployee(key=f'p{i}',skill=1.+i*.1,learning=.6) for i in range(4))
    target=AcquisitionTarget('target-a','seller-a',1,days,employees,4,1,1,1,
        cash_resource_units=3.,financial_debt_resource_units=2.,consideration_resource_units=10.,
        future_orders_per_workday=10.,evidence=(TargetEvidence('prior-score','p0',900.,'target-score-unit',.5),),
        pending_evidence=(TargetEvidence('open-claim','p1',700.,'other-unit',.2),),
        history=(('historical-sales','resource-unit',120.),))
    kwargs={'targets':(target,),'minimum_past_margin':0.,'require_unmet_orders':False,
        'reserve_workdays':1.,'integration_workdays':3,
        'integration_resource_units_per_employee_workday':.2,
        'integration_payroll_resource_units_per_employee_workday':.1,
        'maximum_consolidated_debt_resource_units':100.,'maximum_acquisition_financing_resource_units':20.,
        'evidence_resolution_workdays':2}
    kwargs.update(ma)
    return Config(n=8,days=days,guilds=2,sites=1,team_size=2,trace_every_days=1,
        max_wall_seconds=30.,max_output_mb=30.,max_rss_mb=256.,max_events=5000,
        longitudinal=LongitudinalConfig(annual_exit_probability=0.,annual_vacancy_fill_probability=0.,
            adoption_mode='never',max_active_members=24,max_people_ever=80,
            payroll_resource_units_per_member_workday=.01,maintenance_resource_units_per_member_workday=0.,
            initial_cash_per_member=100.,initial_age_min_years=25.,initial_age_max_years=40.),
        enterprise_growth=EnterpriseGrowthConfig(initial_organization_capacity=8,maximum_organization_capacity=24,
            members_per_guild=4,guilds_per_department=2,members_per_site=8,review_interval_days=30,
            reserve_workdays=1.,signal_half_life_workdays=1.,minimum_target_members=2),
        enterprise_operations=EnterpriseOperatingConfig(base_orders_per_workday=30.,market_noise_log_sd=0.,
            initial_price_resource_units=10.,competitor_price_resource_units=10.,price_adjustment_rate=0.),
        enterprise_acquisition=EnterpriseAcquisitionConfig(**kwargs)).validate()


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parent.parent/'candidate-evidence/test-tmp';base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base);self.root=Path(self.temp.name);self.models=[]
    def tearDown(self):
        for model in self.models:model.ledger.close()
        self.temp.cleanup()
    def model(self,p,name):
        m=Model(p,storage_dir=self.root/name,journal_chunk_bytes=1024);self.models.append(m);return m
    def raw(self,m,name):
        final=self.root/(name+'.json');m.write_final_state(final)
        envelope,db=verify_snapshot(final,expected_config=m.config)
        daily=self.root/(name+'.csv');atomic_csv(daily,m.history)
        verify_daily_evidence(daily,m.config,envelope['state'],db)
        verify_summary_evidence(json.loads(canonical(m.summary())),m.config,envelope['state'],db)
        verify_acquisition_accounts(m.config,envelope['state'],db)
        return envelope,db
    def test_strict_serialization_and_invalid_contracts(self):
        p=configuration();self.assertEqual(Config.from_dict(p.to_dict()),p)
        self.assertNotIn('enterprise_acquisition',Config().to_dict())
        for kw in ({'transaction_resource_units':0.},{'integration_workdays':0},{'integration_exit_probability_per_workday':1.1},
                   {'maximum_consolidated_debt_resource_units':float('inf')},{'integration_mode':'M&A-free-population'}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):configuration(**kw)
        target=p.enterprise_acquisition.targets[0]
        for t in (dataclasses.replace(target,seller='parent'),dataclasses.replace(target,employees=target.employees+(target.employees[0],)),dataclasses.replace(target,installed_seats=3),dataclasses.replace(target,evidence=target.evidence+target.evidence)):
            with self.subTest(t=t.key),self.assertRaises(ValueError):configuration(targets=(t,))
    def test_disabled_path_exact_against_complete_r4_fixture(self):
        base=Path(__file__).resolve().parent/'fixtures/operations-r4-reference/dams_sim'
        pkg=types.ModuleType('acquisition_operations_r4_reference');pkg.__path__=[str(base)];sys.modules[pkg.__name__]=pkg
        RConfig=importlib.import_module(pkg.__name__+'.config').Config
        REngine=importlib.import_module(pkg.__name__+'.longitudinal_model').LongitudinalEngine
        p=dataclasses.replace(configuration(days=12),enterprise_acquisition=None)
        r=REngine(RConfig.from_dict(p.to_dict()),storage_dir=self.root/'r3',journal_chunk_bytes=1024);self.models.append(r)
        m=self.model(p,'optout')._long
        for _ in range(p.days):
            r.step();m.step();a=copy.deepcopy(r.header());b=copy.deepcopy(m.header())
            a.pop('execution_source_sha256');b.pop('execution_source_sha256')
            self.assertEqual(canonical(a),canonical(b))
            self.assertEqual([dataclasses.asdict(v) for v in r.agents],[dataclasses.asdict(v) for v in m.agents])
            for table in TABLE_COLUMNS:self.assertEqual(list(r.ledger.rows(table)),list(m.ledger.rows(table)))
        self.assertEqual(r.summary(),m.summary())
    def test_purchase_payments_assets_debt_workers_and_units_not_instant(self):
        p=configuration();m=self.model(p,'acquire');m.run(3);s=m._long.acquisitions.targets['target-a']
        self.assertEqual(s['status'],'integrating');self.assertEqual(len(m.agents),8);self.assertEqual(m._long.enterprise.capacity,8)
        self.assertEqual(s['consideration_paid'],10.);self.assertEqual(s['transaction_paid'],1.);self.assertEqual(s['transferred_cash'],3.);self.assertEqual(s['assumed_debt'],2.)
        self.assertAlmostEqual(s['integration_paid'],1.2)
        self.assertEqual(s['owner'],'parent');m.run(8)
        self.assertEqual(s['status'],'integrated');self.assertEqual(s['ready_day'],7)
        self.assertEqual(len(m.agents),12);self.assertEqual(m._long.enterprise.capacity,12)
        self.assertEqual((m._long.enterprise.guilds,m._long.enterprise.departments,m._long.enterprise.sites),(3,2,2))
        self.assertEqual(sum(v=='parent' for v in s['employee_status'].values()),4)
        self.assertEqual(m._long.operations.assumed_acquisition_debt,2.)
        # Independently calculated balance, no call to a model transition method.
        expected=800.+3.-10.-1.-3.6+ m.metrics['revenue_resource_units']-m.metrics['operating_resource_units']
        self.assertAlmostEqual(m._long.cash,expected)
        self.assertEqual(m.metrics.get('recruitment_resource_units',0.),0.)
        self.assertEqual([a.skill for a in m.agents[8:]],[p.skill for p in p.enterprise_acquisition.targets[0].employees])
        self.assertEqual(s['retained_evidence'][0]['unit'],'target-score-unit')
        self.assertTrue(all(m.ledger.credit(i,m.agents[i].guild)<900. for i in range(8,12)))
        m._long.validate_semantics()
    def test_weekend_delays_paid_integration_and_recovery_is_exact(self):
        p=configuration();full=self.model(p,'full');full.run();split=self.model(p,'split');split.run(4)
        s=split._long.acquisitions.targets['target-a'];self.assertEqual(s['integration_workdays'],1);self.assertAlmostEqual(s['integration_paid'],1.2)
        cp=self.root/'weekend.json';split.write_checkpoint(cp);verify_snapshot(cp,expected_config=p)
        resumed=Model.restore_checkpoint(cp,storage_dir=self.root/'resume',expected_config=p);self.models.append(resumed);resumed.run()
        self.assertEqual(full.state(),resumed.state());self.assertEqual(full.semantic_digest(),resumed.semantic_digest())
        final=self.root/'final.json';resumed.write_final_state(final);value,db=verify_snapshot(final,expected_config=p)
        self.assertTrue(validate_persisted_state(value['state'],db)['valid'])
    def test_insufficient_money_no_ownership_and_failure_transaction_atomic(self):
        p=configuration(maximum_acquisition_financing_resource_units=0.);m=self.model(p,'poor')
        m._long.cash=0.
        s=m._long.acquisitions;before=s.to_dict()
        effects,events=s.begin_day(0,working=True,operating=True,cash=0.,growth=m._long.enterprise,debt=0.,ever_people=8,active=8,tokens=[r['token'] for r in m.people],people_cap=80,unmet_orders=True,exit_draw=lambda *_:1.)
        self.assertEqual(effects['consideration'],0.);self.assertEqual(s.targets['target-a']['owner'],'seller-a')
        m._long.enterprise.observe(0,10.,1.,working=True)
        effects,events=s.begin_day(1,working=True,operating=True,cash=0.,growth=m._long.enterprise,debt=0.,ever_people=8,active=8,tokens=[r['token'] for r in m.people],people_cap=80,unmet_orders=True,exit_draw=lambda *_:1.)
        self.assertEqual(effects['consideration'],0.)
        saved=s.to_dict()
        with self.assertRaises(ValueError):s.begin_day(1,working=True,operating=True,cash=10.,growth=m._long.enterprise,debt=0.,ever_people=8,active=8,tokens=[],people_cap=80,unmet_orders=True,exit_draw=lambda *_:1.)
        self.assertEqual(saved,s.to_dict())
    def test_financing_is_real_liability_not_organic_revenue(self):
        p=configuration(reserve_workdays=.01,maximum_acquisition_financing_resource_units=1500.,maximum_consolidated_debt_resource_units=2000.);t=dataclasses.replace(p.enterprise_acquisition.targets[0],consideration_resource_units=1000.);p=dataclasses.replace(p,enterprise_acquisition=dataclasses.replace(p.enterprise_acquisition,targets=(t,)),longitudinal=dataclasses.replace(p.longitudinal,initial_cash_per_member=1.))
        m=self.model(p,'finance');m.run(3);s=m._long.acquisitions.targets['target-a']
        self.assertGreater(s['financing'],0.);self.assertEqual(m.metrics['financing_inflow_resource_units'],s['financing'])
        self.assertEqual(m._long.operations.borrowed,s['financing']);self.assertGreater(m._long.operations.interest_accrued,0.)
        m._long.validate_semantics()
    def test_all_departures_preserve_evidence_and_idle_assets(self):
        p=configuration(integration_exit_probability_per_workday=1.);m=self.model(p,'departures');m.run()
        s=m._long.acquisitions.targets['target-a'];self.assertEqual(set(s['employee_status'].values()),{'departed'})
        self.assertEqual(m.metrics.get('acquired_people',0.),0.);self.assertEqual(len(m.agents),8)
        self.assertEqual(m._long.enterprise.capacity,12);self.assertTrue(s['retained_evidence'])
        m._long.validate_semantics()
    def test_autonomy_vs_centralization_changes_governance_and_real_work(self):
        p=configuration();a=self.model(p,'autonomy');b=self.model(configuration(integration_mode='centralize'),'central')
        a.run();b.run()
        self.assertEqual(a._long.acquisitions.targets['target-a']['consideration_paid'],b._long.acquisitions.targets['target-a']['consideration_paid'])
        self.assertNotEqual(a._long.acquisitions.coordination(8),b._long.acquisitions.coordination(8))
        self.assertNotEqual(a.metrics['produced_work_units'],b.metrics['produced_work_units'])
        self.assertEqual([v.skill for v in a.agents],[v.skill for v in b.agents])
    def test_complete_original_daily_and_summary_gate(self):
        for name,kwargs in (('autonomy',{}),('central',{'integration_mode':'centralize'}),('departures',{'integration_exit_probability_per_workday':1.})):
            with self.subTest(mode=name):
                m=self.model(configuration(**kwargs),name);m.run();self.raw(m,name)
    def test_no_purchase_when_capacity_or_financing_is_unavailable(self):
        for name,p in (('resource-cap',dataclasses.replace(configuration(),enterprise_growth=dataclasses.replace(configuration().enterprise_growth,maximum_organization_capacity=8))),
                       ('lender-refusal',configuration(minimum_past_margin=1.,maximum_acquisition_financing_resource_units=0.))):
            with self.subTest(case=name):
                m=self.model(p,name);m.run();s=m._long.acquisitions.targets['target-a']
                self.assertEqual(s['owner'],'seller-a');self.assertEqual(s['status'],'available')
                self.assertEqual(s['seller_consideration_received'],0.);self.assertEqual(s['target_cash_remaining'],3.)
                self.assertEqual(len(m.agents),8);self.raw(m,name)
    def test_exception_after_closing_proposal_rolls_back_every_target_stock(self):
        p=configuration();m=self.model(p,'state-atomic');s=m._long.acquisitions;g=m._long.enterprise
        s.begin_day(0,working=True,operating=True,cash=800.,growth=g,debt=0.,ever_people=8,active=8,tokens=[],people_cap=80,unmet_orders=True,exit_draw=lambda *_:1.)
        g.observe(0,10.,1.,working=True);before=s.to_dict()
        def failure(*_):raise RuntimeError('injected independent exit-draw failure')
        with self.assertRaisesRegex(RuntimeError,'injected'):
            s.begin_day(1,working=True,operating=True,cash=800.,growth=g,debt=0.,ever_people=8,active=8,tokens=[],people_cap=80,unmet_orders=True,exit_draw=failure)
        self.assertEqual(s.to_dict(),before)
        g.observe(1,10.,1.,working=True)
        with self.assertRaisesRegex(ValueError,'present or future'):
            s.begin_day(1,working=True,operating=True,cash=800.,growth=g,debt=0.,ever_people=8,active=8,tokens=[],people_cap=80,unmet_orders=True,exit_draw=lambda *_:1.)
        self.assertEqual(s.to_dict(),before)
    def test_unfunded_integration_can_fail_and_never_installs_idle_owned_assets(self):
        p=configuration(maximum_unfunded_workdays=2)
        p=dataclasses.replace(p,longitudinal=dataclasses.replace(p.longitudinal,suspension_day=3))
        m=self.model(p,'failure');m.run();s=m._long.acquisitions.targets['target-a']
        self.assertEqual(s['owner'],'parent');self.assertEqual(s['status'],'failed');self.assertEqual(s['ready_day'],None)
        self.assertEqual(len(m.agents),8);self.assertEqual(m._long.enterprise.capacity,8)
        self.assertEqual(set(s['employee_status'].values()),{'departed'})
        self.assertEqual(s['seller_consideration_received'],10.);self.assertEqual(s['transferred_cash'],3.);self.assertEqual(s['assumed_debt'],2.)
        self.assertEqual(s['retained_evidence'][0]['evidence_quality'],.5)
        self.raw(m,'failed')
    def test_ma_changes_demand_capacity_and_work_without_changing_initial_traits(self):
        p=configuration();a=self.model(p,'ma');b=self.model(dataclasses.replace(p,enterprise_acquisition=None),'no-ma');a.run();b.run()
        for field in ('skill','care_hours','autonomy','reciprocity'):
            self.assertEqual([getattr(v,field) for v in a.agents[:8]],[getattr(v,field) for v in b.agents[:8]])
        self.assertGreater(a.metrics['orders_arrived_units'],b.metrics['orders_arrived_units'])
        self.assertGreater(a._long.enterprise.capacity,b._long.enterprise.capacity)
        self.assertNotEqual(a.metrics['produced_work_units'],b.metrics['produced_work_units'])
        self.assertEqual(a._long.acquisitions.targets['target-a']['resolved_evidence'][0]['unit'],'other-unit')
    def test_independent_journal_accounts_reject_coherent_paid_price_forgery(self):
        from research_tools.validate_longitudinal import iter_journal_rows
        import sqlite3
        m=self.model(configuration(),'independent-negative');m.run();envelope,db=self.raw(m,'independent-negative')
        connection=sqlite3.connect(db);rows=list(iter_journal_rows(connection));connection.close()
        index=next(i for i,(_,kind,_,_) in enumerate(rows) if kind=='acquisition_integration_paid')
        day,kind,ident,payload=rows[index];value=json.loads(payload);value['paid']+=1.
        rows[index]=(day,kind,ident,canonical(value).decode())
        # The independent validator recomputes the contract price rather than
        # accepting another engine transition or a declared positive verdict.
        with patch('research_tools.validate_acquisition_accounts.iter_journal_rows',return_value=(row for row in rows)):
            with self.assertRaisesRegex(ValueError,'systems cost plus retained payroll'):
                verify_acquisition_accounts(m.config,envelope['state'],db)
    def test_tampered_target_state_is_not_recoverable(self):
        m=self.model(configuration(),'original');m.run(8)
        for field,value in (('assumed_debt',100.),('owner','attacker'),('retained_evidence',[])):
            state=m.state();state['enterprise_acquisition_state']['targets']['target-a'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):Model.restore(state,storage_dir=self.root/('bad-'+field))
        state=m.state();state['enterprise_acquisition_state']['targets']['target-a']['integration_paid']+=1.;state['metrics']['acquisition_integration_resource_units']+=1.;state['cash']-=1.
        with self.assertRaisesRegex(ValueError,'final committed journal'):Model.restore(state,storage_dir=self.root/'coherent-forgery')

if __name__=='__main__':unittest.main()
