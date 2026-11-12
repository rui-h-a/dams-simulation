#!/usr/bin/env python3
"""Recompute the enterprise main-study backlog comparisons from retained daily data."""

import csv
from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean, stdev


ROOT = Path(__file__).resolve().parent
WORLDS = range(1000, 1010)
HIERARCHY = "control-hierarchy"
DAMS = "dams-witness"
LINEAR = "linear-witness"
ARMS = (HIERARCHY, "control-process", DAMS, LINEAR)
ROSTER = [(world, arm) for world in WORLDS for arm in ARMS]
START = date(2020, 1, 1)
MILESTONES = {
    "pre_adoption": date(2021, 12, 31),
    "year_one": date(2022, 12, 31),
    "year_three": date(2024, 12, 31),
    "before_tail": date(2026, 11, 1),
    "end": date(2026, 12, 31),
}
DAY_COUNT = (MILESTONES["end"] - START).days + 1
TERMINAL_SHA256 = "eaff24a4232c6c03558a275900dcfc7d7a3e1083451c228c304863d15812ed46"
FILES = ("README.md", "daily.csv.gz", "results.json", "terminal.csv", "verify.py")
FIELDS = (
    "world", "arm", "timeseries_sha256", "day", "calendar_date",
    "work_creation_enabled", "review_backlog_records",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_terminal():
    path = ROOT / "terminal.csv"
    require(sha256(path) == TERMINAL_SHA256, "Published terminal file digest differs")
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    require([(int(row["world"]), row["arm"]) for row in rows] == ROSTER,
            "Unexpected published terminal roster")
    return rows


def read_daily(terminal):
    milestones = {day: name for name, day in MILESTONES.items()}
    dates = [START + timedelta(days=day) for day in range(DAY_COUNT)]
    records = {}
    with gzip.open(ROOT / "daily.csv.gz", "rt", encoding="utf-8", newline="") as stream:
        rows = csv.DictReader(stream)
        require(tuple(rows.fieldnames or ()) == FIELDS, "Unexpected daily columns")
        for (world, arm), final in zip(ROSTER, terminal, strict=True):
            values = {}
            for day, calendar_day in enumerate(dates):
                row = next(rows, None)
                label = f"{world}/{arm}/day {day}"
                require(row is not None and row["world"] == str(world) and row["arm"] == arm,
                        f"Missing or reordered daily record at {label}")
                require(row["timeseries_sha256"] == final["daily_series_sha256"],
                        f"Original daily series binding differs at {label}")
                require(row["day"] == str(day) and row["calendar_date"] == calendar_day.isoformat(),
                        f"Calendar differs at {label}")
                require(row["work_creation_enabled"] == str(calendar_day <= MILESTONES["before_tail"]),
                        f"Work creation flag differs at {label}")
                stock = int(row["review_backlog_records"])
                require(stock >= 0, f"Negative backlog at {label}")
                if calendar_day in milestones:
                    values[milestones[calendar_day]] = stock
            require(values["end"] == int(final["last_review_backlog_records"]),
                    f"Published terminal backlog differs for {world}/{arm}")
            require(values["before_tail"] > 0, f"Zero denominator for {world}/{arm}")
            records[world, arm] = values
        require(next(rows, None) is None, "Unexpected extra daily record")
    return records


def calculate(records):
    for world in WORLDS:
        require(len({records[world, arm]["pre_adoption"] for arm in ARMS}) == 1,
                f"Pre-adoption stock differs within world {world}")
    gaps = {key: [records[w, DAMS][key] - records[w, HIERARCHY][key] for w in WORLDS]
            for key in ("year_one", "year_three", "end")}
    end_gap = mean(gaps["end"])
    half_width = 2.2621571628540993 * stdev(gaps["end"]) / len(WORLDS) ** 0.5
    tail_declines = {}
    for arm in ARMS:
        values = [records[world, arm] for world in WORLDS]
        tail_declines[arm] = round(mean(100 * (v["before_tail"] - v["end"]) / v["before_tail"]
                                        for v in values), 6)
    linear_gaps = [records[w, DAMS]["end"] - records[w, LINEAR]["end"] for w in WORLDS]
    return {
        "daily_records": len(ROSTER) * DAY_COUNT,
        "tail_days": (MILESTONES["end"] - MILESTONES["before_tail"]).days,
        "shared_pre_adoption_mean": mean(records[w, HIERARCHY]["pre_adoption"] for w in WORLDS),
        "dams_minus_hierarchy_mean": {key: mean(values) for key, values in gaps.items()},
        "mean_tail_decline_percent": tail_declines,
        "terminal_means": {arm: mean(records[w, arm]["end"] for w in WORLDS) for arm in ARMS},
        "dams_minus_hierarchy_terminal_interval_95": [round(end_gap - half_width, 6),
                                                     round(end_gap + half_width, 6)],
        "dams_minus_linear_terminal_range": [min(linear_gaps), max(linear_gaps)],
    }


def main():
    expected_hashes = "".join(f"{sha256(ROOT / name)}  {name}\n" for name in FILES)
    require((ROOT / "SHA256SUMS").read_text(encoding="utf-8") == expected_hashes,
            "Packet digest check failed")
    results = calculate(read_daily(read_terminal()))
    expected = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
    require(expected == results, "Stored estimates differ from the daily recomputation")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
