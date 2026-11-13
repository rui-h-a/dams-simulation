"""Recompute paired operating and verification cost contrasts."""
from hashlib import sha256
import json
from math import isfinite, sqrt
from pathlib import Path
from statistics import mean, stdev

INPUT_SHA = "80311a182a4a612731dbaa329e2dc480536f1d43ee85f591bed4c9f9abfaeff7"
ARMS = ("dams-central", "dams-witness", "dams-consensus")
WORLDS = range(1000, 1065)
T_CRITICAL = 1.9977296543176926


def interval(values):
    center = mean(values)
    half = T_CRITICAL * stdev(values) / sqrt(len(values))
    return [round(x, 8) for x in (center, center - half, center + half)]


def endpoints(row):
    exposure = row["active_member_calendar_days"] / 365.25
    if exposure <= 0:
        raise ValueError("Nonpositive member-year exposure")
    net = row["revenue_resource_units"] - row["operating_resource_units"]
    return (net / exposure, row["verification_resource_units"] / exposure,
            row["review_hours"] / exposure, row["organization_verification_hours"] / exposure)


def main():
    raw = Path(__file__).with_name("arm-costs.json").read_bytes()
    if sha256(raw).hexdigest() != INPUT_SHA:
        raise SystemExit("arm-costs.json failed SHA-256 verification")
    rows = json.loads(raw)
    arms = {}
    for row in rows:
        key = row["world"], row["arm"]
        if key in arms or key[0] not in WORLDS or key[1] not in ARMS:
            raise ValueError(f"Unexpected arm {key}")
        values = endpoints(row)
        if not all(isfinite(value) for value in values):
            raise ValueError(f"Non-finite metric at {key}")
        arms[key] = values
    if len(arms) != len(WORLDS) * len(ARMS):
        raise ValueError("Incomplete world and arm roster")
    output = {}
    for later, earlier in ((ARMS[1], ARMS[0]), (ARMS[2], ARMS[0]), (ARMS[2], ARMS[1])):
        differences = [[x - y for x, y in zip(arms[w, later], arms[w, earlier], strict=True)] for w in WORLDS]
        output[f"{later}_minus_{earlier}"] = {
            key: interval(list(values)) for key, values in zip(
                ("net_operating_result", "verification_processing", "review_hours", "organization_verification_hours"),
                zip(*differences, strict=True), strict=True)
        }
        output[f"{later}_minus_{earlier}"]["break_even_at_0_1"] = interval([-v[0] + 0.1*v[1] for v in differences])
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
