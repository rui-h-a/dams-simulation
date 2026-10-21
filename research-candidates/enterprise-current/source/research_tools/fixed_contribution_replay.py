"""Replay one fixed evidence tape under five formal-authority policies.

No Model, behavioural transition, latent truth, RNG or empirical calibration is
used. Events are processed once in declared (day, sequence) order. A query sees
only events already available at the end of its calendar day.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

from dams_sim.authority import authority, gini, tier_authority, total_variation

VERSION = "fixed-contribution-replay-1"
POLICIES = ("equal", "linear", "sublinear", "performance_tiers", "tenure_tiers")
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def _keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required.split()):
        raise ValueError("object fields differ from the declared replay schema")


def _name(value):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("identifiers must be nonempty strings of at most128 characters")
    return value


def _integer(value, *, minimum=0):
    if type(value) is not int or not minimum <= value <= 10**7:
        raise ValueError("day/sequence must be an integer within declared bounds")
    return value


def _number(value, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError("quantity must be finite and nonnegative (positive where required)")
    return float(value)


def validate_tape(tape):
    _keys(tape, "schema_version time_unit alpha half_life_days tier_weights domains members events query_days")
    if type(tape["schema_version"]) is not int or tape["schema_version"] != 1 or tape["time_unit"] != "calendar_day":
        raise ValueError("unknown tape schema or time unit")
    if not 0 < _number(tape["alpha"]) < 1:
        raise ValueError("sublinear alpha must be strictly between zero and one")
    if tape["half_life_days"] is not None:
        _number(tape["half_life_days"], positive=True)
    weights = tape["tier_weights"]
    if not isinstance(weights, list) or not 1 <= len(weights) <= 64:
        raise ValueError("tier weights must contain1..64 entries")
    numbers = [_number(x, positive=True) for x in weights]
    if numbers != sorted(numbers):
        raise ValueError("tier weights must be nondecreasing")
    domains = tape["domains"]
    if not isinstance(domains, list) or not 1 <= len(domains) <= 64:
        raise ValueError("domain roster must contain1..64 entries")
    for domain in domains:
        _name(domain)
    if len(set(domains)) != len(domains):
        raise ValueError("duplicate domain")
    members = tape["members"]
    if not isinstance(members, list) or not 1 <= len(members) <= 100_000:
        raise ValueError("member roster must contain1..100000 people")
    ids = set()
    for member in members:
        _keys(member, "member_id appointment_days")
        who = _name(member["member_id"])
        if who in ids:
            raise ValueError("duplicate member")
        ids.add(who)
        appointments = member["appointment_days"]
        if not isinstance(appointments, dict) or not set(appointments) <= set(domains):
            raise ValueError("initial appointments name an unknown domain")
        for day in appointments.values():
            if type(day) is not int or not -10**7 <= day <= 0:
                raise ValueError("initial appointment days must be nonpositive integers")
    queries = tape["query_days"]
    if not isinstance(queries, list) or not 1 <= len(queries) <= 256:
        raise ValueError("query days must contain1..256 observations")
    for day in queries:
        _integer(day)
    if queries != sorted(set(queries)):
        raise ValueError("query days must be strictly increasing")
    events = tape["events"]
    if not isinstance(events, list) or len(events) > 1_000_000:
        raise ValueError("events must be a bounded list")
    seen, last = set(), (-1, -1)
    fields = {"record": "claim_id earned_day credit attributions", "confirm": "claim_id",
              "revoke": "claim_id", "exit": "member_id", "eligibility": "member_id domain eligible"}
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in fields:
            raise ValueError("unknown event kind")
        kind = event["kind"]
        _keys(event, "event_id day sequence kind " + fields[kind])
        eid = _name(event["event_id"])
        order = (_integer(event["day"]), _integer(event["sequence"]))
        if eid in seen or order <= last:
            raise ValueError("duplicate event or non-increasing event order")
        seen.add(eid)
        last = order
        if event["day"] > queries[-1]:
            raise ValueError("events after the last observation would be silently unobserved")
        if "claim_id" in event:
            _name(event["claim_id"])
        if "member_id" in event and event["member_id"] not in ids:
            raise ValueError("event names an unknown member")
        if kind == "record":
            if _integer(event["earned_day"]) > event["day"]:
                raise ValueError("record cannot observe future earned credit")
            _number(event["credit"])
            attrs = event["attributions"]
            if not isinstance(attrs, list) or not attrs:
                raise ValueError("record needs explicit cross-domain attribution")
            pairs, fractions = set(), []
            for attr in attrs:
                _keys(attr, "member_id domain fraction")
                pair = (attr["member_id"], attr["domain"])
                if pair[0] not in ids or pair[1] not in domains or pair in pairs:
                    raise ValueError("unknown or duplicate attribution recipient")
                pairs.add(pair)
                fractions.append(_number(attr["fraction"], positive=True))
            if not math.isclose(math.fsum(fractions), 1., rel_tol=0, abs_tol=1e-12):
                raise ValueError("attributions must conserve one record's credit")
        if kind == "eligibility":
            if event["domain"] not in domains or type(event["eligible"]) is not bool:
                raise ValueError("eligibility needs a known domain and Boolean value")
    return tape


def replay(tape):
    """Return exact-policy counterfactuals conditional on the supplied tape.

    Validation may inspect future event syntax, but allocation reads only the
    prefix at query time. No outcome from another policy feeds the tape.
    """
    validate_tape(tape)
    members = {m["member_id"]: {"active": True, "appointments": dict(m["appointment_days"])}
               for m in tape["members"]}
    claims, observations, cursor = {}, [], 0
    for day in tape["query_days"]:
        while cursor < len(tape["events"]) and tape["events"][cursor]["day"] <= day:
            event = tape["events"][cursor]
            kind = event["kind"]
            if kind == "record":
                cid = event["claim_id"]
                if cid in claims:
                    raise ValueError("duplicate claim record")
                claims[cid] = {"record": event, "confirmed_day": None, "revoked": False}
            elif kind in ("confirm", "revoke"):
                claim = claims.get(event["claim_id"])
                if claim is None:
                    raise ValueError("confirmation/revocation before a known record")
                if kind == "confirm":
                    if claim["confirmed_day"] is not None or claim["revoked"]:
                        raise ValueError("duplicate confirmation or confirmation after revocation")
                    claim["confirmed_day"] = event["day"]
                else:
                    if claim["confirmed_day"] is None or claim["revoked"]:
                        raise ValueError("revocation requires an unrevoked confirmed record")
                    claim["revoked"] = True
            elif kind == "exit":
                member = members[event["member_id"]]
                if not member["active"]:
                    raise ValueError("duplicate exit")
                member["active"] = False
            else:
                member = members[event["member_id"]]
                domain = event["domain"]
                if not member["active"] or (domain in member["appointments"]) == event["eligible"]:
                    raise ValueError("eligibility transition is redundant or follows exit")
                if event["eligible"]:
                    member["appointments"][domain] = event["day"]
                else:
                    del member["appointments"][domain]
            cursor += 1
        credits = {d: {} for d in tape["domains"]}
        pending, confirmed, revoked = 0, 0, 0
        for claim in claims.values():
            if claim["revoked"]:
                revoked += 1
                continue
            if claim["confirmed_day"] is None:
                pending += 1
                continue
            confirmed += 1
            record = claim["record"]
            half = tape["half_life_days"]
            decay = 1. if half is None else 2. ** (-(day-record["earned_day"])/half)
            for attr in record["attributions"]:
                bucket = credits[attr["domain"]].setdefault(attr["member_id"], [])
                value = record["credit"] * attr["fraction"] * decay
                if record["credit"] > 0 and value == 0:
                    raise ValueError("positive decayed credit underflow; refusing a false all-zero fallback")
                bucket.append(value)
        for domain in tape["domains"]:
            eligible = sorted(who for who, m in members.items()
                              if m["active"] and domain in m["appointments"])
            score = [math.fsum(credits[domain].get(who, [])) for who in eligible]
            if any(not math.isfinite(x) for x in score):
                raise ValueError("aggregate confirmed credit overflow")
            tenure = [day-members[who]["appointments"][domain] for who in eligible]
            policies = {}
            if eligible:
                shares = {"equal": authority(score, 0, zero_policy="equal"),
                          "linear": authority(score, 1, zero_policy="equal"),
                          "sublinear": authority(score, tape["alpha"], zero_policy="equal"),
                          "performance_tiers": tier_authority(score, tape["tier_weights"]),
                          "tenure_tiers": tier_authority(tenure, tape["tier_weights"])}
                for policy, weights in shares.items():
                    policies[policy] = {"shares": dict(zip(eligible, weights)),
                                        "formal_share_gini": gini(weights),
                                        "tv_from_linear": total_variation(weights, shares["linear"])}
            else:
                policies = {p: {"shares": {}, "formal_share_gini": None, "tv_from_linear": None} for p in POLICIES}
            observations.append({"day": day, "domain": domain, "eligible_count": len(eligible),
                                 "status": "allocated" if eligible else "suspended_no_eligible",
                                 "confirmed_decayed_credit": dict(zip(eligible, score)),
                                 "appointment_tenure_calendar_days": dict(zip(eligible, tenure)),
                                 "observed_record_counts_all_domains": {"pending": pending, "confirmed": confirmed, "revoked": revoked},
                                 "policies": policies})
    return {"protocol": VERSION, "evidence_kind": "fixed_exogenous_authority_replay",
            "behavior_simulated": False, "new_formal_abm_worlds": 0,
            "decay_anchor": "earned_day", "observation_order": "end_of_day_after_declared_sequence",
            "appointment_regrant": "tenure_restarts_at_grant_day",
            "zero_credit_rule": "equal_among_eligible_ABM_convention",
            "roster_count": len(members), "event_count": len(tape["events"]),
            "observations": observations}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def load_tape(path):
    with Path(path).open("rb") as source:
        data = source.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds16MiB")
    tape = json.loads(data, object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON constant")))
    return tape, hashlib.sha256(data).hexdigest()


def write_new(path, result):
    """Publish a complete fsynced output atomically; never replace an old file."""
    target = Path(path)
    data = (json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode()
    if len(data) > MAX_OUTPUT_BYTES:
        raise ValueError("output exceeds64MiB; no partial result was published")
    fd, temporary = tempfile.mkstemp(prefix=".fixed-replay-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, target)
    finally:
        os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tape", required=True)
    parser.add_argument("--output", required=True, help="fresh JSON file in an existing directory")
    args = parser.parse_args(argv)
    tape, input_sha = load_tape(args.tape)
    started = time.monotonic()
    result = replay(tape)
    result["provenance"] = {"input_sha256": input_sha,
                            "replay_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                            "authority_source_sha256": hashlib.sha256((Path(__file__).parents[1]/"dams_sim/authority.py").read_bytes()).hexdigest()}
    # Runtime belongs in an observer receipt, not the deterministic result bytes.
    write_new(args.output, result)
    print(json.dumps({"status": "completed_fixed_exogenous_replay", "roster_count": result["roster_count"],
                      "event_count": result["event_count"], "replay_and_publication_wall_seconds": time.monotonic()-started,
                      "new_formal_abm_worlds": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
