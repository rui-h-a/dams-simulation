"""Verify the paired summary of the retained ten-world enterprise study."""

import json
import math
from pathlib import Path
from statistics import fmean, stdev

ROOT = Path(__file__).resolve().parent
T_CRITICAL_95_DF9 = 2.262157162798205


def read_json(name):
    return json.loads((ROOT / name).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(actual, expected):
    return math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)


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
    count = 0
    for arm, outcomes in comparisons.items():
        require(len(outcomes) == 25, f"Incomplete outcomes for {arm}.")
        for outcome, result in outcomes.items():
            label = f"{arm}/{outcome}"
            pairs = result["world_values"]
            require([row[0] for row in pairs] == expected_worlds, f"Roster mismatch: {label}")
            differences = [intervention - reference for _, intervention, reference in pairs]
            mean = fmean(differences)
            deviation = stdev(differences)
            require(close(result["mean_difference"], mean), f"Mean mismatch: {label}")
            require(close(result["sd_difference"], deviation), f"Standard deviation mismatch: {label}")
            interval = result["ci95"]
            if deviation == 0:
                require(interval is None, f"Degenerate interval mismatch: {label}")
            else:
                half_width = T_CRITICAL_95_DF9 * deviation / math.sqrt(len(pairs))
                require(
                    interval is not None and len(interval) == 2
                    and close(interval[0], mean - half_width)
                    and close(interval[1], mean + half_width),
                    f"Interval mismatch: {label}",
                )
            count += 1
    print(f"Verified {count} paired outcome summaries across {len(expected_worlds)} worlds.")


if __name__ == "__main__":
    main()
