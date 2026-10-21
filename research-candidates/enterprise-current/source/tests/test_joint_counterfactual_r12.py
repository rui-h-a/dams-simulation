"""Small real-model candidate tests; no formal study samples.

Requires a separately admitted fixture supervisor (90s/90MiB early stop,
100MiB hard ceiling, 2GiB free floor) and DAMS_R12_TEST_TMP. No paid activity.
Each firm has eight initial people; all tails/restarts are engineering controls.
"""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


class FullHistoryCounterfactualTest(unittest.TestCase):
    def setUp(self):
        from dams_sim.config import Config
        from dams_sim.joint_firm import JointRun, JointTerms
        from research_tools.r11_design import base_config
        base = Path(os.environ['DAMS_R12_TEST_TMP']).resolve(strict=True)
        if shutil.disk_usage(base).free < 2*1024**3:self.skipTest('fixture free floor refused')
        self.tmp = tempfile.TemporaryDirectory(prefix='r12-joint-', dir=base)
        self.root = Path(self.tmp.name); self.opened = []
        config, _ = base_config(scale=8, context='startup', growth='none', world=12000,
                                seed=731, pre_years=0, post_years=1)
        config.update(days=8, regime='sublinear', backend='witness')
        config['longitudinal']['work_creation_stop_day'] = None
        config['longitudinal'].update(adoption_mode='fixed', adoption_day=0)
        buyer = Config.from_dict(config)
        target = replace(buyer, world=12001, seed=732, regime='hierarchy', backend='central',
                         longitudinal=replace(buyer.longitudinal, adoption_mode='never'))
        self.original_terms = JointTerms(offer_day=3, integration_workdays=1)
        self.parent = JointRun(buyer, target, self.root/'common', self.original_terms)
        self.opened.append(self.parent)

    def tearDown(self):
        for run in self.opened:run.close()
        size = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
        failed = size >= 90*1024**2 or any(test is self for test, _ in
                    self._outcome.result.errors+self._outcome.result.failures)
        if failed or os.environ.get('DAMS_R12_KEEP_FIXTURES') == '1':
            self.root.rename(self.root.with_name(('failure-' if failed else 'passed-')+self.root.name))
        self.tmp.cleanup()
        self.assertLess(size, 90*1024**2)

    def common(self):
        from dams_sim.joint_counterfactual import CounterfactualPlan
        self.parent.run(3)
        checkpoint = self.parent.root/'common-parent.json'; self.parent.snapshot(checkpoint)
        return checkpoint, CounterfactualPlan.from_checkpoint(checkpoint)

    def test_actual_pc_joint_restore_keeps_version_and_complete_state(self):
        from dams_sim.joint_firm import JointRun
        self.parent.run(5); checkpoint = self.parent.root/'controlled.json'; self.parent.snapshot(checkpoint)
        restored = JointRun.restore(checkpoint, self.root/'restored'); self.opened.append(restored)
        for company in ('buyer', 'target'):
            self.assertEqual(restored._all()[company].semantic_digest(), self.parent._all()[company].semantic_digest())
            self.assertEqual(restored._all()[company].pc.to_dict(), self.parent._all()[company].pc.to_dict())
        self.parent.run(); restored.run()
        self.assertEqual(restored.records, self.parent.records)

    def test_initial_metric_tampering_is_refused_before_plan_authority(self):
        import copy
        from dams_sim.joint_firm import JointRun, verify_cash_records
        from dams_sim.joint_counterfactual import CounterfactualPlan, ownership_scoped_totals
        checkpoint, _ = self.common()
        value = json.loads(checkpoint.read_bytes())
        for key in ('revenue_resource_units', 'operating_resource_units'):
            value['records'][0]['firms']['buyer']['metrics_before'][key] = -100.
        bad = self.parent.root/'bad-initial-metrics.json'; bad.write_text(json.dumps(value))
        # There is deliberately no counterfactual lineage yet to protect this
        # ordinary common checkpoint. Each independent admission must refuse.
        def bad_restore():
            restored=JointRun.restore(bad, self.root/'bad-initial-restore')
            restored.close()
        for check in (lambda: verify_cash_records(value['records']),
                      lambda: ownership_scoped_totals(value['records']),
                      lambda: ownership_scoped_totals(value['records'], since_day=3),
                      lambda: CounterfactualPlan.from_checkpoint(bad),
                      bad_restore):
            with self.subTest(check=check), self.assertRaisesRegex(ValueError, 'initial|nonnegative'):
                check()
        prior = self.parent.records
        self.parent.records = copy.deepcopy(value['records'])
        try:
            with self.assertRaisesRegex(ValueError, 'initial'):
                self.parent.validate_joint_metadata()
        finally:self.parent.records = prior

    def test_incompatible_valid_native_snapshots_refuse_before_engine_restore(self):
        from dams_sim.model import Model
        from dams_sim.joint_firm import JointRun
        from dams_sim.joint_counterfactual import CounterfactualPlan
        from dams_sim.longitudinal_model import verify_snapshot
        from dams_sim.storage import file_digest
        checkpoint, _ = self.common()
        value = json.loads(checkpoint.read_bytes())
        original = self.parent._all()['target'].config
        different = replace(original, longitudinal=replace(original.longitudinal, work_weekdays=(0, 1, 2, 3)))
        different.validate(); original.validate()
        alternate = Model(different, storage_dir=self.root/'different-target')
        try:
            for _ in range(3):alternate.step()
            native = self.parent.root/'different-target.json'; alternate.write_checkpoint(native)
            envelope, _ = verify_snapshot(native, expected_config=different)
        finally:alternate.ledger.close()
        value['firms']['target'] = {'file': native.name, 'sha256': file_digest(native), 'day': 3,
                                    'config': different.to_dict(),
                                    'state_semantic_sha256': envelope['state_semantic_sha256']}
        bad = self.parent.root/'incompatible-pair.json'; bad.write_text(json.dumps(value))
        # Both firm native snapshots independently verify, but pairing them is
        # forbidden before restoration or plan creation, not a later raw failure.
        buyer = value['firms']['buyer']
        verify_snapshot(checkpoint.parent/buyer['file'], expected_config=self.parent._all()['buyer'].config)
        with patch.object(Model, 'restore_checkpoint', side_effect=AssertionError('engine restore reached')) as restore:
            with self.assertRaisesRegex(ValueError, 'calendars/horizons'):
                JointRun.restore(bad, self.root/'incompatible-restore')
            restore.assert_not_called()
        self.assertFalse((self.root/'incompatible-restore').exists())
        with patch('dams_sim.longitudinal_model.verify_snapshot', side_effect=AssertionError('raw inspection reached')) as raw:
            with self.assertRaisesRegex(ValueError, 'calendars/horizons'):
                CounterfactualPlan.from_checkpoint(bad)
            raw.assert_not_called()

    def test_three_arms_preserve_both_histories_pay_conversion_and_resume(self):
        from dams_sim.model import Model
        from dams_sim.joint_firm import JointRun, full_inventory
        from dams_sim.joint_counterfactual import ARMS, ownership_scoped_totals, configuration_history
        from dams_sim.storage import file_digest, atomic_csv
        from dams_sim.longitudinal_model import verify_snapshot
        from research_tools.validate_longitudinal import verify_daily_evidence, verify_summary_evidence
        checkpoint, plan = self.common(); original = file_digest(checkpoint)
        before = {c: full_inventory(m._long) for c, m in self.parent._all().items()}
        target = self.parent._private['target']
        self.assertGreater(target.ledger.pending_count('review')+target.ledger.pending_count('commit'), 0)
        self.assertGreater(target.operations.backlog(), 0.)
        self.assertTrue(target.operations.shipments)
        arms = {}
        for arm in ARMS:
            # No target/buyer generation is permissible after the common parent.
            with patch.object(Model, '__init__', side_effect=AssertionError('must restore, never regenerate')):
                run = JointRun.fork_counterfactual(checkpoint, self.root/arm, plan=plan, arm=arm)
            self.opened.append(run); arms[arm] = run
            self.assertEqual(asdict(run.terms), asdict(self.original_terms))
            for company, model in run._all().items():self.assertEqual(full_inventory(model._long), before[company])
            run.run(6); cp = run.root/'joint-six.json'; run.snapshot(cp)
            resumed = JointRun.restore(cp, self.root/(arm+'-resumed'), expected_plan=plan, expected_arm=arm)
            self.opened.append(resumed); run.run(); resumed.run()
            self.assertEqual(resumed.records, run.records)
            self.assertEqual(resumed.events, run.events)
            for company in ('buyer', 'target'):
                self.assertEqual(resumed._all()[company].semantic_digest(), run._all()[company].semantic_digest())
                model = run._all()[company]
                raw = run.root/('raw-'+company); raw.mkdir()
                final = raw/'final_state.json'; model.write_final_state(final)
                envelope, database = verify_snapshot(final, expected_config=model.config)
                atomic_csv(raw/'timeseries.csv', model.history)
                summary = json.loads(json.dumps(model.summary()))
                (raw/'summary.json').write_text(json.dumps(summary))
                verify_daily_evidence(raw/'timeseries.csv', model.config, envelope['state'], database,
                                      config_history=configuration_history(run, company))
                verify_summary_evidence(summary, model.config, envelope['state'], database)
            completed = [event for event in run.events if event['kind'] == 'integration-complete']
            if arm == 'no-acquisition':self.assertEqual(completed, [])
            else:
                self.assertEqual(len(completed), 1)
                event = completed[0]
                self.assertEqual(event['mode'], arm)
                self.assertEqual(event['history_terms_mode'], self.original_terms.integration_mode)
                self.assertEqual(event['intervention_arm'], arm)
                for model in run._all().values():
                    actual = [json.loads(payload) for seq, day, kind, ident, payload in model.ledger.rows('journal')
                              if day == event['day'] and kind == 'joint_owned_control']
                    self.assertEqual(len(actual), 1)
                    self.assertEqual(actual[0]['mode'], arm)
                if arm == 'centralize':
                    value = json.loads(cp.read_bytes())
                    next(e for e in value['events'] if e['kind'] == 'integration-complete')['mode'] = 'preserve-autonomy'
                    bad = run.root/'wrong-completion-label.json'; bad.write_text(json.dumps(value))
                    with self.assertRaisesRegex(ValueError, 'completion event'):
                        JointRun.restore(bad, self.root/'bad-label-restore', expected_plan=plan, expected_arm=arm)
        self.assertEqual(file_digest(checkpoint), original)
        no = arms['no-acquisition']; self.assertEqual(no.owners['target'], 'seller')
        self.assertTrue(all(row['firms']['target']['control'] is None for row in no.records))
        self.assertEqual(no.seller_account, 0.); self.assertEqual(no._private['target'].day, 8)
        central = arms['centralize']; target = central.units['target']
        self.assertGreater(target.metrics['migration_resource_units'], 0.)
        self.assertGreater(target.metrics['training_hours'], 0.)
        self.assertGreater(target.metrics['dual_run_resource_units'], 0.)
        self.assertEqual(target.policy(0), central.units['buyer'].policy(0))
        self.assertIsNotNone(central.counterfactual['governance_transition'])
        self.assertIsNone(arms['preserve-autonomy'].counterfactual['governance_transition'])
        totals = ownership_scoped_totals(central.records)
        self.assertLess(totals['buyer-corporate-group']['revenue_resource_units'],
                        totals['two-firm-system']['revenue_resource_units'])

    def test_terms_mutation_and_relabelled_restore_are_refused(self):
        from dams_sim.joint_firm import JointRun
        checkpoint, plan = self.common()
        run = JointRun.fork_counterfactual(checkpoint, self.root/'arm', plan=plan, arm='no-acquisition')
        self.opened.append(run); run.run(6); cp = run.root/'arm.json'; run.snapshot(cp)
        with self.assertRaises(ValueError):JointRun.restore(cp, self.root/'unbound')
        with self.assertRaises(ValueError):JointRun.restore(cp, self.root/'wrong-arm', expected_plan=plan, expected_arm='centralize')
        run.terms = replace(run.terms, consideration=999.)
        with self.assertRaises(ValueError):run.snapshot(run.root/'bad-terms.json')
        before = {c: m.semantic_digest() for c, m in run._all().items()}
        with self.assertRaises(ValueError):run.step()
        self.assertEqual(before, {c: m.semantic_digest() for c, m in run._all().items()})
        other = JointRun.fork_counterfactual(checkpoint, self.root/'relabel', plan=plan, arm='no-acquisition')
        self.opened.append(other); other.counterfactual['arm'] = 'centralize'
        with self.assertRaises(ValueError):other.step()
        self.assertEqual(other.day, plan.payload['parent_day'])

    def test_different_work_weekdays_refuse_before_model_construction(self):
        from dams_sim.model import Model
        from dams_sim.joint_firm import JointRun
        buyer = self.parent._all()['buyer'].config
        original = self.parent._all()['target'].config
        target = replace(original, longitudinal=replace(original.longitudinal, work_weekdays=[0, 1, 2, 3]))
        target.validate()
        with patch.object(Model, '__init__', side_effect=AssertionError('calendar must refuse before constructor')):
            with self.assertRaisesRegex(ValueError, 'Gregorian calendars') as error:
                JointRun(buyer, target, self.root/'calendar-refused', self.original_terms)
        self.assertFalse((self.root/'calendar-refused').exists())
        (self.root/'calendar-refusal.json').write_text(json.dumps({
            'buyer_work_weekdays': buyer.longitudinal.work_weekdays,
            'target_work_weekdays': target.longitudinal.work_weekdays,
            'target_configuration_independently_validated': True,
            'new_model_constructor_called': False, 'output_directory_created': False,
            'error_type': type(error.exception).__name__, 'error': str(error.exception)}))


if __name__ == '__main__':unittest.main()
