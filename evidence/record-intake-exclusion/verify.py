#!/usr/bin/env python3
"""Recompute the paired submission-exposure comparison without simulation."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean


BACKENDS = ("central", "witness", "consensus")
WORLDS = tuple(range(2100, 2108))
EXPOSURES = (0, 1)
FIELDS = (
    "censored_records",
    "unfinished_records",
    "confirmed_records",
    "confirmation_mean_days",
    "produced_work_units",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_rows(path, provenance):
    expected = {(world, backend, exposure) for world in WORLDS for backend in BACKENDS for exposure in EXPOSURES}
    records = {}
    cases = set()
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            key = int(row["world"]), row["backend"], int(row["exposure"])
            case = row["case"]
            require(key in expected and key not in records, f"Invalid or duplicate case settings: {key}")
            require(case not in cases, f"Duplicate case ID: {case}")
            cases.add(case)
            values = {field: float(row[field]) for field in FIELDS}
            require(all(math.isfinite(v) and v >= 0 for v in values.values()), f"Invalid outcome: {key}")
            require(all(values[field].is_integer() for field in FIELDS[:3]), f"Non-integer count: {key}")
            records[key] = case, values
    require(set(records) == expected and cases == set(provenance["case_bindings"]), "Incomplete case roster")
    return records


def check_source(directory, records, provenance):
    source = provenance["source"]
    for name, digest in source["files_sha256"].items():
        require(sha256(directory / name) == digest, f"Source fingerprint mismatch: {name}")
    statuses = read_json(directory / source["case_status_index"])
    complete = {entry["case"] for entry in statuses if entry["status"] == "complete"}
    configs = {}
    for key, (case, values) in records.items():
        binding = provenance["case_bindings"][case]
        manifest_path = directory / case / "manifest.json"
        summary_path = directory / case / "summary.json"
        require(sha256(manifest_path) == binding["manifest_sha256"], f"Case manifest mismatch: {case}")
        manifest = read_json(manifest_path)
        require(sha256(summary_path) == binding["summary_sha256"] == manifest["output_sha256"]["summary.json"],
                f"Case summary mismatch: {case}")
        require(case in complete and manifest["status"] == "complete" and manifest["source_sha256"] == source["source_sha256"],
                f"Ineligible case: {case}")
        summary = read_json(summary_path)
        require(all(float(summary.get(field, 0)) == values[field] for field in FIELDS), f"Different case outcome: {case}")
        config = manifest["config"]
        world, backend, exposure = key
        require((config["world"], config["backend"], config["administrative_censorship_exposure"]) ==
                (world, backend, [exposure] * 3), f"Different case settings: {case}")
        require(all(config[k] == value for k, value in provenance["design"].items()), f"Different study settings: {case}")
        configs[key] = config
    for world in WORLDS:
        for backend in BACKENDS:
            zero, one = (configs[world, backend, exposure] for exposure in EXPOSURES)
            require(set(zero) == set(one) and [k for k in zero if zero[k] != one[k]] == ["administrative_censorship_exposure"],
                    f"Nonmatched exposure settings: {world}/{backend}")


def result(records, opportunities):
    comparisons = {}
    for backend in BACKENDS:
        series = {exposure: [records[world, backend, exposure][1] for world in WORLDS] for exposure in EXPOSURES}
        means = {str(exposure): {field: fmean(row[field] for row in series[exposure]) for field in FIELDS} for exposure in EXPOSURES}
        contrasts = {}
        for field in FIELDS:
            deltas = [one[field] - zero[field] for zero, one in zip(series[0], series[1])]
            contrasts[field] = {
                "mean": fmean(deltas),
                "lower_in_exposure_one": sum(value < 0 for value in deltas),
                "equal": sum(value == 0 for value in deltas),
                "higher_in_exposure_one": sum(value > 0 for value in deltas),
            }
        comparisons[backend] = {
            "means_by_exposure": means,
            "paired_exposure_one_minus_zero": contrasts,
            "exposure_one_share_of_original_claim_opportunities_confirmed_percent": 100 * means["1"]["confirmed_records"] / opportunities,
            "exposure_one_minus_zero_work_units_per_1000_member_days": 1000 * contrasts["produced_work_units"]["mean"] / opportunities,
        }
    return {
        "worlds_per_backend": len(WORLDS),
        "matched_backend_world_pairs": len(WORLDS) * len(BACKENDS),
        "original_claim_opportunities_per_world": opportunities,
        "pairs_with_lower_unfinished_stock_and_lower_mean_confirmation_delay": sum(
            all(records[world, backend, 1][1][field] < records[world, backend, 0][1][field]
                for field in ("unfinished_records", "confirmation_mean_days"))
            for backend in BACKENDS for world in WORLDS
        ),
        "comparisons": comparisons,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, help="verify the retained ensemble files and bound case summaries")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    provenance = read_json(folder / "provenance.json")
    csv_path = folder / "world-outcomes.csv"
    require(sha256(csv_path) == provenance["portable_input_sha256"], "Portable CSV fingerprint mismatch")
    records = load_rows(csv_path, provenance)
    if args.source_dir:
        check_source(args.source_dir, records, provenance)
    design = provenance["design"]
    print(json.dumps(result(records, design["n"] * design["days"]), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
