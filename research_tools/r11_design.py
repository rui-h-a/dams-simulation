"""R11 study design: organization contexts, growth modes, adoption timing and governance arms.

Every numeric value below is a declared design or stress assumption unless the
factor registry (candidate-evidence/R11_FACTOR_COVERAGE.json) names a source.
Contexts change mechanisms (financing, disclosure and review load, routines,
memory, geography, languages, demand growth); a context label never sets an
outcome directly. Scale means the initial population at day 0.
"""
from __future__ import annotations

import copy
import json
import math
from datetime import date
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent/'candidate-evidence/calendar-networks-r7.json'
CALENDAR_START = '2020-01-01'

# Big Five and related latent correlations. Magnitudes are typical of published
# inventories but the exact matrix is UNVERIFIED (registry: van der Linden et al.
# 2010 matrix not obtained); they are therefore design assumptions with a
# structural sensitivity variant that sets every correlation to zero.
TRAIT_CORRELATIONS = {
    'conscientiousness|neuroticism': -0.30, 'agreeableness|neuroticism': -0.25,
    'extraversion|neuroticism': -0.25, 'extraversion|openness': 0.25,
    'agreeableness|conscientiousness': 0.25, 'education|openness': 0.25,
    'education|judgment': 0.30, 'patience|risk_tolerance': -0.10,
}


def _country(key, utc, legal, logistics_days, logistics_cost, communication):
    return {'key': key, 'utc_offset_hours': utc, 'work_start_local': 9, 'work_end_local': 17,
            'legal_resource_units_per_worker_workday': legal, 'logistics_delay_days': logistics_days,
            'logistics_resource_units_per_unit': logistics_cost, 'communication_workday_units': communication,
            'export_allowed': True}


# Contexts: mechanism parameters only. "age_years" is the organization age at
# day 0 (conditional synthetic initial state, history_mode unchanged).
CONTEXTS = {
    'startup': dict(age_years=1.0, memory=.15, routine=.15, credit=0., quit=.20, credit_limit_per_member=0.,
                    expansion_margin=.05, demand_growth_per_year=.25, reporting_hours=.005, verification_contacts=1,
                    countries=[_country('home', 0, 0., 1, 0., 0.)], languages=[['l0', 1.]], age_range=(22., 40.)),
    'unicorn': dict(age_years=5.0, memory=.30, routine=.30, credit=2., quit=.22, credit_limit_per_member=30.,
                    expansion_margin=.0, demand_growth_per_year=.40, reporting_hours=.008, verification_contacts=2,
                    countries=[_country('home', 0, 0., 1, 0., 0.), _country('region-b', 8, .002, 3, .02, .01)],
                    languages=[['l0', .8], ['l1', .2]], age_range=(22., 45.)),
    'listed': dict(age_years=30.0, memory=.70, routine=.75, credit=10., quit=.12, credit_limit_per_member=20.,
                   expansion_margin=.10, demand_growth_per_year=.05, reporting_hours=.015, verification_contacts=3,
                   countries=[_country('home', 0, .002, 1, .01, 0.), _country('region-b', 1, .003, 2, .02, .01),
                              _country('region-c', 8, .003, 4, .03, .02)],
                   languages=[['l0', .7], ['l1', .2], ['l2', .1]], age_range=(22., 62.)),
    'traditional': dict(age_years=40.0, memory=.75, routine=.85, credit=12., quit=.10, credit_limit_per_member=5.,
                        expansion_margin=.15, demand_growth_per_year=.0, reporting_hours=.010, verification_contacts=2,
                        countries=[_country('home', 0, .001, 1, .03, 0.)], languages=[['l0', 1.]], age_range=(22., 64.)),
    'century': dict(age_years=100.0, memory=.90, routine=.92, credit=15., quit=.07, credit_limit_per_member=10.,
                    expansion_margin=.15, demand_growth_per_year=.01, reporting_hours=.012, verification_contacts=2,
                    countries=[_country('home', 0, .001, 1, .02, 0.), _country('region-b', 1, .002, 2, .02, .01)],
                    languages=[['l0', .85], ['l1', .15]], age_range=(22., 64.)),
    # Reference context for a large multinational with business units, global
    # logistics and dispersed time zones. It is not a reconstruction of any company.
    'multinational': dict(age_years=25.0, memory=.60, routine=.65, credit=8., quit=.18, credit_limit_per_member=25.,
                          expansion_margin=.08, demand_growth_per_year=.10, reporting_hours=.015, verification_contacts=3,
                          countries=[_country('na', -5, .002, 2, .02, .01), _country('eu', 1, .004, 2, .02, .01),
                                     _country('apac-a', 9, .002, 4, .03, .02), _country('apac-b', 8, .002, 4, .03, .02),
                                     _country('latam', -3, .003, 5, .04, .02), _country('mea', 3, .003, 5, .04, .02)],
                          languages=[['l0', .45], ['l1', .2], ['l2', .15], ['l3', .1], ['l4', .1]], age_range=(22., 62.)),
}

GROWTH_MODES = ('none', 'organic', 'acquisition', 'organic+acquisition')

ARMS = {
    # Unchanged performance-tier hierarchy (no adoption).
    'control-hierarchy': dict(adoption='never', regime='hierarchy', backend='central'),
    # Same transition costs (migration, training, dual running) without a change of
    # authority rule: separates generic digitization/process change from DAMS.
    'control-process': dict(adoption='fixed', regime='hierarchy', backend='central'),
    'dams-central': dict(adoption='fixed', regime='sublinear', backend='central'),
    'dams-witness': dict(adoption='fixed', regime='sublinear', backend='witness'),
    'dams-consensus': dict(adoption='fixed', regime='sublinear', backend='consensus'),
    'linear-witness': dict(adoption='fixed', regime='linear', backend='witness'),
    'equal-witness': dict(adoption='fixed', regime='equal', backend='witness'),
}


def day_of_year_offset(years):
    start = date.fromisoformat(CALENDAR_START)
    return (date(start.year+years, start.month, start.day)-start).days


def base_config(*, scale, context, growth, world, seed, pre_years, post_years, person_context=True):
    if context not in CONTEXTS:
        raise ValueError('unknown context')
    if growth not in GROWTH_MODES:
        raise ValueError('unknown growth mode')
    cx = CONTEXTS[context]
    d = json.loads(TEMPLATE.read_text())
    branch = day_of_year_offset(pre_years)
    days = day_of_year_offset(pre_years+post_years)
    n = int(scale)
    guilds = max(2, round(n/250))
    organic = growth in ('organic', 'organic+acquisition')
    acquisition = growth in ('acquisition', 'organic+acquisition')
    capacity_ceiling = 3*n if organic else n
    d.update(n=n, days=days, guilds=guilds, team_size=8, seed=seed, world=world, regime='hierarchy',
             backend='central', alpha=.5, hierarchy_basis='performance', trace_every_days=1,
             attack='none', fault_start_day=0, fault_stop_day=0,
             max_wall_seconds=86400., sites=len(cx['countries']), max_output_mb=10_000_000., max_rss_mb=14000., max_events=10**12)
    L = d['longitudinal']
    L.update(calendar_start=CALENDAR_START, organization_initial_age_years=cx['age_years'],
             initial_memory=cx['memory'], initial_routine_strength=cx['routine'],
             historical_verified_credit_per_member=cx['credit'],
             initial_age_min_years=cx['age_range'][0], initial_age_max_years=cx['age_range'][1],
             entrant_age_years=27., retirement_age_years=65., annual_exit_probability=0.,
             annual_vacancy_fill_probability=.999, annual_leave_workdays=20., holidays=[],
             max_active_members=4*n, max_people_ever=12*n, adoption_mode='never', adoption_day=0,
             transition_days=180, initial_cash_per_member=150.,
             work_creation_stop_day=days-60)
    years = (days+1)/365.25
    L['demand_schedule'] = [[day_of_year_offset(y), round((1+cx['demand_growth_per_year'])**y, 6)]
                            for y in range(1, math.ceil(years)) if day_of_year_offset(y) < days]
    G = d['enterprise_growth']
    G.update(initial_organization_capacity=n, maximum_organization_capacity=capacity_ceiling,
             initial_departments=math.ceil(guilds/2), guilds_per_department=2,
             members_per_guild=max(8, math.ceil(capacity_ceiling/guilds)),
             members_per_site=max(16, capacity_ceiling),
             expansion_step_members=max(5, n//50), contraction_step_members=max(5, n//50),
             expansion_profit_margin=cx['expansion_margin'])
    O = d['enterprise_operations']
    O.update(base_orders_per_workday=float(n), countries=cx['countries'],
             capital_credit_limit_resource_units=cx['credit_limit_per_member']*n,
             management_span=8)
    T = d['organization_tasks']
    T.update(department_count=4, department_objectives=[[.8, .15, .05], [.6, .3, .1], [.5, .3, .2], [.7, .2, .1]],
             reporting_hours_per_layer=cx['reporting_hours'], verification_contacts=cx['verification_contacts'],
             management_span=8)
    A = d['enterprise_acquisition']
    if acquisition:
        A['targets'] = [_target(n, k, branch+day_of_year_offset(k+1)-day_of_year_offset(0)-1, days, cx)
                        for k in range(post_years) if branch+day_of_year_offset(k+1)-1 < days-90]
    else:
        A['targets'] = []
    if not A['targets']:
        d.pop('enterprise_acquisition')
    d.pop('joint_population', None)
    if person_context:
        d['person_context'] = person_context_for(cx)
    return d, branch


def _target(n, k, available, days, cx):
    size = max(4, n//20)
    employees = [{'key': f't{k}-p{j}', 'birth_date': '1988-01-01', 'skill': 1.0, 'tenure': 3.0, 'care_hours': .05,
                  'autonomy': .5, 'reciprocity': .5, 'learning': .5, 'trust': .6, 'fatigue': .1} for j in range(size)]
    return {'key': f'target-{k}', 'seller': f'seller-{k}', 'available_day': available, 'expires_day': min(days-1, available+180),
            'employees': employees, 'installed_seats': size, 'guilds': max(1, size//250), 'departments': 1,
            'sites': 1, 'cash_resource_units': 10.*size, 'financial_debt_resource_units': 20.*size,
            'consideration_resource_units': 60.*size, 'historical_age_years': 10.,
            'organizational_knowledge': .4, 'future_orders_per_workday': float(size),
            'prior_regime': 'hierarchy', 'prior_backend': 'central',
            'evidence': [{'event_id': f't{k}-prior', 'person_key': f't{k}-p0', 'amount': 900., 'unit': 'target-score-unit',
                          'evidence_quality': .5, 'origin_day': 0}],
            'pending_evidence': [{'event_id': f't{k}-open', 'person_key': f't{k}-p1', 'amount': 700., 'unit': 'other-unit',
                                  'evidence_quality': .2, 'origin_day': 0}],
            'history': [['historical-sales', 'resource-unit', 120.*size]]}


def person_context_for(cx):
    return {'schema_version': 1, 'trait_correlations': dict(TRAIT_CORRELATIONS),
            'employment_mix': [['permanent', .85, 1.0], ['fixed-term', .10, 1.8], ['contractor', .05, 2.5]],
            'caregiver_share': .3, 'caregiver_care_hours': .15, 'other_care_hours': .03, 'care_hours_sd': .03,
            'language_groups': cx['languages'], 'cross_language_help_discount': .15,
            'cross_group_evaluation_bias': 0., 'baseline_annual_quit_probability': cx['quit']}


def arm_config(base, branch, arm):
    spec = ARMS[arm]
    d = copy.deepcopy(base)
    d['regime'] = spec['regime']
    d['backend'] = spec['backend']
    L = d['longitudinal']
    if spec['adoption'] == 'never':
        L['adoption_mode'] = 'never'
        L['adoption_day'] = 0
    else:
        L['adoption_mode'] = 'fixed'
        L['adoption_day'] = branch
    return d
