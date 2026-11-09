"""Recompute retrospective regret contrasts from paired synthetic aggregates."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from math import isclose, isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev

FIELDS = ("total_regret", "completed_decisions", "available_member_hours", "present_member_workdays", "scored_proposals")
ENTERPRISE_ARMS = ("control-hierarchy", "control-process", "dams-witness", "linear-witness")
FROZEN_ARMS = ("startup-existing", "startup-founding")
T_CRITICAL = {66: 1.9971379083920038, 300: 1.96792966906567}
METRICS = {
    "regret_per_1000_scored_proposals": (4, 1_000),
    "regret_per_1000_completed_decisions": (1, 1_000),
    "regret_per_million_available_hours": (2, 1_000_000),
    "regret_per_1000_present_workdays": (3, 1_000),
}

def estimate(values):
    n = len(values)
    mean, sd = fmean(values), stdev(values)
    half = T_CRITICAL[n] * sd / sqrt(n)
    return {"pairs": n, "difference": mean, "sd_difference": sd,
            "interval_95": [mean - half, mean + half],
            "positive_pairs": sum(v > 0 for v in values),
            "zero_pairs": sum(v == 0 for v in values)}

def rows_for(study, worlds, arms, proposals):
    assert [r["world"] for r in study] == list(worlds)
    for row in study:
        assert set(row) == {"world", "arms"}
        assert set(row["arms"]) == set(arms)
        for regret, decisions, hours, days, attempts in row["arms"].values():
            assert isfinite(regret) and regret >= 0
            assert type(decisions) is int and 0 < decisions <= attempts == proposals
            assert type(attempts) is int
            assert isfinite(hours) and hours > 0
            assert type(days) is int and days > 0
    return [r["arms"] for r in study]

def contrast(rows, treatment, reference):
    pairs = [(r[treatment], r[reference]) for r in rows]
    report = {}
    for name, (i, multiplier) in METRICS.items():
        treat = [a[0] / a[i] * multiplier for a, _ in pairs]
        ref = [b[0] / b[i] * multiplier for _, b in pairs]
        result = estimate([a - b for a, b in zip(treat, ref)])
        result["treatment_mean"] = fmean(treat)
        result["reference_mean"] = fmean(ref)
        result["relative_difference_of_means"] = result["difference"] / result["reference_mean"]
        report[name] = result
    scored_loss = []
    decision_frequency = []
    for a, b in pairs:
        q_a, q_b = a[0] / a[4], b[0] / b[4]
        f_a, f_b = a[4] / a[2], b[4] / b[2]
        scored_loss.append((q_a - q_b) * (f_a + f_b) / 2 * 1_000_000)
        decision_frequency.append((f_a - f_b) * (q_a + q_b) / 2 * 1_000_000)
        assert isclose(scored_loss[-1] + decision_frequency[-1],
                       (a[0] / a[2] - b[0] / b[2]) * 1_000_000, rel_tol=1e-11, abs_tol=1e-10)
    report["hourly_difference_components"] = {
        "regret_per_scored_proposal": estimate(scored_loss),
        "scored_proposals_per_available_hour": estimate(decision_frequency),
    }
    report["pairs_with_equal_denominators"] = {
        FIELDS[i]: sum(a[i] == b[i] for a, b in pairs) for i in range(1, 5)
    }
    return report

def recompute(data):
    assert data["schema"] == "dams-startup-regret-aggregates-v1"
    assert tuple(data["arm_fields"]) == FIELDS
    sizes = data["by_initial_size"]
    assert set(sizes) == {"30", "120"}
    output = {}
    for size, first in (("30", 50_000), ("120", 51_000)):
        enterprise = rows_for(sizes[size]["enterprise"], range(1000, 1300), ENTERPRISE_ARMS, 2_526)
        frozen = rows_for(sizes[size]["frozen"], range(first, first + 66), FROZEN_ARMS, 5_196)
        output[size] = {
            "enterprise": {arm: contrast(enterprise, arm, ENTERPRISE_ARMS[0])
                           for arm in ENTERPRISE_ARMS[1:]},
            "frozen": {"startup-founding": contrast(frozen, FROZEN_ARMS[1], FROZEN_ARMS[0])},
        }
        output[size]["enterprise"]["dams-minus-linear-witnessed"] = contrast(
            enterprise, "dams-witness", "linear-witness")
    return output

def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("data", nargs="?", type=Path,
                        default=Path(__file__).with_name("startup-regret-aggregates.json"))
    args = parser.parse_args()
    raw = args.data.read_bytes()
    print(json.dumps({
        "aggregate_sha256": sha256(raw).hexdigest(),
        "scope": "Retrospective paired summaries of synthetic worlds. Intervals are unadjusted Student-t intervals.",
        "by_initial_size": recompute(json.loads(raw)),
    }, indent=2, allow_nan=False))

if __name__ == "__main__":
    main()
