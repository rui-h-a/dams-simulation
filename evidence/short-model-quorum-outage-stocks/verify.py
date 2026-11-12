"""Recompute the short-model stock split for scheduled quorum loss."""
from collections import defaultdict
import csv
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from statistics import fmean

PROCESSES = ("central", "witness", "consensus")
WORLDS = range(2100, 2108)
DAYS = (19, 34, 35, 59)
STOCK_DIGEST = "d746cf769a6e0bd0c005123890e7ee582bd7fd9c1d792e6d4e898498d2aac76a"
DELAY_DIGEST = "c25d844e98fa1c5f897f8061c4236fd9233b9525af54aa901b91ad3377eb1743"


def read(name, expected):
    raw = Path(__file__).with_name(name).read_bytes()
    if sha256(raw).hexdigest() != expected:
        raise ValueError(f"Input digest mismatch: {name}")
    return csv.DictReader(StringIO(raw.decode()))


def main():
    stocks = {}
    for row in read("outage-stocks.csv", STOCK_DIGEST):
        key = int(row["world"]), row["process"], int(row["end_of_model_day"])
        review, settlement, total = (int(row[name]) for name in ("initial_review", "settlement", "total"))
        slots = {"central": 108, "witness": 88, "consensus": 72}[key[1]]
        if (key in stocks or review != 120 + key[2] * (120 - slots) or
                settlement < 0 or review + settlement != total):
            raise ValueError(f"Invalid stock row: {key}")
        stocks[key] = (review, settlement, total)
    if stocks.keys() != {(w, p, d) for w in WORLDS for p in PROCESSES for d in DAYS}:
        raise ValueError("Incomplete stock roster")
    delays = defaultdict(dict)
    for row in read("outage-delays.csv", DELAY_DIGEST):
        w, process = int(row["world"]), row["process"]
        n, mean = int(row["confirmed_records"]), float(row["mean_confirmation_days"])
        if w in delays[process] or not (0 < n <= 7200 and 0 <= mean < 60):
            raise ValueError(f"Invalid delay row: {(w, process)}")
        delays[process][w] = (n, mean)
    if set(delays) != set(PROCESSES) or any(set(d) != set(WORLDS) for d in delays.values()):
        raise ValueError("Incomplete delay roster")
    by_process = {}
    for process in PROCESSES:
        dates = {}
        for day in DAYS:
            avg = [fmean(stocks[w, process, day][i] for w in WORLDS) for i in range(3)]
            dates[day] = dict(zip(("initial_review", "settlement", "total"), avg))
        by_process[process] = dict(
            stock_means=dates, completed_delay_days=round(fmean(delays[process][w][1] for w in WORLDS), 10))
    change = [stocks[w, "consensus", 35][1] - stocks[w, "consensus", 34][1] for w in WORLDS]
    print(json.dumps(dict(worlds=len(WORLDS), cases=len(PROCESSES)*len(WORLDS),
                          process_means=by_process, consensus_settlement_change_day_35=dict(
                              mean=fmean(change), minimum=min(change), maximum=max(change))), indent=2))


if __name__ == "__main__":
    main()
