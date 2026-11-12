"""Recompute lower-half formal-share summaries and a paired interval."""
import csv
from hashlib import sha256
from io import StringIO
import json
from math import sqrt
from pathlib import Path
from statistics import fmean, stdev

WORLD_IDS = list(range(1000, 1032))
POLICIES = ("equal", "linear", "sublinear", "hierarchy", "hierarchy_tenure")
DATA_SHA256 = "2b1da98302c427bbf54470b02349210341bea87eeb01af432d93ad5bf32d44f6"
T_975_DF31 = 2.039513446396408


def main():
    raw = Path(__file__).with_name("world-half-shares.csv").read_bytes()
    if sha256(raw).hexdigest() != DATA_SHA256:
        raise ValueError("Input digest mismatch: world-half-shares.csv")
    reader = csv.DictReader(StringIO(raw.decode()))
    if reader.fieldnames != ["world", *POLICIES]:
        raise ValueError("Unexpected input columns")
    rows = list(reader)
    if [int(row["world"]) for row in rows] != WORLD_IDS:
        raise ValueError("Unexpected world roster")
    shares = {policy: [float(row[policy]) for row in rows] for policy in POLICIES}
    if any(not 0 <= value <= .5 for series in shares.values() for value in series):
        raise ValueError("Invalid lower-half share")
    if shares["hierarchy"] != shares["hierarchy_tenure"]:
        raise ValueError("The tier results must coincide")
    delta = [100 * (d - l) for d, l in zip(shares["sublinear"], shares["linear"], strict=True)]
    center = fmean(delta)
    width = T_975_DF31 * stdev(delta) / sqrt(len(delta))
    result = dict(worlds=len(rows), mean_percent={p: round(100 * fmean(s), 10) for p, s in shares.items()},
                  dams_minus_linear_percentage_points=dict(mean=round(center, 10),
                      paired_95=[round(center - width, 10), round(center + width, 10)],
                      positive_worlds=sum(d > 0 for d in delta)))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
