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
# Pairs outside the Big Five. 'judgment' is decision quality, read here as
# general cognitive ability; observed rather than corrected meta-analytic values
# are used because that reading is itself imperfect (conservative choice).
#   patience|risk_tolerance  +0.21  Falk, Becker, Dohmen, Huffman and Sunde
#       (2016), IZA DP 9504, Table 10 (individual-level partial correlation,
#       Global Preferences Survey; both traits coded high = patient / willing).
#   education|judgment       +0.46  Strenze (2007), Intelligence 35(5), Table 1
#       (mean r, k = 59, N = 84,828; corrected rho .56).
#   judgment|openness        +0.17  Anglim et al. (2022), Psychological Bulletin
#       148(5-6), observed r between openness and intelligence.
#   education|openness       +0.15  assumption inside the reported .14-.31 range
#       (Almlund et al. 2011), lowered because part of the link runs through
#       judgment.
#   health_risk|neuroticism  +0.15  assumption; direction only (no pooled r).
# Every unlisted pair is zero.
OTHER_TRAIT_CORRELATIONS = {
    'patience|risk_tolerance': 0.21, 'education|judgment': 0.46,
    'judgment|openness': 0.17, 'education|openness': 0.15,
    'health_risk|neuroticism': 0.15,
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
    sites = len(cx['countries'])
    if growth == 'acquisition':
        # The installed-inventory contract sizes guilds and sites from the
        # maximum capacity; without organic headroom a small acquired firm
        # cannot be installed as its own site, so acquisitions run with organic growth.
        raise ValueError("acquisitions are studied as 'organic+acquisition'")
    targets = _simulated_targets(n, context, branch, days, world, seed) if acquisition else []
    acquired_seats = sum(len(t['employees']) for t in targets)
    G.update(initial_organization_capacity=n, maximum_organization_capacity=capacity_ceiling+acquired_seats,
             initial_departments=math.ceil(guilds/2), guilds_per_department=2,
             members_per_guild=max(8, math.ceil(capacity_ceiling/guilds)),
             # With acquisitions, sites are sized so the acquired firm can be
             # installed as one more site, guild and department of the acquirer.
             members_per_site=max(16, _initial_site_load(n, guilds, sites), math.ceil(capacity_ceiling/sites))
             if acquisition else max(16, capacity_ceiling),
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
    if targets:
        for t in targets:
            t.update(guilds=1, departments=1, sites=1)
        A.update(MA_INTEGRATION, targets=targets,
                 # The price may be borrowed in full at the operating credit rate, so
                 # whether the deal closes turns on the acquirer's margin, not on its
                 # cash balance on the offer day (design choice).
                 maximum_acquisition_financing_resource_units=_price_with_costs(targets),
                 maximum_consolidated_debt_resource_units=cx['credit_limit_per_member']*n+_price_with_costs(targets)
                 +sum(t['financial_debt_resource_units'] for t in targets),
                 integration_resource_units_per_employee_workday=_integration_cost(targets),
                 integration_payroll_resource_units_per_employee_workday=L['payroll_resource_units_per_member_workday'])
    else:
        d.pop('enterprise_acquisition')
    d.pop('joint_population', None)
    if person_context:
        d['person_context'] = person_context_for(cx, context)
    d = apply_variant(d, branch, variant, context) if person_context else d
    return d, branch


# Two-firm acquisition (research note .revision/r11-research/ma-integration-parameters-20261019.md).
# One target per world: a separately simulated founding-stage firm of one fifth of
# the acquirer's initial size, available from the start of the second year after
# the adoption boundary for 180 days. Its roster, money and records come from its
# own simulated history (research_tools/r11_targets.py).
MA_TARGET_SHARE = .2
MA_TARGET_CONTEXT = 'startup'
MA_PRICE_MULTIPLE = 14.  # price = net cash + 14 x last-year operating surplus (12-16, computed range)
MA_INTEGRATION_COST_SHARE = .025  # integration spending, share of price (EY 2024: 1-4 % of deal value)
MA_INTEGRATION = {
    'integration_mode': 'preserve-autonomy', 'minimum_past_margin': 0., 'require_unmet_orders': False,
    # In this module acquired staff are held out of production until integration is
    # paid, so the window is the administrative transfer, one working month (design
    # scenario; no sourced duration), not the multi-year disruption period.
    'integration_workdays': 20,
    # Excess exit of acquired staff by the end of the first year after the deal, about
    # 0.065 (Verginer and Riccaboni, Fig. 3a at tau=1, visual reading), placed in the
    # transfer window: 1-(1-0.065)^(1/20). Later excess exit is not represented.
    'integration_exit_probability_per_workday': 3.36e-3,
}


def _simulated_targets(n, context, branch, days, world, seed):
    from research_tools.r11_targets import simulate_target, target_from_state
    available = branch+365
    if available >= days-90:
        return []
    key = 'target-0'
    state, td = simulate_target(key=key, scale=max(4, round(n*MA_TARGET_SHARE)), context=MA_TARGET_CONTEXT,
                                world=world, seed=seed+1, available_day=available)
    t = target_from_state(key=key, state=state, context=MA_TARGET_CONTEXT, available_day=available,
                          expires_day=min(days-1, available+180), growth=td['enterprise_growth'])
    t['consideration_resource_units'] = max(1., t.pop('net_cash')+MA_PRICE_MULTIPLE*max(0., t.pop('annual_surplus')))
    return [t]


def _initial_site_load(n, guilds, sites):
    load = [0]*sites
    for g in range(guilds):
        load[g % sites] += (n+guilds-1-g)//guilds
    return max(load)


def _price_with_costs(targets):
    t = targets[0]
    return t['consideration_resource_units']*(1+MA_INTEGRATION_COST_SHARE)+1.


def _integration_cost(targets):
    t = targets[0]
    return MA_INTEGRATION_COST_SHARE*t['consideration_resource_units']/(len(t['employees'])*MA_INTEGRATION['integration_workdays'])


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
            'cross_group_evaluation_bias': 0., 'baseline_annual_quit_probability': cx['quit'],
            'quit_risk_tolerance_coefficient': .22}


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
    'demand-matched': {'enterprise_operations.base_orders_per_workday': 'matched'},
    'ma-centralize': {'enterprise_acquisition.integration_mode': 'centralize'},
    # Absence in spells whose onset and length depend on age (R11.4 mechanism).
    'sickness-spells': {'person_context.sick_spells': 'aok2017'},
}
# Amendment 9: executed governance decisions change later productivity. The null (effect 0) is the default path, not a variant.
# Amendment 9a: at 0.05-0.2 the summed live effects (20 days of daily decisions) reach the 1.5 clip on
# 72-98 % of work rows in both arms, so those sizes measure the clip, not decision quality; 0.005/0.01 keep it rare.
for _effect in (0.005, 0.01, 0.05, 0.1, 0.2):
    VARIANTS[f'decision-effect-{_effect:g}'] = {'decision_effect': _effect, 'decision_execution_delay_days': 5,
                                                'decision_execution_hours': 0.01, 'decision_effect_days': 20}
# Scale-limit study (exploratory): each variant relaxes one design rule that is a fixed multiple
# of the initial population, so that a flat per-member curve over N can be checked for being
# true or an artefact of per-capita design arithmetic.
VARIANTS['scale-capacity-1n'] = {'scale.capacity_multiplier': 1}  # organization capacity ceiling n instead of 3n
VARIANTS['scale-capacity-6n'] = {'scale.capacity_multiplier': 6}
VARIANTS['scale-caps-wide'] = {'scale.caps': (20, 60)}  # active/ever-people caps 20n/60n instead of 4n/12n
VARIANTS['scale-no-work-stop'] = {'scale.work_stop_days': 0}  # no stop of new work 60 days before the end
# Review and appeal capacity of a fixed total (calibrated at 100 initial members) instead of per member.
VARIANTS['scale-fixed-review'] = {'scale.fixed_review_reference_n': 100}
# Every present member files about one claim per workday while review slots are 0.9/unit per member-day,
# so the default review queue is unstable (rho > 1) in every arm; 1.6 gives rho < 1 for all three backends.
VARIANTS['review-stable'] = {'review_capacity_per_member_day': 1.6}

# Spell lengths: share of sickness cases by calendar-day length band, German statutory
# insurer AOK, 2017 (WIdO Fehlzeiten-Report 2018, Abb. 29.1.5); the open band above 42 days
# is closed at 183 days so that it carries about 42 per cent of all days, as reported.
SICK_SPELL_BINS = [[1, 3, .355], [4, 7, .312], [8, 14, .173], [15, 21, .058], [22, 28, .029], [29, 42, .029], [43, 183, .044]]
# Age bands: cases per 100 insured and days per case (same source, Abb. 29.1.9/29.1.10),
# each divided by its unweighted mean over the ten bands.
_AOK_CASES = [276.8, 225.3, 166.1, 146.5, 146.6, 149.0, 150.2, 155.9, 166.1, 175.2]
_AOK_DAYS = [5.0, 6.1, 7.7, 8.8, 10.0, 11.4, 13.4, 15.3, 17.3, 21.0]
SICK_AGE_PROFILE = [[15+5*k, round(c/(sum(_AOK_CASES)/10), 4), round(d/(sum(_AOK_DAYS)/10), 4)]
                    for k, (c, d) in enumerate(zip(_AOK_CASES, _AOK_DAYS))]
# Level calibrated to the UK 2023 value of 4.9 days lost per worker (ONS; calibration year):
# at 4.3 three 300-member traditional worlds over three years gave 3.97 days per member-year,
# so the level is scaled to 4.3 x 4.9 / 3.97 = 5.3. The 2024 value (4.4) is held out.
SICK_ANNUAL_LEVEL = 5.3

# Amendment 6: market size matched to capacity. The template fixes the price
# (price_adjustment_rate 0), and orders arrive at one unit per member per workday
# times the demand schedule, so in every context 67-90 % of order units expire and
# output is always capacity-bound. The factor scales base orders so that arriving
# orders are 78 % of what the hierarchy arm delivers when capacity-bound (target
# capacity utilization 0.78), the 2021-2025
# mean of US capacity utilization (Federal Reserve G.17, series TCU: 76.9, 78.5,
# 77.3, 76.1, 75.9). Ratios measured on pilot worlds 0-2 (not confirmation worlds),
# hierarchy arm, delivered and arrived units per active member-workday: startup
# 0.350/1.639, traditional 0.335/0.997, listed 0.337/1.161, century 0.336/1.019,
# unicorn 0.340/3.407; factor = 0.78 x delivered/arrived.
DEMAND_MATCH = {'startup': .166, 'traditional': .262, 'listed': .226, 'century': .257, 'unicorn': .078}


def apply_variant(d, branch, name, context=None):
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
        elif key == 'person_context.sick_spells':
            pc.update(sick_spell_model='spells', sick_spell_bins=SICK_SPELL_BINS, sick_age_profile=SICK_AGE_PROFILE,
                      annual_sick_days=SICK_ANNUAL_LEVEL)
        elif key == 'enterprise_operations.base_orders_per_workday':
            if context not in DEMAND_MATCH:
                raise ValueError('demand-matched variant has no measured factor for this context')
            d['enterprise_operations']['base_orders_per_workday'] *= DEMAND_MATCH[context]
        elif key == 'scale.capacity_multiplier':
            n, G, L = d['n'], d['enterprise_growth'], d['longitudinal']
            if 'enterprise_acquisition' in d or G['maximum_organization_capacity'] != 3*n:
                raise ValueError('scale capacity variants are defined for organic growth only')
            ceiling = int(value)*n
            G.update(maximum_organization_capacity=ceiling, members_per_guild=max(8, math.ceil(ceiling/d['guilds'])),
                     members_per_site=max(16, ceiling))
            L.update(max_active_members=max(L['max_active_members'], ceiling), max_people_ever=max(L['max_people_ever'], 3*ceiling))
        elif key == 'scale.caps':
            n, L = d['n'], d['longitudinal']
            L.update(max_active_members=value[0]*n, max_people_ever=value[1]*n)
        elif key == 'scale.work_stop_days':
            d['longitudinal']['work_creation_stop_day'] = d['days']-int(value)
        elif key == 'scale.fixed_review_reference_n':
            factor = value/d['n']
            d['review_capacity_per_member_day'] = d.get('review_capacity_per_member_day', .9)*factor
            d['appeal_capacity_per_member_day'] = d.get('appeal_capacity_per_member_day', .1)*factor
        elif key.startswith('enterprise_acquisition.'):
            d['enterprise_acquisition'][key.split('.', 1)[1]] = value
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
