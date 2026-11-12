#!/usr/bin/env python3
"""Recompute the paired baseline startup scale contrasts without simulation."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import fmean, stdev

SIZES = (30, 60, 120, 300, 600, 1000)
WORLDS = tuple(range(3000, 3005))
ARMS = ("witness", "hierarchy")
FAMILY_SIZE = 2 * (len(SIZES) - 1)
SHA256 = re.compile(r"[0-9a-f]{64}")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def t4_critical(two_sided_alpha: float) -> float:
    """Invert the exact two-sided Student t tail for four degrees of freedom."""
    lower, upper = 0.0, 32.0
    for _ in range(60):
        candidate = (lower + upper) / 2
        r = candidate / math.hypot(candidate, 2.0)
        tail = (1 - r) ** 2 * (2 + r) / 2
        if tail > two_sided_alpha:
            lower = candidate
        else:
            upper = candidate
    return (lower + upper) / 2


def interval(sample: list[float], critical: float) -> list[float]:
    center = fmean(sample)
    half_width = critical * stdev(sample) / math.sqrt(len(sample))
    return [center - half_width, center + half_width]


def load_endpoints(path: Path) -> dict[tuple[int, int, str], tuple[float, float]]:
    endpoints = {}
    summary_hashes = set()
    prehistories = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            size = int(row["initial_members"])
            world = int(row["world"])
            arm = row["arm"]
            key = (size, world, arm)
            if size not in SIZES or world not in WORLDS or arm not in ARMS or key in endpoints:
                raise ValueError(f"Unexpected or duplicate arm: {key}")
            summary_hash = row["arm_summary_sha256"]
            prehistory_hash = row["paired_prehistory_sha256"]
            if not SHA256.fullmatch(summary_hash) or not SHA256.fullmatch(prehistory_hash):
                raise ValueError(f"Invalid source fingerprint: {key}")
            if summary_hash in summary_hashes:
                raise ValueError(f"Duplicate arm summary fingerprint: {key}")
            summary_hashes.add(summary_hash)
            pair = (size, world)
            if prehistories.setdefault(pair, prehistory_hash) != prehistory_hash:
                raise ValueError(f"Different prehistory fingerprints within pair: {pair}")
            exposure = float(row["active_member_calendar_days"]) / 365.25
            revenue = float(row["revenue_resource_units"])
            cost = float(row["operating_resource_units"])
            if not all(math.isfinite(v) and v >= 0 for v in (exposure, revenue, cost)) or exposure == 0:
                raise ValueError(f"Invalid exposure or accounting value: {key}")
            total = revenue - cost
            endpoints[key] = (total / exposure, total)
    expected = len(SIZES) * len(WORLDS)
    if len(endpoints) != expected * len(ARMS) or len(set(prehistories.values())) != expected:
        raise ValueError("Expected distinct paired prehistories and two arms at all sizes and worlds")
    return endpoints


def stats(sample: list[float]) -> dict:
    return {
        "mean": fmean(sample),
        "sample_sd": stdev(sample),
        "pointwise_95_interval": interval(sample, t4_critical(0.05)),
        "familywise_95_interval": interval(sample, t4_critical(0.05 / FAMILY_SIZE)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-summary", type=Path, help="also check a local copy of the frozen scale-study.json")
    args = parser.parse_args()

    folder = Path(__file__).resolve().parent
    provenance = json.loads((folder / "provenance.json").read_text(encoding="utf-8"))
    data_path = folder / "world-endpoints.csv"
    if digest(data_path) != provenance["world_endpoints_csv_sha256"]:
        raise ValueError("Portable data SHA-256 does not match provenance")
    public = provenance["frozen_public_scale_study"]
    frozen = public["baseline_rate_contrast_mean_low_high"]
    if args.public_summary:
        if digest(args.public_summary) != public["sha256"]:
            raise ValueError("Frozen public source SHA-256 does not match provenance")
        rows = json.loads(args.public_summary.read_text(encoding="utf-8"))
        actual = {
            str(row["N"]): row["net_operating_result_per_member_year"]["diff_ci"]
            for row in rows
            if row["variant"] == "baseline"
        }
        if actual != frozen:
            raise ValueError("Frozen public baseline values do not match provenance")

    endpoints = load_endpoints(data_path)
    gaps = {}
    for size in SIZES:
        gaps[size] = {
            world: tuple(
                witness - hierarchy
                for witness, hierarchy in zip(
                    endpoints[size, world, "witness"], endpoints[size, world, "hierarchy"]
                )
            )
            for world in WORLDS
        }
        rates = [gaps[size][world][0] for world in WORLDS]
        recomputed = [fmean(rates), *interval(rates, t4_critical(0.05))]
        if any(abs(x - y) > 1e-8 for x, y in zip(recomputed, frozen[str(size)])):
            raise ValueError(f"Frozen published baseline result did not reproduce at size {size}")

    contrasts = []
    for left, right in zip(SIZES, SIZES[1:]):
        n_added = right - left
        rate = [(gaps[right][w][0] - gaps[left][w][0]) / n_added * 100 for w in WORLDS]
        total = [(gaps[right][w][1] - gaps[left][w][1]) / n_added for w in WORLDS]
        contrasts.append({
            "initial_members_from": left,
            "initial_members_to": right,
            "rate_gap_change_per_100_added_initial_members": stats(rate),
            "whole_firm_first_year_total_gap_change_per_added_initial_member": stats(total),
        })
    print(json.dumps({
        "scope": "Retrospective analysis of frozen default startup simulations",
        "gap": "DAMS with witnessed record minus retained performance-tier hierarchy",
        "matched_worlds_per_step": len(WORLDS),
        "t_degrees_of_freedom": len(WORLDS) - 1,
        "bonferroni_family_size": FAMILY_SIZE,
        "rate_units": "resource units per member-year per 100 added initial members",
        "total_units": "first-year resource units per added initial member",
        "all_six_frozen_public_baseline_rate_contrasts_reproduced": True,
        "contrasts": contrasts,
    }, indent=2))


if __name__ == "__main__":
    main()
