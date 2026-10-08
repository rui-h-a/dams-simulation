"""Archive admission preserves raw capacity and immutable stage identity."""
from pathlib import Path
from datetime import datetime, timezone
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'research_tools'))
from cloud_control import (GuardError, Ledger, checked_config, stage_archive_options,
                           stage_stop_options, stage_science_deadlines, storage_request_allowance, utc,
                           stage_evidence_download_options, coordinator_archive_reservation,
                           coordinator_request_allowance, coordinator_transfer_allowance)
from test_cloud_control import fixture


class ArchiveControlTests(unittest.TestCase):
    def compressed(self):
        config = fixture()
        config['stages'][0].update(transport_codec='deflate-chunks-v1',
                                  archive_max_raw_bytes=1024**3,
                                  archive_min_free_bytes=1024**3)
        return config

    def test_default_raw_and_explicit_compressed_capacity(self):
        self.assertEqual(stage_archive_options(fixture()['stages'][0]),
                         {'transport_codec': 'raw-v1'})
        config = self.compressed()
        checked_config(config)
        options = stage_archive_options(config['stages'][0])
        self.assertEqual(options, {'transport_codec': 'deflate-chunks-v1',
                                  'archive_max_raw_bytes': 1024**3,
                                  'archive_min_free_bytes': 1024**3})

    def test_unknown_codec_missing_boolean_and_negative_caps_refused(self):
        for field, value in [('transport_codec', 'imagined-compression'),
                             ('archive_max_raw_bytes', None),
                             ('archive_max_raw_bytes', True),
                             ('archive_max_raw_bytes', 0),
                             ('archive_max_raw_bytes', 10**15),
                             ('archive_min_free_bytes', None),
                             ('archive_min_free_bytes', False),
                             ('archive_min_free_bytes', -1)]:
            with self.subTest(field=field, value=value):
                config = self.compressed()
                config['stages'][0][field] = value
                with self.assertRaises(GuardError):
                    checked_config(config)
        config = fixture()
        config['stages'][0]['archive_max_raw_bytes'] = 1024**3
        with self.assertRaises(GuardError):
            checked_config(config)

    def test_disk_admission_counts_old_and_new_raw_trees_without_ratio(self):
        config = self.compressed()
        config['stages'][0].update(disk_gib=20, archive_max_raw_bytes=10 * 1024**3)
        with self.assertRaisesRegex(GuardError, 'old and staged raw'):
            checked_config(config)

    def test_codec_or_raw_cap_change_cannot_reuse_reservation(self):
        config = self.compressed()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.json'
            Ledger(path, config).reserve(config['stages'][0])
            for field, value in [('transport_codec', 'raw-v1'),
                                 ('archive_max_raw_bytes', 2 * 1024**3),
                                 ('archive_min_free_bytes', 2 * 1024**3)]:
                with self.subTest(field=field):
                    changed = self.compressed()
                    changed['stages'][0][field] = value
                    with self.assertRaises(GuardError):
                        Ledger(path, changed).reserve(changed['stages'][0])

    def test_file_cap_is_explicit_bounded_and_cannot_be_ignored_by_raw(self):
        config = self.compressed()
        config['stages'][0]['archive_max_files'] = 8192
        self.assertEqual(stage_archive_options(config['stages'][0])['archive_max_files'], 8192)
        for value in (True, 0, 8193, None):
            config['stages'][0]['archive_max_files'] = value
            with self.assertRaises(GuardError):
                stage_archive_options(config['stages'][0])
        raw = fixture()['stages'][0]
        raw['archive_max_files'] = 100
        with self.assertRaises(GuardError):
            stage_archive_options(raw)

    def stopping(self):
        config = self.compressed()
        config['budget_cap_usd'] = '100'
        stage = config['stages'][0]
        stage.update(max_seconds=7200, shutdown_margin_seconds=3630,
                     pipeline_stop_grace_seconds=360, final_storage_requests_reserved=110000,
                     final_storage_bytes_reserved=97 * 1024**3, max_result_gib='129',
                     storage_operations_usd_upper='2', disk_retention_hours=3)
        stage['runtime_limits']['cooperative_stop_grace_seconds'] = 300
        return config

    def test_stop_grace_leaves_full_archive_hour_inside_original_deadline(self):
        config = self.stopping(); stage = config['stages'][0]
        checked_config(config)
        self.assertEqual(stage_stop_options(config, stage), {
            'pipeline_stop_grace_seconds': 360, 'final_storage_requests_reserved': 110000,
            'final_storage_bytes_reserved': 97 * 1024**3})
        self.assertEqual(storage_request_allowance(config, stage), 133312)
        deadline = datetime(2030, 1, 2, 2, tzinfo=timezone.utc)
        limits = stage_science_deadlines(stage, deadline.isoformat())
        self.assertEqual((deadline - utc(limits['deadline_utc'])).total_seconds(), 3990)
        self.assertEqual((deadline - utc(limits['stop_cutoff_utc'])).total_seconds(), 3630)
        # The watchdog powers off thirty seconds before provider DELETE.
        self.assertEqual((deadline.timestamp() - 30 - utc(limits['stop_cutoff_utc']).timestamp()), 3600)
        self.assertEqual((utc(limits['stop_cutoff_utc']) - utc(limits['deadline_utc'])).total_seconds(), 360)
        legacy = fixture()['stages'][0]
        self.assertEqual(stage_stop_options(config, legacy), {})
        self.assertNotIn('stop_cutoff_utc', stage_science_deadlines(legacy, deadline.isoformat()))

    def test_invalid_stop_or_final_reserve_refused_before_dispatch(self):
        for field, value in [('pipeline_stop_grace_seconds', True),
                             ('pipeline_stop_grace_seconds', 359),
                             ('pipeline_stop_grace_seconds', 3600),
                             ('final_storage_requests_reserved', False),
                             ('final_storage_requests_reserved', 0),
                             ('final_storage_requests_reserved', 133303),
                             ('final_storage_bytes_reserved', True),
                             ('final_storage_bytes_reserved', 0),
                             ('final_storage_bytes_reserved', 129 * 1024**3)]:
            with self.subTest(field=field, value=value):
                config = self.stopping(); config['stages'][0][field] = value
                with self.assertRaises(GuardError):
                    checked_config(config)
        config = self.stopping()
        config['stages'][0]['runtime_limits']['stop_cutoff_utc'] = '2030-01-01T00:00:00Z'
        with self.assertRaises(GuardError):
            checked_config(config)
        config = self.stopping()
        del config['stages'][0]['pipeline_stop_grace_seconds']
        with self.assertRaises(GuardError):
            checked_config(config)

    def test_request_reserve_and_grace_cannot_change_frozen_stage(self):
        config = self.stopping()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.json'
            Ledger(path, config).reserve(config['stages'][0])
            for field, value in [('pipeline_stop_grace_seconds', 420),
                                 ('final_storage_requests_reserved', 100000),
                                 ('final_storage_bytes_reserved', 96 * 1024**3)]:
                changed = self.stopping(); changed['stages'][0][field] = value
                with self.assertRaises(GuardError):
                    Ledger(path, changed).reserve(changed['stages'][0])

    def test_interrupted_evidence_opt_in_is_separate_from_auto_resume_options(self):
        config = self.stopping(); stage = config['stages'][0]
        self.assertEqual(stage_evidence_download_options(stage), {})
        stage['preserve_interrupted_evidence'] = True
        checked_config(config)
        self.assertTrue(stage_stop_options(config, stage)['preserve_interrupted_evidence'])
        self.assertEqual(stage_evidence_download_options(stage), {'allow_interrupted_evidence': True})
        self.assertNotIn('allow_interrupted_evidence', stage_archive_options(stage))
        for value in ('true', 1, None):
            stage['preserve_interrupted_evidence'] = value
            with self.assertRaises(GuardError):
                checked_config(config)
        stage['preserve_interrupted_evidence'] = True
        del stage['final_storage_requests_reserved']
        with self.assertRaises(GuardError):
            checked_config(config)
        config = self.stopping(); config['stages'][0]['preserve_interrupted_evidence'] = True
        del config['stages'][0]['final_storage_bytes_reserved']
        with self.assertRaises(GuardError):
            checked_config(config)

    def test_external_archive_and_collector_share_one_original_actor_and_egress_cap(self):
        config = self.stopping(); stage = config['stages'][0]
        stage.update(max_egress_gib='241', archive_source_requests_reserved=50000,
                     archive_source_egress_gib_reserved='133')
        checked_config(config)
        self.assertEqual(coordinator_archive_reservation(config, stage), (50000, 133 * 1024**3))
        self.assertEqual(coordinator_request_allowance(config, stage), 83312)
        self.assertEqual(coordinator_transfer_allowance(config, stage), 108 * 1024**3)
        self.assertEqual(storage_request_allowance(config, stage), 133312)
        self.assertNotIn('archive_source_requests_reserved', stage_stop_options(config, stage))
        for field, value in [('archive_source_requests_reserved', True),
                             ('archive_source_requests_reserved', 133303),
                             ('archive_source_egress_gib_reserved', '241'),
                             ('archive_source_egress_gib_reserved', '0')]:
            changed = self.stopping(); changed['stages'][0].update(
                max_egress_gib='241', archive_source_requests_reserved=50000,
                archive_source_egress_gib_reserved='133')
            changed['stages'][0][field] = value
            with self.assertRaises(GuardError):
                checked_config(changed)
        del stage['archive_source_requests_reserved']
        with self.assertRaises(GuardError):
            checked_config(config)


if __name__ == '__main__':
    unittest.main()
