"""Shared schema routing/origin contract fixtures, not completed studies."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from dams_sim.spec import resolve_spec
from dams_sim.storage import canonical, digest, file_digest
from research_tools import validate_pipeline


class LongitudinalSharedGateTests(unittest.TestCase):
    @staticmethod
    def fixture(root):
        (root / 'pipeline_manifest.json').write_text(json.dumps({'schema_version': 3}))
        child = root / 'confirmation' / 'fixture'; child.mkdir(parents=True)
        manifest = {'source_sha256': 'fixture-source', 'config_sha256': 'fixture-config',
                    'git_commit': 'fixture-commit', 'git_dirty': False,
                    'execution_provenance': {'origin': 'fixture'}}
        (child / 'manifest.json').write_text(json.dumps(manifest))
        generation = root / 'publication/generated/analysis/generation_manifest.json'
        generation.parent.mkdir(parents=True); generation.write_text('fixture generation')
        config = SimpleNamespace(world=30000, n=1000, days=3743, regime='sublinear', backend='central')
        case = {'attempt': child, 'manifest': manifest, 'config': config}
        return SimpleNamespace(root=root, spec=resolve_spec('longitudinal-adoption-5y', 1000),
                               cases={'fixture-id': case}, result=lambda: {'unique_complete_cases': 1, 'status': 'validated'})

    def test_schema_three_routes_to_independent_gate_and_binds_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); study = self.fixture(root)
            with patch('research_tools.validate_longitudinal.CheckedLongitudinalStudy', return_value=study) as gate, \
                 patch.object(validate_pipeline, 'CheckedStudy') as old_gate, \
                 patch('dams_sim.model.Model.__init__', side_effect=AssertionError('no Model')) as model:
                result = validate_pipeline.validate_pipeline_output(root, 'longitudinal-adoption-5y', 1000, expected_provenance={'origin': 'fixture'})
                gate.assert_called_once_with(root, expected_provenance={'origin': 'fixture'})
                old_gate.assert_not_called(); model.assert_not_called()
            self.assertEqual(result['case_origins_sha256'], digest(canonical(result['case_origins'])))
            self.assertEqual(result['case_origins'][0]['manifest_sha256'], file_digest(root/'confirmation/fixture/manifest.json'))
            self.assertEqual(result['case_origins'][0]['attempt'], 'confirmation/fixture')
            self.assertTrue(result['validation'])

    def test_request_mismatch_and_wrong_origin_roster_count_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); study = self.fixture(root)
            with patch('research_tools.validate_longitudinal.CheckedLongitudinalStudy', return_value=study):
                for spec, scale in [('longitudinal-adoption-10y', 1000), ('longitudinal-adoption-5y', 10000)]:
                    with self.subTest(spec=spec, scale=scale), self.assertRaisesRegex(ValueError, 'requested spec/scale'):
                        validate_pipeline.validate_pipeline_output(root, spec, scale)
                study.result=lambda: {'unique_complete_cases': 2}
                with self.assertRaisesRegex(ValueError, 'origin roster'):
                    validate_pipeline.validate_pipeline_output(root)

    def test_actual_independent_loader_rejects_partial_without_constructing_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'pipeline_manifest.json').write_text(json.dumps({'schema_version': 3, 'status': 'failed'}))
            with patch('dams_sim.model.Model.__init__', side_effect=AssertionError('no Model')) as model:
                with self.assertRaises(ValueError):
                    validate_pipeline.validate_pipeline_output(root, 'longitudinal-adoption-5y', 1000)
                model.assert_not_called()

    def test_manifest_link_is_rejected_before_either_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); target=root/'actual.json';target.write_text('{"schema_version":3}')
            (root/'pipeline_manifest.json').symlink_to(target)
            with patch.object(validate_pipeline, 'CheckedStudy') as old_gate, \
                 patch('research_tools.validate_longitudinal.CheckedLongitudinalStudy') as new_gate:
                with self.assertRaisesRegex(ValueError, 'symlinked'):
                    validate_pipeline.validate_pipeline_output(root)
                old_gate.assert_not_called();new_gate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
