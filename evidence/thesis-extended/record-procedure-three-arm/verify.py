"""Recompute the three paired DAMS record-procedure comparisons."""

import csv
from hashlib import sha256
from io import StringIO
import json
from math import isclose, isfinite, sqrt
from pathlib import Path
from statistics import mean, stdev


ROOT = Path(__file__).resolve().parent
CSV_NAME = "retained-summary-rows.csv"
WORLDS = tuple(range(1000, 1065))
CENTRAL, WITNESS, CONSENSUS = "dams-central", "dams-witness", "dams-consensus"
ARMS = (CENTRAL, WITNESS, CONSENSUS)
BASELINE_ENGINE = "487ab3fdf1afbf904a940a6a9b871967a805ac646d162632466e52ace3adac7e"
PROCESS_ENGINE = "5f0497a859b4b2295f234ed8ccc3dd6c4ce85d80ef3aa08c70f902c7cdff0c34"
ENGINES = {CENTRAL: PROCESS_ENGINE, WITNESS: BASELINE_ENGINE, CONSENSUS: PROCESS_ENGINE}
OUTCOMES = (
    "net_operating_result_per_member_year",
    "confirmation_mean_days",
    "confirmed_records",
    "unfinished_records",
)
SUMMARY_FIELDS = (
    "active_member_calendar_days",
    "revenue_resource_units",
    "operating_resource_units",
    *OUTCOMES[1:],
)
DIGEST_FIELDS = ("source_summary_sha256", "common_prehistory_rows_sha256", "hierarchy_series_sha256")
FIELDS = ("world", "arm", "source_engine_sha256", *DIGEST_FIELDS, *SUMMARY_FIELDS)
PAIRS = ((WITNESS, CENTRAL), (CONSENSUS, CENTRAL), (CONSENSUS, WITNESS))
FAMILY_SIZE = len(PAIRS) * len(OUTCOMES)
# Student t quantile at 1 - 0.05 / (2 * 12), with 64 degrees of freedom.
T_CRITICAL = 2.9718627800522976


def check_digest(value):
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("Invalid SHA-256 label")


def read_outcomes(row):
    exposure = int(row["active_member_calendar_days"])
    revenue = float(row["revenue_resource_units"])
    expense = float(row["operating_resource_units"])
    delay = float(row["confirmation_mean_days"])
    confirmed = int(row["confirmed_records"])
    unfinished = int(row["unfinished_records"])
    if (
        exposure <= 0 or confirmed <= 0 or unfinished < 0
        or any(not isfinite(value) or value < 0 for value in (revenue, expense, delay))
    ):
        raise ValueError("Invalid summary fields")
    net = (revenue - expense) / (exposure / 365.25)
    return dict(zip(OUTCOMES, (net, delay, confirmed, unfinished), strict=True))


def read_panel(raw):
    reader = csv.DictReader(StringIO(raw.decode("utf-8"), newline=""))
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError("Unexpected summary column names")
    values, matches, summaries = {}, {}, set()
    for row in reader:
        if None in row or None in row.values():
            raise ValueError("Unexpected summary row width")
        world, arm = int(row["world"]), row["arm"]
        if world not in WORLDS or arm not in ARMS or (world, arm) in values:
            raise ValueError(f"Unexpected or duplicate world-arm row {world}, {arm}")
        if row["source_engine_sha256"] != ENGINES[arm]:
            raise ValueError(f"Unexpected source engine for {world}, {arm}")
        for field in DIGEST_FIELDS:
            check_digest(row[field])
        summary = row["source_summary_sha256"]
        if summary in summaries:
            raise ValueError(f"Duplicate source summary for {world}, {arm}")
        summaries.add(summary)
        match = (row["common_prehistory_rows_sha256"], row["hierarchy_series_sha256"])
        if matches.setdefault(world, match) != match:
            raise ValueError(f"Inconsistent provenance labels in world {world}")
        values[world, arm] = read_outcomes(row)
    if set(values) != {(world, arm) for world in WORLDS for arm in ARMS}:
        raise ValueError("Incomplete world-arm roster")
    return values


def compare(values):
    comparisons = {}
    for later, earlier in PAIRS:
        outcomes = {}
        for metric in OUTCOMES:
            differences = [values[world, later][metric] - values[world, earlier][metric] for world in WORLDS]
            point = mean(differences)
            radius = T_CRITICAL * stdev(differences) / sqrt(len(differences))
            outcomes[metric] = {
                "mean": point,
                "adjusted_95": [point - radius, point + radius],
                "positive": sum(value > 0 for value in differences),
                "negative": sum(value < 0 for value in differences),
                "zero": differences.count(0),
            }
        comparisons[f"{later}_minus_{earlier}"] = outcomes
    return comparisons


def check_comparisons(actual, expected):
    if actual.keys() != expected.keys():
        raise ValueError("Unexpected contrast roster")
    for pair, outcomes in actual.items():
        if outcomes.keys() != expected[pair].keys():
            raise ValueError(f"Unexpected outcome roster for {pair}")
        for metric, result in outcomes.items():
            target = expected[pair][metric]
            counts = ("positive", "negative", "zero")
            actual_floats = [result["mean"], *result["adjusted_95"]]
            expected_floats = [target["mean"], *target["adjusted_95"]]
            if any(type(target[key]) is not int or result[key] != target[key] for key in counts):
                raise ValueError(f"Sign counts differ for {pair}, {metric}")
            if not all(isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
                       for a, b in zip(actual_floats, expected_floats, strict=True)):
                raise ValueError(f"Paired estimates differ for {pair}, {metric}")


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    for name in (CSV_NAME, "verify.py", "README.md"):
        if sha256((ROOT / name).read_bytes()).hexdigest() != manifest["files_sha256"][name]:
            raise ValueError(f"Checksum differs for {name}")
    if manifest["engines_by_arm"] != ENGINES or manifest["worlds"] != list(WORLDS):
        raise ValueError("Manifest roster differs")
    if manifest["family_size"] != FAMILY_SIZE or manifest["critical_df64"] != T_CRITICAL:
        raise ValueError("Manifest interval settings differ")
    values = read_panel((ROOT / CSV_NAME).read_bytes())
    comparisons = compare(values)
    check_comparisons(comparisons, manifest["comparisons"])
    print(json.dumps({
        "paired_worlds": len(WORLDS),
        "arm_rows": len(values),
        "family_size": FAMILY_SIZE,
        "comparisons": comparisons,
    }, indent=2))


if __name__ == "__main__":
    main()
