"""Paired-effect figure for one R11 study, drawn from its inference.json.

One panel per primary outcome; one row per comparison arm; the point is the
mean paired difference from the reference arm over independent worlds and the
bar its Student-t 95 % interval. Every axis includes zero. The figure is a
quick reading aid for a reproduced study; the thesis figures are drawn by the
thesis repository from the same files.

Usage: python research_tools/r11_figure.py STUDY_DIR   (writes STUDY_DIR/effects.pdf)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault('SOURCE_DATE_EPOCH', '0')  # identical bytes for identical input
import matplotlib  # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research_tools.r11_world_count import PRIMARY  # noqa: E402


def rows(inference):
    """[(arm, outcome, mean, low, high, unit, worlds)] for the primary outcomes."""
    out = []
    for arm, comp in sorted(inference['comparisons'].items()):
        for k in PRIMARY:
            r = comp.get(k, {})
            if 'ci95' in r:
                out.append((arm, k, r['mean_difference'], r['ci95'][0], r['ci95'][1], r['unit'], r['worlds']))
    return out


def draw(study_dir):
    study_dir = Path(study_dir)
    inf = json.loads((study_dir/'inference.json').read_text())
    data = rows(inf)
    arms = sorted({r[0] for r in data})
    fig, axs = plt.subplots(1, len(PRIMARY), figsize=(9, 1.2+.45*len(arms)), sharey=True)
    for ax, k in zip(axs, PRIMARY):
        sel = [r for r in data if r[1] == k]
        for arm, _, m, lo, hi, unit, n in sel:
            y = arms.index(arm)
            ax.plot([lo, hi], [y, y], color='#3e3e3e', lw=1.2)
            ax.plot(m, y, 'o', color='#3e3e3e', ms=4)
        ax.axvline(0, color='#888888', lw=.7)
        lo, hi = ax.get_xlim()
        ax.set_xlim(min(lo, 0), max(hi, 0))
        ax.set_title(f"{k.replace('_', ' ')}\n({sel[0][5] if sel else ''})", fontsize=8)
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(4))
        ax.ticklabel_format(axis='x', style='sci', scilimits=(-3, 4))
        ax.tick_params(labelsize=8)
    axs[0].set_yticks(range(len(arms)), [f'{a} ({next(r[6] for r in data if r[0] == a)} worlds)' for a in arms], fontsize=8)
    axs[0].set_ylim(len(arms)-.5, -.5)
    fig.suptitle(f"Difference from {inf['reference']} (paired worlds, 95% interval)", fontsize=9)
    fig.tight_layout()
    path = study_dir/'effects.pdf'
    fig.savefig(path, metadata={'CreationDate': None, 'ModDate': None})
    plt.close(fig)
    return path


if __name__ == '__main__':
    print(draw(sys.argv[1]))
