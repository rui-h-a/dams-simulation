"""Recompute the traditional firm's retained demand-matched comparisons."""

from hashlib import sha256
import json
from math import isclose, isfinite, sqrt
from pathlib import Path
import re
from statistics import fmean, stdev

ROOT = Path(__file__).resolve().parent
ASSIGNED = range(1000, 1065)
ARMS = ("control-hierarchy", "dams-witness")
NET = "net_operating_result_per_member_year"
REGRET = "decision_regret_per_available_member_hour"
CRITICAL = {32: 2.0395134463964077, 33: 2.0369333434601016}
HEX256 = re.compile(r"[a-f0-9]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def interval(values):
    if not values:
        return None
    n = len(values)
    require(n in CRITICAL, f"Unexpected number of available differences: {n}")
    mean = fmean(values)
    half_width = CRITICAL[n] * stdev(values) / sqrt(n)
    return {"worlds": n, "mean": mean, "ci95": [mean - half_width, mean + half_width]}


def number(value):
    return type(value) in (int, float) and isfinite(value)


def validate(data):
    require(data["assigned_worlds"] == list(ASSIGNED), "Assignment differs")
    outcomes = data["units"]
    require(len(outcomes) == 24 and NET in outcomes and REGRET in outcomes, "Outcome roster differs")
    studies = {"baseline": {}, "demand-matched": {}}
    for row in data["arm_rows"]:
        scenario, world, arm = row["scenario"], row["world"], row["arm"]
        require(scenario in studies and world in ASSIGNED and arm in ARMS, "Unexpected arm")
        require(set(row["outcomes"]) == set(outcomes), "Arm outcomes differ")
        require(all(value is None or number(value) for value in row["outcomes"].values()), "Invalid outcome")
        require(all(HEX256.fullmatch(row[key]) for key in
                    ("receipt_sha256", "summary_sha256", "series_sha256", "prehistory_sha256")), "Invalid source digest")
        require(number(row["arrived"]) and number(row["delivered"]) and 0 <= row["delivered"] and 0 < row["arrived"]
                and (row["delivered"] <= row["arrived"] or isclose(row["delivered"], row["arrived"], rel_tol=1e-10)),
                "Invalid delivery totals")
        index = (world, arm)
        require(index not in studies[scenario], "Duplicate arm")
        studies[scenario][index] = row
    completed = sorted({world for world, _ in studies["demand-matched"]})
    missing = sorted(set(ASSIGNED) - set(completed))
    require(len(completed) == 33 and completed == data["completed"] and missing == data["missing"], "Incomplete roster differs")
    for scenario, worlds in (("baseline", ASSIGNED), ("demand-matched", completed)):
        source = studies[scenario]
        require(source.keys() == {(w, arm) for w in worlds for arm in ARMS}, "Unpaired world")
        require(all(source[w, ARMS[0]]["prehistory_sha256"] == source[w, ARMS[1]]["prehistory_sha256"] for w in worlds),
                "Paired arms have different prehistory receipts")
    failures = data["retained_failures"]
    require(len({r["world"] for r in failures}) == len(failures) == 3, "Failure inventory differs")
    require(all(r["world"] in ASSIGNED and r["kind"] == "prehistory" and
                r["retained_pair"] == (r["world"] in completed) and HEX256.fullmatch(r["sha256"]) for r in failures),
            "Inconsistent failure inventory")
    return studies


def difference(source, world, metric):
    values = [source[world, arm]["outcomes"][metric] for arm in ARMS]
    return None if None in values else values[1] - values[0]


def summarize(data):
    studies = validate(data)
    base, variant = studies["baseline"], studies["demand-matched"]
    worlds, missing = data["completed"], data["missing"]
    outcomes = {}
    diagnostics = {}
    for metric, unit in data["units"].items():
        a = [difference(base, w, metric) for w in worlds]
        b = [difference(variant, w, metric) for w in worlds]
        outcomes[metric] = {
            "unit": unit,
            "variant_contrast": interval([v for v in b if v is not None]),
            "change_from_baseline": interval([y - x for x, y in zip(a, b) if x is not None and y is not None]),
        }
        if metric in (NET, REGRET):
            diagnostics[metric] = {
                "baseline_retained": interval(a),
                "baseline_missing": interval([difference(base, w, metric) for w in missing]),
                "missing_world_mean_at_zero_full_mean": -sum(b) / len(missing),
                "retained_sign_counts": {
                    "negative": sum(v < 0 for v in b), "zero": sum(v == 0 for v in b), "positive": sum(v > 0 for v in b),
                },
            }
    delivery = {
        arm: {
            "relative_to_own_baseline": fmean(variant[w, arm]["delivered"] / base[w, arm]["delivered"] for w in worlds),
            "fraction_of_orders": fmean(variant[w, arm]["delivered"] / variant[w, arm]["arrived"] for w in worlds),
        }
        for arm in ARMS
    }
    return {"assigned": len(ASSIGNED), "retained_pairs": len(worlds), "missing_pairs": len(missing),
            "outcomes": outcomes, "diagnostics": diagnostics, "delivery": delivery}


def same(a, b):
    if type(a) != type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, float):
        return isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    return a == b


def main():
    raw = (ROOT / "arm-outcomes.json").read_bytes()
    proof = json.loads((ROOT / "results.json").read_bytes())
    require(sha256(raw).hexdigest() == proof["input_sha256"], "Frozen input digest differs")
    result = summarize(json.loads(raw))
    require(same(result, proof["results"]), "Recomputed outcomes differ")
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    main()
