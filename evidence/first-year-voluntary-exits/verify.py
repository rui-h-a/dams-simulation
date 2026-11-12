"""Recompute first-year voluntary exit summaries from frozen synthetic counts."""

from collections import defaultdict
from csv import DictReader
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
ARMS = ("control-hierarchy", "control-process", "dams-witness", "linear-witness")
STUDIES = {
    "founding-300": (range(1000, 1057), 0, 1827),
    "main-10000": (range(1000, 1010), 731, 2557),
}
WINDOW = 365
COUNTS = ("voluntary_exits", "early_voluntary_exits", "eligible_recruits", "early_recruit_quits")
HASHES = ("arm_receipt_sha256", "final_state_sha256")
FIELDS = ("study", "world", "arm", *COUNTS, *HASHES)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_rows():
    """Check the CSV schema, study roster, and constraints on all four counts."""
    rows = {}
    with (ROOT / "arm-counts.csv").open(encoding="utf-8", newline="") as stream:
        reader = DictReader(stream)
        require(reader.fieldnames == list(FIELDS), "Unexpected count fields")
        for row in reader:
            require(set(row) == set(FIELDS) and None not in row.values(), "Invalid CSV row")
            key = row["study"], int(row["world"]), row["arm"]
            require(key not in rows, f"Duplicate arm {key}")
            require(all(SHA256_PATTERN.fullmatch(row[field]) for field in HASHES), f"Invalid hash for {key}")
            for field in COUNTS:
                row[field] = int(row[field])
            quits, early_quits, recruits, recruit_quits = (row[field] for field in COUNTS)
            require(0 <= recruit_quits <= early_quits <= quits and recruit_quits <= recruits,
                    f"Inconsistent counts for {key}")
            rows[key] = row
    expected = {(study, world, arm) for study, (worlds, _, _) in STUDIES.items()
                for world in worlds for arm in ARMS}
    require(rows.keys() == expected, "Study roster differs")
    return rows


def percentage(numerator, denominator):
    require(denominator > 0, "Empty pooled denominator")
    value = Decimal(100 * numerator) / Decimal(denominator)
    return str(value.quantize(Decimal(".1"), rounding=ROUND_HALF_UP))


def summarize(rows):
    pooled = defaultdict(lambda: defaultdict(int))
    for (study, _, arm), row in rows.items():
        for field in COUNTS:
            pooled[study, arm][field] += row[field]
    summaries = {}
    for study in STUDIES:
        arms = {}
        for arm in ARMS:
            counts = pooled[study, arm]
            arms[arm] = {
                **counts,
                "early_quits_share_percent": percentage(counts["early_voluntary_exits"], counts["voluntary_exits"]),
                "eligible_recruit_quit_percent": percentage(counts["early_recruit_quits"], counts["eligible_recruits"]),
            }
        summaries[study] = arms

    def bounds(study, field):
        values = [row[field] for row in summaries[study].values()]
        return [min(values, key=Decimal), max(values, key=Decimal)]

    return {
        "arm_count": len(rows),
        "window_days": WINDOW,
        "summaries": summaries,
        "displayed_percent_ranges": {
            "founding_share_of_quits": bounds("founding-300", "early_quits_share_percent"),
            "founding_incidence_among_eligible_entrants": bounds("founding-300", "eligible_recruit_quit_percent"),
            "main_incidence_among_eligible_post_branch_entrants": bounds("main-10000", "eligible_recruit_quit_percent"),
        },
    }


def main():
    expected = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
    require(sha256((ROOT / "arm-counts.csv").read_bytes()).hexdigest() == expected["arm_counts_sha256"],
            "Frozen count file digest differs")
    result = {"arm_counts_sha256": expected["arm_counts_sha256"], **summarize(read_rows())}
    require(result == expected, "Recomputed results differ from the frozen results")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
