"""Optional decision-execution path: null test, clairvoyant/inverted bounds and validation."""
import dataclasses
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from dams_sim.config import Config
from dams_sim.longitudinal_model import LongitudinalEngine
from dams_sim.model import Model
from dams_sim.storage import canonical
from research_tools.r11_design import base_config

DAYS = 40


def world(**overrides):
    d, _ = base_config(scale=300, context='startup', growth='organic', world=3, seed=20261023, pre_years=0, post_years=1)
    d.update(overrides)
    return d


def run(d, force=None, days=DAYS):
    tmp = Path(tempfile.mkdtemp(prefix='dec-exec-'))
    LongitudinalEngine.decision_force_correct = force
    try:
        m = Model(Config.from_dict(d), storage_dir=tmp/'work')
        for day in range(1, days+1):
            m.run(day)
        L = m._long
        h = hashlib.sha256(canonical({k: v for k, v in L.header().items() if k != 'execution_source_sha256'}))
        for a in L.agents:
            h.update(canonical(dataclasses.asdict(a)))
        h.update(L.ledger.semantic_digest().encode())
        out = L.metrics['produced_work_units']
        result = dict(digest=h.hexdigest(), output=out, metrics=dict(L.metrics))
        L.ledger.close()
        return result
    finally:
        LongitudinalEngine.decision_force_correct = None
        shutil.rmtree(tmp, ignore_errors=True)


class NullTest(unittest.TestCase):
    def test_defaults_are_zero_and_omitted_from_the_config_identity(self):
        c = Config()
        self.assertEqual((c.decision_effect, c.decision_execution_delay_days, c.decision_execution_hours), (0.0, 0, 0.0))
        d = Config.from_dict(world()).to_dict()
        for k in ('decision_effect', 'decision_execution_delay_days', 'decision_execution_hours', 'decision_effect_days'):
            self.assertNotIn(k, d)

    def test_explicit_zero_effect_equals_run_without_the_fields(self):
        base = run(world())
        explicit = run(world(decision_effect=0.0, decision_execution_delay_days=0, decision_execution_hours=0.0, decision_effect_days=20))
        self.assertEqual(base['digest'], explicit['digest'])
        self.assertNotIn('decisions_executed', base['metrics'])
        self.assertNotIn('decision_execution_hours', base['metrics'])


class ExecutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kw = dict(decision_effect=0.05, decision_execution_delay_days=2, decision_execution_hours=0.5, decision_effect_days=20)
        cls.null = run(world())
        cls.actual = run(world(**kw))
        cls.right = run(world(**kw), force=True)
        cls.wrong = run(world(**kw), force=False)

    def test_effect_changes_output_and_charges_execution_hours(self):
        self.assertNotEqual(self.actual['digest'], self.null['digest'])
        self.assertGreater(self.actual['metrics']['decisions_executed'], 0)
        self.assertGreater(self.actual['metrics']['decision_execution_hours'], 0)
        self.assertEqual(self.actual['metrics']['governance_hours'] >= 0, True)

    def test_clairvoyant_bound_brackets_actual_and_inverted(self):
        self.assertGreaterEqual(self.right['output'], self.actual['output'])
        self.assertGreaterEqual(self.actual['output'], self.wrong['output'])
        self.assertGreater(self.right['output'], self.wrong['output'])

    def test_pending_and_live_effects_survive_state_round_trip(self):
        tmp = Path(tempfile.mkdtemp(prefix='dec-exec-'))
        try:
            d = world(decision_effect=0.05, decision_execution_delay_days=5, decision_execution_hours=0.5)
            m = Model(Config.from_dict(d), storage_dir=tmp/'work')
            for day in range(1, 15):
                m.run(day)
            L = m._long
            self.assertTrue(L.decision_pending and L.decision_effects)
            copy = LongitudinalEngine.restore(L.state(), storage_dir=tmp/'copy')
            self.assertEqual(copy.decision_pending, L.decision_pending)
            self.assertEqual(copy.decision_effects, L.decision_effects)
            self.assertEqual(copy.semantic_digest(), L.semantic_digest())
            copy.ledger.close()
            L.ledger.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ValidationTest(unittest.TestCase):
    def test_negative_or_malformed_values_are_rejected(self):
        for k, v in (('decision_effect', -0.1), ('decision_execution_delay_days', -1), ('decision_execution_hours', -1.0),
                     ('decision_effect_days', 0), ('decision_effect_days', -3), ('decision_effect', float('nan')), ('decision_effect', 1.5),
                     ('decision_execution_delay_days', 1.5)):
            with self.subTest(k=k, v=v):
                kw = dict(decision_effect=0.05)
                kw[k] = v
                with self.assertRaises(ValueError):
                    Config.from_dict(world(**kw))

    def test_delay_and_hours_require_a_positive_effect(self):
        with self.assertRaises(ValueError):
            Config.from_dict(world(decision_execution_hours=0.5))

    def test_round_trip_keeps_enabled_fields(self):
        c = Config.from_dict(world(decision_effect=0.05, decision_effect_days=10))
        self.assertEqual(Config.from_dict(c.to_dict()).decision_effect_days, 10)


if __name__ == '__main__':
    unittest.main()
