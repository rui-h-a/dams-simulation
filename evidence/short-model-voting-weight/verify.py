"""Recompute short-model turnout and unexercised formal weight."""
from collections import defaultdict
import csv
from hashlib import sha256
from io import StringIO
import json
from math import isclose, sqrt
from pathlib import Path
from statistics import fmean, stdev

WORLD_IDS = range(1000, 1032)
POLICIES = ("equal", "linear", "sublinear", "hierarchy", "hierarchy_tenure")
DAYS = range(60)
DATA_SHA256 = "2998ba6f1f6ffc1e85154f46dbe740b30c8f0dc409daee3efee3772482f49eab"
T_975_DF31 = 2.039513446396408


def describe(values):
    center = fmean(values)
    half = T_975_DF31 * stdev(values) / sqrt(len(values))
    return {"mean": round(center, 10), "paired_95": [round(center-half, 10), round(center+half, 10)]}


def main():
    raw = Path(__file__).with_name("daily-voting.csv").read_bytes()
    if sha256(raw).hexdigest() != DATA_SHA256:
        raise ValueError("Input digest mismatch: daily-voting.csv")
    reader = csv.reader(StringIO(raw.decode()))
    if next(reader) != ["world", "policy", "day", "attendance", "unexercised_formal_weight"]:
        raise ValueError("Unexpected input columns")
    cells = defaultdict(dict)
    for world, policy, day, attendance, weight in reader:
        values = (float(attendance), float(weight))
        key, day = (int(world), policy), int(day)
        if day in cells[key] or not all(0 <= value <= 1 for value in values):
            raise ValueError(f"Invalid daily observation: {key}, {day}")
        if policy == "equal" and not isclose(values[1], 1-values[0], abs_tol=1e-15):
            raise ValueError("Unexercised weight must equal the absent fraction under equal shares")
        cells[key][day] = values
    if cells.keys() != {(world, policy) for world in WORLD_IDS for policy in POLICIES}:
        raise ValueError("The dataset must cover five policies across 32 matched worlds")
    if any(set(values) != set(DAYS) for values in cells.values()):
        raise ValueError("The dataset must cover all 60 days for every arm")
    totals = {key: tuple(fmean(days[d][i] for d in DAYS) for i in (0, 1)) for key, days in cells.items()}
    endpoints = ("attendance", "unexercised_formal_weight")
    means = {p: {endpoint: round(100*fmean(totals[w,p][i] for w in WORLD_IDS), 10)
                 for i, endpoint in enumerate(endpoints)} for p in POLICIES}
    contrasts = {}
    for a, b in (("hierarchy_tenure", "hierarchy"), ("sublinear", "linear"), ("sublinear", "hierarchy")):
        contrasts[f"{a}_minus_{b}"] = {
            endpoint: describe([100*(totals[w,a][i]-totals[w,b][i]) for w in WORLD_IDS])
            for i, endpoint in enumerate(endpoints)
        }
    result = dict(worlds=len(WORLD_IDS), arms=len(cells), daily_observations=sum(map(len,cells.values())),
                  means_percent=means, contrasts_percentage_points=contrasts)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
