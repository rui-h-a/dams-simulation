#!/usr/bin/env python3
"""Recompute the four prespecified outcomes from retained seven-year arm totals."""

from csv import DictReader
from hashlib import sha256
from io import StringIO
import json
from math import erfc, exp, fsum, gamma, isfinite, sqrt
from pathlib import Path
from statistics import fmean, stdev

WORLDS = tuple(range(1000, 1010))
ARMS = ('control-hierarchy', 'control-process', 'dams-witness', 'linear-witness')
FIELDS = (
    'active_member_calendar_days', 'available_member_work_hours', 'revenue_resource_units',
    'operating_resource_units', 'decision_regret_units', 'review_hours', 'appeal_hours',
    'governance_hours', 'organization_verification_hours', 'verification_resource_units',
)
OUTCOMES = (
    'net_operating_result_per_member_year', 'decision_regret_per_available_member_hour',
    'governance_member_hours_per_member_year', 'witness_resource_units_per_member_year',
)
T_975_DF9 = 2.262157162798205
T_BONFERRONI_DF9 = 3.110934823179468


def load_totals(root):
    payload = (root / 'arm-inputs.csv').read_bytes()
    manifest = json.loads((root / 'manifest.json').read_bytes())
    if sha256(payload).hexdigest() != manifest['arm_inputs_sha256']:
        raise ValueError('Arm inputs do not match the manifest')
    totals = {}
    reader = DictReader(StringIO(payload.decode('utf-8')))
    if reader.fieldnames != ['world', 'arm', 'days', *FIELDS]:
        raise ValueError('Unexpected arm-input columns')
    for row in reader:
        key = int(row['world']), row['arm']
        if key in totals or int(row['days']) != 2557:
            raise ValueError(f'Duplicate arm or wrong horizon: {key}')
        values = {name: float(row[name]) for name in FIELDS}
        if (any(not isfinite(value) or value < 0 for value in values.values())
                or not values['active_member_calendar_days'] or not values['available_member_work_hours']):
            raise ValueError(f'Invalid totals or exposure: {key}')
        totals[key] = values
    if totals.keys() != {(world, arm) for world in WORLDS for arm in ARMS}:
        raise ValueError('The inputs do not contain the complete four-arm roster')
    return totals


def outcomes(totals):
    years = totals['active_member_calendar_days'] / 365.25
    operating = totals['revenue_resource_units'] - totals['operating_resource_units']
    hours = sum(totals[k] for k in ('review_hours', 'appeal_hours', 'governance_hours',
                                    'organization_verification_hours'))
    values = (operating / years, totals['decision_regret_units'] / totals['available_member_work_hours'],
              hours / years, totals['verification_resource_units'] / years)
    return dict(zip(OUTCOMES, values, strict=True))


def holm(probabilities):
    # Preserve the slot of an undefined test, and retain null in the output.
    ordered = sorted(
        enumerate(probabilities),
        key=lambda item: (1 if item[1] is None else item[1], item[0]),
    )
    adjusted = [None] * len(ordered)
    previous = 0.0
    for rank, (index, p_value) in enumerate(ordered):
        if p_value is not None:
            previous = min(1.0, max(previous, (len(ordered) - rank) * p_value))
            adjusted[index] = previous
    return adjusted


def quadrature(upper, count):
    step = upper / count
    return [
        (i * step, (1 if i in (0, count) else (4 if i % 2 else 2)) * step / 3)
        for i in range(count + 1)
    ]


def scale_density(value, df):
    # Density of sqrt(V / df) for V ~ chi-square(df).
    coefficient = 2 * (df / 2) ** (df / 2) / gamma(df / 2)
    return coefficient * value ** (df - 1) * exp(-df * value**2 / 2)


def normal_sf(value):
    return erfc(value / sqrt(2)) / 2


def null_two_sided_p(statistic, df):
    # For large statistics, scale the integration axis so the integrand does
    # not become concentrated in a tiny neighborhood of zero.
    rescale = max(1.0, statistic)
    return 2 / rescale * fsum(
        weight * scale_density(value / rescale, df)
        * normal_sf(statistic * value / rescale)
        for value, weight in quadrature(12 if statistic > 1 else 6, 8192)
    )


def recompute(totals):
    calculated = {key: outcomes(values) for key, values in totals.items()}
    comparisons = {}
    for arm in ARMS[1:]:
        comparisons[arm] = {}
        for name in OUTCOMES:
            paired = [[world, calculated[world, arm][name], calculated[world, ARMS[0]][name]] for world in WORLDS]
            differences = [treatment - control for _, treatment, control in paired]
            mean = fmean(differences)
            deviation = stdev(differences)
            standard_error = deviation / sqrt(len(WORLDS))
            marginal = T_975_DF9 * standard_error
            adjusted = T_BONFERRONI_DF9 * standard_error
            comparisons[arm][name] = {
                'mean_difference': mean, 'sample_sd': deviation,
                'ci95': [mean - marginal, mean + marginal] if deviation else None,
                'ci95_bonferroni_within_arm': [mean - adjusted, mean + adjusted] if deviation else None,
                'p_unadjusted_two_sided': null_two_sided_p(abs(mean) / standard_error, len(WORLDS) - 1)
                if deviation else None,
                'world_values': paired,
            }
    all_rows = [row for endpoints in comparisons.values() for row in endpoints.values()]
    for endpoints in comparisons.values():
        rows = list(endpoints.values())
        for row, p in zip(rows, holm([x['p_unadjusted_two_sided'] for x in rows]), strict=True):
            row['p_holm_within_arm_four'] = p
    for row, p in zip(all_rows, holm([x['p_unadjusted_two_sided'] for x in all_rows]), strict=True):
        row['p_holm_study_wide_twelve'] = p
    return {'worlds': list(WORLDS), 'reference': ARMS[0], 'comparisons': comparisons}


if __name__ == '__main__':
    print(json.dumps(recompute(load_totals(Path(__file__).resolve().parent)), indent=2))
