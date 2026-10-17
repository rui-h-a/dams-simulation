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

# Big Five latent intercorrelations: the ten meta-analytic true-score estimates
# (rho, corrected for unreliability and range restriction; K = 212 samples,
# N = 144,117) in van der Linden, te Nijenhuis and Bakker (2010), Journal of
# Research in Personality 44(3), Table 2, p. 318, doi:10.1016/j.jrp.2010.03.003.
# Traits in the model are error-free latent variables, so rho rather than the
# observed r is the matching quantity.
BIG_FIVE_CORRELATIONS = {
    'conscientiousness|openness': 0.20, 'extraversion|openness': 0.43,
    'agreeableness|openness': 0.21, 'neuroticism|openness': -0.17,
    'conscientiousness|extraversion': 0.29, 'agreeableness|conscientiousness': 0.43,
    'conscientiousness|neuroticism': -0.43, 'agreeableness|extraversion': 0.26,
    'extraversion|neuroticism': -0.36, 'agreeableness|neuroticism': -0.36,
}
# Same source, Table 3, p. 319, employee subsample (N = 10,654); used only in the
# 'big-five-employees' structural variant.
BIG_FIVE_CORRELATIONS_EMPLOYEES = {
    'conscientiousness|openness': 0.31, 'extraversion|openness': 0.50,
    'agreeableness|openness': 0.34, 'neuroticism|openness': -0.40,
    'conscientiousness|extraversion': 0.26, 'agreeableness|conscientiousness': 0.56,
    'conscientiousness|neuroticism': -0.66, 'agreeableness|extraversion': 0.45,
    'extraversion|neuroticism': -0.40, 'agreeableness|neuroticism': -0.51,
}
# Pairs outside the Big Five have no matrix source in the registry; they remain
# design assumptions. Every unlisted pair is zero.
OTHER_TRAIT_CORRELATIONS = {
    'education|openness': 0.25, 'education|judgment': 0.30,
    'patience|risk_tolerance': -0.10, 'health_risk|neuroticism': 0.15,
}
TRAIT_CORRELATIONS = {**BIG_FIVE_CORRELATIONS, **OTHER_TRAIT_CORRELATIONS}

# Monthly capacity multipliers for physical work. Northern-hemisphere hot-summer,
# southern-hemisphere hot-summer and temperate profiles; magnitudes are stress
# assumptions for heat exposure, not calibrated productivity losses.
CLIMATE = {'temperate': [1.]*12, 'hot-north': [1.]*5+[.92, .88, .88, .94]+[1.]*3,
           'hot-south': [.88, .88, .94]+[1.]*8+[.92], 'tropical': [.93]*12}



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
    # Guilds adopt in four waves 0, 6, 12 and 18 months after the boundary.
    'dams-staged-witness': dict(adoption='staged', regime='sublinear', backend='witness'),
    # Adoption only when the observable review backlog per active member reaches 2 records.
    'dams-triggered-witness': dict(adoption='triggered', regime='sublinear', backend='witness'),
}


def day_of_year_offset(years):
    start = date.fromisoformat(CALENDAR_START)
    return (date(start.year+years, start.month, start.day)-start).days


def base_config(*, scale, context, growth, world, seed, pre_years, post_years, person_context=True, variant='baseline'):
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
             management_span=8, coordination_manager_basis='span')
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
        d['person_context'] = person_context_for(cx, context)
    d = apply_variant(d, branch, variant) if person_context else d
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


COUNTRY_PROFILES = {
    # Country-level preference shifts in within-sample SD units (GPS structure:
    # 7-13.5% of preference variance lies between countries; Falk et al. 2018).
    # Values are illustrative magnitudes within that bound, not country estimates.
    'home': dict(language='l0', shifts={}, trust=0., climate='temperate', shock=[.02, .85, 20]),
    'region-b': dict(language='l1', shifts={'patience': .15, 'risk_tolerance': -.10}, trust=.03, climate='hot-north', shock=[.05, .8, 15]),
    'region-c': dict(language='l2', shifts={'patience': -.10, 'risk_tolerance': .10}, trust=-.03, climate='tropical', shock=[.08, .75, 20]),
    'na': dict(language='l0', shifts={'risk_tolerance': .10}, trust=0., climate='hot-north', shock=[.02, .9, 10]),
    'eu': dict(language='l1', shifts={'patience': .15}, trust=.03, climate='temperate', shock=[.02, .9, 10]),
    'apac-a': dict(language='l2', shifts={'patience': .20, 'risk_tolerance': -.15}, trust=.02, climate='hot-north', shock=[.03, .85, 15]),
    'apac-b': dict(language='l3', shifts={'patience': .05, 'risk_tolerance': -.05}, trust=-.02, climate='tropical', shock=[.06, .8, 20]),
    'latam': dict(language='l4', shifts={'patience': -.15, 'risk_tolerance': .05}, trust=-.05, climate='hot-south', shock=[.10, .75, 25]),
    'mea': dict(language='l4', shifts={'patience': -.05}, trust=-.03, climate='hot-north', shock=[.10, .7, 25]),
}
REMOTE_SHARE = {'startup': .5, 'unicorn': .35, 'listed': .25, 'traditional': .05, 'century': .1, 'multinational': .3}
PHYSICAL_SHARE = {'startup': .05, 'unicorn': .15, 'listed': .30, 'traditional': .60, 'century': .45, 'multinational': .40}


def person_context_for(cx, context=None):
    keys = [c['key'] for c in cx['countries']]
    n = len(keys)
    origins = [[k, 1/n if i < n-1 else 1-(n-1)*round(1/n, 12), COUNTRY_PROFILES[k]['language'], dict(COUNTRY_PROFILES[k]['shifts']), COUNTRY_PROFILES[k]['trust']]
               for i, k in enumerate(keys)]
    for i in range(n-1):
        origins[i][1] = round(1/n, 12)
    origins[-1][1] = 1-math.fsum(r[1] for r in origins[:-1])
    extra = {'origins': origins, 'site_origins': keys, 'local_origin_share': .9 if n > 1 else 1.,
             'migrant_exit_multiplier': 1.3,
             'female_share': .45, 'caregiver_share_female': .38, 'caregiver_share_male': .18,
             'gender_evaluation_bias': 0., 'ethnic_groups': [['e0', .7], ['e1', .2], ['e2', .1]],
             'minority_voice_shift': 0., 'cross_ethnic_help_discount': .0,
             'physical_work_share': PHYSICAL_SHARE.get(context, .3),
             'site_climate': [CLIMATE[COUNTRY_PROFILES[k]['climate']] for k in keys],
             'site_institution_shocks': [list(COUNTRY_PROFILES[k]['shock']) for k in keys],
             'annual_sick_days': 8.,
             'dependents_mean': .8, 'care_hours_per_dependent': .02, 'remote_share': REMOTE_SHARE.get(context, .2),
             'commute_hours_mean': .9, 'commute_fatigue_per_hour': .02, 'faction_size': 8, 'faction_cohesion': .2}
    return {**_person_context_base(cx), **extra}


def _person_context_base(cx):
    return {'schema_version': 1, 'trait_correlations': dict(TRAIT_CORRELATIONS),
            'employment_mix': [['permanent', .85, 1.0], ['fixed-term', .10, 1.8], ['contractor', .05, 2.5]],
            'caregiver_share': .3, 'caregiver_care_hours': .15, 'other_care_hours': .03, 'care_hours_sd': .03,
            'language_groups': cx['languages'], 'cross_language_help_discount': .15,
            'cross_group_evaluation_bias': 0., 'baseline_annual_quit_probability': cx['quit']}


# Structural/assumption variants for institutional-counterfactual robustness.
# Each changes one family of psychological, cultural or economic assumptions.
VARIANTS = {
    'baseline': {},
    'independent-traits': {'person_context.trait_correlations': {}},
    'big-five-employees': {'person_context.trait_correlations': 'employees'},
    'no-origin-preferences': {'person_context.origins.shifts': 'zero'},
    'evaluation-bias': {'person_context.gender_evaluation_bias': -0.05, 'person_context.cross_group_evaluation_bias': -0.05},
    'strong-factions': {'person_context.faction_cohesion': 0.5},
    'no-factions': {'person_context.faction_cohesion': 0.0},
    'high-turnover': {'person_context.baseline_annual_quit_probability': 'x1.5'},
    'low-turnover': {'person_context.baseline_annual_quit_probability': 'x0.5'},
    'reversed-share-response': {'autonomy_response': -0.25},
    'demand-shock': {'longitudinal.demand_schedule': 'shock'},
}


def apply_variant(d, branch, name):
    if name not in VARIANTS:
        raise ValueError('unknown variant')
    d = copy.deepcopy(d)
    pc = d.get('person_context')
    for key, value in VARIANTS[name].items():
        if key == 'person_context.trait_correlations':
            pc['trait_correlations'] = ({**BIG_FIVE_CORRELATIONS_EMPLOYEES, **OTHER_TRAIT_CORRELATIONS}
                                        if value == 'employees' else {})
        elif key == 'person_context.origins.shifts':
            for row in pc['origins']:
                row[3] = {}; row[4] = 0.
        elif key == 'person_context.baseline_annual_quit_probability':
            pc['baseline_annual_quit_probability'] = min(.95, pc['baseline_annual_quit_probability']*float(value[1:]))
        elif key.startswith('person_context.'):
            pc[key.split('.', 1)[1]] = value
        elif key == 'longitudinal.demand_schedule':
            # A 30 % demand fall one year after the boundary, recovering after 180 days.
            sched = d['longitudinal']['demand_schedule']
            level = lambda day: next((v for when, v in reversed(sched) if when <= day), 1.)
            t0 = branch+365
            sched = sorted([r for r in sched if not (t0 <= r[0] < t0+180)] + [[t0, round(level(t0)*.7, 6)], [t0+180, level(t0+180)]])
            d['longitudinal']['demand_schedule'] = sched
        else:
            d[key] = value
    return d


def arm_config(base, branch, arm):
    spec = ARMS[arm]
    d = copy.deepcopy(base)
    d['regime'] = spec['regime']
    d['backend'] = spec['backend']
    L = d['longitudinal']
    if spec['adoption'] == 'never':
        L['adoption_mode'] = 'never'
        L['adoption_day'] = 0
    elif spec['adoption'] == 'fixed':
        L['adoption_mode'] = 'fixed'
        L['adoption_day'] = branch
    elif spec['adoption'] == 'staged':
        L['adoption_mode'] = 'staged'
        L['guild_adoption_days'] = [[g, branch+182*(g % 4)] for g in range(d['guilds'])]
    else:
        L['adoption_mode'] = 'observable'
        L['observable_trigger'] = 'review_backlog_per_active_member'
        L['trigger_threshold'] = 2.0
        L['trigger_not_before_day'] = branch
    return d
