"""Recompute five short-model policy contrasts against linear credit."""
import csv
from hashlib import sha256
from io import StringIO
import json
from math import isfinite, sqrt
from pathlib import Path
from statistics import NormalDist, fmean, stdev

DATA_SHA256 = "f90836ed0ac63f5b0e95152fa2b91beb571ae4d9c1fc2671095f3722e4d39921"
POLICIES = ("equal", "linear", "sublinear", "hierarchy", "hierarchy_tenure")
WORLDS = range(1000, 1032)
EXPOSURE = 120 * 60
CRITERION = 0.02
Z = NormalDist().inv_cdf(0.975)


def main():
    data = Path(__file__).with_name("world-work.csv").read_bytes()
    if sha256(data).hexdigest() != DATA_SHA256:
        raise ValueError("Input digest mismatch: world-work.csv")
    rows = csv.DictReader(StringIO(data.decode()))
    if rows.fieldnames != ["world", "policy", "produced_work_units"]:
        raise ValueError("Unexpected input columns")
    values = {}
    for row in rows:
        key = (int(row["world"]), row["policy"])
        work = float(row["produced_work_units"])
        if key in values or not isfinite(work) or work < 0:
            raise ValueError(f"Invalid output for {key}")
        values[key] = work / EXPOSURE
    if values.keys() != {(w, p) for w in WORLDS for p in POLICIES}:
        raise ValueError("Expected five policies in each of 32 matched worlds")
    effects = {}
    for policy in POLICIES:
        delta = [values[w, policy] - values[w, "linear"] for w in WORLDS]
        mean = fmean(delta)
        se = stdev(delta) / sqrt(len(delta))
        effects[policy] = {key: round(1000*value, 10) for key, value in
                           (("mean", mean), ("se", se), ("low", mean-Z*se), ("high", mean+Z*se))}
    print(json.dumps(dict(worlds=len(WORLDS), arms=len(values),
                          units="work units per 1,000 member-days",
                          primary_criterion=1000*CRITERION,
                          effects=effects), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
