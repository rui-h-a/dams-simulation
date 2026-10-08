"""Public command-line entry: doctor, smoke, run, reproduce, benchmark, resume."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
from pathlib import Path
import platform
try:
    import resource
except ImportError:  # Windows runs remain untested; report unavailable measurement.
    resource = None
import shutil
import sys
import time

from .config import Config
from .model import Model
from .report import report
from .storage import atomic_csv, atomic_json, canonical, digest, output_hashes, provenance, source_hash, unique_run, verify_outputs, atomic_stream, file_digest


def rss_mb() -> float | None:
    if resource is None:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value/(1024*1024) if sys.platform == "darwin" else value/1024


def doctor() -> dict:
    available_ram = None
    try:
        available_ram = os.sysconf("SC_PHYS_PAGES")*os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        pass
    return {"python": platform.python_version(), "minimum_python": "3.11", "os": platform.platform(),
            "architecture": platform.machine(), "logical_cpus": os.cpu_count(), "physical_ram_bytes": available_ram,
            "free_disk_bytes": shutil.disk_usage(Path.cwd()).free, "gpu_required": False,
            "network_required_for_run": False, "runtime_dependencies": [], "measured_current_environment": True}


def load_config(args: argparse.Namespace) -> Config:
    values = json.loads(args.config.read_text()) if args.config else {}
    for name in ("n", "days", "regime", "backend", "world", "seed"):
        value = getattr(args, name, None)
        if value is not None:
            values[name] = value
    return Config.from_dict(values)


def run_world(p: Config, path: Path, *, checkpoint_day: int | None = None, restored: Model | None = None, restart_origin: dict | None = None,
              checkpoint_interval_days: int | None = None, checkpoint_interval_seconds: float = 60,
              stop_requested=None, metadata_extra: dict | None = None) -> dict:
    wall, cpu = time.monotonic(), time.process_time()
    metadata = provenance()
    metadata.update({"run_id": path.name, "status": "running", "config_sha256": digest(canonical(p.to_dict())), "config": p.to_dict()})
    if metadata_extra:
        metadata.update(metadata_extra)
    if restart_origin is not None:
        metadata["restart_origin"] = restart_origin
    atomic_json(path/"manifest.json", metadata)
    model = restored
    last_checkpoint_day = model.day if model is not None else 0
    last_checkpoint_time = wall
    def checkpoint():
        nonlocal last_checkpoint_day, last_checkpoint_time
        name = f"checkpoint-day-{model.day:06d}.json"
        def writer(stream):
            stream.write('{"config_sha256":'); stream.write(canonical(metadata['config_sha256']))
            stream.write(',"source_sha256":'); stream.write(canonical(metadata['source_sha256']))
            stream.write(',"state":'); model.write_state(stream); stream.write('}\n')
        atomic_stream(path/name, writer, max_bytes=int(p.max_output_mb*1_000_000))
        previous = []
        if (path/'checkpoint-index.json').exists():
            old = json.loads((path/'checkpoint-index.json').read_text())
            previous = [r for r in old['snapshots'] if r['file'] != name][:1]
        index = {'source_sha256': metadata['source_sha256'], 'config_sha256': metadata['config_sha256'],
                 'snapshots': [{'file':name, 'sha256':file_digest(path/name), 'day':model.day}, *previous],
                 'attempt_elapsed_wall_seconds':time.monotonic()-wall}
        atomic_json(path/'checkpoint-index.json',index)
        keep = {r['file'] for r in index['snapshots']}
        for old in path.glob('checkpoint-day-*.json'):
            if old.name not in keep: old.unlink()
        last_checkpoint_day, last_checkpoint_time = model.day, time.monotonic()
    try:
        start_init = time.monotonic()
        if model is None:
            model = Model(p)
        init_wall = time.monotonic()-start_init
        start_sim = time.monotonic()
        if checkpoint_day is not None:
            model.run(checkpoint_day)
            atomic_json(path/"checkpoint.json", {"source_sha256": source_hash(), "state": model.state()})
        elif checkpoint_interval_days is not None:
            while model.day < p.days:
                model.check_resources()
                if stop_requested is not None and stop_requested():
                    checkpoint()
                    raise InterruptedError("interrupted at a completed-day boundary; checkpoint retained")
                if time.monotonic()-wall > p.max_wall_seconds:
                    checkpoint()
                    raise TimeoutError("whole-attempt deadline exceeded; checkpoint retained")
                model.step()
                if (model.day-last_checkpoint_day >= checkpoint_interval_days or
                    time.monotonic()-last_checkpoint_time >= checkpoint_interval_seconds):
                    checkpoint()
            checkpoint()
        else:
            model.run()
        sim_wall = time.monotonic()-start_sim
        start_stats = time.monotonic()
        summary = model.summary()
        stats_wall = time.monotonic()-start_stats
        start_serialization = time.monotonic()
        atomic_json(path/"summary.json", summary)
        atomic_csv(path/"timeseries.csv", model.history)
        atomic_stream(path/"final_state.json", lambda stream: (model.write_state(stream), stream.write("\n")),
                      max_bytes=int(p.max_output_mb*1_000_000))
        serialization_wall = time.monotonic()-start_serialization
        model.check_resources()
        plot_start = time.monotonic()
        report(path, model.history, summary)
        plotting_wall = time.monotonic()-plot_start
        size = sum(f.stat().st_size for f in path.iterdir() if f.is_file())
        if size > p.max_output_mb*1_000_000:
            raise RuntimeError("output resource limit exceeded; run is not complete")
        hash_start = time.monotonic()
        hashes = output_hashes(path)
        hashing_wall = time.monotonic()-hash_start
        metadata.update({"status": "checkpointed" if model.day < p.days else "complete", "exit_code": 0,
                         "initialization_wall_seconds": init_wall, "simulation_wall_seconds": sim_wall,
                         "statistics_wall_seconds": stats_wall, "serialization_wall_seconds":serialization_wall,
                         "hashing_wall_seconds":hashing_wall, "total_wall_seconds": time.monotonic()-wall,
                         "cpu_seconds": time.process_time()-cpu, "peak_process_rss_mb": rss_mb(), "output_bytes": size,
                         "network_io_bytes": 0, "rss_measurement_unit": "MiB", "output_limit_unit": "decimal MB", "compiled_kernel_seconds": 0, "plotting_seconds": plotting_wall,
                         "output_sha256": hashes})
        atomic_json(path/"manifest.json", metadata)
        return {"run_path": str(path), "status": metadata["status"], "summary": summary}
    except Exception as error:
        # Do not allocate another full state on an RSS/OOM failure. Preserve any
        # existing checkpoint rather than pretending all failures are restartable.
        if model is not None and not isinstance(error, MemoryError):
            if checkpoint_interval_days is not None:
                # The last periodic snapshot is already durable. Saving again on
                # a disk/output error would mask the original failure.
                if isinstance(error, (InterruptedError, TimeoutError)) and model.day != last_checkpoint_day:
                    checkpoint()
            else:
                atomic_json(path/"checkpoint.json", {"source_sha256": source_hash(), "state": model.state()})
        metadata.update({"status": "failed", "exit_code": 1, "error_type": type(error).__name__, "error": str(error),
                         "days_completed": model.day if model is not None else 0, "total_wall_seconds": time.monotonic()-wall,
                         "output_sha256": output_hashes(path)})
        atomic_json(path/"manifest.json", metadata)
        raise


def reproduce(p: Config, path: Path, worlds: int) -> dict:
    # Pre-specified set, no sequential significance stopping. Each row is an independent world-policy run.
    rows = []
    start = time.monotonic()
    metadata = {**provenance(), "run_id": path.name, "status": "running", "worlds": worlds, "config": p.to_dict()}
    atomic_json(path/"manifest.json", metadata)
    try:
        for world in range(p.world, p.world+worlds):
            for regime in ("equal", "linear", "sublinear", "hierarchy", "hierarchy_tenure"):
                for backend in ("central", "witness", "consensus"):
                    config = dataclasses.replace(p, world=world, regime=regime, backend=backend)
                    child = path/f"world-{world:04d}-{regime}-{backend}"
                    child.mkdir(exist_ok=False)
                    rows.append(run_world(config, child)["summary"])
    except Exception as error:
        atomic_csv(path/"world_summary.csv", rows)
        atomic_json(path/"manifest.json", {**metadata, "status": "failed", "exit_code": 1, "completed_policy_runs": len(rows),
                                          "error": str(error), "total_wall_seconds": time.monotonic()-start, "output_sha256": output_hashes(path)})
        raise
    atomic_csv(path/"world_summary.csv", rows)
    atomic_json(path/"manifest.json", {**metadata, "status": "complete", "exit_code": 0,
                                      "total_wall_seconds": time.monotonic()-start, "output_sha256": output_hashes(path)})
    return {"run_path": str(path), "worlds": worlds, "policy_runs": len(rows), "status": "complete"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "smoke", "run", "reproduce", "benchmark", "resume", "pipeline"))
    parser.add_argument("--spec", default="validation")
    parser.add_argument("--scale", type=int)
    parser.add_argument("--runtime-limits", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, default=Path("runs"))
    parser.add_argument("--n", type=int)
    parser.add_argument("--days", type=int)
    parser.add_argument("--world", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--regime")
    parser.add_argument("--backend")
    parser.add_argument("--worlds", type=int, default=3)
    parser.add_argument("--checkpoint-day", type=int)
    parser.add_argument("--checkpoint", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "pipeline":
            from .pipeline import run_pipeline
            result = run_pipeline(args.spec, args.scale, args.output, args.runtime_limits)
        elif args.command == "doctor":
            result = doctor()
        else:
            p = load_config(args)
            if args.command == "smoke":
                p = dataclasses.replace(p, n=24, days=12, guilds=3, sites=2).validate()
            path = unique_run(args.output, args.command)
            if args.command == "resume":
                if not args.checkpoint:
                    raise ValueError("resume requires --checkpoint")
                origin = json.loads((args.checkpoint.parent/"manifest.json").read_text())
                verify_outputs(args.checkpoint.parent, origin, required=(args.checkpoint.name,))
                value = json.loads(args.checkpoint.read_text())
                if value["source_sha256"] != source_hash():
                    raise ValueError("checkpoint source hash differs; migration is not supported")
                restored = Model.restore(value["state"])
                restart_origin = {"parent_run_id":origin["run_id"],
                    "parent_manifest_sha256":digest((args.checkpoint.parent/"manifest.json").read_bytes()),
                    "checkpoint_file":args.checkpoint.name,"checkpoint_sha256":digest(args.checkpoint.read_bytes()),
                    "parent_source_sha256":origin["source_sha256"],"parent_config_sha256":origin["config_sha256"]}
                result = run_world(restored.config, path, restored=restored, restart_origin=restart_origin)
            elif args.command == "reproduce":
                if not 2 <= args.worlds <= 1000:
                    raise ValueError("worlds must be between 2 and 1000")
                result = reproduce(p, path, args.worlds)
            else:
                result = run_world(p, path, checkpoint_day=args.checkpoint_day)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError, TimeoutError, MemoryError) as error:
        print(f"dams-sim: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
