"""Check the arithmetic of the retained 65-world record-process comparison."""

import csv
from hashlib import sha256
import io
import json
from math import isclose, isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev


ROOT = Path(__file__).resolve().parent
ARMS = ("dams-central", "dams-consensus")
WORLDS = list(range(1000, 1065))
FIELDS = (
    "world",
    "arm",
    "active_member_calendar_days",
    "operating_revenue",
    "operating_resources",
    "mean_confirmed_record_delay_days",
    "confirmed_records",
    "unfinished_records",
    "source_summary_sha256",
)
METRICS = (
    "net_operating_result_per_member_year",
    "confirmation_mean_days",
    "confirmed_records",
    "unfinished_records",
)
# Two-sided 95% interval for 65 paired observations (64 degrees of freedom).
T_CRITICAL = 1.9977296543176928


def score(row):
    exposure = int(row["active_member_calendar_days"])
    revenue = float(row["operating_revenue"])
    resources = float(row["operating_resources"])
    delay = float(row["mean_confirmed_record_delay_days"])
    confirmed = int(row["confirmed_records"])
    unfinished = int(row["unfinished_records"])
    digest = row["source_summary_sha256"]
    if (
        exposure <= 0
        or confirmed <= 0
        or unfinished < 0
        or any(not isfinite(x) or x < 0 for x in (revenue, resources, delay))
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise ValueError("Invalid retained row")
    return dict(zip(METRICS, ((revenue - resources) / (exposure / 365.25), delay, confirmed, unfinished)))


def paired_summary(differences):
    mean = fmean(differences)
    sd = stdev(differences)
    radius = T_CRITICAL * sd / sqrt(len(differences))
    return {
        "mean": mean,
        "sample_sd": sd,
        "ci95": [mean - radius, mean + radius],
        "positive": sum(x > 0 for x in differences),
        "negative": sum(x < 0 for x in differences),
        "zero": differences.count(0),
    }


def check_value(actual, expected):
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError("Result vector length differs")
        for a, e in zip(actual, expected, strict=True):
            check_value(a, e)
    elif type(expected) is int:
        if actual != expected:
            raise ValueError(f"Unexpected count {actual}, expected {expected}")
    elif not isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"Unexpected value {actual}, expected {expected}")


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    if sha256(Path(__file__).read_bytes()).hexdigest() != manifest["verifier_sha256"]:
        raise ValueError("Verifier checksum differs")
    raw = (ROOT / "retained-summary-rows.csv").read_bytes()
    if sha256(raw).hexdigest() != manifest["retained_summary_rows_sha256"]:
        raise ValueError("Summary row checksum differs")
    rows = csv.DictReader(io.StringIO(raw.decode(), newline=""))
    if tuple(rows.fieldnames or ()) != FIELDS:
        raise ValueError("Unexpected summary row fields")
    values = {}
    for row in rows:
        key = int(row["world"]), row["arm"]
        if key in values:
            raise ValueError("Duplicate world-arm row")
        values[key] = score(row)
    if set(values) != {(w, a) for w in WORLDS for a in ARMS}:
        raise ValueError("World or arm roster differs")
    if manifest["worlds"] != WORLDS or manifest["arms"] != list(ARMS):
        raise ValueError("Manifest roster differs")
    contrasts = {}
    for metric in METRICS:
        differences = [
            values[w, "dams-consensus"][metric] - values[w, "dams-central"][metric]
            for w in WORLDS
        ]
        contrasts[metric] = paired_summary(differences)
        for key, value in contrasts[metric].items():
            check_value(value, manifest["contrast"][metric][key])
        for arm in ARMS:
            mean = fmean(values[w, arm][metric] for w in WORLDS)
            check_value(mean, manifest["arm_means"][arm][metric])
    print(json.dumps({
        "worlds": len(WORLDS),
        "arm_rows": len(values),
        "difference": "dams-consensus minus dams-central",
        "contrasts": contrasts,
    }, indent=2))


if __name__ == "__main__":
    main()
