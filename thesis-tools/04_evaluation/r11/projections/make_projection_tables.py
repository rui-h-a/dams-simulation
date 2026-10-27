"""Tables of the partial-roster 300-member studies and of the projections beyond the executed horizon and population.

Inputs (this directory): partial-studies.json, horizon-projection.json, scale-projection.json, projections.json,
and ../traditional/inference.json (five-year traditional study, 65 worlds) for the confirmation-delay ratio.
Usage: python 04_evaluation/r11/projections/make_projection_tables.py
Writes 05_thesis/generated/r11_table_partial.tex and r11_table_projections.tex.
"""
import json
from pathlib import Path

R = Path(__file__).resolve().parent
GEN = R.parents[2]/'05_thesis'/'generated'
J = lambda name: json.loads((R/name).read_text())


def num(v, fmt='{:.1f}'):
    return fmt.format(v).replace('-', '$-$')


def cell(m, ci, fmt='{:.1f}', scale=1):
    return f'{num(m*scale, fmt)} [{num(ci[0]*scale, fmt)}, {num(ci[1]*scale, fmt)}]'


def partial_table():
    p = J('partial-studies.json')
    rows = [('tenyear', 'dams-witness', 'Ten-year (2 + 10 y)', 'DAMS, witnessed'),
            ('nogrowth', 'dams-witness', 'No growth (2 + 5 y)', 'DAMS, witnessed'),
            ('timing', 'dams-staged-witness', 'Timing (2 + 5 y)', 'DAMS, staged'),
            ('timing', 'dams-triggered-witness', '', 'DAMS, triggered'),
            ('process', 'dams-central', 'Record (2 + 5 y)', 'DAMS, central'),
            ('process', 'dams-consensus', '', 'DAMS, consensus')]
    lines = [r'\begin{table}[htbp]', r'\centering', r'\footnotesize', r'\setlength{\tabcolsep}{2.5pt}',
             r'\caption{Partial rosters of four 300-member comparisons in a traditional firm after two years of history (organic growth unless stated; staged adoption proceeds guild by guild, triggered adoption starts when a backlog threshold is crossed; central and consensus name the record substrate in place of the witnessed record). Each changed arm minus the kept performance-tier hierarchy in the same world; mean paired difference with Student-$t$ 95\% interval over the complete pairs. Each study assigned 65 worlds before running; the pairs shown are those whose arms completed and passed the production validators before the studies were stopped. They are descriptive and are not the confirmatory rosters; the last column is the hierarchy mean of net operating result over the same worlds. Net operating result covers the whole run, including the two shared years before adoption; synthetic, uncalibrated model.}',
             r'\label{tab:r11-partial}', r'\begin{tabular}{@{}llrrrr@{}}', r'\toprule',
             r'Comparison & Changed arm & Pairs & Net operating result & Confirmation delay & Ref. \\',
             r' & & of 65 & (units per member-year) & (days) & \\', r'\midrule']
    for study, arm, label, armlab in rows:
        c = p[study]['comparisons'][arm]
        o, q = c['outcomes']['net_operating_result_per_member_year'], c['outcomes']['confirmation_mean_days']
        lines.append(f"{label} & {armlab} & {len(c['paired_worlds'])} & {cell(o['mean'], o['ci95'])} & "
                     f"{cell(q['mean'], q['ci95'])} & {o['reference_mean']:.0f} \\\\")
    lines += [r'\bottomrule', r'\end{tabular}', r'\end{table}', '']
    (GEN/'r11_table_partial.tex').write_text('\n'.join(lines))


def projection_table():
    h, s, pr = J('horizon-projection.json'), J('scale-projection.json'), J('projections.json')
    p = J('partial-studies.json')
    trad = json.loads((R.parent/'traditional'/'inference.json').read_text())['comparisons']['dams-witness']['confirmation_mean_days']
    ten = p['tenyear']['comparisons']['dams-witness']['outcomes']['confirmation_mean_days']
    t5 = trad['mean_difference']
    ratio, rlo, rhi = ten['mean']/t5, ten['ci95'][0]/t5, ten['ci95'][1]/t5
    hv = h['endpoints']['net_per_member_year']['errors']['P1']
    sv = s['endpoints']['net_operating_result_per_member_year']
    W = r'p{14.6cm}'
    head = lambda text: rf'\multicolumn{{3}}{{@{{}}{W}@{{}}}}{{{text}}} \\'
    lines = [r'\begin{table}[htbp]', r'\centering', r'\footnotesize', r'\setlength{\tabcolsep}{4pt}',
             r'\caption{Projections of the 10,000-member main study beyond its executed horizon and population: each changed arm minus the kept performance-tier hierarchy, multinational firm with organic growth. Each block names the projection rule and the check of that rule on executed runs. Intervals combine the sampling error of the ten main-study worlds with the error of the rule in its check; they are projection intervals, not confidence intervals of simulated outcomes. Synthetic, uncalibrated model.}',
             r'\label{tab:r11-projections}', r'\begin{tabular}{@{}lrr@{}}', r'\toprule',
             r'Changed arm & Observed & Projected [interval] \\', r'\midrule']
    lines.append(head(r'\emph{Ten-year net operating result after adoption} (units per member-year after adoption, excluding the two shared years of history). Rule: years 6--10 continue at the mean contrast of years 2--5. Check on the five complete 300-member ten-year worlds, using years 1--5 only: root mean square error '
                      + num(hv['rmse']) + ', mean error ' + num(hv['mean_error']) + r' units; carrying the five-year value forward instead erred by '
                      + num(h['endpoints']['net_per_member_year']['errors']['P0']['mean_error']) + ' units on average and a linear trend by '
                      + num(h['endpoints']['net_per_member_year']['errors']['P2']['rmse']) + ' (root mean square).'))
    for a, lab in (('dams-witness', 'DAMS, witnessed'), ('linear-witness', 'Linear, witnessed'), ('control-process', 'Process change')):
        x = pr['horizon'][a]['net_per_member_year']; t = x['ten_year_projection']
        lines.append(f"\\quad {lab} & {num(x['five_year']['mean'])} (five years) & {num(t['mean'])} [{num(t['interval'][0])}, {num(t['interval'][1])}] \\\\")
    lines.append(r'\addlinespace[4pt]')
    c5 = pr['confirmation']['dams-witness']
    lines.append(head(r'\emph{Ten-year mean confirmation delay} (days). Rule: the backlog drifts linearly in time (Proposition~1 of Appendix~\ref{app:scale-limits}), and the ratio of the ten-year to the five-year contrast in the 300-member traditional firm, '
                      + num(ratio, '{:.2f}') + ' (interval ' + num(rlo, '{:.2f}') + '--' + num(rhi, '{:.2f}') + r'), is applied. Check: a line through the backlog per member of years 1--5 predicted year 9 within 4\% in all ten arms of those worlds.'))
    lines.append(f"\\quad DAMS, witnessed & {num(c5['mean'])} (five years) & {num(c5['mean']*ratio)} [{num(c5['mean']*rlo)}, {num(c5['mean']*rhi)}] \\\\")
    lines.append(r'\addlinespace[4pt]')
    lines.append(head(r'\emph{Five-year net operating result at 50,000 and 100,000 members} (units per member-year, whole run). Rule: the contrast does not depend on size, as Propositions~1 to~5 of Appendix~\ref{app:scale-limits} imply for per-member review capacity; the 10,000-member value is carried to the larger sizes. Check on the first year after founding at six sizes from 30 to 1,000 members, each predicted from the other five: root mean square error '
                      + num(sv['loo_rmse']) + f", {round(sv['loo_coverage']*len(sv['leave_one_out']))} of {len(sv['leave_one_out'])} sizes inside the interval; fitted slope "
                      + num(sv['slope_per_decade']) + ' (standard error ' + num(sv['slope_se']) + r') per tenfold size, which the interval includes.'))
    x = pr['scale']['dams-witness']['net_operating_result_per_member_year']
    for N in ('50000', '100000'):
        r = x['projection'][N]
        lines.append(f"\\quad DAMS, witnessed, {int(N):,} members & {num(x['observed_10000']['mean'])} (10,000) & {num(r['invariant_projection'])} [{num(r['interval'][0])}, {num(r['interval'][1])}] \\\\")
    lines += [r'\bottomrule', r'\end{tabular}', r'\end{table}', '']
    (GEN/'r11_table_projections.tex').write_text('\n'.join(lines))

if __name__ == '__main__':
    partial_table()
    projection_table()
    print('written', GEN/'r11_table_partial.tex', GEN/'r11_table_projections.tex')
