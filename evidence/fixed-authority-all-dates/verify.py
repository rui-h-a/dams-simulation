"""Independently verify the released six-date fixed-evidence allocation.

Only reads the frozen input and result once each. No model or producer imports.
"""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
from itertools import groupby
import json
from pathlib import Path
import sys

COMMIT = "ed82a5817158d735b4878998e55cb312b1576b40"
DAYS = (0, 1, 30, 90, 180, 365)
DOMAINS = ("d0", "d1")
POLICIES = ("equal", "linear", "sublinear", "performance_tiers", "tenure_tiers")
CREDIT_REL = Decimal("2e-15")
SHARE_REL = Decimal("1e-14")
SUMMARY_ABS = Decimal("2e-14")
SUM_ABS = Decimal("1e-14")


def require(valid, label):
    if not valid:
        raise ValueError(str(label))


def unique_json(raw):
    def object_from_pairs(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs), "duplicate JSON keys")
        return result

    def reject_nonfinite(value):
        raise ValueError(f"nonfinite JSON numeric literal {value}")

    return json.loads(raw, object_pairs_hook=object_from_pairs, parse_float=Decimal,
                      parse_constant=reject_nonfinite)


def read_frozen(path, entry):
    def fingerprint(stat):
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    before = fingerprint(path.stat())
    require(before[2] == entry["bytes"], f"{path.name} unexpected file length")
    with path.open("rb") as file:
        import os
        require(fingerprint(os.fstat(file.fileno())) == before, f"{path.name} open identity")
        content = file.read(entry["bytes"] + 1)
        require(fingerprint(os.fstat(file.fileno())) == before, f"{path.name} read identity")
    require(fingerprint(path.stat()) == before, f"{path.name} final identity")
    sha = hashlib.sha256(content).hexdigest()
    require(len(content) == entry["bytes"] and sha == entry["sha256"],
            f"{path.name} uncompressed byte count/SHA-256")
    return unique_json(content)


def decimal_ratio(value):
    return Decimal(value.numerator) / Decimal(value.denominator)


def normalize(weights):
    total = sum(weights)
    return [v / total for v in weights] if total else [Decimal(1) / len(weights)] * len(weights)


def tier_shares(scores, weights):
    n = len(scores)
    # Recover positional sums with a prefix integral. Tied people receive one
    # average across the complete rank interval occupied by their score.
    bounds = [0]
    for rank in range(n):
        bounds.append(bounds[-1] + weights[rank * len(weights) // n])
    output = [None] * n
    start = 0
    for _, group in groupby(sorted(range(n), key=scores.__getitem__), key=scores.__getitem__):
        block = tuple(group)
        stop = start + len(block)
        share = Decimal(bounds[stop] - bounds[start]) / len(block) / bounds[n]
        for idx in block:
            output[idx] = share
        start = stop
    return output


def allocations(amounts, appointments, tiers):
    scale_free = [decimal_ratio(value) for value in amounts]
    return {
        "equal": [Decimal(1) / len(amounts)] * len(amounts),
        "linear": normalize(scale_free),
        "sublinear": normalize([value.sqrt() for value in scale_free]),
        "performance_tiers": tier_shares(amounts, tiers),
        "tenure_tiers": tier_shares(appointments, tiers),
    }


def gini(shares):
    # Each sorted element's gap from its predecessors contributes once.
    prefix = gap = Decimal(0)
    for index, share in enumerate(sorted(shares)):
        gap += index * share - prefix
        prefix += share
    return gap / (prefix * len(shares))


def index_events(tape):
    # An immutable temporal index queried by date, independent of the
    # producer's mutable per-query replay state.
    histories = {}
    exits = {}
    claims = {}
    for member in tape["members"]:
        name = member["member_id"]
        require(name not in histories, "duplicate roster member")
        initial = member["appointment_days"]
        require(all(k in DOMAINS and type(v) is int and v <= 0
                    for k, v in initial.items()), "invalid initial appointment")
        histories[name] = {d: [(0, initial.get(d))] for d in DOMAINS}
    stamp = (-1, -1)
    ids = set()
    for event in tape["events"]:
        day, seq = event["day"], event["sequence"]
        require(type(day) is int and type(seq) is int and 0 <= day <= DAYS[-1] and seq >= 0
                and stamp < (day, seq), "invalid event chronology")
        stamp = (day, seq)
        event_id = event["event_id"]
        require(event_id not in ids, "duplicate event")
        ids.add(event_id)
        match event["kind"]:
            case "record":
                claim = event["claim_id"]
                quantity = Fraction(event["credit"])
                require(claim not in claims and event["earned_day"] == 0 and quantity >= 0,
                        "invalid released record")
                attrs = {}
                for attr in event["attributions"]:
                    who, domain = attr["member_id"], attr["domain"]
                    frac = Fraction(attr["fraction"])
                    require(who in histories and domain in DOMAINS and (who, domain) not in attrs
                            and frac > 0, "invalid attribution")
                    attrs[who, domain] = frac
                require(sum(attrs.values()) == 1, "attribution fractions must sum to 1")
                claims[claim] = {"recorded": day, "confirmed": None, "revoked": None,
                                 "amounts": {k: quantity * v for k, v in attrs.items()}}
            case "confirm":
                claim = claims[event["claim_id"]]
                require(claim["confirmed"] is None, "duplicate confirmation")
                claim["confirmed"] = day
            case "revoke":
                claim = claims[event["claim_id"]]
                require(claim["confirmed"] is not None and claim["revoked"] is None,
                        "invalid revocation")
                claim["revoked"] = day
            case "eligibility":
                who, domain, granted = event["member_id"], event["domain"], event["eligible"]
                changes = histories[who][domain]
                require(who not in exits and type(granted) is bool and
                        (changes[-1][1] is None) == granted, "invalid eligibility transition")
                changes.append((day, day if granted else None))
            case "exit":
                who = event["member_id"]
                require(who in histories and who not in exits, "invalid exit")
                exits[who] = day
            case _:
                raise ValueError("unknown event kind")
    return histories, exits, tuple(claims.values())


def scores_at(claims, day):
    amounts = defaultdict(Fraction)
    counts = dict.fromkeys(("pending", "confirmed", "revoked"), 0)
    for claim in claims:
        recorded, confirmed, revoked = (claim[k] for k in ("recorded", "confirmed", "revoked"))
        if recorded > day:
            continue
        if confirmed is None or confirmed > day:
            counts["pending"] += 1
        elif revoked is not None and revoked <= day:
            counts["revoked"] += 1
        else:
            counts["confirmed"] += 1
            for pair, value in claim["amounts"].items():
                amounts[pair] += value
    return amounts, counts


def appointment_at(history, day):
    return next(start for when, start in reversed(history) if when <= day)


class Errors:
    def __init__(self):
        self.maximum = defaultdict(lambda: Decimal(0))
        self.tests = Counter()
        self.exact_binary64 = Counter()

    def check(self, kind, observed, expected, location, relative=None, absolute=None):
        require(type(observed) in (int, Decimal), (location, kind, "invalid observed type"))
        error = abs(observed - expected)
        limit = (abs(expected) * relative) if relative is not None else absolute
        require(error <= limit, (location, kind, "numeric discrepancy", str(error), str(limit)))
        self.maximum[kind] = max(self.maximum[kind], error)
        self.tests[kind] += 1
        self.exact_binary64[kind] += float(observed) == float(expected)


def verify(tape, result, manifest, summary):
    require(tape["schema_version"] == 1 and tape["time_unit"] == "calendar_day"
            and tuple(tape["query_days"]) == DAYS and tuple(tape["domains"]) == DOMAINS
            and tape["alpha"] == Decimal(".5") and tape["half_life_days"] == 180
            and tape["tier_weights"] == [1, 2, 4, 8], "released protocol configuration")
    require(len(tape["members"]) == result["roster_count"] == manifest["roster"] == 10000
            and len(tape["events"]) == result["event_count"] == manifest["events"] == 20403,
            "released roster and event totals")
    require(result["behavior_simulated"] is False and result["new_formal_abm_worlds"] == 0
            and result["protocol"] == "fixed-contribution-replay-1"
            and result["decay_anchor"] == "earned_day"
            and result["observation_order"] == "end_of_day_after_declared_sequence"
            and result["appointment_regrant"] == "tenure_restarts_at_grant_day"
            and result["zero_credit_rule"] == "equal_among_eligible_ABM_convention",
            "released result metadata")
    require(result["provenance"] == {
        "input_sha256": manifest["assets"]["input.json"]["sha256"],
        "replay_source_sha256": manifest["source_pins"]["research_tools/fixed_contribution_replay.py"],
        "authority_source_sha256": manifest["source_pins"]["dams_sim/authority.py"],
    }, "input and implementation provenance")
    observations = {(row["day"], row["domain"]): row for row in result["observations"]}
    summaries = {(row["day"], row["domain"]): row for row in summary["rows"]}
    expected_grid = {(t, d) for t in DAYS for d in DOMAINS}
    require(observations.keys() == summaries.keys() == expected_grid and
            len(result["observations"]) == len(summary["rows"]) == len(expected_grid), "exact day/domain grid")

    histories, exits, claims = index_events(tape)
    errors = Errors()
    rows = []
    for day in DAYS:
        amounts, counts = scores_at(claims, day)
        factor = Decimal(2) ** (-Decimal(day) / tape["half_life_days"])
        for domain in DOMAINS:
            appointments = {
                who: day - start for who, domains in histories.items()
                if (who not in exits or day < exits[who]) and
                (start := appointment_at(domains[domain], day)) is not None
            }
            ordered = tuple(sorted(appointments))
            n = len(ordered)
            row = observations[day, domain]
            condensed = summaries[day, domain]
            require(n and row["status"] == "allocated" and
                    row["eligible_count"] == condensed["eligible_count"] == n,
                    (day, domain, "allocation status or eligible count"))
            require(counts == row["observed_record_counts_all_domains"] == condensed["record_counts"],
                    (day, domain, "global record counts"))
            observed_appointments = row["appointment_tenure_calendar_days"]
            require(appointments == observed_appointments and
                    all(type(x) is int for x in observed_appointments.values()),
                    (day, domain, "complete eligible IDs and exact appointment vector"))
            unaged = [amounts[who, domain] for who in ordered]
            credits = row["confirmed_decayed_credit"]
            require(credits.keys() == appointments.keys(), (day, domain, "complete credit vector keys"))
            for who, amount in zip(ordered, unaged, strict=True):
                errors.check("credit", credits[who], decimal_ratio(amount) * factor,
                             (day, domain, who), relative=CREDIT_REL)
            expected = allocations(unaged, [appointments[who] for who in ordered], tape["tier_weights"])
            require(row["policies"].keys() == condensed["policies"].keys() == set(POLICIES),
                    (day, domain, "policy names"))
            for policy, shares in expected.items():
                got = row["policies"][policy]
                observed_shares = got["shares"]
                require(observed_shares.keys() == appointments.keys(),
                        (day, domain, policy, "complete share vector keys"))
                for who, share in zip(ordered, shares, strict=True):
                    errors.check("share", observed_shares[who], share, (day, domain, policy, who),
                                 relative=SHARE_REL)
                errors.check("share_sum", sum(observed_shares.values()), Decimal(1),
                             (day, domain, policy), absolute=SUM_ABS)
                tv = sum((max(a - b, Decimal(0)) for a, b in zip(shares, expected["linear"], strict=True)))
                for key, metric in (("formal_share_gini", gini(shares)), ("tv_from_linear", tv)):
                    require(got[key] == condensed["policies"][policy][key],
                            (day, domain, policy, key, "full result vs summary"))
                    errors.check(key, got[key], metric, (day, domain, policy), absolute=SUMMARY_ABS)
            rows.append({"day": day, "domain": domain, "eligible_members": n, "global_record_counts": counts})
    return {
        "observations": rows,
        "eligible_rows_with_exact_ids_and_appointment_values": sum(row["eligible_members"] for row in rows),
        "numerical_comparison_counts": dict(errors.tests),
        "comparisons_which_round_to_identical_binary64": dict(errors.exact_binary64),
        "max_absolute_errors": {key: str(value) for key, value in errors.maximum.items()},
    }


def smoke_test():
    weights = allocations(list(map(Fraction, [1, 1, 4, 9])), [4, 3, 2, 1], [1, 2, 4, 8])
    require(weights["linear"] == [Decimal(x) / 15 for x in [1, 1, 4, 9]], "linear smoke test")
    require(weights["sublinear"] == [Decimal(x) / 7 for x in [1, 1, 2, 3]], "sqrt smoke test")
    require(weights["performance_tiers"] == [Decimal(x) / 30 for x in [3, 3, 8, 16]],
            "rank tie crossing boundary smoke test")
    require(weights["tenure_tiers"] == [Decimal(x) / 15 for x in [8, 4, 2, 1]],
            "appointment smoke test")
    require(gini([Decimal(1), Decimal(0)]) == Decimal(".5"), "Gini smoke test")
    try:
        Errors().check("share", Decimal("1e-19"), Decimal(0), "fake nonzero", relative=SHARE_REL)
    except ValueError:
        return
    raise ValueError("comparator failed to detect an invalid positive share")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pinned", type=Path, help="root of the pinned original source checkout")
    parser.add_argument("--identity", type=Path, default=Path(__file__).with_name("identity.json"),
                        help="pinned Git and release identities (default: alongside this script)")
    parser.add_argument("--tape", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    with localcontext() as ctx:
        ctx.prec = 64
        smoke_test()
        if args.smoke:
            print("Six smoke checks passed.")
            return
        require(all((args.pinned, args.tape, args.result, args.output)), "missing required paths")
        require(not args.output.exists(), "receipt path already exists")
        identity = unique_json(args.identity.read_bytes())
        require(identity["commit"] == identity["release"]["targetCommitish"] == COMMIT, "fixed Git revision")
        pinned = {}
        for row in identity["git_objects"]:
            name = Path(row["path"]).name
            content = (args.pinned / row["path"]).read_bytes()
            require(len(content) == row["bytes"] and hashlib.sha256(content).hexdigest() == row["sha256"]
                    and hashlib.sha1(f"blob {len(content)}\0".encode()+content).hexdigest() == row["sha1"],
                    f"pinned Git source identity for {name}")
            pinned[name] = content
        manifest = unique_json(pinned["manifest.json"])
        protocol = unique_json(pinned["protocol.json"])
        require(protocol["source_pins"] == manifest["source_pins"] and
                hashlib.sha256(pinned["summary.json"]).hexdigest() == manifest["summary_sha256"],
                "protocol and condensed summary hashes")
        for source, expected in manifest["source_pins"].items():
            require(hashlib.sha256(pinned[Path(source).name]).hexdigest() == expected, "frozen source hash")
        for asset in manifest["assets"].values():
            release_asset, = [a for a in identity["release"]["assets"] if a["name"] == asset["gzip_asset"]]
            require(release_asset["size"] == asset["gzip_bytes"]
                    and release_asset["digest"] == "sha256:" + asset["gzip_sha256"],
                    "release attachment identity")
        tape = read_frozen(args.tape, manifest["assets"]["input.json"])
        result = read_frozen(args.result, manifest["assets"]["result.json"])
        report = verify(tape, result, manifest, unique_json(pinned["summary.json"]))
        report.update({
            "status": "passed",
            "public_commit": COMMIT,
            "release": identity["release"]["tagName"],
            "input_and_result": manifest["assets"],
            "source_pins": manifest["source_pins"],
            "method": ("independent event index with end-of-day queries, exact fractional attribution and tiers, "
                       "64-digit Decimal earned-day decay, square roots, normalized shares, Gini and TV"),
            "tolerances": {"credit_relative": str(CREDIT_REL), "share_relative": str(SHARE_REL),
                           "share_sum_absolute": str(SUM_ABS), "summary_absolute": str(SUMMARY_ABS),
                           "zero_expected_member_credit_and_share": "exact"},
            "new_formal_abm_worlds": 0,
            "model_or_producer_imports": [n for n in sys.modules if n.startswith(("dams_sim", "research_tools"))],
        })
        require(not report["model_or_producer_imports"], "producer import")
        with args.output.open("x") as file:
            json.dump(report, file, indent=2, sort_keys=True)
            file.write("\n")
        print(f"Verified {len(report['observations'])} observations with exact eligibility and appointments.")


if __name__ == "__main__":
    main()
