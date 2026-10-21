"""Build an openly specified synthetic temporal tape, not an empirical company."""
import argparse
import json
from .fixed_contribution_replay import MAX_INPUT_BYTES, validate_tape, write_new


def build_tape(population=10000):
    if type(population) is not int or not 1 <= population <= 100000:
        raise ValueError("fixture population must be an integer in1..100000")
    members, events = [], []
    for i in range(population):
        who, primary = f"p{i:05d}", f"d{i%2}"
        members.append({"member_id": who, "appointment_days": {"d0": -((17*i)%3650), "d1": -((17*i)%3650)}})
        attrs = [{"member_id": who, "domain": primary, "fraction": 1.}]
        if i%10 == 0:
            attrs = [{"member_id": who, "domain": primary, "fraction": .25},
                     {"member_id": who, "domain": f"d{1-i%2}", "fraction": .75}]
        events.extend([
            {"event_id": f"record-{who}", "day": 0, "kind": "record", "claim_id": who,
             "earned_day": 0, "credit": (i%100+1)**2, "attributions": attrs},
            {"event_id": f"confirm-{who}", "day": 1+i%31, "kind": "confirm", "claim_id": who}])
        if i%97 == 0:
            events.append({"event_id": f"revoke-{who}", "day": 90, "kind": "revoke", "claim_id": who})
        if i%101 == 0:
            events.append({"event_id": f"remove-{who}", "day": 120, "kind": "eligibility",
                           "member_id": who, "domain": primary, "eligible": False})
            if i%100 != 0:
                events.append({"event_id": f"grant-{who}", "day": 181, "kind": "eligibility",
                               "member_id": who, "domain": primary, "eligible": True})
        if i%100 == 0:
            events.append({"event_id": f"exit-{who}", "day": 181, "kind": "exit", "member_id": who})
    priority = {"record": 0, "confirm": 1, "revoke": 2, "eligibility": 3, "exit": 4}
    events.sort(key=lambda e: (e["day"], 5 if e.get("eligible") is True else priority[e["kind"]], e["event_id"]))
    previous, sequence = None, 0
    for e in events:
        sequence = sequence+1 if e["day"] == previous else 0
        e["sequence"] = sequence
        previous = e["day"]
    return validate_tape({"schema_version": 1, "time_unit": "calendar_day", "alpha": .5,
                          "half_life_days": 180, "tier_weights": [1, 2, 4, 8], "domains": ["d0", "d1"],
                          "members": members, "events": events, "query_days": [0, 1, 30, 90, 180, 365]})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=int, default=10000)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    tape = build_tape(args.population)
    if len((json.dumps(tape, sort_keys=True, separators=(",", ":"))+"\n").encode()) > MAX_INPUT_BYTES:
        raise ValueError("fixture exceeds replay's16MiB input ceiling; no smaller population was substituted")
    write_new(args.output, tape)


if __name__ == "__main__":
    main()
