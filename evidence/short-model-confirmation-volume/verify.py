"""Verify the confirmation counts plotted in the thesis from retained histograms."""
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).resolve().parent
BACKENDS = ("central", "witness", "consensus")
WORLDS = range(1000, 1032)
DAYS = range(61)
MEMBERS = 120
HISTOGRAM_SHA = "c20a5d600e66a0df20fe32184cc02eead3831e740733554cf3285b375c85828a"
SERIES_SHA = "be361f09786683b460f27d3e9ce23a00cafc16250fa362dfebcd530a18a0ba49"


def read(name, digest):
    data = (ROOT / name).read_bytes()
    if sha256(data).hexdigest() != digest:
        raise ValueError(f"Input digest mismatch: {name}")
    return data


def main():
    hist = read("world-delay-counts.csv", HISTOGRAM_SHA)
    curves = read("confirmation_counts.csv", SERIES_SHA)
    rows = {}
    for row in csv.DictReader(io.StringIO(hist.decode())):
        key = row["backend"], int(row["world"])
        confirmed, unfinished = int(row["confirmed"]), int(row["unfinished"])
        delays = [int(row[f"delay_{d}"]) for d in DAYS]
        if key in rows or min(delays) < 0 or confirmed <= 0 or unfinished < 0 or sum(delays) != confirmed:
            raise ValueError(f"Invalid histogram for {key}")
        rows[key] = confirmed, unfinished, delays
    if rows.keys() != {(backend, world) for backend in BACKENDS for world in WORLDS}:
        raise ValueError("The dataset must cover three backends in each of 32 worlds")

    expected = io.StringIO()
    writer = csv.writer(expected, lineterminator="\n")
    writer.writerow(("backend", "days", "mean_per_member"))
    results = {}
    for backend in BACKENDS:
        matched = [rows[backend, w] for w in WORLDS]
        counts = [0] * len(matched)
        conditional = []
        for day in DAYS:
            counts = [prior + row[2][day] for prior, row in zip(counts, matched, strict=True)]
            conditional.append(fmean(c / row[0] for c, row in zip(counts, matched, strict=True)))
            writer.writerow((backend, day, sum(counts) / (len(WORLDS) * MEMBERS)))
        results[backend] = {
            "day60_confirmations_per_member": sum(counts) / (len(WORLDS) * MEMBERS),
            "day60_unfinished_per_world": fmean(row[1] for row in matched),
            "conditional_share_by_delay_day": conditional,
        }
    if expected.getvalue().encode() != curves:
        raise ValueError("The plotted series differs from the recomputed series")
    print(json.dumps({"worlds": len(WORLDS), "members_per_world": MEMBERS, "backends": results},
                     indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
