"""Configuration of the small-batch sensitivity variants and arms (span of control, scope rule, fault, saturation)."""
import dataclasses
import unittest

from dams_sim.config import Config
from research_tools import r11_design


def _base(variant, scale=60, pre=1, post=1):
    return r11_design.base_config(scale=scale, context='multinational', growth='organic', world=1, seed=20261017,
                                  pre_years=pre, post_years=post, variant=variant)


class SpanVariantTest(unittest.TestCase):
    def test_span_variants_change_only_the_span_of_both_sections(self):
        full, _ = _base('full')
        self.assertEqual((full['enterprise_operations']['management_span'], full['organization_tasks']['management_span']), (8, 8))
        for name, span in (('full-span4', 4), ('full-span16', 16)):
            d, _ = _base(name)
            self.assertEqual((d['enterprise_operations']['management_span'], d['organization_tasks']['management_span']), (span, span), name)
            for section in ('enterprise_operations', 'organization_tasks'):
                a = dict(d[section]); b = dict(full[section]); a.pop('management_span'); b.pop('management_span')
                self.assertEqual(a, b, name)
            rest = lambda x: {k: v for k, v in x.items() if k not in ('enterprise_operations', 'organization_tasks')}
            self.assertEqual(rest(d), rest(full), name)
            self.assertEqual(Config.from_dict(d).administrative_fatigue_reference, .14)

    def test_span_variants_keep_the_full_automation_settings_on_arms(self):
        for name in ('full-span4', 'full-span16'):
            d, branch = _base(name)
            au = Config.from_dict(r11_design.arm_config(d, branch, 'dams-isa-coord', name)).automation
            self.assertEqual((au.record_classes, au.charge_processing, au.coordination_automation), (True, True, .5))

    def test_existing_variants_are_unchanged(self):
        self.assertEqual(r11_design.VARIANTS['accounted-work-span4']['enterprise_operations.management_span'], 4)
        self.assertEqual(r11_design.VARIANTS['accounted-work-span16']['organization_tasks.management_span'], 16)
        self.assertEqual(r11_design.VARIANTS['full'], dict(r11_design._FULL))


class CoordSensitivityArmTest(unittest.TestCase):
    def setUp(self):
        self.d, self.branch = _base('full')
        self.cfg = lambda arm: Config.from_dict(r11_design.arm_config(self.d, self.branch, arm, 'full'))
        self.coord = self.cfg('dams-isa-coord')

    def test_unscoped_arm_differs_from_dams_isa_coord_only_in_the_scope_rule(self):
        c = self.cfg('dams-isa-coord-unscoped'); au = c.automation
        self.assertEqual((c.regime, c.backend), ('sublinear', 'witness'))
        self.assertFalse(au.scoped_authority)
        self.assertTrue(au.sandboxes and au.project_sandboxes)
        self.assertEqual(dataclasses.replace(au, scoped_authority=True), self.coord.automation)

    def test_fault_arm_adds_the_outage_of_dams_isa_fault(self):
        c = self.cfg('dams-isa-coord-fault'); au = c.automation
        old = self.cfg('dams-isa-fault').automation
        self.assertEqual((au.fault_mode, au.fault_sandbox, au.fault_days, au.fault_start_day),
                         (old.fault_mode, old.fault_sandbox, old.fault_days, self.branch+120))
        self.assertEqual(au.fault_start_day, old.fault_start_day)
        self.assertEqual(dataclasses.replace(au, fault_mode='none', fault_sandbox=self.coord.automation.fault_sandbox,
                                             fault_days=self.coord.automation.fault_days, fault_start_day=self.coord.automation.fault_start_day),
                         self.coord.automation)

    def test_saturated_arm_caps_the_quota_like_dams_isa_saturated(self):
        au = self.cfg('dams-isa-coord-saturated').automation
        self.assertEqual(au.quota_units_per_member_day, self.cfg('dams-isa-saturated').automation.quota_units_per_member_day)
        self.assertEqual(au.quota_units_per_member_day, .6)
        self.assertEqual(dataclasses.replace(au, quota_units_per_member_day=self.coord.automation.quota_units_per_member_day), self.coord.automation)

    def test_dams_isa_coord_and_older_arms_are_unchanged(self):
        self.assertEqual(r11_design.ARMS['dams-isa-coord']['automation'],
                         {'sandboxes': True, 'scoped_authority': True, 'project_sandboxes': True, 'coordination_automation': 0.5})
        self.assertEqual(r11_design.ARMS['dams-isa-fault']['automation']['fault_days'], 14)
        self.assertEqual(r11_design.ARMS['dams-isa-saturated']['automation']['quota_units_per_member_day'], 0.6)
        self.assertEqual(r11_design.ARMS['dams-isa-unscoped']['automation'], {'sandboxes': True})


class FoundingAdoptionTest(unittest.TestCase):
    def test_zero_pre_years_puts_the_adoption_boundary_on_day_zero(self):
        d, branch = _base('full', pre=0, post=1)
        self.assertEqual(branch, 0)
        a = r11_design.arm_config(d, branch, 'dams-isa-coord', 'full')
        self.assertEqual((a['longitudinal']['adoption_mode'], a['longitudinal']['adoption_day']), ('fixed', 0))
        Config.from_dict(a)


if __name__ == '__main__':
    unittest.main()
