"""Hand-computed temporal and policy counterexamples; no DAMS Model."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from research_tools.fixed_contribution_replay import load_tape, replay, write_new


def member(who, appointment=0):
    return {"member_id": who, "appointment_days": {"d": appointment}}


def event(kind, day, sequence, **fields):
    return {"event_id": f"e{day}-{sequence}", "day": day, "sequence": sequence, "kind": kind, **fields}


def record(who, credit, day, sequence, claim=None, earned=None, domain="d"):
    return event("record", day, sequence, claim_id=claim or who, earned_day=day if earned is None else earned,
                 credit=credit, attributions=[{"member_id": who, "domain": domain, "fraction": 1}])


def base():
    return {"schema_version": 1, "time_unit": "calendar_day", "alpha": .5,
            "half_life_days": 1, "tier_weights": [1, 2, 4, 8], "domains": ["d"],
            "members": [member("A"), member("B")], "events": [], "query_days": [0, 1, 2, 3]}


class FixedReplayTests(unittest.TestCase):
    def assert_shares(self, observation, policy, expected):
        actual = observation["policies"][policy]["shares"]
        self.assertEqual(set(actual), set(expected))
        for who, value in expected.items():
            self.assertAlmostEqual(actual[who], value, places=13)

    def test_delayed_confirmation_uses_earned_age_and_record_revocation(self):
        t = base()
        t["events"] = [record("A", 4, 0, 0), record("B", 1, 1, 0),
                       event("confirm", 1, 1, claim_id="B"), event("confirm", 2, 0, claim_id="A"),
                       event("revoke", 3, 0, claim_id="A")]
        rows = replay(t)["observations"]
        self.assert_shares(rows[0], "linear", {"A": .5, "B": .5})
        self.assert_shares(rows[1], "linear", {"A": 0, "B": 1})
        self.assertEqual(rows[2]["confirmed_decayed_credit"], {"A": 1., "B": .5})
        self.assert_shares(rows[2], "linear", {"A": 2/3, "B": 1/3})
        self.assert_shares(rows[3], "linear", {"A": 0, "B": 1})
        self.assertEqual(rows[3]["confirmed_decayed_credit"]["B"], .25)

    def test_revocation_removes_only_target_record_without_negative_clipping(self):
        t = base()
        t["events"] = [record("A", 4, 0, 0, "a0"), event("confirm", 0, 1, claim_id="a0"),
                       record("A", 1, 1, 0, "a1"), event("confirm", 1, 1, claim_id="a1"),
                       event("revoke", 2, 0, claim_id="a0")]
        self.assertEqual(replay(t)["observations"][2]["confirmed_decayed_credit"]["A"], .5)

    def test_five_policies_and_ties_match_independent_rational_values(self):
        t = base()
        t["members"] = [member(who, -age) for who, age in zip("ABCD", [4, 3, 2, 1])]
        t["half_life_days"] = None
        t["query_days"] = [0]
        t["events"] = []
        for i, (who, amount) in enumerate(zip("ABCD", [1, 1, 4, 9])):
            t["events"] += [record(who, amount, 0, 2*i), event("confirm", 0, 2*i+1, claim_id=who)]
        row = replay(t)["observations"][0]
        self.assert_shares(row, "equal", dict.fromkeys("ABCD", .25))
        self.assert_shares(row, "linear", dict(zip("ABCD", [1/15, 1/15, 4/15, 9/15])))
        self.assert_shares(row, "sublinear", dict(zip("ABCD", [1/7, 1/7, 2/7, 3/7])))
        self.assert_shares(row, "performance_tiers", dict(zip("ABCD", [.1, .1, 4/15, 8/15])))
        self.assert_shares(row, "tenure_tiers", dict(zip("ABCD", [8/15, 4/15, 2/15, 1/15])))

    def test_cross_domain_credit_is_fractional_and_never_moves_implicitly(self):
        t = base()
        t["domains"] = ["d", "other"]
        t["members"][0]["appointment_days"]["other"] = 0
        r = record("A", 8, 0, 0)
        r["attributions"] = [{"member_id": "A", "domain": "d", "fraction": .25},
                             {"member_id": "A", "domain": "other", "fraction": .75}]
        t["events"] = [r, event("confirm", 0, 1, claim_id="A"),
                       event("eligibility", 1, 0, member_id="A", domain="d", eligible=False)]
        rows = replay(t)["observations"]
        self.assertEqual(rows[0]["confirmed_decayed_credit"]["A"], 2)
        self.assertEqual(rows[1]["confirmed_decayed_credit"]["A"], 6)
        self.assertNotIn("A", rows[2]["confirmed_decayed_credit"])
        self.assertEqual(rows[3]["confirmed_decayed_credit"]["A"], 3)

    def test_eligibility_before_confirmation_regrant_and_exit(self):
        t = base()
        t["members"][0]["appointment_days"] = {}
        t["events"] = [record("A", 4, 0, 0), event("confirm", 0, 1, claim_id="A"),
                       event("eligibility", 1, 0, member_id="A", domain="d", eligible=True),
                       event("exit", 2, 0, member_id="A"), event("exit", 3, 0, member_id="B")]
        rows = replay(t)["observations"]
        self.assertEqual(rows[0]["eligible_count"], 1)
        self.assertEqual(rows[1]["appointment_tenure_calendar_days"]["A"], 0)
        self.assert_shares(rows[1], "linear", {"A": 1., "B": 0.})
        self.assertEqual(rows[3]["status"], "suspended_no_eligible")
        self.assertIsNone(rows[3]["policies"]["equal"]["formal_share_gini"])

    def test_future_event_values_do_not_change_earlier_allocations(self):
        t = base()
        t["events"] = [record("A", 4, 0, 0), event("confirm", 2, 0, claim_id="A")]
        altered = copy.deepcopy(t)
        altered["events"][0]["credit"] = 400
        a, b = replay(t)["observations"], replay(altered)["observations"]
        self.assertEqual(a[:2], b[:2])
        self.assertNotEqual(a[2]["confirmed_decayed_credit"], b[2]["confirmed_decayed_credit"])

    def test_input_order_does_not_give_tied_member_privilege(self):
        t = base()
        t["members"].reverse()
        self.assertEqual(replay(t), replay(base()))

    def test_small_roster_keeps_documented_positional_tier_rule(self):
        t = base()
        t["members"][0]["appointment_days"]["d"] = -1
        row = replay(t)["observations"][0]
        self.assert_shares(row, "tenure_tiers", {"A": 4/5, "B": 1/5})

    def test_invalid_transitions_and_schema_refuse(self):
        mutations = []
        for e in [event("confirm", 0, 0, claim_id="unknown"),
                  event("eligibility", 0, 0, member_id="A", domain="d", eligible=True)]:
            t = base(); t["events"] = [e]; mutations.append(t)
        t = base(); t["alpha"] = True; mutations.append(t)
        t = base(); t["half_life_days"] = 0; mutations.append(t)
        t = base(); t["query_days"] = [0, 0]; mutations.append(t)
        t = base(); t["members"].append(member("A")); mutations.append(t)
        t = base(); t["events"] = [record("A", 1, 0, 0, earned=1)]; mutations.append(t)
        t = base(); t["events"] = [record("A", 1, 4, 0)]; mutations.append(t)
        t = base(); t["events"] = [record("A", 1, 0, 0)]; t["events"][0]["attributions"][0]["fraction"] = .5; mutations.append(t)
        t = base(); t["events"] = [record("A", 1, 0, 0), event("confirm", 0, 1, claim_id="A"), event("confirm", 0, 2, claim_id="A")]; mutations.append(t)
        for t in mutations:
            with self.subTest(t=t), self.assertRaises(ValueError):
                replay(t)

    def test_atomic_publication_is_deterministic_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"result.json"
            value = replay(base())
            write_new(path, value)
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_new(path, {"different": True})
            self.assertEqual(path.read_bytes(), original)
            other = Path(directory)/"other.json"
            write_new(other, value)
            self.assertEqual(other.read_bytes(), original)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["other.json", "result.json"])

    def test_duplicate_json_key_and_nonfinite_constants_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"input.json"
            for payload in ['{"alpha":0.5,"alpha":0.6}', '{"alpha":NaN}']:
                path.write_text(payload)
                with self.assertRaises(ValueError):
                    load_tape(path)

    def test_positive_credit_underflow_cannot_trigger_equal_fallback(self):
        t = base()
        t["half_life_days"] = 1e-6
        t["events"] = [record("A", 1, 0, 0), event("confirm", 0, 1, claim_id="A")]
        with self.assertRaisesRegex(ValueError, "underflow"):
            replay(t)


if __name__ == "__main__":
    unittest.main()
