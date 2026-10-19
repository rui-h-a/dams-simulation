"""Acquisition targets read from separately simulated firms (R11 M&A study).

Each target is its own R11 world: a hierarchy firm on a central record at fixed
installed capacity, run from the shared calendar start to the day it becomes
available for purchase. The
acquired roster (birth dates, skill, tenure, care load, autonomy, reciprocity,
learning, trust, fatigue), cash, debt, organizational memory, sales and each
employee's confirmed contribution record come from that firm's simulated state
instead of being set by hand. Traits outside the target schema are drawn again
on entry by the acquirer's person-context model.
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

from dams_sim.config import Config
from dams_sim.model import Model
from research_tools.r11_design import CONTEXTS, base_config


def _years_to(day):
    return math.ceil((day+1)/365.25)


def simulate_target(*, key, scale, context, world, seed, available_day, workdir=None):
    """Run one target firm to available_day and return (state, config dict)."""
    pre = _years_to(available_day)
    d, _ = base_config(scale=scale, context=context, growth='none', world=world, seed=seed, pre_years=pre, post_years=1)
    config = Config.from_dict(d)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        m = Model(config, storage_dir=Path(tmp)/'work')
        m.run(available_day)
        cp = Path(tmp)/'target.json'
        m.write_checkpoint(cp)
        m._long.ledger.close()
        state = json.loads(cp.read_text())['state']
    return state, d


def target_from_state(*, key, state, context, available_day, expires_day, growth, evidence_quality=.5):
    """Map a simulated firm's state onto the acquisition target schema."""
    agents = {a['id']: a for a in state['agents']}
    active = [p for p in state['people'] if p.get('exited_day') is None]
    employees, evidence = [], []
    for p in sorted(active, key=lambda p: p['id']):
        a = agents[p['id']]
        ek = f'{key}-p{p["id"]}'
        employees.append({'key': ek, 'birth_date': p['birth_date'], 'skill': a['skill'], 'tenure': a['tenure'],
                          'care_hours': min(.3, a['care_hours']), 'autonomy': a['autonomy'], 'reciprocity': a['reciprocity'],
                          'learning': a['learning'], 'trust': a['trust'], 'fatigue': a['fatigue']})
        if a['confirmed'] > 0:
            evidence.append({'event_id': f'{ek}-record', 'person_key': ek, 'amount': a['confirmed'],
                             'unit': 'target-score-unit', 'evidence_quality': evidence_quality, 'origin_day': 0})
    seats = len(employees)
    guilds = max(1, math.ceil(seats/growth['members_per_guild']))
    memory = list(state['memory'].values())
    ops = state['enterprise_operating_state']
    sales = state['metrics']['revenue_resource_units']
    # Operating surplus over the last 365 days, as the change in cash net of debt.
    h = state['history']
    worth = lambda row: row['cash_resource_units']-row['credit_debt_resource_units']
    surplus = worth(h[-1])-worth(h[-366]) if len(h) > 365 else 0.
    return {'key': key, 'seller': f'seller-{key}', 'available_day': available_day, 'expires_day': expires_day,
            'employees': employees, 'installed_seats': seats, 'guilds': guilds,
            'departments': max(1, math.ceil(guilds/growth['guilds_per_department'])),
            'sites': max(1, math.ceil(seats/growth['members_per_site'])),
            'cash_resource_units': max(0., state['cash']), 'financial_debt_resource_units': ops['debt'],
            'historical_age_years': CONTEXTS[context]['age_years']+(available_day+1)/365.25,
            'organizational_knowledge': min(2., math.fsum(memory)/len(memory)),
            'future_orders_per_workday': state['demand']*seats,
            'prior_regime': 'hierarchy', 'prior_backend': 'central',
            'evidence': evidence, 'pending_evidence': [],
            'history': [['historical-sales', 'resource-unit', sales]],
            # Valuation inputs, removed by the design once the price is set.
            'net_cash': max(0., state['cash'])-ops['debt'], 'annual_surplus': surplus}
