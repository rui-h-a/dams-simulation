"""Recompute the manuscript's resource figures from the 40 recorded arms."""
import csv
from decimal import Decimal
from pathlib import Path
from statistics import median

FIELDS = (
    "world", "arm", "sim_wall_seconds", "validation_wall_seconds",
    "recorded_worker_cpu_seconds", "recorded_peak_rss_mib", "journal_bytes",
)
ARMS = ("control-hierarchy", "control-process", "dams-witness", "linear-witness")
SECONDS_PER_HOUR = Decimal(3600)


def read_measurements(path):
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("Unexpected resource column names or order")
        rows = []
        seen = set()
        for row in reader:
            key = (int(row["world"]), row["arm"])
            if key in seen:
                raise ValueError(f"Duplicate observation for {key}")
            seen.add(key)
            phases_and_rss = tuple(Decimal(row[field]) for field in FIELDS[2:-1])
            journal_bytes = int(row["journal_bytes"])
            if not all(x.is_finite() and x > 0 for x in phases_and_rss) or journal_bytes <= 0:
                raise ValueError(f"Invalid measurements for {key}")
            rows.append((*phases_and_rss, journal_bytes))
    expected = {(world, arm) for world in range(1000, 1010) for arm in ARMS}
    if seen != expected:
        raise ValueError("Expected four distinct arms for each of ten worlds")
    return rows


def main():
    rows = read_measurements(Path(__file__).with_name("arm-resources.csv"))
    simulations, validations, cpu, rss, journal_sizes = zip(*rows, strict=True)
    duration_seconds = sorted(sim + val for sim, val in zip(simulations, validations, strict=True))
    slot_pairs = zip(duration_seconds[:20], reversed(duration_seconds[20:]), strict=True)
    optimal_pair_seconds = max(a + b for a, b in slot_pairs)
    # If three jobs cannot finish within this feasible schedule, any faster
    # schedule needs exactly two jobs per slot. Pairing extremes is optimal.
    if optimal_pair_seconds >= 3 * duration_seconds[0]:
        raise ValueError("The two-jobs-per-slot optimality argument does not apply")
    twenty_slot_hours = optimal_pair_seconds / SECONDS_PER_HOUR
    figures = [
        ("Simulation phase sum (h)", f"{sum(simulations) / SECONDS_PER_HOUR:.0f}", "797"),
        ("Validation phase sum (h)", f"{sum(validations) / SECONDS_PER_HOUR:.0f}", "595"),
        ("Simulation phase median (h)", f"{median(simulations) / SECONDS_PER_HOUR:.1f}", "19.9"),
        ("Validation phase median (h)", f"{median(validations) / SECONDS_PER_HOUR:.1f}", "14.7"),
        ("Recorded worker peak (MiB)", f"{max(rss):.0f}", "706"),
        ("Final journal size sum (GB)", f"{Decimal(sum(journal_sizes)) / 10**9:.0f}", "288"),
        ("Largest final journal size (GB)", f"{Decimal(max(journal_sizes)) / 10**9:.2f}", "7.28"),
        ("Four-slot phase time lower bound (h, rounded)",
         f"{sum(duration_seconds) / (4 * SECONDS_PER_HOUR):.0f}", "348"),
        ("Ideal twenty-slot phase time (h)", f"{twenty_slot_hours:.2f}", "70.35"),
        ("Ideal remaining time in 72 h (h)", f"{72 - twenty_slot_hours:.2f}", "1.65"),
        ("Four times maximum worker RSS (GiB)", f"{4 * max(rss) / 1024:.2f}", "2.76"),
        ("Four times maximum final journal size (GB)",
         f"{Decimal(4 * max(journal_sizes)) / 10**9:.1f}", "29.1"),
    ]
    for label, measured, expected in figures:
        if measured != expected:
            raise ValueError(f"{label}: calculated {measured}, expected {expected}")
        print(f"{label}: {measured}")
    print(f"Median recorded worker CPU time (h): {median(cpu) / SECONDS_PER_HOUR:.2f}")


if __name__ == "__main__":
    main()
