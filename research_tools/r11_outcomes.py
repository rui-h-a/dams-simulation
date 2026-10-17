"""Multi-objective outcomes and paired comparisons for R11 arms.

Each outcome keeps its own unit; there is no composite or sustainability score.
Rates use realised exposure (active member-days, available member-hours), not
initial population times horizon. Synthetic resource units are model units,
not currency. ESG/SDG entries are labelled proxies for the mechanisms the model
represents; they are not ESG ratings.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from pathlib import Path

OUTCOMES = {
    # name: (unit, direction, dimension)
    'operating_margin_ratio': ('ratio of revenue', 'higher', 'profit'),
    'net_operating_result_per_member_year': ('resource units / member-year', 'higher', 'profit'),
    'operating_cost_per_delivered_unit': ('resource units / product unit', 'lower', 'cost'),
    'governance_cost_per_member_year': ('resource units / member-year', 'lower', 'cost'),
    'output_per_available_member_hour': ('work units / member-hour', 'higher', 'efficiency'),
    'delivered_share_of_orders': ('ratio', 'higher', 'delivery'),
    'expired_share_of_orders': ('ratio', 'lower', 'delivery'),
    'confirmation_mean_days': ('calendar days', 'lower', 'process'),
    'honest_rejection_share': ('ratio of reviews', 'lower', 'quality'),
    'fraud_acceptance_share': ('ratio of fraudulent records', 'lower', 'quality'),
    'decision_regret_per_available_member_hour': ('regret units / member-hour', 'lower', 'decision'),
    'learning_hours_per_member_year': ('hours / member-year', 'higher', 'innovation proxy'),
    'memory_loss_per_member_year': ('memory units / member-year', 'lower', 'knowledge retention'),
    'voluntary_and_other_exits_per_member_year': ('exits / member-year', 'lower', 'retention'),
    'sick_absence_per_member_year': ('workdays / member-year', 'lower', 'social proxy (health)'),
    'material_logistics_per_delivered_unit': ('resource units / product unit', 'lower', 'environmental proxy'),
    'share_gap_female_minus_male': ('formal-share difference', 'closer to 0', 'social proxy (inclusion)'),
    'share_gap_migrant_minus_local': ('formal-share difference', 'closer to 0', 'social proxy (inclusion)'),
    'share_gap_caregiver_minus_other': ('formal-share difference', 'closer to 0', 'social proxy (inclusion)'),
    'share_gap_minority_minus_majority': ('formal-share difference', 'closer to 0', 'social proxy (inclusion)'),
    'gini_formal_authority': ('Gini of formal shares', 'descriptive', 'governance (concentration, not fairness)'),
    'participation_fraction': ('ratio of eligible members', 'higher', 'governance'),
    'final_active_members': ('people', 'descriptive', 'scale'),
    'closed': ('indicator', 'lower', 'survival'),
}


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def outcomes(arm_dir):
    arm_dir = Path(arm_dir)
    s = json.loads((arm_dir/'summary.json').read_text())
    rows = list(csv.DictReader((arm_dir/'timeseries.csv').open()))
    last = rows[-1]
    member_years = s['active_member_calendar_days']/365.25 if s['active_member_calendar_days'] else None
    rev, opc = s['revenue_resource_units'], s['operating_resource_units']
    gov = s['review_hours']+s['appeal_hours']+s['governance_hours']+s.get('organization_verification_hours', 0.)
    orders = s.get('orders_arrived_units') or None
    delivered = s.get('delivered_product_units') or None
    per_my = (lambda v: v/member_years if member_years else None)
    tail = rows[-365:]
    def mean_col(col):
        vals = [_f(r.get(col)) for r in tail]
        vals = [v for v in vals if v is not None]
        return statistics.fmean(vals) if vals else None
    out = {
        'operating_margin_ratio': (rev-opc)/rev if rev else None,
        'net_operating_result_per_member_year': per_my(rev-opc),
        'operating_cost_per_delivered_unit': opc/delivered if delivered else None,
        'governance_cost_per_member_year': per_my(gov+s.get('verification_resource_units', 0.)),
        'output_per_available_member_hour': s.get('produced_work_per_available_member_hour'),
        'delivered_share_of_orders': delivered/orders if orders and delivered is not None else None,
        'expired_share_of_orders': s.get('orders_expired_units', 0.)/orders if orders else None,
        'confirmation_mean_days': s.get('confirmation_mean_days'),
        'honest_rejection_share': s['honest_records_rejected']/max(1., s['confirmed_records']+s['reviews_rejected']),
        'fraud_acceptance_share': (s['fraudulent_records_accepted']/max(1., s['fraudulent_records_accepted']+s['fraudulent_records_detected'])) if (s['fraudulent_records_accepted']+s['fraudulent_records_detected']) else None,
        'decision_regret_per_available_member_hour': s.get('decision_regret_per_available_member_hour'),
        'learning_hours_per_member_year': per_my(s.get('organization_learning_hours', 0.)),
        'memory_loss_per_member_year': per_my(s.get('memory_loss_units', 0.)),
        'voluntary_and_other_exits_per_member_year': per_my(s.get('exits', 0.)),
        'sick_absence_per_member_year': per_my(s.get('sick_absence_workdays', 0.)),
        'material_logistics_per_delivered_unit': s.get('enterprise_material_and_logistics_resource_units', 0.)/delivered if delivered else None,
        'gini_formal_authority': mean_col('gini_authority'),
        'participation_fraction': mean_col('participation_fraction'),
        'final_active_members': s.get('active_members_final'),
        'closed': 1. if s.get('closure_day') is not None else 0.,
    }
    for g in ('share_gap_female_minus_male', 'share_gap_migrant_minus_local', 'share_gap_caregiver_minus_other', 'share_gap_minority_minus_majority'):
        out[g] = mean_col(g)
    return out


def paired(study_root, reference='control-hierarchy'):
    """Paired differences arm minus reference, one difference per independent world."""
    root = Path(study_root)
    worlds = sorted(p for p in root.iterdir() if p.is_dir() and (p/'prehistory'/'prehistory.json').exists())
    table = {}
    for w in worlds:
        ref = w/reference
        if not (ref/'arm.json').exists():
            continue
        base = outcomes(ref)
        for arm in sorted(p for p in w.iterdir() if (p/'arm.json').exists() and p.name != reference):
            o = outcomes(arm)
            for k in OUTCOMES:
                if o.get(k) is not None and base.get(k) is not None:
                    table.setdefault(arm.name, {}).setdefault(k, []).append(o[k]-base[k])
    report = {}
    for arm, cols in table.items():
        report[arm] = {}
        for k, d in cols.items():
            n = len(d)
            m = statistics.fmean(d)
            se = statistics.stdev(d)/math.sqrt(n) if n >= 2 else None
            report[arm][k] = {'unit': OUTCOMES[k][0], 'direction': OUTCOMES[k][1], 'dimension': OUTCOMES[k][2],
                              'worlds': n, 'mean_difference': m, 'mc_standard_error': se, 'differences': d}
    return report


if __name__ == '__main__':
    print(json.dumps(paired(sys.argv[1], *(sys.argv[2:3])), indent=1))
