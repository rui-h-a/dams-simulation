#!/usr/bin/env python3
"""Recompute the four prespecified outcomes from retained seven-year arm totals."""

from csv import DictReader
from hashlib import sha256
from io import StringIO
import json
from math import isfinite, sqrt
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
            margin = T_975_DF9 * deviation / sqrt(len(WORLDS))
            interval = [mean - margin, mean + margin] if deviation else None
            comparisons[arm][name] = {'mean_difference': mean, 'ci95': interval,
                                      'world_values': paired}
    return {'worlds': list(WORLDS), 'reference': ARMS[0], 'comparisons': comparisons}


if __name__ == '__main__':
    print(json.dumps(recompute(load_totals(Path(__file__).resolve().parent)), indent=2))
