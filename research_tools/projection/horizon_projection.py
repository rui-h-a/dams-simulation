"""Held-out-period check of horizon projections on the complete 300-member ten-year worlds.

For each paired world: observed 10-year contrast of an endpoint (ratio of sums over post-adoption years 1-10) and
projections that use years 1-5 only. Projection rules (fixed before looking at years 6-10):
  P0 persistence     : the five-year contrast.
  P1 steady state    : year 1 as observed, years 6-10 at the mean contrast of years 2-5.
  P2 linear trend    : years 6-10 from a least-squares line through years 2-5.
Years are weighted by member-years (or available hours) of each arm. Writes OUT/horizon-projection.json.
"""
import json, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from yearly import yearly

ENDPOINTS = {'net_per_member_year': 'member_years', 'regret_per_hour': 'hours', 'output_per_hour': 'hours',
             'review_governance_hours_per_member_year': 'member_years'}


def ratio(series, key, years):
    w = 'member_years' if ENDPOINTS[key] == 'member_years' else None
    num = den = 0.
    for r in series:
        if r['year'] in years:
            weight = r['member_years'] if w else r['hours']
            num += r[key]*weight; den += weight
    return num/den


def fit_line(xs, ys):
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    b = sum((x-mx)*(y-my) for x, y in zip(xs, ys))/sum((x-mx)**2 for x in xs)
    return my-b*mx, b


def project(h, d, key, rule):
    """10-year contrast from years 1-5 under a rule: per-arm yearly values for 6-10, member weights from year 5."""
    out = {}
    for name, s in (('h', h), ('d', d)):
        obs = {r['year']: r for r in s if r['year'] <= 5}
        vals = {y: obs[y][key] for y in obs}
        if rule == 'P0':
            out[name] = ratio(s, key, range(1, 6)); continue
        if rule == 'P1':
            fut = {y: statistics.fmean(vals[k] for k in (2, 3, 4, 5)) for y in range(6, 11)}
        else:
            a, b = fit_line([2, 3, 4, 5], [vals[k] for k in (2, 3, 4, 5)])
            fut = {y: a+b*y for y in range(6, 11)}
        wkey = 'member_years' if ENDPOINTS[key] == 'member_years' else 'hours'
        w5 = obs[5][wkey]
        num = sum(obs[y][key]*obs[y][wkey] for y in obs)+sum(fut[y]*w5 for y in fut)
        den = sum(obs[y][wkey] for y in obs)+5*w5
        out[name] = num/den
    return out['d']-out['h']


def main(root, worlds, out):
    res = {'worlds': worlds, 'rules': ['P0', 'P1', 'P2'], 'endpoints': {}}
    for key in ENDPOINTS:
        rows = []
        for w in worlds:
            dd = Path(root)/f'n300-traditional-organic-pre2-post10-w{w}'
            h, d = yearly(dd/'control-hierarchy/timeseries.csv'), yearly(dd/'dams-witness/timeseries.csv')
            for s in (h, d):
                for r in s:
                    r['hours'] = r['member_years']  # replaced below
            # available hours per year
            for s, arm in ((h, 'control-hierarchy'), (d, 'dams-witness')):
                import csv
                rws = list(csv.DictReader(open(dd/arm/'timeseries.csv')))
                for r in s:
                    ia, ib = 730+round(365.25*(r['year']-1))-1, min(len(rws), 730+round(365.25*r['year']))-1
                    r['hours'] = float(rws[ib]['available_member_work_hours_cumulative'])-float(rws[ia]['available_member_work_hours_cumulative'])
            observed10 = ratio(d, key, range(1, 11))-ratio(h, key, range(1, 11))
            observed10_excl_tail = ratio(d, key, range(1, 10))-ratio(h, key, range(1, 10))
            row = {'world': w, 'observed_10y': observed10, 'observed_years_1_9': observed10_excl_tail,
                   'yearly_contrast': [rd[key]-rh[key] for rh, rd in zip(h, d)]}
            for rule in res['rules']:
                row[rule] = project(h, d, key, rule)
            rows.append(row)
        summ = {}
        for rule in res['rules']:
            e = [r[rule]-r['observed_10y'] for r in rows]
            summ[rule] = {'mean_error': statistics.fmean(e), 'rmse': (statistics.fmean(x*x for x in e))**.5,
                          'max_abs_error': max(abs(x) for x in e)}
        obs = [r['observed_10y'] for r in rows]
        res['endpoints'][key] = {'worlds': rows, 'errors': summ, 'observed_mean': statistics.fmean(obs),
                                 'observed_sd': statistics.stdev(obs)}
    Path(out).write_text(json.dumps(res, indent=1, allow_nan=False))
    for k, v in res['endpoints'].items():
        print(k, 'obs mean', f"{v['observed_mean']:.4g}", 'sd', f"{v['observed_sd']:.3g}",
              {r: {kk: float(f'{vv:.3g}') for kk, vv in e.items()} for r, e in v['errors'].items()})


if __name__ == '__main__':
    main(sys.argv[1], [int(x) for x in sys.argv[2].split(',')], sys.argv[3])
