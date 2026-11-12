"""Recompute leave-one-world-out precision from the retained paired results."""

from hashlib import sha256
import json
from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev
import sys

WORLDS = tuple(range(1000, 1010))
SOURCE_SHA256 = "9507497f0e295c93fcbf31ed6d29a9a02cb2532dbd10ab63d07973420de1eecc"
ENDPOINTS = {
    "net_operating_result_per_member_year": ("E1", 1),
    "decision_regret_per_available_member_hour": ("E2", 1_000_000),
    "governance_member_hours_per_member_year": ("E3", 1),
    "witness_resource_units_per_member_year": ("E4", 1),
}
# Student-t quantiles for marginal 95% and Bonferroni over four endpoints.
CRITICAL = {9: (2.306004135204166, 3.2059554533229315),
            10: (2.262157162798205, 3.110934823179475)}


def intervals(values, delta):
    n = len(values)
    mean = fmean(values)
    error = stdev(values) / sqrt(n)
    if error <= 0:
        raise ValueError("Degenerate contrast in the retained sample")
    marginal, adjusted = [critical * error for critical in CRITICAL[n]]
    lo, hi = mean - adjusted, mean + adjusted
    return {
        "mean": round(mean, 9),
        "marginal_half_width_over_target": round(marginal / (delta / 2), 9),
        "bonferroni_half_width_over_target": round(adjusted / (delta / 2), 9),
        "marginal_target_met": marginal <= delta / 2,
        "bonferroni_target_met": adjusted <= delta / 2,
        "bonferroni_bounds": [round(lo, 9), round(hi, 9)],
        "bonferroni_relation": (
            "below_negative_threshold" if hi < -delta else
            "above_positive_threshold" if lo > delta else
            "inside_thresholds" if -delta < lo <= hi < delta else "crosses_threshold"
        ),
    }


def main(path):
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("The paired-results input differs from the pinned pooled.json")
    data = json.loads(raw)
    if tuple(data["worlds"]) != WORLDS or data["reference"] != "control-hierarchy":
        raise ValueError("Unexpected reference or world roster")
    comparison = data["comparisons"]["dams-witness"]
    result = {}
    for endpoint, (label, scale) in ENDPOINTS.items():
        record = comparison[endpoint]
        rows = record["world_values"]
        if tuple(row[0] for row in rows) != WORLDS:
            raise ValueError(f"Missing, duplicate or out-of-order world in {label}")
        values = [(arm - reference) * scale for _, arm, reference in rows]
        delta = record["delta"] * scale
        if not all(isfinite(v) for v in values) or not (isfinite(delta) and delta > 0):
            raise ValueError(f"Invalid endpoint data in {label}")
        subsets = {str(world): intervals(values[:i] + values[i + 1:], delta)
                   for i, world in enumerate(WORLDS)}
        result[label] = {
            "delta": round(delta, 9),
            "full_roster": intervals(values, delta),
            "omissions_meeting_marginal_target": sum(row["marginal_target_met"] for row in subsets.values()),
            "omissions_meeting_bonferroni_target": sum(row["bonferroni_target_met"] for row in subsets.values()),
            "omitted_world": subsets,
        }
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 verify.py PATH_TO_POOLED_JSON")
    main(Path(sys.argv[1]))
