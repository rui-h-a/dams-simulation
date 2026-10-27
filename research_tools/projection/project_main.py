"""Projections of the 10,000-member main study beyond its executed horizon and population.

Inputs: main-study world directories (summary.json/timeseries.csv per arm), the held-out-period check of the
300-member ten-year worlds (horizon-projection.json) and the leave-one-size-out check of the scale study
(scale-projection.json). Rules (P1 for horizon; size-invariant contrast with log-size sensitivity) are those selected
by the checks; nothing is refitted on the main study. Writes OUT/projections.json.
Usage: project_main.py MAIN_ROOT HORIZON_JSON SCALE_JSON OUT
"""
import json, math, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from yearly import yearly
from horizon_projection import project, ratio
from research_tools.r11_outcomes import outcomes
from research_tools.r11_inference import t_quantile
import csv

ARMS = ['control-process', 'dams-witness', 'linear-witness']
HKEYS = {'net_per_member_year': 'net_operating_result_per_member_year',
         'output_per_hour': 'output_per_available_member_hour',
         'review_governance_hours_per_member_year': 'governance_member_hours_per_member_year'}


def with_hours(series, path, pre=2):
    rws = list(csv.DictReader(open(path))); start = round(365.25*pre)
    for r in series:
        ia, ib = start+round(365.25*(r['year']-1))-1, min(len(rws), start+round(365.25*r['year']))-1
        r['hours'] = float(rws[ib]['available_member_work_hours_cumulative'])-float(rws[ia]['available_member_work_hours_cumulative'])
    return series


def ci(d):
    m, s = statistics.fmean(d), statistics.stdev(d); h = t_quantile(.975, len(d)-1)*s/len(d)**.5
    return m, s, [m-h, m+h], s/len(d)**.5


def main(root, hjson, sjson, out):
    H = json.loads(Path(hjson).read_text())['endpoints']; S = json.loads(Path(sjson).read_text())['endpoints']
    worlds = sorted(Path(root).glob('w*/n10000-*'))
    ser = {}
    for wd in worlds:
        ser[wd] = {a: with_hours(yearly(wd/a/'timeseries.csv'), wd/a/'timeseries.csv') for a in ['control-hierarchy']+ARMS}
    res = {'worlds': [w.parent.name for w in worlds], 'horizon': {}, 'scale': {}, 'yearly': {}}
    for a in ARMS:
        res['horizon'][a] = {}; res['yearly'][a] = {}
        for k, okey in HKEYS.items():
            five = [ratio(ser[w][a], k, range(1, 6))-ratio(ser[w]['control-hierarchy'], k, range(1, 6)) for w in ser]
            proj = [project(ser[w]['control-hierarchy'], ser[w][a], k, 'P1') for w in ser]
            m5 = ci(five); mp = ci(proj)
            val = H[k]['errors']['P1']
            allowance = val['rmse']
            half = math.sqrt((mp[2][1]-mp[0])**2+(1.96*allowance)**2)
            res['horizon'][a][k] = {'five_year': {'mean': m5[0], 'ci95': m5[2]}, 'ten_year_projection': {
                'mean': mp[0], 'sampling_ci95': mp[2], 'validation_rmse_300': allowance,
                'validation_mean_error_300': val['mean_error'], 'interval': [mp[0]-half, mp[0]+half]}}
            yc = [[r2[k]-r1[k] for r1, r2 in zip(ser[w]['control-hierarchy'], ser[w][a])] for w in ser]
            res['yearly'][a][k] = [ci([y[i] for y in yc]) [0:3:2] for i in range(len(yc[0]))]
    # population projection of the five-year contrast
    per = {w: {a: outcomes(w/a) for a in ['control-hierarchy']+ARMS} for w in worlds}
    for a in ARMS:
        res['scale'][a] = {}
        for okey in ['net_operating_result_per_member_year', 'output_per_available_member_hour',
                     'governance_member_hours_per_member_year', 'decision_regret_per_available_member_hour']:
            d = [per[w][a][okey]-per[w]['control-hierarchy'][okey] for w in per]
            m, s, c, se = ci(d)
            sp = S[okey]; rows = {}
            for N in (50000, 100000):
                dec = math.log10(N/10000)
                shift = sp['slope_per_decade']*dec
                half = 1.96*math.sqrt(se**2+sp['tau']**2+(sp['slope_se']*dec)**2+shift**2)
                rows[str(N)] = {'invariant_projection': m, 'log_trend_projection': m+shift,
                                'interval': [m-half, m+half], 'decades_beyond_10000': dec}
            res['scale'][a][okey] = {'observed_10000': {'mean': m, 'ci95': c}, 'projection': rows,
                                     'scale_check': {'tau': sp['tau'], 'slope_per_decade': sp['slope_per_decade'],
                                                     'slope_se': sp['slope_se'], 'loo_rmse': sp['loo_rmse'],
                                                     'loo_coverage': sp['loo_coverage']}}
    res['confirmation'] = {}
    for a in ARMS:
        d = [per[w][a]['confirmation_mean_days']-per[w]['control-hierarchy']['confirmation_mean_days'] for w in per]
        m, sd, c, se = ci(d)
        res['confirmation'][a] = {'mean': m, 'ci95': c, 'reference': statistics.fmean(per[w]['control-hierarchy']['confirmation_mean_days'] for w in per)}
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out)/'projections.json').write_text(json.dumps(res, indent=1, allow_nan=False))
    for a in ARMS:
        h = res['horizon'][a]['net_per_member_year']; s5 = res['scale'][a]['net_operating_result_per_member_year']
        print(a, 'net 5y', round(h['five_year']['mean'], 2), '10y proj', round(h['ten_year_projection']['mean'], 2),
              [round(x, 1) for x in h['ten_year_projection']['interval']],
              '| 50k', [round(x, 1) for x in s5['projection']['50000']['interval']], '100k', [round(x, 1) for x in s5['projection']['100000']['interval']])
        print('   yearly net contrast', [round(x[0], 1) for x in res['yearly'][a]['net_per_member_year']])


if __name__ == '__main__':
    main(*sys.argv[1:5])
