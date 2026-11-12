"""Recompute how a fixed rate improvement closes operating gaps under discount scenarios."""

import hashlib
import importlib.util
import json
from math import fsum, isclose, prod
from statistics import fmean
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "r11-10000-operating-payback"
RATES = (0.0, 0.05, 0.10)


def load_payback():
    spec = importlib.util.spec_from_file_location("retained_payback", SOURCE / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def world_gap(input_rows, world, payback, discount):
    factors = {year: (1 + discount) ** (-year) for year in range(1, 11)}
    future_factor = fsum(factors[year] for year in range(6, 11))
    pv = {}
    discounted_exposure = {}
    for arm in payback.ARMS:
        observed = fsum(factors[year] * prod(input_rows[world, arm, year]) for year in payback.YEARS)
        _, _, five_year_exposure, future_rate = payback.arm_totals(input_rows, world, arm)
        discounted_exposure[arm] = (five_year_exposure / 5) * future_factor
        pv[arm] = observed + discounted_exposure[arm] * future_rate
    gap = pv[payback.DAMS] - pv[payback.HIERARCHY]
    exposure = discounted_exposure[payback.DAMS]
    return {"world": world, "present_value_difference": gap,
            "discounted_future_dams_member_years": exposure, "world_specific_rate_threshold": -gap / exposure}


def run():
    payback = load_payback()
    provenance = payback.read_json(SOURCE / "provenance.json")
    source_file = SOURCE / "annual-inputs.csv"
    source_hash = hashlib.sha256(source_file.read_bytes()).hexdigest()
    payback.require(source_hash == provenance["inputs_sha256"], "Source input fingerprint differs")
    inputs = payback.load_inputs(source_file)
    undiscounted = payback.read_json(SOURCE / "results.json")
    initial_threshold = undiscounted["summary"]["common_dams_rate_uplift_required_to_close_mean_total_gap"]
    scenarios = {}
    for discount in RATES:
        rows = [world_gap(inputs, world, payback, discount) for world in payback.WORLDS]
        thresholds = [row["world_specific_rate_threshold"] for row in rows]
        mean_gap = fmean(row["present_value_difference"] for row in rows)
        mean_exposure = fmean(row["discounted_future_dams_member_years"] for row in rows)
        common = -mean_gap / mean_exposure
        if discount == 0:
            payback.require(isclose(common, initial_threshold, abs_tol=1e-10), "Original mean threshold differs")
            payback.require(all(isclose(v, row["dams_rate_uplift_required_to_close_world_total_gap"], abs_tol=1e-10)
                                for v, row in zip(thresholds, undiscounted["worlds"])), "Original world thresholds differ")
        scenarios[f"{discount:.2f}"] = {
            "common_rate_threshold": common,
            "world_threshold_minimum": min(thresholds),
            "world_threshold_maximum": max(thresholds),
            "worlds_closing_at_common_threshold": sum(v <= common for v in thresholds),
            "worlds_closing_at_undiscounted_common_threshold": sum(v <= initial_threshold for v in thresholds),
            "worlds": rows,
        }
    return {"annual_input_sha256": source_hash, "world_count": len(payback.WORLDS), "scenarios": scenarios}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, allow_nan=False))
