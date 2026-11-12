"""Recompute paired end-of-day backlog comparisons from retained stocks."""
from fractions import Fraction
from hashlib import sha256
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


def main():
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
    results = []
    for counterparty in (ARMS[0], ARMS[1], ARMS[3]):
        pairs = [(world["arms"]["dams-witness"], world["arms"][counterparty]) for world in worlds]
        results.append(compare(pairs, counterparty, days))
    print(json.dumps(dict(year_days=days, comparisons=results), indent=2))


if __name__ == "__main__":
    main()
