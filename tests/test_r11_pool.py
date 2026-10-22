"""Pooling admitted batch reports of one study equals inference on the union."""
import unittest

from research_tools.r11_inference import _difference_summary
from research_tools.r11_pool import pool
from research_tools.r11_world_count import PRIMARY


def report(worlds, offset, commit='c'*40, args='x', errors=()):
    rows = {}
    for k in PRIMARY:
        vals = [[w, 10.+w%7+offset, 10.] for w in worlds]
        rows[k] = {'world_values': vals, 'assigned_worlds': len(worlds), 'missing_worlds': []}
    return {'schema': 'r11-roster-and-raw-inference-v2', 'admission_errors': list(errors), 'reference': 'control-hierarchy',
            'study_identity': {'commit': commit, 'args_without_worlds': args},
            'comparisons': {'dams-witness': rows}}


class PoolTest(unittest.TestCase):
    def test_pool_equals_union(self):
        a, b = report(range(1000, 1004), 0.), report(range(1004, 1009), 0.)
        out = pool([a, b])['comparisons']['dams-witness']
        diffs = [10.+w%7-10. for w in range(1000, 1009)]
        ref = _difference_summary(diffs, PRIMARY[0], None)
        self.assertEqual(out[PRIMARY[0]]['worlds'], 9)
        self.assertAlmostEqual(out[PRIMARY[0]]['mean_difference'], ref['mean_difference'])
        self.assertAlmostEqual(out[PRIMARY[0]]['ci95'][0], ref['ci95'][0])
        self.assertEqual(out[PRIMARY[0]]['holm_family_size'], len(PRIMARY))

    def test_refuses_unadmitted_batch(self):
        with self.assertRaises(ValueError):
            pool([report(range(1000, 1003), 0.), report(range(1003, 1006), 0., errors=['x'])])

    def test_refuses_duplicate_world(self):
        with self.assertRaises(ValueError):
            pool([report(range(1000, 1003), 0.), report(range(1002, 1005), 0.)])

    def test_refuses_different_study(self):
        with self.assertRaises(ValueError):
            pool([report(range(1000, 1003), 0.), report(range(1003, 1006), 0., commit='d'*40)])


if __name__ == '__main__':
    unittest.main()
