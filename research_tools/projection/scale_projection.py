"""Leave-one-size-out check of the size-invariant projection rule on the exploratory scale study (30-1,000 members).

Rule fixed in advance: the paired DAMS-minus-hierarchy contrast at an unobserved size equals the inverse-variance
weighted mean of the observed sizes; its prediction interval adds the between-size standard deviation tau
(DerSimonian-Laird) to the sampling error. Also fits the weighted slope in log10(N). Writes OUT json.
"""
import json, math, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_tools.r11_inference import t_quantile

KEYS = ['net_operating_result_per_member_year', 'decision_regret_per_available_member_hour',
        'governance_member_hours_per_member_year', 'output_per_available_member_hour']


def se_from(ci, worlds):
    return (ci[2]-ci[1])/2/t_quantile(.975, worlds-1)


def pool(points):
    w = [1/p['se']**2 for p in points]
    m = sum(wi*p['est'] for wi, p in zip(w, points))/sum(w)
    q = sum(wi*(p['est']-m)**2 for wi, p in zip(w, points))
    c = sum(w)-sum(wi*wi for wi in w)/sum(w)
    tau2 = max(0., (q-(len(points)-1))/c) if c > 0 else 0.
    wr = [1/(p['se']**2+tau2) for p in points]
    mr = sum(wi*p['est'] for wi, p in zip(wr, points))/sum(wr)
    return mr, (1/sum(wr))**.5, tau2**.5, q


def slope(points):
    xs = [math.log10(p['N']) for p in points]; w = [1/p['se']**2 for p in points]
    mx = sum(wi*x for wi, x in zip(w, xs))/sum(w); my = sum(wi*p['est'] for wi, p in zip(w, points))/sum(w)
    sxx = sum(wi*(x-mx)**2 for wi, x in zip(w, xs))
    b = sum(wi*(x-mx)*(p['est']-my) for wi, x, p in zip(w, xs, points))/sxx
    resid = [p['est']-(my+b*(x-mx)) for x, p in zip(xs, points)]
    s2 = sum(wi*r*r for wi, r in zip(w, resid))/(len(points)-2)
    return b, (max(s2, 1.)/sxx)**.5


def main(src, out, variant='baseline'):
    rows = [r for r in json.loads(Path(src).read_text()) if r['variant'] == variant]
    res = {'variant': variant, 'sizes': sorted(r['N'] for r in rows), 'endpoints': {}}
    for k in KEYS:
        pts = [{'N': r['N'], 'est': r[k]['diff_ci'][0], 'se': se_from(r[k]['diff_ci'], r['worlds'])} for r in rows]
        pts.sort(key=lambda p: p['N'])
        m, se, tau, q = pool(pts)
        b, sb = slope(pts)
        loo = []
        for i, p in enumerate(pts):
            mo, seo, tauo, _ = pool(pts[:i]+pts[i+1:])
            half = 1.96*math.sqrt(seo**2+tauo**2+p['se']**2)
            loo.append({'N': p['N'], 'observed': p['est'], 'predicted': mo, 'error': mo-p['est'],
                        'interval_half_width': half, 'covered': abs(mo-p['est']) <= half})
        res['endpoints'][k] = {'points': pts, 'pooled': m, 'pooled_se': se, 'tau': tau, 'Q': q,
                               'slope_per_decade': b, 'slope_se': sb, 'leave_one_out': loo,
                               'loo_rmse': statistics.fmean(x['error']**2 for x in loo)**.5,
                               'loo_coverage': sum(x['covered'] for x in loo)/len(loo)}
        e = res['endpoints'][k]
        print(k, f"pooled {m:.4g} se {se:.3g} tau {tau:.3g} slope/decade {b:.3g}±{sb:.3g} loo rmse {e['loo_rmse']:.3g} cover {e['loo_coverage']:.2f}")
    Path(out).write_text(json.dumps(res, indent=1, allow_nan=False))


if __name__ == '__main__':
    main(*sys.argv[1:])
