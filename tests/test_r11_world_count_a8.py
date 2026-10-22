"""Amendment 8 world-count rule: separate-unit endpoints, anchored thresholds,
t at the confirmation's own degrees of freedom and a chi-square upper SD."""
import math
import unittest

from research_tools.r11_world_count import chi2_quantile, endpoint_requirement, required_worlds


class TestQuantiles(unittest.TestCase):
    def test_chi2_quantile_known_values(self):
        # chi2_{2;0.20} = -2 ln(0.8) exactly; chi2_{9;0.20} ~= 5.380 (tables)
        self.assertAlmostEqual(chi2_quantile(.2, 2), -2*math.log(.8), places=6)
        self.assertAlmostEqual(chi2_quantile(.2, 9), 5.380, places=2)

    def test_upper_sd_factor_for_three_world_pilot(self):
        self.assertAlmostEqual(math.sqrt(2/chi2_quantile(.2, 2)), 2.117, places=3)


class TestRequiredWorlds(unittest.TestCase):
    def test_uses_confirmation_degrees_of_freedom(self):
        # rho = s/delta = 4.284 with the point SD; the audit's worked value is 285
        self.assertEqual(required_worlds(4.284), 285)

    def test_minimum_ten(self):
        self.assertEqual(required_worlds(0.01), 10)

    def test_larger_ratio_needs_more_worlds(self):
        self.assertGreater(required_worlds(2.0), required_worlds(1.0))


class TestEndpoint(unittest.TestCase):
    def test_nonpositive_anchor_is_refused_not_dropped(self):
        r = endpoint_requirement([1., 2., 3.], anchor=-5., nu_p=2)
        self.assertEqual(r['status'], 'no positive anchor')
        self.assertIsNone(r['required_worlds_point'])

    def test_constant_differences_need_minimum(self):
        r = endpoint_requirement([.5, .5, .5], anchor=100., nu_p=2)
        self.assertEqual(r['sd_paired_difference'], 0.)
        self.assertEqual(r['required_worlds_point'], 10)

    def test_threshold_is_two_percent_of_anchor(self):
        r = endpoint_requirement([1., 2., 4.], anchor=50., nu_p=2)
        self.assertAlmostEqual(r['delta'], 1.)
        self.assertGreaterEqual(r['required_worlds_upper80'], r['required_worlds_point'])


if __name__ == '__main__':
    unittest.main()
