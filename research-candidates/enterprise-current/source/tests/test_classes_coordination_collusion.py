"""Record classes, automated coordination, processing charged to cash and collusive review.

Pure mechanisms first, then small validated engine runs (scale 30 through the raw validators of research_tools.r11_study),
a checkpoint in the middle of an arm, and tampering controls the validators must refuse."""
import dataclasses
import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from dams_sim import automation as A
from dams_sim.automation import AutomationConfig
from dams_sim.config import Config
from dams_sim.enterprise_operations import EnterpriseOperatingState
from dams_sim.longitudinal import LongitudinalConfig
from dams_sim.longitudinal_model import LongitudinalEngine, verify_snapshot
from dams_sim.model import Model
from dams_sim.randomness import WorldRandom
from dams_sim.storage import atomic_csv, canonical
from research_tools import r11_design, r11_study
from research_tools.validate_longitudinal import iter_journal_rows


def journal(out):
    con = sqlite3.connect(f'file:{next(Path(out).rglob("final_state.sqlite"))}?mode=ro&immutable=1', uri=True)
    try:
        return [(d, k, i, json.loads(p)) for d, k, i, p in iter_journal_rows(con)]
    finally:
        con.close()


class RecordClassTest(unittest.TestCase):
    N = 40000

    def test_class_draw_partitions_the_unit_interval_by_the_declared_shares(self):
        c = AutomationConfig(record_classes=True)
        self.assertEqual([A.record_class(c, u) for u in (0., .69, .70, .94, .95, .999)], ['objective', 'objective', 'inferential', 'inferential', 'disputed', 'disputed'])
        rng = WorldRandom(5, 1); counts = Counter(A.record_class(c, rng.uniform('record_class', i)) for i in range(self.N))
        for k, share in (('objective', .7), ('inferential', .25), ('disputed', .05)):
            self.assertAlmostEqual(counts[k]/self.N, share, delta=4*math.sqrt(.25/self.N))

    def test_inferential_records_abstain_and_err_more_and_cost_twice_the_units(self):
        c = AutomationConfig(record_classes=True)
        rng = WorldRandom(11, 3)
        def draw(klass, rule_accept):
            out = Counter()
            for i in range(self.N):out[A.verdict(c, rule_accept=rule_accept, u_uncertain=rng.uniform('u', i), u_error=rng.uniform('e', i), klass=klass)] += 1
            return out
        tol = 4*math.sqrt(.25/self.N)
        for klass, abstain, error in (('objective', .05, .05), ('inferential', .20, .10)):
            honest, fraud = draw(klass, True), draw(klass, False)
            self.assertAlmostEqual(honest['exception']/self.N, abstain, delta=tol); self.assertAlmostEqual(fraud['exception']/self.N, abstain, delta=tol)
            self.assertAlmostEqual(honest['reject']/(self.N-honest['exception']), error, delta=tol)
            self.assertAlmostEqual(fraud['accept']/(self.N-fraud['exception']), error, delta=tol)
        self.assertEqual((A.class_multiplier(c, 'inferential'), A.class_multiplier(c, 'objective'), A.class_multiplier(c, 'disputed')), (2., 1., 1.))
        self.assertEqual(A.verdict(c, rule_accept=True, u_uncertain=.5, u_error=.5), A.verdict(c, rule_accept=True, u_uncertain=.5, u_error=.5, klass='objective'))

    def test_contract_and_hidden_fields(self):
        base = AutomationConfig(sandboxes=True).to_dict()
        for name in AutomationConfig.CLASS_FIELDS + AutomationConfig.COORDINATION_FIELDS + ('charge_processing',):self.assertNotIn(name, base)
        on = AutomationConfig(record_classes=True, coordination_automation=.5, charge_processing=True)
        self.assertEqual(AutomationConfig.from_dict(on.to_dict()), on)
        self.assertEqual(on.to_dict()['coordination_processing_units_per_member_day'], .5)
        for bad in ({'record_classes': True, 'class_share_disputed': .1}, {'record_classes': True, 'class_share_objective': .9, 'class_share_inferential': .2, 'class_share_disputed': 0.},
                    {'class_share_disputed': .1, 'class_share_objective': .65}, {'inferential_processing_multiplier': 2.5},
                    {'record_classes': True, 'inferential_processing_multiplier': .5}, {'coordination_automation': 1.5},
                    {'coordination_processing_units_per_member_day': 1.}, {'charge_processing': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):AutomationConfig.from_dict(bad)
        d, branch = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1)
        cfg = r11_design.arm_config(d, branch, 'dams-auto-coord'); cfg2 = dict(cfg); cfg2.pop('enterprise_operations'); cfg2.pop('organization_tasks'); cfg2.pop('enterprise_growth', None)
        with self.assertRaises(ValueError):Config.from_dict(cfg2)       # nothing to automate without coordination or reporting models

    def test_earlier_arm_configuration_digests_are_unchanged(self):
        expected = {'control-hierarchy': 'f19fa3086825b433', 'dams-auto': '6df586a396a9e91b', 'dams-isa': 'd265fe2c60e012ec', 'hierarchy-isa': '67b57b675455cf84'}
        d, branch = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1, variant='accounted-work')
        for arm, digest in expected.items():
            self.assertEqual(hashlib.sha256(canonical(Config.from_dict(r11_design.arm_config(d, branch, arm)).to_dict())).hexdigest()[:16], digest, arm)


class CoordinationAndVariantTest(unittest.TestCase):
    def setUp(self):
        d, branch = r11_design.base_config(scale=60, context='multinational', growth='organic', world=1, seed=2, pre_years=0, post_years=1)
        self.ops = EnterpriseOperatingState(Config.from_dict(r11_design.arm_config(d, branch, 'control-hierarchy')))

    def test_per_layer_time_scales_with_the_automated_share_and_composes_with_scoped_layers(self):
        o = self.ops; args = (60, 2, 4, 6, 0)
        full, none = o.coordination_required(*args), o.coordination_required(*args, layer_time=0.)
        self.assertGreater(full, none)
        self.assertAlmostEqual(o.coordination_required(*args, layer_time=.5), (full+none)/2, places=12)       # only the per-layer term is scaled
        scope = lambda layers: A.effective_layers(AutomationConfig(sandboxes=True, scoped_authority=True), layers)
        scoped, scoped_none = o.coordination_required(*args, scope), o.coordination_required(*args, scope, 0.)
        self.assertLess(scoped, full)
        self.assertAlmostEqual(o.coordination_required(*args, scope, .5), (scoped+scoped_none)/2, places=12)    # share applied to the layers borne
        self.assertEqual(o.coordination_required(*args, None, 1.), full)

    def test_variants_set_the_automation_block_of_arms_that_have_one(self):
        base = lambda variant: r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1, variant=variant)
        for variant, rate in (('accounted-work-decisions-charged', .01), ('accounted-work-decisions-charged-r10', .1)):
            d, branch = base(variant)
            self.assertNotIn('automation', d); self.assertEqual(d['decision_effect'], {})
            auto = Config.from_dict(r11_design.arm_config(d, branch, 'dams-auto', variant)).automation
            self.assertEqual((auto.charge_processing, auto.resource_units_per_processing_unit), (True, rate))
            self.assertNotIn('automation', r11_design.arm_config(d, branch, 'control-hierarchy', variant))
            self.assertFalse(Config.from_dict(r11_design.arm_config(d, branch, 'dams-auto')).automation.charge_processing)   # no variant, no charge
        shared = lambda variant: {k: v for k, v in base(variant)[0].items()}
        self.assertEqual(shared('accounted-work-decisions'), shared('accounted-work-decisions-charged'))      # the shared history is unchanged

    def test_coord_arms_are_the_corresponding_arms_with_share_one_half(self):
        d, branch = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1)
        for arm, twin, extra in (('hierarchy-auto-coord', 'hierarchy-auto', {}), ('dams-auto-coord', 'dams-auto', {}),
                                 ('hierarchy-isa-coord', 'hierarchy-isa-full', {}), ('dams-isa-coord', 'dams-isa-full', {})):
            a, b = Config.from_dict(r11_design.arm_config(d, branch, arm)), Config.from_dict(r11_design.arm_config(d, branch, twin))
            self.assertEqual((a.regime, a.backend), (b.regime, b.backend))
            self.assertEqual(a.automation.coordination_automation, .5); self.assertEqual(b.automation.coordination_automation, 0.)
            self.assertEqual(dataclasses.replace(a.automation, coordination_automation=0.), b.automation)

    def test_collusion_variants_configure_the_attack_window_after_the_boundary(self):
        for name, fraction in (('accounted-work-decisions-collude05', .05), ('accounted-work-decisions-collude20', .2)):
            d, branch = r11_design.base_config(scale=60, context='multinational', growth='organic', world=1, seed=2, pre_years=1, post_years=2, variant=name)
            c = Config.from_dict(d)
            self.assertEqual((c.attack, c.attack_cohort_fraction, c.attack_start_day, c.attack_stop_day), ('collude', fraction, branch, c.days))
            self.assertAlmostEqual(c.attack_budget_hours_per_day, .1*math.ceil(60*fraction))
        with self.assertRaises(ValueError):Config.from_dict({**Config().to_dict(), 'attack': 'collude'})     # longitudinal engine only


class PlainEngineCollusionTest(unittest.TestCase):
    """Without the organization-tasks model there is no named reviewer: a present member of the domain is drawn."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')); self.root = Path(self.temp.name); self.models = []
    def tearDown(self):
        for m in self.models:m.ledger.close()
        self.temp.cleanup()

    def config(self, attack):
        return Config(n=24, days=60, guilds=2, team_size=2, trace_every_days=1, max_output_mb=60, attack=attack, attack_cohort_fraction=.25,
                      attack_budget_hours_per_day=.3, attack_start_day=10, attack_stop_day=60,
                      longitudinal=LongitudinalConfig(annual_exit_probability=0., annual_vacancy_fill_probability=0., adoption_mode='never')).validate()

    def test_bought_acceptances_follow_the_cohort_and_window_and_pass_the_validator(self):
        from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence
        m = Model(self.config('collude'), storage_dir=self.root/'c'); self.models.append(m); m.run()
        s = m.summary(); slots = lambda i: m._long.people[i]['slot']
        self.assertGreater(s['fraudulent_records_submitted'], 100); self.assertGreater(s['colluded_acceptances'], 0)
        self.assertLessEqual(s['colluded_decisive_acceptances'], s['colluded_acceptances']); self.assertGreater(s['colluded_decisive_acceptances'], 0)
        final = self.root/'c.json'; m.write_final_state(final); st, db = verify_snapshot(final, expected_config=m.config)
        csv = self.root/'c.csv'; atomic_csv(csv, m.history)
        verify_daily_evidence(csv, m.config, st['state'], db); verify_summary_evidence(json.loads(canonical(m.summary())), m.config, st['state'], db)
        con = sqlite3.connect(f'file:{db}?mode=ro&immutable=1', uri=True)
        reviews = [json.loads(p) for _d, k, _i, p in iter_journal_rows(con) if k == 'review']; con.close()
        bought = [r for r in reviews if r['colluded']]
        self.assertEqual(len(bought), s['colluded_acceptances'])
        self.assertTrue(all(r['accepted'] and r['fraudulent'] and slots(r['collusion_reviewer']) < math.ceil(24*.25) for r in bought))
        honest = [r for r in reviews if not r['colluded'] and r['fraudulent']]
        self.assertTrue(honest)                                                      # forged records meeting a non-cohort reviewer follow the rule
        self.assertTrue(all(r['accepted'] == r['rule_accept'] for r in honest))
        plain = Model(self.config('forge'), storage_dir=self.root/'f'); self.models.append(plain); plain.run()
        self.assertNotIn('colluded_acceptances', plain.summary())
        self.assertLess(plain.summary()['fraudulent_records_accepted'], s['fraudulent_records_accepted'])


class EngineTest(unittest.TestCase):
    """Validated runs at scale 30: every arm passes the raw validators in r11_study.arm."""
    VARIANTS = ('accounted-work-decisions-classes-charged', 'accounted-work-decisions-charged', 'accounted-work-decisions-charged-r10',
                'accounted-work-decisions-collude20', 'accounted-work-decisions')

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(dir=os.environ.get('TMPDIR')); cls.root = Path(cls.tmp)
        cls.pre = {}
        for v in cls.VARIANTS:
            r11_study.prehistory(dict(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1, variant=v, out=str(cls.root/('pre-'+v))))
            cls.pre[v] = cls.root/('pre-'+v)
        cls.out = {}; cls.models = []

    @classmethod
    def tearDownClass(cls):
        for m in cls.models:m._long.ledger.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_arm(self, arm, variant):
        if (arm, variant) not in self.out:
            out = self.root/f'{arm}@{variant}'
            r11_study.arm(dict(prehistory=str(self.pre[variant]), arm=arm, retain_journal=True, out=str(out)))
            self.out[(arm, variant)] = (out, json.loads((out/'summary.json').read_text()))
        return self.out[(arm, variant)]

    def fork(self, arm, variant, name):
        pre = self.pre[variant]; meta = json.loads((pre/'prehistory.json').read_text()); parent = Config.from_dict(meta['config'])
        child = Config.from_dict(r11_design.arm_config(meta['config'], meta['branch_day'], arm, meta.get('variant')))
        base = Model.restore_checkpoint(pre/meta['checkpoint'], expected_config=parent, storage_dir=self.root/(name+'-restored'))
        m = base.fork(child, storage_dir=self.root/(name+'-work')); base._long.ledger.close(); self.models.append(m)
        return m, child, parent, meta['branch_day']

    CV = 'accounted-work-decisions-classes-charged'

    # ---- record classes ----
    def test_record_classes_route_disputed_records_to_humans_and_charge_inferential_records_double(self):
        out, s = self.run_arm('dams-auto-coord', self.CV)
        rows = journal(out); autos = [v for _d, k, _i, v in rows if k == 'auto_verify']
        by = {k: [v for v in autos if v['record_class'] == k] for k in A.CLASSES}
        self.assertTrue(all(len(by[k]) > 100 for k in by))
        self.assertTrue(all(v['route'] == 'disputed' and v['verdict'] == 'exception' and v['processing_units'] == 0 for v in by['disputed']))   # never automated
        self.assertEqual({v['route'] for v in autos if v['route'] == 'disputed'}, {'disputed'}); self.assertFalse([v for v in autos if v['route'] == 'disputed' and v['record_class'] != 'disputed'])
        units = {k: {v['processing_units'] for v in by[k] if v['route'] in ('automated', 'uncertain')} for k in A.CLASSES}
        self.assertEqual((units['objective'], units['inferential']), ({1.2}, {2.4}))      # witness evidence backend multiplier 1.2; inferential twice
        rc = s['record_class_summary']
        self.assertEqual(sum(rc[k]['claims'] for k in rc), len(autos))
        self.assertEqual(rc['disputed']['automated_decisions'], 0); self.assertEqual(rc['disputed']['human_reviews'] + rc['disputed']['confirmed'] > 0, True)
        self.assertLess(rc['inferential']['automated_share'], rc['objective']['automated_share'])
        self.assertGreater(rc['inferential']['wrong_automated_decisions']/rc['inferential']['automated_decisions'],
                           rc['objective']['wrong_automated_decisions']/rc['objective']['automated_decisions'])
        self.assertAlmostEqual(sum(rc[k]['processing_units'] for k in rc), s['automated_processing_units'], places=6)
        self.assertEqual(s['exception_disputed'], rc['disputed']['claims'])
        for k in rc:self.assertGreater(rc[k]['mean_days_to_confirmation'], 0)
        self.assertGreater(rc['disputed']['mean_days_to_confirmation'], 0)
        shares = {k: rc[k]['claims']/len(autos) for k in rc}
        self.assertAlmostEqual(shares['inferential'], .25, delta=.03); self.assertAlmostEqual(shares['disputed'], .05, delta=.02)

    # ---- automated coordination and charged processing ----
    def test_coordination_automation_cuts_coordination_and_reporting_time_and_is_counted(self):
        out, s = self.run_arm('dams-auto-coord', self.CV); base = self.run_arm('dams-auto', self.CV)[1]
        self.assertAlmostEqual(s['coordination_processing_units'], .5*s['present_member_workdays'], places=6)    # every guild adopted at the boundary
        self.assertNotIn('coordination_processing_units', base)
        self.assertLess(s['enterprise_coordination_requested_workdays'], base['enterprise_coordination_requested_workdays'])
        self.assertLess(s['organization_network_hours'], base['organization_network_hours'])
        plans = [v for _d, k, _i, v in journal(out) if k == 'organization_plan']
        automated = [a for v in plans for a in v['allocations'].values() if 'reporting_automation' in a]
        self.assertTrue(automated); self.assertTrue(all(a['reporting_automation'] == .5 for a in automated))
        sites = [r for _d, k, _i, v in journal(out) if k == 'enterprise_coordination_begin' for r in v['plan']['sites'] if 'automated_people' in r]
        self.assertTrue(sites); self.assertTrue(all(r['automation_share'] == .5 for r in sites))

    def test_coordination_automation_composes_with_scoped_authority(self):
        iso, base = self.run_arm('hierarchy-isa-coord', self.CV)[1], self.run_arm('hierarchy-isa-full', self.CV)[1]
        auto = self.run_arm('hierarchy-auto-coord', self.CV)[1]
        self.assertLess(iso['enterprise_coordination_requested_workdays'], base['enterprise_coordination_requested_workdays'])
        self.assertLess(iso['organization_network_hours'], base['organization_network_hours'])
        self.assertLess(iso['enterprise_coordination_requested_workdays'], auto['enterprise_coordination_requested_workdays'])    # scope and automation both cut it

    def test_charged_processing_is_paid_from_cash_and_scales_with_the_rate(self):
        low, high = self.run_arm('dams-auto-coord', 'accounted-work-decisions-charged')[1], self.run_arm('dams-auto-coord', 'accounted-work-decisions-charged-r10')[1]
        for s, rate in ((low, .01), (high, .1)):
            units = s['automated_processing_units']+s['verification_resource_units']+s.get('coordination_processing_units', 0.)
            self.assertAlmostEqual(s['processing_charge_resource_units'], rate*units, delta=1e-6*units+1e-9)     # cash never ran short
        self.assertGreater(high['processing_charge_resource_units'], 5*low['processing_charge_resource_units'])
        self.assertLess(high['cash_resource_units'], low['cash_resource_units'])
        none = self.run_arm('dams-auto', 'accounted-work-decisions')[1]
        self.assertNotIn('processing_charge_resource_units', none)
        out = self.run_arm('dams-auto-coord', 'accounted-work-decisions-charged')[0]
        charges = [v for _d, k, _i, v in journal(out) if k == 'processing_charge']
        self.assertEqual(len(charges), 366); self.assertAlmostEqual(math.fsum(v['charged'] for v in charges), low['processing_charge_resource_units'], places=6)

    # ---- collusive review ----
    def test_collusion_buys_acceptance_only_from_cohort_reviewers_and_never_from_the_verifier(self):
        V = 'accounted-work-decisions-collude20'
        manual, auto = self.run_arm('control-hierarchy', V), self.run_arm('dams-isa-full', V)
        for out, s in (manual, auto):
            self.assertGreater(s['fraudulent_records_submitted'], 500); self.assertGreater(s['colluded_acceptances'], 0)
            reviews = [v for _d, k, _i, v in journal(out) if k == 'review']
            bought = [r for r in reviews if r['colluded']]
            self.assertEqual(len(bought), s['colluded_acceptances']); self.assertTrue(all(r['fraudulent'] and r['accepted'] for r in bought))
            people = json.loads((out/'final_state.json').read_text())['state']['people']
            self.assertTrue(all(people[r['verification_reviewer']]['slot'] < math.ceil(30*.2) for r in bought))
            self.assertTrue(all(r['accepted'] == r['rule_accept'] for r in reviews if not r['colluded']))
        self.assertEqual(manual[1].get('forged_accepted_by_automation', 0), 0)
        self.assertGreater(auto[1]['forged_accepted_by_automation'], 0)                    # the verifier errs at its declared rates, it is not bribed
        self.assertLess(auto[1]['colluded_acceptances'], manual[1]['colluded_acceptances'])    # only the exception lane is exposed
        self.assertLess(auto[1]['fraudulent_records_accepted'], manual[1]['fraudulent_records_accepted'])
        self.assertGreater(manual[1]['forged_credit_units'], 0)
        control = self.run_arm('dams-auto', 'accounted-work-decisions')[1]
        self.assertNotIn('fraudulent_records_submitted', control); self.assertNotIn('colluded_acceptances', control)

    # ---- checkpoints ----
    def test_checkpoint_in_the_middle_of_an_arm_keeps_the_new_mechanisms(self):
        for arm, variant in (('dams-isa-coord', self.CV), ('hierarchy-auto', 'accounted-work-decisions-collude20')):
            whole, child, parent, branch = self.fork(arm, variant, 'ckpt-'+arm)
            whole.run(branch+150); path = self.root/f'ckpt-{arm}.json'; whole.write_checkpoint(path)
            resumed = Model.restore_checkpoint(path, storage_dir=self.root/f'ckpt-{arm}-resumed', expected_config=child)
            whole.run(); resumed.run()
            self.assertEqual(resumed.state(), whole.state()); self.assertEqual(resumed._long.semantic_digest(), whole._long.semantic_digest())
            self.assertEqual(canonical(resumed.summary()), canonical(whole.summary()))
            final = self.root/f'ckpt-{arm}-final.json'; resumed.write_final_state(final); atomic_csv(self.root/'timeseries.csv', resumed.history)
            summary = json.loads(canonical(resumed.summary())); resumed._long.ledger.close()
            r11_study._validate(resumed, final, parent, branch, summary)

    # ---- tampering ----
    def test_validators_refuse_a_wrong_class_multiplier(self):
        with mock.patch.object(A, 'class_multiplier', lambda c, k: 3. if k == 'inferential' else 1.):
            m, *_ = self.fork('dams-auto', self.CV, 'tamper-units')
            m.run(); final = self.root/'tamper-units-final.json'; m.write_final_state(final); atomic_csv(self.root/'timeseries.csv', m.history)
            summary = json.loads(canonical(m.summary())); m._long.ledger.close()
            with self.assertRaisesRegex(ValueError, 'units/verdict differ'):
                r11_study._validate(m, final, Config.from_dict(json.loads((self.pre[self.CV]/'prehistory.json').read_text())['config']), 0, summary)

    def test_validators_refuse_reporting_automation_that_differs_from_the_declared_share(self):
        original = LongitudinalEngine._coordination_share
        with mock.patch.object(LongitudinalEngine, '_coordination_share', lambda self, guild: 0. if guild == 0 else original(self, guild)):
            m, *_ = self.fork('hierarchy-auto-coord', self.CV, 'tamper-share'); m.run(40)
            with self.assertRaisesRegex(ValueError, 'reporting automation differs'):
                m.write_checkpoint(self.root/'tamper-share.json')

    def test_validators_refuse_a_bought_acceptance_for_a_reviewer_outside_the_cohort(self):
        V = 'accounted-work-decisions-collude20'
        with mock.patch.object(LongitudinalEngine, '_colluding', lambda self, reviewer, claim, day: bool(claim.fraudulent) and reviewer is not None):
            m, *_ = self.fork('control-hierarchy', V, 'tamper-collusion'); m.run()
            final = self.root/'tamper-collusion-final.json'; m.write_final_state(final); atomic_csv(self.root/'timeseries.csv', m.history)
            summary = json.loads(canonical(m.summary())); m._long.ledger.close()
            with self.assertRaisesRegex(ValueError, 'collusion record differs'):
                r11_study._validate(m, final, Config.from_dict(json.loads((self.pre[V]/'prehistory.json').read_text())['config']), 0, summary)


if __name__ == '__main__':
    unittest.main()
