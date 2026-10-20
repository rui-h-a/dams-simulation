import unittest

from research_tools.r11_design import DEMAND_MATCH, base_config


def config(context, variant):
    d, _ = base_config(scale=300, context=context, growth='organic', world=1000, seed=20261017,
                       pre_years=0 if context == 'startup' else 2, post_years=5, variant=variant)
    return d


class DemandMatchedTest(unittest.TestCase):
    def test_only_base_orders_change(self):
        for context in ('startup', 'traditional'):
            base, var = config(context, 'baseline'), config(context, 'demand-matched')
            self.assertAlmostEqual(var['enterprise_operations']['base_orders_per_workday'],
                                   base['enterprise_operations']['base_orders_per_workday']*DEMAND_MATCH[context])
            var['enterprise_operations']['base_orders_per_workday'] = base['enterprise_operations']['base_orders_per_workday']
            self.assertEqual(var, base)

    def test_unmeasured_context_is_refused(self):
        with self.assertRaises(ValueError):
            config('multinational', 'demand-matched')


if __name__ == '__main__':
    unittest.main()
