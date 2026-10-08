"""Shared publication styling and layouts; no model or outcome transformations.

Policy identity uses a name, marker and dash pattern as well as muted colour.
The plotting functions accept already computed estimates and retain their full
coordinates. Facets share scales; no jitter, smoothing or curve displacement.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

IMPORTED_MODULE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

STYLE_VERSION = "DAMS-publication-2026-10-08-v3"
POLICY_ORDER = ("equal", "linear", "sublinear", "hierarchy", "hierarchy_tenure")
POLICY_NAMES = {
    "equal": "Equal", "linear": "Linear", "sublinear": "DAMS",
    "hierarchy": "Performance tiers", "hierarchy_tenure": "Tenure tiers",
}
FACTOR_LABELS = {
    "alpha": r"Exponent $\alpha$",
    "review_capacity_per_member_day": "Review capacity",
    "review_error_sd": "Review noise",
    "autonomy_response": "Share-effort response",
    "cooperation_strength": "Cooperation",
    "update_interval_days": "Allocation interval",
}
POLICY_STYLE = {
    "equal": ("#30343B", (0, (1, 2)), "o"),
    "linear": ("#496D86", "-", "s"),
    "sublinear": ("#64577B", (0, (5, 2)), "^"),
    "hierarchy": ("#826D3E", (0, (5, 2, 1, 2)), "D"),
    "hierarchy_tenure": ("#47786F", (0, (2, 2)), "v"),
}
INK = "#252A30"
GRID = "#E7E9EC"
REFERENCE = "#90979E"
MEASURED = "#496D86"
PLANNED = "#826D3E"


def apply_style(plt) -> None:
    """Visible hierarchy and substantial marks at manuscript reading size."""
    plt.rcParams.update({
        "font.family": "serif", "font.serif": ["cmr10"],
        "mathtext.fontset": "cm", "axes.formatter.use_mathtext": True,
        "axes.unicode_minus": False, "font.size": 12,
        "axes.labelsize": 12, "axes.titlesize": 14,
        "axes.labelpad": 8, "axes.titlepad": 10,
        "xtick.labelsize": 11.5, "ytick.labelsize": 11.5,
        "xtick.major.pad": 5, "ytick.major.pad": 5,
        "legend.fontsize": 11.5, "legend.labelspacing": .55,
        "legend.handlelength": 2.1, "legend.columnspacing": 1.3,
        "text.color": INK, "axes.labelcolor": INK,
        "axes.edgecolor": "#9CA3AA", "xtick.color": INK, "ytick.color": INK,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.linewidth": .6, "lines.linewidth": 1.5,
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
        "svg.hashsalt": STYLE_VERSION, "figure.facecolor": "white",
        "axes.facecolor": "white", "savefig.facecolor": "white",
    })


def style_metadata() -> dict:
    return {"version": STYLE_VERSION,
            "module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "policy_order": list(POLICY_ORDER),
            "policy_styles": {p: {"name": POLICY_NAMES[p], "color": c,
                                  "line": line, "marker": marker}
                              for p, (c, line, marker) in POLICY_STYLE.items()},
            "source_font_points": 12,
            "source_title_points": 14,
            "source_tick_and_legend_points": 11.5,
            "layout_contract": "common facet scales; unchanged plotted coordinates; no smoothing, jitter or displacement"}


def grid(ax, axis="y") -> None:
    ax.grid(axis=axis, color=GRID, linewidth=.4)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, width=.6, pad=5)
    ax.minorticks_off()


def dynamics_figure(plt, series: dict, *, shock_window=(25, 30), policies=POLICY_ORDER):
    """Five directly labelled rows, with separate work and queue columns.

    series[policy][metric] contains days, mean, low and high, all unchanged.
    Vertical axes are shared within each metric, including uncertainty bands.
    """
    fig, axes = plt.subplots(len(policies), 2, figsize=(6.4, 1.05*len(policies)+.9), sharex=True, squeeze=False,
                             gridspec_kw={"hspace": .38, "wspace": .32})
    metrics = ("output_work_units", "review_backlog_records")
    for row, policy in enumerate(policies):
        color, line, marker = POLICY_STYLE[policy]
        for col, metric in enumerate(metrics):
            ax = axes[row, col]
            s = series[policy][metric]
            ax.fill_between(s["days"], s["low"], s["high"], color=color,
                            alpha=.15, linewidth=0)
            artist, = ax.plot(s["days"], s["mean"], color=color, linestyle=line,
                    marker=marker, markevery=max(1,len(s["days"])//6), markersize=5.8,
                    markeredgewidth=.8, linewidth=1.5)
            artist.set_gid(f"{policy}:{metric}")
            if shock_window is not None and shock_window[1]>shock_window[0]:
                ax.axvspan(*shock_window, color="#F0F1F2", zorder=-2)
            grid(ax)
            ax.set_xlim(min(s["days"]), max(s["days"])+1)
            ax.xaxis.set_major_locator(plt.MaxNLocator(3,integer=True))
            if row == 0:
                ax.set_title(("(a) Work/member-day", "(b) Queue/member")[col],
                             loc="left", pad=8)
            if col == 0:
                ax.text(.98, .92, POLICY_NAMES[policy], transform=ax.transAxes,
                        ha="right", va="top", fontsize=12, color=INK)
            if row < len(policies)-1:
                ax.spines['bottom'].set_visible(False)
                ax.tick_params(axis='x', bottom=False)
            if row == len(policies)-1:
                ax.set_xlabel("Day")
    for col, metric in enumerate(metrics):
        lower = min(min(series[p][metric]["low"]) for p in policies)
        upper = max(max(series[p][metric]["high"]) for p in policies)
        pad = (upper-lower)*.08 or .1
        for ax in axes[:, col]:
            ax.set_ylim(max(0, lower-pad), upper+pad)
            ax.yaxis.set_major_locator(plt.MaxNLocator(3))
    fig.subplots_adjust(left=.09, right=.985, bottom=.085, top=.93)
    return fig


def attack_figure(plt, effects: list[dict], *, policies=POLICY_ORDER, attacks=("forge", "freeride"), use_saved_endpoints=False):
    """Budget columns and attack rows; policy-labelled intervals on one scale."""
    budgets=sorted({e["budget_hours_day"] for e in effects if e["attack"] in attacks})
    if not budgets:raise ValueError("no executed attack budgets to plot")
    fig, axes = plt.subplots(len(attacks), len(budgets), figsize=(6.4, 1.9*len(attacks)+.9), sharex=True, sharey=True, squeeze=False)
    for row, attack in enumerate(attacks):
        for col, budget in enumerate(budgets):
            ax = axes[row, col]
            for index, policy in enumerate(policies):
                estimate = next(e for e in effects if e["policy"] == policy and
                                e["attack"] == attack and e["budget_hours_day"] == budget)
                color, _, marker = POLICY_STYLE[policy]
                error = [[estimate['mean']-estimate['low']],[estimate['high']-estimate['mean']]] if use_saved_endpoints else 1.96*estimate['se']
                container = ax.errorbar(estimate["mean"], index, xerr=error,
                            fmt=marker, color=color, markersize=7.5,
                            capsize=3.5, linewidth=1.5, markeredgewidth=.8)
                container.lines[0].set_gid(f"{policy}:{attack}:{budget}")
            ax.axvline(0, color=REFERENCE, linewidth=.85, zorder=-2)
            ax.set_yticks(range(len(policies)), [POLICY_NAMES[p] for p in policies])
            if row == 0:
                ax.set_title(f"Budget {budget:g}", loc="left", pad=10)
            if col == 0:
                ax.text(-.72, .5, {'forge':'Forged claims','freeride':'Free-riding'}.get(attack,attack.title()),
                        transform=ax.transAxes, rotation=90, ha="center", va="center", fontsize=14)
            grid(ax, "x")
            ax.spines['left'].set_visible(False)
            ax.set_ylim(len(policies)-.5, -.5)
            if row == len(attacks)-1:
                ax.set_xlabel("Work change\n(units/member-day)")
    fig.subplots_adjust(left=.285, right=.98, bottom=.16, top=.91, hspace=.33, wspace=.20)
    return fig
