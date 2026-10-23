"""World-count rules for the enterprise studies (docs/r11-formal-protocol.md).

Amendment 8 (default): four primary endpoints with separate units; for each, an
absolute threshold delta_k = 2 % of a positive reference-arm anchor that involves
no contrast; and the smallest R with t_{0.975,R-1} * s_k / sqrt(R) <= delta_k / 2,
where s_k is the pilot SD of paired differences (dams-witness minus
control-hierarchy). R* = max(10, max_k R_k). The pilot's small degrees of freedom
are reported through the one-sided 80 % upper confidence limit of the SD,
s_k * sqrt(nu / chi2_{nu;0.20}), as a sensitivity value that does not set R*.

legacy_world_count() keeps the rule executed by Amendments 1-7 (fixed 2.262 and
delta = 2 % of |reference mean|) for those studies' records.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research_tools.r11_outcomes import outcomes  # noqa: E402

LEGACY_PRIMARY = ('net_operating_result_per_member_year', 'decision_regret_per_available_member_hour',
                  'governance_cost_per_member_year')
# endpoint -> (unit, anchor in the reference arm: summary keys divided by member-years,
# or the endpoint's own reference mean)
PRIMARY_ENDPOINTS = {
    'net_operating_result_per_member_year': ('resource units per member-year', 'revenue_resource_units'),
    'decision_regret_per_available_member_hour': ('regret units per available member-hour', None),
    'governance_member_hours_per_member_year': ('member-hours per member-year', 'available_member_work_hours'),
    'witness_resource_units_per_member_year': ('resource units per member-year', 'operating_resource_units'),
}
PRIMARY = tuple(PRIMARY_ENDPOINTS)
THRESHOLD_SHARE = .02
MINIMUM = 10


def _lower_gamma_regularized(a, x):
    """P(a, x) by series (x < a+1) or continued fraction (Numerical Recipes)."""
    if x <= 0:
        return 0.
    gln = math.lgamma(a)
    if x < a+1:
        term = total = 1/a
        n = a
        for _ in range(10000):
            n += 1
            term *= x/n
            total += term
            if abs(term) < abs(total)*1e-15:
                break
        return total*math.exp(-x+a*math.log(x)-gln)
    b, c, d = x+1-a, 1/1e-300, 1/(x+1-a)
    h = d
    for i in range(1, 10000):
        an = -i*(i-a)
        b += 2
        d = an*d+b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b+an/c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1/d
        delta = d*c
        h *= delta
        if abs(delta-1) < 1e-15:
            break
    return 1-math.exp(-x+a*math.log(x)-gln)*h


def chi2_quantile(p, df):
    """Lower quantile q with P(chi2_df <= q) = p, by bisection on the CDF."""
    lo, hi = 0., max(10., 10.*df)
    while _lower_gamma_regularized(df/2, hi/2) < p:
        hi *= 2
    for _ in range(200):
        mid = (lo+hi)/2
        if _lower_gamma_regularized(df/2, mid/2) < p:
            lo = mid
        else:
            hi = mid
    return (lo+hi)/2


def required_worlds(rho, minimum=MINIMUM, level=.975):
    """Smallest R >= 2 with t_{level,R-1} * rho / sqrt(R) <= 1/2 (rho = s/delta), at least minimum."""
    from research_tools.r11_inference import t_quantile  # imported here: r11_inference imports PRIMARY
    if rho <= 0:
        return minimum
    r = 2
    while t_quantile(level, r-1)*rho/math.sqrt(r) > .5:
        r += 1
        if r > 10**7:
            raise ValueError('world requirement exceeds 10^7')
    return max(minimum, r)


def endpoint_requirement(differences, anchor, nu_p, share=THRESHOLD_SHARE, minimum=MINIMUM, family=4):
    row = {'pilot_pairs': len(differences), 'anchor': anchor, 'nu_p': nu_p}
    if anchor is None or not anchor > 0:
        row.update(status='no positive anchor', delta=None, required_worlds_point=None, required_worlds_upper80=None)
        return row
    if len(differences) < 2:
        row.update(status='insufficient pilot pairs', delta=None, required_worlds_point=None, required_worlds_upper80=None)
        return row
    delta = share*anchor
    s = statistics.stdev(differences)
    upper = s*math.sqrt(nu_p/chi2_quantile(.2, nu_p))
    row.update(status='ok', delta=delta, sd_paired_difference=s, rho=s/delta, sd_upper80=upper,
               required_worlds_point=required_worlds(s/delta, minimum),
               required_worlds_upper80=required_worlds(upper/delta, minimum),
               required_worlds_bonferroni=required_worlds(s/delta, minimum, level=1-.05/(2*family)))
    return row


def _member_years(arm_dir):
    s = json.loads((Path(arm_dir)/'summary.json').read_text())
    days = s.get('active_member_calendar_days')
    return s, (days/365.25 if days else None)


def world_count(root, arm='dams-witness', reference='control-hierarchy', minimum=MINIMUM):
    worlds = sorted(p for p in Path(root).iterdir() if (p/reference/'arm.json').exists() and (p/arm/'arm.json').exists())
    nu_p = len(worlds)-1
    rows = {}
    for k, (unit, anchor_key) in PRIMARY_ENDPOINTS.items():
        ref = [outcomes(w/reference)[k] for w in worlds]
        dif = [None if r is None or outcomes(w/arm)[k] is None else outcomes(w/arm)[k]-r for w, r in zip(worlds, ref)]
        anchors = []
        for w, r in zip(worlds, ref):
            if anchor_key is None:
                anchors.append(r)
            else:
                s, my = _member_years(w/reference)
                anchors.append(s.get(anchor_key, 0.)/my if my else None)
        defined = [d for d in dif if d is not None]
        anchor = statistics.fmean(anchors) if anchors and all(a is not None for a in anchors) else None
        row = endpoint_requirement(defined, anchor, nu_p, minimum=minimum)
        row.update(unit=unit, anchor_source=anchor_key or 'reference-arm mean of the endpoint',
                   undefined_pairs=len(dif)-len(defined))
        rows[k] = row
    point = {k: r['required_worlds_point'] for k, r in rows.items() if r.get('required_worlds_point')}
    binding = max(point, key=point.get) if point else None
    return {'rule': 'Amendment 8: R_k = min R with t_{0.975,R-1} s_k/sqrt(R) <= delta_k/2, delta_k = 2% of a positive '
                    'reference-arm anchor; R* = max(10, max_k R_k); 80% upper-SD and Bonferroni values are reported',
            'pilot_worlds': len(worlds), 'outcomes': rows, 'R': point[binding] if binding else None,
            'binding_endpoint': binding, 'endpoints_without_requirement': sorted(set(rows)-set(point))}


def legacy_world_count(root, arm='dams-witness', reference='control-hierarchy', t=2.262, minimum=10):
    """Rule executed by Amendments 1-7; kept unchanged for their records."""
    worlds = sorted(p for p in Path(root).iterdir() if (p/reference/'arm.json').exists() and (p/arm/'arm.json').exists())
    rows = {}
    for k in LEGACY_PRIMARY:
        ref = [outcomes(w/reference)[k] for w in worlds]
        dif = [outcomes(w/arm)[k]-outcomes(w/reference)[k] for w in worlds]
        if len(dif) < 2 or any(v is None for v in ref+dif):
            rows[k] = {'worlds': len(dif), 'status': 'insufficient pilot pairs'}
            continue
        delta = .02*abs(statistics.fmean(ref))
        s = statistics.stdev(dif)
        need = max(minimum, math.ceil((t*s/(delta/2))**2)) if delta > 0 else None
        rows[k] = {'worlds': len(dif), 'reference_mean': statistics.fmean(ref), 'delta': delta,
                   'sd_paired_difference': s, 'required_worlds': need}
    req = [r['required_worlds'] for r in rows.values() if r.get('required_worlds')]
    return {'rule': 'max(10, ceil((2.262*s/(delta/2))^2)), delta=2% of reference mean',
            'pilot_worlds': len(worlds), 'outcomes': rows, 'R': max(req) if req else None}


if __name__ == '__main__':
    legacy = len(sys.argv) > 2 and sys.argv[2] == '--legacy'
    print(json.dumps((legacy_world_count if legacy else world_count)(sys.argv[1]), indent=1))
