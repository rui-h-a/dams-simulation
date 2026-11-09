#!/usr/bin/env python3
"""Recompute elapsed checkpoint timing and inventoried bytes for the partial pilot."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import subprocess

HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
SCOPES = ("comparison_arms", "batch_shared_or_metadata", "outside_batch")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def verify_engine(repository: Path, source: dict) -> None:
    def git(*arguments: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(repository), *arguments])

    commit = source["engine_git_commit"]
    files = {}
    for entry in git("ls-tree", "-z", commit, "dams_sim/").split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            _, kind, blob = metadata.split()
            if kind == b"blob" and path.endswith(b".py"):
                files[path.rsplit(b"/", 1)[1]] = blob.decode("ascii")
    require(len(files) == source["engine_python_files"], "Unexpected engine file count")
    digest = hashlib.sha256()
    for name, blob in sorted(files.items()):
        digest.update(name + b"\0" + git("cat-file", "blob", blob))
    require(digest.hexdigest() == source["engine_source_sha256"], "Engine source digest mismatch")


def checkpoint_timings(path: Path, provenance: dict) -> list[dict]:
    before, after = provenance["timing_checkpoints"]
    temporary = provenance["temporary_checkpoint_day"]
    plan = provenance["plan"]
    records = {}
    for row in csv_rows(path):
        key = (int(row["world"]), row["arm"], int(row["checkpoint_day"]), row["kind"], row["retention_state"])
        timestamp = int(row["relative_mtime_seconds"])
        require(key not in records, f"Duplicate checkpoint record: {key}")
        require(timestamp >= 0 and int(row["bytes"]) > 0 and HEX_SHA256.fullmatch(row["declared_sha256"]) is not None,
                f"Invalid checkpoint metadata: {key}")
        records[key] = timestamp
    require(min(records.values()) == 0, "Checkpoint times must be rebased to the first observed time")
    expected = set()
    timings = []
    for world in plan["worlds"]:
        for arm in plan["arms"]:
            for day in (before, after):
                expected.update((world, arm, day, kind, "finalized") for kind in ("json", "sqlite"))
            expected.add((world, arm, temporary, "sqlite", "temporary"))
            earlier = records[world, arm, before, "json", "finalized"]
            later = records[world, arm, after, "json", "finalized"]
            require(later > earlier, f"Checkpoint times are out of order: {world}, {arm}")
            timings.append({
                "world": world,
                "arm": arm,
                "last_finalized_checkpoint_day": after,
                "elapsed_seconds": later - earlier,
                "simulated_days_between_checkpoints": after - before,
                "elapsed_seconds_per_simulated_day": (later - earlier) / (after - before),
            })
    require(set(records) == expected, "Unexpected set of exported checkpoint records")
    return timings


def inventory(path: Path, source: dict) -> dict:
    totals = {scope: {"files": 0, "bytes": 0} for scope in SCOPES}
    seen = set()
    for row in csv_rows(path):
        scope = row["scope"]
        size, count = int(row["bytes_per_file"]), int(row["file_count"])
        require(scope in totals and size >= 0 and count > 0 and (scope, size) not in seen, "Invalid inventory row")
        seen.add((scope, size))
        totals[scope]["files"] += count
        totals[scope]["bytes"] += size * count
    all_files = sum(row["files"] for row in totals.values())
    all_bytes = sum(row["bytes"] for row in totals.values())
    batch_bytes = all_bytes - totals["outside_batch"]["bytes"]
    require((all_files, all_bytes, batch_bytes) == (
        source["retained_inventory_file_count"], source["retained_inventory_all_bytes"],
        source["retained_inventory_batch_bytes"]), "Inventory totals differ from the source binding")
    return {"all_files": all_files, "all_bytes": all_bytes, "batch_bytes": batch_bytes, "by_scope": totals}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, help="verify the pinned engine digest in a local repository")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    provenance = json.loads((folder / "provenance.json").read_text(encoding="utf-8"))
    for name in ("checkpoint-files.csv", "inventory-sizes.csv"):
        require(sha(folder / name) == provenance["data_sha256"][name], f"Exported data fingerprint mismatch: {name}")
    if args.source_repo:
        verify_engine(args.source_repo, provenance["source"])
    cases = checkpoint_timings(folder / "checkpoint-files.csv", provenance)
    rates = [case["elapsed_seconds_per_simulated_day"] for case in cases]
    storage = inventory(folder / "inventory-sizes.csv", provenance["source"])
    print(json.dumps({
        "worlds": provenance["plan"]["worlds"],
        "case_count": len(cases),
        "cases": cases,
        "elapsed_seconds_per_simulated_day_range": [min(rates), max(rates)],
        "elapsed_seconds_per_simulated_day_median": median(rates),
        "storage_inventory": storage,
    }, indent=2))


if __name__ == "__main__":
    main()
