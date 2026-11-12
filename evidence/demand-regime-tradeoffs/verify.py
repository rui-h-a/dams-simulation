"""Recompute within-world changes under the demand-matched regime."""

from collections import Counter
from csv import DictReader
from hashlib import sha256
import json
from math import isclose, isfinite, sqrt
from pathlib import Path
import re
from statistics import fmean, stdev

ROOT = Path(__file__).resolve().parent
WORLDS = {"founding": list(range(1000, 1057)),
          "traditional-retained": [*range(1000, 1029), 1031, 1033, 1037, 1038]}
CRITICAL = {57: 2.003240718847872, 33: 2.0369333434601016}
REGIMES = ("baseline", "variant")
ARMS = ("control-hierarchy", "dams-witness")
METRICS = ("net_operating_result_per_member_year", "decision_regret_per_available_member_hour", "confirmation_mean_days")
FIELDS = ("study", "world", "regime", "arm", *METRICS, "arm_sha256", "summary_sha256")
SHA = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load():
    arms = {}
    with (ROOT / "arm-outcomes.csv").open(newline="", encoding="utf-8") as stream:
        reader = DictReader(stream)
        require(reader.fieldnames == list(FIELDS), "Unexpected CSV fields")
        for row in reader:
            require(set(row) == set(FIELDS) and None not in row.values(), "Incomplete CSV row")
            key = row["study"], int(row["world"]), row["regime"], row["arm"]
            require(key not in arms, f"Duplicate arm: {key}")
            require(SHA.fullmatch(row["arm_sha256"]) and SHA.fullmatch(row["summary_sha256"]), f"Invalid source digest: {key}")
            arms[key] = {m: float(row[m]) for m in METRICS}
            require(all(isfinite(v) for v in arms[key].values()), f"Nonfinite outcome: {key}")
    expected = {(s,w,r,a) for s,ws in WORLDS.items() for w in ws for r in REGIMES for a in ARMS}
    require(arms.keys() == expected, "Matched arm roster differs")
    return arms


def interval(values):
    n = len(values)
    mean = fmean(values)
    half = CRITICAL[n] * stdev(values) / sqrt(n)
    return {"worlds": n, "mean": mean, "ci95": [mean-half, mean+half]}


def summarize(arms):
    result = {}
    for study, worlds in WORLDS.items():
        changes = {m: [] for m in METRICS}
        signs = Counter()
        for world in worlds:
            for metric in METRICS:
                contrasts = [arms[study,world,regime,ARMS[1]][metric] - arms[study,world,regime,ARMS[0]][metric]
                             for regime in REGIMES]
                changes[metric].append(contrasts[1] - contrasts[0])
            signs[changes[METRICS[0]][-1] > 0, changes[METRICS[1]][-1] > 0] += 1
        result[study] = {
            "change": {m: interval(values) for m,values in changes.items()},
            "joint_count": signs[True,True],
            "operating_improved": signs[True,True] + signs[True,False],
            "regret_increased": signs[True,True] + signs[False,True],
        }
    return result


def same(a, b):
    if type(a) != type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k],b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x,y) for x,y in zip(a,b))
    if isinstance(a, float):
        return isclose(a,b,rel_tol=1e-12,abs_tol=1e-12)
    return a == b


def main():
    raw = (ROOT / "arm-outcomes.csv").read_bytes()
    proof = json.loads((ROOT / "results.json").read_bytes())
    require(sha256(raw).hexdigest() == proof["input_sha256"], "Input digest differs")
    require(same(summarize(load()), proof["results"]), "Recomputed outcomes differ")
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    main()
