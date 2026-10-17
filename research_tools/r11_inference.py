"""Confirmatory inference for an R11 study, following docs/r11-formal-protocol.md.

For each comparison arm against the reference arm and each primary outcome:
paired differences over independent worlds, mean, paired Student-t 95 %
interval, two-sided p-value, Holm adjustment across the three primary outcomes
within the comparison, and the achieved half-width against the declared
threshold delta (2 % of the reference-arm mean of the pilot, passed in).
Secondary outcomes get the same interval, labelled unadjusted.

Usage: python research_tools/r11_inference.py STUDY_ROOT [--first-world 1000]
           [--delta-json PILOT_WORLD_COUNT.json]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research_tools.r11_outcomes import OUTCOMES, outcomes  # noqa: E402
from research_tools.r11_world_count import PRIMARY  # noqa: E402


def _betacf(a, b, x):
    """Continued fraction for the regularized incomplete beta (Lentz)."""
    tiny = 1e-300
    c, d = 1., 1.-(a+b)*x/(a+1)
    d = 1/(d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2*m
        aa = m*(b-m)*x/((a+m2-1)*(a+m2))
        d = 1+aa*d; d = 1/(d if abs(d) > tiny else tiny)
        c = 1+aa/c if abs(1+aa/c) > tiny else tiny
        h *= d*c
        aa = -(a+m)*(a+b+m)*x/((a+m2)*(a+m2+1))
        d = 1+aa*d; d = 1/(d if abs(d) > tiny else tiny)
        c = 1+aa/c if abs(1+aa/c) > tiny else tiny
        delta = d*c; h *= delta
        if abs(delta-1) < 1e-15:
            return h
    raise ArithmeticError('incomplete beta did not converge')


def _ibeta(a, b, x):
    if x <= 0:
        return 0.
    if x >= 1:
        return 1.
    front = math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log1p(-x))
    if x < (a+1)/(a+b+2):
        return front*_betacf(a, b, x)/a
    return 1-front*_betacf(b, a, 1-x)/b


def t_two_sided_p(t, df):
    return _ibeta(df/2, .5, df/(df+t*t))


def t_quantile(p, df):
    """Upper quantile: P(T <= q) = p, for 0.5 < p < 1, by bisection on the CDF."""
    lo, hi = 0., 1e3
    for _ in range(200):
        mid = (lo+hi)/2
        if 1-t_two_sided_p(mid, df)/2 < p:
            lo = mid
        else:
            hi = mid
    return (lo+hi)/2


def holm(pvalues):
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted, running = [0.]*len(pvalues), 0.
    for rank, i in enumerate(order):
        running = max(running, min(1., (len(pvalues)-rank)*pvalues[i]))
        adjusted[i] = running
    return adjusted


def infer(root, *, first_world=1000, reference='control-hierarchy', deltas=None):
    root = Path(root)
    worlds = sorted(p for p in root.iterdir() if p.is_dir() and (p/reference/'arm.json').exists()
                    and int(p.name.rsplit('-w', 1)[1]) >= first_world)
    arms = sorted({a.name for w in worlds for a in w.iterdir() if (a/'arm.json').exists() and a.name != reference})
    failures = sorted(str(f.relative_to(root)) for f in root.rglob('failure.json'))
    report = {'study_root': str(root), 'reference': reference, 'first_world': first_world,
              'worlds_with_reference': len(worlds), 'failures': failures, 'comparisons': {}}
    for arm in arms:
        rows, primary_p = {}, []
        for k in OUTCOMES:
            d = []
            for w in worlds:
                if (w/arm/'arm.json').exists():
                    o, b = outcomes(w/arm), outcomes(w/reference)
                    if o.get(k) is not None and b.get(k) is not None:
                        d.append(o[k]-b[k])
            if len(d) < 2:
                rows[k] = {'worlds': len(d), 'status': 'fewer than two complete pairs'}
                continue
            n, m, s = len(d), statistics.fmean(d), statistics.stdev(d)
            q = t_quantile(.975, n-1)
            se = s/math.sqrt(n)
            t = m/se if se > 0 else math.inf
            p = t_two_sided_p(t, n-1) if math.isfinite(t) else 0.
            row = {'unit': OUTCOMES[k][0], 'worlds': n, 'mean_difference': m, 'sd_difference': s,
                   'ci95': [m-q*se, m+q*se], 'half_width': q*se, 'p_two_sided': p,
                   'role': 'primary' if k in PRIMARY else 'secondary (unadjusted)'}
            if deltas and k in deltas:
                row['delta'] = deltas[k]
                row['half_width_within_delta_over_2'] = q*se <= deltas[k]/2
            rows[k] = row
            if k in PRIMARY:
                primary_p.append(k)
        for k, adj in zip(primary_p, holm([rows[k]['p_two_sided'] for k in primary_p])):
            rows[k]['p_holm'] = adj
        report['comparisons'][arm] = rows
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--first-world', type=int, default=1000)
    ap.add_argument('--delta-json', help='output of r11_world_count.py on the pilot')
    ns = ap.parse_args()
    deltas = None
    if ns.delta_json:
        wc = json.loads(Path(ns.delta_json).read_text())
        deltas = {k: v['delta'] for k, v in wc['outcomes'].items() if 'delta' in v}
    print(json.dumps(infer(ns.root, first_world=ns.first_world, deltas=deltas), indent=1))
