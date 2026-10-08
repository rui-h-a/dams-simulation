"""Pure-I/O publication boundaries; small fixtures are not completed studies."""
import csv
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from research_tools import longitudinal_analysis as analysis


def person(**updates):
    return {'id': 0, 'entered_day': 0, 'first_positive_authority_day': None,
            'exited_day': None, 'exit_reason': None, 'active_calendar_days': 2,
            'active_workdays': 2, 'present_workdays': 2, 'available_work_hours': 2., **updates}


class CohortTests(unittest.TestCase):
    def test_earliest_competing_event_and_declared_day_ties(self):
        cases = [
            (person(exited_day=5, exit_reason='exit'), {'closed_day': 2, 'suspended_day': None}, ('competing-organization-closure', None, False)),
            (person(), {'closed_day': 2, 'suspended_day': 1}, ('competing-organization-suspension', None, False)),
            (person(first_positive_authority_day=5), {'closed_day': 2, 'suspended_day': None}, ('competing-organization-closure', None, False)),
            (person(first_positive_authority_day=1, exited_day=1), {'closed_day': 1, 'suspended_day': 1}, ('positive-formal-allocation', 1, True)),
            (person(), {'closed_day': None, 'suspended_day': None}, ('administratively-censored', None, False)),
        ]
        for p, state, expected in cases:
            with self.subTest(person=p, state=state):
                self.assertEqual(analysis.allocation_outcome(p, state), expected)

    def test_layoff_memory_loss_and_never_positive_members_are_retained(self):
        case = {'case_id': 'fixture', 'config': SimpleNamespace(days=2, world=99),
                'tags': {'context': 'financing', 'arm_id': 'financing-dams'}}
        state = {'people': [person(exited_day=1, exit_reason='layoff'), person(id=1, entered_day=1)],
                 'agents': [{'id': 0, 'care_hours': 0.}, {'id': 1, 'care_hours': .25}],
                 'closed_day': None, 'suspended_day': None}
        events = [(0, 'entry', 'initial:0', json.dumps({'initial': True, 'person': 0})),
                  (0, 'day_end', 'd0', json.dumps({'memory': {'0': .5}, 'routine': {'0': .4}})),
                  (1, 'layoff', 'p0', json.dumps({'memory_loss': .25})),
                  (1, 'day_end', 'd1', json.dumps({'memory': {'0': .25}, 'routine': {'0': .4}}))]
        rows = analysis.derive_case(case, {'state': state}, events)
        self.assertEqual([row['assigned_people'] for row in rows], [1, 1])
        self.assertEqual([row['positive_formal_allocations'] for row in rows], [0, 0])
        self.assertEqual(rows[0]['whole_case_departure_memory_loss_units'], .25)
        self.assertEqual(json.loads(rows[0]['allocation_outcome_counts_json']), {'competing-layoff': 1})
        self.assertEqual(json.loads(rows[1]['allocation_outcome_counts_json']), {'administratively-censored': 1})
        self.assertEqual(rows[1]['present_member_work_hours'], 1.5)


class PublicationFilesystemTests(unittest.TestCase):
    def test_destination_link_file_and_dangling_nodes_are_rejected_before_raw_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target'; target.mkdir()
            linked = root / 'linked'; linked.symlink_to(target, target_is_directory=True)
            ordinary = root / 'ordinary'; ordinary.write_text('keep user file')
            dangling_tree = root / 'dangling-tree'; dangling_tree.mkdir()
            (dangling_tree / 'dangling').symlink_to(root / 'missing')
            for destination in (linked, ordinary, dangling_tree):
                with self.subTest(destination=destination), patch.object(analysis, 'CheckedLongitudinalStudy') as loader:
                    with self.assertRaises(ValueError):
                        analysis.analyze(root / 'runs', destination)
                    loader.assert_not_called()
            self.assertEqual(ordinary.read_text(), 'keep user file')
            self.assertEqual(list(target.iterdir()), [])
            self.assertTrue((dangling_tree / 'dangling').is_symlink())

    def test_failed_final_rename_restores_previous_publication_and_keeps_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / 'publication'; destination.mkdir(); (destination / 'old').write_text('original')
            candidate = root / 'candidate'; candidate.mkdir(); (candidate / 'new').write_text('new')
            replace = analysis.os.replace
            def fail_candidate(source, target):
                if Path(source) == candidate:
                    raise OSError('controlled final rename failure')
                return replace(source, target)
            with patch.object(analysis.os, 'replace', side_effect=fail_candidate), self.assertRaisesRegex(OSError, 'controlled'):
                analysis.commit_candidate(candidate, destination)
            self.assertEqual((destination / 'old').read_text(), 'original')
            self.assertEqual((candidate / 'new').read_text(), 'new')
            receipt = json.loads((root / 'candidate-failure.json').read_text())
            self.assertTrue(receipt['previous_restored'])
            self.assertIsNone(receipt['rollback_error'])

    def test_loaded_source_drift_is_rejected_before_validation(self):
        native = analysis.file_digest
        for name in analysis.IMPORTED_CODE_SHA256:
            def changed(path, name=name):
                return 'different-loaded-code' if Path(path) == analysis.ROOT / name else native(path)
            with self.subTest(name=name), patch.object(analysis, 'file_digest', side_effect=changed), patch.object(analysis, 'CheckedLongitudinalStudy') as loader:
                with self.assertRaisesRegex(ValueError, 'after import'):
                    analysis.analyze('/unused/runs', '/unused/publication')
                loader.assert_not_called()


class RelativeAdoptionTests(unittest.TestCase):
    @staticmethod
    def fixture(anchors):
        checked = SimpleNamespace(protocol={'confirmation_world_ids': list(range(len(anchors)))},
                                  spec=SimpleNamespace(calendar_start='2020-01-01', common_end_day=3))
        cases = {}
        for world, anchor in enumerate(anchors):
            for arm in ('treated', 'reference'):
                cases[arm, world] = {'case_id': f'{arm}:{world}',
                    'config': SimpleNamespace(longitudinal=SimpleNamespace(organization_initial_age_years=30)),
                    'summary': {'adoption_days': {'0': anchor}, 'closure_day': 1 if world == 0 else None, 'suspension_day': None}}
        return checked, {'contrast_id': 'fixture-contrast', 'treatment_arm': 'treated', 'reference_arm': 'reference'}, cases

    def run_fixture(self, anchors, directory):
        import numpy as np
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        checked, contrast, cases = self.fixture(anchors)
        paired = np.asarray([[[world + day, 10 * world + day, 100 * world + day] for day in range(3)] for world in range(len(anchors))], dtype=float)
        outputs = set()
        with patch.object(analysis, '_save') as save:
            caption = analysis.relative_trajectory(checked, contrast, cases, paired, Path(directory), outputs, plt)
            fig = save.call_args.args[0]
            plt.close(fig)
        with (Path(directory) / 'generated/analysis/relative_adoption_trajectory.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        with (Path(directory) / 'generated/analysis/relative_adoption_world_coverage.csv').open() as stream:
            coverage = list(csv.DictReader(stream))
        return rows, coverage, caption

    def test_nonadopter_keeps_all_assigned_effect_undefined(self):
        with tempfile.TemporaryDirectory() as directory:
            rows, coverage, caption = self.run_fixture([0, None], directory)
            self.assertTrue(all(row['assigned_worlds'] == '2' for row in rows))
            self.assertTrue(all(row['defined_worlds'] == '1' for row in rows))
            self.assertTrue(all(row['mean_paired_difference'] == '' for row in rows))
            self.assertEqual(coverage[1]['alignment_status'], 'never-fully-adopted')
            self.assertEqual(coverage[0]['closure_day'], '1')
            self.assertIn('no adopter-only effect', caption)

    def test_calendar_shift_uses_observed_day_without_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            rows, coverage, _ = self.run_fixture([0, 1], directory)
            row = next(row for row in rows if row['metric'] == 'active_members' and row['relative_calendar_day'] == '0')
            # World0's day0=0, world1's day1=2: exact observed paired mean=1.
            self.assertEqual(float(row['mean_paired_difference']), 1.)
            self.assertEqual(row['defined_worlds'], '2')
            self.assertEqual(coverage[1]['actual_last_guild_adoption_calendar_date'], '2020-01-02')
            boundary = next(row for row in rows if row['relative_calendar_day'] == '-1')
            self.assertEqual(boundary['mean_paired_difference'], '')
            self.assertEqual(boundary['unavailable_calendar_worlds'], '1')

    def test_all_missing_anchors_do_not_become_zero_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            rows, coverage, _ = self.run_fixture([None, None], directory)
            self.assertTrue(all(row['mean_paired_difference'] == '' for row in rows))
            self.assertTrue(all(row['defined_worlds'] == '0' for row in rows))
            self.assertTrue(all(row['alignment_status'] == 'never-fully-adopted' for row in coverage))


if __name__ == '__main__':
    unittest.main()
