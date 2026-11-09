"""Recompute paired R11 acquisition contrasts from retained synthetic arm aggregates."""
from argparse import ArgumentParser
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import correlation, fmean, stdev

WORLDS = tuple(range(1000, 1120))
MODES = ("preserve", "centralize")
ARMS = ("hierarchy", "dams")
# Two-sided 95% Student-t critical value for 120 paired observations.
T_CRITICAL_DF119 = 1.980099876426006
ENDPOINTS = (
    "net_operating_result_per_member_year",
    "governance_cost_per_member_year",
    "decision_regret_per_available_member_hour",
    "confirmation_mean_days",
    "honest_rejection_share",
    "gini_formal_authority",
    "voluntary_and_other_exits_per_member_year",
    "final_active_members",
)
TABLE_SCALES = (1, 1, 1_000_000, 1, 100, 1, 1_000, 1)
SCIENTIFIC_SOURCE = "93e5b872f351741bc79d22c2d8641e6f64187d0a"

def estimates(values):
    mean = fmean(values)
    sd = stdev(values)
    half = T_CRITICAL_DF119 * sd / math.sqrt(len(values))
    return {"pairs": len(values), "mean_difference": mean,
            "sd_difference": sd, "ci95": [mean - half, mean + half]}

def scaled(estimate, factor):
    return {**estimate, "mean_difference": factor * estimate["mean_difference"],
            "sd_difference": factor * estimate["sd_difference"],
            "ci95": [factor * v for v in estimate["ci95"]]}

def validate(data):
    assert data["schema"] == "dams-r11-acquisition-arm-aggregates-v1"
    assert data["source_commit"] == SCIENTIFIC_SOURCE
    assert tuple(data["endpoints_in_order"]) == ENDPOINTS
    worlds = data["worlds"]
    assert tuple(row["world"] for row in worlds) == WORLDS
    for row in worlds:
        assert set(row) == {"world", *MODES}
        for mode in MODES:
            assert set(row[mode]) == set(ARMS)
            for arm in ARMS:
                values = row[mode][arm]
                assert len(values) == len(ENDPOINTS)
                assert all(type(v) in (int, float) and math.isfinite(v) for v in values)
    return worlds

def recompute(data):
    worlds = validate(data)
    within_mode = {mode: {} for mode in MODES}
    interaction = {}
    mode_effects = {arm: {} for arm in ARMS}
    for i, (endpoint, factor) in enumerate(zip(ENDPOINTS, TABLE_SCALES)):
        ds = {
            mode: [w[mode]["dams"][i] - w[mode]["hierarchy"][i] for w in worlds]
            for mode in MODES
        }
        for mode in MODES:
            within_mode[mode][endpoint] = estimates(ds[mode])
        diff = [b - a for a, b in zip(ds["preserve"], ds["centralize"])]
        interaction_estimate = estimates(diff)
        interaction[endpoint] = {
            "native_units": interaction_estimate,
            "table_units": scaled(interaction_estimate, factor),
            "within_mode_differences_correlation": correlation(ds["preserve"], ds["centralize"]),
        }
        for arm in ARMS:
            changes = [w["centralize"][arm][i] - w["preserve"][arm][i] for w in worlds]
            mode_effects[arm][endpoint] = scaled(estimates(changes), factor)
    return within_mode, interaction, mode_effects

def compare_frozen(rows, r11):
    """Optionally validate the retained within-mode estimates against thesis inputs."""
    mapping = {"preserve": "ma-preserve", "centralize": "ma-centralize"}
    maxima = {"mean_difference": 0.0, "sd_difference": 0.0, "ci95": 0.0}
    checked = 0
    for mode, stem in mapping.items():
        frozen = json.loads((r11 / stem / "inference.json").read_bytes())["comparisons"]["dams-witness"]
        for endpoint in ENDPOINTS:
            got, want = rows[mode][endpoint], frozen[endpoint]
            assert got["pairs"] == want["worlds"] == 120
            for field in maxima:
                gv, wv = got[field], want[field]
                if field != "ci95":
                    gv, wv = [gv], [wv]
                for g, w in zip(gv, wv):
                    assert math.isclose(g, w, rel_tol=1e-8, abs_tol=1e-10), (mode, endpoint, field, g, w)
                    maxima[field] = max(maxima[field], abs(g - w))
                    checked += 1
    return {"endpoint_mode_pairs": len(MODES) * len(ENDPOINTS),
            "scalars_compared": checked, "max_absolute_difference": maxima}

def main():
    p = ArgumentParser(description=__doc__)
    p.add_argument("data", nargs="?", type=Path,
                   default=Path(__file__).with_name("ma-paired-aggregates.json"))
    p.add_argument("--r11", type=Path, help="optional directory containing the frozen inference inputs")
    args = p.parse_args()
    raw = args.data.read_bytes()
    data = json.loads(raw)
    within_mode, interaction, mode_effects = recompute(data)
    report = {
        "aggregate_payload_sha256": sha256(raw).hexdigest(),
        "independent_world_id_count": len(WORLDS),
        "arm_aggregate_count": len(WORLDS) * len(MODES) * len(ARMS),
        "status": "retained synthetic analysis; cross-mode interaction is retrospective",
        "interaction_sign": "(centralize DAMS - centralize hierarchy) - (preserve DAMS - preserve hierarchy)",
        "interval": "paired Student-t 95%, unadjusted; Monte Carlo variation under the simulated design",
        "within_mode_in_native_units": within_mode,
        "retrospective_package_interaction": interaction,
        "retrospective_centralize_minus_preserve_by_arm_in_table_units": mode_effects,
    }
    if args.r11:
        report["frozen_summary_check"] = compare_frozen(within_mode, args.r11)
    print(json.dumps(report, indent=2, allow_nan=False))

if __name__ == "__main__":
    main()
