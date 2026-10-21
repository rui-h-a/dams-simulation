import unittest

from dams_sim.config import Config
from research_tools.r11_design import MA_INTEGRATION, apply_variant, base_config


def acquirer(**kw):
    args = dict(scale=40, context='listed', growth='organic+acquisition', world=0, seed=7, pre_years=1, post_years=2)
    args.update(kw)
    return base_config(**args)


class SimulatedTargetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.branch = acquirer()
        cls.target = cls.d['enterprise_acquisition']['targets'][0]

    def test_config_validates_in_both_integration_modes(self):
        Config.from_dict(self.d)
        c = Config.from_dict(apply_variant(self.d, self.branch, 'ma-centralize'))
        self.assertEqual(c.enterprise_acquisition.integration_mode, 'centralize')

    def test_roster_and_money_come_from_the_simulated_firm(self):
        people = self.target['employees']
        self.assertGreaterEqual(len(people), 4)
        self.assertGreater(len({p['skill'] for p in people}), 1)
        self.assertGreater(len({p['birth_date'] for p in people}), 1)
        self.assertGreater(self.target['consideration_resource_units'], self.target['cash_resource_units'])
        self.assertNotIn('net_cash', self.target)
        self.assertEqual(self.target['available_day'], self.branch+365)

    def test_target_is_deterministic_and_depends_on_seed(self):
        again, _ = acquirer()
        self.assertEqual(again['enterprise_acquisition']['targets'], self.d['enterprise_acquisition']['targets'])
        other, _ = acquirer(seed=8)
        self.assertNotEqual(other['enterprise_acquisition']['targets'][0]['employees'], self.target['employees'])

    def test_integration_parameters_and_unsupported_mode(self):
        A = self.d['enterprise_acquisition']
        for key, value in MA_INTEGRATION.items():
            self.assertEqual(A[key], value)
        spent = A['integration_resource_units_per_employee_workday']*len(self.target['employees'])*A['integration_workdays']
        self.assertAlmostEqual(spent, .025*self.target['consideration_resource_units'])
        with self.assertRaises(ValueError):
            acquirer(growth='acquisition')


if __name__ == '__main__':
    unittest.main()
