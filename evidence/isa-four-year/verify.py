"""Recompute the staged four-year statistics with the Python standard library."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory


FILES = {"README.md", "arm-outcomes.json", "expected-statistics.json", "isa_spec.py", "stats.py", "verify.py",
         "mechanism-counts.json", "expected-mechanisms.json"}


def recompute_mechanisms(aggregates, mechanism):
    worlds = aggregates["worlds"]
    observations = mechanism["worlds"]
    if (mechanism["schema"], mechanism["first_day"], mechanism["end_day_exclusive"]) != (
            "isa-four-year-mechanism-counts-v1", 731, 2191) or observations.keys() != worlds.keys():
        raise ValueError("unexpected mechanism study window or roster")

    witnessed = "dams-isa-coord"
    automated_dams = "dams-auto-coord"
    sandbox_arms = {"hierarchy-isa-coord", "dams-isa-central-coord", witnessed}
    classes = ("objective", "inferential", "disputed")
    review_metrics = ("claims", "automated_decisions", "human_reviews")
    review_totals = {arm: dict.fromkeys(review_metrics, 0) for arm in (automated_dams, witnessed)}
    rejection_rates = dict.fromkeys(next(iter(worlds.values()))["arms"], Fraction(0))
    project_fields = ("projects_created", "projects_closed", "projects_active")
    project_counts = {field: [] for field in project_fields}
    for world, observed in observations.items():
        arms = worlds[world]["arms"]
        if observed["first_review"].keys() != arms.keys() or observed["sandboxes"].keys() != sandbox_arms:
            raise ValueError(f"incomplete mechanism roster for world {world}")
        for arm, totals in review_totals.items():
            values = arms[arm]["values"]
            for metric in review_metrics:
                counts = [values[f"class_{record_class}_{metric}"] for record_class in classes]
                if any(type(n) not in (float, int) or not math.isfinite(n) or n < 0 or int(n) != n for n in counts):
                    raise ValueError(f"invalid {metric} count for {world}/{arm}")
                count = int(sum(counts))
                if metric == "claims" and count != observed["first_review"][arm]["filed"]:
                    raise ValueError(f"claim class totals disagree with period filings for {world}/{arm}")
                totals[metric] += count
        for arm, row in observed["first_review"].items():
            filed, human, automated = (row[k] for k in ("filed", "human_rejected", "automated_rejected"))
            if any(type(n) is not int or n < 0 for n in (filed, human, automated)) or filed == 0:
                raise ValueError(f"invalid first-review count for {world}/{arm}")
            if automated != arms[arm]["values"]["automated_rejects"]:
                raise ValueError(f"automated rejection totals disagree for {world}/{arm}")
            rejection_rates[arm] += Fraction(100 * (human + automated), len(worlds) * filed)
        for arm, row in observed["sandboxes"].items():
            created, closed, active = (row[k] for k in project_fields)
            if (any(type(n) is not int or n < 0 for n in row.values()) or created - closed != active
                    or row["split_retirements"] != 0 or row["merge_retirements"] != 0):
                raise ValueError(f"sandbox accounting differs for {world}/{arm}")
        for field in project_fields:
            project_counts[field].append(observed["sandboxes"][witnessed][field])

    routing = mechanism["routing_percent"]
    if (any(type(n) is not int or not 0 <= n <= 100 for n in routing.values())
            or sum(routing[f"class_{c}"] for c in classes) != 100
            or routing["audit_minimum"] > routing["audit_fixed"]
            or any(totals["claims"] <= 0 for totals in review_totals.values())):
        raise ValueError("invalid routing assumptions")
    eligible = sum(routing[f"class_{c}"] * (100 - routing[f"abstain_{c}"])
                   for c in ("objective", "inferential"))
    review_percent = {
        arm: {
            metric: f"{float(Fraction(100 * totals[metric], totals['claims'])):.1f}"
            for metric in ("automated_decisions", "human_reviews")
        }
        for arm, totals in review_totals.items()
    }
    return {
        "automatic_claim_review_percent": {
            arm: percents["automated_decisions"] for arm, percents in review_percent.items()},
        "witnessed_review_totals": review_totals[witnessed],
        "witnessed_review_percent": review_percent[witnessed],
        "mean_honest_rejections_per_100_period_filings": {
            arm: f"{float(value):.1f}" for arm, value in rejection_rates.items()},
        "witnessed_project_count_ranges": {k: [min(v), max(v)] for k, v in project_counts.items()},
        "expected_automatic_first_review_percent": {
            k: f"{float(Fraction((100 - routing[k]) * eligible, 10000)):.2f}"
            for k in ("audit_fixed", "audit_minimum")},
    }


def compare(reference, actual, path="statistics"):
    if isinstance(reference, dict) and isinstance(actual, dict) and reference.keys() == actual.keys():
        for key, value in reference.items():
            compare(value, actual[key], f"{path}.{key}")
        return
    if isinstance(reference, list) and isinstance(actual, list) and len(reference) == len(actual):
        for index, (expected, observed) in enumerate(zip(reference, actual)):
            compare(expected, observed, f"{path}[{index}]")
        return
    if type(reference) is type(actual):
        if isinstance(reference, float):
            if math.isclose(reference, actual, rel_tol=5e-12, abs_tol=0.0):
                return
        elif reference == actual:
            return
    raise ValueError(f"recomputed result differs at {path}")


def main():
    directory = Path(__file__).resolve().parent
    checksums = json.loads((directory / "SHA256SUMS.json").read_bytes())
    if checksums.keys() != FILES:
        raise ValueError("checksum roster does not match the documented files")
    for name, expected in checksums.items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"checksum mismatch: {name}")

    with TemporaryDirectory(prefix="dams-four-year-") as scratch:
        output = Path(scratch) / "statistics.json"
        subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(directory / "stats.py"), str(directory / "arm-outcomes.json"),
             "--horizon-years", "4", "--out", str(output)],
            cwd=scratch, check=True, capture_output=True,
        )
        computed = output.read_bytes()
    expected = (directory / "expected-statistics.json").read_bytes()
    result = json.loads(computed)
    compare(json.loads(expected), result)
    mechanisms = recompute_mechanisms(json.loads((directory / "arm-outcomes.json").read_bytes()),
                                      json.loads((directory / "mechanism-counts.json").read_bytes()))
    primary = "net_operating_result_post_per_member_year"
    mechanisms["operating_result_steps"] = {}
    for code in ("S5", "S1", "S7", "S4", "S3"):
        contrast = result["contrasts"][code]
        outcome = contrast["outcomes"][primary]
        mechanisms["operating_result_steps"][code] = {
            "first_arm": contrast["minuend"], "second_arm": contrast["subtrahend"],
            "estimate": f"{outcome['mean']:.2f}", "paired_ci95": [f"{x:.2f}" for x in outcome["ci95"]],
        }
    custody = result["contrasts"]["S2"]["outcomes"][primary]
    lower, upper = custody["ci95"]
    mechanisms["witnessed_custody_loss_offset"] = {
        "unit": "resource units per member-year",
        "estimate": f"{-custody['mean']:.2f}",
        "paired_ci95": [f"{-upper:.2f}", f"{-lower:.2f}"],
    }
    compare(json.loads((directory / "expected-mechanisms.json").read_bytes()), mechanisms, "mechanisms")
    paired = sum(len(contrast["outcomes"]) for contrast in result["contrasts"].values())
    print(json.dumps({
        "four_years": result["horizon_years_after_adoption"],
        "worlds": result["n_worlds"], "arms": len(result["arms"]),
        "paired_outcome_estimates": paired,
        "primary_holm_tests": len(result["holm_adjusted_p_primary_outcome"]),
        "byte_identical": computed == expected,
        "mechanisms": mechanisms,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
