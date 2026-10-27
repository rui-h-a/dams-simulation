"""Generate thesis tables for the R11.3 enterprise studies from inference.json.

Usage: python 04_evaluation/r11/make_tables.py  (writes 05_thesis/generated/r11_*.tex)
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent.parent/'05_thesis'/'generated'
ARMS = [('control-process', 'Process change, hierarchy kept'), ('dams-witness', 'DAMS, sublinear, witnessed'),
        ('linear-witness', 'Linear allocation, witnessed')]
ROWS = [('net_operating_result_per_member_year', 'Net operating result', 'units/member-year', 2, 1),
        ('governance_cost_per_member_year', 'Legacy governance index', r'\begin{tabular}[t]{@{}l@{}}mixed units/\\member-year\end{tabular}', 2, 1),
        ('decision_regret_per_available_member_hour', r'Decision regret', r'$10^{-6}$/member-hour', 2, 1e6),
        ('confirmation_mean_days', 'Mean confirmation delay', 'days', 1, 1),
        ('honest_rejection_share', 'Honest claims rejected', 'percentage points', 2, 100),
        ('gini_formal_authority', 'Gini of formal authority', 'index', 4, 1),
        ('voluntary_and_other_exits_per_member_year', 'Exits', 'per member-year', 4, 1),
        ('final_active_members', 'Final active members', 'members', 2, 1)]


def fmt(v, digits):
    return f'{v:+.{digits}f}'.replace('-', '$-$')


def table(study, label, caption):
    inf = json.loads((ROOT/study/'inference.json').read_text())
    w = inf['worlds_with_reference']
    lines = [r'\begin{table}[htbp]', r'\centering', r'\footnotesize', r'\setlength{\tabcolsep}{4pt}',
             rf'\caption{{Compact-retention enterprise-model summary (Section~\ref{{sec:r11-status}}). {caption} Each entry is the mean paired difference from the performance-tier hierarchy over {w} independent worlds with a paired Student-$t$ 95\% interval. $^{{\dagger}}$Primary outcome; p-values are Holm-adjusted across the three primary outcomes within each comparison. Displayed intervals are pointwise, not multiplicity-adjusted. Other rows are secondary. The legacy governance index adds member hours and resource units; it is not a cost in one unit. Recorded failure files (not a complete missing-world inventory): {len(inf["failures"])}.}}',
             rf'\label{{{label}}}',
             r'\begin{tabular}{@{}llrrr@{}}', r'\toprule',
             r'Outcome & Unit & ' + ' & '.join(r'\shortstack[r]{'+name.replace(', ', r',\\ ',1)+'}' for _, name in ARMS) + r' \\', r'\midrule']
    for n_row, (key, name, unit, digits, scale) in enumerate(ROWS):
        means, intervals = [], []
        for arm, _ in ARMS:
            r = inf['comparisons'][arm][key]
            lo, hi = (x*scale for x in r['ci95'])
            mark = r'$^{\dagger}$' if r['role'] == 'primary' else ''
            means.append(f"{fmt(r['mean_difference']*scale, digits)}{mark}")
            intervals.append(rf"\scriptsize[{fmt(lo, digits)}, {fmt(hi, digits)}]")
        if n_row:
            lines.append(r'\addlinespace[2pt]')
        lines.append(f'{name} & {unit} & ' + ' & '.join(means) + r' \\')
        lines.append(' & & ' + ' & '.join(intervals) + r' \\')
    lines += [r'\bottomrule', r'\end{tabular}', r'\end{table}', '']
    return '\n'.join(lines).replace('$-$', '$-$')


CONTEXTS = [('traditional', 'Traditional'), ('century', 'Century-old'), ('listed', 'Listed'), ('unicorn', 'Unicorn')]


def context_table(label, caption, studies=None):
    """DAMS (witnessed) minus hierarchy, one column per completed study (default: mid-life contexts)."""
    done = [(c, name) for c, name in (studies or CONTEXTS) if (ROOT/c/'inference.json').exists()]
    infs = {c: json.loads((ROOT/c/'inference.json').read_text()) for c, _ in done}
    failures = sum(len(infs[c]['failures']) for c, _ in done)
    lines = [r'\begin{table}[htbp]', r'\centering', r'\footnotesize', r'\setlength{\tabcolsep}{4pt}',
             rf'\caption{{Compact-retention enterprise-model summary (Section~\ref{{sec:r11-status}}). {caption} Each entry is the mean paired difference between DAMS on the witnessed process and the performance-tier hierarchy, with a paired Student-$t$ 95\% interval. $^{{\dagger}}$Primary outcome; p-values are Holm-adjusted across the three primary outcomes. Displayed intervals are pointwise, not multiplicity-adjusted. Other rows are secondary. The legacy governance index adds member hours and resource units; it is not a cost in one unit. Recorded failure files (not a complete missing-world inventory): {failures}.}}',
             rf'\label{{{label}}}',
             r'\begin{tabular}{@{}l' + 'r'*len(done) + r'@{}}', r'\toprule',
             r'Outcome (unit) & ' + ' & '.join(name for _, name in done) + r' \\',
             r'Worlds & ' + ' & '.join(str(infs[c]['worlds_with_reference']) for c, _ in done) + r' \\', r'\midrule']
    for n_row, (key, name, unit, digits, scale) in enumerate(ROWS):
        means, intervals = [], []
        for c, _ in done:
            r = infs[c]['comparisons']['dams-witness'][key]
            lo, hi = (x*scale for x in r['ci95'])
            mark = r'$^{\dagger}$' if r['role'] == 'primary' else ''
            means.append(f"{fmt(r['mean_difference']*scale, digits)}{mark}")
            intervals.append(rf"\scriptsize[{fmt(lo, digits)}, {fmt(hi, digits)}]")
        if n_row:
            lines.append(r'\addlinespace[2pt]')
        lines.append(f'{name} & ' + ' & '.join(means) + r' \\')
        lines.append(rf'\quad\scriptsize {unit} & ' + ' & '.join(intervals) + r' \\')
    lines += [r'\bottomrule', r'\end{tabular}', r'\end{table}', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True)
    (OUT/'r11_table_n300.tex').write_text(table('n300', 'tab:r11-n300',
        'Five-year outcomes of a 300-member startup that adopts its governance arrangement at founding.'))
    (OUT/'r11_table_n30.tex').write_text(table('n30', 'tab:r11-n30',
        'Five-year outcomes of a 30-member startup that adopts its governance arrangement at founding.'))
    (OUT/'r11_table_n120.tex').write_text(table('n120', 'tab:r11-n120',
        'Five-year outcomes of a 120-member startup that adopts its governance arrangement at founding.'))
    (OUT/'r11_table_contexts.tex').write_text(context_table('tab:r11-contexts',
        'Mid-life adoption by a 300-member organization after two years of history, five years after adoption.'))
    (OUT/'r11_table_ma.tex').write_text(context_table('tab:r11-ma',
        'Growth by acquisition: a 300-member listed acquirer with two years of history, five years after adoption, offered one simulated target firm; all deals closed in both arms of every world.',
        studies=[('ma-centralize', 'Centralized target'), ('ma-preserve', 'Autonomous target')]))
    print('written', OUT/'r11_table_n300.tex', OUT/'r11_table_n30.tex', OUT/'r11_table_n120.tex')
