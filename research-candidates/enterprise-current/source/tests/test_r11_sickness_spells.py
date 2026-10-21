import os
import tempfile
import unittest
from pathlib import Path

from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.person_context import PersonContextState, spell_absent
from research_tools.r11_design import SICK_AGE_PROFILE, SICK_SPELL_BINS, apply_variant, base_config


class _Rng:
    def __init__(self, values):
        self.values = values

    def uniform(self, stream, day, token):
        return self.values.get((stream, day), .99)


class SpellRuleTest(unittest.TestCase):
    def config(self):
        d, b = base_config(scale=12, context='startup', growth='organic', world=0, seed=3, pre_years=0, post_years=1)
        return Config.from_dict(apply_variant(d, b, 'sickness-spells')).person_context

    def test_spell_covers_its_days_and_blocks_new_onset(self):
        c, state = self.config(), {}
        rng = _Rng({('pc_sick', 10): 0., ('pc_spell_bin', 10): 0., ('pc_spell_len', 10): .99})
        self.assertTrue(spell_absent(c, state, 't', 10, 250, 30., 0., rng))
        length = state['t']-10
        self.assertTrue(1 <= length <= 3)  # first band 1-3 days, age 30 duration multiplier < 1
        for day in range(10, state['t']):
            self.assertTrue(spell_absent(c, state, 't', day, 250, 30., 0., _Rng({})))
        self.assertFalse(spell_absent(c, state, 't', state['t'], 250, 30., 0., _Rng({})))

    def test_age_profile_and_bins_are_declared(self):
        self.assertAlmostEqual(sum(r[2] for r in SICK_SPELL_BINS), 1.)
        self.assertEqual([r[0] for r in SICK_AGE_PROFILE], list(range(15, 65, 5)))
        # Older workers: fewer onsets than the youngest, much longer spells.
        self.assertLess(SICK_AGE_PROFILE[-1][1], SICK_AGE_PROFILE[0][1])
        self.assertGreater(SICK_AGE_PROFILE[-1][2], 4*SICK_AGE_PROFILE[0][2])

    def test_spell_state_survives_checkpoint(self):
        d, b = base_config(scale=12, context='startup', growth='organic', world=0, seed=3, pre_years=0, post_years=1)
        config = Config.from_dict(apply_variant(d, b, 'sickness-spells'))
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            m = Model(config, storage_dir=Path(tmp)/'a')
            m.run(120)
            state = m._long.pc.to_dict()
            self.assertIn('sick_until', state)
            restored = PersonContextState.restore(config, state)
            self.assertEqual(restored.sick_until, m._long.pc.sick_until)
            m._long.ledger.close()


if __name__ == '__main__':
    unittest.main()
