#!/usr/bin/env python3
"""Recompute exploratory terminal-stock contrasts without running the model."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent
WORLDS = range(1000, 1010)
ARMS = ("control-hierarchy", "control-process", "dams-witness", "linear-witness")
REFERENCE = ARMS[0]
METRICS = ("unfinished_records", "confirmed_records", "confirmation_p95_days")
SOURCE_SHA256 = "76246e9fdddf566e1db24f0d7da58afa4fa2a44a467889340010205a6ddb3f77"
T_95_DF9 = 2.2621571628540993
HASH_COLUMNS = (
    "arm_receipt_sha256",
    "arm_summary_sha256",
    "daily_series_sha256",
    "last_daily_row_sha256",
)
COLUMNS = (
    "world", "arm", "source_sha256", *HASH_COLUMNS, "daily_rows", "last_day",
    "last_review_backlog_records", *METRICS,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_records() -> dict:
    with (ROOT / "terminal.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        check(tuple(reader.fieldnames or ()) == COLUMNS, "Unexpected CSV columns")
        rows = list(reader)
    expected = [(w, a) for w in WORLDS for a in ARMS]
    check([(int(r["world"]), r["arm"]) for r in rows] == expected,
          "Incomplete, duplicate or reordered world-arm roster")
    for col in HASH_COLUMNS:
        check(len({r[col] for r in rows}) == len(expected), f"Repeated {col}")
    values = {}
    for key, row in zip(expected, rows):
        check(row["source_sha256"] == SOURCE_SHA256, f"Unexpected source at {key}")
        check(all(re.fullmatch(r"[0-9a-f]{64}", row[c]) for c in HASH_COLUMNS),
              f"Invalid digest at {key}")
        check(row["daily_rows"] == "2557" and row["last_day"] == "2556",
              f"Unexpected terminal horizon at {key}")
        counts = {metric: int(row[metric]) for metric in METRICS}
        check(all(value >= 0 for value in counts.values()), f"Negative result at {key}")
        check(counts["unfinished_records"] == int(row["last_review_backlog_records"]),
              f"Summary stock and daily terminal stock differ at {key}")
        values[key] = counts
    return values


def calculate() -> dict:
    records = read_records()
    estimates = {}
    for metric in METRICS:
        arms = {arm: [records[w, arm][metric] for w in WORLDS] for arm in ARMS}
        arm_means = {arm: mean(values) for arm, values in arms.items()}
        comparisons = {}
        pairs = [(arm, REFERENCE) for arm in ARMS[1:]] + [("dams-witness", "linear-witness")]
        for arm, ref in pairs:
            differences = [x - y for x, y in zip(arms[arm], arms[ref])]
            estimate = mean(differences)
            half_width = T_95_DF9 * stdev(differences) / len(differences) ** 0.5
            comparisons[f"{arm}_minus_{ref}"] = {
                "mean": estimate,
                "paired_interval_95": [estimate - half_width, estimate + half_width],
                "mean_relative_to_reference": estimate / arm_means[ref],
                "positive_worlds": sum(d > 0 for d in differences),
                "zero_worlds": sum(d == 0 for d in differences),
                "negative_worlds": sum(d < 0 for d in differences),
            }
        estimates[metric] = {"arm_means": arm_means, "comparisons": comparisons}
    return {
        "schema": "dams-r11-terminal-stock-1",
        "source_sha256": SOURCE_SHA256,
        "worlds": list(WORLDS),
        "arms": list(ARMS),
        "rows": len(records),
        "daily_rows_per_arm": 2557,
        "last_day": 2556,
        "interval_method": "exploratory unadjusted paired Student t, df=9",
        "estimates": estimates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-results", action="store_true")
    args = parser.parse_args()
    result = calculate()
    if args.print_results:
        print(json.dumps(result, indent=2))
        return
    expected = (ROOT / "results.json").read_text(encoding="utf-8")
    check(json.loads(expected) == result, "Stored estimates differ from the recomputation")
    filenames = ("README.md", "results.json", "terminal.csv", "verify.py")
    hashes = "".join(f"{hashlib.sha256((ROOT / f).read_bytes()).hexdigest()}  {f}\n"
                     for f in filenames)
    check((ROOT / "SHA256SUMS").read_text(encoding="utf-8") == hashes,
          "Packet digest check failed")
    print("Verified 40 bound arm rows, terminal-stock equality, 12 paired estimates and packet digests.")


if __name__ == "__main__":
    main()
