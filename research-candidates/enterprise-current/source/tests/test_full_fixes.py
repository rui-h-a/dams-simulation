"""Fixes to the full configuration: shadow evaluation of self-modification, the reporting delay under scoped authority,
the symmetric processing charge, and the sensitivity variants of `full`.

Pure mechanisms first, then small validated engine runs (scale 30 through the raw validators of research_tools.r11_study)."""
import dataclasses
import json
import math
import os
import shutil
import sqlite3
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

from dams_sim import automation as A
from dams_sim.automation import AutomationConfig
from dams_sim.config import Config
from dams_sim.model import Model
from research_tools import r11_design, r11_study
from research_tools.validate_longitudinal import iter_journal_rows


def journal(out):
    con = sqlite3.connect(f'file:{next(Path(out).rglob("final_state.sqlite"))}?mode=ro&immutable=1', uri=True)
    try:
        return [(d, k, i, json.loads(p)) for d, k, i, p in iter_journal_rows(con)]
    finally:
        con.close()


def round_half_up(x):
    return math.floor(x+Fraction(1, 2))


class ShadowRoutesTest(unittest.TestCase):
    def test_claims_that_humans_handle_live_are_human_in_the_shadow(self):
        for route in ('quota', 'outage', 'disputed'):
            for hypothetical in ('accept', 'reject', 'exception'):
                self.assertTrue(A.shadow_to_human(False, route, hypothetical), (route, hypothetical))
        self.assertTrue(A.shadow_to_human(True, 'automated', 'accept'))        # sampled under the candidate fraction
        self.assertTrue(A.shadow_to_human(False, 'automated', 'exception'))    # the verifier abstains
        self.assertTrue(A.shadow_to_human(False, 'sampled', 'exception'))
        for route in ('automated', 'sampled', 'uncertain', None):
            for hypothetical in ('accept', 'reject'):
                self.assertFalse(A.shadow_to_human(False, route, hypothetical), (route, hypothetical))

    def test_a_sampled_claim_is_human_in_the_shadow_while_the_service_is_down(self):
        # live-sampled claims keep the route 'sampled'; the outage of their sandbox is passed separately
        self.assertTrue(A.shadow_to_human(False, 'sampled', 'accept', service_down=True))
        self.assertTrue(A.shadow_to_human(False, 'automated', 'reject', service_down=True))
        self.assertFalse(A.shadow_to_human(False, 'sampled', 'accept', service_down=False))

    def test_every_route_that_live_handling_sends_to_humans_is_covered(self):
        # the routes of automation.ROUTES that are decided by a human without the candidate fraction mattering
        self.assertEqual(set(A.SHADOW_HUMAN_ROUTES), {'quota', 'outage', 'disputed'})
        self.assertLessEqual(set(A.SHADOW_HUMAN_ROUTES), set(A.ROUTES))


class ReportingDelayTest(unittest.TestCase):
    def config(self, share, layers=1):
        return AutomationConfig(sandboxes=True, scoped_authority=True, cross_boundary_share=share, in_scope_layers=layers)

    def test_delay_is_the_scope_plus_the_rounded_cross_boundary_share_of_the_layers_above(self):
        for l0 in (0, 1, 2):
            for share in (0., .25, .5, .75, 1.):
                c = self.config(share, l0)
                for depth in range(0, 7):
                    inside = min(depth, l0)
                    expected = inside+round_half_up(Fraction(share).limit_denominator(100)*(depth-inside))
                    self.assertEqual(A.in_scope_delay_layers(c, depth), expected, (l0, share, depth))

    def test_share_one_is_the_unscoped_rule_and_share_zero_the_scope(self):
        for l0 in (0, 1, 2):
            for depth in range(0, 7):
                self.assertEqual(A.in_scope_delay_layers(self.config(1., l0), depth), depth)
                self.assertEqual(A.in_scope_delay_layers(self.config(0., l0), depth), min(depth, l0))

    def test_quarter_and_half_share_at_depths_two_to_five(self):
        quarter = [A.in_scope_delay_layers(self.config(.25), d) for d in (2, 3, 4, 5)]
        half = [A.in_scope_delay_layers(self.config(.5), d) for d in (2, 3, 4, 5)]
        self.assertEqual(quarter, [1, 2, 2, 2])    # 1+floor(.25(d-1)+.5)
        self.assertEqual(half, [2, 2, 3, 3])       # 1+floor(.5(d-1)+.5): a half layer rounds up

    def test_delay_never_exceeds_depth_and_does_not_shrink_with_depth(self):
        for share in (0., .1, .25, .5, .9, 1.):
            c = self.config(share)
            delays = [A.in_scope_delay_layers(c, d) for d in range(0, 9)]
            self.assertTrue(all(x <= d for x, d in zip(delays, range(9))))
            self.assertEqual(delays, sorted(delays))


class ProcessingChargeConfigTest(unittest.TestCase):
    def test_base_level_rate_is_validated_and_hidden_when_unset(self):
        d, branch = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1)
        self.assertNotIn('processing_charge_rate', d)
        self.assertIsNone(Config.from_dict(d).processing_charge_rate)
        self.assertNotIn('processing_charge_rate', Config.from_dict(d).to_dict())
        for bad in (-1., float('nan'), True, 'x'):
            with self.assertRaises(ValueError):
                Config.from_dict({**d, 'processing_charge_rate': bad})
        c = Config.from_dict({**d, 'processing_charge_rate': .01})
        self.assertEqual(c.to_dict()['processing_charge_rate'], .01)
        self.assertEqual(Config.from_dict(c.to_dict()), c)
        with self.assertRaises(ValueError):
            Config.from_dict({**Config().to_dict(), 'processing_charge_rate': .01})    # longitudinal engine only

    def test_earlier_variants_do_not_set_the_base_rate_and_full_does(self):
        for name, spec in r11_design.VARIANTS.items():
            self.assertEqual('processing_charge_rate' in spec, name.startswith('full'), name)
        d, _ = r11_design.base_config(scale=30, context='traditional', growth='organic', world=0, seed=2, pre_years=0, post_years=1, variant='full')
        self.assertEqual(Config.from_dict(d).processing_charge_rate, .01)


class SensitivityVariantTest(unittest.TestCase):
    ARMS = ('control-hierarchy', 'dams-witness', 'hierarchy-auto-coord', 'dams-auto-coord', 'hierarchy-isa-coord', 'dams-isa-central-coord',
            'dams-isa-coord', 'dams-isa-coord-x50')

    def build(self, variant, arm):
        d, branch = r11_design.base_config(scale=60, context='multinational', growth='organic', world=1, seed=2, pre_years=1, post_years=1, variant=variant)
        return Config.from_dict(d), Config.from_dict(r11_design.arm_config(d, branch, arm, variant)), branch

    def test_sensitivity_variants_differ_from_full_in_one_setting(self):
        base, full_arm, branch = self.build('full', 'dams-isa-coord')
        c, a, _ = self.build('full-collude05', 'dams-isa-coord'); self.assertEqual((c.attack, c.attack_cohort_fraction), ('collude', .05)); self.assertEqual(a.automation, full_arm.automation)
        c, a, _ = self.build('full-collude20', 'dams-isa-coord'); self.assertEqual((c.attack, c.attack_cohort_fraction, c.attack_start_day), ('collude', .2, branch))
        c, a, _ = self.build('full-g20', 'dams-isa-coord'); self.assertEqual((c.decision_effect.gamma, base.decision_effect.gamma), (.2, .05)); self.assertEqual(a.automation, full_arm.automation)
        for arm in ('dams-isa-coord', 'dams-witness'):
            c, a, _ = self.build('full-charge10', arm)
            self.assertEqual(c.processing_charge_rate, .1)
            if a.automation is not None:self.assertEqual((a.automation.charge_processing, a.automation.resource_units_per_processing_unit), (True, .1))
        c, a, _ = self.build('full-classes-503515', 'dams-isa-coord')
        self.assertEqual((a.automation.class_share_objective, a.automation.class_share_inferential, a.automation.class_share_disputed), (.5, .35, .15))
        self.assertEqual((full_arm.automation.class_share_objective, full_arm.automation.class_share_inferential, full_arm.automation.class_share_disputed), (.7, .25, .05))
        self.assertEqual(A.record_class(a.automation, .49), 'objective'); self.assertEqual(A.record_class(a.automation, .5), 'inferential')
        self.assertEqual(A.record_class(a.automation, .849), 'inferential'); self.assertEqual(A.record_class(a.automation, .85), 'disputed')

    def test_coordination_share_variants_change_only_arms_that_automate_coordination(self):
        for variant, share in (('full-coord25', .25), ('full-coord75', .75)):
            for arm in ('hierarchy-auto-coord', 'dams-auto-coord', 'hierarchy-isa-coord', 'dams-isa-central-coord', 'dams-isa-coord', 'dams-isa-coord-x50'):
                self.assertEqual(self.build(variant, arm)[1].automation.coordination_automation, share, (variant, arm))
            for arm in ('dams-auto', 'dams-isa', 'dams-isa-full', 'hierarchy-auto'):
                self.assertEqual(self.build(variant, arm)[1].automation.coordination_automation, 0., (variant, arm))
            for arm in ('control-hierarchy', 'dams-witness'):
                self.assertIsNone(self.build(variant, arm)[1].automation)
        # all other settings of the arm equal those under `full`
        for arm in ('dams-isa-coord', 'hierarchy-auto-coord'):
            a, b = self.build('full', arm)[1].automation, self.build('full-coord25', arm)[1].automation
            self.assertEqual(dataclasses.replace(b, coordination_automation=.5), a)

    def test_formal_arms_under_full_all_charge_at_the_same_rate(self):
        for arm in self.ARMS:
            c = self.build('full', arm)[1]
            rate = c.automation.resource_units_per_processing_unit if c.automation is not None and c.automation.charge_processing else c.processing_charge_rate
            self.assertEqual(rate, .01, arm)
        # earlier variants: no base-level rate, so an arm without automation does not pay
        c = self.build('accounted-work-decisions-charged', 'dams-witness')[1]
        self.assertIsNone(c.processing_charge_rate); self.assertIsNone(c.automation)


class EngineTest(unittest.TestCase):
    """Validated runs at scale 30: every arm passes the raw validators in r11_study.arm."""
    VARIANTS = ('full', 'full-charge10', 'accounted-work-decisions-charged')

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

    # ---- F3: the charge is symmetric ----
    def test_an_arm_without_automation_pays_for_its_witness_verification_units(self):
        out, s = self.run_arm('dams-witness', 'full')
        self.assertGreater(s['verification_resource_units'], 0)
        self.assertNotIn('automated_processing_units', s)
        self.assertAlmostEqual(s['processing_charge_resource_units'], .01*s['verification_resource_units'], delta=1e-9*s['verification_resource_units'])
        charges = [v for _d, k, _i, v in journal(out) if k == 'processing_charge']
        self.assertEqual(len(charges), 366)
        self.assertTrue(all(v['rate'] == .01 for v in charges))
        self.assertAlmostEqual(math.fsum(v['charged'] for v in charges), s['processing_charge_resource_units'], places=9)
        self.assertEqual(s['operating_unfunded_resource_units'], 0)

    def test_measures_report_the_charged_units_of_an_arm_without_automation(self):
        from research_tools import r11_isa_measures
        out, s = self.run_arm('dams-witness', 'full')
        m = r11_isa_measures.measures(out)[0]['governance_cost']
        self.assertAlmostEqual(m['processing_units_charged'], s['verification_resource_units'], places=9)
        self.assertEqual(m['processing_charge_rate'], .01)
        self.assertAlmostEqual(m['processing_units_charged']*m['processing_charge_rate'], m['processing_charge_resource_units'], places=9)

    def test_the_same_record_costs_the_same_with_and_without_automation(self):
        ws = self.run_arm('dams-witness', 'full')[1]; au = self.run_arm('dams-auto', 'full')[1]
        for s in (ws, au):
            units = s.get('automated_processing_units', 0.)+s['verification_resource_units']+s.get('coordination_processing_units', 0.)
            self.assertAlmostEqual(s['processing_charge_resource_units'], .01*units, delta=1e-9*units)

    def test_a_central_record_without_automation_accumulates_and_pays_nothing(self):
        s = self.run_arm('control-hierarchy', 'full')[1]
        self.assertEqual(s.get('verification_resource_units', 0.), 0.)
        self.assertEqual(s['processing_charge_resource_units'], 0.)

    def test_rate_variant_scales_the_charge_of_an_arm_without_automation(self):
        low, high = self.run_arm('dams-witness', 'full')[1], self.run_arm('dams-witness', 'full-charge10')[1]
        self.assertAlmostEqual(high['processing_charge_resource_units'], .1*high['verification_resource_units'], delta=1e-9*high['verification_resource_units'])
        self.assertGreater(high['processing_charge_resource_units'], 5*low['processing_charge_resource_units'])

    def test_earlier_variants_leave_an_arm_without_automation_unchanged(self):
        s = self.run_arm('dams-witness', 'accounted-work-decisions-charged')[1]
        self.assertNotIn('processing_charge_resource_units', s)

    # ---- F2: the reporting delay of cross-boundary work ----
    def test_scoped_arm_with_full_cross_boundary_share_equals_the_unscoped_arm(self):
        spec = dict(r11_design.ARMS['dams-isa'], automation={'sandboxes': True, 'scoped_authority': True, 'cross_boundary_share': 1.})
        with mock.patch.dict(r11_design.ARMS, {'test-isa-x100': spec}):
            sc_out, _ = self.run_arm('test-isa-x100', 'full')
        un_out, _ = self.run_arm('dams-isa-unscoped', 'full')
        sc, un = journal(sc_out), journal(un_out)

        def by_day(rows, kind):
            out = {}
            for d, k, _i, v in rows:
                if k == kind:out.setdefault(d, []).append(v)
            return out
        plans_sc, plans_un = by_day(sc, 'organization_plan'), by_day(un, 'organization_plan')
        work_sc, work_un = by_day(sc, 'work'), by_day(un, 'work')
        compared = 0
        for day in sorted(set(plans_sc) & set(plans_un)):
            a, b = plans_sc[day][0], plans_un[day][0]
            if a['topology'] != b['topology'] or a['present_people'] != b['present_people'] or a['funding_fraction'] != b['funding_fraction']:continue
            depth = a['topology']['reporting_depth']
            scoped = [x for x in a['allocations'].values() if 'reporting_layers' in x]
            if not scoped:continue
            for person, x in a['allocations'].items():
                self.assertEqual(x['reporting_layers'], depth[person])                      # layers borne equal the full depth
                y = b['allocations'][person]
                self.assertEqual(x['requested_network_hours'], y['requested_network_hours'])    # reporting hours equal
            wsc = {w['person']: w['organization']['claim_ready_day'] for w in work_sc.get(day, [])}
            wun = {w['person']: w['organization']['claim_ready_day'] for w in work_un.get(day, [])}
            self.assertEqual(wsc, wun)                                                      # reporting delay equal
            for person, ready in wsc.items():self.assertEqual(ready, day+1+depth[str(person)])
            compared += len(wsc)
            if compared > 400:break
        self.assertGreater(compared, 100)
        coord_sc, coord_un = by_day(sc, 'enterprise_coordination_begin'), by_day(un, 'enterprise_coordination_begin')
        same = [day for day in sorted(set(coord_sc) & set(coord_un)) if plans_sc.get(day) and plans_un.get(day) and plans_sc[day][0]['topology'] == plans_un[day][0]['topology']
                and plans_sc[day][0]['present_people'] == plans_un[day][0]['present_people']]
        self.assertTrue(same)
        # coordination hours: on days with the same plan inputs, the requested workdays of every site are equal
        equal_inputs = 0
        for day in same:
            a, b = coord_sc[day][0], coord_un[day][0]
            if a['inputs'] != b['inputs']:continue
            equal_inputs += 1
            self.assertEqual([v['requested'] for v in a['plan']['sites']], [v['requested'] for v in b['plan']['sites']])
        self.assertGreater(equal_inputs, 0)

    def test_scoped_arms_delay_cross_boundary_reports_by_the_rounded_share_of_the_layers_above_the_scope(self):
        for arm, share in (('dams-isa', .25), ('dams-isa-x50', .5)):
            out, _ = self.run_arm(arm, 'full')
            rows = journal(out); c = Config.from_dict(json.loads((self.pre['full']/'prehistory.json').read_text())['config'])
            a = AutomationConfig(sandboxes=True, scoped_authority=True, cross_boundary_share=share)
            plans = {d: v for d, k, _i, v in rows if k == 'organization_plan'}
            seen = set()
            for d, k, _i, v in rows:
                if k != 'work' or d not in plans or 'reporting_layers' not in plans[d]['allocations'][str(v['person'])]:continue
                depth = plans[d]['topology']['reporting_depth'][str(v['person'])]
                self.assertEqual(v['organization']['claim_ready_day'], d+1+A.in_scope_delay_layers(a, depth))
                seen.add(depth)
            self.assertTrue(seen)
            self.assertTrue(all(A.in_scope_delay_layers(a, x) <= x for x in seen))

    # ---- F1: shadow evaluation costs disputed claims as a human lane ----
    def test_candidate_settings_are_not_cheaper_when_every_claim_is_disputed(self):
        auto = {'sandboxes': True, 'scoped_authority': True, 'class_share_objective': 0., 'class_share_inferential': 0., 'class_share_disputed': 1.}
        with mock.patch.dict(r11_design.ARMS, {'test-isa-disputed': dict(r11_design.ARMS['dams-isa'], automation=auto)}):
            out, s = self.run_arm('test-isa-disputed', 'full')
        rows = journal(out)
        autos = [v for _d, k, _i, v in rows if k == 'auto_verify']
        self.assertTrue(autos and all(v['route'] == 'disputed' for v in autos))         # every claim is a human lane
        decided = [(k, v) for _d, k, _i, v in rows if k in ('selfmod_adopt', 'selfmod_rollback') and 'shadow_health' in v]
        self.assertTrue(decided)
        for _kind, e in decided:
            # every claim goes to a human whatever the candidate fraction, so live and candidate cost the same
            self.assertAlmostEqual(e['shadow_health'], e['live_health'], places=12)
        self.assertEqual({kind for kind, _e in decided}, {'selfmod_adopt'})      # equal health is not a degradation


if __name__ == '__main__':
    unittest.main()
