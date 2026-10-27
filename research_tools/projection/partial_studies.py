"""Outcomes of the complete, validated arms of the 300-member r11-7 studies (partial rosters).

For every arm with arm.json: check the summary/timeseries bytes against the receipt, compute r11_outcomes,
and pair each DAMS arm with the hierarchy arm of the same world. Writes OUT/partial-studies.json.
Usage: partial_studies.py ROOT OUT
"""
import json, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dams_sim.storage import file_digest
from research_tools.r11_outcomes import outcomes
from research_tools.r11_inference import t_quantile

KEYS = ['net_operating_result_per_member_year', 'confirmation_mean_days', 'decision_regret_per_available_member_hour',
        'output_per_available_member_hour', 'governance_member_hours_per_member_year', 'final_active_members', 'closed']


def arm_result(d):
    r = json.loads((d/'arm.json').read_text())
    for name, key in (('summary.json', 'summary_json'), ('timeseries.csv', 'timeseries_csv')):
        if file_digest(d/name) != r['files'][key]:
            raise SystemExit(f'digest mismatch {d}/{name}')
    return outcomes(d), r


def main(root, out):
    root, res = Path(root), {}
    for study in ('tenyear', 'nogrowth', 'timing', 'process'):
        plan = json.loads((root/study/'plan.json').read_text())
        worlds, arms = plan['worlds'], plan['arms']
        per = {}
        for wdir in sorted((root/study).glob('n300-*-w*')):
            w = int(wdir.name.rsplit('-w', 1)[1])
            for a in arms:
                if (wdir/a/'arm.json').exists():
                    per.setdefault(w, {})[a] = arm_result(wdir/a)[0]
        comp = {}
        for a in arms[1:]:
            pairs = sorted(w for w in per if 'control-hierarchy' in per[w] and a in per[w])
            rows = {}
            for k in KEYS:
                d = [per[w][a][k]-per[w]['control-hierarchy'][k] for w in pairs
                     if per[w][a].get(k) is not None and per[w]['control-hierarchy'].get(k) is not None]
                row = {'n': len(d), 'differences': d}
                if len(d) > 1:
                    m, s = statistics.fmean(d), statistics.stdev(d); h = t_quantile(.975, len(d)-1)*s/len(d)**.5
                    row.update(mean=m, sd=s, ci95=[m-h, m+h],
                               reference_mean=statistics.fmean(per[w]['control-hierarchy'][k] for w in pairs))
                rows[k] = row
            comp[a] = {'paired_worlds': pairs, 'outcomes': rows}
        res[study] = {'context': plan['context'], 'growth': plan['growth'], 'post_years': plan['post_years'],
                      'assigned_worlds': len(worlds), 'arms': arms,
                      'complete_arms': {a: sum(a in v for v in per.values()) for a in arms}, 'comparisons': comp}
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out)/'partial-studies.json').write_text(json.dumps(res, indent=1, allow_nan=False))
    for s, v in res.items():
        print(s, v['complete_arms'])
        for a, c in v['comparisons'].items():
            o = c['outcomes']['net_operating_result_per_member_year']; q = c['outcomes']['confirmation_mean_days']
            print('  ', a, 'pairs', len(c['paired_worlds']), 'net', round(o.get('mean', float('nan')), 2), [round(x, 2) for x in o.get('ci95', [])],
                  'ref', round(o.get('reference_mean', float('nan')), 1), '| confirm days', round(q.get('mean', float('nan')), 1), [round(x, 1) for x in q.get('ci95', [])])


if __name__ == '__main__':
    main(*sys.argv[1:3])
