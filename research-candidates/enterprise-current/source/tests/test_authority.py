import math
import random
import unittest
from dams_sim.authority import authority, cap_shares, gini, signed_concentration_gap, split_gain, tier_authority, total_variation


class AuthorityTests(unittest.TestCase):
    def test_independent_power_oracle(self):
        r = random.Random(219)
        for n in (2, 7, 120):
            for alpha in (0.5, 0.8, 1, 1.2):
                x = [r.randint(0, 1000) for _ in range(n)]
                oracle = [v**alpha/sum(w**alpha for w in x) for v in x]
                for got, expected in zip(authority(x, alpha, zero_policy="equal"), oracle):
                    self.assertAlmostEqual(got, expected, places=14)

    def test_independent_pairwise_gini(self):
        x = [0, 1, 2, 7, 8]
        oracle = sum(abs(a-b) for a in x for b in x)/(2*len(x)*sum(x))
        self.assertAlmostEqual(gini(x), oracle)

    def test_boundaries(self):
        self.assertEqual(authority([0, 4], 0.8, zero_policy="equal"), [0, 1])
        self.assertEqual(authority([0, 0], 0.8, zero_policy="equal"), [0.5, 0.5])
        for x in ([], [-1], [math.nan], [math.inf]):
            with self.assertRaises(ValueError):
                authority(x, 0.8, zero_policy="equal")
        with self.assertRaises(ValueError):
            authority([0, 0], 0.8, zero_policy="reject")
        with self.assertRaises(ValueError):
            authority([1, 2], 0.8, zero_policy="unwritten")
        self.assertEqual(authority([1e308, 1e-308, 0], 0.8, zero_policy="equal")[0], 1)

    def test_concentration_signed_and_population_invariant(self):
        x, y = [0.8, 0.2], [0.5, 0.5]
        tv = total_variation(x, y)
        self.assertAlmostEqual(tv, 0.3)
        self.assertAlmostEqual(total_variation([0.4, 0.4, 0.1, 0.1], [0.25]*4), tv)
        self.assertAlmostEqual(sum(abs(a-b) for a, b in zip(x, y))/len(x), 2*tv/len(x))
        self.assertLess(signed_concentration_gap(y, x), 0)
        self.assertGreater(signed_concentration_gap(x, y), 0)

    def test_ties_are_permutation_equivariant(self):
        self.assertEqual(tier_authority([1]*7, [1, 3, 7]), [1/7]*7)
        x = [1, 1, 2, 2, 3, 3]
        a = tier_authority(x, [1, 3, 7, 15])
        self.assertEqual(a[0], a[1])
        self.assertEqual(a[2], a[3])
        self.assertEqual(a[4], a[5])
        self.assertEqual(tier_authority(x[::-1], [1, 3, 7, 15]), a[::-1])
        with self.assertRaises(ValueError):
            tier_authority([1, 2], [3, 1])

    def test_cap_and_split_boundary(self):
        a = cap_shares([0.9, 0.05, 0.05], 0.4)
        self.assertAlmostEqual(sum(a), 1)
        self.assertLessEqual(max(a), 0.4)
        self.assertEqual(cap_shares([0, 0, 0], 0.4), [1/3]*3)
        with self.assertRaises(ValueError):
            cap_shares([1, 0], 0.4)
        self.assertGreater(split_gain([4, 8, 12], 0, 5, 0.8)["within_regime_gain"], 1)
        self.assertAlmostEqual(split_gain([4, 8, 12], 0, 5, 1)["within_regime_gain"], 1)


if __name__ == "__main__":
    unittest.main()
