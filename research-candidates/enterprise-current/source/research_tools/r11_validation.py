"""Structural and behavioral pattern checks for R11 arms (validation layers 1 and 2).

Reads only the retained outputs of a validated arm (final_state.json, summary.json,
timeseries.csv). Reference values are public aggregates used as targets for
patterns, not inputs that were fitted here; each carries its source and its
period so that calibration and holdout periods remain separate.
"""
from __future__ import annotations

import collections
import csv
import json
import math
import statistics
import sys
from pathlib import Path

# U.S. JOLTS annual average monthly quits rate, total nonfarm (percent per month).
# Calibration period 2021-2023, holdout 2024-2025 (BLS JOLTS Table 21/22, accessed 2026-10-17).
JOLTS_MONTHLY_QUITS = {2021: 2.7, 2022: 2.8, 2023: 2.4, 2024: 2.1, 2025: 2.0}


def _quantiles(values, qs=(.1, .25, .5, .75, .9)):
    if not values:
        return None
    v = sorted(values)
    return {str(q): v[min(len(v)-1, int(q*(len(v)-1)+.5))] for q in qs}


def arm_patterns(arm_dir):
    arm = Path(arm_dir)
    env = json.loads((arm/'final_state.json').read_text())
    state = env.get('state', env)
    summary = json.loads((arm/'summary.json').read_text())
    people = state['people']
    days = state['day']
    member_years = summary['active_member_calendar_days']/365.25
    reasons = collections.Counter(p['exit_reason'] for p in people if p.get('exited_day') is not None)
    tenure_at_exit = [(p['exited_day']-p['entered_day'])/365.25 for p in people
                      if p.get('exited_day') is not None and p.get('exit_reason') in ('voluntary_exit', 'exit')]
    org = state.get('organization_tasks_state') or {}
    depth = None
    topo = (org.get('last_plan') or {}).get('topology') if isinstance(org.get('last_plan'), dict) else None
    if topo and 'reporting_depth' in topo:
        depths = list(topo['reporting_depth'].values())
        depth = {'max_layers': max(depths)+1, 'mean_depth': statistics.fmean(depths)}
    guild_sizes = collections.Counter()
    for slot, i in state['slots'].items():
        guild_sizes[state['slot_guild'][slot]] += 1
    rows = list(csv.DictReader((arm/'timeseries.csv').open()))
    active = [float(r['active_members']) for r in rows if r.get('active_members')]
    return {
        'arm': arm.name, 'days': days,
        'members_initial': summary.get('initial_population'), 'members_final': summary.get('active_members_final'),
        'members_min': min(active) if active else None, 'members_max': max(active) if active else None,
        'member_years': member_years,
        'exits_per_member_year_by_reason': {k: v/member_years for k, v in reasons.items()},
        'voluntary_exit_annual_rate': (reasons.get('voluntary_exit', 0)+reasons.get('exit', 0))/member_years,
        'tenure_at_voluntary_exit_years_quantiles': _quantiles(tenure_at_exit),
        'share_of_voluntary_exits_within_first_year': (sum(t < 1 for t in tenure_at_exit)/len(tenure_at_exit)) if tenure_at_exit else None,
        'sick_absence_days_per_member_year': summary.get('sick_absence_workdays', 0.)/member_years,
        'confirmation_mean_days': summary.get('confirmation_mean_days'),
        'confirmation_p95_days': summary.get('confirmation_p95_days'),
        'reporting_structure': depth,
        'guild_size_quantiles': _quantiles(list(guild_sizes.values())),
        'guilds_final': len(guild_sizes), 'sites_final': summary.get('site_count_final'),
        'departments_final': summary.get('department_count_final'),
    }


def references():
    cal = statistics.fmean(JOLTS_MONTHLY_QUITS[y] for y in (2021, 2022, 2023))*12/100
    hold = statistics.fmean(JOLTS_MONTHLY_QUITS[y] for y in (2024, 2025))*12/100
    return {'jolts_quits_annualized_calibration_2021_2023': cal,
            'jolts_quits_annualized_holdout_2024_2025': hold,
            'note': 'monthly average x12 approximates an annual quit share; U.S. total nonfarm; not firm-specific'}


def study(root):
    out = {'references': references(), 'arms': []}
    for arm in sorted(Path(root).glob('*/*')):
        if (arm/'arm.json').exists() and (arm/'final_state.json').exists():
            out['arms'].append({'world': arm.parent.name, **arm_patterns(arm)})
    return out


if __name__ == '__main__':
    print(json.dumps(study(sys.argv[1]), indent=1, default=str))
