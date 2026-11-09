"""Recompute a deadline comparison from fixed, measured R11 phase times."""
import argparse
import csv
from hashlib import sha256
from io import StringIO
import json
from math import fsum, isfinite
from pathlib import Path
from statistics import median
from urllib.request import urlopen

CSV_URL = (
    "https://raw.githubusercontent.com/rui-h-a/dams-simulation/"
    "968ecadacedd88ad815a5f19de51de2011e853d0/"
    "evidence/r11-10000-resources/arm-resources.csv"
)
CSV_SHA256 = "ecfe2af861458f9421043744a522c567c73e7e0f1955c18f943071013fc315b3"
PREHISTORY_SHA256 = "72acfacf045d81cd90de467e7b39864b44c697b5976e8d927b7438a507c6df20"
ARMS = ("control-hierarchy", "control-process", "dams-witness", "linear-witness")
SECONDS_PER_HOUR = 3600
DEADLINE_HOURS = 72
SLOTS_PER_HOST = 4


def minimax_below_three(durations, slots):
    """Find the exact ideal makespan when an optimal slot holds at most two jobs."""
    jobs = sorted(durations)
    pairs = len(jobs) - slots
    if not 0 <= pairs <= slots:
        raise ValueError("Outside the range of the two-job schedule")
    paired = [a + b for a, b in zip(jobs[:pairs], reversed(jobs[pairs:2*pairs]))]
    upper = max(jobs[-1], *paired) if paired else jobs[-1]
    if upper >= fsum(jobs[:3]):
        raise ValueError("A faster schedule might use a third job on some slots")
    return upper


def summarize(raw, history_raw):
    if sha256(raw).hexdigest() != CSV_SHA256:
        raise ValueError("Input does not match the fixed resource packet")
    worlds = {}
    seen = set()
    workers = []
    journals = []
    sim_times = []
    check_times = []
    for row in csv.DictReader(StringIO(raw.decode())):
        world = int(row["world"])
        arm = row["arm"]
        if (world, arm) in seen:
            raise ValueError("Duplicate world and arm")
        seen.add((world, arm))
        sim = float(row["sim_wall_seconds"]) / SECONDS_PER_HOUR
        check = float(row["validation_wall_seconds"]) / SECONDS_PER_HOUR
        rss = float(row["recorded_peak_rss_mib"])
        journal = int(row["journal_bytes"])
        if not all(isfinite(x) and x > 0 for x in (sim, check, rss, journal)):
            raise ValueError("Invalid resource observation")
        worlds.setdefault(world, []).append(sim + check)
        sim_times.append(sim)
        check_times.append(check)
        workers.append(rss)
        journals.append(journal)
    if seen != {(world, arm) for world in range(1000, 1010) for arm in ARMS}:
        raise ValueError("Incomplete fixed roster")
    if sha256(history_raw).hexdigest() != PREHISTORY_SHA256:
        raise ValueError("Input does not match the fixed prehistory data")
    histories = {}
    for row in csv.DictReader(StringIO(history_raw.decode())):
        world = int(row["world"])
        duration = float(row["wall_seconds"]) / SECONDS_PER_HOUR
        if world in histories or int(row["observed_days"]) != 731 or not isfinite(duration) or duration <= 0:
            raise ValueError("Invalid prehistory row")
        histories[world] = duration
    if histories.keys() != worlds.keys():
        raise ValueError("Incomplete prehistory roster")
    arms = [duration for durations in worlds.values() for duration in durations]
    batches = [max(durations) for durations in worlds.values()]
    whole_worlds = [histories[world] + max(arm_times) for world, arm_times in worlds.items()]
    table = []
    for hosts in (5, 6, 8, 10):
        flexible = minimax_below_three(arms, hosts * SLOTS_PER_HOST)
        grouped = minimax_below_three(batches, hosts)
        table.append({
            "hosts": hosts,
            "fixed_world_with_prehistory_hours": minimax_below_three(whole_worlds, hosts),
            "fixed_world_hours": grouped,
            "movable_arms_hours": flexible,
            "fixed_world_headroom_hours_at_72": DEADLINE_HOURS - grouped,
            "movable_arms_headroom_hours_at_72": DEADLINE_HOURS - flexible,
        })
    return {
        "roster": {"worlds": len(worlds), "arms": len(arms)},
        "source_csv_sha256": CSV_SHA256,
        "source_prehistory_csv_sha256": PREHISTORY_SHA256,
        "prehistory_wall_hours_sum": fsum(histories.values()),
        "one_host_fixed_worlds_with_prehistory_hours": fsum(whole_worlds),
        "two_fastest_whole_world_hours": fsum(sorted(whole_worlds)[:2]),
        "simulation_hours_sum": fsum(sim_times),
        "validation_hours_sum": fsum(check_times),
        "validation_fraction_of_timed_hours": fsum(check_times) / fsum(arms),
        "median_simulation_hours_per_arm": median(sim_times),
        "median_validation_hours_per_arm": median(check_times),
        "one_host_workload_floor_hours": fsum(arms) / SLOTS_PER_HOST,
        "one_host_fixed_worlds_hours": fsum(batches),
        "peak_worker_rss_mib": max(workers),
        "max_four_worker_rss_gib_allowance": SLOTS_PER_HOST * max(workers) / 1024,
        "journal_bytes_sum": sum(journals),
        "max_four_final_journals_bytes_allowance": SLOTS_PER_HOST * max(journals),
        "fixed_worlds_and_movable_arms": table,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path)
    args = parser.parse_args()
    if args.source is not None:
        data = args.source.read_bytes()
    else:
        with urlopen(CSV_URL, timeout=30) as reply:
            data = reply.read(16000)
    print(json.dumps(summarize(data, Path(__file__).with_name("prehistory.csv").read_bytes()), indent=2))


if __name__ == "__main__":
    main()
