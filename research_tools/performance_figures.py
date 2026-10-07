"""Generate DAMS infrastructure tables/figures from retained CPU measurements.

Large-scale ranges are conditional planning envelopes, not confidence intervals
or certified execution. No cloud resource is provisioned by this script.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys


def solve(matrix: list[list[float]], values: list[float]) -> list[float]:
    rows = [list(row) + [value] for row, value in zip(matrix, values)]
    for i in range(len(rows)):
        pivot = max(range(i, len(rows)), key=lambda j: abs(rows[j][i]))
        rows[i], rows[pivot] = rows[pivot], rows[i]
        value = rows[i][i]
        if abs(value) < 1e-12:
            raise ValueError("unidentified growth model")
        rows[i] = [v / value for v in rows[i]]
        for j in range(len(rows)):
            if i != j:
                factor = rows[j][i]
                rows[j] = [a - factor * b for a, b in zip(rows[j], rows[i])]
    return [row[-1] for row in rows]


def fit(points: list[dict], target: str) -> tuple[list[float], list[dict]]:
    x = [[1.0, p["n"] / 100_000, p["n"] * p["days"] / 300_000] for p in points]
    y = [p[target] for p in points]
    # Exact active-set search for the three nonnegative cost coefficients.
    # This is constrained least squares, not clipping a negative unconstrained
    # fit. Every boundary submodel is refitted and compared by actual residual.
    candidates = []
    for mask in range(1, 8):
        active = [i for i in range(3) if mask & (1 << i)]
        normal = [[sum(row[i] * row[j] for row in x) for j in active] for i in active]
        rhs = [sum(row[i] * value for row, value in zip(x, y)) for i in active]
        try:
            fitted = solve(normal, rhs)
        except ValueError:
            continue
        if any(c < 0 for c in fitted):
            continue
        coefficients = [0.0] * 3
        for i, value in zip(active, fitted):
            coefficients[i] = value
        residual = sum((sum(a * b for a,b in zip(row, coefficients))-value)**2 for row,value in zip(x,y))
        candidates.append((residual, coefficients))
    if not candidates:
        raise ValueError("no admissible nonnegative growth model")
    coefficients = min(candidates, key=lambda v: v[0])[1]
    residuals = [{"n": p["n"], "days": p["days"], "observed": value,
                  "fitted": sum(a * b for a, b in zip(row, coefficients)),
                  "relative_error": (sum(a * b for a, b in zip(row, coefficients)) - value) / value}
                 for p, row, value in zip(points, x, y)]
    return coefficients, residuals


def load(path: Path) -> tuple[list[dict], list[dict], dict]:
    status = json.loads((path / "status.json").read_text())
    if status["status"] != "finished_with_retained_failures" or not status.get("source_unchanged"):
        raise ValueError("formal timing batch is incomplete or source-invalidated")
    with (path / "scale_summary.csv").open() as stream:
        scales = [{k: v if k in {"temperature", "source_sha256"} else float(v) if v else None for k, v in row.items()}
                  for row in csv.DictReader(stream)]
    with (path / "parallel_summary.csv").open() as stream:
        parallel = [{k: v if k in {"kind", "status"} else float(v) if v else None for k, v in row.items()}
                    for row in csv.DictReader(stream)]
    if any(r["status"] != "complete" for r in parallel):
        raise ValueError("do not plot failed parallel batches as measured speedup")
    plan = json.loads((path / "plan.json").read_text())
    if any(s["source_sha256"] != plan["source_sha256"] for s in scales):
        raise ValueError("mixed timing sources")
    consistency = json.loads((path / "parallel_consistency.json").read_text())
    if len(consistency) != 8 or any(c["final_state_unique_hashes"] != 1 or c["strict_same_platform_equal"] is not True for c in consistency):
        raise ValueError("parallel worlds do not retain one consistent final-state hash")
    if any("wrapper_wall_seconds_median" not in s or "complete_child_sampled_peak_rss_mb_median" not in s for s in scales):
        raise ValueError("resummarize retained raw measurements with benchmark.py before publication")
    # Check derived CSV values against retained measurement/watchdog JSONs.
    # Matching a source label alone does not validate a derived timing table.
    raw = []
    for file in sorted(path.glob("*/measurements.json")):
        watchdog = json.loads((file.parent / "watchdog.json").read_text())
        for row in json.loads(file.read_text()):
            if row["source_sha256"] != plan["source_sha256"] or row["status"] != "complete":
                raise ValueError("raw complete-world measurement differs from the pinned source/status")
            row["complete_child_sampled_peak_rss_mb"] = watchdog["sampled_peak_rss_mb"]
            row["whole_run_provenance_serialization_hash_io_residual_seconds"] = row["wrapper_wall_seconds"] - sum(
                row[k] for k in ("initialization_population_generation_seconds", "simulation_seconds", "statistics_seconds", "plotting_seconds"))
            raw.append(row)
    expected = {(cell["n"],cell["days"],temp):cell["cold_processes"] for cell in plan["single_world"]
                for temp in ("fresh_interpreter","same_interpreter_fresh_model")}
    if {(int(s["n"]),int(s["days"]),s["temperature"]) for s in scales} != set(expected):
        raise ValueError("formal scale cells differ from the actual measurement plan")
    for summary in scales:
        cell=(int(summary["n"]),int(summary["days"]),summary["temperature"])
        subset=[r for r in raw if r["task"].startswith("single-") and (r["n"],r["days"],r["temperature"])==cell]
        if len(subset) != expected[cell] or summary["complete_repeats"] != len(subset):
            raise ValueError("formal timing repetition count differs from raw measurements")
        for key,value in summary.items():
            if not key.endswith(("_median","_min","_max")):
                continue
            field,bound=key.rsplit("_",1)
            values=[r[field] for r in subset if r[field] is not None]
            observed=(statistics.median(values) if bound=="median" else min(values) if bound=="min" else max(values)) if values else None
            if observed != value and (observed is None or value is None or not math.isclose(observed,value,rel_tol=1e-12,abs_tol=1e-10)):
                raise ValueError("derived scale CSV disagrees with raw field: "+key)
    batches=json.loads((path / "parallel.json").read_text())
    if len(parallel) != len(batches):
        raise ValueError("parallel CSV batch count differs from raw batches")
    for row in parallel:
        batch=next((b for b in batches if all(b[k]==row[k] for k in ("kind","repeat","workers"))),None)
        if batch is None or any(row[k] != v for k,v in batch.items()):
            raise ValueError("parallel CSV differs from the raw recorded batch")
        baseline=next(b for b in batches if b["kind"]==row["kind"] and b["repeat"]==row["repeat"] and b["workers"]==1)
        field="speedup" if row["kind"]=="strong" else "weak_scaling_efficiency"
        if not math.isclose(row[field],baseline["batch_wall_seconds"]/batch["batch_wall_seconds"],rel_tol=1e-12):
            raise ValueError("parallel derived scaling ratio is incorrect")
    return scales, parallel, plan


def projections(path: Path, scales: list[dict], plan: dict) -> dict:
    cold = [s for s in scales if s["temperature"] == "fresh_interpreter"]
    points = [{"n": int(s["n"]), "days": int(s["days"]), "rss_mb": s["complete_child_sampled_peak_rss_mb_median"],
               "output_mb": s["output_bytes_median"] / 1_000_000} for s in cold]
    memory, memory_residuals = fit(points, "rss_mb")
    output, output_residuals = fit(points, "output_mb")
    withheld = []
    for target in ("rss_mb", "output_mb"):
        for index,point in enumerate(points):
            coefficients,_ = fit([p for i,p in enumerate(points) if i!=index],target)
            fitted = sum(a*b for a,b in zip([1,point["n"]/100_000,point["n"]*point["days"]/300_000],coefficients))
            withheld.append({"target":target,"withheld_n":point["n"],"withheld_days":point["days"],
                             "observed":point[target],"predicted":fitted,"relative_error":(fitted-point[target])/point[target],
                             "training_cells":3,"diagnostic":"leave_one_workload_cell_out_refit; not independent hardware validation"})
    init_rates = [s["initialization_population_generation_seconds_" + bound] / s["n"] for s in cold for bound in ("min", "max")]
    sim_rates = [s["simulation_seconds_" + bound] / (s["n"] * s["days"]) for s in cold for bound in ("min", "max")]
    stats_rates = [s["statistics_seconds_" + bound] / (s["n"] * s["days"]) for s in cold for bound in ("min", "max")]
    plot_rates = [s["plotting_seconds_" + bound] / s["days"] for s in cold for bound in ("min", "max")]
    io_rates = [s["whole_run_provenance_serialization_hash_io_residual_seconds_" + bound] / s["output_bytes_median"] for s in cold for bound in ("min", "max")]
    rows = []
    for n in (100_000, 1_000_000, 10_000_000):
        days, events = 30, n * 30
        x = [1, n / 100_000, events / 300_000]
        rss = sum(a * b for a, b in zip(x, memory))
        mb = sum(a * b for a, b in zip(x, output))
        growth = max(1, math.log2(events) / math.log2(300_000))
        # Lower envelope retains the best observed phase rates. Upper envelope
        # adds logarithmic heap/sort growth and an explicit factor-two margin.
        time_low = (min(init_rates) * n + min(sim_rates) * events + min(stats_rates) * events
                    + min(plot_rates) * days + min(io_rates) * mb * 1_000_000)
        time_high = 2 * (max(init_rates) * n + max(sim_rates) * events * growth
                         + max(stats_rates) * events * growth + max(plot_rates) * days
                         + max(io_rates) * mb * 1_000_000 * growth)
        rows.append({"n": n, "days": days, "work_events": events, "evidence_type": "conditional_extrapolation_unexecuted",
                     "time_low_seconds": time_low, "time_high_seconds": time_high,
                     "rss_central_mb": rss, "rss_low_mb": 0.5 * rss, "rss_high_mb": 2 * rss,
                     "output_central_mb": mb, "upper_logarithmic_growth_factor": growth,
                     "default_event_limit_would_refuse": events > 2_000_000})
    result = {"source_sha256": plan["source_sha256"], "equation": "intercept + b_N*(N/100000) + b_E*(N*T/300000), nonnegative constrained least squares",
              "fit_method": "Exact active-set constrained least squares across all nonempty subsets of three cost coefficients; negative unconstrained output intercept rejected, boundary model refitted rather than clipped.",
              "memory_coefficients_mb": memory, "output_coefficients_decimal_mb": output,
              "memory_local_fit_residuals": memory_residuals, "output_local_fit_residuals": output_residuals,
              "leave_one_workload_out_diagnostics": withheld,
              "memory_observation": "Median of three whole-child guardian RSS peaks at 50 ms cadence. Each child includes two fresh models; this captures final hashing and allocator carryover conservatively, but is a sampled process peak rather than a reset per-world high-water.",
              "runtime_phase_rate_ranges": {"initialization_seconds_per_person": [min(init_rates), max(init_rates)],
                                             "simulation_seconds_per_work_claim": [min(sim_rates), max(sim_rates)],
                                             "statistics_seconds_per_work_claim": [min(stats_rates), max(stats_rates)],
                                             "diagnostic_plotting_seconds_per_day": [min(plot_rates), max(plot_rates)],
                                             "residual_seconds_per_output_byte": [min(io_rates), max(io_rates)]},
              "projections": rows, "independent_external_validation": False,
              "scope": "Four measured workload cells, N=120..100000, T=3..60, emitted claims=7200..300000, G=max(4,N//100), team five, ring one. Not a fit for fixed G, altered backlog, other backends, long horizons or optimized/aggregated agents.",
              "uncertainty": "Broad planning envelopes, not confidence intervals, guaranteed memory bounds or measured completion. Coefficients fit the same local observations; small residual is not independent validation. Memory 0.5x..2x central prediction reflects structural/allocator uncertainty. Time upper adds observed phase maxima, logarithmic heap/sort growth and explicit 2x margin. Increasing N/T far beyond observed event counts can invalidate these forms.",
              "rationale": "Explicit Agent/Claim Python objects, per-agent snapshots and sparse maps contribute N; seen IDs, completed delays and outstanding claims contribute N*T. Full-state dict/JSON serialization duplicates retained state. Summary quantiles sort completed delay histories, so their observed per-claim rate receives the event logarithmic margin; diagnostic SVG generation scales with recorded days. Two measured cells share 300000 claims but differ tenfold in N, separating population and retained-event terms locally."}
    (path / "conditional_projections.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    with (path / "projection_withheld_diagnostics.csv").open("w", newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(withheld[0]));writer.writeheader();writer.writerows(withheld)
    with (path / "conditional_projections.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return result


def grouped_parallel(parallel: list[dict], kind: str, key: str) -> list[dict]:
    out = []
    for workers in (1, 2, 4):
        rows = [r for r in parallel if r["kind"] == kind and r["workers"] == workers]
        values = [r[key] for r in rows]
        out.append({"workers": workers, "median": statistics.median(values), "min": min(values), "max": max(values)})
    return out


def latex_tables(path: Path, scales: list[dict], parallel: list[dict], plan: dict, projection: dict, target: Path) -> None:
    lines = ["% Automatically generated from CPU benchmark CSVs; do not hand edit.",
             "% Source: " + plan["source_sha256"],
             r"\begin{table}[tbp]", r"\centering\small",
             r"\caption{Research resource tiers. The M2 Pro environment is measured; lower provisions, HPC and cloud are conditional plans, not certified platforms. CPU-model members are distinct from ledger nodes.}",
             r"\label{tab:infrastructure-tiers}",
             r"\begin{tabular}{@{}>{\raggedright\arraybackslash}p{.14\textwidth}>{\raggedright\arraybackslash}p{.24\textwidth}>{\raggedright\arraybackslash}p{.54\textwidth}@{}}",
             r"\toprule Tier & Environment & Explicit workload and limits \\ \midrule",
             r"Minimum & Measured: M2 Pro, 12 cores, 16 GiB, macOS 26.6, Python 3.14.6 & $N=120,T=60,G=4,S=2$; team size 5, ring degree 1; all daily modules, one world at a time. A 2-core/4--8 GiB machine is an untested estimated lower provision. No GPU/MPI/runtime network; keep raw state and traces. \\ \addlinespace",
             r"Recommended & Same measured workstation; other 8-core/16--32 GiB SSD systems untested & $N=1{,}000$--$10{,}000,T=30,G=N/100,S=2$--8. One to four independent worlds; the four-worker speedup is measured at $N=1{,}000$, not certified for all larger workloads. Model cap 1.5 GiB/child, external cap 2 GiB; keep at least 4 GiB OS headroom and twice projected outputs plus 5 GiB free disk. \\ \addlinespace",
             r"Large/HPC & Untested Linux CPU node, 16+ physical cores, 64--128 GiB RAM, NVMe/scratch & Recheck $N=100{,}000,T=3$ first; longer or million-person individual worlds require explicit event/RAM/output caps and new profiling. Independent-world scheduler jobs, no implemented MPI split. 8 h wall budget and 100 GiB scratch are planning values; no certified GPU kernel. \\ \addlinespace",
             r"Cloud burst & Untested GCP E2, us-central1, 4 vCPU/16 GiB & First profile $N\leq10{,}000,T=30$ with the same modules, up to two worlds; 50 GiB balanced disk. Batch plan: 12 VM-hour maximum, USD 10 budget, checkpoint/stop rules required before provision. Network for setup/transfer; no paid resource was started. \\",
             r"\bottomrule\end{tabular}", r"\end{table}",
             r"\begin{table}[tbp]", r"\centering\small",
             r"\caption{Sublinear/central CPU reference worlds on the M2 Pro: medians of three fresh child processes, each followed by one fresh-model warm repeat (C/W). $T$ differs across cells; these are explicit workloads, not a population-only scaling experiment. Initialization includes population generation. Run wall and CPU include output hashing, final manifest and measurement readback, excluding process launch. RSS is OS high-water (MiB) read before final output hashes; warm values inherit earlier allocation/high-water. Whole-child guardian peaks are retained separately.}",
             r"\label{tab:benchmark}",
             r"\setlength{\tabcolsep}{4pt}",
             r"\begin{tabular}{@{}rrcrrrrrr@{}}",
             r"\toprule $N$ & $T$ (d) & C/W & Init. (s) & Sim. (s) & Run (s) & CPU (s) & RSS & Output (MB) \\ \midrule"]
    for s in scales:
        temp = "C" if s["temperature"] == "fresh_interpreter" else "W"
        n = f"{int(s['n']):,}".replace(",", "{,}")
        lines.append(f"${n}$ & {int(s['days'])} & {temp} & {s['initialization_population_generation_seconds_median']:.3f} & {s['simulation_seconds_median']:.3f} & {s['wrapper_wall_seconds_median']:.3f} & {s['cpu_seconds_median']:.3f} & {s['peak_process_rss_mb_median']:.1f} & {s['output_bytes_median']/1_000_000:.2f} " + r"\\")
    lines.extend([r"\bottomrule\end{tabular}", r"\end{table}"])
    stop = json.loads((path / "partial-n1000000-t1" / "watchdog.json").read_text())
    strong = grouped_parallel(parallel, "strong", "speedup")
    weak = grouped_parallel(parallel, "weak", "weak_scaling_efficiency")
    lines.append(f"The million-person, one-day attempt stopped after {stop['spawn_to_exit_wall_seconds']:.2f} s at sampled resident memory {stop['sampled_peak_rss_mb']:.1f} MiB under the 1 GiB watchdog; it produced no complete world. The ten-million-person request was refused by the default two-million-event bound before population allocation. For four matched independent worlds of 1,000 people over 30 days, median speedups were {strong[1]['median']:.2f} and {strong[2]['median']:.2f} with two and four workers; weak-scaling efficiencies were {weak[1]['median']:.3f} and {weak[2]['median']:.3f} with two worlds per worker. All eight world identifiers retained exactly one final-state hash across worker counts. These results support independent-world batching on this host, not single-world MPI or GPU claims.")
    lines.append("The exact full benchmark source, rule-module hash and complete source snapshot remain available with the raw manifests. Final publication mode requires that this batch's full-core hash equals the currently installed core. Statistics, SVG generation, serialization/hash/I/O residuals, CPU time and kernel-accounted disk bytes are recorded in the public benchmark CSVs; a separate population-generation subphase was not timed. Planning envelopes outside the measured range are conditional on the retained Python objects, queues and full-state serialization, not certified capacity.")
    worst=max((d for d in projection["leave_one_workload_out_diagnostics"] if d["target"]=="rss_mb"),key=lambda d:abs(d["relative_error"]))
    lines.append(f"A leave-one-workload-out memory refit misses the withheld {worst['withheld_n']:,}-person/{worst['withheld_days']}-day cell by {abs(worst['relative_error'])*100:.1f}\\%, despite small full-fit residuals. This checks local cross-workload sensitivity; it neither validates the structural coefficients nor measures prediction error at million-person scales. The factor-two planning margins are declared allowances, not empirically established coverage or guaranteed upper bounds.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n")


def figures(path: Path, scales: list[dict], parallel: list[dict], plan: dict, projection: dict, output: Path, fragment: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter
    # 9.6 pt in a 17 cm source remains approximately 9 pt when the thesis's
    # 15.92 cm text width scales the figure. Do not make printed labels tiny.
    plt.rcParams.update({"font.family": "serif", "font.serif": ["DejaVu Serif"], "font.size": 9.6, "axes.labelsize": 9.6,
                         "axes.titlesize": 10, "legend.fontsize": 9.6, "xtick.labelsize": 9.6, "ytick.labelsize": 9.6,
                         "text.color": "#202020", "axes.labelcolor": "#202020", "axes.edgecolor": "#555555",
                         "figure.facecolor": "white", "axes.facecolor": "white", "pdf.fonttype": 42, "svg.fonttype": "none",
                         "svg.hashsalt": "DAMS-performance-"+plan["source_sha256"]})
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    def save(fig, name):
        for suffix in ("pdf", "svg"):
            target = output / (name + "." + suffix)
            creator="DAMS retained CPU benchmark, source "+plan["source_sha256"]
            metadata=({"Creator":creator,"CreationDate":None,"ModDate":None} if suffix=="pdf" else {"Creator":creator,"Date":None})
            fig.savefig(target, metadata=metadata)
            paths.append(target)
        plt.close(fig)
    def style(ax):
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="#dddddd", linewidth=0.5)
        ax.set_axisbelow(True)
    fig, axes = plt.subplots(1, 2, figsize=(17/2.54, 8.8/2.54))
    for panel, (key, ylabel) in enumerate((("wrapper_wall_seconds", "Complete run wall time (s)"), ("peak_process_rss_mb", "OS high-water RSS (MiB)"))):
        ax = axes[panel]
        for temp, offset, marker, label, color in (("fresh_interpreter", -0.08, "o", "Process-cold", "#202020"), ("same_interpreter_fresh_model", 0.08, "s", "Fresh model, warm process", "#777777")):
            rows = [s for s in scales if s["temperature"] == temp]
            med = [s[key + "_median"] for s in rows]
            lo, hi = [s[key + "_min"] for s in rows], [s[key + "_max"] for s in rows]
            ax.errorbar([i+offset for i in range(len(rows))], med, yerr=[[a-b for a,b in zip(med,lo)], [b-a for a,b in zip(med,hi)]],
                        fmt=marker, color=color, markerfacecolor="white" if offset > 0 else color, markersize=5, capsize=3, linewidth=1, label=label)
        ax.set_xticks(range(4), ["120\n60 d", "1,000\n30 d", "10,000\n30 d", "100,000\n3 d"])
        ax.set_xlabel("People N and explicit horizon T")
        ax.set_ylabel(ylabel)
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(FixedLocator([0.1,1,10] if panel == 0 else [30,50,100,200,500]))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x,_: f"{x:g}"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.set_title("(a) Complete worlds" if panel == 0 else "(b) CLI high-water reading", loc="left")
        style(ax)
    axes[1].legend(loc="upper left", frameon=False)
    fig.tight_layout(pad=0.7, w_pad=1.7)
    save(fig, "benchmark_workloads")
    fig, axes = plt.subplots(1, 2, figsize=(17/2.54, 9.3/2.54))
    actual = [s for s in scales if s["temperature"] == "fresh_interpreter" and s["days"] == 30]
    for i, (key, lower, upper, ylabel, divisor) in enumerate((("wrapper_wall_seconds_median", "time_low_seconds", "time_high_seconds", "Complete run wall time (min)", 60),
                                                            ("complete_child_sampled_peak_rss_mb_median", "rss_low_mb", "rss_high_mb", "Whole-child sampled RSS (GiB)", 1024))):
        ax = axes[i]
        ax.plot([s["n"] for s in actual], [s[key]/divisor for s in actual], "o", color="#202020", label="Measured T=30")
        rows = projection["projections"]
        n = [r["n"] for r in rows]
        low, high = [r[lower]/divisor for r in rows], [r[upper]/divisor for r in rows]
        center = [math.sqrt(a*b) for a,b in zip(low,high)]
        ax.fill_between(n, low, high, color="#e4e4e4", hatch="///", edgecolor="#aaaaaa", linewidth=0.5, label="Planning envelope")
        ax.plot(n, center, "D--", color="#555555", markerfacecolor="white", markersize=4, linewidth=1, label="Unexecuted projection")
        ax.set_xscale("log");ax.set_yscale("log")
        ax.xaxis.set_major_locator(FixedLocator([1000,10000,100000,1000000,10000000]))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x,_: f"{int(x/1000)}k" if x<1_000_000 else f"{int(x/1_000_000)}m"))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x,_: f"{x:g}"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.set_xlabel("People N; 30-day projection")
        ax.set_ylabel(ylabel);ax.set_title("(a) Conditional completion time" if i==0 else "(b) Conditional memory", loc="left")
        style(ax)
    stop = json.loads((path / "partial-n1000000-t1" / "watchdog.json").read_text())
    axes[1].plot([1_000_000], [stop["sampled_peak_rss_mb"]/1024], "x", color="#202020", markersize=6)
    axes[1].annotate("T=1 guard stop\n(no complete world)", (1_000_000, stop["sampled_peak_rss_mb"]/1024), xytext=(-10,-35), textcoords="offset points", ha="center", fontsize=9.6)
    axes[1].axhline(16, color="#999999", linestyle=":", linewidth=0.8)
    axes[1].text(1.1e3, 17.5, "Local RAM: 16 GiB\n(reserve OS headroom)", fontsize=9.6, va="bottom")
    axes[0].legend(loc="upper left", frameon=False)
    fig.tight_layout(pad=0.7, w_pad=1.7)
    save(fig, "benchmark_projection")
    fig, axes = plt.subplots(1, 2, figsize=(17/2.54, 8.0/2.54))
    for panel, (kind, key, ylabel) in enumerate((("strong", "speedup", "Strong-scaling speedup"), ("weak", "weak_scaling_efficiency", "Weak-scaling efficiency"))):
        ax=axes[panel];rows=grouped_parallel(parallel, kind, key)
        xs,ys=[r["workers"] for r in rows],[r["median"] for r in rows]
        ax.errorbar(xs,ys,yerr=[[r["median"]-r["min"] for r in rows],[r["max"]-r["median"] for r in rows]],fmt="o-",color="#202020",markersize=5,capsize=3,linewidth=1,label="Measured median/range")
        ax.plot(xs, xs if kind=="strong" else [1]*3, "--",color="#888888",linewidth=1,label="Ideal reference")
        ax.set_xticks(xs);ax.set_xlabel("Independent-world workers");ax.set_ylabel(ylabel)
        upper=max(4.3 if kind=="strong" else 1.15, max(r["max"] for r in rows)*1.1)
        ax.set_ylim(0,upper);style(ax)
        ax.set_title("(a) Fixed four-world batch" if kind=="strong" else "(b) Two worlds per worker",loc="left")
    axes[0].legend(frameon=False,loc="upper left")
    fig.tight_layout(pad=0.7,w_pad=1.7);save(fig,"benchmark_parallel")
    sha=plan["source_sha256"][:12]
    fragments=[]
    captions=[("benchmark_workloads","Measured CPU reference workloads",f"Complete runs including output hashes, final manifests and measurement readback, excluding process launch, on Apple M2 Pro/macOS 26.6/Python 3.14.6, benchmark source {sha}. Points are medians and bars min--max of three child-process repeats; every warm repeat initializes a fresh full model. Horizons differ explicitly, so the populations are not a common-horizon scaling series. CLI RSS is OS high-water read before final output hashes; warm points inherit prior high-water and allocator state. The whole-child guardian peak is separately retained and used for conservative memory planning. All normal daily modules and full-state output are retained. This is software resource evidence, not organizational efficiency."),
              ("benchmark_projection","Conditional large-scale resource projections",f"Source {sha}; black circles are complete measured 30-day workloads, hollow diamonds/dashes are unexecuted 30-day projections. Hatched bands are deliberately broad structural planning envelopes, not confidence intervals or guaranteed limits. A fitted N plus NT memory/output model and observed phase rates with logarithmic heap/sort growth give these scenarios. Memory fits the median whole-child guardian peaks sampled every 50 ms, including both fresh models and final hashing, rather than a reset per-world high-water. Four measured cells (including 100,000 people for three days) fit local coefficients; they do not independently validate large-scale prediction. The cross marks the actual million-person one-day RSS stop, not a completed world's peak. All projected thirty-day requests exceed the default event limit; no completed results or cloud performance are implied."),
              ("benchmark_parallel","Independent-world CPU batching",f"Benchmark source {sha}; N=1,000, T=30, full modules and outputs. Strong scaling holds four matched worlds fixed; weak scaling holds two worlds per worker. Points are medians and bars min--max across three batches; dashed lines show ideal references. Launch, execution and monitoring enter batch wall time. Each of eight world IDs has exactly one final-state SHA-256 across worker counts and repeats. These are same-platform deterministic checks, not external validation, single-world MPI scaling or a GPU benchmark.")]
    for name,title,caption in captions:
        fragments.extend([r"\begin{figure}[tbp]",r"\centering",rf"\includegraphics[width=\linewidth]{{figures/results/{name}.pdf}}",rf"\caption{{{caption}}}",rf"\label{{fig:{name.replace('_','-')}}}",r"\end{figure}"])
    fragment.parent.mkdir(parents=True,exist_ok=True);fragment.write_text("\n".join(fragments)+"\n")
    (path / "publication_artifacts.json").write_text(json.dumps({"source_sha256":plan["source_sha256"],"generator_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"matplotlib":matplotlib.__version__,"files":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},sort_keys=True)+"\n")


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch",type=Path,required=True)
    parser.add_argument("--figures",type=Path,required=True)
    parser.add_argument("--tables",type=Path,required=True)
    parser.add_argument("--fragment",type=Path,required=True)
    parser.add_argument("--state",choices=("analysis-ready","final"),default="analysis-ready")
    args=parser.parse_args()
    scales,parallel,plan=load(args.batch)
    if args.state == "final":
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from dams_sim.storage import source_hash
        if source_hash() != plan["source_sha256"]:
            raise ValueError("final artifacts require exactly the current complete core source hash; retain older measurements as preliminary")
    projection=projections(args.batch,scales,plan)
    latex_tables(args.batch,scales,parallel,plan,projection,args.tables)
    figures(args.batch,scales,parallel,plan,projection,args.figures,args.fragment)
    publication=json.loads((args.batch/"publication_artifacts.json").read_text())
    publication.update({"state":args.state,"measurement_tool_sha256":plan["benchmark_tool_sha256"],
                        "summarizer_sha256":hashlib.sha256((Path(__file__).parent/"benchmark.py").read_bytes()).hexdigest(),
                        "derived_csv_checked_against_raw_json":True,
                        "raw_measurement_files_sha256":{str(p.relative_to(args.batch)):hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(args.batch.glob("*/measurements.json"))}})
    if args.state == "analysis-ready":
        # Preserve legitimate earlier-source numbers in their evidence directory,
        # but do not silently carry them into current final Findings/Results.
        (args.batch / "preliminary-infrastructure.tex").write_bytes(args.tables.read_bytes())
        (args.batch / "preliminary-performanceplots.tex").write_bytes(args.fragment.read_bytes())
        tables=args.tables.read_text().split(r"\begin{table}[tbp]",2)
        static_tiers=r"\begin{table}[tbp]"+tables[1]
        static_tiers=static_tiers.replace("the four-worker speedup is measured at", "the earlier-source four-worker speedup is preliminary at")
        pending=(r"% Analysis-ready: current-source benchmark still pending; earlier raw evidence retained separately."+"\n"+static_tiers+
                 "\n"+r"\begin{table}[tbp]\centering\small"+"\n"+
                 r"\caption{Planned final-source infrastructure measurements. Completed earlier-source benchmarks are retained as preliminary evidence and are excluded from these final cells.}"+"\n"+
                 r"\label{tab:benchmark}"+"\n"+
                 r"\begin{tabular}{@{}p{.9\textwidth}@{}}\toprule"+"\n"+
                 r"\SimulationPending{final-version-infrastructure-benchmark: whole-run time, phase times, RSS, output and parallel scaling}"+"\n"+
                 r"\\\bottomrule\end{tabular}\end{table}"+"\n")
        args.tables.write_text(pending)
        args.fragment.write_text(r"\SimulationPending{final-version-infrastructure-performance-figures}"+"\n")
    publication["files"].update({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.tables,args.fragment)})
    (args.batch/"publication_artifacts.json").write_text(json.dumps(publication,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"status":"generated","state":args.state,"batch":str(args.batch),"tables":str(args.tables),"figure_fragment":str(args.fragment)},indent=2))


if __name__ == "__main__":
    main()
