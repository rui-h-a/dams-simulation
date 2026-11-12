"""Reconcile retained main-study terminal recognition stocks and cumulative flows."""

from csv import DictReader
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import fmean

ROOT = Path(__file__).resolve().parent
WORLDS = range(1000, 1010)
ARMS = ("control-hierarchy", "control-process", "dams-witness", "linear-witness")
DIGESTS = ("summary_sha256", "arm_receipt_sha256", "prehistory_sha256")
COUNTS = ("work_events", "confirmed_records", "reviews_rejected", "appeals_processed", "appeals_denied", "unfinished_records")
FIELDS = ("world", "arm", *COUNTS, *DIGESTS)
HASH = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load():
    rows = {}
    with (ROOT / "arm-flows.csv").open(newline="", encoding="utf-8") as stream:
        reader = DictReader(stream)
        require(reader.fieldnames == list(FIELDS), "Unexpected CSV fields")
        for row in reader:
            key = int(row["world"]), row["arm"]
            require(key not in rows, "Duplicate world and arm")
            require(all(HASH.fullmatch(row[k]) for k in DIGESTS), "Invalid retained fingerprint")
            values = {k: int(row[k]) for k in COUNTS}
            require(min(values.values()) >= 0, "Negative count")
            returned = values["appeals_processed"] - values["appeals_denied"]
            require(returned >= 0, "More appeals denied than processed")
            expected = values["work_events"] - values["confirmed_records"] - values["reviews_rejected"] + returned
            require(expected == values["unfinished_records"], f"Recognition stock does not balance for {key}")
            rows[key] = {**values, "appeals_returning_to_settlement": returned, "prehistory_sha256": row["prehistory_sha256"]}
    require(rows.keys() == {(w, arm) for w in WORLDS for arm in ARMS}, "Incomplete arm roster")
    require(all(len({rows[w, a]["prehistory_sha256"] for a in ARMS}) == 1 for w in WORLDS), "Shared history differs")
    return rows


def summarize(rows):
    metrics = ("work_events", "confirmed_records", "reviews_rejected", "appeals_returning_to_settlement", "unfinished_records")
    comparisons = {}
    for reference in ("control-hierarchy", "control-process", "linear-witness"):
        differences = [{"world": w, **{k: rows[w, "dams-witness"][k] - rows[w, reference][k] for k in metrics}}
                       for w in WORLDS]
        comparisons[reference] = {"mean_differences": {k: fmean(r[k] for r in differences) for k in metrics},
                                  "worlds": differences}
    return {"arm_count": len(rows), "world_count": len(WORLDS), "comparisons": comparisons}


def main():
    proof = json.loads((ROOT / "results.json").read_bytes())
    require(sha256((ROOT / "arm-flows.csv").read_bytes()).hexdigest() == proof["input_sha256"], "Arm-flow digest differs")
    require(summarize(load()) == proof["results"], "Recomputed results differ")
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    main()
