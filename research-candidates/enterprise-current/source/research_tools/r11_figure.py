"""Paired-effect figure for one R11 study, drawn from its inference.json.

One panel per primary outcome; one row per comparison arm; the point is the
mean paired difference from the reference arm over independent worlds and the
bar its Student-t 95 % interval. Estimated panels include zero; panels without
defined intervals have no numerical axis. The figure is a
quick reading aid for a reproduced study; the thesis figures are drawn by the
thesis repository from the same files.

Usage: python research_tools/r11_figure.py STUDY_DIR   (writes STUDY_DIR/effects.pdf)
"""
from __future__ import annotations

import json
import math
import os
import sys
import textwrap
from pathlib import Path

os.environ.setdefault('SOURCE_DATE_EPOCH', '0')  # identical bytes for identical input
import matplotlib  # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research_tools.r11_world_count import PRIMARY  # noqa: E402

PRIMARY_LABELS = {
    'net_operating_result_per_member_year': 'Net operating result',
    'decision_regret_per_available_member_hour': 'Decision regret',
    'governance_cost_per_member_year': 'Legacy governance index',
    'governance_member_hours_per_member_year': 'Governance member-hours',
    'witness_resource_units_per_member_year': 'Witness resource units',
}


def _admitted(inference):
    if (inference.get('schema') != 'r11-roster-and-raw-inference-v2'
            or inference.get('admission_errors') != []
            or not isinstance(inference.get('comparisons'), dict) or not inference['comparisons']):
        raise ValueError('figure requires a nonempty raw-inference-v2 report without admission errors')
    for arm, comparison in inference['comparisons'].items():
        if not isinstance(comparison, dict) or not all(k in comparison for k in PRIMARY):
            raise ValueError('comparison is missing its declared primary outcomes')
        for key in PRIMARY:
            row = comparison[key]
            if (not isinstance(row, dict) or type(row.get('worlds')) is not int or row['worlds'] < 0
                    or not isinstance(row.get('unit'), str) or not isinstance(row.get('status'), str)):
                raise ValueError('primary outcome has no valid sample/unit/status record')


def rows(inference):
    """[(arm, outcome, mean, low, high, unit, worlds)] for the primary outcomes."""
    _admitted(inference)
    out = []
    for arm, comp in sorted(inference['comparisons'].items()):
        for k in PRIMARY:
            r = comp.get(k, {})
            ci = r.get('ci95')
            if ci is None:
                continue  # Zero variance and insufficient pairs have no t interval.
            mean = r.get('mean_difference')
            if (not isinstance(ci, list) or len(ci) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in [mean, *ci])
                    or not ci[0] <= mean <= ci[1] or r['worlds'] < 2):
                raise ValueError('defined interval/mean/sample is invalid')
            out.append((arm, k, mean, ci[0], ci[1], r['unit'], r['worlds']))
    return out


def draw(study_dir):
    study_dir = Path(study_dir)
    inf = json.loads((study_dir/'inference.json').read_text())
    data = rows(inf)
    arms = sorted(inf['comparisons'])
    height = 2.6+.6*len(arms)
    fig, axs = plt.subplots(1, len(PRIMARY), figsize=(10, height), sharey=True)
    for ax, k in zip(axs, PRIMARY):
        sel = [r for r in data if r[1] == k]
        for arm, _, m, lo, hi, unit, n in sel:
            y = arms.index(arm)
            ax.plot([lo, hi], [y, y], color='#3e3e3e', lw=1.2)
            ax.plot(m, y, 'o', color='#3e3e3e', ms=4)
            ax.annotate(f'n={n}', (m, y), xytext=(0, 6), textcoords='offset points', ha='center', fontsize=6)
        estimated = {r[0] for r in sel}
        for arm in arms:
            if arm not in estimated:
                row = inf['comparisons'][arm][k]
                label = 'CI undefined' if row.get('mean_difference') is not None else 'Not estimated'
                ax.text(.02, arms.index(arm), f"{label} (n={row['worlds']})",
                        transform=ax.get_yaxis_transform(), va='center', fontsize=7, color='#3e3e3e')
        if sel:
            ax.axvline(0, color='#888888', lw=.7)
            lo, hi = ax.get_xlim()
            ax.set_xlim(min(lo, 0), max(hi, 0))
            ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(4))
            ax.ticklabel_format(axis='x', style='sci', scilimits=(-3, 4))
        else:
            ax.set_xticks([])
            ax.spines[['bottom', 'top', 'right']].set_visible(False)
        unit = textwrap.fill(inf['comparisons'][arms[0]][k]['unit'], width=29, break_long_words=False)
        ax.set_title(f"{PRIMARY_LABELS[k]}\n({unit})", fontsize=8, pad=10)
        ax.tick_params(labelsize=8)
    axs[0].set_yticks(range(len(arms)), arms, fontsize=8)
    axs[0].set_ylim(len(arms)-.5, -.5)
    fig.suptitle(f"Difference from {inf['reference']}\nConditional paired worlds; defined 95% intervals", fontsize=9)
    if inf.get('measurement_warnings'):
        fig.text(.5, .035, 'Legacy governance index mixes units; separate components remain in the report.',
                 ha='center', fontsize=7, color='#3e3e3e')
    fig.subplots_adjust(left=.15, right=.98, bottom=.65/height, top=1-1.3/height, wspace=.35)
    path = study_dir/'effects.pdf'
    fig.savefig(path, metadata={'CreationDate': None, 'ModDate': None})
    plt.close(fig)
    return path


if __name__ == '__main__':
    print(draw(sys.argv[1]))
