"""Joint operations/M&A engineering controls, never formal study worlds."""
import copy
import dataclasses as D
import json
import math
import sqlite3
import unittest

from dams_sim.enterprise_operations import CountryContext
from dams_sim.model import Model
from research_tools.validate_longitudinal import iter_journal_rows
import test_enterprise_acquisition as original_controls
from test_enterprise_acquisition import configuration


class IntegratedTests(unittest.TestCase):
    setUp = original_controls.AcquisitionTests.setUp
    tearDown = original_controls.AcquisitionTests.tearDown
    model = original_controls.AcquisitionTests.model
    raw = original_controls.AcquisitionTests.raw

    @staticmethod
    def work_rows(path):
        connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            return [json.loads(row[3]) for row in iter_journal_rows(connection)
                    if row[1] == 'work']
        finally:
            connection.close()

    @staticmethod
    def overloaded():
        p = configuration(autonomy_coordination_workday_units=.2)
        return D.replace(p, enterprise_operations=D.replace(
            p.enterprise_operations, management_span=1,
            coordination_workday_units_per_layer=.35,
            management_overload_workday_units_per_report=.05)).validate()

    def test_acquisition_and_persisted_coordination_debt_share_real_person_time(self):
        p = self.overloaded()
        m = self.model(p, 'joint-time')
        m.run()
        state, db = self.raw(m, 'joint-time')
        self.assertEqual(m.summary()['model_version'], 'longitudinal-enterprise-integrated-6')
        self.assertGreater(m.metrics['acquired_people'], 0)
        self.assertGreater(math.fsum(m.operations.coordination_pending.values()), 0)
        self.assertLessEqual(m.metrics['max_individual_time_booked_hours'], 1 + 1e-12)
        acquired = set(m.acquisitions.targets['target-a']['employee_ids'].values())
        work = self.work_rows(db)
        acquired_work = [v for v in work if v['person'] in acquired]
        self.assertTrue(acquired_work)
        # Independent observable bound: neither M&A nor queued management work
        # can consume more than this person's entire available workday.
        for v in acquired_work:
            self.assertGreaterEqual(v['enterprise_coordination'], .2 * v['funding_fraction'])
            a = state['state']['agents'][v['person']]
            self.assertLessEqual(v['enterprise_coordination'] + v['effort'] +
                                 v['training'] + v['attack_hours'], 1 - a['care_hours'] + 1e-12)

    def test_joint_queue_and_target_history_checkpoint_recovery_is_exact(self):
        p = self.overloaded()
        m = self.model(p, 'joint-resume')
        m.run(15)
        checkpoint = self.root / 'joint-checkpoint.json'
        m.write_checkpoint(checkpoint)
        resumed = Model.restore_checkpoint(checkpoint, expected_config=p,
                                           storage_dir=self.root / 'joint-restored')
        self.models.append(resumed)
        m.run()
        resumed.run()
        self.assertEqual(m.semantic_digest(), resumed.semantic_digest())
        self.assertEqual(m.operations.to_dict(), resumed.operations.to_dict())
        self.assertEqual(m.acquisitions.to_dict(), resumed.acquisitions.to_dict())
        self.assertEqual(m.acquisitions.targets['target-a']['retained_evidence'],
                         [D.asdict(v) for v in p.enterprise_acquisition.targets[0].evidence])
        self.raw(m, 'joint-original')
        self.raw(resumed, 'joint-resumed')

    def test_acquired_idle_restricted_site_does_not_stop_parent_domestic_work(self):
        p = configuration()
        p = D.replace(p, enterprise_operations=D.replace(
            p.enterprise_operations,
            countries=(CountryContext(key='parent', export_allowed=False),
                       CountryContext(key='target', export_allowed=False,
                                      logistics_resource_units_per_unit=100.)),
            destination_market_shares=(1., 0.))).validate()
        m = self.model(p, 'joint-domestic')
        m.run()
        _, db = self.raw(m, 'joint-domestic')
        self.assertGreater(m.metrics['acquired_people'], 0)
        work = self.work_rows(db)
        acquired = set(m.acquisitions.targets['target-a']['employee_ids'].values())
        self.assertGreater(math.fsum(v['produced_research_only'] for v in work
                                    if v['person'] not in acquired), 0)
        self.assertEqual(math.fsum(v['produced_research_only'] for v in work
                                  if v['person'] in acquired), 0)
        self.assertGreater(m.metrics['acquisition_integration_resource_units'], 0)

    def test_coherent_joint_stock_edits_cannot_replace_committed_original_events(self):
        p = self.overloaded()
        m = self.model(p, 'joint-forgery')
        m.run()
        self.raw(m, 'joint-forgery')
        before = m.semantic_digest()
        forged = copy.deepcopy(m.state())
        forged['enterprise_operating_state']['coordination_requested_units'] += 1.
        forged['enterprise_operating_state']['coordination_pending']['0'] += 1.
        with self.assertRaises(ValueError):
            Model.restore(forged, storage_dir=self.root / 'joint-forged')
        self.assertEqual(m.semantic_digest(), before)


if __name__ == '__main__':
    unittest.main()
