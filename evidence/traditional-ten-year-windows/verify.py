"""Recompute the observed operating and review-stock contrasts."""
from csv import DictReader
from math import fsum, isfinite, sqrt
from pathlib import Path
from statistics import mean, stdev

WORLDS = range(1000, 1065)
ARMS = ("dams", "hierarchy")
YEARS = range(1, 11)


def load():
    fields = ("member_years", "net_per_member_year", "review_backlog_per_member_end")
    data = {}
    with Path(__file__).with_name("annual-arm-aggregates.csv").open(newline="", encoding="utf-8") as file:
        for row in DictReader(file):
            key = int(row["world"]), row["arm"], int(row["adoption_year"])
            values = tuple(float(row[field]) for field in fields)
            if key in data or values[0] <= 0 or values[2] < 0 or not all(map(isfinite, values)):
                raise ValueError(f"Invalid annual aggregate: {key}")
            data[key] = values
    if data.keys() != {(w, a, y) for w in WORLDS for a in ARMS for y in YEARS}:
        raise ValueError("The annual roster is incomplete")
    return data


def operating(data, world, arm, years):
    rows = [data[world, arm, y] for y in years]
    return fsum(exposure * rate for exposure, rate, _ in rows) / fsum(row[0] for row in rows)


def main():
    data = load()
    early, late, stock5, stock10 = [], [], [], []
    for w in WORLDS:
        early.append(operating(data, w, "dams", range(1, 6)) - operating(data, w, "hierarchy", range(1, 6)))
        late.append(operating(data, w, "dams", range(6, 11)) - operating(data, w, "hierarchy", range(6, 11)))
        stock5.append(data[w, "dams", 5][2] - data[w, "hierarchy", 5][2])
        stock10.append(data[w, "dams", 10][2] - data[w, "hierarchy", 10][2])
    improvement = [later - earlier for earlier, later in zip(early, late, strict=True)]
    half_width = 1.9977296543176928 * stdev(improvement) / sqrt(len(improvement))
    center = mean(improvement)
    actual = tuple(round(mean(values), 1) for values in (early, late, improvement, stock5, stock10))
    interval = (round(center - half_width, 1), round(center + half_width, 1))
    signs = (sum(x > 0 for x in late), sum(x < 0 for x in late))
    if actual != (-34.3, -10.1, 24.1, 77.3, 156.9) or interval != (19.2, 29.0) or signs != (20, 45):
        raise ValueError("Published five-year window comparison differs")
    print("Verified operating gaps, the interval on their change, late-window signs and year-end review stocks.")


if __name__ == "__main__":
    main()
