#!/usr/bin/env python3
"""Recompute operating totals and the fixed-exposure break-even scenario."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean, stdev


WORLDS = tuple(str(n) for n in range(1000, 1010))
HIERARCHY, DAMS = "control-hierarchy", "dams-witness"
ARMS = (HIERARCHY, DAMS)
YEARS = tuple(range(1, 6))
T9_975 = 2.262157162798205


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(test, message):
    if not test:
        raise ValueError(message)


def load_inputs(path):
    expected = {(world, arm, year) for world in WORLDS for arm in ARMS for year in YEARS}
    inputs = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            key = row["world"], row["arm"], int(row["year_after_adoption"])
            require(key in expected and key not in inputs, f"Unexpected or duplicate row {key}")
            exposure = float(row["member_years"])
            rate = float(row["net_operating_resource_units_per_member_year"])
            require(math.isfinite(exposure) and exposure > 0 and math.isfinite(rate), f"Invalid values {key}")
            inputs[key] = exposure, rate
    require(set(inputs) == expected, "Incomplete set of world, arm and year rows")
    return inputs


def check_source(directory, provenance, inputs):
    for name, digest in provenance["analysis_sha256"].items():
        require(sha256(directory / name) == digest, f"Analysis fingerprint mismatch for {name}")
    manifest = read_json(directory / "input-manifest.json")["main"]
    source = read_json(directory / "independent-net-candidate.json")
    require(source["boundaries"] == provenance["post_adoption_boundaries"], "Different adoption boundaries")
    for world in WORLDS:
        source_record = manifest[world]
        receipt_record = provenance["worlds"][world]
        for key in ("archive_sha256", "prehistory_sha256", "branch_day", "scientific_source_sha256", "simulated_days"):
            require(source_record[key] == receipt_record[key], f"Different {key} in world {world}")
        annual = source["main_10_worlds"][f"dams-a12-n10000-w{world}"]["arms"]
        for arm in ARMS:
            require(source_record["arms"][arm] == receipt_record["arms"][arm], f"Different arm provenance {world}/{arm}")
            for row in annual[arm]:
                key = world, arm, row["year"]
                require(inputs[key] == (row["member_years"], row["net_per_member_year"]), f"Different annual input {key}")


def arm_totals(inputs, world, arm):
    rows = [inputs[world, arm, year] for year in YEARS]
    exposure = math.fsum(person_years for person_years, _ in rows)
    observed = math.fsum(person_years * rate for person_years, rate in rows)
    future_exposure = 5 * rows[-1][0]
    future_rate = fmean(rate for _, rate in rows[1:])
    return observed, exposure, future_exposure, future_rate


def comparison(inputs, world):
    h, h_exposure, h_future_exposure, h_future_rate = arm_totals(inputs, world, HIERARCHY)
    d, d_exposure, d_future_exposure, d_future_rate = arm_totals(inputs, world, DAMS)
    future_h, future_d = h_future_exposure * h_future_rate, d_future_exposure * d_future_rate
    five_year_total = d - h
    future_total = future_d - future_h
    ten_year_total = five_year_total + future_total
    return {
        "world": world,
        "years_1_to_5_total_difference": five_year_total,
        "years_6_to_10_projected_total_difference": future_total,
        "years_1_to_10_projected_total_difference": ten_year_total,
        "years_1_to_5_rate_difference": d / d_exposure - h / h_exposure,
        "years_1_to_10_projected_rate_difference": (d + future_d) / (d_exposure + d_future_exposure)
        - (h + future_h) / (h_exposure + h_future_exposure),
        "years_6_to_10_dams_projected_member_years": d_future_exposure,
        "years_6_to_10_dams_projected_rate": d_future_rate,
        "dams_rate_uplift_required_to_close_world_total_gap": -ten_year_total / d_future_exposure,
    }


def mean_ci(rows, key):
    values = [row[key] for row in rows]
    mean = fmean(values)
    half_width = T9_975 * stdev(values) / math.sqrt(len(values))
    return {"mean": mean, "paired_world_t95": [mean - half_width, mean + half_width]}


def summarize(rows):
    mean_exposure = fmean(row["years_6_to_10_dams_projected_member_years"] for row in rows)
    mean_future_rate = fmean(row["years_6_to_10_dams_projected_rate"] for row in rows)
    ten_year = mean_ci(rows, "years_1_to_10_projected_total_difference")
    uplift = -ten_year["mean"] / mean_exposure
    return {
        "world_count": len(rows),
        "years_1_to_5_total_difference": mean_ci(rows, "years_1_to_5_total_difference"),
        "years_6_to_10_projected_total_difference": mean_ci(rows, "years_6_to_10_projected_total_difference"),
        "years_1_to_10_projected_total_difference": ten_year,
        "years_1_to_5_mean_rate_difference": fmean(row["years_1_to_5_rate_difference"] for row in rows),
        "years_1_to_10_mean_projected_rate_difference": fmean(row["years_1_to_10_projected_rate_difference"] for row in rows),
        "years_6_to_10_dams_mean_projected_member_years": mean_exposure,
        "years_6_to_10_dams_mean_projected_rate": mean_future_rate,
        "common_dams_rate_uplift_required_to_close_mean_total_gap": uplift,
        "common_uplift_percent_of_mean_dams_projected_rate": 100 * uplift / mean_future_rate,
        "world_specific_dams_uplifts": mean_ci(rows, "dams_rate_uplift_required_to_close_world_total_gap"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, help="also verify the retained analysis files and every exported row")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    provenance = read_json(folder / "provenance.json")
    input_file = folder / "annual-inputs.csv"
    require(sha256(input_file) == provenance["inputs_sha256"], "Portable input fingerprint mismatch")
    inputs = load_inputs(input_file)
    if args.source_dir:
        check_source(args.source_dir, provenance, inputs)
    rows = [comparison(inputs, world) for world in WORLDS]
    summary = summarize(rows)
    if args.source_dir:
        projection = read_json(args.source_dir / "projections.json")["horizon"][DAMS]["net_per_member_year"]
        require(math.isclose(summary["years_1_to_5_mean_rate_difference"], projection["five_year"]["mean"], abs_tol=1e-10),
                "Five-year rate does not match the published analysis")
        require(math.isclose(summary["years_1_to_10_mean_projected_rate_difference"], projection["ten_year_projection"]["mean"], abs_tol=1e-10),
                "Ten-year projected rate does not match the published analysis")
    print(json.dumps({"summary": summary, "worlds": rows}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
