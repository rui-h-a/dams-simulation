"""Recompute the disposition of the 50,000-member formal comparison."""
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

p = Path(__file__).with_name("evidence.json")
raw = p.read_bytes()

if sha256(raw).hexdigest() != "bee004245bcfa497b417321d85180ce83e4c27aa553fb79e93c80a8c3425eadb":
    raise SystemExit("evidence.json SHA-256 mismatch")
worlds = json.loads(raw)["worlds"]
assert [r["world"] for r in worlds] == list(range(1000, 1010))
arms = ["control-hierarchy", "control-process", "dams-witness"]
for r in worlds:
    assert r["assigned"] == {
        "worlds": [r["world"]], "arms": arms, "scale": 50000, "pre_years": 2, "post_years": 5,
        "source_sha256": "76246e9fdddf566e1db24f0d7da58afa4fa2a44a467889340010205a6ddb3f77",
    }
    assert r["event_kind"] == "prehistory" and r["complete_arm_receipts"] == 0
    assert r["provider_instance_removed_utc"].startswith("2026-10-27T")
    assert r["wall_seconds"] > 0 and r["peak_rss_mib"] > 0
    assert r["plan_generation"].isdigit() and r["event_generation"].isdigit()
hours = [r["wall_seconds"] / 3600 for r in worlds]
memory = [r["peak_rss_mib"] for r in worlds]
report = dict(
    assigned_worlds=len(worlds), assigned_arms=len(worlds) * len(arms),
    completed_shared_histories=len(hours), completed_arm_receipts=sum(r["complete_arm_receipts"] for r in worlds),
    complete_policy_worlds=sum(r["complete_arm_receipts"] == len(arms) for r in worlds),
    retained_progress_objects=sum(r["progress_objects"] for r in worlds),
    shared_history_elapsed_hours=[round(min(hours), 6), round(median(hours), 6), round(max(hours), 6)],
    shared_history_peak_worker_mib=[round(min(memory), 6), round(max(memory), 6)],
)
print(json.dumps(report, indent=2, sort_keys=True))
