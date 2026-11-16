"""Paired statistics over worlds for the automation / nested-sandbox study (prespecification).

    python stats.py results.json --out stats.json

The unit of inference is the world (firm).  A contrast (first arm minus second) uses every world in which both arms are
present in results.json, which holds accepted cells only (collect.py reads accept.json); the worlds used and the worlds
left out, with the reason, are stored with it.

Per arm and outcome: mean, SD, 95% Student-t interval over worlds.
Per contrast and outcome: the paired differences, their mean, SD, 95% Student-t interval (exact quantile from a numerical
inversion of the t distribution, no external library), two-sided p-value of the paired t test, number of worlds and number
of worlds with a positive difference.  With one world only the point value is reported (``ci95`` and ``p_value`` are null
and ``note`` says "n=1, no interval"); a zero SD with two or more worlds also gives null interval and p.

Primary outcome (isa_spec.PRIMARY_OUTCOME): net operating result per member-year over the days from the branch day on.
Four primary contrasts P1 to P4 (Holm step-down adjusted p-values over the four, family size fixed at 4; a contrast without
a p-value is ranked last, which is the conservative reading).  Secondary contrasts S1 to S7 and all secondary outcomes are
reported unadjusted and labelled so.  For the primary outcome (and the whole-run net result) the difference is also given
as a percentage of the Hierarchy mean over the same worlds (``percent_of_hierarchy_mean``).

Contrast 3 rule (interpretation rules of the prespecification).  The interval of P3 on the primary outcome is expressed
as a percentage of the mean of control-hierarchy (the Hierarchy) over the worlds of the contrast.  Statement A applies if the interval lies
wholly inside +-2%: the authority rule makes no material operating difference in this model.  Statement B applies if the
interval excludes zero: the sign is reported.  Statement C applies otherwise: the question is not resolved (a
non-significant difference is not equivalence).  When A and B both apply they are reported in one sentence (sign resolved, size inside the margin: no material operating difference).  Without an interval
(n=1) no statement applies and the field says so.
Contrast 2 rule: P2 is positive if and only if its 95% interval lies above zero; point value and interval are reported.

``unfunded_check``: per arm, the number of accepted worlds with a non-zero unfunded operating amount after adoption (above
isa_spec.UNFUNDED_TOLERANCE), the maximum and the mean per member-year, the share of the charge left unpaid, and ``all_zero``.
When not all zero, a plain sentence names the arms and says that their net operating result is not net of all processing cost.
``operating_unfunded_resource_units`` also holds shortfalls of the other daily operating bills (README, "The unfunded amount").
``sentences``: the outcome-dependent sentences of the results section (LaTeX-ready text; one wording for every possible outcome): firms
planned/started/stopped/completed, precision, order of the arms, the P2 statement, margin and statement of contrast 3, attribution (S4, S1),
and the processing rate at which Sandbox DAMS and its comparator coincide (P1, P2, S3).  make_tex.py turns them into macros.
``scope``: worlds planned, started, stopped, accepted (from collect.py) and the Student-t half-width factor t(0.975, n-1)/sqrt(n)
for the accepted n beside those for 7 and 10 worlds: the interval half-width is this factor times the SD of the paired differences.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import isa_spec as spec  # noqa: E402


# ---- Student t distribution -------------------------------------------------------------------

def _betacf(a, b, x):
    tiny = 1e-300
    qab, qap, qam = a+b, a+1., a-1.
    c, d = 1., 1.-qab*x/qap
    d = 1./(d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 500):
        m2 = 2*m
        aa = m*(b-m)*x/((qam+m2)*(a+m2))
        d = 1.+aa*d
        d = 1./(d if abs(d) > tiny else tiny)
        c = 1.+aa/c
        c = c if abs(c) > tiny else tiny
        h *= d*c
        aa = -(a+m)*(qab+m)*x/((a+m2)*(qap+m2))
        d = 1.+aa*d
        d = 1./(d if abs(d) > tiny else tiny)
        c = 1.+aa/c
        c = c if abs(c) > tiny else tiny
        delta = d*c
        h *= delta
        if abs(delta-1.) < 1e-15:
            break
    return h


def betainc(a, b, x):
    """Regularised incomplete beta function I_x(a, b)."""
    if x <= 0.:
        return 0.
    if x >= 1.:
        return 1.
    front = math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log1p(-x))
    if x < (a+1.)/(a+b+2.):
        return front*_betacf(a, b, x)/a
    return 1.-front*_betacf(b, a, 1.-x)/b


def t_two_sided_p(t, df):
    return betainc(df/2., .5, df/(df+t*t))


def t_quantile(p, df):
    """Quantile of Student's t for 0.5 < p < 1, by bisection on the exact distribution function."""
    target = 2.*(1.-p)  # two-sided tail probability
    lo, hi = 0., 1.
    while t_two_sided_p(hi, df) > target:
        hi *= 2.
    for _ in range(200):
        mid = (lo+hi)/2.
        if t_two_sided_p(mid, df) > target:
            lo = mid
        else:
            hi = mid
    return (lo+hi)/2.


def paired_summary(values):
    """Summary of a list of paired differences (or of per-world values for an arm)."""
    n = len(values)
    out = {'values': values, 'n': n, 'mean': statistics.fmean(values) if n else None, 'sd': None, 'ci95': None,
           't_critical': None, 'p_value': None, 'positive_count': sum(1 for v in values if v > 0),
           'note': None}
    if n == 1:
        out['note'] = 'n=1, no interval'
    elif n >= 2:
        sd = statistics.stdev(values)
        out['sd'] = sd
        if sd > 0:
            crit = t_quantile(.975, n-1)
            half = crit*sd/math.sqrt(n)
            out['t_critical'] = crit
            out['ci95'] = [out['mean']-half, out['mean']+half]
            out['p_value'] = t_two_sided_p(out['mean']/(sd/math.sqrt(n)), n-1)
        else:
            out['note'] = 'zero SD: interval and p-value undefined'
    return out


def holm(pvalues, family_size):
    """Holm step-down adjusted p-values; pvalues is {name: p or None}.  A missing p ranks last (adjusted p null)."""
    ranked = sorted((p, k) for k, p in pvalues.items() if p is not None)
    adjusted, running = {k: None for k in pvalues}, 0.
    for i, (p, k) in enumerate(ranked):
        running = max(running, min(1., (family_size-i)*p))
        adjusted[k] = running
    return adjusted


# ---- assembling ---------------------------------------------------------------------------------

def world_values(results):
    """{arm: {outcome: {world: value}}}; None values are left out."""
    table = {}
    for world, wd in results['worlds'].items():
        for arm, d in wd['arms'].items():
            slot = table.setdefault(arm, {})
            for name, value in d['values'].items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    slot.setdefault(name, {})[world] = float(value)
    return table


def percent_of(ref_mean, x):
    return None if (x is None or not ref_mean) else 100.*x/ref_mean


def contrast3_rule(c, ref_mean):
    """The three pre-specified statements for P3 on the primary outcome.  `text` completes the sentence "For P3, <text>."."""
    out = {'contrast': spec.CONTRAST_BY_ID[spec.CONTRAST3][0], 'margin_percent_of_hierarchy_mean': spec.MARGIN_PERCENT,
           'hierarchy_mean': ref_mean, 'margin_in_units': None if ref_mean is None else spec.MARGIN_PERCENT/100.*ref_mean,
           'mean_percent': None, 'ci95_percent': None, 'statements_applying': [], 'status': None, 'text': None}
    if c is None or c['mean'] is None:
        out.update(status='not estimable', text='no firm has both arms accepted, so the contrast is not estimated')
        return out
    out['mean_percent'] = percent_of(ref_mean, c['mean'])
    if c['ci95'] is None:
        why = ('with one firm no interval can be computed' if c.get('n') == 1
               else 'the paired differences do not vary, so no interval can be computed')
        out.update(status='undetermined', text=why + ' and none of the three statements applies')
        return out
    lo, hi = (percent_of(ref_mean, x) for x in c['ci95'])
    out['ci95_percent'] = [lo, hi]
    m = spec.MARGIN_PERCENT
    inside = lo > -m and hi < m
    excl = lo > 0 or hi < 0
    if inside:
        out['statements_applying'].append('A_within_margin')
    if excl:
        out['statements_applying'].append('B_excludes_zero_positive' if lo > 0 else 'B_excludes_zero_negative')
    if not inside and not excl:
        out['statements_applying'].append('C_unresolved')
    texts = {'A_within_margin': f'the 95% interval lies wholly inside +-{m:g}% of the Hierarchy mean, so the authority rule makes no material operating difference in this model',
             'B_excludes_zero_positive': 'the 95% interval excludes zero: the net operating result is higher under the contribution-based rule',
             'B_excludes_zero_negative': 'the 95% interval excludes zero: the net operating result is lower under the contribution-based rule',
             'C_unresolved': 'the interval neither lies inside the margin nor excludes zero, so the question is not resolved (a non-significant difference is not equivalence)'}
    out['status'] = '+'.join(out['statements_applying'])
    if inside and excl:
        sign = 'positive' if lo > 0 else 'negative'
        out['text'] = (f'the sign is resolved ({sign}; the 95% interval excludes zero) and the size is inside the +-{m:g}% margin of the Hierarchy mean, '
                       'so the authority rule makes no material operating difference in this model')
    else:
        out['text'] = '; '.join(texts[s_] for s_ in out['statements_applying'])
    return out


def contrast2_rule(c):
    """P2 is positive if and only if its 95% interval lies above zero; point value and interval are reported."""
    out = {'contrast': 'P2', 'mean': None, 'ci95': None, 'positive': None, 'text': None}
    if c is None or c['mean'] is None:
        out['text'] = 'P2 not estimable'
        return out
    out['mean'], out['ci95'] = c['mean'], c['ci95']
    if c['ci95'] is None:
        out['text'] = f'P2 point value {c["mean"]:+.2f}; ' + (c['note'] or 'no interval') + ': cannot be called positive (positive means the 95% interval lies above zero)'
        return out
    out['positive'] = c['ci95'][0] > 0
    out['text'] = (f'P2 point value {c["mean"]:+.2f}, 95% interval [{c["ci95"][0]:+.2f}, {c["ci95"][1]:+.2f}]: '
                   + ('positive (the interval lies above zero)' if out['positive'] else
                      'not positive: no operating advantage of the design over an automated hierarchy is claimed'))
    return out


def half_width_factor(n):
    """t(0.975, n-1)/sqrt(n): the 95% half-width of an interval over n worlds per unit of SD; None for n < 2."""
    return t_quantile(.975, n-1)/math.sqrt(n) if n >= 2 else None


def done_phrase(sc):
    """'completed all seven arms' for the planned five-year run; for a shorter horizon (--horizon-years) 'reached the N-year horizon in all seven arms'."""
    h = sc.get('horizon_years', 5)
    return 'completed all seven arms' if h == 5 else f'reached the {spec.nw(h)}-year horizon in all seven arms'


def scope_check(results, worlds, horizon_years=5):
    """The scope block of collect.py plus the half-width factors for the accepted n, 7 and 10 worlds."""
    sc = dict(results.get('scope') or {})
    sc['horizon_years'] = horizon_years
    complete = sc.get('worlds_accepted_complete')
    if complete is None:   # results.json from an older collect.py: all arms of the sample cannot be told, use the worlds with any cell
        complete = worlds
        sc['scope_note'] = 'results.json carries no scope block (older collect.py); worlds with any accepted cell are counted as accepted'
        sc.setdefault('worlds_planned', None)
        sc.setdefault('worlds_started', None)
        sc.setdefault('worlds_stopped_before_completion', [])
        sc['worlds_accepted_complete'], sc['n_accepted_complete'] = complete, len(complete)
    n = len(complete)
    f_n, f7, f10 = half_width_factor(n), half_width_factor(7), half_width_factor(10)
    sc['half_width_factor'] = {'accepted_n': n, 'value': f_n, 'seven_worlds': f7, 'ten_worlds': f10,
                               'formula': 't(0.975, n-1)/sqrt(n); 95% half-width = factor x SD of the paired differences',
                               'width_relative_to_ten': None if f_n is None else f_n/f10,
                               'width_relative_to_seven': None if f_n is None else f_n/f7}
    planned, started, stopped = sc.get('worlds_planned'), sc.get('worlds_started'), sc.get('worlds_stopped_before_completion') or []
    if f_n is None:
        prec = f'with {n} accepted firm{"s" if n != 1 else ""} no interval can be computed'
    else:
        prec = (f'the 95% half-width is {f_n:.3f} SD for the {n} accepted firms, against {f7:.3f} for seven and {f10:.3f} for ten firms, '
                f'so a given spread of the paired differences gives intervals {f_n/f10:.2f} times as wide as ten firms would')
    sc['text'] = (f'{n} of {planned if planned is not None else "n/a"} planned firms {"completed all arms" if horizon_years == 5 else f"reached the {spec.nw(horizon_years)}-year horizon in all arms"}. The loss of precision: {prec}.')
    return sc


def small_positive(v, places=3):
    """A number with `places` decimals; a positive value that would round to zero is written '<0.001' (never as 0.000)."""
    return f'<{10**-places:.{places}f}' if 0 < v < 0.5*10**-places else f'{v:.{places}f}'


def small_prose(v, places=3):
    """Number for running text: a positive value that would round to zero is written 'below 0.001'."""
    return f'below {10**-places:.{places}f}' if 0 < v < 0.5*10**-places else f'{v:.{places}f}'


def unfunded_check(results):
    """Unfunded operating amount after adoption, per arm over the accepted worlds, and the sentence required when it is not zero."""
    tol = spec.UNFUNDED_TOLERANCE
    arms = {}
    for arm in spec.ARMS:
        rows = [(w, wd['arms'][arm]['unfunded']) for w, wd in sorted(results['worlds'].items(), key=lambda kv: int(kv[0])) if arm in wd['arms'] and 'unfunded' in wd['arms'][arm]]
        if not rows:
            continue
        post = [u['post_per_member_year'] for _, u in rows]
        whole = [u['whole_per_member_year'] for _, u in rows]
        shares = [u['share_unpaid_post'] for _, u in rows if u['share_unpaid_post'] is not None]
        paid = [u['processing_charge_paid_post_per_member_year'] for _, u in rows if u['processing_charge_paid_post_per_member_year'] is not None]
        nonzero = [w for w, u in rows if u['post_nonzero']]
        arms[arm] = {'n_worlds': len(rows), 'worlds_nonzero_post': len(nonzero), 'worlds_nonzero_post_list': nonzero,
                     'max_post_per_member_year': max(post), 'mean_post_per_member_year': statistics.fmean(post),
                     'mean_whole_per_member_year': statistics.fmean(whole), 'max_whole_per_member_year': max(whole),
                     'mean_before_adoption_units': statistics.fmean(u['before_adoption_units'] for _, u in rows),
                     'mean_charge_paid_post_per_member_year': statistics.fmean(paid) if paid else None,
                     'share_unpaid_mean': statistics.fmean(shares) if shares else None, 'share_unpaid_max': max(shares) if shares else None,
                     'worlds_with_share_defined': len(shares),
                     'mean_post_days_with_shortfall': statistics.fmean(u['post_days_with_shortfall'] for _, u in rows),
                     'all_zero': not nonzero, 'whole_run_all_zero': not any(u['whole_nonzero'] for _, u in rows),
                     'series_consistent': all(u['daily_sum_equals_cumulative'] and u['summary_equals_series'] is not False for _, u in rows)}
    bad = [a for a, d in arms.items() if not d['all_zero']]
    hist = [a for a, d in arms.items() if not d['whole_run_all_zero']]
    names = [spec.NAME[a] for a in bad]
    if bad:
        parts = [f'{spec.NAME[a]} ({spec.nw(arms[a]["worlds_nonzero_post"])} of {spec.nw(arms[a]["n_worlds"])} firm{"s" if arms[a]["n_worlds"] != 1 else ""}; '
                 f'mean {small_prose(arms[a]["mean_post_per_member_year"])}, maximum {small_prose(arms[a]["max_post_per_member_year"])} '
                 'resource units per member-year)' for a in bad]
        text = ('After adoption the unfunded operating amount is not zero in ' + '; '.join(parts) + f'. In {"this arm" if len(bad) == 1 else "these arms"} cash did not cover the daily charges in full, '
                'the unpaid part is recorded and no debt is carried, so the net operating result of '
                + (' and '.join(names) if len(names) < 3 else ', '.join(names[:-1]) + ' and ' + names[-1])
                + ' is not net of all processing cost. The unfunded amount also contains shortfalls of the other daily operating bills, so the unpaid processing charge is at most this amount.')
    else:
        text = ('The unfunded operating amount is zero after adoption in every accepted firm and arm: the processing charge, in the arms that pay one, was paid in full, '
                'so the net operating result of every arm is net of all processing cost.')
    if hist and not bad:
        text += ' The shared years before adoption carry a non-zero unfunded amount in ' + ', '.join(spec.NAME[a] for a in hist) + '.'
    return {'tolerance_resource_units': tol, 'basis': 'increase of operating_unfunded_resource_units_cumulative from the branch day on, per post member-year',
            'column_contains': spec.UNFUNDED_CONTENT, 'arms': arms, 'arms_nonzero_post': bad, 'arms_with_unfunded_in_history': hist,
            'all_zero': not bad, 'text': text}


# ---- outcome-dependent sentences (LaTeX-ready; one case for every possible outcome) ----------------------------------

BREAK_EVEN = (('P1', 'dams-isa-coord', 'control-hierarchy'), ('P2', 'dams-isa-coord', 'hierarchy-auto-coord'), ('S3', 'dams-isa-coord', 'hierarchy-isa-coord'))


def scope_sentence(sc):
    """Firms planned and firms that completed all seven arms with accepted records; execution history (started, stopped) and the reason are not stated here."""
    P, n = sc.get('worlds_planned'), sc['half_width_factor']['accepted_n']
    if P is not None and n == P:
        return f'All {spec.nw(P)} planned firms {done_phrase(sc)}.' if P != 1 else f'The one planned firm {done_phrase(sc)}.'
    head = (f'Of the {spec.nw(P)} planned firm{"" if P == 1 else "s"}, ' if P is not None else 'Of the firms of the study, ')
    who = spec.nw(n) if n > 0 else 'none'
    return head + f'{who} {done_phrase(sc)}.'


def precision_sentence(sc, contrasts):
    f = sc['half_width_factor']
    n, f_n, f10 = f['accepted_n'], f['value'], f['ten_worlds']
    if n >= 10:
        text = 'The intervals have the width provided for by the design of ten firms.'
    elif n >= 2:
        text = (f'With {spec.nw(n)} firms the 95\\% half-width of an interval is {f_n:.3f} times the standard deviation of the paired differences, '
                f'against {f10:.3f} with ten firms; the intervals are {round((f_n/f10-1)*100)}\\% wider than the design of ten firms would give.')
    elif n == 1:
        text = 'With one firm no interval can be computed, and the contrasts are point values.'
    else:
        text = f'No firm {done_phrase(sc)} with accepted records, so no contrast on all arms is estimated.'
    used = sorted({c['outcomes'][spec.PRIMARY_OUTCOME]['n'] for c in contrasts.values() if spec.PRIMARY_OUTCOME in c['outcomes']})
    if used and n >= 1 and used != [n]:
        rng = f'{spec.nw(used[0])}' if len(used) == 1 else f'between {spec.nw(used[0])} and {spec.nw(used[-1])}'
        text += f' Each contrast uses the firms in which both of its arms are accepted, {rng} firms.'
    return text


def level_order_sentence(arms):
    vals = [(a, arms[a][spec.PRIMARY_OUTCOME]['mean']) for a in spec.ARMS if a in arms and spec.PRIMARY_OUTCOME in arms[a]]
    if len(vals) < 2:
        return ''
    vals.sort(key=lambda t: -t[1])
    return 'From the highest to the lowest mean net operating result the arms are ' + spec.join_and([spec.NAME[a] for a, _ in vals]) + '.'


def p_two_sentence(c):
    pos = spec.interval_position(c)
    if pos == 'above':
        return 'The interval of P2 lies above zero: the complete design has an operating advantage over a hierarchy with the same automation in this model.'
    if pos == 'below':
        return 'The interval of P2 lies below zero, and no operating advantage of the design over an automated hierarchy is claimed.'
    if pos == 'contains':
        return 'The interval of P2 contains zero, and no operating advantage of the design over an automated hierarchy is claimed.'
    if pos == 'none':
        why = 'With one firm no interval can be computed' if c.get('n') == 1 else 'The paired differences do not vary, so no interval can be computed'
        return why + ', and no operating advantage of the design over an automated hierarchy is claimed.'
    return 'P2 is not estimated because no firm has both of its arms accepted, and no operating advantage of the design over an automated hierarchy is claimed.'


def p_three_sentence(rule3):
    """Margin and statement of contrast 3."""
    text = (rule3['text'] or 'n/a').replace('%', '\\%').replace('+-', '$\\pm$')
    if rule3['hierarchy_mean'] is None:
        return f'For P3, {text}.'
    return (f'The margin of {rule3["margin_percent_of_hierarchy_mean"]:g}\\% of the Hierarchy mean is '
            f'{spec.plain(rule3["margin_in_units"], 2)} units. For P3, {text}.')


def attribution_sentence(s4, s1):
    pos4, pos1 = spec.interval_position(s4), spec.interval_position(s1)
    if pos4 == 'above':
        text = ('The sandbox architecture raises the net operating result inside a hierarchy (S4); a gain that appears there is attributed to automation '
                'with scoped authority, which is available under any authority rule.')
    elif pos4 == 'below':
        text = 'The sandbox architecture lowers the net operating result inside a hierarchy (S4), and no gain is attributed to automation with scoped authority under that rule.'
    elif pos4 == 'contains':
        text = 'The interval of S4 contains zero: no gain of the sandbox architecture inside a hierarchy is shown, and none is attributed to automation with scoped authority.'
    elif pos4 == 'none':
        text = (('With one firm no interval can be computed' if s4.get('n') == 1 else 'The paired differences of S4 do not vary, so no interval can be computed')
                + ' and no gain is attributed to automation with scoped authority.')
    else:
        text = 'S4 is not estimated because no firm has both of its arms accepted, and no gain is attributed to automation with scoped authority.'
    if pos1 == 'above' and pos4 != 'above':
        text += ' The gain under the contribution-based rule and record (S1) has no counterpart inside a hierarchy.'
    return text


def break_even_sentences(contrasts):
    """Point-estimate statements on the processing rate at which the net operating result of the arm and its comparator coincide."""
    out = {}
    for cid, a_arm, c_arm in BREAK_EVEN:
        A, C = spec.NAME[a_arm], 'the ' + spec.NAME[c_arm]
        o = contrasts[cid]['outcomes']
        g, u = o.get(spec.PRIMARY_OUTCOME), o.get('processing_units_per_member_year_post')
        if not g or not u or g['mean'] is None or u['mean'] is None:
            out[cid] = ''
            continue
        G, U = g['mean'], u['mean']
        r_star = spec.PROCESSING_RATE + G/U if U != 0 else None
        if U == 0:
            t = f'{A} and {C} use the same processing, so the difference between them does not depend on the rate.'
        elif G == 0:
            t = f'{A} and {C} have the same mean net operating result at the declared rate.'
        elif G > 0 and U > 0:
            t = f'{A} has a higher mean net operating result than {C} at the declared rate; the difference vanishes at a processing rate of {spec.plain(r_star, 2)}.'
        elif G > 0 and U < 0:
            t = f'{A} has a higher mean net operating result than {C} and uses less processing, so the difference does not vanish at a higher rate.'
        elif G < 0 and U > 0:
            t = f'The mean net operating result of {A} is lower than that of {C} at the declared rate, and a higher rate lowers it further relative to {C}.'
        else:
            t = (f'The mean net operating result of {A} is lower than that of {C} at the declared rate; because {A} uses less processing, the difference narrows '
                 f'at a higher rate and vanishes at a processing rate of {spec.plain(r_star, 2)}.')
        out[cid] = t
    return out


def build_sentences(arms, contrasts, rule2, rule3, scope):
    prim = spec.PRIMARY_OUTCOME
    c = lambda cid: contrasts[cid]['outcomes'].get(prim)
    be = break_even_sentences(contrasts)
    return {'scope': scope_sentence(scope), 'precision': precision_sentence(scope, contrasts), 'level_order': level_order_sentence(arms),
            'p_two': p_two_sentence(c('P2')), 'p_three': p_three_sentence(rule3), 'attribution': attribution_sentence(c('S4'), c('S1')),
            'break_even_p1': be['P1'], 'break_even_p2': be['P2'], 'break_even_s3': be['S3'],
            'note': 'LaTeX-ready sentences (percent signs and minus signs already escaped); the unfunded and proposition sentences are built in make_tex.py'}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('results')
    ap.add_argument('--out', required=True)
    ap.add_argument('--horizon-years', type=int, default=5,
                    help='years after adoption covered by results.json (default 5, the planned run); a different value only changes the wording of the scope sentences and is checked against results.json')
    args = ap.parse_args()
    results = json.loads(Path(args.results).read_text())
    declared = results.get('horizon_years_after_adoption', 5)
    if declared != args.horizon_years:
        raise SystemExit(f'results.json covers {declared} years after adoption; pass --horizon-years {declared} (given {args.horizon_years})')
    table = world_values(results)
    worlds = sorted((w for w in results['worlds'] if results['worlds'][w]['arms']), key=int)
    excluded = results.get('excluded', [])

    arms = {}
    for arm in spec.ARMS:
        if arm not in table:
            continue
        arms[arm] = {}
        for name, per_world in sorted(table[arm].items()):
            ws = sorted(per_world, key=int)
            s = paired_summary([per_world[w] for w in ws])
            s['worlds'] = ws
            for k in ('positive_count', 'p_value', 'note'):
                s.pop(k)
            arms[arm][name] = s

    contrasts = {}
    for cid, minuend, subtrahend, kind, description in spec.CONTRASTS:
        entry = {'id': cid, 'kind': kind, 'minuend': minuend, 'subtrahend': subtrahend, 'description': description,
                 'adjustment': 'Holm over P1 to P4' if kind == 'primary' else 'unadjusted', 'outcomes': {}}
        if minuend not in table or subtrahend not in table:
            entry['worlds'] = []
            entry['worlds_left_out'] = [{'world': w, 'reason': 'arm not present'} for w in worlds]
            contrasts[cid] = entry
            continue
        used = [w for w in worlds if w in results['worlds'] and minuend in results['worlds'][w]['arms'] and subtrahend in results['worlds'][w]['arms']]
        entry['worlds'] = used
        left = []
        for w in worlds:
            if w in used:
                continue
            why = [e['reason'] for e in excluded if e['world'] == w and e['arm'] in (minuend, subtrahend)]
            left.append({'world': w, 'reason': '; '.join(why) or 'an arm is missing'})
        for e in excluded:
            if e['world'] not in worlds and e['arm'] in (minuend, subtrahend):
                left.append({'world': e['world'], 'reason': e['reason']})
        entry['worlds_left_out'] = left
        for name in spec.OUTCOMES:
            common = [w for w in used if w in table[minuend].get(name, {}) and w in table[subtrahend].get(name, {})]
            if not common:
                continue
            diffs = [table[minuend][name][w]-table[subtrahend][name][w] for w in common]
            res = paired_summary(diffs)
            res['differences'] = res.pop('values')
            res['worlds'] = common
            if name in (spec.PRIMARY_OUTCOME, 'net_operating_result_whole_per_member_year'):
                # percent of the HIERARCHY (control-hierarchy) mean over the worlds of the contrast: the unit of the margin and of the figures
                hier = [table.get(spec.REFERENCE, {}).get(name, {}).get(w) for w in common]
                hier_mean = statistics.fmean(hier) if (hier and None not in hier) else None
                res['hierarchy_mean'] = hier_mean
                res['percent_of_hierarchy_mean'] = {
                    'mean': percent_of(hier_mean, res['mean']),
                    'ci95': [percent_of(hier_mean, x) for x in res['ci95']] if (res['ci95'] and hier_mean) else None}
                res['subtrahend_mean'] = statistics.fmean(table[subtrahend][name][w] for w in common)
            entry['outcomes'][name] = res
        contrasts[cid] = entry

    # Holm over the four primary contrasts, on the primary outcome
    p_raw = {cid: contrasts[cid]['outcomes'].get(spec.PRIMARY_OUTCOME, {}).get('p_value') for cid in spec.PRIMARY_IDS}
    adjusted = holm(p_raw, spec.HOLM_FAMILY_SIZE)
    for cid in spec.PRIMARY_IDS:
        if spec.PRIMARY_OUTCOME in contrasts[cid]['outcomes']:
            contrasts[cid]['outcomes'][spec.PRIMARY_OUTCOME]['holm_p_primary_family'] = adjusted[cid]
    p3 = contrasts[spec.CONTRAST3]['outcomes'].get(spec.PRIMARY_OUTCOME)
    ref_mean = p3['hierarchy_mean'] if p3 else None
    rule3 = contrast3_rule(p3, ref_mean)
    rule2 = contrast2_rule(contrasts['P2']['outcomes'].get(spec.PRIMARY_OUTCOME))
    scope = scope_check(results, worlds, args.horizon_years)

    out = {'horizon_years_after_adoption': args.horizon_years, 'primary_outcome': spec.PRIMARY_OUTCOME, 'reference': spec.REFERENCE, 'n_worlds': len(worlds), 'worlds': worlds,
           'arms_in_results': sorted(arms), 'outcomes': results['outcomes'],
           'primary_contrasts': spec.PRIMARY_IDS, 'holm_family_size': spec.HOLM_FAMILY_SIZE,
           'holm_adjusted_p_primary_outcome': adjusted, 'contrast3_rule': rule3, 'contrast2_rule': rule2,
           'arms': arms, 'contrasts': contrasts, 'unfunded_check': unfunded_check(results), 'scope': scope,
           'sentences': build_sentences(arms, contrasts, rule2, rule3, scope),
           'not_computable': results.get('not_computable', {}),
           'note': ('ci95 is a Student-t interval over worlds; null when fewer than two worlds or zero SD. P1 to P4 carry Holm-adjusted p '
                    'for the primary outcome; every other p-value is unadjusted.')}
    Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True, allow_nan=False)+'\n')
    print(f'{len(worlds)} world(s), {len(arms)} arms, {len(contrasts)} contrasts -> {args.out}')
    for cid in spec.PRIMARY_IDS + [c[0] for c in spec.CONTRASTS if c[3] != 'primary']:
        r = contrasts[cid]['outcomes'].get(spec.PRIMARY_OUTCOME)
        if r:
            ci = 'n=1, no interval' if r['ci95'] is None else f'[{r["ci95"][0]:.2f}, {r["ci95"][1]:.2f}]'
            print(f'{cid} {contrasts[cid]["minuend"]} - {contrasts[cid]["subtrahend"]}: {r["mean"]:+.3f} {ci} (n={r["n"]})')
    print('contrast 3 rule:', rule3['status'], '-', rule3['text'])
    print('unfunded check: all_zero =', out['unfunded_check']['all_zero'], '-', out['unfunded_check']['text'])
    print('scope:', out['scope']['text'])


if __name__ == '__main__':
    main()
