"""Statistics used by research_tools/r11_inference.py against published constants."""
import unittest

from research_tools.r11_inference import holm, t_quantile, t_two_sided_p


class InferenceStatisticsTest(unittest.TestCase):
    def test_student_t_quantiles(self):
        for df, q in [(2, 4.302653), (9, 2.262157), (56, 2.003241), (299, 1.967929)]:
            self.assertAlmostEqual(t_quantile(.975, df), q, places=5)

    def test_two_sided_p_and_holm(self):
        self.assertAlmostEqual(t_two_sided_p(2.262157, 9), .05, places=6)
        self.assertAlmostEqual(t_two_sided_p(1.0, 30), .325309, places=5)
        self.assertEqual(holm([.01, .04, .03]), [.03, .06, .06])



class EvidenceAdmissionTest(unittest.TestCase):
    """Small I/O and statistical counterexamples; no model days are executed."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        import shutil
        # Fixtures are kilobytes, but still respect the research workspace floor.
        self.assertGreater(shutil.disk_usage(tempfile.gettempdir()).free, 2*1024**3)
        self.tmp = tempfile.TemporaryDirectory(prefix='r11-inference-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def plan(self, worlds=(0, 1000, 1001, 1002)):
        import json
        from dams_sim.storage import source_hash
        self.data = dict(scale=8, context='startup', growth='organic', pre_years=0,
                         post_years=1, seed=20261017, worlds=list(worlds),
                         arms=['control-hierarchy', 'dams-witness'],
                         source_sha256=source_hash(), scientific_dependencies={'test': 'bound'},
                         retention_policy='full-original-journal-v1')
        (self.root/'plan.json').write_text(json.dumps(self.data))
        return self.data

    def receipt(self, world, arm):
        directory = self.root/f'n8-startup-organic-pre0-post1-w{world}'/arm
        directory.mkdir(parents=True, exist_ok=True)
        (directory/'arm.json').write_text('{}')
        return directory

    def test_assigned_missing_worlds_survive_and_pilots_unassigned_do_not_count(self):
        from unittest.mock import patch
        from research_tools.r11_inference import infer
        from research_tools.r11_outcomes import OUTCOMES
        self.plan()
        for w in (0, 1000, 1002, 9999):
            self.receipt(w, 'dams-witness')
        self.receipt(1000, 'control-hierarchy')
        (self.root/'failure-attempt-0.json').write_text('{}')
        with patch('research_tools.r11_retention.science_manifest', return_value={'test': 'bound'}), \
             patch('research_tools.r11_inference._verify_prehistory', return_value=({}, {'files': {}, 'receipt_sha256': 'hash', 'origin': {}})), \
             patch('research_tools.r11_inference._verified_outcomes', return_value=({k: 0. for k in OUTCOMES}, {})) as checker:
            report = infer(self.root)
        self.assertEqual(report['assigned_worlds'], [1000, 1001, 1002])
        self.assertEqual(report['excluded_before_first_world'], [0])
        self.assertEqual(checker.call_count, 3)
        self.assertEqual(len(report['world_inventory']), 3)
        row = report['comparisons']['dams-witness']['closed']
        self.assertEqual(row['worlds'], 1)
        self.assertEqual([w['world'] for w in row['missing_worlds']], [1001, 1002])
        self.assertTrue(row['conditional_on_completion'])
        self.assertIn('failure-attempt-0.json', report['failures'])
        self.assertFalse(report['complete_preservation_accepted'])

    def test_unbound_plan_lists_original_roster_without_inference(self):
        import json
        from unittest.mock import patch
        from research_tools.r11_inference import infer
        self.plan(); del self.data['scientific_dependencies']
        (self.root/'plan.json').write_text(json.dumps(self.data))
        self.receipt(1000, 'control-hierarchy')
        with patch('research_tools.r11_retention.science_manifest', return_value={'test': 'bound'}), \
             patch('research_tools.r11_inference._verified_outcomes') as checker:
            report = infer(self.root)
        checker.assert_not_called()
        self.assertTrue(report['admission_errors'])
        self.assertEqual(report['comparisons'], {})
        self.assertEqual(len(report['world_inventory']), 3)
        self.assertNotIn('scientific_dependencies', json.loads((self.root/'plan.json').read_text()))

    def test_duplicate_world_identity_refuses_roster(self):
        from research_tools.r11_inference import _planned_roster
        self.plan(worlds=(1000, 1000))
        with self.assertRaisesRegex(ValueError, 'unique'):
            _planned_roster(self.root, 1000, 'control-hierarchy')

    def test_changed_summary_refused_before_outcome_read(self):
        import json
        from unittest.mock import patch
        from dams_sim.storage import file_digest
        from research_tools.r11_design import ARMS
        from research_tools.r11_inference import _verified_outcomes
        self.plan()
        directory = self.receipt(1000, 'dams-witness')
        for name in ('final_state.json', 'timeseries.csv', 'summary.json'):
            (directory/name).write_text('{}')
        receipt = dict(kind='r11-arm', arm='dams-witness', arm_spec=ARMS['dams-witness'],
                       source_sha256=self.data['source_sha256'], parent_prehistory_sha256='hash',
                       branch_day=0, retention='journal', files={key: file_digest(directory/name)
                         for name, key in [('final_state.json','final_state_json'),
                                           ('timeseries.csv','timeseries_csv'), ('summary.json','summary_json')]})
        (directory/'arm.json').write_text(json.dumps(receipt))
        (directory/'summary.json').write_text('{"changed":true}')
        with patch('research_tools.r11_inference.outcomes') as reader:
            with self.assertRaisesRegex(ValueError, 'summary.json'):
                _verified_outcomes(directory.parent, 'dams-witness', self.data, {'branch_day': 0}, {'receipt_sha256': 'hash', 'origin': {}})
        reader.assert_not_called()

    def test_zero_variance_never_claims_significance_or_exact_precision(self):
        from research_tools.r11_inference import _difference_summary
        for value in (0., 1., -1.):
            with self.subTest(value=value):
                row = _difference_summary([value]*3, 'closed', {'closed': .1})
                self.assertEqual(row['mean_difference'], value)
                self.assertIsNone(row['p_two_sided'])
                self.assertIsNone(row['ci95'])
                self.assertFalse(row['half_width_within_delta_over_2'])

    def test_nonfinite_outcome_kept_as_missing_without_shrinking_holm_family(self):
        from unittest.mock import patch
        from research_tools.r11_inference import infer
        from research_tools.r11_outcomes import OUTCOMES
        from research_tools.r11_world_count import PRIMARY
        self.plan(worlds=(1000, 1001, 1002))
        for w in self.data['worlds']:
            for arm in self.data['arms']:
                self.receipt(w, arm)
        def values(path, arm, *_):
            world = int(path.name.rsplit('-w', 1)[1])
            data = {k: 0. for k in OUTCOMES}
            if arm == 'dams-witness':
                data[PRIMARY[0]] = {1000: 1., 1001: 2., 1002: 3.}[world]
                data[PRIMARY[1]] = float('nan') if world == 1001 else 0.
            return data, {}
        with patch('research_tools.r11_retention.science_manifest', return_value={'test': 'bound'}), \
             patch('research_tools.r11_inference._verify_prehistory', return_value=({}, {'files': {}, 'receipt_sha256': 'hash', 'origin': {}})), \
             patch('research_tools.r11_inference._verified_outcomes', side_effect=values):
            rows = infer(self.root)['comparisons']['dams-witness']
        self.assertEqual(rows[PRIMARY[1]]['worlds'], 2)
        self.assertEqual(rows[PRIMARY[1]]['missing_worlds'][0]['world'], 1001)
        self.assertIsNone(rows[PRIMARY[1]]['p_holm'])
        for key in PRIMARY:
            self.assertEqual(rows[key]['holm_family_size'], 3)
        self.assertAlmostEqual(rows[PRIMARY[0]]['p_holm'], rows[PRIMARY[0]]['p_two_sided']*3)


    def test_zero_delivery_and_absent_zero_counters_keep_their_meanings(self):
        import json
        from research_tools.r11_outcomes import outcomes
        (self.root/'summary.json').write_text(json.dumps({
            'active_member_calendar_days': 365.25, 'orders_arrived_units': 10.,
            'delivered_product_units': 0., 'verification_resource_units': 2.}))
        (self.root/'timeseries.csv').write_text('day\n0\n')
        row = outcomes(self.root)
        self.assertEqual(row['delivered_share_of_orders'], 0.)
        self.assertIsNone(row['operating_cost_per_delivered_unit'])
        self.assertIsNone(row['honest_rejection_share'])
        self.assertIsNone(row['fraud_acceptance_share'])
        self.assertEqual(row['governance_member_hours_per_member_year'], 0.)
        self.assertEqual(row['witness_resource_units_per_member_year'], 2.)
        self.assertEqual(row['governance_cost_per_member_year'], 2.)


if __name__ == '__main__':
    unittest.main()
