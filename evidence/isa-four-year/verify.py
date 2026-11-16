"""Recompute the staged four-year statistics with the Python standard library."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


FILES = {"README.md", "arm-outcomes.json", "expected-statistics.json", "isa_spec.py", "stats.py", "verify.py"}


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
    paired = sum(len(contrast["outcomes"]) for contrast in result["contrasts"].values())
    print(json.dumps({
        "four_years": result["horizon_years_after_adoption"],
        "worlds": result["n_worlds"], "arms": len(result["arms"]),
        "paired_outcome_estimates": paired,
        "primary_holm_tests": len(result["holm_adjusted_p_primary_outcome"]),
        "byte_identical": computed == expected,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
