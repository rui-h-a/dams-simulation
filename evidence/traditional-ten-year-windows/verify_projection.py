"""Recompute the retrospective ten-year operating projection diagnostics."""
from collections import Counter
from math import fsum, sqrt
from pathlib import Path
from runpy import run_path
from statistics import mean, stdev

window_check = run_path(str(Path(__file__).with_name("verify.py")))
WORLDS = window_check["WORLDS"]
load = window_check["load"]
operating = window_check["operating"]

RULES = ("persistence", "years_2_to_5", "linear_trend")


def projections(data, world, arm):
    early = operating(data, world, arm, range(1, 6))
    early_exposure = fsum(data[world, arm, year][0] for year in range(1, 6))
    future_exposure = 5 * data[world, arm, 5][0]
    rates = [data[world, arm, year][1] for year in range(2, 6)]
    middle = mean(rates)
    # For years 2–5, centered at 3.5, the sum of squared offsets is 5.
    slope = fsum((year - 3.5) * rate for year, rate in zip(range(2, 6), rates, strict=True)) / 5
    # The average of a line evaluated at years 6–10 is its value at year 8.
    future = dict(zip(RULES, (early, middle, middle + 4.5 * slope), strict=True))
    full = {rule: (early_exposure * early + future_exposure * rate) / (early_exposure + future_exposure)
            for rule, rate in future.items()}
    return full, future


def rmse(errors):
    return sqrt(mean(error ** 2 for error in errors))


def main():
    data = load()
    cases = []
    for world in WORLDS:
        dams = projections(data, world, "dams")
        hierarchy = projections(data, world, "hierarchy")
        whole = operating(data, world, "dams", range(1, 11)) - operating(data, world, "hierarchy", range(1, 11))
        late = operating(data, world, "dams", range(6, 11)) - operating(data, world, "hierarchy", range(6, 11))
        whole_predictions, late_predictions = (
            {rule: d[rule] - h[rule] for rule in RULES} for d, h in zip(dams, hierarchy, strict=True)
        )
        cases.append((whole, late, whole_predictions, late_predictions))
    whole_errors = {rule: [full[rule] - whole for whole, _, full, _ in cases] for rule in RULES}
    late_errors = {rule: [predicted[rule] - actual for _, actual, _, predicted in cases] for rule in RULES}

    if tuple(round(rmse(whole_errors[r]), 1) for r in RULES) != (15.6, 10.0, 27.5):
        raise ValueError("Ten-year projection errors differ")
    if round(mean(whole_errors[RULES[1]]), 1) != 2.8 or round(stdev(row[0] for row in cases), 1) != 13.9:
        raise ValueError("Ten-year projection bias or observed variation differs")

    for criterion in (whole_errors, late_errors):
        selections = Counter()
        errors = []
        signs = Counter()
        for index, (_, actual, _, predicted) in enumerate(cases):
            rule = min(RULES, key=lambda r: fsum(e ** 2 for j, e in enumerate(criterion[r]) if j != index))
            selections[rule] += 1
            errors.append(predicted[rule] - actual)
            if predicted[rule] * actual > 0:
                signs["negative" if actual < 0 else "positive"] += 1
        if selections != {RULES[1]: 65} or round(rmse(errors), 1) != 20.0:
            raise ValueError("Held-out rule selection or error differs")
        if signs != {"negative": 32, "positive": 8}:
            raise ValueError("Held-out sign classification differs")

    if sum(row[1] < 0 for row in cases) != 45 or round(rmse(-row[1] for row in cases), 1) != 24.3:
        raise ValueError("Reference late-period predictions differ")
    print("Verified ten-year projection errors and both leave-one-world-out criteria.")


if __name__ == "__main__":
    main()
