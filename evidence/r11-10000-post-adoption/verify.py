"""Recompute the main study's five-year post-adoption contrasts."""

import csv
from hashlib import sha256
from io import StringIO
import json
from math import fsum, isclose, isfinite, sqrt
from pathlib import Path
from statistics import mean, stdev


ROOT = Path(__file__).resolve().parent
DATA = "arm-totals.csv"
REFERENCE = "control-hierarchy"
ARMS = (REFERENCE, "control-process", "dams-witness", "linear-witness")
WORLDS = tuple(range(1000, 1010))
DATES = ("2022-01-01", "2026-12-31")
TOTALS = (
    "active_member_calendar_days", "available_member_work_hours", "revenue_resource_units",
    "operating_resource_units", "decision_regret_units", "review_hours", "appeal_hours",
    "governance_hours",
)
DIGESTS = ("timeseries_sha256", "summary_sha256", "arm_receipt_sha256", "shared_history_sha256")
FIELDS = ("world", "arm", *TOTALS, "completed_first_reviews", *DIGESTS)
OUTCOMES = (
    "net_operating_result_per_member_year", "decision_regret_per_million_available_member_hours",
    "governance_member_hours_per_member_year", "witness_resource_units_per_member_year",
)
FAMILY_SIZE = (len(ARMS) - 1) * len(OUTCOMES)
# Student t quantile at 1 - 0.05 / (2 * 12), with nine degrees of freedom.
T_CRITICAL = 3.8078784447247136


def rates(row, arm):
    values = {field: float(row[field]) for field in TOTALS}
    if any(not isfinite(value) or value < 0 for value in values.values()):
        raise ValueError("Invalid arm totals")
    years = int(row["active_member_calendar_days"]) / 365.25
    hours = values["available_member_work_hours"]
    reviews = int(row["completed_first_reviews"])
    if years <= 0 or hours <= 0 or reviews < 0:
        raise ValueError("Invalid exposure or completed-review count")
    if not isclose(values["review_hours"], 0.1 * reviews, rel_tol=0.0, abs_tol=0.003):
        raise ValueError("Review hours disagree with completed-review count")
    governance = fsum((values["review_hours"], values["appeal_hours"], values["governance_hours"], 0.025 * reviews))
    processing = 0.2 * reviews if arm in ("dams-witness", "linear-witness") else 0.0
    return dict(zip(OUTCOMES, (
        (values["revenue_resource_units"] - values["operating_resource_units"]) / years,
        1_000_000 * values["decision_regret_units"] / hours,
        governance / years,
        processing / years,
    ), strict=True))


def read_panel(raw):
    reader = csv.DictReader(StringIO(raw.decode("utf-8"), newline=""))
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError("Unexpected arm-total columns")
    values, histories = {}, {}
    receipt_hashes = set()
    for row in reader:
        if None in row or None in row.values():
            raise ValueError("Unexpected arm-total row width")
        world, arm = int(row["world"]), row["arm"]
        if world not in WORLDS or arm not in ARMS or (world, arm) in values:
            raise ValueError(f"Unexpected or duplicate world-arm row: {world}, {arm}")
        for field in DIGESTS:
            value = row[field]
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"Invalid source digest: {field}")
        if histories.setdefault(world, row["shared_history_sha256"]) != row["shared_history_sha256"]:
            raise ValueError(f"Common prehistory labels disagree in world {world}")
        receipt = row["arm_receipt_sha256"]
        if receipt in receipt_hashes:
            raise ValueError("Duplicate source arm receipt")
        receipt_hashes.add(receipt)
        values[world, arm] = rates(row, arm)
    if set(values) != {(world, arm) for world in WORLDS for arm in ARMS}:
        raise ValueError("Incomplete world-arm roster")
    return values


def comparisons(values):
    result = {}
    for arm in ARMS[1:]:
        result[arm] = {}
        for outcome in OUTCOMES:
            paired = [values[world, arm][outcome] - values[world, REFERENCE][outcome] for world in WORLDS]
            center = mean(paired)
            half_width = T_CRITICAL * stdev(paired) / sqrt(len(paired))
            result[arm][outcome] = {
                "values_by_world": [round(value, 10) for value in paired],
                "mean": round(center, 10),
                "bonferroni_95": [round(center - half_width, 10), round(center + half_width, 10)]
                if half_width else None,
            }
    return result


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    if (manifest["schema"] != "dams-main-post-adoption-totals-v1"
            or tuple(manifest["inclusive_dates"]) != DATES
            or tuple(manifest["worlds"]) != WORLDS
            or tuple(manifest["arms"]) != ARMS
            or manifest["daily_rows_after_adoption"] != 73040):
        raise ValueError("Unexpected evidence manifest")
    raw = (ROOT / DATA).read_bytes()
    if sha256(raw).hexdigest() != manifest["arm_totals_sha256"]:
        raise ValueError("Arm-total checksum differs")
    values = read_panel(raw)
    print(json.dumps({"inclusive_dates": DATES, "worlds": WORLDS, "arms_checked": len(values),
                      "family_size": FAMILY_SIZE, "comparisons": comparisons(values)}, indent=2))


if __name__ == "__main__":
    main()
