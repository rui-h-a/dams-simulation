"""Regression: the organization verifier must sum administrative hours as the model does.

Adding fifteen million per-person values one at a time drifts from the model's daily exact sums by more than the
verifier's relative tolerance of 1e-11; this stopped eleven 10,000-member arms in their fifth year.
"""
import math
import random
import unittest

from research_tools.validate_organization_tasks import daily_administrative_hours, _close


class VerifierExactSum(unittest.TestCase):
    def test_daily_exact_sum_matches_model_and_naive_sum_can_fail(self):
        rng = random.Random(20261116)
        days = [{str(i): round(rng.uniform(0.0, 0.3), 6) for i in range(10000)} for _ in range(300)]
        model = 0.0
        verifier = 0.0
        naive = 0.0
        for day in days:
            model += math.fsum(day.values())
            verifier += daily_administrative_hours(day)
            for h in day.values():
                naive += h
        self.assertEqual(model, verifier)
        _close(verifier, model, 'administrative work hours')
        self.assertNotEqual(naive, model)


if __name__ == '__main__':
    unittest.main()
