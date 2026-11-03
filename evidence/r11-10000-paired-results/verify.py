"""Verify the paired summary of the retained ten-world enterprise study."""

import json
import math
from pathlib import Path
from statistics import fmean, stdev

ROOT = Path(__file__).resolve().parent
T_CRITICAL_95_DF9 = 2.262157162798205
# Two-sided Bonferroni family of four endpoints, with nine degrees of freedom.
T_CRITICAL_BONFERRONI_DF9 = 3.110934823179468
PRIMARY_OUTCOMES = {
    "net_operating_result_per_member_year",
    "decision_regret_per_available_member_hour",
    "governance_member_hours_per_member_year",
    "witness_resource_units_per_member_year",
}


def read_json(name):
    return json.loads((ROOT / name).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected):
    return math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)


def verify_interval(interval, mean, deviation, count, critical, label):
    if deviation == 0:
        require(interval is None, f"Degenerate interval mismatch: {label}")
        return
    half_width = critical * deviation / math.sqrt(count)
    require(
        interval is not None and len(interval) == 2
        and close(interval[0], mean - half_width)
        and close(interval[1], mean + half_width),
        f"Interval mismatch: {label}",
    )


def main():
    roster = read_json("roster.json")
    pooled = read_json("pooled.json")
    expected_worlds = list(range(1000, 1010))
    require(
        roster["assigned"] == roster["finished"] == pooled["worlds"] == expected_worlds
        and not roster["missing"],
        "The roster must contain all ten assigned worlds.",
    )

    comparisons = pooled["comparisons"]
    require(
        set(comparisons) == {"control-process", "dams-witness", "linear-witness"},
        "Unexpected intervention arms.",
    )
    figure = read_json("figure.json")
    plotted = figure["arms"]
    require(figure["reference"] == pooled["reference"], "Reference arm mismatch.")
    require(figure["K"] == len(PRIMARY_OUTCOMES) and set(plotted) == set(comparisons),
            "Primary outcome family or arm mismatch.")
    count = 0
    primary_count = 0
    for arm, outcomes in comparisons.items():
        require(len(outcomes) == 25, f"Incomplete outcomes for {arm}.")
        require(set(plotted[arm]) == PRIMARY_OUTCOMES and PRIMARY_OUTCOMES <= outcomes.keys(),
                f"Incomplete primary outcomes for {arm}.")
        for outcome, result in outcomes.items():
            label = f"{arm}/{outcome}"
            pairs = result["world_values"]
            require([row[0] for row in pairs] == expected_worlds, f"Roster mismatch: {label}")
            differences = [intervention - reference for _, intervention, reference in pairs]
            mean = fmean(differences)
            deviation = stdev(differences)
            require(close(result["mean_difference"], mean), f"Mean mismatch: {label}")
            require(close(result["sd_difference"], deviation), f"Standard deviation mismatch: {label}")
            verify_interval(result["ci95"], mean, deviation, len(pairs), T_CRITICAL_95_DF9, label)
            if outcome in PRIMARY_OUTCOMES:
                series = plotted[arm][outcome]
                require(series["differences"] == [[row[0], value] for row, value in zip(pairs, differences)],
                        f"Plot-point mismatch: {label}")
                require(close(series["mean"], mean) and close(series["delta"], result["delta"]),
                        f"Primary mean or threshold mismatch: {label}")
                verify_interval(series["ci95"], mean, deviation, len(pairs), T_CRITICAL_95_DF9, label)
                verify_interval(series.get("ci_bonferroni"), mean, deviation, len(pairs),
                                T_CRITICAL_BONFERRONI_DF9, f"{label} (adjusted)")
                primary_count += 1
            count += 1
    print(f"Verified {count} paired outcome summaries across {len(expected_worlds)} worlds.")
    print(f"Verified plotted differences and adjusted intervals for {primary_count} primary comparisons.")


if __name__ == "__main__":
    main()
