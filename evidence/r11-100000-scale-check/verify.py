"""Check scale-check coverage and summarize recorded prehistory resources."""
from hashlib import sha256
import json
from pathlib import Path

DATA_SHA256 = "d4ff7f8129487e2e71c20ec571ff002c7fe9ac67d93842bb30a041384625fd36"


def main():
    raw = Path(__file__).with_name("evidence.json").read_bytes()
    if sha256(raw).hexdigest() != DATA_SHA256:
        raise ValueError("Input digest mismatch: evidence.json")
    evidence = json.loads(raw)
    rows = evidence["worlds"]
    if [row["world"] for row in rows] != [1000, 1001, 1002]:
        raise ValueError("Unexpected assigned world roster")
    if any(row["final_machine_operation"] != "deleted" or
           row["verified_arm_receipts"] not in (0, 1, 2) for row in rows):
        raise ValueError("Invalid recorded status")
    histories = [row["prehistory"] for row in rows if row["prehistory"] is not None]
    hours = [row["wall_seconds"] / 3600 for row in histories]
    memory = [row["peak_rss_mib"] / 1024 for row in histories]
    expected = evidence["protocol_projected_prehistory_hours"]
    arms = sum(row["verified_arm_receipts"] for row in rows)
    pairs = sum(row["verified_arm_receipts"] == 2 for row in rows)
    print(json.dumps(dict(assigned_worlds=len(rows), completed_prehistories=len(histories),
                          assigned_arms=2*len(rows), completed_arm_receipts=arms, paired_worlds=pairs,
                          prehistory_wall_hours=[round(min(hours), 4), round(max(hours), 4)],
                          prehistory_wall_to_plan=[round(min(hours)/expected, 4), round(max(hours)/expected, 4)],
                          prehistory_peak_gib=[round(min(memory), 4), round(max(memory), 4)]),
                     indent=2))


if __name__ == "__main__":
    main()
