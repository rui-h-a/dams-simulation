"""Recompute slope sensitivity and operating-gap components from retained startup outcomes."""

import csv
from hashlib import sha256
import json
from math import isclose, log10, sqrt
from pathlib import Path
import runpy
from statistics import mean, stdev


ROOT = Path(__file__).resolve().parent
INPUT_SHA256 = {
    "world-endpoints.csv": "f8a2725ca6a294f8931db90c1e1ab8f7af4346d2f00ff28da164d01a9a8dca44",
    "verify.py": "60c51bd2b2babfa13d9ecd59523775c8b49f4e7e8d7af32dbafc13f48ce2f57f",
}
COMPONENTS = ("change_in_rate_gap", "change_in_mean_exposure", "change_in_between_arm_exposure_term")


def slope_refits(sizes, worlds, rates, interval, critical):
    weight = {n: len(worlds) / stdev(rates[n, w] for w in worlds) ** 2 for n in sizes}
    log_size = {n: log10(n) for n in sizes}
    reports = {}
    for omitted in (None, *sizes):
        included = [n for n in sizes if n != omitted]
        weighted_mean = sum(weight[n] * log_size[n] for n in included) / sum(weight[n] for n in included)
        sum_squares = sum(weight[n] * (log_size[n] - weighted_mean) ** 2 for n in included)
        coefficients = {n: weight[n] * (log_size[n] - weighted_mean) / sum_squares for n in included}
        slopes = {str(w): sum(coefficients[n] * rates[n, w] for n in included) for w in worlds}
        reports["all_sizes" if omitted is None else str(omitted)] = {
            "slopes_by_world": slopes, "mean_slope": mean(slopes.values()),
            "paired_t95": interval(list(slopes.values()), critical),
        }
    return reports


def total_components(sizes, worlds, endpoints, exposures, rules):
    steps = []
    family = len(COMPONENTS) * (len(sizes) - 1)
    critical = rules["t4_critical"](0.05 / family)
    for left, right in zip(sizes, sizes[1:]):
        increment = right - left
        rows, totals = {}, []
        for world in worlds:
            points = []
            for size in (left, right):
                dams_key, hierarchy_key = (size, world, "witness"), (size, world, "hierarchy")
                dams_rate, dams_total = endpoints[dams_key]
                hierarchy_rate, hierarchy_total = endpoints[hierarchy_key]
                dams_years, hierarchy_years = exposures[dams_key], exposures[hierarchy_key]
                points.append((
                    dams_rate - hierarchy_rate,
                    (dams_years + hierarchy_years) / 2,
                    (dams_rate + hierarchy_rate) / 2 * (dams_years - hierarchy_years),
                    dams_total - hierarchy_total,
                ))
            (old_gap, old_exposure, old_imbalance, old_total), (gap, exposure, imbalance, total) = points
            values = ((gap - old_gap) * (exposure + old_exposure) / (2 * increment),
                      (exposure - old_exposure) * (gap + old_gap) / (2 * increment),
                      (imbalance - old_imbalance) / increment)
            observed = (total - old_total) / increment
            if not isclose(sum(values), observed, abs_tol=1e-9):
                raise ValueError(f"Operating total does not reconcile at {left} to {right}, world {world}")
            rows[str(world)] = dict(zip(COMPONENTS, values, strict=True))
            totals.append(observed)
        statistics = {}
        for key in COMPONENTS:
            sample = [row[key] for row in rows.values()]
            statistics[key] = {"mean": mean(sample), "adjusted_95": rules["interval"](sample, critical)}
        steps.append({"initial_members_from": left, "initial_members_to": right, "total_change": mean(totals),
                      "components": statistics, "by_world": rows})
    return {"family_size": family, "size_steps": steps}


def display_precision(value):
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, list):
        return [display_precision(item) for item in value]
    if isinstance(value, dict):
        return {key: display_precision(item) for key, item in value.items()}
    return value


def main():
    for name, expected in INPUT_SHA256.items():
        if sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Published input differs: {name}")
    rules = runpy.run_path(str(ROOT / "verify.py"))
    csv_path = ROOT / "world-endpoints.csv"
    endpoints = rules["load_endpoints"](csv_path)
    sizes, worlds = rules["SIZES"], rules["WORLDS"]
    rates = {(n, w): endpoints[n, w, "witness"][0] - endpoints[n, w, "hierarchy"][0]
             for n in sizes for w in worlds}
    with csv_path.open(newline="", encoding="utf-8") as stream:
        exposures = {(int(row["initial_members"]), int(row["world"]), row["arm"]):
                     float(row["active_member_calendar_days"]) / 365.25 for row in csv.DictReader(stream)}
    result = display_precision({
        "slope_refits": slope_refits(sizes, worlds, rates, rules["interval"], rules["t4_critical"](0.05)),
        "operating_total_components": total_components(sizes, worlds, endpoints, exposures, rules),
    })
    expected = json.loads((ROOT / "expected-results.json").read_text(encoding="utf-8"))
    if result != expected:
        raise ValueError("Recomputed extensions differ from the retained results")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
