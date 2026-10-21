"""KB-only tests; standard library only, no Model, raw data or SQLite imports."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

path = Path(__file__).resolve().parents[1]/'dams_sim/joint_counterfactual.py'
spec = importlib.util.spec_from_file_location('r12_counterfactual_pure', path)
module = importlib.util.module_from_spec(spec)
loaded_before = set(sys.modules)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
loaded_by_module = set(sys.modules)-loaded_before


def plan_payload():
    terms = {'offer_day': 2, 'consideration': 10.}
    return {'schema': module.PLAN_SCHEMA, 'source_sha256': '1'*64,
            'parent_checkpoint': 'parent.json', 'parent_day': 2, 'history_terms': terms,
            'history_terms_sha256': module._digest(terms),
            'firm_config_sha256': {'buyer': '2'*64, 'target': '3'*64},
            'firm_state_sha256': {'buyer': '4'*64, 'target': '5'*64},
            'parent_records_sha256': '6'*64,
            'parent_files': {'parent.json': {'bytes': 100, 'sha256': '7'*64}},
            'arms': list(module.ARMS)}


class PureCounterfactualTest(unittest.TestCase):
    def test_plan_is_canonical_and_returns_detached_values(self):
        payload = plan_payload()
        plan = module.CounterfactualPlan(module._canonical(payload).decode())
        changed = plan.payload; changed['history_terms']['consideration'] = 999.
        self.assertEqual(plan.payload, payload)
        with self.assertRaises(ValueError):module.CounterfactualPlan(json.dumps(payload))

    def test_tampered_history_calendar_action_and_path_refuse(self):
        base = plan_payload()
        mutations = []
        for key, value in (('parent_day', 3), ('arms', ['centralize']), ('source_sha256', 'unfixed')):
            mutations.append({**base, key: value})
        changed = copy.deepcopy(base); changed['history_terms']['consideration'] = 999.; mutations.append(changed)
        changed = copy.deepcopy(base); changed['parent_files']['../unsafe'] = changed['parent_files'].pop('parent.json'); mutations.append(changed)
        for payload in mutations:
            with self.subTest(payload=payload), self.assertRaises(ValueError):module.validate_plan(payload)

    def test_purchase_does_not_consolidate_preownership_target_revenue(self):
        records = []
        for day, target_owner in enumerate(('seller', 'seller', 'buyer')):
            records.append({'day': day, 'owners': {'buyer': 'buyer', 'target': target_owner},
                            'firms': {company: {'metrics_before': {'revenue_resource_units': day*value},
                                                'metrics_after': {'revenue_resource_units': (day+1)*value}}
                                      for company, value in (('buyer', 10.), ('target', 100.))}})
        totals = module.ownership_scoped_totals(records)
        self.assertEqual(totals['buyer-corporate-group']['revenue_resource_units'], 130.)
        self.assertEqual(totals['two-firm-system']['revenue_resource_units'], 330.)
        records[2]['owners']['target'] = 'seller'
        self.assertEqual(module.ownership_scoped_totals(records)['buyer-corporate-group']['revenue_resource_units'], 30.)

    def test_incomplete_daily_roster_cannot_be_summed(self):
        with self.assertRaises(ValueError):module.ownership_scoped_totals([{'day': 1, 'firms': {}}])

    def test_negative_initial_and_discontinuous_counters_refuse_even_before_interval(self):
        def record(day, before, after):
            return {'day':day,'owners':{'buyer':'buyer','target':'seller'},
                    'firms':{c:{'metrics_before':{'revenue_resource_units':before},
                                'metrics_after':{'revenue_resource_units':after}} for c in ('buyer','target')}}
        cases = [[record(0, -100., 10.)], [record(0, 1., 10.)],
                 [record(0, 0., 10.), record(1, 9., 20.)],
                 [record(0, 0., 10.), record(1, 10., float('inf'))],
                 [record(0, False, 10.)]]
        for records in cases:
            for since in (0,len(records)):
                with self.subTest(records=records,since=since), self.assertRaises(ValueError):
                    module.ownership_scoped_totals(records,since_day=since)

    def test_no_model_or_raw_library_was_loaded(self):
        self.assertNotIn('dams_sim.model', loaded_by_module)
        self.assertNotIn('sqlite3', loaded_by_module)


if __name__ == '__main__':unittest.main()
