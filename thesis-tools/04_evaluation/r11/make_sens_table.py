"""Table of the exploratory behavioral sensitivity batches (founding startup, 300 members, ten paired worlds, one year).
Usage: python 04_evaluation/r11/make_sens_table.py  (writes 05_thesis/generated/r11_table_sensitivity.tex)"""
import json
from pathlib import Path

R = Path(__file__).resolve().parent
OUT = R.parent.parent/'05_thesis'/'generated'/'r11_table_sensitivity.tex'
LABEL = {'baseline': 'Default design', 'reversed-share-response': 'Share response $-0.25$',
         'share-response-0': 'Share response 0', 'share-response-0.5': 'Share response 0.5',
         'skill-dispersion-low': 'Skill dispersion 19\\,\\% of mean', 'skill-dispersion-high': 'Skill dispersion 48\\,\\% of mean',
         'status-bias': 'Status-conditioned review', 'tenure-quit-profile': 'Tenure-dependent exit'}
rows = json.loads((R/'sensitivity-share-response.json').read_text())
rows += [r for r in json.loads((R/'sensitivity-engine.json').read_text()) if r['variant'] != 'baseline']
assert all(r['admitted'] and len(r['worlds']) == 10 for r in rows)


def cell(x, scale=1, fmt='{:.1f}'):
    m, (lo, hi) = x['mean']*scale, (v*scale for v in x['ci95'])
    f = lambda v: fmt.format(v).replace('-', '$-$')
    return f'{f(m)} [{f(lo)}, {f(hi)}]'


lines = [r'\begin{table}[htbp]', r'\centering', r'\footnotesize', r'\setlength{\tabcolsep}{3pt}',
         r'\caption{Exploratory behavioral sensitivity of the founding contrast: DAMS on the witnessed record minus the kept performance-tier hierarchy, 300 initial members, one year after founding adoption, ten paired worlds (3000--3009) per row, paired Student-$t$ 95\% intervals. The default share response is 0.25. All batches passed the raw-evidence admission; synthetic, uncalibrated model.}',
         r'\label{tab:r11-sensitivity}', r'\begin{tabular}{@{}lrrr@{}}', r'\toprule',
         r'Variant & Net operating result & Decision regret & Governance time \\',
         r' & (units per member-year) & ($10^{-6}$ per member-hour) & (hours per member-year) \\', r'\midrule']
for r in rows:
    lines.append(f"{LABEL[r['variant']]} & {cell(r['net_operating_result_per_member_year'])} & "
                 f"{cell(r['decision_regret_per_available_member_hour'], 1e6)} & {cell(r['governance_member_hours_per_member_year'], 1, '{:.2f}')} \\\\")
lines += [r'\bottomrule', r'\end{tabular}', r'\end{table}', '']
OUT.write_text('\n'.join(lines))
print('written', OUT)
