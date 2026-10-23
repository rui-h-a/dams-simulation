import unittest

from research_tools.r11_design import VARIANTS, base_config

FIELDS = ('decision_effect', 'decision_execution_delay_days', 'decision_execution_hours', 'decision_effect_days')


def config(variant):
    d, _ = base_config(scale=300, context='startup', growth='organic', world=2000, seed=20261017,
                       pre_years=0, post_years=1, variant=variant)
    return d


class DecisionVariantTest(unittest.TestCase):
    def test_each_variant_sets_the_four_fields(self):
        for name, effect in (('decision-effect-0.05', .05), ('decision-effect-0.1', .1), ('decision-effect-0.2', .2)):
            d = config(name)
            self.assertEqual([d[k] for k in FIELDS], [effect, 5, .01, 20])

    def test_only_those_fields_differ_from_baseline(self):
        base = config('baseline')
        for k in FIELDS:
            self.assertEqual(base.get(k, {'decision_effect': 0., 'decision_execution_delay_days': 0,
                                          'decision_execution_hours': 0., 'decision_effect_days': 20}[k]),
                             {'decision_effect': 0., 'decision_execution_delay_days': 0,
                              'decision_execution_hours': 0., 'decision_effect_days': 20}[k])
        d = config('decision-effect-0.1')
        for k in FIELDS:
            d.pop(k, None); base.pop(k, None)
        self.assertEqual(d, base)

    def test_null_is_not_a_variant(self):
        self.assertNotIn('decision-effect-0', VARIANTS)
        with self.assertRaises(ValueError):
            config('decision-effect-0')


if __name__ == '__main__':
    unittest.main()
