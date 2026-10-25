"""Exploratory engine-sensitivity parameters: defaults keep identity, variants change only their mechanism."""
import dataclasses
import hashlib
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.storage import canonical
from research_tools.r11_design import base_config

DAYS = 40
FIELDS = {'skill-dispersion-low': ('skill_log_sd', .1883), 'skill-dispersion-high': ('skill_log_sd', .4553),
          'status-bias': ('status_evaluation_bias', .05),
          'tenure-quit-profile': ('tenure_quit_profile', [[1, 2.0], [3, 1.0], [100, 0.6]])}


def world(variant='baseline', **overrides):
    d, _ = base_config(scale=60, context='startup', growth='organic', world=3, seed=20261025, pre_years=0, post_years=1,
                       variant=variant)
    d.update(overrides)
    return d


def run(d, days=DAYS):
    tmp = Path(tempfile.mkdtemp(prefix='eng-var-'))
    try:
        m = Model(Config.from_dict(d), storage_dir=tmp/'work')
        for day in range(1, days+1):
            m.run(day)
        L = m._long
        h = hashlib.sha256(canonical({k: v for k, v in L.header().items() if k != 'execution_source_sha256'}))
        for a in L.agents:
            h.update(canonical(dataclasses.asdict(a)))
        h.update(L.ledger.semantic_digest().encode())
        result = dict(digest=h.hexdigest(), skills=[a.skill for a in L.agents], metrics=dict(L.metrics))
        L.ledger.close()
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class EngineVariantTest(unittest.TestCase):
    def test_defaults_are_omitted_from_the_config_identity(self):
        d = Config.from_dict(world()).to_dict()
        for k in ('skill_log_sd', 'status_evaluation_bias', 'tenure_quit_profile'):
            self.assertNotIn(k, d)

    def test_each_variant_sets_only_its_field(self):
        base = world()
        for name, (k, v) in FIELDS.items():
            d = world(name)
            self.assertEqual(d[k], v)
            d.pop(k)
            self.assertEqual(d, base)
            c = Config.from_dict(world(name))
            self.assertEqual(Config.from_dict(c.to_dict()), c)

    def test_explicit_defaults_reproduce_the_frozen_run(self):
        base = run(world())
        explicit = run(world(skill_log_sd=.4, status_evaluation_bias=0., tenure_quit_profile=[]))
        self.assertEqual(base['digest'], explicit['digest'])

    def test_skill_dispersion_scales_log_skill_only(self):
        base, low = run(world()), run(world('skill-dispersion-low'))
        n = 60  # initial members draw the same skill token in both runs
        ratios = {round(math.log(b)/math.log(s), 6) for b, s in zip(base['skills'][:n], low['skills'][:n]) if abs(math.log(s)) > 1e-9}
        self.assertEqual(ratios, {round(.4/.1883, 6)})

    def test_status_bias_and_tenure_profile_change_the_run(self):
        base = run(world())['digest']
        for name in ('status-bias', 'tenure-quit-profile'):
            self.assertNotEqual(run(world(name))['digest'], base, name)

    def test_tenure_profile_validation(self):
        for bad in ([[3, 1.0], [1, 2.0]], [[1, -1.0]], [[1, 2.0, 3.0]]):
            with self.assertRaises(ValueError):
                Config.from_dict(world(tenure_quit_profile=bad))


if __name__ == '__main__':
    unittest.main()
