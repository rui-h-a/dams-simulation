"""Regression: the organization verifier must add its daily quantities as the model does.

Adding fifteen million per-person values one at a time drifts from the model's daily exact sums by more than the
verifier's relative tolerance of 1e-11; this stopped eleven 10,000-member arms in their fifth year.  The planned network,
quality and learning hours were added person by person within each day (relative difference about 1.8e-13 per day at
10,000 members for the network hours); they now follow the model's exact daily sum as well.
"""
import math
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dams_sim.config import Config
from dams_sim.model import Model
from research_tools import validate_organization_tasks as organization_verifier
from research_tools.r11_design import base_config
from research_tools.validate_organization_tasks import daily_administrative_hours, daily_planned_hours, _close


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

    def test_planned_hours_follow_the_model_expression_and_person_by_person_sum_differs(self):
        rng = random.Random(20261117)
        model = verifier = naive = 0.0
        differing_days = 0
        for day in range(400):
            funding = 1.0 if day % 3 else rng.uniform(0.8, 1.0)
            hours = {i: round(rng.choice((0.0975, 0.09, 0.1175, 0.075)) + rng.uniform(0.0, 0.01), 4) for i in range(9000)}
            # the model's expression (dams_sim/longitudinal_model.py, organization planning): fsum of the day's values times funding
            model += math.fsum(hours.values()) * funding
            verifier += daily_planned_hours(list(hours.values()), funding)
            today = 0.0
            for value in hours.values():
                today += value * funding
            differing_days += today != math.fsum(hours.values()) * funding
            naive += today
        self.assertEqual(model, verifier)
        _close(verifier, model, 'organization network hours')
        self.assertGreater(differing_days, 0)
        self.assertNotEqual(naive, model)


class VerifierCumulativeAgreesBitForBit(unittest.TestCase):
    def test_organization_hour_totals_equal_the_models_exactly(self):
        # Small enough to run in seconds; the three planned-hour totals and the administrative total are compared
        # with the model's cumulative metrics day by day and must be bit-identical, not merely within tolerance.
        d, _branch = base_config(scale=200, context='multinational', growth='organic', world=1000, seed=20261017,
                                 pre_years=2, post_years=5, variant='full')
        config = Config.from_dict(d)
        pairs = {}
        real = organization_verifier._close

        def record(a, b, label):
            if label in ('organization_network_hours', 'organization_quality_hours', 'organization_learning_hours',
                         'administrative_work_hours'):
                pairs.setdefault(label, []).append((a, b))
            return real(a, b, label)

        root = Path(__file__).resolve().parent.parent / 'candidate-evidence/test-tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            model = Model(config, storage_dir=Path(tmp) / 'work')
            try:
                model.run(40)
                with mock.patch.object(organization_verifier, '_close', record):
                    model._long.validate_semantics()
            finally:
                model._long.ledger.close()
        for label in ('organization_network_hours', 'organization_quality_hours', 'organization_learning_hours',
                      'administrative_work_hours'):
            self.assertEqual(len(pairs.get(label, ())), 40, label)
            for day, (recorded, recomputed) in enumerate(pairs[label]):
                self.assertEqual(recorded, recomputed, f'{label} day {day}')
            self.assertGreater(pairs[label][-1][0], 0.0, label)


if __name__ == '__main__':
    unittest.main()
