import dataclasses
import json
import unittest
from dams_sim.config import Config
from dams_sim.model import Model
from dams_sim.randomness import WorldRandom
from dams_sim.storage import canonical, digest


class ModelTests(unittest.TestCase):
    def test_counter_rng_has_no_cursor(self):
        r = WorldRandom(42, 3)
        expected = r.normal("shock", 2, 10)
        for i in range(100):
            r.normal("irrelevant_branch", i)
        self.assertEqual(expected, r.normal("shock", 2, 10))
        self.assertNotEqual(expected, WorldRandom(42, 4).normal("shock", 2, 10))

    def test_restart_and_schedule_order_exact(self):
        for regime in ("equal", "linear", "sublinear", "hierarchy", "hierarchy_tenure"):
            for backend in ("central", "witness", "consensus"):
                p = Config(n=24, days=15, guilds=3, regime=regime, backend=backend,
                           attack="duplicate", attack_start_day=2, attack_stop_day=10)
                whole = Model(p).run()
                partial = Model(p).run(7)
                resumed = Model.restore(json.loads(canonical(partial.state()))).run()
                reversed_model = Model(p)
                while reversed_model.day < p.days:
                    reversed_model.step(reverse_agents=True)
                self.assertEqual(digest(canonical(whole.state())), digest(canonical(resumed.state())))
                self.assertEqual(digest(canonical(whole.state())), digest(canonical(reversed_model.state())))

    def test_structural_alternatives_and_quorum_recovery(self):
        for behavior in ("linear_response", "satisficing", "reinforcement"):
            p = Config(n=24, days=12, guilds=3, behavior_rule=behavior, autonomy_response=-0.25)
            self.assertEqual(Model(p).run().day, 12)
        p = Config(n=24, days=12, guilds=3, backend="consensus", quorum_unavailable_start_day=0, quorum_unavailable_stop_day=8)
        m = Model(p).run(8)
        self.assertTrue(all(a.confirmed == 0 for a in m.agents))
        m.run()
        self.assertGreater(m.summary()["confirmed_records"], 0)
        self.assertEqual(m.summary()["quorum_unavailable_days"], 8)

    def test_no_future_credit(self):
        m = Model(Config(n=12, days=8, guilds=2))
        m.step()
        self.assertTrue(all(a.confirmed == 0 for a in m.agents))
        m.step()
        self.assertTrue(all(a.confirmed == 0 for a in m.agents))
        m.step()
        self.assertTrue(any(a.confirmed > 0 for a in m.agents))

    def test_free_riding_changes_behaviour(self):
        p = Config(n=24, days=12, guilds=3, attack_start_day=0, attack_stop_day=12, attack_budget_hours_per_day=8)
        baseline = Model(p).run()
        attacked = Model(dataclasses.replace(p, attack="freeride")).run()
        self.assertLess(attacked.summary()["effort_hours"], baseline.summary()["effort_hours"])
        self.assertLess(attacked.summary()["produced_work_units"], baseline.summary()["produced_work_units"])
        self.assertEqual(attacked.summary()["attack_budget_hours"], 96)

    def test_same_attack_budget_and_unique_credit(self):
        budgets = []
        for regime in ("equal", "linear", "sublinear", "hierarchy"):
            p = Config(n=24, days=12, guilds=3, regime=regime, attack="duplicate", attack_start_day=0, attack_stop_day=12)
            m = Model(p).run()
            budgets.append(m.summary()["attack_budget_hours"])
            self.assertGreater(m.summary()["duplicate_records_rejected"], 0)
            self.assertEqual(m.summary()["confirmed_records"], len(m.seen))
        self.assertEqual(len(set(budgets)), 1)

    def test_all_attacks_consume_real_time(self):
        for attack in ("freeride", "forge", "duplicate", "censor"):
            budgets = []
            for regime in ("equal", "linear", "sublinear", "hierarchy", "hierarchy_tenure"):
                p = Config(n=24, days=12, guilds=3, regime=regime, attack=attack,
                           attack_start_day=0, attack_stop_day=12, attack_budget_hours_per_day=2)
                summary = Model(p).run().summary()
                self.assertAlmostEqual(summary["attack_hours_consumed"], 24)
                self.assertLessEqual(summary["max_individual_time_booked_hours"], 1+1e-12)
                budgets.append(summary["attack_hours_consumed"])
            self.assertEqual(len(set(budgets)), 1)
        with self.assertRaises(ValueError):
            Model(Config(n=24,days=2,guilds=3,attack="forge",attack_start_day=0,attack_stop_day=2,attack_budget_hours_per_day=24)).run()

    def test_resource_stops(self):
        with self.assertRaises(MemoryError):
            Model(Config(n=24,days=2,guilds=3,max_rss_mb=1))
        with self.assertRaises(ValueError):
            Config(n=1000,days=100,max_events=100).validate()

    def test_queue_capacity_and_explicit_unfinished(self):
        p = Config(n=24, days=12, guilds=3)
        fast = Model(p).run()
        slow = Model(dataclasses.replace(p, review_capacity_per_member_day=0.1)).run()
        self.assertGreater(slow.summary()["unfinished_records"], fast.summary()["unfinished_records"])
        self.assertLessEqual(fast.summary()["review_hours"], p.n*p.days*p.review_capacity_per_member_day*0.1+1e-9)

    def test_invalid_configs(self):
        for values in ({"n": 0}, {"days": 0}, {"n": True}, {"alpha": float("nan")}, {"update_interval_days": 0},
                       {"guilds": 121}, {"typo": 1}, {"regime": "magical"}, {"review_capacity_per_member_day": 10}):
            with self.assertRaises((ValueError, TypeError)):
                Config.from_dict(values)


if __name__ == "__main__":
    unittest.main()
