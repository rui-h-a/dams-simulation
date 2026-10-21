"""Opt-in dependence controls: engineering samples, never formal study worlds."""
import copy
import dataclasses as D
import importlib
import json
import math
from pathlib import Path
from statistics import NormalDist, correlation, mean, pstdev
import sys
import types
import unittest

from dams_sim.config import Config
from dams_sim.joint_population import (JointPopulationConfig, VERSION, draw_entry_traits,
                                      generation_witness, _quantile)
from dams_sim.longitudinal_storage import TABLE_COLUMNS
from dams_sim.model import Model
from dams_sim.randomness import WorldRandom
from dams_sim.storage import canonical, atomic_csv
from dams_sim.longitudinal_model import verify_snapshot
from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence
from research_tools.validate_acquisition_accounts import verify_acquisition_accounts
from research_tools.validate_longitudinal import _verify_joint_entry, iter_journal_rows
import test_enterprise_acquisition as original_controls
from test_enterprise_acquisition import configuration

ASSUMPTIONS = JointPopulationConfig(skill_tenure_latent_correlation=.55,
                                    skill_care_latent_correlation=-.35,
                                    tenure_care_latent_correlation=.1)


class JointPopulationTests(unittest.TestCase):
    setUp = original_controls.AcquisitionTests.setUp
    tearDown = original_controls.AcquisitionTests.tearDown
    model = original_controls.AcquisitionTests.model
    def raw(self, m, name):
        final=self.root/(name+'.json');m.write_final_state(final)
        envelope,db=verify_snapshot(final,expected_config=m.config)
        daily=self.root/(name+'.csv');atomic_csv(daily,m.history)
        verify_daily_evidence(daily,m.config,envelope['state'],db)
        verify_summary_evidence(json.loads(canonical(m.summary())),m.config,envelope['state'],db)
        if m.config.enterprise_acquisition is not None:
            verify_acquisition_accounts(m.config,envelope['state'],db)
        return envelope,db

    @staticmethod
    def simple(days=12, joint=ASSUMPTIONS, **longitudinal):
        p = configuration(days=days)
        return D.replace(p, enterprise_acquisition=None, enterprise_operations=None,
                         enterprise_growth=None, joint_population=joint,
                         longitudinal=D.replace(p.longitudinal, **longitudinal)).validate()

    def test_optional_schema_roundtrip_zero_default_and_no_demographic_mapping(self):
        self.assertNotIn('joint_population', Config().to_dict())
        self.assertEqual(JointPopulationConfig().correlations(), (0.,0.,0.))
        p = self.simple()
        self.assertEqual(Config.from_dict(p.to_dict()), p)
        for bad in ({'schema_version':True}, {'schema_version':2},
                    {'skill_care_latent_correlation':True},
                    {'skill_tenure_latent_correlation':float('nan')},
                    {'skill_care_latent_correlation':1.},
                    {'skill_tenure_latent_correlation':.9,'skill_care_latent_correlation':.9,'tenure_care_latent_correlation':-.9},
                    {'skill_tenure_latent_correlation':1-1e-12},
                    {'nationality_skill_multiplier':2.}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                JointPopulationConfig.from_dict(bad)
        with self.assertRaises(ValueError): D.replace(Config(),joint_population=ASSUMPTIONS).validate()
        with self.assertRaises(ValueError): D.replace(p,joint_population={}).validate()

    def test_disabled_complete_trajectory_equals_preserved_pre_joint_source(self):
        base = Path(__file__).resolve().parent/'fixtures/integrated-r6-pre-joint-reference/dams_sim'
        pkg = types.ModuleType('joint_disabled_r6_reference')
        pkg.__path__ = [str(base)];sys.modules[pkg.__name__] = pkg
        RConfig = importlib.import_module(pkg.__name__+'.config').Config
        REngine = importlib.import_module(pkg.__name__+'.longitudinal_model').LongitudinalEngine
        p = configuration(days=12)
        r = REngine(RConfig.from_dict(p.to_dict()),storage_dir=self.root/'pre-joint',journal_chunk_bytes=1024)
        self.models.append(r)
        m = self.model(p,'disabled')._long
        for _ in range(p.days):
            r.step();m.step();a=copy.deepcopy(r.header());b=copy.deepcopy(m.header())
            a.pop('execution_source_sha256');b.pop('execution_source_sha256')
            self.assertEqual(canonical(a),canonical(b))
            self.assertEqual([D.asdict(v) for v in r.agents],[D.asdict(v) for v in m.agents])
            for table in TABLE_COLUMNS:self.assertEqual(list(r.ledger.rows(table)),list(m.ledger.rows(table)))
        self.assertEqual(r.summary(),m.summary())

    def test_explicit_zero_draws_are_bit_exact_legacy_marginals_initial_and_recruit(self):
        rng = WorldRandom(42,8,world_context='joint-control')
        for initial in (True,False):
            for i in range(50):
                token=f'person:{i}'
                actual=draw_entry_traits(JointPopulationConfig(),rng,token,initial=initial)
                expected={'skill':math.exp(.4*rng.normal('skill',token)),
                          'tenure':rng.uniform('tenure',token) if initial else 0.,
                          'care_hours':.3*rng.uniform('care',token),
                          'autonomy':rng.uniform('autonomy',token),
                          'reciprocity':rng.uniform('reciprocity',token)}
                self.assertEqual(actual,expected)

    def test_actual_latent_sample_correlation_and_marginal_units(self):
        rng=WorldRandom(42,91,world_context='joint-sampling-engineering-control')
        normal=NormalDist();draws=[draw_entry_traits(ASSUMPTIONS,rng,f'initial:{i}',initial=True) for i in range(12000)]
        skill=[math.log(v['skill'])/.4 for v in draws]
        tenure=[normal.inv_cdf(v['tenure']) for v in draws]
        care=[normal.inv_cdf(v['care_hours']/.3) for v in draws]
        for a,b,target in ((skill,tenure,.55),(skill,care,-.35),(tenure,care,.1)):
            self.assertAlmostEqual(correlation(a,b),target,delta=.025)
        self.assertAlmostEqual(mean(skill),0.,delta=.035)
        self.assertAlmostEqual(pstdev(skill),1.,delta=.035)
        self.assertAlmostEqual(mean([v['tenure'] for v in draws]),.5,delta=.012)
        self.assertAlmostEqual(mean([v['care_hours'] for v in draws]),.15,delta=.004)
        self.assertTrue(all(0<=v['care_hours']<=.3 and 0<=v['tenure']<=1 and v['skill']>0 for v in draws))
        # Appointment tenure is not sampled as prior employment for a recruit.
        entrants=[draw_entry_traits(ASSUMPTIONS,rng,f'entry:5:{i}',initial=False) for i in range(12000)]
        self.assertTrue(all(v['tenure']==0. for v in entrants))
        self.assertAlmostEqual(correlation([math.log(v['skill'])/.4 for v in entrants],
                                         [normal.inv_cdf(v['care_hours']/.3) for v in entrants]),-.35,delta=.025)

    def test_person_world_streams_ignore_order_and_unrelated_calls(self):
        rng=WorldRandom(42,8,world_context='joint-order')
        tokens=[f'initial:{i}' for i in range(80)]
        forward={k:draw_entry_traits(ASSUMPTIONS,rng,k,initial=True) for k in tokens}
        rng.normal('unrelated-policy',123)
        reverse={k:draw_entry_traits(ASSUMPTIONS,rng,k,initial=True) for k in reversed(tokens)}
        self.assertEqual(forward,reverse)
        other=WorldRandom(42,9,world_context='joint-order')
        scoped=WorldRandom(42,8,world_context='joint-other')
        self.assertNotEqual(forward[tokens[0]],draw_entry_traits(ASSUMPTIONS,other,tokens[0],initial=True))
        self.assertNotEqual(forward[tokens[0]],draw_entry_traits(ASSUMPTIONS,scoped,tokens[0],initial=True))

    def test_initial_and_recruit_entry_witness_full_raw_and_recovery(self):
        p=self.simple(days=20,exit_schedule=((1,0,'exit'),),annual_vacancy_fill_probability=1.,world_context='joint-recruit-recovery')
        full=self.model(p,'joint-full');full.run()
        split=self.model(p,'joint-split');split.run(7)
        cp=self.root/'joint-cp.json';split.write_checkpoint(cp)
        resumed=Model.restore_checkpoint(cp,expected_config=p,storage_dir=self.root/'joint-resumed')
        self.models.append(resumed);resumed.run()
        self.assertEqual(full.semantic_digest(),resumed.semantic_digest())
        self.assertEqual(full.state(),resumed.state())
        self.assertEqual(full.summary()['model_version'],VERSION)
        self.assertGreater(full.metrics['entrants'],0)
        self.raw(full,'full-raw');_,db=self.raw(resumed,'resumed-raw')
        import sqlite3
        connection=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
        try: entries=[json.loads(v[3]) for v in iter_journal_rows(connection) if v[1]=='entry']
        finally:connection.close()
        self.assertEqual(len(entries),len(resumed.people))
        self.assertTrue(all(v['joint_population']['configuration_sha256'] for v in entries))
        recruit=[v for v in entries if not v['initial']]
        self.assertTrue(recruit)
        self.assertTrue(all(v['joint_population']['entry_traits']['tenure']==0. for v in recruit))

    def test_joint_recovery_immutable_trait_and_tenure_forgeries_refused(self):
        m=self.model(self.simple(days=8),'joint-immutable');m.run()
        before=m.semantic_digest()
        for field in ('skill','care_hours','autonomy','reciprocity','tenure'):
            forged=copy.deepcopy(m.state());forged['agents'][0][field]+=.01
            with self.subTest(field=field),self.assertRaises(ValueError):
                Model.restore(forged,storage_dir=self.root/('forged-'+field))
        self.assertEqual(m.semantic_digest(),before)

    def test_raw_entry_witness_missing_or_coherently_forged_is_refused(self):
        p=self.simple();rng=WorldRandom(p.seed,p.world,world_context=p.longitudinal.world_context)
        traits=draw_entry_traits(ASSUMPTIONS,rng,'initial:0',initial=True)
        witness=generation_witness(ASSUMPTIONS,traits,initial=True)
        _verify_joint_entry(p,rng,'initial:0',True,witness,traits)
        with self.assertRaises(ValueError):_verify_joint_entry(p,rng,'initial:0',True,None,traits)
        forged=copy.deepcopy(witness);agent=copy.deepcopy(traits)
        forged['entry_traits']['care_hours']+=.01;agent['care_hours']+=.01
        with self.assertRaises(ValueError):_verify_joint_entry(p,rng,'initial:0',True,forged,agent)
        with self.assertRaises(ValueError):_verify_joint_entry(D.replace(p,joint_population=None),rng,'initial:0',True,witness,traits)

    def test_dependence_changes_actual_time_and_production_not_unused_metadata(self):
        zero=self.model(self.simple(joint=JointPopulationConfig()),'zero-dependence')
        joint=self.model(self.simple(),'joint-dependence')
        zero.run();joint.run();self.raw(zero,'zero-raw');self.raw(joint,'joint-raw')
        self.assertEqual([a.skill for a in zero.agents],[a.skill for a in joint.agents])
        self.assertNotEqual([a.care_hours for a in zero.agents],[a.care_hours for a in joint.agents])
        self.assertNotEqual(zero.metrics['available_member_work_hours'],joint.metrics['available_member_work_hours'])
        self.assertNotEqual(zero.metrics['effort_hours'],joint.metrics['effort_hours'])
        self.assertNotEqual(zero.metrics['produced_work_units'],joint.metrics['produced_work_units'])
        self.assertLessEqual(joint.metrics['max_individual_time_booked_hours'],1+1e-12)

    def test_existing_acquired_profiles_and_history_are_not_redrawn(self):
        p=D.replace(configuration(),joint_population=ASSUMPTIONS).validate()
        m=self.model(p,'joint-ma');m.run();self.raw(m,'joint-ma-raw')
        self.assertEqual(m.metrics['acquired_people'],4)
        for profile in p.enterprise_acquisition.targets[0].employees:
            person=next(v for v in m.people if v.get('source_person')==profile.key)
            a=m.agents[person['id']]
            for field in ('skill','care_hours','autonomy','reciprocity'):
                self.assertEqual(getattr(a,field),getattr(profile,field))
        self.assertEqual(m.acquisitions.targets['target-a']['retained_evidence'],
                         [D.asdict(v) for v in p.enterprise_acquisition.targets[0].evidence])

    def test_year_boundary_and_closed_calendar_tenure_recovery(self):
        p=self.simple(days=9,calendar_start='2020-12-28',closure_day=3)
        m=self.model(p,'calendar-close');m.run();m._long.validate_semantics()
        cp=self.root/'calendar-close.json';m.write_checkpoint(cp)
        restored=Model.restore_checkpoint(cp,expected_config=p,storage_dir=self.root/'calendar-close-resume')
        self.models.append(restored);self.assertEqual(m.semantic_digest(),restored.semantic_digest())
        self.raw(restored,'calendar-close-raw')

    def test_near_endpoint_quantiles_and_joint_source_config_separation(self):
        self.assertTrue(math.isfinite(_quantile(0.)))
        self.assertTrue(math.isfinite(_quantile(1.)))
        for value in (-.1,1.1,float('nan')):
            with self.assertRaises(ValueError):_quantile(value)
        m=self.model(self.simple(),'joint-separation');m.run(3)
        forged=copy.deepcopy(m.state());forged['config'].pop('joint_population')
        with self.assertRaises(ValueError):Model.restore(forged,storage_dir=self.root/'wrong-version')
        with self.assertRaises(ValueError):
            m._long.fork(D.replace(m.config,joint_population=JointPopulationConfig()),storage_dir=self.root/'wrong-fork')


if __name__=='__main__':unittest.main()
