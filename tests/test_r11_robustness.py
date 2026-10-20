import unittest

from research_tools.r11_outcomes import OUTCOMES
from research_tools.r11_robustness import CRITERION, compare, interval


def rows(contrasts, level=100.):
    """world id -> arm/reference outcome rows with the given arm-minus-reference contrast."""
    return {w: {'reference': {k: level for k in OUTCOMES}, 'arm': {k: level+c for k in OUTCOMES}}
            for w, c in contrasts.items()}


class RobustnessTest(unittest.TestCase):
    def test_interval_matches_hand_computation(self):
        r = interval([1., 2., 3.])
        self.assertEqual((r['worlds'], r['mean']), (3, 2.))
        # t(0.975, 2) = 4.3027; sd 1; se 1/sqrt(3)
        self.assertAlmostEqual(r['ci95'][1], 2+4.3027/3**.5, places=3)
        self.assertIsNone(interval([1.]))

    def test_change_is_paired_on_common_worlds_only(self):
        base = rows({1000: -10., 1001: -12., 1002: -11.})
        var = rows({1000: -8., 1001: -9., 1003: -50.})  # 1003 has no baseline twin
        out = compare(base, var)
        ch = out['outcomes'][CRITERION]['change_from_baseline']
        self.assertEqual((out['worlds_common'], ch['worlds']), (2, 2))
        self.assertEqual(ch['mean'], ((-8+10)+(-9+12))/2)
        self.assertEqual(out['outcomes'][CRITERION]['variant_contrast']['worlds'], 3)

    def test_robust_requires_same_sign_and_interval_excluding_zero(self):
        base = rows({w: -10.-w % 3 for w in range(1000, 1010)})
        self.assertTrue(compare(base, rows({w: -5.-w % 2 for w in range(1000, 1010)}))['robust'])
        self.assertFalse(compare(base, rows({w: 5.+w % 2 for w in range(1000, 1010)}))['robust'])
        self.assertFalse(compare(base, rows({w: (-1.)**w for w in range(1000, 1010)}))['robust'])


if __name__ == '__main__':
    unittest.main()
