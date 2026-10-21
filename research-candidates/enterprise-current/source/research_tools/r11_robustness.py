"""Assumption-robustness report for an R11 variant study (protocol Amendments 4 and 5).

A variant study repeats a baseline study on the same world identities with one
declared assumption changed. For every outcome this reports
- the variant's own paired contrast (arm minus reference) with a 95 % interval,
- the change of that contrast from the baseline contrast on the same worlds,
  (arm - reference)_variant - (arm - reference)_baseline, with a 95 % interval
  over the worlds present in both studies,
- the arm means of both studies (absolute levels, e.g. sick absence).
The robustness criterion of Amendment 4: the net-operating-result contrast of
the variant has the baseline's sign and its interval excludes zero.

Usage: python research_tools/r11_robustness.py BASELINE_ROOT VARIANT_ROOT
           [--first-world 1000] [--arm dams-witness] [--reference control-hierarchy]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research_tools.r11_inference import t_quantile  # noqa: E402
from research_tools.r11_outcomes import OUTCOMES, outcomes  # noqa: E402

CRITERION = 'net_operating_result_per_member_year'


def interval(values):
    """Mean and paired Student-t 95 % interval; None below two values."""
    if len(values) < 2:
        return None
    n, m, s = len(values), statistics.fmean(values), statistics.stdev(values)
    h = t_quantile(.975, n-1)*s/math.sqrt(n)
    return {'worlds': n, 'mean': m, 'ci95': [m-h, m+h]}


def study_rows(root, arm, reference, first_world):
    """world id -> {'arm': outcomes, 'reference': outcomes} for worlds with both arms."""
    rows = {}
    for w in sorted(Path(root).iterdir()):
        if not w.is_dir() or '-w' not in w.name:
            continue
        wid = int(w.name.rsplit('-w', 1)[1])
        if wid >= first_world and (w/arm/'arm.json').exists() and (w/reference/'arm.json').exists():
            rows[wid] = {'arm': outcomes(w/arm), 'reference': outcomes(w/reference)}
    return rows


def compare(base, var):
    """base, var: world id -> {'arm': {k: v}, 'reference': {k: v}}. Pure function of the rows."""
    common = sorted(set(base) & set(var))
    report = {'worlds_baseline': len(base), 'worlds_variant': len(var), 'worlds_common': len(common), 'outcomes': {}}
    for k in OUTCOMES:
        def contrast(rows, w):
            a, r = rows[w]['arm'].get(k), rows[w]['reference'].get(k)
            return None if a is None or r is None else a-r
        cv = [c for c in (contrast(var, w) for w in sorted(var)) if c is not None]
        change = []
        for w in common:
            cb, cvw = contrast(base, w), contrast(var, w)
            if cb is not None and cvw is not None:
                change.append(cvw-cb)
        means = {}
        for name, rows in (('baseline', base), ('variant', var)):
            for side in ('reference', 'arm'):
                vals = [rows[w][side][k] for w in rows if rows[w][side].get(k) is not None]
                means[f'{name}_{side}_mean'] = statistics.fmean(vals) if vals else None
        report['outcomes'][k] = {'unit': OUTCOMES[k][0], 'variant_contrast': interval(cv),
                                 'change_from_baseline': interval(change), **means}
    crit = report['outcomes'][CRITERION]
    vc = crit['variant_contrast']
    bc = interval([b for b in (base[w]['arm'].get(CRITERION) - base[w]['reference'].get(CRITERION)
                               for w in base) if b is not None]) if len(base) >= 2 else None
    report['robust'] = bool(vc and bc and math.copysign(1, vc['mean']) == math.copysign(1, bc['mean'])
                            and (vc['ci95'][0] > 0 or vc['ci95'][1] < 0))
    report['baseline_contrast_' + CRITERION] = bc
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('baseline')
    ap.add_argument('variant')
    ap.add_argument('--first-world', type=int, default=1000)
    ap.add_argument('--arm', default='dams-witness')
    ap.add_argument('--reference', default='control-hierarchy')
    ns = ap.parse_args()
    base = study_rows(ns.baseline, ns.arm, ns.reference, ns.first_world)
    var = study_rows(ns.variant, ns.arm, ns.reference, ns.first_world)
    out = compare(base, var)
    out.update({'baseline_root': str(ns.baseline), 'variant_root': str(ns.variant), 'arm': ns.arm, 'reference': ns.reference})
    print(json.dumps(out, indent=1))
