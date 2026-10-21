"""Publication of validated schema-3 longitudinal evidence, without simulation.

Worlds remain the independent unit. Missing windows, zero exposure, closure and
never-positive formal allocations remain visible. Synthetic learning/memory are
not innovation measures. No interpolation or outcome-dependent world selection.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile

from dams_sim.longitudinal_design import PRIMARY_ENDPOINTS, declared_contrasts
from dams_sim.longitudinal_pipeline import driver_hash
from dams_sim.storage import atomic_bytes, atomic_csv, atomic_json, file_digest, source_hash
from research_tools.figure_style import apply_style, style_metadata, MEASURED, REFERENCE, grid
from research_tools.validate_longitudinal import CheckedLongitudinalStudy
from research_tools import analyze as _dispatch, figure_style as _style, validate_longitudinal as _validator, longitudinal_derived as _derived
from research_tools.longitudinal_derived import (LIMITATIONS, DERIVED_DEFINITIONS, allocation_outcome, derive_case, common_trajectory_data, relative_trajectory_data)

VERSION = "DAMS-longitudinal-publication-2"
ROOT = Path(__file__).resolve().parents[1]
GENERATION = Path("generated/analysis/generation_manifest.json")
IMPORTED_CORE_SHA256 = source_hash()
IMPORTED_DRIVER_SHA256 = driver_hash()
IMPORTED_CODE_SHA256 = {
    "research_tools/longitudinal_analysis.py": file_digest(Path(__file__)),
    "research_tools/analyze.py": _dispatch.IMPORTED_MODULE_SHA256,
    "research_tools/figure_style.py": _style.IMPORTED_MODULE_SHA256,
    "research_tools/validate_longitudinal.py": _validator.VALIDATOR_IMPORT_SHA256,
    "research_tools/longitudinal_derived.py": _derived.IMPORTED_MODULE_SHA256,
}
LABELS = {
    "work_per_present_member_workday": ("Produced work", "Work units / present-member workday"),
    "decision_regret_per_decision": ("Decision regret", "Regret units / completed decision"),
    "net_resource_gain_per_present_member_workday": ("Net resource gain", "Resource units / present-member workday"),
    "closed_by_common_end": ("Closure risk", "Paired closure probability difference"),
}


def require_loaded_sources():
    if source_hash() != IMPORTED_CORE_SHA256 or driver_hash() != IMPORTED_DRIVER_SHA256:
        raise ValueError("scientific source changed after publication-module import")
    if any(file_digest(ROOT / name) != sha for name, sha in IMPORTED_CODE_SHA256.items()):
        raise ValueError("analyzer/style/validator source changed after import")


def publication_tree(path):
    if path.is_symlink():
        raise ValueError("publication tree is symlinked")
    if path.exists() and not path.is_dir():
        raise ValueError("publication destination is not a directory")
    for child in path.rglob('*'):
        if child.is_symlink() or not (child.is_file() or child.is_dir()):
            raise ValueError("publication tree contains a symlink or special node")


def publication_signature(path):
    publication_tree(path)
    return {'exists': path.exists(), 'files': {item.relative_to(path).as_posix(): file_digest(item)
            for item in path.rglob('*') if item.is_file()}}


def tex(value):
    mapping = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
               "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(mapping.get(c, c) for c in str(value))


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def number(value):
    return "undefined" if value is None else f"{value:.4g}"


def validate_estimates(rows):
    """Never turn an absent estimate into a plotted zero or a dropped row."""
    seen = set()
    for row in rows:
        key = (row["contrast_id"], row["window_id"], row["endpoint"])
        if key in seen:
            raise ValueError("repeated longitudinal interval")
        seen.add(key)
        if row["assigned_worlds"] != row["defined_pairs"] + row["undefined_pairs"]:
            raise ValueError("paired-world risk set differs")
        values = [row[k] for k in ("mean_effect", "lower", "upper")]
        if any(v is None for v in values):
            if not all(v is None for v in values) or row["precision_met"]:
                raise ValueError("partial/precision-certified undefined estimate")
        elif not all(finite(v) for v in values) or not values[1] <= values[0] <= values[2]:
            raise ValueError("invalid interval coordinates")



def _save(fig, name, destination, outputs, plt):
    for extension in ("pdf", "svg"):
        relative = Path("figures/results") / f"{name}.{extension}"
        metadata = {"Creator": VERSION}
        metadata.update({"CreationDate": None, "ModDate": None} if extension == "pdf" else {"Date": None})
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(target, metadata=metadata)
        outputs.add(relative.as_posix())
    plt.close(fig)


def forest_figures(checked, destination, outputs, plt):
    from matplotlib.ticker import MaxNLocator
    rows = [r for r in checked.paired_intervals if r["primary"]]
    validate_estimates(rows)
    fragments = []
    contrasts = [c for c in declared_contrasts(checked.spec) if c["primary"]]
    for endpoint, _target, _unit in PRIMARY_ENDPOINTS:
        estimates = {r["contrast_id"]: r for r in rows if r["endpoint"] == endpoint}
        if set(estimates) != {c["contrast_id"] for c in contrasts}:
            raise ValueError("primary forest omits prescribed contrasts")
        fig, ax = plt.subplots(figsize=(6.6, max(3.1, .55 * len(contrasts) + 1.1)))
        for index, contrast in enumerate(contrasts):
            row = estimates[contrast["contrast_id"]]
            if row["mean_effect"] is None:
                ax.text(.99, index, "undefined; all worlds retained", transform=ax.get_yaxis_transform(), ha="right", va="center", fontsize=10.5)
            else:
                ax.errorbar(row["mean_effect"], index,
                            xerr=[[row["mean_effect"] - row["lower"]], [row["upper"] - row["mean_effect"]]],
                            fmt="o", color=MEASURED, markersize=7, capsize=3, linewidth=1.4)
        ax.set_yticks(range(len(contrasts)), [c["contrast_id"].replace("--", "\nvs ").replace("-", " ") for c in contrasts])
        ax.set_ylim(len(contrasts) - .5, -.5)
        ax.axvline(0, color=REFERENCE, linewidth=.8, zorder=0)
        ax.set_xlabel(LABELS[endpoint][1] + "\n(treatment minus reference)")
        ax.xaxis.set_major_locator(MaxNLocator(nbins=4, min_n_ticks=3))
        ax.set_title(LABELS[endpoint][0], loc="left")
        grid(ax, "x")
        ax.spines["left"].set_visible(False)
        fig.subplots_adjust(left=.46, right=.98, bottom=.20, top=.90)
        name = "longitudinal-" + endpoint.replace("_", "-")
        _save(fig, name, destination, outputs, plt)
        kind = "conservative paired-discordance exact-binomial intervals" if endpoint == "closed_by_common_end" else "approximate paired Student-t intervals"
        caption = (f"{LABELS[endpoint][0]} at the common strategy end date. All {checked.protocol['confirmation_worlds']} assigned paired worlds are retained. "
                   f"Intervals are {kind}, with Bonferroni allocation across the declared primary family. "
                   "These quantify conditional Monte Carlo uncertainty, not model validity or empirical organization effects. Undefined estimates remain labelled.")
        fragments.append("\\begin{figure}[htbp]\n\\centering\n\\includegraphics[width=\\textwidth]{figures/results/" + name + ".pdf}\n\\caption{" + tex(caption) + "}\n\\label{fig:" + name + "}\n\\end{figure}\n")
    return "\n".join(fragments)


def trajectory(checked, destination, outputs, plt):
    """A declared primary contrast, all confirmation worlds, unsmoothed raw days.

    Bands are empirical 2.5/97.5 percentiles of the assigned paired worlds, not
    confidence bands. Missing rates invalidate that day's whole-world mean.
    """
    import numpy as np
    contrast = next(c for c in declared_contrasts(checked.spec) if c["primary"])
    world_ids = checked.protocol["confirmation_world_ids"]
    case_map = {(c["tags"]["arm_id"], c["config"].world): c for c in checked.select(stage="confirmation", role="strategy")}
    data = common_trajectory_data(checked, contrast, case_map)
    all_rows = data['rows']
    adoption_days = data['adoption_days']
    never_adopted = data['never_adopted_worlds']
    initial_age = data['initial_organization_age_years']
    means = np.asarray([[row['mean_paired_difference'] for row in all_rows if row['metric'] == metric] for metric in _derived.METRICS]).T
    low = np.asarray([[row['world_distribution_p025'] for row in all_rows if row['metric'] == metric] for metric in _derived.METRICS]).T
    high = np.asarray([[row['world_distribution_p975'] for row in all_rows if row['metric'] == metric] for metric in _derived.METRICS]).T
    origin = date.fromisoformat(checked.spec.calendar_start)
    from datetime import timedelta
    dates = [origin + timedelta(days=d) for d in range(checked.spec.common_end_day)]
    # Common-calendar estimates retain every assigned world. Relative alignment
    # below uses real anchors and explicit coverage, never relabelled ticks.
    titles = (("Active members", "Members"), ("Cumulative cash difference", "Synthetic resource units"), ("Unfinished review queue", "Records"))
    fig, axes = plt.subplots(3, 1, figsize=(6.6, 6.0), sharex=True)
    for column, (title, unit) in enumerate(titles):
        ax = axes[column]
        ax.fill_between(range(len(dates)), low[:, column], high[:, column], color=MEASURED, alpha=.14, linewidth=0)
        ax.plot(range(len(dates)), means[:, column], color=MEASURED, linewidth=1.1)
        ax.axhline(0, color=REFERENCE, linewidth=.7)
        if adoption_days:
            ax.axvspan(min(adoption_days), max(adoption_days), color="#EFEDEF", zorder=-1)
            ax.axvline(statistics.median(adoption_days), color="#64577B", linestyle="--", linewidth=1)
        ax.set_title(title, loc="left", fontsize=12)
        ax.set_ylabel(unit)
        grid(ax)
    axes[-1].set_xlabel("Calendar days since " + checked.spec.calendar_start)
    from dams_sim.longitudinal import anniversary
    years = list(range(0, checked.spec.latest_fixed_adoption_year + checked.spec.post_adoption_years + 1, 2))
    positions = [(anniversary(origin, year) - origin).days for year in years]
    axes[0].secondary_xaxis("top").set_xticks(positions, [f"{initial_age + year:g}" for year in years])
    axes[0].text(.5, 1.32, "Organization age (years)", transform=axes[0].transAxes, ha="center", fontsize=11.5)
    fig.subplots_adjust(left=.17, right=.98, bottom=.10, top=.87, hspace=.48)
    name = "longitudinal-paired-trajectory"
    _save(fig, name, destination, outputs, plt)
    relative = "generated/analysis/paired_trajectory.csv"
    atomic_csv(destination / relative, all_rows)
    outputs.add(relative)
    caption = (f"Unsmoothed daily paired differences for the predeclared first primary contrast, {contrast['contrast_id']}. "
               f"Lines are means of all {len(world_ids)} assigned worlds; shaded bands are empirical world-distribution percentiles, not confidence bands. " +
               ("The vertical marker is the median actual last-guild adoption date; its shaded span shows the observed range. " if adoption_days else "No world fully adopted, so no adoption marker is drawn. ") +
               f"{never_adopted} worlds never fully adopted and remain in every paired trajectory. Organization age and calendar time are distinct. "
               "Cash includes paid transition and operating costs in synthetic resource units; its difference is not a calibrated financial return.")
    common_fragment = "\\begin{figure}[htbp]\n\\centering\n\\includegraphics[width=\\textwidth]{figures/results/" + name + ".pdf}\n\\caption{" + tex(caption) + "}\n\\label{fig:" + name + "}\n\\end{figure}\n"
    return common_fragment + relative_trajectory(checked, contrast, case_map, data['paired'], destination, outputs, plt)


def relative_trajectory(checked, contrast, case_map, paired, destination, outputs, plt):
    """Align the complete paired days; retain nonadopters and unavailable days.

    No interpolation, conditioning on survival, or adopter-only mean. The
    separate coverage panel explains why an all-assigned estimate is undefined.
    Original per-world CSVs plus anchor rows map each relative day to its exact
    calendar date; aggregate curves have no invented shared calendar date.
    """
    import numpy as np
    data = relative_trajectory_data(checked, contrast, case_map, paired.tolist() if isinstance(paired, np.ndarray) else paired)
    worlds = checked.protocol['confirmation_world_ids']
    rows, coverage = data['rows'], data['coverage']
    days = np.asarray(data['days'])
    defined_anchors = data['defined_anchors']
    available = np.asarray([row['defined_worlds'] for row in rows if row['metric'] == _derived.METRICS[0]])
    complete = available == len(worlds)
    means = np.asarray([[row['mean_paired_difference'] if row['mean_paired_difference'] is not None else np.nan for row in rows if row['metric'] == metric] for metric in _derived.METRICS]).T
    low = np.asarray([[row['world_distribution_p025'] if row['world_distribution_p025'] is not None else np.nan for row in rows if row['metric'] == metric] for metric in _derived.METRICS]).T
    high = np.asarray([[row['world_distribution_p975'] if row['world_distribution_p975'] is not None else np.nan for row in rows if row['metric'] == metric] for metric in _derived.METRICS]).T
    for filename, values in (('relative_adoption_world_coverage', coverage), ('relative_adoption_trajectory', rows)):
        relative = 'generated/analysis/' + filename + '.csv'
        atomic_csv(destination / relative, values)
        outputs.add(relative)
    fig, axes = plt.subplots(4, 1, figsize=(6.6, 7.3), sharex=True,
                             gridspec_kw={'height_ratios': [1, 1, 1, .8]})
    labels = (('Active members', 'Members'), ('Cumulative cash difference', 'Synthetic resource units'),
              ('Unfinished review queue', 'Records'))
    for column, (title, unit) in enumerate(labels):
        ax = axes[column]
        if complete.any():
            ax.fill_between(days, low[:, column], high[:, column], color=MEASURED, alpha=.14, linewidth=0)
            ax.plot(days, means[:, column], color=MEASURED, linewidth=1.1)
        else:
            ax.text(.5, .5, 'Undefined: incomplete assigned-world coverage', transform=ax.transAxes,
                    ha='center', va='center', fontsize=10.5,
                    bbox={'facecolor': 'white', 'edgecolor': 'none', 'pad': 2}, zorder=5)
            ax.set_yticks([])
            ax.spines['left'].set_visible(False)
        if complete.any():
            ax.axhline(0, color=REFERENCE, linewidth=.7)
        if defined_anchors:
            ax.axvline(0, color='#64577B', linestyle='--', linewidth=1)
        ax.set_title(title, loc='left', fontsize=12); ax.set_ylabel(unit); grid(ax)
    ax = axes[-1]
    ax.plot(days, available, color=MEASURED, linewidth=1.2,
            marker='o' if len(days) == 1 else None, markersize=6)
    ax.axhline(len(worlds), color=REFERENCE, linestyle='--', linewidth=.9)
    ax.set_ylim(-.5, len(worlds) + .5)
    ax.set_ylabel('Paired worlds'); ax.set_title('Assigned-world coverage', loc='left', fontsize=12)
    ax.set_xlabel('Calendar days relative to actual last-guild adoption'); grid(ax)
    if not defined_anchors:
        ax.set_xticks([0], ['No anchor'])
    fig.subplots_adjust(left=.19, right=.98, bottom=.10, top=.95, hspace=.65)
    name = 'longitudinal-relative-adoption-trajectory'
    _save(fig, name, destination, outputs, plt)
    caption = (f"Actual-adoption alignment for {contrast['contrast_id']}, with {len(worlds)} assigned paired worlds. "
               "Each treatment and matched control uses the treatment's actual last-guild adoption date. "
               "An estimate is shown only when every assigned world has both an anchor and the observed calendar day. "
               f"{len(worlds) - len(defined_anchors)} never-fully-adopted worlds remain in the coverage denominator; no adopter-only effect replaces undefined means. "
               "Bands are empirical world-distribution percentiles, not confidence bands. Exact Gregorian anchors and organization ages are exported with each world. "
               "Closed and suspended worlds remain in their complete recorded trajectories.")
    return "\\begin{figure}[htbp]\n\\centering\n\\includegraphics[width=\\textwidth]{figures/results/" + name + ".pdf}\n\\caption{" + tex(caption) + "}\n\\label{fig:" + name + "}\n\\end{figure}\n"


def commit_candidate(candidate, destination):
    """Keep the previous publication reachable if the final rename fails."""
    previous = None
    if destination.exists():
        previous = Path(tempfile.mkdtemp(prefix='.longitudinal-publication-previous-', dir=destination.parent))
        previous.rmdir()  # Only the new empty directory owned by this call.
        os.replace(destination, previous)
    try:
        os.replace(candidate, destination)
    except BaseException as error:
        rollback_error = None
        if previous is not None:
            try:
                os.replace(previous, destination)
            except OSError as failure:
                rollback_error = type(failure).__name__
        atomic_json(candidate.parent / (candidate.name + '-failure.json'),
                    {'status': 'publication-commit-failed', 'error': type(error).__name__,
                     'rollback_error': rollback_error, 'candidate': str(candidate),
                     'previous': str(previous) if previous else None,
                     'previous_restored': previous is not None and rollback_error is None})
        if rollback_error:
            raise RuntimeError('publication rollback failed; previous tree remains at ' + str(previous)) from error
        raise


def analyze(runs, thesis_dir, *, pipeline_in_progress=False):
    require_loaded_sources()
    raw_destination = Path(thesis_dir)
    publication_tree(raw_destination)
    runs, destination = Path(runs).resolve(), raw_destination.resolve()
    if destination == runs or runs.is_relative_to(destination):
        raise ValueError("publication destination cannot contain the scientific run")
    if destination.is_relative_to(runs) and destination != runs / "publication":
        raise ValueError("in-run publication must use its dedicated directory")
    checked = CheckedLongitudinalStudy(runs, publication_required=False, producer_in_progress=pipeline_in_progress)
    validate_estimates(checked.paired_intervals)
    source_before = source_hash()
    if source_before != checked.identity["source_sha256"]:
        raise ValueError("scientific source changed after raw validation")
    require_loaded_sources()
    code_before = dict(IMPORTED_CODE_SHA256)
    destination.parent.mkdir(parents=True, exist_ok=True)
    old_generation = destination / GENERATION
    if any(destination.rglob("*")) and not old_generation.is_file():
        raise ValueError("refuse to overwrite an unrecorded publication tree")
    if old_generation.is_file():
        old = json.loads(old_generation.read_text())
        if old.get("version") != VERSION or old.get("source_sha256") != source_before or old.get("spec_sha256") != checked.spec.sha256:
            raise ValueError("existing publication has another source or specification")
        actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file() and p != old_generation}
        if actual != set(old["outputs"]):
            raise ValueError("existing publication has an unrecorded output")
        for relative, sha in old["outputs"].items():
            path = destination / relative
            if path.is_symlink() or not path.resolve().is_relative_to(destination) or file_digest(path) != sha:
                raise ValueError("existing publication bytes differ from its inventory")
        if old["inputs"] != {k: v for k, v in checked.inputs.items() if k != "pipeline_manifest.json"}:
            raise ValueError("existing publication came from different raw inputs")
    previous_signature = publication_signature(destination)
    published_destination = destination
    # Build outside the final tree. A failed writer leaves its candidate for
    # diagnosis without making the next invocation overwrite unrecorded output.
    destination = Path(tempfile.mkdtemp(prefix=".longitudinal-publication-candidate-", dir=destination.parent))
    old_generation = destination / GENERATION
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    apply_style(plt)
    outputs = set()
    def write_json(relative, value):
        atomic_json(destination / relative, value)
        outputs.add(relative)
    def write_csv(relative, value):
        atomic_csv(destination / relative, value)
        outputs.add(relative)
    def write_tex(relative, value):
        atomic_bytes(destination / relative, value.encode())
        outputs.add(relative)
    for name, values in (("observations", checked.observations), ("longitudinal_endpoints", checked.endpoints), ("paired_intervals", checked.paired_intervals)):
        write_csv(f"generated/analysis/{name}.csv", values)
    derived = []
    for case in checked.select(stage="confirmation", role="strategy"):
        envelope, _database = checked.read_snapshot(case["case_id"])
        derived.extend(derive_case(case, envelope, checked.iter_journal(case["case_id"])))
    write_csv("generated/analysis/cohort_and_memory_descriptives.csv", derived)
    primary = [r for r in checked.paired_intervals if r["primary"]]
    claims = {"schema_version": 3, "source_sha256": checked.identity["source_sha256"], "spec": checked.spec.to_dict(),
              "confirmation_worlds": checked.protocol["confirmation_worlds"], "independent_unit": "paired world",
              "mc_precision_met": all(r["precision_met"] for r in primary), "primary": primary,
              "post_window_status_counts": dict(Counter(r["status"] for r in checked.endpoints if r["stage"] == "confirmation")),
              "limitations": LIMITATIONS}
    write_json("generated/analysis/numerical_claims.json", claims)
    write_json("generated/analysis/derived_definitions.json", DERIVED_DEFINITIONS)
    write_tex("generated/longitudinal_effects.tex", forest_figures(checked, destination, outputs, plt))
    write_tex("generated/longitudinal_trajectories.tex", trajectory(checked, destination, outputs, plt))
    write_tex("generated/longitudinal_results.tex", "The completed longitudinal specification contains " + str(checked.protocol["confirmation_worlds"]) +
              " independent confirmation worlds, paired within each declared organizational context. " +
              "Its primary intervals describe conditional Monte Carlo uncertainty. " +
              ("Every primary precision target was met. " if claims["mc_precision_met"] else "At least one primary precision target was not met; the prescribed worlds and failed targets remain visible. ") +
              "Post-adoption windows use actual last-guild adoption dates and their matched control dates. Zero-exposure and unavailable windows remain undefined. " +
              "Cohort summaries retain members who exit or never receive positive formal allocations. These quantities do not measure observed real influence or innovation.\n")
    # The final pipeline manifest changes after publication. It is identity
    # checked above but excluded from input digests to avoid a circular hash.
    inputs = {k: v for k, v in checked.inputs.items() if k != "pipeline_manifest.json"}
    for relative, expected in inputs.items():
        if file_digest(runs / relative) != expected:
            raise ValueError("raw input changed during publication")
    if source_hash() != source_before or driver_hash() != checked.identity["pipeline_driver_sha256"] or any(file_digest(ROOT / name) != sha for name, sha in code_before.items()):
        raise ValueError("scientific/analyzer source changed during publication")
    require_loaded_sources()
    publication_tree(destination)
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file() and p != old_generation}
    if actual != outputs:
        raise ValueError("publication output inventory differs")
    atomic_json(old_generation, {"status": "complete", "schema_version": 3, "version": VERSION,
                                **checked.identity, "protocol_sha256": inputs["protocol.json"],
                                "analysis_sources_sha256": code_before, "inputs": inputs,
                                "outputs": {name: file_digest(destination / name) for name in sorted(outputs)},
                                "runtime": {"python": sys.version.split()[0], "matplotlib": matplotlib.__version__},
                                "figure_style": style_metadata(), "model_executions": 0})
    require_loaded_sources()
    publication_tree(published_destination)
    if publication_signature(published_destination) != previous_signature:
        raise ValueError('existing publication changed while candidate was generated')
    commit_candidate(destination, published_destination)
    return claims
