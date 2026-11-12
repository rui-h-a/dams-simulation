"""Recalculate output recovery from the retained pairs without running the model."""
from collections import Counter
import csv
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

root = Path(__file__).resolve().parent
observations = root / "paired-work.csv"
provenance = root / "provenance.json"

if sha256(provenance.read_bytes()).hexdigest() != "9e7aba95763c0dd05e234b7f1e2b40c6ebe28bfdc2bffbcebafa6e1c31c685f0":
    raise SystemExit("provenance.json SHA-256 mismatch")
source = json.loads(provenance.read_bytes())
if sha256(observations.read_bytes()).hexdigest() != source["paired_work_sha256"]:
    raise SystemExit("paired-work.csv SHA-256 mismatch")

policies = ("equal", "hierarchy", "hierarchy_tenure", "linear", "sublinear")
worlds = range(8100, 8108)
pairs = {(policy, world): [] for policy in policies for world in worlds}
assert {(c["policy"], c["world"], c["shock"]) for c in source["cases"]} == {
    (policy, world, shock) for policy, world in pairs for shock in (False, True)
}
assert len(source["cases"]) == 80

with observations.open(newline="") as stream:
    for row in csv.DictReader(stream):
        daily = pairs[row["policy"], int(row["world"])]
        assert len(daily) == int(row["day"])
        values = tuple(Decimal(row[key]) for key in ("shock_work_units", "control_work_units"))
        assert min(values) > 0
        daily.append(values)

thresholds = ("0.99", "0.995", "1.00")
lag_counts = {t: Counter() for t in thresholds}
immediate_by_rule = {t: Counter() for t in thresholds}
total_differences = {policy: [] for policy in policies}
for (policy, _), values in pairs.items():
    assert len(values) == 60 and all(a == b for a, b in values[:25])
    for label in thresholds:
        limit = Decimal(label)
        start = next(
            (j for j in range(30, 58)
             if all(a >= limit * b for a, b in values[j:j + 3])),
            None,
        )
        lag = None if start is None else start - 30
        lag_counts[label]["unrecovered" if lag is None else str(lag)] += 1
        if lag == 0:
            immediate_by_rule[label][policy] += 1
    total_differences[policy].append(sum(a - b for a, b in values[25:]))

result = dict(
    pairs=len(pairs),
    paired_day_rows=sum(map(len, pairs.values())),
    window_days=3,
    first_eligible_day=30,
    last_eligible_day=57,
    lag_counts=lag_counts,
    immediate_by_rule={
        threshold: {p: counts[p] for p in policies}
        for threshold, counts in immediate_by_rule.items()
    },
    pairs_with_cumulative_deficit_at_day_59=sum(
        d < 0 for differences in total_differences.values() for d in differences
    ),
    mean_cumulative_shock_minus_control_at_day_59={
        policy: str((sum(differences) / len(differences)).quantize(Decimal(".000001")))
        for policy, differences in total_differences.items()
    },
)
print(json.dumps(result, indent=2, sort_keys=True))
