"""Recompute post-adoption outstanding stock using matched daily summaries."""

from argparse import ArgumentParser
from csv import DictReader
import gzip
from hashlib import sha256
import json
from math import isclose, sqrt
from pathlib import Path
import re
from statistics import fmean, stdev

ROOT = Path(__file__).resolve().parent
WORLDS = range(1000, 1010)
ARMS = ("control-hierarchy", "control-process", "dams-witness", "linear-witness")
FIRST_DAY, END_DAY = 731, 2557
DAYS = END_DAY - FIRST_DAY
T9_975 = 2.262157162798205
FIELDS = ("world", "arm", "record_days", "member_days", "appeal_days", "timeseries_sha256")
HASH = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load():
    rows = {}
    with (ROOT / "arm-totals.csv").open(newline="", encoding="utf-8") as stream:
        reader = DictReader(stream)
        require(reader.fieldnames == list(FIELDS), "Unexpected CSV fields")
        for row in reader:
            key = int(row["world"]), row["arm"]
            require(key not in rows, "Duplicate world and arm")
            record_days, member_days, appeal_days = (int(row[k]) for k in FIELDS[2:5])
            require(record_days >= 0 and member_days > 0 and appeal_days >= 0, "Invalid arm totals")
            require(HASH.fullmatch(row["timeseries_sha256"]), "Invalid timeseries digest")
            rows[key] = (record_days, member_days, appeal_days, row["timeseries_sha256"])
    require(rows.keys() == {(w, arm) for w in WORLDS for arm in ARMS}, "Incomplete arm roster")
    return rows


def check_history(history, rows, expected):
    require(sha256(history.read_bytes()).hexdigest() == expected, "Pinned history digest differs")
    values = {key: [] for key in rows}
    with gzip.open(history, "rt", encoding="utf-8", newline="") as stream:
        for row in DictReader(stream):
            key = int(row["world"]), row["arm"]
            require(key in rows and row["timeseries_sha256"] == rows[key][-1], "Original timeseries digest differs")
            day = int(row["day"])
            require(day == len(values[key]), "Day missing or repeated")
            values[key].append(int(row["review_backlog_records"]))
    require(all(len(daily) == END_DAY and sum(daily[FIRST_DAY:]) == rows[key][0] for key, daily in values.items()),
            "Daily backlog totals differ")


def interval(values):
    mean = fmean(values)
    half = T9_975 * stdev(values) / sqrt(len(values))
    return {"mean": mean, "ci95": [mean-half, mean+half]}


def summarize(rows):
    means = {}
    for arm in ARMS:
        means[arm] = {
            "mean_daily_backlog": fmean(rows[w,arm][0]/DAYS for w in WORLDS),
            "mean_exposure_weighted_backlog_per_member": fmean(rows[w,arm][0]/rows[w,arm][1] for w in WORLDS),
        }
    contrasts = {}
    for reference in ("control-hierarchy", "control-process", "linear-witness"):
        a = [rows[w,"dams-witness"] for w in WORLDS]
        b = [rows[w,reference] for w in WORLDS]
        totals = [x[0]-y[0] for x,y in zip(a,b)]
        contrasts[f"dams-witness minus {reference}"] = {
            "mean_daily_backlog_difference": interval([n/DAYS for n in totals]),
            "cumulative_record_day_difference": interval(totals),
            "exposure_weighted_backlog_per_member_difference": interval([x[0]/x[1]-y[0]/y[1] for x,y in zip(a,b)]),
        }
    return {"days_after_adoption": DAYS, "means": means, "comparisons": contrasts}


def same(a, b):
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k],b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x,y) for x,y in zip(a,b))
    return isclose(a,b,rel_tol=1e-10,abs_tol=1e-10) if isinstance(a,float) else a == b


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, help="Validate against the pinned daily.csv.gz history")
    args = parser.parse_args()
    proof = json.loads((ROOT / "results.json").read_bytes())
    require(sha256((ROOT / "arm-totals.csv").read_bytes()).hexdigest() == proof["input_sha256"], "Arm totals digest differs")
    rows = load()
    if args.history:
        check_history(args.history, rows, proof["daily_history_sha256"])
    require(same(summarize(rows), proof["result"]), "Recomputed estimates differ")
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    main()
