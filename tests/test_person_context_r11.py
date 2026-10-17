"""R11 person/context controls. Engineering checks only; no formal worlds."""
import dataclasses as D
import json
import math
from pathlib import Path
import statistics
import tempfile
import unittest

from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.organization_tasks import OrganizationTasksConfig as OC
from dams_sim.person_context import PersonContextConfig as PC, PersonContextState, TRAITS, VERSION
from dams_sim.randomness import WorldRandom
from dams_sim.joint_population import JointPopulationConfig
import test_organization_tasks_r7 as _r7
from test_enterprise_acquisition import configuration


CORR = {'conscientiousness|neuroticism': -0.4, 'agreeableness|conscientiousness': 0.3,
        'extraversion|neuroticism': -0.3, 'openness|education': 0.3, 'judgment|education': 0.4}


def person(**kw):
    base = dict(trait_correlations=CORR, employment_mix=[['permanent', .8, 1.], ['fixed-term', .2, 2.]],
                caregiver_share=.3, language_groups=[['l0', .7], ['l1', .3]], cross_language_help_discount=.2,
                baseline_annual_quit_probability=.12,
                origins=[['o-a', .6, 'l0', {'patience': .2}, .05], ['o-b', .4, 'l1', {'risk_tolerance': -.2}, -.05]],
                site_origins=['o-a', 'o-b'], local_origin_share=.8, migrant_exit_multiplier=1.5,
                female_share=.45, caregiver_share_female=.4, caregiver_share_male=.15, gender_evaluation_bias=-.05,
                ethnic_groups=[['e0', .7], ['e1', .3]], minority_voice_shift=-.1, cross_ethnic_help_discount=.05,
                physical_work_share=.4, site_climate=[[1.]*6+[.85, .85]+[1.]*4, [1.]*12],
                site_institution_shocks=[[.5, .7, 10], [0., 1., 1]], annual_sick_days=8.,
                dependents_mean=1.2, remote_share=.3, commute_hours_mean=.6, appearance_evaluation_bias=-.02,
                faction_size=4, faction_cohesion=.3)
    base.update(kw)
    return PC(**base).validate()


class PersonContextTests(unittest.TestCase):
    setUp = _r7.OrganizationTests.setUp
    tearDown = _r7.OrganizationTests.tearDown
    model = _r7.OrganizationTests.model
    raw = _r7.OrganizationTests.raw
    events = _r7.OrganizationTests.events

    def config(self, days=14, organization=None, pc=None, **kw):
        p = configuration(days=max(days, 2))
        return D.replace(p, days=days, organization_tasks=organization or OC(), person_context=pc, **kw).validate()

    def test_schema_rejects_invalid_and_conflicting_inputs(self):
        self.assertNotIn('person_context', Config().to_dict())
        for bad in ({'trait_correlations': {'skill|patience': .1}}, {'trait_correlations': {'openness|openness': .1}},
                    {'trait_correlations': {'openness|education': 1.2}}, {'employment_mix': [['a', .5, 1.]]},
                    {'language_groups': [['x', .6], ['x', .4]]}, {'caregiver_share': 1.5}, {'schema_version': 2},
                    {'trait_correlations': {'conscientiousness|neuroticism': .95, 'conscientiousness|agreeableness': .95,
                                            'agreeableness|neuroticism': -.95}}, {'unknown': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PC.from_dict({**PC().to_dict(), **bad})
        with self.assertRaises(ValueError):
            self.config(pc=person(), joint_population=JointPopulationConfig())
        p = self.config(pc=person())
        self.assertEqual(Config.from_dict(json.loads(json.dumps(p.to_dict()))).to_dict(), p.to_dict())

    def test_copula_reproduces_declared_correlations_and_marginals(self):
        p = self.config(pc=person())
        state, rng = PersonContextState(p), WorldRandom(7, 0)
        draws = [state.draw(rng, f'probe:{k}') for k in range(6000)]
        col = {t: [d['traits'][t] for d in draws] for t in TRAITS}
        local = [d for d in draws if d['origin'] == 'o-a']
        lcol = {t: [d['traits'][t] for d in local] for t in TRAITS}
        for key, rho in CORR.items():
            a, b = key.split('|')
            with self.subTest(pair=key):
                self.assertAlmostEqual(statistics.correlation(lcol[a], lcol[b]), rho, delta=.06)
        self.assertAlmostEqual(statistics.fmean(lcol['patience']), .2, delta=.05)
        self.assertAlmostEqual(statistics.correlation(col['patience'], col['risk_tolerance']), 0., delta=.05)
        for t in TRAITS:
            if t not in ('patience', 'risk_tolerance'):
                self.assertAlmostEqual(statistics.fmean(col[t]), 0., delta=.05)
            self.assertAlmostEqual(statistics.pstdev(lcol[t]), 1., delta=.06)
        self.assertAlmostEqual(statistics.fmean(d['gender'] == 'female' for d in draws), .45, delta=.02)
        fem = [d for d in draws if d['gender'] == 'female']; mal = [d for d in draws if d['gender'] == 'male']
        self.assertAlmostEqual(statistics.fmean(d['caregiver'] for d in fem), .4, delta=.03)
        self.assertAlmostEqual(statistics.fmean(d['caregiver'] for d in mal), .15, delta=.03)
        self.assertTrue(all(d['language'] == ('l0' if d['origin'] == 'o-a' else 'l1') for d in draws))
        self.assertAlmostEqual(statistics.fmean(d['ethnicity'] == 'e1' for d in draws), .3, delta=.02)
        self.assertAlmostEqual(statistics.fmean(d['dependents'] for d in draws), 1.2, delta=.05)
        self.assertTrue(all(d['commute_hours'] == 0. for d in draws if d['remote']))
        self.assertFalse(any(d['remote'] and d['physical_work'] for d in draws))
        care = statistics.fmean(d['care_hours'] for d in draws if d['caregiver'])
        other = statistics.fmean(d['care_hours'] for d in draws if not d['caregiver'])
        self.assertGreater(care, other+.1)

    def test_paths_are_declared_and_labels_have_no_direct_effect(self):
        p = self.config(pc=person())
        s = PersonContextState(p)
        rec = s.draw(WorldRandom(1, 0), 'a')
        rec['traits'] = {t: 0. for t in TRAITS}
        s.append(rec)
        rec['migrant'] = False; rec['identity'] = s.c.initial_identity
        twin = dict(rec, traits=dict(rec['traits']), language='l1', caregiver=not rec['caregiver'], gender='female' if rec['gender'] == 'male' else 'male', ethnicity='e1' if rec['ethnicity'] == 'e0' else 'e0')
        s.append(twin)
        for f in ('effort_shift', 'cooperation_factor', 'decision_noise_factor', 'learning_factor', 'annual_quit_probability'):
            self.assertEqual(getattr(s, f)(0), getattr(s, f)(1), f)
        self.assertAlmostEqual(s.help_language_factor(0, 1), .8*.95)
        self.assertEqual(s.evaluation_shift(0, 1), 0.)
        self.assertEqual(s.evaluation_shift(1 if twin['gender'] == 'female' else 0, 0 if twin['gender'] == 'female' else 1), -.05)
        s.people[0]['stress'] = .8
        self.assertGreater(s.annual_quit_probability(0), s.annual_quit_probability(1))
        s.people[1]['fairness'] = .2
        self.assertGreater(s.annual_quit_probability(1), PersonContextState(p).c.baseline_annual_quit_probability)

    def test_enabled_run_is_deterministic_checkpointable_and_auditable(self):
        p = self.config(days=24, pc=person())
        full = self.model(p, 'full'); again = self.model(p, 'again'); split = self.model(p, 'split')
        full.run(); again.run(); split.run(9)
        self.assertEqual(full.semantic_digest(), again.semantic_digest())
        cp = self.root/'checkpoint.json'; split.write_checkpoint(cp)
        resumed = Model.restore_checkpoint(cp, expected_config=p, storage_dir=self.root/'resumed'); self.models.append(resumed); resumed.run()
        self.assertEqual(full.semantic_digest(), resumed.semantic_digest()); self.assertEqual(full.state(), resumed.state())
        self.assertEqual(full.summary()['model_version'], VERSION)
        entries = self.events(full, 'entry')
        self.assertTrue(entries and all('person_context' in e[2] for e in entries))
        row = full.history[-1]
        self.assertIsNotNone(row['mean_stress'])
        self.assertIn('person_context_groups', self.events(full, 'day_end')[-1][2])
        self.raw(full, 'full-raw'); self.raw(resumed, 'resumed-raw')

    def test_heavy_care_load_checkpoint_restores(self):
        # Large households push care time above the legacy 0.3 bound; R11 allows up to 0.6.
        p = self.config(days=12, pc=person(dependents_mean=8., care_hours_per_dependent=.05))
        m = self.model(p, 'care'); m.run(6)
        self.assertGreater(max(a.care_hours for a in m._long.agents), .3)
        cp = self.root/'care-cp.json'; m.write_checkpoint(cp)
        r = Model.restore_checkpoint(cp, expected_config=p, storage_dir=self.root/'care-restored'); self.models.append(r); r.run()
        m.run(); self.assertEqual(m.semantic_digest(), r.semantic_digest())

    def test_disabled_run_has_no_person_context_traces(self):
        p = self.config(days=10)
        m = self.model(p, 'off'); m.run()
        self.assertNotIn('person_context_state', m.state())
        self.assertFalse(any('person_context' in e[2] for e in self.events(m, 'entry')))
        self.assertNotIn('mean_stress', m.history[-1])

    def test_quit_hazard_generates_voluntary_exits_and_stress_responds_to_load(self):
        p = self.config(days=60, pc=person(baseline_annual_quit_probability=.95), max_wall_seconds=900.)
        m = self.model(p, 'quit'); m.run()
        self.assertGreater(sum(r['exit_reason'] == 'voluntary_exit' for r in m.people), 0)
        stress = [m.pc.people[i]['stress'] for i in m.active]
        self.assertTrue(all(0 <= v <= 1 for v in stress))
        m._long.validate_semantics()
        self.raw(m, 'quit-raw')


if __name__ == '__main__':
    unittest.main()
