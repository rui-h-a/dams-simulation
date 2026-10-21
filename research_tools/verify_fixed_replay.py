"""Independently check one fixed-tape observation; no simulation imports.

Example: python3 -B -m research_tools.verify_fixed_replay --tape input.json
    --result result.json --day 365 --output NEW_CHECK.json

The checker reconstructs the entire eligible credit/tenure and five-policy
vectors from records, confirmations, revocations and membership events. It
checks only the requested existing observation day, not behavioural or empirical
validity. Public receipts contain basenames and hashes, never machine identity.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import stat
import tempfile

POLICIES = ("equal", "linear", "sublinear", "performance_tiers", "tenure_tiers")
MAX_INPUT_BYTES = 16 * 1024**2
MAX_RESULT_BYTES = 64 * 1024**2
SHARE_ABS_TOL = 1e-14
REL_TOL = 1e-12
CREDIT_ABS_TOL = 1e-12


def require(condition):
    if not condition:
        raise ValueError("independent verification condition failed")


def number(value, *, positive=False):
    require(type(value) in (int, float))
    require(math.isfinite(value) and (value > 0 if positive else value >= 0))
    return float(value)


def integer(value, *, minimum=0):
    require(type(value) is int and minimum <= value <= 10**7)
    return value


def name(value):
    require(isinstance(value, str) and 0 < len(value) <= 128)
    return value


def keys(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields.split()))


def unique_object(pairs):
    out = {}
    for k, v in pairs:
        require(k not in out)
        out[k] = v
    return out


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def load_one(path, limit):
    """One bounded file read through EOF; identities stay private to the process."""
    path = Path(path)
    before = identity(path.stat())
    require(before[2] <= limit)
    with path.open("rb") as source:
        require(stat.S_ISREG(os.fstat(source.fileno()).st_mode))
        require(identity(os.fstat(source.fileno())) == before)
        raw = source.read(limit + 1)
        require(len(raw) == before[2] and len(raw) <= limit)
        require(identity(os.fstat(source.fileno())) == before)
    require(identity(path.stat()) == before)
    value = json.loads(raw, object_pairs_hook=unique_object,
                       parse_constant=lambda _: require(False))
    observation = {"basename": path.name, "bytes": len(raw),
                   "sha256": hashlib.sha256(raw).hexdigest()}
    return value, observation, before


def normalized(values):
    total = math.fsum(values.values())
    require(math.isfinite(total))
    if not values:
        return {}
    return {k: v / total if total else 1 / len(values) for k, v in values.items()}


def ranked_tiers(scores, weights):
    """Rank positions define weights; entire exact-score tie blocks share them."""
    if not scores:
        return {}
    ordered = sorted(scores, key=scores.__getitem__)
    raw, start = {}, 0
    for _, people in itertools.groupby(ordered, key=scores.__getitem__):
        group = list(people)
        stop = start + len(group)
        average = math.fsum(weights[(r * len(weights)) // len(scores)]
                            for r in range(start, stop)) / len(group)
        raw.update(dict.fromkeys(group, average))
        start = stop
    return normalized(raw)


def vector_check(actual, expected, *, absolute):
    require(isinstance(actual, dict) and set(actual) == set(expected))
    errors = []
    for person, value in expected.items():
        observed = number(actual[person])
        require(math.isclose(observed, value, rel_tol=REL_TOL, abs_tol=absolute))
        errors.append(abs(observed - value))
    return max(errors, default=0.0)


def verify(tape, result, day):
    """Pure independent arithmetic; neither producer nor authority is imported."""
    integer(day)
    keys(tape, "schema_version time_unit alpha half_life_days tier_weights domains members events query_days")
    require(type(tape["schema_version"]) is int and tape["schema_version"] == 1)
    require(tape["time_unit"] == "calendar_day")
    alpha = number(tape["alpha"])
    require(0 < alpha < 1)
    half = tape["half_life_days"]
    if half is not None:
        half = number(half, positive=True)
    weights = tape["tier_weights"]
    require(isinstance(weights, list) and 1 <= len(weights) <= 64)
    weights = [number(w, positive=True) for w in weights]
    require(weights == sorted(weights))
    domains = tape["domains"]
    require(isinstance(domains, list) and 1 <= len(domains) <= 64)
    for d in domains:
        name(d)
    require(len(set(domains)) == len(domains))
    queries = tape["query_days"]
    require(isinstance(queries, list) and 1 <= len(queries) <= 256)
    for t in queries:
        integer(t)
    require(queries == sorted(set(queries)) and day in queries)
    roster = tape["members"]
    require(isinstance(roster, list) and 1 <= len(roster) <= 100000)
    members = {}
    for person in roster:
        keys(person, "member_id appointment_days")
        who = name(person["member_id"])
        require(who not in members)
        appointments = person["appointment_days"]
        require(isinstance(appointments, dict) and set(appointments) <= set(domains))
        for t in appointments.values():
            require(integer(t, minimum=-10**7) <= 0)
        members[who] = {"active": True, "appointments": dict(appointments)}
    events = tape["events"]
    require(isinstance(events, list) and len(events) <= 1000000)
    extra = {"record": "claim_id earned_day credit attributions", "confirm": "claim_id",
             "revoke": "claim_id", "exit": "member_id",
             "eligibility": "member_id domain eligible"}
    records, confirmed, revoked, ids, last = {}, set(), set(), set(), (-1, -1)
    for event in events:
        require(isinstance(event, dict) and isinstance(event.get("kind"), str))
        kind = event["kind"]
        require(kind in extra)
        keys(event, "event_id day sequence kind " + extra[kind])
        eid = name(event["event_id"])
        order = (integer(event["day"]), integer(event["sequence"]))
        require(eid not in ids and order > last and event["day"] <= queries[-1])
        ids.add(eid)
        last = order
        if "claim_id" in event:
            name(event["claim_id"])
        if kind == "record":
            require(integer(event["earned_day"]) <= event["day"])
            number(event["credit"])
            parts = event["attributions"]
            require(isinstance(parts, list) and len(parts) > 0)
            pairs, fractions = set(), []
            for part in parts:
                keys(part, "member_id domain fraction")
                who, domain = name(part["member_id"]), name(part["domain"])
                require(who in members and domain in domains and (who, domain) not in pairs)
                pairs.add((who, domain))
                fractions.append(number(part["fraction"], positive=True))
            require(math.isclose(math.fsum(fractions), 1, rel_tol=0, abs_tol=1e-12))
        if "member_id" in event:
            require(name(event["member_id"]) in members)
        if kind == "eligibility":
            require(name(event["domain"]) in domains and type(event["eligible"]) is bool)
        if event["day"] > day:
            continue  # Future syntax is checked; future states never enter this allocation.
        if kind == "record":
            require(event["claim_id"] not in records)
            records[event["claim_id"]] = event
        elif kind == "confirm":
            cid = event["claim_id"]
            require(cid in records and cid not in confirmed and cid not in revoked)
            confirmed.add(cid)
        elif kind == "revoke":
            cid = event["claim_id"]
            require(cid in confirmed and cid not in revoked)
            revoked.add(cid)
        elif kind == "exit":
            require(members[event["member_id"]]["active"])
            members[event["member_id"]]["active"] = False
        else:
            member = members[event["member_id"]]
            appointments = member["appointments"]
            require(member["active"] and (event["domain"] in appointments) != event["eligible"])
            if event["eligible"]:
                appointments[event["domain"]] = event["day"]
            else:
                del appointments[event["domain"]]
    credits = {d: {} for d in domains}
    for cid in sorted(confirmed - revoked):
        record = records[cid]
        decay = 1.0 if half is None else 2 ** (-(day - record["earned_day"]) / half)
        for part in record["attributions"]:
            value = record["credit"] * part["fraction"] * decay
            require(math.isfinite(value) and (record["credit"] == 0 or value > 0))
            credits[part["domain"]].setdefault(part["member_id"], []).append(value)
    require(isinstance(result, dict) and result.get("protocol") == "fixed-contribution-replay-1")
    require(result.get("behavior_simulated") is False and type(result.get("new_formal_abm_worlds")) is int
            and result["new_formal_abm_worlds"] == 0)
    require(result.get("decay_anchor") == "earned_day"
            and result.get("observation_order") == "end_of_day_after_declared_sequence"
            and result.get("appointment_regrant") == "tenure_restarts_at_grant_day"
            and result.get("zero_credit_rule") == "equal_among_eligible_ABM_convention")
    require(type(result.get("roster_count")) is int and result["roster_count"] == len(roster)
            and type(result.get("event_count")) is int and result["event_count"] == len(events))
    rows = result.get("observations")
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows))
    selected_rows = [row for row in rows if row.get("day") == day]
    require(len(selected_rows) == len(domains)
            and {row.get("domain") for row in selected_rows} == set(domains))
    counts = {"pending": len(records) - len(confirmed),
              "confirmed": len(confirmed - revoked), "revoked": len(revoked)}
    findings = []
    for domain in domains:
        row = next(row for row in selected_rows if row["domain"] == domain)
        eligible = sorted(who for who, m in members.items()
                          if m["active"] and domain in m["appointments"])
        C = {who: math.fsum(credits[domain].get(who, [])) for who in eligible}
        require(all(math.isfinite(c) for c in C.values()))
        tenure = {who: day - members[who]["appointments"][domain] for who in eligible}
        require(type(row.get("eligible_count")) is int and row["eligible_count"] == len(eligible))
        require(row.get("status") == ("allocated" if eligible else "suspended_no_eligible"))
        actual_counts = row.get("observed_record_counts_all_domains")
        require(isinstance(actual_counts, dict) and set(actual_counts) == set(counts))
        require(all(type(actual_counts[k]) is int and actual_counts[k] == v for k, v in counts.items()))
        credit_error = vector_check(row.get("confirmed_decayed_credit"), C, absolute=CREDIT_ABS_TOL)
        actual_tenure = row.get("appointment_tenure_calendar_days")
        require(isinstance(actual_tenure, dict) and set(actual_tenure) == set(tenure)
                and all(type(actual_tenure[k]) is int and actual_tenure[k] == v for k, v in tenure.items()))
        expected = {"equal": dict.fromkeys(eligible, 1 / len(eligible)) if eligible else {},
                    "linear": normalized(C), "sublinear": normalized({k: v**alpha for k, v in C.items()}),
                    "performance_tiers": ranked_tiers(C, weights), "tenure_tiers": ranked_tiers(tenure, weights)}
        actual_policies = row.get("policies")
        require(isinstance(actual_policies, dict) and set(actual_policies) == set(POLICIES))
        selected = sorted(set(eligible[:3] + eligible[len(eligible)//2:len(eligible)//2+1] + eligible[-3:]))
        policies = {}
        for policy, wanted in expected.items():
            allocation = actual_policies[policy]
            require(isinstance(allocation, dict))
            error = vector_check(allocation.get("shares"), wanted, absolute=SHARE_ABS_TOL)
            total = math.fsum(allocation["shares"].values())
            require(math.isclose(total, 1 if eligible else 0, rel_tol=0, abs_tol=1e-12))
            if not eligible:
                require(allocation.get("formal_share_gini") is None and allocation.get("tv_from_linear") is None)
            policies[policy] = {"sum": total, "max_all_member_absolute_error": error,
                                "selected_members": {k: {"expected": wanted[k], "observed": allocation["shares"][k]}
                                                     for k in selected}}
        findings.append({"domain": domain, "eligible_count": len(eligible),
                         "all_member_credit_max_error": credit_error,
                         "confirmed_credit_total_eligible": math.fsum(C.values()),
                         "record_counts_all_domains": counts, "policies": policies})
    return {"status": "verified_requested_day", "day": day,
            "scope": "full eligible C, tenure and five share vectors at one existing observation day; no behavioural or empirical validation",
            "new_formal_abm_worlds": 0,
            "comparison_tolerances": {"relative": REL_TOL, "share_absolute": SHARE_ABS_TOL,
                                      "credit_absolute": CREDIT_ABS_TOL, "sum_absolute": 1e-12},
            "domains": findings}


def write_new(path, value):
    target = Path(path)
    payload = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    require(len(payload) <= 1024**2)
    descriptor, temporary = tempfile.mkstemp(prefix=".fixed-verify-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, target)
    finally:
        os.unlink(temporary)
    descriptor = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tape", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--day", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        target = Path(args.output)
        if os.path.lexists(target):
            raise FileExistsError("fresh output required")
        source_path = Path(__file__)
        source_before = hashlib.sha256(source_path.read_bytes()).hexdigest()
        tape, tape_observation, tape_identity = load_one(args.tape, MAX_INPUT_BYTES)
        result, result_observation, result_identity = load_one(args.result, MAX_RESULT_BYTES)
        require(isinstance(result.get("provenance"), dict)
                and result["provenance"].get("input_sha256") == tape_observation["sha256"])
        checked = verify(tape, result, args.day)
        require(identity(Path(args.tape).stat()) == tape_identity
                and identity(Path(args.result).stat()) == result_identity)
        require(hashlib.sha256(source_path.read_bytes()).hexdigest() == source_before)
        checked["inputs"] = {"tape": tape_observation, "result": result_observation}
        checked["verifier_source_sha256"] = source_before
        write_new(target, checked)
        print(json.dumps({"status": checked["status"], "day": args.day,
                          "new_formal_abm_worlds": 0}))
        return 0
    except Exception as error:
        # No argv, paths, payloads or exception messages in public terminal output.
        print(json.dumps({"status": "verification_failed", "error_class": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
