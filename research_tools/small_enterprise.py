"""Versioned five-year startup comparisons using the retained CPU model.

This study supplements the original longitudinal roster. Workforce targets are
an explicit exogenous control, not endogenous growth or empirical calibration.
The model and original longitudinal raw gate are reused without modification.
"""
from __future__ import annotations

import argparse
import dataclasses
from datetime import date, datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import shutil

from dams_sim.config import Config
from dams_sim.longitudinal import anniversary, estimate_longitudinal
from dams_sim.longitudinal_design import (
    LongitudinalSpec, PRIMARY_ENDPOINTS, _long_config,
)
from dams_sim.longitudinal_outputs import (
    endpoint_value, snapshot_rows, window_observation,
)
from dams_sim.longitudinal_pipeline import driver_hash
from dams_sim.longitudinal_statistics import planned_world_count, paired_interval
from dams_sim.runtime import RuntimeLimits
from dams_sim.scheduler import Scheduler
from dams_sim.spec import case_key
from dams_sim.storage import (
    atomic_csv, atomic_json, canonical, digest, file_digest, source_hash,
)
from research_tools.validate_longitudinal import CheckedLongitudinalCases

SCALES = (30, 120, 300)
CORE = "b763b85e34164095911ae1cb85efca123b11205f305cd47e950cf9e0493e0b36"
DRIVER = "81edc3f731f39157ae562c0ec920416c8c1fe3b642d8938bdd3d99a48d37ecd3"
GIB = 1024 ** 3
CAPS = {30: 4 * GIB, 120: 8 * GIB, 300: 16 * GIB}


def spec(n):
    if n not in SCALES:
        raise ValueError("population must be 30, 120 or 300")
    template = LongitudinalSpec("longitudinal-adoption-5y", n=n,
                               latest_fixed_adoption_year=0).validate()
    return template, {
        "schema": "dams-small-enterprise-five-year-v1",
        "population_scales": SCALES, "initial_population": n,
        "calendar_start": template.calendar_start,
        "work_creation_end_day_exclusive": template.common_end_day,
        "days_including_settlement_tail": template.days,
        "full_observation_years": 5, "settlement_tail_calendar_days": 90,
        "seed": template.seed, "pilot_worlds": 4,
        "pilot_world_ids": list(range(40000 + SCALES.index(n) * 1000,
                                       40004 + SCALES.index(n) * 1000)),
        "confirmation_world_start": 50000 + SCALES.index(n) * 1000,
        "confirmation_min": 16, "confirmation_max": 128,
        "family_alpha": .05, "family_tests": 12,
        "primary_endpoints": PRIMARY_ENDPOINTS,
        "arms": ["startup-existing", "startup-founding"],
        "contrast": "founding DAMS minus existing hierarchy; both central",
        "growth_mode": "declared exogenous workforce targets",
        "structure": "retained four guilds and two sites; endogenous version separate",
        "inferential_scope": "conditional synthetic startup comparison; no empirical calibration",
        "unit": "independent paired world; pilot excluded from confirmation",
        "missing_rule": "retain all assigned worlds, closures, failures and undefined exposure",
        "precision_rule": "fixed disjoint confirmation roster from pilot variance; never stop for significance",
        "original_longitudinal_study_replaced": False,
    }


def rows(n, world, limits):
    template, declaration = spec(n)
    result = []
    for arm, strategy, regime in (
        ("startup-existing", "never", "hierarchy"),
        ("startup-founding", "founding", "sublinear"),
    ):
        lc = dataclasses.replace(
            _long_config(template, "startup", strategy),
            world_context=f"small-enterprise-five-year-v1:n={n}:startup-exogenous",
        )
        config = Config(n=n, days=template.days, seed=template.seed, world=world,
                        regime=regime, backend="central", trace_every_days=1,
                        longitudinal=lc, max_events=limits.max_events,
                        max_wall_seconds=limits.world_timeout_seconds,
                        max_output_mb=CAPS[n] / 1_000_000,
                        max_rss_mb=limits.per_world_rss_bytes / (1024 ** 2)).validate()
        estimate = estimate_longitudinal(config)
        if estimate["estimated_output_bytes"] > CAPS[n]:
            raise ValueError("complete four-generation design estimate exceeds case bound")
        result.append({"case_id": case_key(config), "config": config.to_dict(),
                       "tags": {"role": "strategy", "context": "startup", "world": world,
                                "arm_id": arm, "strategy": strategy,
                                "parent_case_id": None, "branch_day": 0,
                                "counts_as_additional_mc_sample": False},
                       "resource_estimate": estimate})
    return result


def guard(component_sha):
    if source_hash() != CORE or driver_hash() != DRIVER:
        raise ValueError("retained scientific source or raw-gate driver changed")
    if file_digest(Path(__file__)) != component_sha:
        raise ValueError("small-study entry changed after admission")


def pair_effects(template, cases, n, stage):
    observations = []
    values = {}
    for case in cases:
        selected, final = snapshot_rows(case["attempt"] / "timeseries.csv", case["config"],
                                       {template.common_end_day - 1, template.days - 1})
        obs = window_observation(template, case, selected, 0, template.common_end_day, "common-end")
        observations.append(obs)
        values[case["tags"]["arm_id"]] = obs
    effects = []
    for name, epsilon, unit in PRIMARY_ENDPOINTS:
        a, ad = endpoint_value(values["startup-founding"], name)
        b, bd = endpoint_value(values["startup-existing"], name)
        effects.append({"initial_n": n, "stage": stage, "world": cases[0]["config"].world,
                        "endpoint": name, "effect": a - b if a is not None and b is not None else None,
                        "unit": unit, "target_halfwidth": epsilon,
                        "treatment_denominator": ad, "reference_denominator": bd})
    return observations, effects


def execute_pair(root, n, world, stage, limits, declaration, component_sha):
    guard(component_sha)
    roster = rows(n, world, limits)
    pair = root / stage / f"world-{world}"
    pair.mkdir(parents=True, exist_ok=True)
    identity = {"source_sha256": CORE, "pipeline_driver_sha256": DRIVER,
                "spec_sha256": digest(canonical(declaration))}
    inventory = {"identity": identity, "entry_sha256": component_sha, "rows": roster}
    marker = pair / "inventory.json"
    if marker.exists() and json.loads(marker.read_text()) != json.loads(canonical(inventory)):
        raise ValueError("existing paired inventory differs; original evidence preserved")
    if not marker.exists():
        atomic_json(marker, inventory)
    bound = 2 * CAPS[n] + 256 * 1024 ** 2
    pair_limits = dataclasses.replace(limits, max_output_bytes=CAPS[n],
                                     batch_max_output_bytes=bound)
    pair_limits = RuntimeLimits.from_dict(pair_limits.to_dict())
    if shutil.disk_usage(pair).free < limits.min_free_disk_bytes + 2 * CAPS[n]:
        raise RuntimeError("actual free storage cannot reserve both complete cases")
    scheduler = Scheduler(pair, pair_limits, DRIVER)
    scheduler.run([Config.from_dict(row["config"]) for row in roster])
    if not scheduler.owned_workers_absent:
        raise RuntimeError("paired producer ownership has not closed")
    guard(component_sha)
    reader = CheckedLongitudinalCases(pair, identity)
    cases = []
    for row in roster:
        attempt = sorted((pair / "cases" / row["case_id"]).glob("attempt-*"))
        attempt = [p for p in attempt if p.is_dir()][-1]
        cases.append(reader.validate_case(row, attempt,
                     expected_provenance=limits.provenance))
    if reader.result()["unique_complete_cases"] != 2:
        raise ValueError("paired raw closure differs")
    template, _ = spec(n)
    obs, effects = pair_effects(template, cases, n, stage)
    atomic_csv(pair / "observations.csv", obs)
    atomic_csv(pair / "effects.csv", effects)
    guard(component_sha)
    result = {"status": "full-paired-raw-validated", "world": world, "stage": stage,
              "initial_n": n, "days": template.days, "observation_years": 5,
              "identity": identity, "entry_sha256": component_sha,
              "cases": [{"case_id": c["case_id"], "arm": c["tags"]["arm_id"],
                         "attempt": str(c["attempt"].relative_to(root)),
                         "manifest_sha256": file_digest(c["attempt"] / "manifest.json"),
                         "state_semantic_sha256": c["state_semantic_sha256"],
                         "summary": c["summary"]} for c in cases],
              "effects": effects, "producer_workers_absent": True,
              "complete_study": False, "empirical_validation": False}
    atomic_json(pair / "paired-result.json", result)
    print(json.dumps({k: result[k] for k in ("status", "world", "stage", "initial_n", "days")}), flush=True)
    return effects


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--population", type=int, choices=SCALES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-limits", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    limits = RuntimeLimits.from_dict(json.loads(args.runtime_limits.read_text()))
    if limits.journal_chunk_bytes != 1048576 or limits.per_world_rss_bytes is None:
        raise ValueError("small study requires explicitly admitted lossless journal and RSS bounds")
    root = args.output.absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("study output cannot have a symlink ancestor")
    root.mkdir(parents=True, exist_ok=True)
    component_sha = file_digest(Path(__file__))
    guard(component_sha)
    _, declaration = spec(args.population)
    frozen = {"declaration": declaration, "entry_sha256": component_sha,
              "source_sha256": CORE, "pipeline_driver_sha256": DRIVER,
              "runtime_sha256": file_digest(args.runtime_limits),
              "pilot_inventory": [r for w in declaration["pilot_world_ids"]
                                  for r in rows(args.population, w, limits)]}
    marker = root / "study.json"
    if marker.exists() and json.loads(marker.read_text()) != json.loads(canonical(frozen)):
        raise ValueError("frozen study source, roster or runtime differs")
    if not marker.exists():
        atomic_json(marker, frozen)
    if args.prepare_only:
        print(json.dumps({"status": "prepared-not-executed", "initial_n": args.population,
                          "days": declaration["days_including_settlement_tail"],
                          "pilot_cases": len(frozen["pilot_inventory"]),
                          "case_bounds": [r["resource_estimate"] for r in frozen["pilot_inventory"][:2]]}), flush=True)
        return
    lock = os.open(root / ".study.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        effects = []
        for world in declaration["pilot_world_ids"]:
            effects += execute_pair(root, args.population, world, "pilot", limits, declaration, component_sha)
        requests = []
        for name, epsilon, unit in PRIMARY_ENDPOINTS:
            values = [r["effect"] for r in effects if r["endpoint"] == name]
            if len(values) != 4 or any(v is None or not math.isfinite(v) for v in values):
                raise ValueError("all assigned pilot endpoints required; undefined exposure cannot be discarded")
            count = planned_world_count(values, name, epsilon, 16, 128, .05, 12)
            requests.append({"endpoint": name, "requested_worlds": count})
        count = max(r["requested_worlds"] for r in requests)
        confirmation = {"requests": requests, "world_count": count,
                        "world_ids": list(range(declaration["confirmation_world_start"],
                                                declaration["confirmation_world_start"] + count)) if count <= 128 else [],
                        "status": "frozen" if count <= 128 else "precision-refused",
                        "pilot_worlds_excluded": True, "study_sha256": digest(canonical(frozen))}
        pointer = root / "confirmation-roster.json"
        if pointer.exists() and json.loads(pointer.read_text()) != confirmation:
            raise ValueError("fixed confirmation roster changed")
        if not pointer.exists():
            atomic_json(pointer, confirmation)
        if count > 128:
            raise ValueError("predeclared precision requires more than 128 worlds; no significance stopping or sample reduction")
        confirmed = []
        for world in confirmation["world_ids"]:
            confirmed += execute_pair(root, args.population, world, "confirmation", limits, declaration, component_sha)
        intervals = [dict(endpoint=name, **paired_interval(
            [r["effect"] for r in confirmed if r["endpoint"] == name], name,
            family_alpha=.05, primary_tests=12, target_halfwidth=epsilon))
            for name, epsilon, unit in PRIMARY_ENDPOINTS]
        atomic_json(root / "confirmation-intervals.json", intervals)
        atomic_json(root / "study-result.json", {"status": "assigned-roster-complete",
                    "independent_confirmation_worlds": count, "pilot_worlds": 4,
                    "precision_met": all(r["precision_met"] for r in intervals),
                    "empirical_validation": False, "original_longitudinal_study_replaced": False})
    finally:
        os.close(lock)


if __name__ == "__main__":
    main()
