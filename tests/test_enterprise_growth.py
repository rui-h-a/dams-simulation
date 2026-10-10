"""Candidate controls, not Monte Carlo study results or empirical validation."""
import copy
import dataclasses
import importlib
import json
from pathlib import Path
import tempfile
import types
import sys
import unittest
from dams_sim.config import Config
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.enterprise_growth import EnterpriseGrowthConfig, EnterpriseGrowthState, MODEL_VERSION
from dams_sim.model import Model
from dams_sim.longitudinal_model import verify_snapshot, LongitudinalEngine
from dams_sim.longitudinal_storage import TABLE_COLUMNS
from dams_sim.storage import canonical, atomic_csv
from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence

BASE=Path(__file__).resolve().parent/'fixtures/schema2-reference'
pkg=types.ModuleType('growth_reference');pkg.__path__=[str(BASE/'dams_sim')]
sys.modules['growth_reference']=pkg
ReferenceConfig=importlib.import_module('growth_reference.config').Config
ReferenceEngine=importlib.import_module('growth_reference.longitudinal_model').LongitudinalEngine


def config(days=50, **growth):
    values={'initial_organization_capacity':8,'maximum_organization_capacity':20,
            'members_per_guild':4,'guilds_per_department':2,'members_per_site':8,
            'review_interval_days':2,'construction_delay_days':3,'hiring_delay_days':2,
            'expansion_step_members':4,'reserve_workdays':1.,
            'signal_half_life_workdays':1.}
    values.update(growth)
    return Config(n=8,days=days,guilds=2,sites=1,team_size=2,trace_every_days=1,
        max_output_mb=30,max_rss_mb=256,
        longitudinal=LongitudinalConfig(annual_exit_probability=0.,annual_vacancy_fill_probability=1.,
            adoption_mode='never',max_active_members=24,max_people_ever=80,
            payroll_resource_units_per_member_workday=.01,maintenance_resource_units_per_member_workday=0.,
            revenue_resource_units_per_work_unit=10.,initial_cash_per_member=1000.),
        enterprise_growth=EnterpriseGrowthConfig(**values)).validate()


class EnterpriseTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parent.parent/'candidate-evidence/test-tmp';root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.root=Path(self.temp.name);self.opened=[]
    def tearDown(self):
        for m in self.opened:m.ledger.close()
        self.temp.cleanup()
    def model(self,p,name):
        m=Model(p,storage_dir=self.root/name,journal_chunk_bytes=1024);self.opened.append(m);return m
    def test_opt_out_serialization_and_reject_invalid_contracts(self):
        self.assertNotIn('enterprise_growth',Config().to_dict())
        self.assertEqual(Config.from_dict(config().to_dict()),config())
        for kw in ({'initial_organization_capacity':7},{'maximum_organization_capacity':25},
                   {'signal_half_life_workdays':0.},{'hiring_delay_days':True},
                   {'expansion_profit_margin':float('nan')},{'initial_departments':0}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):config(**kw)
        with self.assertRaises(ValueError):dataclasses.replace(config(),longitudinal=LongitudinalConfig()).validate()
        with self.assertRaises(ValueError):dataclasses.replace(config(),longitudinal=dataclasses.replace(config().longitudinal,workforce_targets=((3,9),))).validate()
    def test_disabled_path_exact_daily_data_against_frozen_schema2(self):
        p=dataclasses.replace(config(days=12),enterprise_growth=None)
        r=ReferenceEngine(ReferenceConfig.from_dict(p.to_dict()),storage_dir=self.root/'reference',journal_chunk_bytes=1024)
        self.opened.append(r);m=self.model(p,'candidate')._long
        for _ in range(p.days):
            r.step();m.step()
            a=copy.deepcopy(r.header());b=copy.deepcopy(m.header())
            a.pop('execution_source_sha256');b.pop('execution_source_sha256')
            self.assertEqual(canonical(a),canonical(b))
            self.assertEqual([dataclasses.asdict(v) for v in r.agents],[dataclasses.asdict(v) for v in m.agents])
            for table in TABLE_COLUMNS:self.assertEqual(list(r.ledger.rows(table)),list(m.ledger.rows(table)),table)
        self.assertEqual(r.summary(),m.summary())
    def test_construction_cost_delay_hiring_and_irreversible_contraction(self):
        p=config();s=EnterpriseGrowthState(p)
        s.observe(0,100.,10.,working=True)
        paid,events=s.begin_day(2,10000.,active=8,operating=True,payroll_per_member=.01)
        self.assertEqual(paid,4*2.+10.+10.+20.) # seats + guild + department + site
        self.assertEqual(s.capacity,8);self.assertEqual(s.target,8)
        self.assertEqual(s.pending_construction['ready_day'],5)
        s.begin_day(4,10000.,active=8,operating=True,payroll_per_member=.01)
        self.assertEqual(s.capacity,8)
        s.begin_day(5,10000.,active=8,operating=True,payroll_per_member=.01)
        self.assertEqual((s.capacity,s.guilds,s.departments,s.sites),(12,3,2,2))
        self.assertEqual(s.target,8)
        s.begin_day(7,10000.,active=8,operating=True,payroll_per_member=.01)
        self.assertEqual(s.target,12)
        s.observe(7,0.,10000.,working=True)
        s.begin_day(8,10000.,active=12,operating=True,payroll_per_member=.01)
        self.assertEqual(s.target,7);self.assertEqual(s.capacity,12)
        self.assertGreater(s.maintenance_due(),0.)
    def test_unfunded_signal_no_growth_or_future_or_weekend_observation(self):
        p=config();s=EnterpriseGrowthState(p)
        self.assertEqual(s.begin_day(0,1e6,active=8,operating=True,payroll_per_member=.01),(0.,[]))
        s.observe(0,100.,10.,working=False);self.assertEqual(s.observed_workdays,0)
        s.observe(0,100.,10.,working=True)
        paid,events=s.begin_day(2,0.,active=8,operating=True,payroll_per_member=.01)
        self.assertEqual(paid,0.);self.assertEqual(events[0][0],'growth_unfunded')
        with self.assertRaises(ValueError):s.observe(0,100.,10.,working=True)
        with self.assertRaises(ValueError):s.begin_day(0,10000.,active=8,operating=True,payroll_per_member=.01)
        value=s.to_dict();value['last_observed_day']=2
        with self.assertRaises(ValueError):EnterpriseGrowthState.restore(p,value,2)
    def test_new_domains_real_costs_full_checkpoint_and_resume_equal(self):
        p=config(days=28);full=self.model(p,'full');split=self.model(p,'split')
        full.run();split.run(4)
        checkpoint=self.root/'pending.json';split.write_checkpoint(checkpoint)
        value,_=verify_snapshot(checkpoint,expected_config=p)
        self.assertIsNotNone(value['state']['enterprise_growth_state']['pending_construction'])
        resumed=Model.restore_checkpoint(checkpoint,storage_dir=self.root/'resumed',expected_config=p)
        self.opened.append(resumed);resumed.run()
        self.assertEqual(full.semantic_digest(),resumed.semantic_digest())
        self.assertEqual(full.summary(),resumed.summary());self.assertEqual(full.summary()['model_version'],MODEL_VERSION)
        self.assertGreater(full._long.guild_count,p.guilds)
        self.assertGreater(full._long.site_count,p.sites)
        self.assertGreater(full._long.enterprise.departments,p.enterprise_growth.initial_departments)
        self.assertGreater(full.summary()['infrastructure_setup_resource_units'],0.)
        self.assertGreater(full.summary()['infrastructure_maintenance_resource_units'],0.)
        full._long.validate_semantics()
        self.assertTrue(all(len(ids)<=p.enterprise_growth.members_per_guild for ids in full.members.values()))
        for site in range(full._long.site_count):
            self.assertLessEqual(sum(full.agents[i].site==site for i in full.active),p.enterprise_growth.members_per_site)
        final=self.root/'final.json';full.write_final_state(final);envelope,database=verify_snapshot(final,expected_config=p)
        trajectory=self.root/'timeseries.csv';atomic_csv(trajectory,full.history)
        verify_daily_evidence(trajectory,p,envelope['state'],database)
        verify_summary_evidence(json.loads(canonical(full.summary())),p,envelope['state'],database)
        kinds=[r[2] for r in full.ledger.rows('journal')]
        self.assertIn('infrastructure_requested',kinds);self.assertIn('infrastructure_complete',kinds)
        self.assertTrue(any(a.guild>=p.guilds for a in full.agents))
        self.assertTrue(any(a.site>=p.sites for a in full.agents))
    def test_tampered_dynamic_state_and_shared_world_change_rejected(self):
        p=config(days=10);m=self.model(p,'original');m.run(4)
        value=m.state();value['enterprise_growth_state']['capacity']=21
        with self.assertRaises(ValueError):Model.restore(value,storage_dir=self.root/'bad')
        other=dataclasses.replace(p,enterprise_growth=dataclasses.replace(p.enterprise_growth,expansion_step_members=5))
        with self.assertRaises(ValueError):m.fork(other,storage_dir=self.root/'fork')
        state=m.state();state['target']+=1
        with self.assertRaises(ValueError):Model.restore(state,storage_dir=self.root/'badtarget')
    def test_fixed_domain_paired_tables_cannot_relabel_candidate_results(self):
        from dams_sim.longitudinal_outputs import build_tables
        with self.assertRaisesRegex(ValueError,'no admitted paired study protocol'):
            build_tables(None,[{'config':config()}])
    def test_resource_cap_failure_preserves_last_complete_checkpoint(self):
        p=config(days=3,maximum_organization_capacity=8)
        p=dataclasses.replace(p,longitudinal=dataclasses.replace(p.longitudinal,
            max_active_members=8,max_people_ever=8,exit_schedule=((1,0,'exit'),)))
        m=self.model(p,'capped');m.run(1)
        checkpoint=self.root/'safe.json';m.write_checkpoint(checkpoint)
        before={f.name:f.read_bytes() for f in self.root.glob('safe.*')}
        with self.assertRaises(RuntimeError):m.step()
        self.assertTrue(m._long.failed_day)
        with self.assertRaises(ValueError):m.write_final_state(self.root/'invalid.json')
        self.assertEqual(before,{f.name:f.read_bytes() for f in self.root.glob('safe.*')})
        self.assertEqual(len(m.agents),8)
        verify_snapshot(checkpoint,expected_config=p)
    def test_growth_branch_preserves_full_shared_prestate(self):
        p=config(days=15);parent=self.model(p,'parent');parent.run(4)
        child_config=dataclasses.replace(p,regime='sublinear',longitudinal=dataclasses.replace(p.longitudinal,adoption_mode='fixed',adoption_day=4))
        child=parent.fork(child_config,storage_dir=self.root/'child');self.opened.append(child)
        self.assertEqual(child._long.enterprise.to_dict(),parent._long.enterprise.to_dict())
        self.assertEqual(child.branch_origin['parent_state_semantic_sha256'],parent.semantic_digest())
        for table in TABLE_COLUMNS:self.assertEqual(list(child.ledger.rows(table)),list(parent.ledger.rows(table)))
        parent.run();child.run()
        self.assertEqual(parent._long.enterprise.c,child._long.enterprise.c)
        self.assertEqual(parent.rng.uniform('task_difficulty',5,'initial:0'),child.rng.uniform('task_difficulty',5,'initial:0'))
        self.assertEqual(child.day,15)
    def test_same_world_exogenous_keys_and_policy_information_separation(self):
        p=config(days=4);no=self.model(p,'never');yes=self.model(dataclasses.replace(p,regime='hierarchy'),'other')
        for day in range(p.days):
            self.assertEqual(no.rng.uniform('task_difficulty',day,'initial:0'),yes.rng.uniform('task_difficulty',day,'initial:0'))
        # Pure growth transition has no policy, proposed truth or future output input.
        a=EnterpriseGrowthState(p);b=EnterpriseGrowthState(yes.config)
        a.observe(0,100.,10.,working=True);b.observe(0,100.,10.,working=True)
        self.assertEqual(a.begin_day(2,10000.,active=8,operating=True,payroll_per_member=.01),b.begin_day(2,10000.,active=8,operating=True,payroll_per_member=.01))
        self.assertEqual(a.to_dict(),b.to_dict())

if __name__=='__main__':unittest.main()
