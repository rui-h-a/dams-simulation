"""Recompute paired end-of-day backlog comparisons from retained stocks."""
import argparse
from csv import DictReader
from datetime import date, timedelta
from fractions import Fraction
import gzip
from hashlib import sha256
from io import BytesIO, TextIOWrapper
import json
from math import sqrt
from pathlib import Path
from statistics import fmean, stdev

INPUT_SHA256 = "25c81d201e2e6b4d30231f5179aa0f8895852f3d1a53b3daa6219993bc8409bd"
ARMS = ("control-hierarchy", "control-process", "dams-witness", "linear-witness")
CRITICAL_T = 2.262157162798205


def estimate(values):
    average = fmean(values)
    half_width = CRITICAL_T * stdev(values) / sqrt(len(values))
    return [round(x, 6) for x in (average, average - half_width, average + half_width)]


def compare(rows, counterparty, year_days):
    annual = []
    for idx, days in enumerate(year_days):
        annual.append(dict(
            mean_daily_records=estimate([
                Fraction(pair[0]["annual_record_days"][idx] - pair[1]["annual_record_days"][idx], days)
                for pair in rows
            ]),
            year_end_records=estimate([
                pair[0]["year_end_records"][idx] - pair[1]["year_end_records"][idx] for pair in rows
            ]),
        ))
    final_window = dict(
        mean_daily_records=estimate([
            Fraction(a["last_60_record_days"] - b["last_60_record_days"], 60) for a, b in rows
        ]),
        change_in_gap=estimate([
            (a["year_end_records"][-1] - a["before_last_60_records"])
            - (b["year_end_records"][-1] - b["before_last_60_records"]) for a, b in rows
        ]),
    )
    return dict(reference=counterparty, annual=annual, last_60_days=final_window)


def check_daily_history(path, data):
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != data["daily_history_sha256"]:
        raise ValueError("Daily history failed SHA-256 verification")
    limits = data["year_start_days"]
    end = limits[-1]
    tail = end - 60
    dates = [(date(2020, 1, 1) + timedelta(days=day)).isoformat() for day in range(end)]
    expected = {(world["world"], name): arm for world in data["worlds"]
                for name, arm in world["arms"].items()}
    stocks = {key: [None] * end for key in expected}
    with TextIOWrapper(gzip.GzipFile(fileobj=BytesIO(raw)), encoding="utf-8", newline="") as stream:
        for row in DictReader(stream):
            key = (int(row["world"]), row["arm"])
            day = int(row["day"])
            value = int(row["review_backlog_records"])
            if (key not in stocks or not 0 <= day < end or stocks[key][day] is not None
                    or value < 0 or row["calendar_date"] != dates[day]
                    or row["work_creation_enabled"] != str(day < tail)
                    or row["timeseries_sha256"] != expected[key]["daily_source_sha256"]):
                raise ValueError(f"Unexpected daily row for {key} on day {day}")
            stocks[key][day] = value
    for key, series in stocks.items():
        if None in series:
            raise ValueError(f"Missing daily rows for {key}")
        arm = expected[key]
        windows = list(zip(limits, limits[1:]))
        if ([sum(series[a:b]) for a, b in windows] != arm["annual_record_days"]
                or [series[b - 1] for _, b in windows] != arm["year_end_records"]
                or sum(series[tail:]) != arm["last_60_record_days"]
                or series[tail - 1] != arm["before_last_60_records"]):
            raise ValueError(f"Daily history differs from annual or final-window totals for {key}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, help="Verify annual totals against the pinned daily.csv.gz")
    args = parser.parse_args()
    raw = Path(__file__).with_name("annual-stocks.json").read_bytes()
    if sha256(raw).hexdigest() != INPUT_SHA256:
        raise SystemExit("annual-stocks.json failed SHA-256 verification")
    data = json.loads(raw)
    worlds = data["worlds"]
    assert [w["world"] for w in worlds] == list(range(1000, 1010))
    assert data["retained_rows"] == 102280
    limits = data["year_start_days"]
    days = [b - a for a, b in zip(limits, limits[1:])]
    assert days == [365, 365, 366, 365, 365] and limits[0] == 731
    for world in worlds:
        assert tuple(world["arms"]) == ARMS
        for arm in world["arms"].values():
            assert len(arm["annual_record_days"]) == len(arm["year_end_records"]) == len(days)
            assert all(n >= 0 for n in (*arm["annual_record_days"], *arm["year_end_records"],
                                        arm["last_60_record_days"], arm["before_last_60_records"]))
    if args.history:
        try:
            check_daily_history(args.history, data)
        except (KeyError, OSError, ValueError) as error:
            raise SystemExit(str(error)) from None
    results = []
    for counterparty in (ARMS[0], ARMS[1], ARMS[3]):
        pairs = [(world["arms"]["dams-witness"], world["arms"][counterparty]) for world in worlds]
        results.append(compare(pairs, counterparty, days))
    print(json.dumps(dict(year_days=days, comparisons=results), indent=2))


if __name__ == "__main__":
    main()
