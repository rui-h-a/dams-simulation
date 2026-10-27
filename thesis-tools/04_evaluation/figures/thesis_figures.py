"""Result figures of the thesis, redrawn from the retained versioned outputs.

One style for every data figure: drawn at final printed size (text width 159 mm,
so no scaling in LaTeX), Latin Modern text at 9 pt to match the body, white
background, one fixed colour, marker and line style per governance rule across
the whole thesis, direct labels where a legend would force eye travel, and axes
that include the reference value (zero difference) instead of cropping to the data.

Inputs: 05_thesis/generated/analysis/*.csv (source 3d67a62e6eb2 studies),
05_thesis/generated/revision_n{30,120}_confirmation_{effects,summary}.csv and
04_evaluation/r11/*/inference.json. Outputs: 05_thesis/figures/results/<name>.pdf.

Usage: python 04_evaluation/figures/thesis_figures.py
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import os
os.environ.setdefault('SOURCE_DATE_EPOCH', '0')  # deterministic PDF bytes across runs
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import matplotlib.patches  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = ROOT/'05_thesis'/'generated'/'analysis'
GEN = ROOT/'05_thesis'/'generated'
R11 = ROOT/'04_evaluation'/'r11'
OUT = ROOT/'05_thesis'/'figures'/'results'
TEXT_W = 159/25.4  # inches

LM = Path.home()/'Library/TinyTeX/texmf-dist/fonts/opentype/public/lm'
for f in ('lmroman9-regular.otf', 'lmroman9-italic.otf', 'lmroman9-bold.otf'):
    if (LM/f).exists():
        font_manager.fontManager.addfont(str(LM/f))

INK = '#222222'
GRID = '#e4e4e4'
REF = '#6b6b6b'
plt.rcParams.update({
    'font.family': 'Latin Modern Roman', 'font.size': 9, 'mathtext.fontset': 'cm',
    'axes.edgecolor': '#555555', 'axes.labelcolor': INK, 'axes.linewidth': .6, 'axes.titlesize': 9,
    'axes.titleweight': 'normal', 'axes.titlelocation': 'left', 'axes.titlepad': 6,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': False,
    'xtick.color': '#444444', 'ytick.color': '#444444', 'xtick.labelcolor': INK, 'ytick.labelcolor': INK,
    'xtick.major.width': .6, 'ytick.major.width': .6, 'xtick.major.size': 3, 'ytick.major.size': 3,
    'xtick.direction': 'out', 'ytick.direction': 'out', 'legend.frameon': False, 'legend.fontsize': 9,
    'lines.linewidth': 1.2, 'lines.markersize': 4.5, 'errorbar.capsize': 0,
    'pdf.fonttype': 3, 'savefig.dpi': 300, 'figure.dpi': 100,
})

# One identity per rule, used in every figure. Colour is never the only cue:
# each rule also has its own marker and line style. Hues are muted and none is
# reserved for DAMS, so colour does not suggest which rule is better.
POLICY = {
    'equal': dict(label='Equal', color='#2b2b2b', marker='o', ls=':'),
    'linear': dict(label='Linear', color='#3e6a8a', marker='s', ls='-'),
    'sublinear': dict(label='DAMS', color='#6a4f86', marker='^', ls='--'),
    'hierarchy': dict(label='Performance tiers', color='#8a6a3b', marker='D', ls='-.'),
    'hierarchy_tenure': dict(label='Tenure tiers', color='#3b7a6c', marker='v', ls=(0, (3, 1, 1, 1, 1, 1))),
}
ORDER = ['equal', 'linear', 'sublinear', 'hierarchy', 'hierarchy_tenure']
BACKEND = {'central': ('Central record', '-'), 'witness': ('Witnessed record', '--'), 'consensus': ('Consensus record', ':')}


def rows(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    # Tight bounding box keeps outside legends; widths stay at or below the text width.
    fig.savefig(OUT/f'{name}.pdf', bbox_inches='tight', pad_inches=.02, metadata={'CreationDate': None, 'ModDate': None})
    plt.close(fig)
    return OUT/f'{name}.pdf'


def zero_line(ax, axis='x'):
    (ax.axvline if axis == 'x' else ax.axhline)(0, color=REF, lw=.7, zorder=0)


def ygrid(ax):
    ax.yaxis.grid(True, color=GRID, lw=.6)
    ax.set_axisbelow(True)


def xgrid(ax):
    ax.xaxis.grid(True, color=GRID, lw=.6)
    ax.set_axisbelow(True)


def cue(ax, text, corner='upper left'):
    """Small in-panel note saying which direction favours the compared rule."""
    x, ha = (.01, 'left') if 'left' in corner else (.99, 'right')
    y, va = (.99, 'top') if 'upper' in corner else (.01, 'bottom')
    ax.text(x, y, text, transform=ax.transAxes, ha=ha, va=va, fontsize=7.5, color='#444444',
            bbox=dict(boxstyle='square,pad=.15', fc='white', ec='none', alpha=.85), zorder=5)


# --------------------------------------------------------------------------
# Allocation contrast (paired, with intervals) and outcome trade-off (means).

def allocation_effects():
    data = {r['policy']: r for r in rows(ANALYSIS/'allocation_effects.csv')}
    fig, ax = plt.subplots(figsize=(TEXT_W, 1.9))
    for y, p in enumerate(reversed(ORDER)):
        r, s = data[p], POLICY[p]
        m, lo, hi = float(r['mean']), float(r['low']), float(r['high'])
        if p == 'linear':
            ax.plot(0, y, marker=s['marker'], color=s['color'], mfc='white', ms=5, zorder=3)
            ax.annotate('reference (0 by definition)', (0, y), xytext=(6, 0), textcoords='offset points', va='center', color='#555555')
            continue
        ax.plot([lo, hi], [y, y], color=s['color'], lw=1.4, solid_capstyle='butt')
        ax.plot(m, y, marker=s['marker'], color=s['color'], ms=5.5, zorder=3)
    ax.set_yticks(range(len(ORDER)), [POLICY[p]['label'] for p in reversed(ORDER)])
    ax.tick_params(axis='y', length=0)
    ax.spines['left'].set_visible(False)
    zero_line(ax)
    xgrid(ax)
    ax.set_xlim(-0.012, 0.037)
    ax.set_xlabel('Work difference from linear credit (units per member-day)')
    ax.set_ylim(-.6, len(ORDER)-.4)
    fig.tight_layout(pad=.4)
    return save(fig, 'allocation-effects')


def outcome_tradeoffs():
    data = defaultdict(dict)
    for r in rows(ANALYSIS/'tradeoffs.csv'):
        data[r['backend']][r['policy']] = (float(r['work_per_member_day']), float(r['regret_per_guild_day']))
    # Two panels sharing the rule rows: work and regret on ordinary (not reversed) axes. Within a row the
    # three record processes are three marker shapes in grey, so the record is read from shape alone.
    procs = [('central', 'Central record', 'o', '#111111'), ('witness', 'Witnessed record', 's', '#666666'),
             ('consensus', 'Consensus record', 'D', '#aaaaaa')]
    fig, axs = plt.subplots(1, 2, figsize=(TEXT_W, 2.5), sharey=True)
    ys = {p: len(ORDER)-1-i for i, p in enumerate(ORDER)}
    for k, (ax, title) in enumerate(zip(axs, ['(a) Generated work, right is more\n(units per member-day)',
                                              '(b) Decision regret, left is less\n(units per guild-day)'])):
        for p in ORDER:
            vals = [data[b][p][k] for b, *_ in procs]
            ax.plot([min(vals), max(vals)], [ys[p]]*2, color='#cccccc', lw=2.2, zorder=1, solid_capstyle='butt')
            for (b, name, m, c), v in zip(procs, vals):
                ax.plot(v, ys[p], marker=m, color=c, ms=6, ls='none', mec='black', mew=.5, zorder=3, label=name if p == ORDER[0] else None)
        ax.set_title(title, loc='left')
        ax.xaxis.grid(True, color=GRID, lw=.6); ax.set_axisbelow(True)
    axs[0].set_yticks(list(ys.values()), [POLICY[p]['label'] for p in ys])
    axs[0].set_ylim(-.6, len(ORDER)-.4)
    fig.legend(*axs[0].get_legend_handles_labels(), loc='upper center', ncol=3, fontsize=8, bbox_to_anchor=(.55, 1.0))
    fig.tight_layout(pad=.4, w_pad=1.5, rect=(0, 0, 1, .88))
    return save(fig, 'outcome-tradeoffs')


def formal_authority_distribution():
    curves = defaultdict(list)
    for r in rows(ANALYSIS/'lorenz.csv'):
        curves[r['policy']].append((float(r['member_fraction']), float(r['mean'])))
    fig, ax = plt.subplots(figsize=(TEXT_W*.8, 2.8))
    ax.plot([0, 1], [0, 1], color=REF, lw=.7)
    tiers_same = curves['hierarchy'] == curves['hierarchy_tenure']
    for p in ['linear', 'sublinear', 'hierarchy'] + ([] if tiers_same else ['hierarchy_tenure']):
        s = POLICY[p]
        x, y = zip(*sorted(curves[p]))
        ax.plot(x, y, color=s['color'], ls=s['ls'], lw=1.3)
        ax.plot(x[::6], y[::6], color=s['color'], marker=s['marker'], ls='none', ms=4)
    handles = [plt.Line2D([], [], color=REF, lw=.7, label='Equal shares')]
    for p in ['linear', 'sublinear', 'hierarchy']:
        st = POLICY[p]
        name = 'Performance and\ntenure tiers\n(identical curves)' if (p == 'hierarchy' and tiers_same) else st['label']
        handles.append(plt.Line2D([], [], color=st['color'], ls=st['ls'], marker=st['marker'], ms=4, label=name))
    ax.legend(handles=handles, loc='center left', bbox_to_anchor=(1.04, .5), fontsize=8, handlelength=2.6)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_aspect('equal')
    ax.set_xlabel('Members within a guild, sorted by share')
    ax.set_ylabel('Cumulative formal share')
    ygrid(ax)
    fig.tight_layout(pad=.4)
    return save(fig, 'formal-authority-distribution')


def confirmation_cdf(unfinished):
    curves = defaultdict(list)
    for r in rows(ANALYSIS/'cdf.csv'):
        curves[r['backend']].append((int(r['days']), float(r['mean'])))
    last = max(d for c in curves.values() for d, v in c if v < 1)  # last day below completion
    fig, ax = plt.subplots(figsize=(TEXT_W*.66, 3.2))
    for b in ['central', 'witness', 'consensus']:
        name, ls = BACKEND[b]
        d, v = zip(*sorted(curves[b]))
        ax.step(d, v, where='post', color=INK, ls=ls, lw=1.8 if ls == ':' else 1.3,
                label=f'{name} ({unfinished[b]:,} unfinished at day 60)')
    ax.legend(loc='upper center', bbox_to_anchor=(.5, -.32), fontsize=8, handlelength=3, ncol=1)
    ax.set_xlim(0, last+4)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel(f'Days from creation to confirmation (axis ends at day {last+4}; horizon 60 days)')
    ax.set_ylabel('Share of confirmed records\n(unfinished records excluded)')
    ygrid(ax)
    fig.tight_layout(pad=.4)
    return save(fig, 'confirmation-cdf'), last


# --------------------------------------------------------------------------
# Behavioural boundaries, screening, threats and scale.

RULES = [('linear_response', 'Baseline effort rule'), ('satisficing', 'Satisficing rule'), ('reinforcement', 'Reinforcement rule')]


def response_boundaries():
    data = defaultdict(dict)
    for r in rows(ANALYSIS/'boundary_effects.csv'):
        data[r['behavior_rule']][float(r['response'])] = (float(r['mean']), float(r['low']), float(r['high']))
    fig, axs = plt.subplots(1, 3, figsize=(TEXT_W, 2.2), sharey=True)
    for ax, (rule, title) in zip(axs, RULES):
        for k, resp in enumerate([-.25, 0., .25]):
            m, lo, hi = data[rule][resp]
            if resp == 0:
                ax.plot(k, 0, marker='o', mfc='white', color=INK, ms=5, zorder=3)
                continue
            ax.plot([k, k], [lo, hi], color=INK, lw=1.3)
            ax.plot(k, m, marker='o', color=INK, ms=5, zorder=3)
        zero_line(ax, 'y')
        ax.set_xticks([0, 1, 2], ['$-0.25$', '0', '$+0.25$'])
        ax.set_xlim(-.6, 2.6)
        ax.set_title(title)
        ygrid(ax)
    axs[0].set_ylabel('DAMS minus linear work\n(units per member-day)')
    axs[1].set_xlabel('Share-response coefficient (open circle: zero work contrast)')
    fig.tight_layout(pad=.4, w_pad=.8)
    return save(fig, 'response-boundaries')


# Tested levels of each factor (research_tools/study.py of the source study; the run manifests of the
# reproduced screen use exactly these values).
FACTORS = {'cooperation_strength': 'Cooperation strength\n0 to 0.9', 'autonomy_response': 'Share response\n$-$0.5 to 1.0',
           'review_capacity_per_member_day': 'Review capacity\n0.4 to 1.6 records/member-day',
           'review_error_sd': 'Review noise (s.d.)\n0 to 0.6', 'update_interval_days': 'Allocation interval\n1 to 20 days',
           'alpha': r'Exponent $\alpha$' + '\n0.4 to 0.95'}


LABEL_SIDE = {'alpha': (6, -9), 'update_interval_days': (6, 5)}


def sensitivity_screen():
    data = {r['factor']: (float(r['mu_star']), float(r['sigma'])) for r in rows(ANALYSIS/'sensitivity_screen.csv')}
    factors = sorted(data, key=lambda k: data[k][0], reverse=True)
    fig, axs = plt.subplots(1, 2, figsize=(TEXT_W, 2.8), sharey=True)
    for column, (ax, title, symbol) in enumerate(zip(
            axs, ('Average size of a one-step change in work', 'How much that change varies across paths'),
            (r'$\mu^*$', r'$\sigma$'))):
        values = [data[k][column] for k in factors]
        ax.plot(values, range(len(factors)), marker='o', color=INK, ms=4.5, ls='none')
        for y, value in enumerate(values):
            ax.annotate(f'{value:.3f}', (value, y), xytext=(5, 0),
                        textcoords='offset points', va='center', fontsize=8)
        ax.set_xlim(0, max(values)*1.38)
        ax.set_title(title, fontsize=8)
        ax.set_xlabel(symbol + ' (work per member-day)', fontsize=8)
        ax.set_yticks(range(len(factors)))
        ax.tick_params(axis='y', length=0)
        ax.spines['left'].set_visible(False)
        xgrid(ax)
    axs[0].set_yticklabels([FACTORS[k] for k in factors])
    axs[0].invert_yaxis()
    fig.tight_layout(pad=.4, w_pad=1.0)
    return save(fig, 'sensitivity-screen')


def attack_cost_effects():
    data = defaultdict(dict)
    for r in rows(ANALYSIS/'attack_effects.csv'):
        data[(r['attack'], r['policy'])][int(r['budget_hours_day'])] = (float(r['mean']), float(r['low']), float(r['high']))
    attacks = [('forge', 'Forged claims'), ('freeride', 'Free-riding')]
    attacks = [(a, t) for a, t in attacks if any(k[0] == a for k in data)]
    fig, axs = plt.subplots(1, len(attacks), figsize=(TEXT_W, 2.3), sharey=True, sharex=True)
    for ax, (a, title) in zip(axs, attacks):
        for y, p in enumerate(reversed(ORDER)):
            s = POLICY[p]
            for budget, dy, fill in ((2, .13, 'white'), (8, -.13, s['color'])):
                m, lo, hi = data[(a, p)][budget]
                ax.plot([lo, hi], [y+dy, y+dy], color=s['color'], lw=1.2)
                ax.plot(m, y+dy, marker=s['marker'], color=s['color'], mfc=fill, ms=5, zorder=3)
        zero_line(ax)
        xgrid(ax)
        ax.set_title(title)
        ax.set_yticks(range(len(ORDER)), [POLICY[p]['label'] for p in reversed(ORDER)])
        ax.tick_params(axis='y', length=0)
        ax.spines['left'].set_visible(False)
    axs[0].set_xlabel('Work change from the same rule without attack (units per member-day)', loc='left')
    h1 = axs[-1].plot([], [], marker='o', color=INK, mfc='white', ls='none', label='2 attack hours per day')[0]
    h2 = axs[-1].plot([], [], marker='o', color=INK, ls='none', label='8 attack hours per day')[0]
    fig.legend(handles=[h1, h2], loc='upper right', ncol=2, bbox_to_anchor=(1, 1.02))
    fig.tight_layout(pad=.4, rect=(0, 0, 1, .89))
    return save(fig, 'attack-cost-effects')


def organizational_scale():
    data = defaultdict(dict)
    for r in rows(ANALYSIS/'scale_effects.csv'):
        data[r['policy']][int(r['population_n'])] = (float(r['mean']), float(r['low']), float(r['high']))
    fig, ax = plt.subplots(figsize=(TEXT_W*.68, 2.5))
    for p in ORDER:
        s = POLICY[p]
        ns = sorted(data[p])
        m = [data[p][n][0] for n in ns]
        ax.plot(ns, m, color=s['color'], ls=s['ls'], lw=1.1)
        for n in ns:
            mm, lo, hi = data[p][n]
            ax.plot([n, n], [lo, hi], color=s['color'], lw=1.1)
        ax.plot(ns, m, color=s['color'], marker=s['marker'], ls='none', ms=5,
                mfc='white' if p == 'linear' else s['color'])
        ax.annotate(s['label'], (ns[-1], m[-1]), xytext=(6, 0), textcoords='offset points', va='center', color=s['color'], fontsize=8)
    ax.set_xscale('log')
    ax.set_xticks([500, 1000, 5000, 10000], ['500', '1,000', '5,000', '10,000'])
    ax.minorticks_off()
    ax.set_xlim(420, 26000)
    zero_line(ax, 'y')
    ygrid(ax)
    ax.set_xlabel('Members (logarithmic scale)')
    ax.set_ylabel('Work difference from linear\n(units per member-day)')
    fig.tight_layout(pad=.4)
    return save(fig, 'organizational-scale')


# --------------------------------------------------------------------------
# Founding comparisons N30 and N120 (paired worlds).

ENDPOINTS = [('work_per_present_member_workday', 'Work', 'units per present member-workday'),
             ('decision_regret_per_decision', 'Decision regret', 'units per decision'),
             ('net_resource_gain_per_present_member_workday', 'Net resource gain', 'units per present member-workday')]


def confirmation_contrasts():
    eff, summ = {}, {}
    for n in (30, 120):
        eff[n] = defaultdict(list)
        for r in rows(GEN/f'revision_n{n}_confirmation_effects.csv'):
            eff[n][r['endpoint']].append(float(r['effect']))
        summ[n] = {r['endpoint']: r for r in rows(GEN/f'revision_n{n}_confirmation_summary.csv')}
    fig, axs = plt.subplots(len(ENDPOINTS), 1, figsize=(TEXT_W, 3.9))
    for ax, (e, title, unit) in zip(axs, ENDPOINTS):
        allv = eff[30][e]+eff[120][e]
        span = max(abs(min(allv)), abs(max(allv)))*1.08
        for y, n in ((1, 30), (0, 120)):
            vals = sorted(eff[n][e])
            jit = [((i*0.618) % 1-.5)*.34 for i in range(len(vals))]
            ax.scatter(vals, [y+.08+j for j in jit], s=13, facecolors='none', edgecolors='#7a7a7a', linewidths=.6, zorder=2)
            r = summ[n][e]
            m, lo, hi = float(r['mean_effect']), float(r['lower']), float(r['upper'])
            ax.plot([lo, hi], [y-.3, y-.3], color=INK, lw=1.6)
            ax.plot(m, y-.3, marker='D', color=INK, ms=4.5, zorder=3)
        zero_line(ax)
        ax.set_xlim(-span, span)
        ax.set_ylim(-.6, 1.5)
        ax.set_yticks([1, 0], [f'30 members\n({len(eff[30][e])} worlds)', f'120 members\n({len(eff[120][e])} worlds)'])

        ax.tick_params(axis='y', length=0)
        ax.spines['left'].set_visible(False)
        side = {'Work': BETTER_RIGHT, 'Decision regret': BETTER_LEFT, 'Net resource gain': BETTER_RIGHT}[title]
        ax.set_title(f'{title} ({unit}); {side}', loc='left')
        xgrid(ax)
    fig.tight_layout(pad=.4, h_pad=.6)
    return save(fig, 'founding-confirmation')


# --------------------------------------------------------------------------
# R11.3 enterprise model, founding stage at three sizes.

R11_ARMS = [('control-process', 'Process change, hierarchy kept', '#8a8a8a', 'h'),
            ('dams-witness', 'DAMS, witnessed', '#6a4f86', '^'),
            ('linear-witness', 'Linear, witnessed', '#3e6a8a', 's')]  # column order of the size tables
R11_OUT = [('net_operating_result_per_member_year', 'Net operating result\n(units per member-year)', 1, 'above 0: arm better, below: worse'),
           ('decision_regret_per_available_member_hour', 'Decision regret\n($10^{-6}$ per member-hour, log scale)', 1e6, 'all above 0: arm worse'),
           ('confirmation_mean_days', 'Mean confirmation delay\n(days)', 1, 'above 0: arm worse (slower)')]


def r11_sizes():
    sizes = [(30, 'n30'), (120, 'n120'), (300, 'n300')]
    inf = {n: json.loads((R11/d/'inference.json').read_text()) for n, d in sizes}
    fig, axs = plt.subplots(1, 3, figsize=(TEXT_W, 2.5))
    for ax, (key, title, scale, direction) in zip(axs, R11_OUT):
        values = []
        for k, (arm, name, color, marker) in enumerate(R11_ARMS):
            for i, (n, _) in enumerate(sizes):
                r = inf[n]['comparisons'][arm][key]
                x = i+(k-1)*.22  # arms side by side within each size
                lo, hi = (v*scale for v in r['ci95'])
                values += [lo, hi]
                ax.plot([x, x], [lo, hi], color=color, lw=1.6)
                ax.plot(x, r['mean_difference']*scale, marker=marker, color=color, ms=5, ls='none', zorder=3)
        if key == 'decision_regret_per_available_member_hour' and min(values) > 0:
            ax.set_yscale('log')  # differences fall about tenfold per size step and are all positive
            ax.set_ylim(min(values)/1.6, max(values)*1.6)
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f'{v:g}'))
        else:
            zero_line(ax, 'y')
            ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(5))
        ygrid(ax)
        worlds = [inf[n]['comparisons'][R11_ARMS[0][0]][key]['worlds'] for n, _ in sizes]
        ax.set_xticks(range(3), [f'{n:,}\n$n$={w}' for (n, _), w in zip(sizes, worlds)])
        ax.set_xlim(-.5, 2.5)
        ax.set_title(title+'\n'+direction, fontsize=9)
    axs[1].set_xlabel('Initial members ($n$ = paired worlds)')
    handles = [axs[0].plot([], [], marker=m, color=c, ls='none', label=nm)[0] for _, nm, c, m in R11_ARMS]
    fig.legend(handles=handles, loc='upper center', ncol=3, bbox_to_anchor=(.5, 1.0))
    fig.tight_layout(pad=.4, rect=(0, 0, 1, .88), w_pad=1.2)
    return save(fig, 'r11-size-comparison')


# --------------------------------------------------------------------------
# Enterprise model: the same DAMS-witness arm against the kept hierarchy in
# every executed adoption setting, one row per study, one panel per outcome.

R11_SETTINGS = [('n300', 'Startup, at founding'), ('traditional', 'Traditional, mid-life'),
                ('listed', 'Listed, mid-life'), ('century', 'Century-old, mid-life'),
                ('unicorn', 'Unicorn, mid-life'), ('ma-centralize', 'Acquirer, centralized target'),
                ('ma-preserve', 'Acquirer, autonomous target')]
# The second title line names both sides of zero the same way in every panel, so the reader never has to
# remember which direction is good for which outcome.
BETTER_RIGHT, BETTER_LEFT = 'DAMS worse \u2190 0 \u2192 DAMS better', 'DAMS better \u2190 0 \u2192 DAMS worse'
R11_CONTEXT_OUT = [('net_operating_result_per_member_year', 'Net operating result\n(units per member-year)', 1, BETTER_RIGHT),
                   ('governance_cost_per_member_year', 'Legacy governance index\n(mixed units per member-year)', 1, BETTER_LEFT),
                   ('decision_regret_per_available_member_hour', 'Decision regret\n($10^{-6}$ per member-hour)', 1e6, BETTER_LEFT),
                   ('confirmation_mean_days', 'Mean confirmation delay\n(days)', 1, BETTER_LEFT)]


def r11_contexts():
    rows_ = [(d, name, json.loads((R11/d/'inference.json').read_text())['comparisons']['dams-witness'])
             for d, name in R11_SETTINGS if (R11/d/'inference.json').exists()]
    st = POLICY['sublinear']
    fig, axs = plt.subplots(2, 2, figsize=(TEXT_W, 1.5+.3*len(rows_)*2), sharey=True)
    for ax, (key, title, scale, direction) in zip(axs.flat, R11_CONTEXT_OUT):
        for y, (_, _, comp) in enumerate(rows_):
            r = comp[key]
            lo, hi = (v*scale for v in r['ci95'])
            ax.plot([lo, hi], [y, y], color=st['color'], lw=1.6)
            ax.plot(r['mean_difference']*scale, y, marker=st['marker'], color=st['color'], ms=5, ls='none', zorder=3)
        lo, hi = ax.get_xlim()
        lo, hi = min(lo, 0), max(hi, 0)
        ax.set_xlim(lo-.04*(hi-lo), hi+.04*(hi-lo))
        zero_line(ax)
        xgrid(ax)
        ax.set_title(title+'\n'+direction)
    n = len(rows_)
    labels = [f"{name} ({comp['net_operating_result_per_member_year']['worlds']})" for _, name, comp in rows_]
    for ax in axs[:, 0]:
        ax.set_yticks(range(n), labels)
    axs[0, 0].set_ylim(n-.5, -.5)
    fig.supxlabel('DAMS with a witnessed record minus the kept performance-tier hierarchy (paired worlds in brackets)', fontsize=9, y=.0)
    fig.tight_layout(pad=.4, h_pad=1.0, w_pad=1.2)
    return save(fig, 'r11-contexts')


# --------------------------------------------------------------------------
# Assumption robustness (protocol Amendments 4-6): change of the DAMS-minus-
# hierarchy contrast from its baseline on the same worlds, one row per variant.

ROBUST_ROWS = [  # (file, label, operating outcomes can respond in this setting)
    ('independent-traits', 'Uncorrelated traits', True), ('big-five-employees', 'Employee-sample trait correlations', True),
    ('evaluation-bias', 'Evaluation bias in review', True), ('high-turnover', 'Quit rate ×1.5', True),
    ('low-turnover', 'Quit rate ×0.5', True), ('reversed-share-response', 'Effort falls with formal share', True),
    ('spells-founding', 'Sickness in spells', True), ('demand-matched-founding', 'Demand matched to capacity', True),
    ('no-origin-preferences', 'No origin preference shifts', False), ('no-factions', 'No informal factions', False),
    ('strong-factions', 'Strong informal factions', False), ('demand-shock', '30% demand fall for 180 days', False),
    ('spells-traditional', 'Sickness in spells (traditional, mid-life)', True),
    ('demand-matched-traditional', 'Demand matched to capacity (traditional)', True)]
ROBUST_OUT = [('net_operating_result_per_member_year', 'Net operating result\n(units per member-year)\nright of 0: DAMS loss shrinks', 1),
              ('decision_regret_per_available_member_hour', 'Decision regret\n($10^{-6}$ per member-hour)\nright of 0: DAMS regret grows', 1e6)]


def r11_robustness():
    rows_ = [(f, lab, live, json.loads((R11/'robustness'/f'{f}.json').read_text()))
             for f, lab, live in ROBUST_ROWS if live and (R11/'robustness'/f'{f}.json').exists()]  # variants without a channel are named in the caption
    st = POLICY['sublinear']
    fig, axs = plt.subplots(1, 2, figsize=(TEXT_W, .9+.26*len(rows_)), sharey=True)
    for ax, (key, title, scale) in zip(axs, ROBUST_OUT):
        cis = {y: [v*scale for v in d['outcomes'][key]['change_from_baseline']['ci95']] for y, (_, _, live, d) in enumerate(rows_) if live}
        # Axis limit from the bulk of the variants; a variant far outside it is drawn at the edge with its value.
        ext = sorted(max(abs(lo), abs(hi)) for lo, hi in cis.values())
        lim = ext[-1] if len(ext) < 2 or ext[-1] <= 3*ext[-2] else ext[-2]
        lim *= 1.15
        for y, (_, _, live, d) in enumerate(rows_):
            if not live:
                ax.text(0, y, 'no channel to outcomes', ha='center', va='center', fontsize=8, color=REF,
                        bbox=dict(boxstyle='square,pad=.1', fc='white', ec='none'))
                continue
            ch = d['outcomes'][key]['change_from_baseline']
            m = ch['mean']*scale
            lo, hi = cis[y]
            if hi > lim or lo < -lim:
                edge = lim if m > 0 else -lim
                arrow = '\u2192' if m > 0 else '\u2190'
                txt = f'off axis: {m:+.1f} [{lo:.1f}, {hi:.1f}] {arrow}' if m > 0 else f'{arrow} off axis: {m:+.1f} [{lo:.1f}, {hi:.1f}]'
                ax.text(edge, y, txt, ha='right' if m > 0 else 'left', va='center', fontsize=8, color=st['color'],
                        bbox=dict(boxstyle='square,pad=.1', fc='white', ec='none'), zorder=4)
                continue
            ax.plot([lo, hi], [y, y], color=st['color'], lw=1.6)
            ax.plot(m, y, marker=st['marker'], ms=5, ls='none', zorder=3, color=st['color'])
        ax.set_xlim(-lim, lim)
        zero_line(ax)
        xgrid(ax)
        ax.set_title(title)
    axs[0].set_yticks(range(len(rows_)), [f"{lab} ({d['worlds_common']})" for _, lab, _, d in rows_])
    axs[0].set_ylim(len(rows_)-.5, -.5)
    fig.supxlabel('Change of the DAMS-minus-hierarchy contrast from its baseline, same worlds (0 = unchanged)', fontsize=9, y=.0)
    fig.tight_layout(pad=.4, w_pad=1.2)
    return save(fig, 'r11-robustness')


# --------------------------------------------------------------------------
# Daily dynamics, recomputed from the raw confirmation cases of the reproduced
# historical study (DAMS_LEGACY_RUNS points at runs/thesis-reproduction).

def governance_dynamics(runs_dir):
    import statistics
    traces = defaultdict(dict)  # rule -> world -> daily rows
    for case in sorted(Path(runs_dir, 'confirmation').glob('case-*')):
        man = json.loads((case/'manifest.json').read_text())
        c = man['config']
        if c['backend'] == 'central' and c['update_interval_days'] == 1:
            traces[c['regime']][c['world']] = rows(case/'timeseries.csv')
    n = {p: len(v) for p, v in traces.items()}
    members = 120
    key = 'output_work_units'
    # The review backlog grows identically under every rule, so only work is drawn;
    # the backlog equality is stated in the text instead of a panel of coinciding lines.
    # Panel (a): levels (means only; their pointwise interval half-widths are reported in the caption).
    # Panel (b): each rule minus Linear, paired by world, with a pointwise 95% band for every rule.
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(TEXT_W, 2.7), gridspec_kw={'width_ratios': [1.15, 1]})
    for a in (ax, bx):
        a.axvspan(25, 30, color='#ececec', lw=0, zorder=0)
    worlds = sorted(set.intersection(*(set(traces[p]) for p in ORDER)))
    days = [int(r['day']) for r in traces['linear'][worlds[0]]]
    series = {p: [[float(traces[p][w][j][key])/members for w in worlds] for j in range(len(days))] for p in ORDER}
    half = []
    for p in ORDER:
        s_ = POLICY[p]
        mean = [statistics.fmean(v) for v in series[p]]
        half += [1.96*statistics.stdev(v)/math.sqrt(len(v)) for v in series[p]]
        ax.plot(days, mean, color=s_['color'], ls=s_['ls'], lw=1.2, marker=s_['marker'], markevery=10, ms=3.5, label=s_['label'])
        if p == 'linear':
            continue
        d = [[x-y for x, y in zip(series[p][j], series['linear'][j])] for j in range(len(days))]
        m = [statistics.fmean(v) for v in d]
        h = [1.96*statistics.stdev(v)/math.sqrt(len(v)) for v in d]
        bx.fill_between(days, [a-b for a, b in zip(m, h)], [a+b for a, b in zip(m, h)], color=s_['color'], alpha=.13, lw=0)
        bx.plot(days, m, color=s_['color'], ls=s_['ls'], lw=1.2, marker=s_['marker'], markevery=10, ms=3.5)
    for a, title in ((ax, '(a) Mean over %d worlds' % len(worlds)), (bx, '(b) Each rule minus Linear, by world')):
        a.set_xlim(0, max(days)); a.set_xlabel('Day'); ygrid(a); a.set_title(title)
    ax.set_ylabel('Generated work\n(units per member-day)')
    zero_line(bx, 'y')
    bx.set_ylabel('Difference from Linear\n(units per member-day)')
    h, l = ax.get_legend_handles_labels()
    h.append(matplotlib.patches.Patch(color='#ececec')); l.append('Site shock')
    fig.legend(h, l, loc='upper center', ncol=6, fontsize=7.5, handlelength=2.0, columnspacing=.8, handletextpad=.4, bbox_to_anchor=(.5, 1.1))
    fig.tight_layout(pad=.4, w_pad=1.2)
    print('dynamics: largest pointwise half-width of a level mean', round(max(half), 4))
    return save(fig, 'governance-dynamics'), n


# --------------------------------------------------------------------------
# Measured resources of the R11.3 enterprise model and independent-world scaling.

MACHINE_STYLE = {'Apple M2 Pro': ('o', '#2b2b2b'), 'n2d': ('s', '#3e6a8a'), 'c4d': ('D', '#8a6a3b'), 'n2-': ('^', '#3b7a6c'), 'e2': ('v', '#6a4f86')}


def machine_style(name):
    for k, v in MACHINE_STYLE.items():
        if k.lower() in name.lower():
            return k, v
    return name, ('^', '#666666')


def r11_resources():
    res = json.loads((R11/'resources.json').read_text())
    runs = [json.loads(l) for l in (R11/'scaling'/'results.jsonl').read_text().splitlines() if l.strip()]
    # One row per formal study, ordered by size and then by start of adoption; a
    # size axis would stack the six 300-member studies on one point.
    names = {'n30-founding': '30, founding', 'n120-founding': '120, founding', 'n300-founding': '300, founding',
             'n300-traditional-mid': '300, traditional', 'n300-listed-mid': '300, listed', 'n300-century-mid': '300, century-old',
             'n300-unicorn-mid': '300, unicorn', 'n300-acquirer-centralize': '300, acquirer (central.)',
             'n300-acquirer-preserve': '300, acquirer (auton.)'}
    order = [k for k in names if k in res]+sorted(k for k in res if k not in names)
    fig = plt.figure(figsize=(TEXT_W*.94, .9+.24*len(order)))  # tight bbox adds the long row labels
    gs = fig.add_gridspec(1, 3, width_ratios=[1.25, 1, .85])
    axs = [fig.add_subplot(gs[0, 0])]
    axs.append(fig.add_subplot(gs[0, 1], sharey=axs[0]))
    axs.append(fig.add_subplot(gs[0, 2]))
    seen = {}
    for y, k in enumerate(order):
        r = res[k]
        key, (marker, color) = machine_style(r['machine'])
        for ax, field in ((axs[0], 'arm_seconds_per_simulated_day'), (axs[1], 'arm_peak_rss_mb')):
            v = r.get(field)
            if not v:
                continue
            ax.plot([v['min'], v['max']], [y, y], color=color, lw=1)
            seen[key] = ax.plot(v['median'], y, marker=marker, color=color, ms=5, ls='none')[0]
    counts = {k: res[k].get('arm_seconds_per_simulated_day', {}).get('n') for k in order}
    axs[0].set_yticks(range(len(order)), [names.get(k, k)+(f' ({counts[k]} arms)' if counts[k] else '') for k in order])
    axs[0].set_ylim(len(order)-.5, -.5)
    plt.setp(axs[1].get_yticklabels(), visible=False)
    axs[0].set_xscale('log')
    axs[0].set_xticks([.05, .2, 1], ['0.05', '0.2', '1']); axs[0].minorticks_off()
    axs[0].set_title('Seconds per day\n(log scale)'); axs[1].set_title('Peak memory\n(MiB)')
    for ax in axs[:2]:
        xgrid(ax)
    # Strong scaling: same four worlds, 1, 2 and 4 workers, three repeats.
    by = defaultdict(list)
    for r in runs:
        by[r['workers']].append(r['wall_seconds'])
    import statistics
    base = statistics.median(by[1])
    ws = sorted(by)
    ax = axs[2]
    ax.plot(ws, ws, color='#777777', lw=.9, ls=(0, (3, 2)), zorder=5)
    ax.text(.04, .97, 'dashed: ideal\n(speedup =\nprocesses)', transform=ax.transAxes, ha='left', va='top', fontsize=7.5, color='#555555')
    sp = [base/statistics.median(by[w]) for w in ws]
    for w in ws:
        lo, hi = base/max(by[w]), base/min(by[w])
        ax.plot([w, w], [lo, hi], color=INK, lw=1)
    ax.plot(ws, sp, color=INK, marker='o', ms=5)
    ax.set_xticks(ws); ax.set_yticks(ws)
    ax.set_xlim(.6, ws[-1]+.6); ax.set_ylim(.6, ws[-1]+.6)
    ax.set_title('Speedup, four worlds\n(log-log)')
    ax.set_xlabel('Worker processes')
    ax.set_ylabel('Speedup over one process')
    ax.grid(True, color=GRID, lw=.6); ax.set_axisbelow(True)
    labels = {'Apple M2 Pro': 'Workstation (Apple M2 Pro)', 'n2d': 'Cloud N2D', 'c4d': 'Cloud C4D', 'n2-': 'Cloud N2'}
    fig.legend([seen[k] for k in seen], [labels.get(k, k) for k in seen], loc='upper center', ncol=len(seen), bbox_to_anchor=(.5, 1.02))
    fig.tight_layout(pad=.4, rect=(0, 0, 1, .9), w_pad=1.0)
    return save(fig, 'r11-resources'), {w: round(base/statistics.median(by[w]), 3) for w in ws}


def r11_scale():
    """Recorded resource profiles with each workload's actual observation scope.

    The small-organization prehistory profiles remain in resources.json. A
    partial probe on a different machine is a separate row, not a scaling curve.
    """
    scale = json.loads((R11/'scale.json').read_text())
    pts = []
    for h in scale['histories']:
        pts.append(dict(n=h['members'], machine=h['machine'], filled=True, s=h['seconds_per_day'], m=h['peak_rss_mb']))
    for p in scale['probes']:
        if p['seconds_per_day']:
            pts.append(dict(n=p['members'], machine=p['machine'], filled=False, s=p['seconds_per_day'], m=p['peak_rss_mb'],
                            days=p['days_measured']))
    pts.sort(key=lambda q: (not q['filled'], q['n']))
    if not pts:
        raise ValueError('no recorded resource profiles; missing data are not zero')
    fig, axs = plt.subplots(1, 2, figsize=(TEXT_W, 2.65), sharey=True)
    labels = []
    for q in pts:
        scope = '731 days' if q['filled'] else f"{q['days']}-day probe"
        count = q['s']['n']
        labels.append(f"{q['n']:,} members\n{q['machine'].removeprefix('Cloud ')}; {scope}; {count} world{'s' if count != 1 else ''}")
    for ax, key, title in ((axs[0], 's', 'Seconds / simulated day'), (axs[1], 'm', 'Peak memory (MiB)')):
        for y, q in enumerate(pts):
            marker = 's' if q['filled'] else 'D'
            v = q[key]
            if not q['filled']:
                ax.axhspan(y-.42, y+.42, color='#f0f0f0', zorder=0)
            ax.plot([v['min'], v['max']], [y, y], color=INK, lw=1.2)
            ax.plot(v['median'], y, marker=marker, color=INK, ms=5, ls='none',
                    mfc=INK if q['filled'] else 'white', mec=INK)
            number = f"{v['median']:.1f}" if key == 's' else f"{v['median']:,.0f}"
            ax.annotate(number, (v['median'], y), xytext=(6, 6), textcoords='offset points',
                        fontsize=9, va='bottom')
        ax.set_title(title)
        ax.set_ylim(len(pts)-.55, -.6)
        ax.xaxis.grid(True, color=GRID, lw=.6)
        ax.set_axisbelow(True)
        ax.tick_params(axis='y', length=0, pad=8)
    axs[0].set_yticks(range(len(pts)), labels)
    axs[0].set_xscale('log')
    low = min(q['s']['min'] for q in pts)
    high = max(q['s']['max'] for q in pts)
    axs[0].set_xlim(low/1.5, high*2.0)
    axs[0].set_xticks([10, 20, 100, 200], ['10', '20', '100', '200'])
    axs[0].minorticks_off()
    axs[0].set_xlabel('Logarithmic scale')
    axs[1].set_xlim(0, max(q['m']['max'] for q in pts)*1.23)
    axs[1].xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(4))
    axs[1].xaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter('{x:,.0f}'))
    fig.subplots_adjust(left=.39, right=.97, top=.88, bottom=.21, wspace=.28)
    return save(fig, 'r11-scale')


# --------------------------------------------------------------------------
# Exploratory scale study (Appendix C): per-member outcomes over initial size N
# under design variants that relax per-capita rules; data from runs/scale summarized
# into 04_evaluation/r11/scale-study.json.

SCALE_VARIANTS = [('baseline', 'Default design', INK, 'o', '-'),
                  ('scale-no-work-stop', 'No stop of new work at the end', '#3e6a8a', 's', '--'),
                  ('review-stable', 'Review capacity 1.6 per member-day', '#6a4f86', '^', '-.'),
                  ('scale-fixed-review', 'Fixed total review capacity', '#8a6a3b', 'D', ':')]


def scale_study():
    data = json.loads((R11/'scale-study.json').read_text())
    by = defaultdict(dict)
    for r in data:
        by[r['variant']][r['N']] = r
    fig, axs = plt.subplots(2, 2, figsize=(TEXT_W, 4.9))
    axs = axs.ravel()
    for v, name, color, marker, ls in SCALE_VARIANTS:
        ns = sorted(by[v])
        if not ns:
            continue
        diff = [by[v][n]['net_operating_result_per_member_year']['diff_ci'] for n in ns]
        off = {'baseline': -.03, 'scale-no-work-stop': -.01, 'review-stable': .01, 'scale-fixed-review': .03}[v]
        xs = [n*10**off for n in ns]
        axs[0].plot(xs, [d[0] for d in diff], color=color, ls=ls, lw=1, marker=marker, ms=4, label=name)
        for x, d in zip(xs, diff):
            axs[0].plot([x, x], [d[1], d[2]], color=color, lw=1.2)
        series = [[by[v][n]['net_operating_result_per_member_year']['hierarchy'] for n in ns],
                  [by[v][n]['backlog_slope_control-hierarchy'] for n in ns],
                  [by[v][n]['decision_error_control-hierarchy'] for n in ns]]
        for ax, y in zip(axs[1:], series):
            ax.plot(ns, y, color=color, ls=ls, lw=1, marker=marker, ms=4)
    floor = math.atan(.2)/math.pi
    axs[3].axhline(floor, color=REF, lw=.8, ls='--')
    axs[3].annotate('shared-signal floor arctan(0.2)/$\\pi$', (30, floor), xytext=(2, -10), textcoords='offset points', fontsize=7.5, color='#555555')
    zero_line(axs[0], 'y')
    zero_line(axs[2], 'y')
    titles = ['(a) DAMS minus hierarchy, net result\n(units per member-year; below 0: DAMS lower)',
              '(b) Hierarchy arm, net result\n(units per member-year)',
              '(c) Review backlog growth, hierarchy arm\n(records per member-day; above 0: queue grows)',
              '(d) Wrong guild decisions, hierarchy arm\n(share of completed decisions)']
    ns_all = sorted({n for v in by.values() for n in v})
    for ax, t in zip(axs, titles):
        ax.set_xscale('log')
        ax.set_xticks(ns_all, [f'{n:,}' for n in ns_all], fontsize=7.5)
        for n, lab in zip(ns_all, ax.get_xticklabels()):
            lab.set_ha({600: 'right', 1000: 'left'}.get(n, 'center'))
        ax.minorticks_off()
        ax.set_title(t, loc='left')
        ygrid(ax)
    for ax in axs[2:]:
        ax.set_xlabel('Initial members (log scale)')
    axs[3].set_ylim(0, max(.16, axs[3].get_ylim()[1]))
    fig.legend(*axs[0].get_legend_handles_labels(), loc='upper center', ncol=2, fontsize=8, bbox_to_anchor=(.5, 1.0))
    fig.tight_layout(pad=.4, w_pad=1.2, h_pad=1.0, rect=(0, 0, 1, .92))
    return save(fig, 'r11-scale-study')


# --------------------------------------------------------------------------
# Formal studies of Amendment 12: each changed arm minus the kept hierarchy, one panel per primary
# endpoint. Grey dots are worlds (the inferential unit), the marker is the mean, the thick bar the
# marginal 95 % interval and the thin bar the Bonferroni interval over the four endpoints; dashed
# lines mark the pre-registered threshold +/- Delta_k (Table A).

STUDY_ARMS = [('control-process', 'Process change,\nhierarchy kept'), ('dams-witness', 'DAMS,\nwitnessed'),
              ('linear-witness', 'Linear,\nwitnessed')]
STUDY_OUT = [('net_operating_result_per_member_year', 'E1 Net operating result\n(units per member-year)', 1, 'arm worse \u2190 0 \u2192 arm better'),
             ('decision_regret_per_available_member_hour', 'E2 Decision regret\n($10^{-6}$ per available member-hour)', 1e6, 'arm better \u2190 0 \u2192 arm worse'),
             ('governance_member_hours_per_member_year', 'E3 Governance time\n(member-hours per member-year)', 1, 'arm uses less \u2190 0 \u2192 arm uses more'),
             ('witness_resource_units_per_member_year', 'E4 Witness resources\n(units per member-year)', 1, 'arm uses less \u2190 0 \u2192 arm uses more')]


def formal_study(study, name):
    data = json.loads((R11/study/'figure.json').read_text())
    arms = [(a, lab) for a, lab in STUDY_ARMS if a in data['arms']]
    fig, axs = plt.subplots(2, 2, figsize=(TEXT_W, 1.4+.62*len(arms)*2), sharey=True)
    for ax, (key, title, scale, direction) in zip(axs.flat, STUDY_OUT):
        span = []
        for y, (arm, _) in enumerate(arms):
            r = data['arms'][arm].get(key)
            if r is None:
                continue
            st = R11_ARM_STYLE[arm]
            xs = [d*scale for _, d in r['differences']]
            jit = [(-.18 + .36*(i % 7)/6) for i in range(len(xs))]
            ax.plot(xs, [y+.22+j*.3 for j in jit], 'o', ms=2.6, mfc='white', mec='#888888', mew=.6, zorder=2)
            span += xs
            if r.get('ci_bonferroni'):
                lo, hi = (v*scale for v in r['ci_bonferroni']); ax.plot([lo, hi], [y-.08]*2, color=st[0], lw=.8, zorder=3); span += [lo, hi]
            if r.get('ci95'):
                lo, hi = (v*scale for v in r['ci95']); ax.plot([lo, hi], [y-.08]*2, color=st[0], lw=2.4, zorder=3); span += [lo, hi]
            ax.plot(r['mean']*scale, y-.08, marker=st[1], color=st[0], ms=6, ls='none', zorder=4)
            if r.get('delta'):
                d = r['delta']*scale
                for v in (-d, d):
                    ax.axvline(v, color='#999999', lw=.7, ls=(0, (4, 2)), zorder=1)
        lo, hi = min(span+[0]), max(span+[0])
        ax.set_xlim(lo-.06*(hi-lo), hi+.06*(hi-lo))
        zero_line(ax); xgrid(ax)
        ax.set_title(title+'\n'+direction, loc='left')
    n = len(arms)
    for ax in axs[:, 0]:
        ax.set_yticks(range(n), [f'{lab}\n({data["arms"][a][STUDY_OUT[0][0]]["worlds"]} worlds)' for a, lab in arms])
    axs[0, 0].set_ylim(n-.4, -.6)
    fig.supxlabel('Arm minus the kept performance-tier hierarchy in the same world', fontsize=9, y=.0)
    fig.tight_layout(pad=.4, h_pad=1.0, w_pad=1.2)
    return save(fig, name)


# --------------------------------------------------------------------------
# Projections beyond the executed horizon and population (Section sec:r11-projections). Observed values are
# filled markers; projected values are open markers or bands, and the label of every projected element says so.

def projections():
    P = R11/'projections'
    proj = json.loads((P/'projections.json').read_text())
    ten = json.loads((P/'tenyear-yearly.json').read_text())
    scale = json.loads((R11/'scale-study.json').read_text())
    fig, (a, b) = plt.subplots(1, 2, figsize=(TEXT_W-.35, 2.9), gridspec_kw={'width_ratios': [1.15, 1]})
    # (a) yearly contrast of net operating result, DAMS witnessed minus hierarchy
    for i, (w, v) in enumerate(ten.items()):
        ys = [d['net_per_member_year']-h['net_per_member_year'] for h, d in zip(v['hierarchy'], v['dams'])]
        a.plot(range(1, len(ys)+1), ys, color='#b5b5b5', lw=.7, zorder=1,
               label='300 members, ten-year worlds (observed)' if i == 0 else None)
    yl = proj['yearly']['dams-witness']['net_per_member_year']
    yrs = list(range(1, len(yl)+1))
    st = R11_ARM_STYLE['dams-witness']
    a.errorbar(yrs, [m for m, _ in yl], yerr=[[m-c[0] for m, c in yl], [c[1]-m for m, c in yl]], fmt=st[1], color=st[0],
               ms=4.5, lw=1.2, zorder=3, label='10,000 members, years 1–5 (observed)')
    level = sum(m for m, _ in yl[1:])/len(yl[1:])
    a.plot([5.5, 10.4], [level]*2, color=st[0], ls='--', lw=1.1, zorder=2, label='10,000 members, years 6–10 (projected)')
    zero_line(a, 'y'); ygrid(a)
    a.set_xticks(range(1, 11)); a.set_xlim(.5, 10.6)
    a.set_xlabel('Year after adoption')
    a.set_title('(a) Horizon: DAMS minus hierarchy, net result by year\n(units per member-year; below 0: DAMS lower)', loc='left')
    a.legend(loc='lower right', fontsize=7.5, handlelength=1.8)
    # (b) five-year contrast by size
    pts = sorted((r['N'], r['net_operating_result_per_member_year']['diff_ci']) for r in scale if r['variant'] == 'baseline')
    b.errorbar([n for n, _ in pts], [d[0] for _, d in pts], yerr=[[d[0]-d[1] for _, d in pts], [d[2]-d[0] for _, d in pts]],
               fmt='o', color='#8a8a8a', ms=4, lw=1, label='Founding, 30–1,000, first year\n(observed)')
    s = proj['scale']['dams-witness']['net_operating_result_per_member_year']
    m, c = s['observed_10000']['mean'], s['observed_10000']['ci95']
    b.errorbar([10000], [m], yerr=[[m-c[0]], [c[1]-m]], fmt=st[1], color=st[0], ms=5, lw=1.2, label='Multinational, 10,000, five years\n(observed)')
    for i, N in enumerate(('50000', '100000')):
        r = s['projection'][N]
        b.plot([int(N)]*2, r['interval'], color=st[0], lw=1)
        b.plot([int(N)], [r['invariant_projection']], marker=st[1], mfc='white', mec=st[0], ms=5, ls='none',
               label='Multinational, 50,000 and\n100,000, five years (projected)' if i == 0 else None)
    zero_line(b, 'y'); ygrid(b)
    b.set_xscale('log'); b.minorticks_off()
    ticks = [30, 300, 1000, 10000, 50000, 100000]
    b.set_xticks(ticks, ['30', '300', '1,000', '10,000', '50,000', '100,000'], fontsize=7.5)
    for t in b.get_xticklabels():
        t.set_rotation(35); t.set_ha('right'); t.set_rotation_mode('anchor')
    b.set_xlabel('Initial members (log scale)')
    b.set_title('(b) Size: DAMS minus hierarchy, net result\n(units per member-year; below 0: DAMS lower)', loc='left')
    h, l = b.get_legend_handles_labels(); o = [1, 2, 0]
    b.legend([h[i] for i in o], [l[i] for i in o], loc='upper left', bbox_to_anchor=(0, .93), fontsize=7.5, handlelength=1.2)
    b.set_ylim(-110, 8)
    fig.tight_layout(pad=.4, w_pad=1.4)
    return save(fig, 'r11-projections')


R11_ARM_STYLE = {'control-process': ('#8a8a8a', 'h'), 'dams-witness': ('#6a4f86', '^'), 'linear-witness': ('#3e6a8a', 's')}


def recovery_table_placement():
    # Preserve all calculated cells; permit the table beside its interpretation.
    path = GEN/'revision_recovery.tex'
    value = path.read_text()
    assert value.startswith((r'\begin{table}[tbp]', r'\begin{table}[htbp]'))
    path.write_text(value.replace(r'\begin{table}[tbp]', r'\begin{table}[htbp]', 1))


if __name__ == '__main__':
    recovery_table_placement()
    # Unfinished-record stocks come from the generated ledger-stress table of the same study.
    unfinished = {}
    for line in (GEN/'revision_table_ledger-stress.tex').read_text().splitlines():
        cells = [c.strip() for c in line.rstrip('\\ ').split('&')]
        if len(cells) == 6 and cells[0].lower() in BACKEND:
            unfinished[cells[0].lower()] = int(cells[3])
    assert set(unfinished) == set(BACKEND), unfinished
    made = [allocation_effects(), outcome_tradeoffs(), formal_authority_distribution(), confirmation_cdf(unfinished)[0],
            response_boundaries(), sensitivity_screen(), attack_cost_effects(), organizational_scale(),
            confirmation_contrasts(), r11_sizes(), r11_contexts(), r11_robustness()]
    path, speedups = r11_resources()
    made.append(path)
    # r11_scale() is kept for the record; Table tab:r11-scale replaced the figure on 2026-10-25.
    if (R11/'scale-study.json').exists():
        made.append(scale_study())
    for study, name in (('main-n10000', 'r11-main-study'), ('scale-n50000', 'r11-scale-n50000'), ('scale-n100000', 'r11-scale-n100000')):
        if (R11/study/'figure.json').exists():
            made.append(formal_study(study, name))
    if (R11/'projections'/'projections.json').exists():
        made.append(projections())
    print('speedups', speedups)
    import os
    legacy = os.environ.get('DAMS_LEGACY_RUNS')
    if legacy:
        path, counts = governance_dynamics(legacy)
        made.append(path)
        print('dynamics worlds per rule', counts)
    for p in made:
        print(p.relative_to(ROOT))
