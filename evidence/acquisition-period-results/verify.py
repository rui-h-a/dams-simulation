"""Recalculate the operating results from the retained acquisition aggregates."""
import csv
from hashlib import sha256
from io import StringIO
import json
from math import fsum, isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev

WORLDS = range(1000, 1120)
MODES = ("preserve", "centralize")
ARMS = ("hierarchy", "dams")
PERIODS = ("pre_adoption", "adoption_before_closing", "integration", "post_integration")
FIELDS = ("world", "mode", "arm", "period", "active_member_calendar_days",
          "revenue_resource_units", "operating_resource_units")
DATA_SHA256 = "a69e1c66a09b86561337a4c9be3f78f85d783600adccee895ebf53dd5bc401df"
T_CRITICAL_119 = 1.980099876456940


def load_period_totals():
    raw = Path(__file__).with_name("arm-periods.csv").read_bytes()
    if sha256(raw).hexdigest() != DATA_SHA256:
        raise ValueError("arm-periods.csv differs from the released data")
    reader = csv.reader(StringIO(raw.decode()))
    if tuple(next(reader)) != FIELDS:
        raise ValueError("Unexpected data columns")
    rows = {}
    for world, mode, arm, period, days, revenue, expenses in reader:
        key = (int(world), mode, arm, period)
        values = (int(days), float(revenue), float(expenses))
        if key in rows or values[0] <= 0 or any(not isfinite(value) for value in values):
            raise ValueError(f"Invalid arm-period row: {key}")
        rows[key] = values
    expected = {(w, m, a, p) for w in WORLDS for m in MODES for a in ARMS for p in PERIODS}
    if rows.keys() != expected:
        raise ValueError("The input must include all four arms and periods in each world")
    return rows


def net_rate(totals):
    days, revenue, expenses = totals
    return (revenue - expenses) / (days / 365.25)


def describe(values):
    mean = fmean(values)
    half = T_CRITICAL_119 * stdev(values) / sqrt(len(values))
    return {"mean": round(mean, 8), "interval_95": [round(mean - half, 8), round(mean + half, 8)],
            "negative_worlds": sum(value < 0 for value in values)}


def paired(rows, periods):
    values = {name: [] for name in (*MODES, "central_minus_autonomy", "mode_hierarchy", "mode_dams")}
    for world in WORLDS:
        rates = {
            (mode, arm): net_rate(tuple(map(fsum, zip(
                *(rows[(world, mode, arm, period)] for period in periods), strict=True))))
            for mode in MODES for arm in ARMS
        }
        preserve = rates["preserve", "dams"] - rates["preserve", "hierarchy"]
        central = rates["centralize", "dams"] - rates["centralize", "hierarchy"]
        values["preserve"].append(preserve)
        values["centralize"].append(central)
        values["central_minus_autonomy"].append(central - preserve)
        for arm in ARMS:
            values["mode_" + arm].append(rates["centralize", arm] - rates["preserve", arm])
    return {label: describe(series) for label, series in values.items()}


def main():
    rows = load_period_totals()
    pre_integration_match = all(
        rows[world, "preserve", arm, period] == rows[world, "centralize", arm, period]
        for world in WORLDS for arm in ARMS for period in PERIODS[:3]
    )
    result = {
        "worlds": len(WORLDS),
        "retained_arms": len(rows) // len(PERIODS),
        "mode_totals_identical_before_integration": pre_integration_match,
        "full_period": paired(rows, PERIODS),
        **{period: paired(rows, (period,)) for period in PERIODS},
    }
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
