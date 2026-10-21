"""Bounded, process-isolated measurements of the unchanged CPU reference model.

No cloud resources are started. Results distinguish completed worlds, resource
stops and preflight refusals. Initialization includes population generation.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import ctypes
import dataclasses
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CHILD_PYTHON = str(Path(os.environ.get("DAMS_BENCHMARK_PYTHON", shutil.which("python3") or sys.executable)).resolve())
sys.path.insert(0, str(ROOT))
from dams_sim.config import Config
from dams_sim.storage import atomic_csv, atomic_json, canonical, digest, provenance, source_hash, unique_run


class RusageV2(ctypes.Structure):
    # macOS SDK sys/resource.h, struct rusage_info_v2 (RUSAGE_INFO_V2=2).
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in (
        "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups",
        "ri_pageins", "ri_wired_size", "ri_resident_size", "ri_phys_footprint",
        "ri_proc_start_abstime", "ri_proc_exit_abstime", "ri_child_user_time",
        "ri_child_system_time", "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups",
        "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread", "ri_diskio_byteswritten")]


def process_io(pid: int) -> dict | None:
    if sys.platform == "darwin":
        try:
            lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            data = RusageV2()
            lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            lib.proc_pid_rusage.restype = ctypes.c_int
            if lib.proc_pid_rusage(pid, 2, ctypes.byref(data)):
                return None
            return {"rss_bytes": data.ri_resident_size, "physical_footprint_bytes": data.ri_phys_footprint,
                    "disk_read_bytes": data.ri_diskio_bytesread, "disk_write_bytes": data.ri_diskio_byteswritten,
                    "method": "macOS proc_pid_rusage RUSAGE_INFO_V2"}
        except (OSError, AttributeError):
            return None
    if sys.platform.startswith("linux"):
        try:
            fields = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/io").read_text().splitlines())
            resident = int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            return {"rss_bytes": resident, "physical_footprint_bytes": None,
                    "disk_read_bytes": int(fields["read_bytes"]), "disk_write_bytes": int(fields["write_bytes"]),
                    "method": "Linux /proc pid io + statm"}
        except (OSError, ValueError):
            return None
    return None


def child(path: Path) -> int:
    """One newly spawned interpreter; later repeats reuse that interpreter only."""
    from dams_sim.cli import run_world
    plan = json.loads((path / "task.json").read_text())
    if plan.get("expected_source_sha256") not in (None, source_hash()):
        atomic_json(path / "child.json", {"status": "preflight_refused", "reason": "core source changed before child start"})
        return 2
    rows = []
    code_start, cpu_start = time.monotonic(), time.process_time()
    before_io = process_io(os.getpid())
    for repeat in range(plan["repeat_in_process"]):
        # Explicit full fresh model; no state or completed-world cache is reused.
        gc.collect()
        run_path = path / f"run-{repeat}"
        run_path.mkdir()
        config = Config.from_dict(plan["config"])
        begin, cpu = time.monotonic(), time.process_time()
        io_before = process_io(os.getpid())
        error = None
        try:
            result = run_world(config, run_path)
            status = result["status"]
        except Exception as failure:
            status, error = "failed", f"{type(failure).__name__}: {failure}"
        io_after = process_io(os.getpid())
        manifest = json.loads((run_path / "manifest.json").read_text()) if (run_path / "manifest.json").exists() else {}
        completed_days = json.loads((run_path / "summary.json").read_text()).get("days_completed", 0) if (run_path / "summary.json").exists() else manifest.get("days_completed", 0)
        emitted = config.n * completed_days
        phases = [manifest.get(k) for k in ("initialization_wall_seconds", "simulation_wall_seconds", "statistics_wall_seconds", "plotting_seconds")]
        residual = manifest.get("total_wall_seconds", 0) - sum(v for v in phases if v is not None) if all(v is not None for v in phases) else None
        rows.append({"task": plan["name"], "repeat": repeat, "temperature": "fresh_interpreter" if repeat == 0 else "same_interpreter_fresh_model",
                     "status": status, "error": error, "n": config.n, "days": config.days, "guilds": config.guilds, "sites": config.sites,
                     "source_sha256": manifest.get("source_sha256"), "config_sha256": manifest.get("config_sha256"),
                     "run_path": str(run_path.relative_to(ROOT)) if run_path.is_relative_to(ROOT) else str(run_path),
                     "initialization_population_generation_seconds": phases[0], "separately_timed_population_generation_seconds": None,
                     "simulation_seconds": phases[1], "statistics_seconds": phases[2], "plotting_seconds": phases[3],
                     "provenance_serialization_hash_io_residual_seconds": residual,
                     "core_total_wall_seconds": manifest.get("total_wall_seconds"), "wrapper_wall_seconds": time.monotonic() - begin,
                     "cpu_seconds": time.process_time() - cpu, "peak_process_rss_mb": manifest.get("peak_process_rss_mb"),
                     "work_claims_emitted": emitted, "work_claims_per_simulation_second": emitted / phases[1] if phases[1] else None,
                     "compiled_kernel_seconds": 0, "network_io_bytes": 0, "output_bytes": sum(p.stat().st_size for p in run_path.rglob("*") if p.is_file()),
                     "disk_read_bytes": io_after["disk_read_bytes"] - io_before["disk_read_bytes"] if io_before and io_after else None,
                     "disk_write_bytes": io_after["disk_write_bytes"] - io_before["disk_write_bytes"] if io_before and io_after else None,
                     "io_measurement_method": io_after["method"] if io_after else "unavailable",
                     "summary_sha256": digest((run_path / "summary.json").read_bytes()) if (run_path / "summary.json").exists() else None,
                     "final_state_sha256": digest((run_path / "final_state.json").read_bytes()) if (run_path / "final_state.json").exists() else None})
        atomic_json(path / "measurements.json", rows)
        if status != "complete":
            break
    final_io = process_io(os.getpid())
    atomic_json(path / "child.json", {"status": "complete" if len(rows) == plan["repeat_in_process"] and all(r["status"] == "complete" for r in rows) else "failed",
                                     "wall_seconds": time.monotonic() - code_start, "cpu_seconds": time.process_time() - cpu_start,
                                     "disk_read_bytes": final_io["disk_read_bytes"] - before_io["disk_read_bytes"] if before_io and final_io else None,
                                     "disk_write_bytes": final_io["disk_write_bytes"] - before_io["disk_write_bytes"] if before_io and final_io else None})
    return 0 if all(r["status"] == "complete" for r in rows) else 1


def launch(path: Path, task: dict) -> dict:
    path.mkdir(parents=True, exist_ok=False)
    atomic_json(path / "task.json", task)
    env = os.environ.copy()
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[key] = "1"
    start = time.monotonic()
    peak, disk_read, disk_write, stop = 0, 0, 0, None
    with (path / "stdout.log").open("w") as out, (path / "stderr.log").open("w") as err:
        process = subprocess.Popen([CHILD_PYTHON, str(Path(__file__).resolve()), "_child", "--path", str(path)], cwd=ROOT,
                                   stdout=out, stderr=err, env=env, start_new_session=True)
        while process.poll() is None:
            measured = process_io(process.pid)
            if measured:
                peak = max(peak, measured["rss_bytes"])
                disk_read, disk_write = measured["disk_read_bytes"], measured["disk_write_bytes"]
                if measured["rss_bytes"] > task["watchdog_rss_mb"] * 1024 ** 2:
                    stop = "external_RSS_watchdog"
            elif sys.platform not in {"darwin", "linux"}:
                stop = "external_RSS_measurement_unavailable"
            if time.monotonic() - start > task["watchdog_seconds"]:
                stop = "external_wall_watchdog"
            if stop:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                break
            time.sleep(0.05)
        exit_code = process.wait()
    result = {"task": task["name"], "exit_code": exit_code, "status": "complete" if exit_code == 0 else "terminated" if stop else "failed",
              "resource_stop": stop, "spawn_to_exit_wall_seconds": time.monotonic() - start, "sampled_peak_rss_mb": peak / 1024 ** 2,
              "sampled_process_disk_read_bytes": disk_read, "sampled_process_disk_write_bytes": disk_write,
              "monitor_cadence_seconds": 0.05, "task_path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)}
    atomic_json(path / "watchdog.json", result)
    return result


def config(n: int, days: int, world: int = 90000) -> dict:
    # Guild size is bounded around 100; team size/ring-degree remain fixed.
    return dataclasses.replace(Config(), n=n, days=days, guilds=max(4, n // 100), sites=min(8, max(2, n // 1000)), world=world,
                               max_wall_seconds=600, max_output_mb=500, max_rss_mb=1536, max_events=2_000_000).to_dict()


def plan(repeats: int = 3) -> dict:
    return {"source_sha256": source_hash(), "benchmark_tool_sha256": digest(Path(__file__).read_bytes()),
            "child_python_executable": CHILD_PYTHON,
            "ordinary_modules": "all core daily choice, cooperation, voting, review, settlement, appeal, decay, learning, fatigue, trace and full-state output paths",
            "fixed": {"regime": "sublinear", "backend": "central", "team_size": 5, "one_ring_edge_per_agent": True, "max_events": 2_000_000},
            "single_world": [{"n": n, "days": days, "cold_processes": repeats, "fresh_models_per_process": 2} for n, days in ((120, 60), (1000, 30), (10000, 30), (100000, 3))],
            "larger_partial_attempt": {"n": 1_000_000, "days": 1, "watchdog_rss_mb": 1024, "watchdog_seconds": 120},
            "large_preflight": {"n": 10_000_000, "days": 1, "max_events": 2_000_000},
            "parallel": {"workers": [1, 2, 4], "strong_worlds": 4, "weak_worlds_per_worker": 2, "n": 1000, "days": 30, "independent_batch_repeats": 3},
            "interpretation": "fresh interpreter is process-cold, not an OS-page-cache-flush experiment; warm repeats reuse Python but initialize complete fresh models; no platform other than actual host is certified"}


def execute(output: Path, repeats: int, mode: str) -> Path:
    path = unique_run(output, "cpu-benchmark")
    specification = plan(repeats)
    atomic_json(path / "plan.json", specification)
    atomic_json(path / "host.json", {**provenance(), "logical_cpu_count": os.cpu_count(), "free_disk_bytes": shutil.disk_usage(ROOT).free,
                                    "hardware_model": subprocess.check_output(["sysctl", "-n", "hw.model"], text=True).strip() if sys.platform == "darwin" else None,
                                    "memory_bytes": int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)) if sys.platform == "darwin" else None,
                                    "physical_cpu_count": int(subprocess.check_output(["sysctl", "-n", "hw.physicalcpu"], text=True)) if sys.platform == "darwin" else None,
                                    "load_average_1_5_15_min": list(os.getloadavg()) if hasattr(os,"getloadavg") else None,
                                    "child_python_executable": CHILD_PYTHON,
                                    "background_user_apps_stopped": False,
                                    "host_condition": "shared interactive host; user apps and OS services continue; no concurrent DAMS scientific batch authorized by coordinator",
                                    "os_page_cache_flushed": False})
    statuses, parallel = [], []
    start = time.monotonic()
    def changed_source_stop() -> bool:
        if source_hash() == specification["source_sha256"]:
            return False
        atomic_json(path / "status.json", {"status": "invalidated_source_changed", "tasks": statuses,
                                          "expected_source_sha256": specification["source_sha256"], "actual_source_sha256": source_hash(),
                                          "wall_seconds": time.monotonic() - start})
        summarize(path)
        return True
    if mode in {"all", "single"}:
        for cell in specification["single_world"]:
            for repeat in range(repeats):
                if changed_source_stop():
                    return path
                task = {"name": f"single-n{cell['n']}-t{cell['days']}-r{repeat}", "config": config(cell["n"], cell["days"]),
                        "repeat_in_process": 2, "watchdog_rss_mb": 2048, "watchdog_seconds": 1300, "expected_source_sha256": specification["source_sha256"]}
                statuses.append(launch(path / task["name"], task))
                atomic_json(path / "status.json", {"status": "running", "tasks": statuses})
                print(json.dumps(statuses[-1]), flush=True)
        task = {"name": "partial-n1000000-t1", "config": config(1_000_000, 1), "repeat_in_process": 1,
                "watchdog_rss_mb": 1024, "watchdog_seconds": 120, "expected_source_sha256": specification["source_sha256"]}
        if changed_source_stop():
            return path
        statuses.append(launch(path / task["name"], task))
        # Refusal is run through the real Config validation, not presented as execution.
        try:
            Config.from_dict(config(10_000_000, 1))
            refusal = {"status": "unexpected_preflight_acceptance"}
        except ValueError as error:
            refusal = {"n": 10_000_000, "days": 1, "status": "preflight_refused", "reason": str(error), "actual_agent_allocation": False}
        atomic_json(path / "preflight-10000000.json", refusal)
    if mode in {"all", "parallel"}:
        for kind in ("strong", "weak"):
            for repeat in range(3):
                for workers in (1, 2, 4):
                    if changed_source_stop():
                        return path
                    worlds = 4 if kind == "strong" else 2 * workers
                    tasks = [{"name": f"{kind}-r{repeat}-w{workers}-world{world}", "config": config(1000, 30, 91000 + world),
                              "repeat_in_process": 1, "watchdog_rss_mb": 512, "watchdog_seconds": 120, "expected_source_sha256": specification["source_sha256"]} for world in range(worlds)]
                    begin = time.monotonic()
                    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                        results = list(executor.map(lambda task: launch(path / task["name"], task), tasks))
                    wall = time.monotonic() - begin
                    statuses.extend(results)
                    complete = all(r["status"] == "complete" for r in results)
                    parallel.append({"kind": kind, "repeat": repeat, "workers": workers, "worlds": worlds,
                                     "batch_wall_seconds": wall, "status": "complete" if complete else "failed",
                                     "sum_child_wall_seconds": sum(r["spawn_to_exit_wall_seconds"] for r in results),
                                     "worlds_per_second": worlds / wall if complete else None, "max_child_sampled_rss_mb": max(r["sampled_peak_rss_mb"] for r in results),
                                     "conservative_sum_child_peak_rss_mb": sum(r["sampled_peak_rss_mb"] for r in results)})
                    atomic_json(path / "parallel.json", parallel)
                    print(json.dumps(parallel[-1]), flush=True)
    atomic_json(path / "status.json", {"status": "finished_with_retained_failures", "tasks": statuses, "wall_seconds": time.monotonic() - start,
                                      "source_sha256": source_hash(), "source_unchanged": source_hash() == specification["source_sha256"],
                                      "final_load_average_1_5_15_min": list(os.getloadavg()) if hasattr(os,"getloadavg") else None})
    summarize(path)
    return path


def summarize(path: Path) -> None:
    rows = [row for file in sorted(path.glob("*/measurements.json")) for row in json.loads(file.read_text())]
    watchdogs = [json.loads(file.read_text()) for file in sorted(path.glob("*/watchdog.json"))]
    failures = []
    for watchdog in watchdogs:
        task_path = path / watchdog["task"]
        task = json.loads((task_path / "task.json").read_text())
        if watchdog["status"] != "complete":
            manifest_path = task_path / "run-0" / "manifest.json"
            manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
            failures.append({**watchdog, "n": task["config"]["n"], "days": task["config"]["days"],
                             "core_manifest_status": manifest.get("status"), "core_error": manifest.get("error"),
                             "evidence_type": "bounded_actual_partial_attempt"})
        subset = [r for r in rows if r["task"] == watchdog["task"]]
        residual = watchdog["spawn_to_exit_wall_seconds"] - sum(r["wrapper_wall_seconds"] for r in subset)
        for row in subset:
            row["spawn_to_exit_wall_seconds"] = watchdog["spawn_to_exit_wall_seconds"]
            row["process_startup_gc_monitor_residual_seconds"] = residual
            row["rss_note"] = "CLI OS process high-water read before final output hashes; warm repeat inherits earlier high-water and allocator state. Whole-child guardian peak samples the complete child, including both fresh models."
            row["complete_child_sampled_peak_rss_mb"] = watchdog["sampled_peak_rss_mb"]
            phases = [row.get(k) for k in ("initialization_population_generation_seconds", "simulation_seconds", "statistics_seconds", "plotting_seconds")]
            row["whole_run_provenance_serialization_hash_io_residual_seconds"] = (row["wrapper_wall_seconds"] - sum(phases)
                if all(v is not None for v in phases) else None)
    atomic_csv(path / "measurements.csv", rows)
    atomic_csv(path / "resource_failures.csv", failures)
    if (path / "preflight-10000000.json").exists():
        atomic_csv(path / "preflight_refusals.csv", [json.loads((path / "preflight-10000000.json").read_text())])
    parallel = json.loads((path / "parallel.json").read_text()) if (path / "parallel.json").exists() else []
    consistency = []
    for world in range(8):
        subset = [r for r in rows if r["task"].startswith(("strong-", "weak-")) and r["task"].endswith(f"-world{world}")]
        hashes = {r["final_state_sha256"] for r in subset if r["status"] == "complete"}
        consistency.append({"world": 91000 + world, "complete_runs": len([r for r in subset if r["status"] == "complete"]),
                            "final_state_unique_hashes": len(hashes), "strict_same_platform_equal": len(hashes) == 1 if hashes else None})
    for row in parallel:
        baseline = next((v for v in parallel if v["kind"] == row["kind"] and v["repeat"] == row["repeat"] and v["workers"] == 1), None)
        if baseline and baseline["status"] == "complete" and row["status"] == "complete":
            if row["kind"] == "strong":
                row["speedup"] = baseline["batch_wall_seconds"] / row["batch_wall_seconds"]
                row["parallel_efficiency"] = row["speedup"] / row["workers"]
            else:
                row["weak_scaling_efficiency"] = baseline["batch_wall_seconds"] / row["batch_wall_seconds"]
        else:
            row["worlds_per_second"] = None
            row["speedup"] = None
            row["parallel_efficiency"] = None
            row["weak_scaling_efficiency"] = None
    atomic_csv(path / "parallel_summary.csv", parallel)
    atomic_json(path / "parallel_consistency.json", consistency)
    grouped = []
    for n in (120, 1000, 10000, 100000):
        for temperature in ("fresh_interpreter", "same_interpreter_fresh_model"):
            subset = [r for r in rows if r["task"].startswith("single-") and r["n"] == n and r["temperature"] == temperature and r["status"] == "complete"]
            if not subset:
                continue
            summary = {"n": n, "days": subset[0]["days"], "temperature": temperature, "complete_repeats": len(subset), "source_sha256": subset[0]["source_sha256"]}
            for key in ("initialization_population_generation_seconds", "simulation_seconds", "statistics_seconds", "plotting_seconds",
                        "provenance_serialization_hash_io_residual_seconds", "whole_run_provenance_serialization_hash_io_residual_seconds",
                        "core_total_wall_seconds", "wrapper_wall_seconds", "cpu_seconds", "peak_process_rss_mb", "complete_child_sampled_peak_rss_mb",
                        "work_claims_per_simulation_second", "output_bytes", "disk_read_bytes", "disk_write_bytes"):
                values = [r[key] for r in subset if r[key] is not None]
                summary[key + "_median"] = statistics.median(values) if values else None
                summary[key + "_min"] = min(values) if values else None
                summary[key + "_max"] = max(values) if values else None
            grouped.append(summary)
    atomic_csv(path / "scale_summary.csv", grouped)
    atomic_json(path / "summary.json", {"complete_run_measurements": sum(r["status"] == "complete" for r in rows),
                                        "failed_run_measurements": sum(r["status"] != "complete" for r in rows), "resource_failed_tasks": failures, "scale_cells": grouped,
                                        "parallel_consistency": consistency, "unmeasured_population_generation_subphase": True,
                                        "uncertainty": "min/max across timed repeats, not population or statistical confidence intervals"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "summarize", "cloud-dry-run", "_child"))
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "benchmarks")
    parser.add_argument("--path", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--mode", choices=("all", "single", "parallel"), default="all")
    parser.add_argument("--scenario", choices=("single", "batch", "monthly"), default="batch")
    args = parser.parse_args()
    if args.command == "plan":
        print(json.dumps(plan(args.repeats), indent=2))
    elif args.command == "cloud-dry-run":
        rates = json.loads((ROOT / "docs" / "cloud_costs.json").read_text())
        scenario = rates["scenarios"][args.scenario]
        vm_hours = scenario["job_vm_hours"] * (1 + scenario["retry_fraction"]) + scenario["idle_vm_hours"]
        compute = vm_hours * rates["rates"]["vm_usd_per_hour"]
        storage = scenario["storage_gib"] * scenario["storage_hours"] * rates["rates"]["balanced_disk_usd_per_gib_hour"]
        egress = scenario["egress_gib"] * rates["rates"]["egress_usd_per_gib"]
        total = compute + storage + egress
        admissible = total <= scenario["budget_usd"] and vm_hours <= scenario["max_vm_hours"]
        print(json.dumps({"mode": "dry_run_no_resources_started", "region": rates["region"], "machine": rates["machine"], "currency": "USD",
                          "scenario": args.scenario, "compute_vm_hours_including_retry_idle": vm_hours, "compute_usd": compute,
                          "storage_usd": storage, "egress_usd": egress, "total_usd": total, "budget_usd": scenario["budget_usd"],
                          "within_planning_limits": admissible, "performance_is_assumed_not_cloud_measured": True,
                          "billing_controls_are_planning_only": True}, indent=2))
        return 0 if admissible else 2
    elif args.command == "_child":
        return child(args.path)
    elif args.command == "summarize":
        summarize(args.path)
    else:
        if not 1 <= args.repeats <= 5:
            parser.error("repeats must be in [1,5]")
        print(json.dumps({"benchmark_path": str(execute(args.output, args.repeats, args.mode))}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
