"""Pool the 10,000-member main study from arms that passed the full validators on their complete journals.

Protocol Amendment 14 (addendum): the on-machine raw-evidence inference repeats the daily validator for every arm
serially and could not finish before the computing allocation ended. Each world's machine was released after its
four arm receipts (arm.json, written only after the longitudinal, organization and summary validators passed on the
complete journal) and the files they bind were saved. This tool checks, for every world and arm:
  - the receipt kind, arm name, source hash (one value across the study) and that every validator reads 'pass';
  - the SHA-256 of summary.json, timeseries.csv and final_state.json against the receipt;
  - that the parent prehistory receipt matches the saved prehistory.json;
then computes r11_outcomes per arm and the paired Student-t summaries with the Holm family of r11_pool.
Writes OUT/pooled.json (schema of r11_pool), OUT/roster.json and OUT/figure.json (schema of collect_study.py).
Usage: pool_validated_arms.py ROOT OUT   (ROOT holds w<id>/n10000-*/<arm>/...)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dams_sim.storage import file_digest  # noqa: E402
from research_tools.r11_outcomes import outcomes  # noqa: E402
from research_tools.r11_inference import _difference_summary, holm, t_quantile  # noqa: E402
from research_tools.r11_world_count import PRIMARY  # noqa: E402

REF = 'control-hierarchy'
DELTA = {k: v['delta'] for k, v in json.loads(Path(__file__).resolve().parents[2]/'docs'/'r11-tableA-pilot10k.json'.read_text())['outcomes'].items()}


def check_world(wdir):
    world = next(wdir.glob('n10000-*'))
    plan = json.loads((wdir/'plan.json').read_text())
    pre = world/'prehistory'/'prehistory.json'
    out, sources = {}, set()
    for arm in plan['arms']:
        d = world/arm
        r = json.loads((d/'arm.json').read_text())
        if r.get('kind') != 'r11-arm' or r.get('arm') != arm:
            raise SystemExit(f'{d}: receipt identity')
        bad = {k: v for k, v in r['validators'].items() if isinstance(v, str) and v != 'pass'}
        if bad:
            raise SystemExit(f'{d}: validators {bad}')
        for name, key in (('summary.json', 'summary_json'), ('timeseries.csv', 'timeseries_csv'), ('final_state.json', 'final_state_json')):
            if file_digest(d/name) != r['files'][key]:
                raise SystemExit(f'{d}/{name}: digest differs from receipt')
        if r['parent_prehistory_sha256'] != file_digest(pre):
            raise SystemExit(f'{d}: parent prehistory differs')
        sources.add(r['source_sha256'])
        out[arm] = outcomes(d)
    return plan, out, sources


def main(root, outdir):
    root, outdir = Path(root), Path(outdir)
    worlds, per, plans, sources = [], {}, [], set()
    for wdir in sorted(root.glob('w1*')):
        plan, out, src = check_world(wdir)
        w = int(wdir.name[1:]); worlds.append(w); per[w] = out; plans.append(plan); sources |= src
    if len(sources) != 1:
        raise SystemExit(f'more than one source hash: {sources}')
    ident = {k: v for k, v in plans[0].items() if k not in ('worlds', 'jobs', 'executable')}
    for p in plans[1:]:
        if {k: v for k, v in p.items() if k not in ('worlds', 'jobs', 'executable')} != ident:
            raise SystemExit('worlds belong to different study identities')
    arms = [a for a in plans[0]['arms'] if a != REF]
    pooled = {'schema': 'r11-pooled-validated-arms-v1', 'source_sha256': sources.pop(), 'reference': REF,
              'worlds': worlds, 'admission': 'arm receipts with all validators pass on complete journals; '
              'raw-evidence inference re-verification not repeated (Amendment 14 addendum)', 'comparisons': {}}
    for arm in arms:
        rows = {}
        for key in per[worlds[0]][arm]:
            vals = [(w, per[w][arm][key], per[w][REF][key]) for w in worlds
                    if isinstance(per[w][arm].get(key), (int, float)) and isinstance(per[w][REF].get(key), (int, float))
                    and not isinstance(per[w][arm].get(key), bool)]
            if not vals:
                continue
            s = _difference_summary([a-b for _, a, b in vals], key, DELTA)
            s.update(assigned_worlds=len(worlds), missing_worlds=[w for w in worlds if w not in {v[0] for v in vals}],
                     world_values=vals)
            rows[key] = s
        fam = [k for k in PRIMARY if k in rows]
        for k, adj in zip(fam, holm([rows[k].get('p_two_sided') if rows[k].get('p_two_sided') is not None else 1. for k in fam])):
            rows[k]['p_holm'] = adj if rows[k].get('p_two_sided') is not None else None
            rows[k]['holm_family_size'] = len(fam)
        pooled['comparisons'][arm] = rows
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir/'pooled.json').write_text(json.dumps(pooled, indent=1, allow_nan=False))
    (outdir/'roster.json').write_text(json.dumps({'study': 'main-n10000', 'assigned': list(range(1000, 1010)),
                                                  'finished': worlds, 'missing': [w for w in range(1000, 1010) if w not in worlds]}, indent=1))
    fig = {'study': 'main-n10000', 'reference': REF, 'K': len(PRIMARY), 'arms': {}}
    for arm, rows in pooled['comparisons'].items():
        fig['arms'][arm] = {}
        for k in PRIMARY:
            r = rows.get(k)
            if r is None:
                continue
            n = len(r['world_values'])
            row = {'unit': r['unit'], 'worlds': n, 'differences': [[w, a-b] for w, a, b in r['world_values']],
                   'mean': r.get('mean_difference'), 'ci95': r.get('ci95'), 'p_holm': r.get('p_holm'), 'delta': DELTA.get(k)}
            if r.get('sd_difference') and n > 1:
                h = t_quantile(1-0.05/(2*len(PRIMARY)), n-1)*r['sd_difference']/n**.5
                row['ci_bonferroni'] = [r['mean_difference']-h, r['mean_difference']+h]
            fig['arms'][arm][k] = row
    (outdir/'figure.json').write_text(json.dumps(fig, indent=1, allow_nan=False))
    for arm, rows in pooled['comparisons'].items():
        for k in PRIMARY:
            r = rows[k]
            print(arm, k, f"{r['mean_difference']:.4g}", [f'{x:.4g}' for x in (r['ci95'] or [])], 'holm', f"{r.get('p_holm'):.2g}" if r.get('p_holm') is not None else None)


if __name__ == '__main__':
    main(*sys.argv[1:3])
