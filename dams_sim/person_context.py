"""Opt-in person and context mechanisms (R11).

Each agent receives correlated latent traits drawn through a Gaussian copula,
an employment contract, a primary-caregiver role and a working-language group.
Traits act only through declared behavioural paths: effort, cooperation,
participation, appeal voice, decision noise, learning, stress and voluntary
exit. Demographic labels are never read. Language group membership acts only
through communication cost and an optional evaluation-bias parameter, whose
default is zero and which is intended for sensitivity analysis.

Coefficient defaults are design assumptions. Their status and literature
support are recorded in ``candidate-evidence/R11_FACTOR_COVERAGE.json``.
Omitting ``person_context`` from a configuration leaves every earlier code path,
random draw, journal row and state field unchanged.
"""
from __future__ import annotations

import dataclasses
import math

VERSION = 'longitudinal-enterprise-person-context-11-2'
TRAITS = ('conscientiousness', 'agreeableness', 'neuroticism', 'openness',
          'extraversion', 'risk_tolerance', 'patience', 'judgment', 'education', 'health_risk',
          'physical_capacity', 'appearance', 'financial_strain')


def _finite(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite in [{low}, {high}]')


def cholesky(matrix):
    n = len(matrix)
    lower = [[0.]*n for _ in range(n)]
    for i in range(n):
        for j in range(i+1):
            s = math.fsum(lower[i][k]*lower[j][k] for k in range(j))
            if i == j:
                d = matrix[i][i]-s
                if d <= 1e-8:
                    raise ValueError('trait correlation matrix must be positive definite with numerical margin')
                lower[i][j] = math.sqrt(d)
            else:
                lower[i][j] = (matrix[i][j]-s)/lower[j][j]
    return lower


@dataclasses.dataclass(frozen=True)
class PersonContextConfig:
    schema_version: int = 1
    # Pairwise latent correlations, keyed "trait_a|trait_b"; unspecified pairs are zero.
    trait_correlations: dict = dataclasses.field(default_factory=dict)
    education_mean_years: float = 14.0
    education_sd_years: float = 2.5
    # [kind, population share, voluntary-exit hazard multiplier]
    employment_mix: list = dataclasses.field(default_factory=lambda: [['permanent', 1.0, 1.0]])
    caregiver_share: float = 0.0
    caregiver_care_hours: float = 0.15
    other_care_hours: float = 0.03
    care_hours_sd: float = 0.03
    # [label, population share]; one group means no language heterogeneity.
    language_groups: list = dataclasses.field(default_factory=lambda: [['l0', 1.0]])
    cross_language_help_discount: float = 0.0
    cross_group_evaluation_bias: float = 0.0
    baseline_annual_quit_probability: float = 0.0
    quit_stress_coefficient: float = 1.0
    quit_fairness_coefficient: float = 1.5
    quit_patience_coefficient: float = 0.2
    quit_conscientiousness_coefficient: float = 0.1
    stress_half_life_workdays: float = 20.0
    stress_demand_weight: float = 1.0
    stress_resource_weight: float = 0.6
    stress_neuroticism_coefficient: float = 0.25
    initial_fairness: float = 0.6
    fairness_update_rate: float = 0.05
    effort_conscientiousness_coefficient: float = 0.05
    effort_stress_coefficient: float = 0.1
    cooperation_agreeableness_coefficient: float = 0.1
    participation_extraversion_coefficient: float = 0.04
    participation_safety_coefficient: float = 0.2
    voice_base_probability: float = 1.0
    voice_safety_coefficient: float = 0.0
    judgment_noise_coefficient: float = 0.15
    learning_openness_coefficient: float = 0.1
    # R11.1 origin, gender, ethnicity, place and condition paths.
    # origins rows: [label, share, language, {trait: z shift}, initial-trust shift]
    origins: list = dataclasses.field(default_factory=list)
    site_origins: list = dataclasses.field(default_factory=list)
    local_origin_share: float = 1.0
    migrant_exit_multiplier: float = 1.0
    female_share: float = 0.0
    caregiver_share_female: float = -1.0
    caregiver_share_male: float = -1.0
    gender_evaluation_bias: float = 0.0
    ethnic_groups: list = dataclasses.field(default_factory=lambda: [['e0', 1.0]])
    minority_voice_shift: float = 0.0
    cross_ethnic_help_discount: float = 0.0
    physical_work_share: float = 0.0
    site_climate: list = dataclasses.field(default_factory=list)
    site_institution_shocks: list = dataclasses.field(default_factory=list)
    annual_sick_days: float = 0.0
    sick_health_coefficient: float = 0.5
    initial_identity: float = 0.6
    identity_update_rate: float = 0.02
    identity_quit_coefficient: float = 1.0
    identity_participation_coefficient: float = 0.1
    load_noise_coefficient: float = 0.3
    # R11.2 education, body, household, commute, finance and informal factions.
    learning_education_coefficient: float = 0.1
    physical_fatigue_coefficient: float = 0.02
    appearance_evaluation_bias: float = 0.0
    dependents_mean: float = 0.0
    care_hours_per_dependent: float = 0.02
    remote_share: float = 0.0
    commute_fatigue_per_hour: float = 0.02
    commute_hours_mean: float = 0.0
    financial_strain_quit_coefficient: float = 0.2
    financial_strain_stress_coefficient: float = 0.05
    faction_size: int = 0
    faction_cohesion: float = 0.0

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('unknown person context schema')
        if not isinstance(self.trait_correlations, dict):
            raise ValueError('trait_correlations must be an object')
        matrix = [[1. if i == j else 0. for j in range(len(TRAITS))] for i in range(len(TRAITS))]
        for key, rho in self.trait_correlations.items():
            parts = key.split('|') if isinstance(key, str) else []
            if len(parts) != 2 or parts[0] not in TRAITS or parts[1] not in TRAITS or parts[0] == parts[1]:
                raise ValueError(f'unknown trait correlation key {key!r}')
            _finite(rho, -0.99, 0.99, 'trait correlation')
            a, b = TRAITS.index(parts[0]), TRAITS.index(parts[1])
            if matrix[a][b] != 0.:
                raise ValueError('duplicate trait correlation pair')
            matrix[a][b] = matrix[b][a] = float(rho)
        cholesky(matrix)
        _finite(self.education_mean_years, 0, 30, 'education_mean_years')
        _finite(self.education_sd_years, 0, 10, 'education_sd_years')
        for name, rows, width in (('employment_mix', self.employment_mix, 3), ('language_groups', self.language_groups, 2)):
            if not isinstance(rows, list) or not rows or any(not isinstance(r, list) or len(r) != width or not isinstance(r[0], str) for r in rows):
                raise ValueError(f'{name} rows are malformed')
            if len({r[0] for r in rows}) != len(rows):
                raise ValueError(f'{name} labels must be unique')
            for r in rows:
                _finite(r[1], 0, 1, f'{name} share')
            if abs(math.fsum(r[1] for r in rows)-1) > 1e-9:
                raise ValueError(f'{name} shares must sum to one')
        for r in self.employment_mix:
            _finite(r[2], 0, 20, 'employment exit multiplier')
        for name in ('caregiver_share', 'caregiver_care_hours', 'other_care_hours', 'cross_language_help_discount',
                     'baseline_annual_quit_probability', 'initial_fairness', 'fairness_update_rate',
                     'voice_base_probability'):
            _finite(getattr(self, name), 0, 1, name)
        _finite(self.care_hours_sd, 0, 0.5, 'care_hours_sd')
        if not isinstance(self.origins, list) or any(not isinstance(r, list) or len(r) != 5 or not isinstance(r[0], str)
                                                     or not isinstance(r[2], str) or not isinstance(r[3], dict) for r in self.origins):
            raise ValueError('origins rows are malformed')
        if self.origins:
            if len({r[0] for r in self.origins}) != len(self.origins) or abs(math.fsum(r[1] for r in self.origins)-1) > 1e-9:
                raise ValueError('origin labels must be unique and shares sum to one')
            for r in self.origins:
                _finite(r[1], 0, 1, 'origin share'); _finite(r[4], -1, 1, 'origin trust shift')
                for t, v in r[3].items():
                    if t not in TRAITS:
                        raise ValueError('origin shift names an unknown trait')
                    _finite(v, -3, 3, 'origin trait shift')
            labels = {r[0] for r in self.origins}
            if not self.site_origins or any(o not in labels for o in self.site_origins):
                raise ValueError('site_origins must name declared origins')
        for name, rows in (('ethnic_groups', self.ethnic_groups),):
            if not isinstance(rows, list) or not rows or any(not isinstance(r, list) or len(r) != 2 or not isinstance(r[0], str) for r in rows):
                raise ValueError(f'{name} rows are malformed')
            if len({r[0] for r in rows}) != len(rows) or abs(math.fsum(r[1] for r in rows)-1) > 1e-9:
                raise ValueError(f'{name} labels must be unique and shares sum to one')
        for name in ('local_origin_share', 'female_share', 'physical_work_share', 'initial_identity', 'identity_update_rate'):
            _finite(getattr(self, name), 0, 1, name)
        for name in ('caregiver_share_female', 'caregiver_share_male'):
            v = getattr(self, name)
            if v != -1.0:
                _finite(v, 0, 1, name)
        _finite(self.migrant_exit_multiplier, 0, 20, 'migrant_exit_multiplier')
        for name in ('gender_evaluation_bias', 'minority_voice_shift'):
            _finite(getattr(self, name), -1, 1, name)
        _finite(self.cross_ethnic_help_discount, 0, 1, 'cross_ethnic_help_discount')
        _finite(self.annual_sick_days, 0, 60, 'annual_sick_days')
        for name in ('learning_education_coefficient', 'physical_fatigue_coefficient', 'commute_fatigue_per_hour',
                     'financial_strain_quit_coefficient', 'financial_strain_stress_coefficient'):
            _finite(getattr(self, name), -5, 5, name)
        _finite(self.appearance_evaluation_bias, -1, 1, 'appearance_evaluation_bias')
        _finite(self.dependents_mean, 0, 10, 'dependents_mean'); _finite(self.care_hours_per_dependent, 0, .2, 'care_hours_per_dependent')
        _finite(self.remote_share, 0, 1, 'remote_share'); _finite(self.commute_hours_mean, 0, 4, 'commute_hours_mean')
        _finite(self.faction_cohesion, 0, 1, 'faction_cohesion')
        if type(self.faction_size) is not int or not 0 <= self.faction_size <= 10_000:
            raise ValueError('faction_size must be an integer in [0, 10000]')
        for name in ('sick_health_coefficient', 'identity_quit_coefficient', 'identity_participation_coefficient', 'load_noise_coefficient'):
            _finite(getattr(self, name), -5, 5, name)
        for row in self.site_climate:
            if not isinstance(row, list) or len(row) != 12:
                raise ValueError('site_climate rows need twelve monthly multipliers')
            for v in row:
                _finite(v, 0, 1, 'monthly climate capacity multiplier')
        for row in self.site_institution_shocks:
            if not isinstance(row, list) or len(row) != 3 or type(row[2]) is not int or not 1 <= row[2] <= 365:
                raise ValueError('site_institution_shocks rows are [annual probability, capacity multiplier, duration days]')
            _finite(row[0], 0, 1, 'institution shock probability'); _finite(row[1], 0, 1, 'institution shock capacity')
        _finite(self.cross_group_evaluation_bias, -1, 1, 'cross_group_evaluation_bias')
        _finite(self.stress_half_life_workdays, 1, 3650, 'stress_half_life_workdays')
        for name in ('quit_stress_coefficient', 'quit_fairness_coefficient', 'quit_patience_coefficient',
                     'quit_conscientiousness_coefficient', 'stress_demand_weight', 'stress_resource_weight',
                     'stress_neuroticism_coefficient', 'effort_conscientiousness_coefficient',
                     'effort_stress_coefficient', 'cooperation_agreeableness_coefficient',
                     'participation_extraversion_coefficient', 'participation_safety_coefficient',
                     'voice_safety_coefficient', 'judgment_noise_coefficient', 'learning_openness_coefficient'):
            _finite(getattr(self, name), -5, 5, name)
        return self

    def matrix(self):
        m = [[1. if i == j else 0. for j in range(len(TRAITS))] for i in range(len(TRAITS))]
        for key, rho in self.trait_correlations.items():
            a, b = (TRAITS.index(x) for x in key.split('|'))
            m[a][b] = m[b][a] = float(rho)
        return m

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError('person_context must be an object')
        unknown = set(value)-{f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError('unknown person_context fields: '+str(sorted(unknown)))
        return cls(**value).validate()


def _pick(rows, u):
    acc = 0.
    for row in rows:
        acc += row[1]
        if u < acc:
            return row
    return rows[-1]


def _poisson(mean, u):
    if mean <= 0:
        return 0
    k, p = 0, math.exp(-mean)
    acc = p
    while u > acc and k < 50:
        k += 1; p *= mean/k; acc += p
    return k


def clip(x, low=0., high=1.):
    return max(low, min(high, x))


class PersonContextState:
    """Per-person latent traits and dynamic stress/fairness, aligned with agent ids."""

    def __init__(self, config):
        self.c = config.person_context
        self.lower = cholesky(self.c.matrix())
        self.people = []
        self._shock_cache = {}

    def draw(self, rng, token, site=0):
        c = self.c
        z = [rng.normal('pc_trait', token, k) for k in range(len(TRAITS))]
        x = [math.fsum(self.lower[i][k]*z[k] for k in range(i+1)) for i in range(len(TRAITS))]
        traits = dict(zip(TRAITS, x))
        employment = _pick(c.employment_mix, rng.uniform('pc_employment', token))[0]
        caregiver = rng.uniform('pc_caregiver', token) < c.caregiver_share
        mean = c.caregiver_care_hours if caregiver else c.other_care_hours
        care = clip(mean+c.care_hours_sd*rng.normal('pc_care', token), 0., 0.6)
        language = _pick(c.language_groups, rng.uniform('pc_language', token))[0]
        origin, migrant, trust_shift = None, False, 0.
        if c.origins:
            home = c.site_origins[site % len(c.site_origins)]
            if rng.uniform('pc_origin_local', token) < c.local_origin_share:
                origin = home
            else:
                origin = _pick(c.origins, rng.uniform('pc_origin', token))[0]
            row = next(r for r in c.origins if r[0] == origin)
            language, migrant, trust_shift = row[2], origin != home, row[4]
            for t, v in row[3].items():
                traits[t] += v
        female = rng.uniform('pc_gender', token) < c.female_share
        if c.caregiver_share_female != -1.0 and c.caregiver_share_male != -1.0:
            share = c.caregiver_share_female if female else c.caregiver_share_male
            caregiver = rng.uniform('pc_caregiver_gendered', token) < share
            care = clip((c.caregiver_care_hours if caregiver else c.other_care_hours)+c.care_hours_sd*rng.normal('pc_care', token), 0., 0.6)
        ethnicity = _pick(c.ethnic_groups, rng.uniform('pc_ethnicity', token))[0]
        physical = rng.uniform('pc_physical', token) < c.physical_work_share
        dependents = _poisson(c.dependents_mean, rng.uniform('pc_dependents', token))
        if c.dependents_mean > 0:
            care = clip(care+c.care_hours_per_dependent*dependents, 0., 0.6)
        remote = (not physical) and rng.uniform('pc_remote', token) < c.remote_share
        commute = 0. if remote else c.commute_hours_mean*math.exp(.5*rng.normal('pc_commute', token)-.125)
        education = max(0., c.education_mean_years+c.education_sd_years*traits['education'])
        return {'traits': traits, 'education_years': education, 'employment': employment,
                'caregiver': caregiver, 'care_hours': care, 'language': language,
                'origin': origin, 'migrant': migrant, 'trust_shift': trust_shift,
                'gender': 'female' if female else 'male', 'ethnicity': ethnicity, 'physical_work': physical,
                'dependents': dependents, 'remote': remote, 'commute_hours': commute,
                'stress': 0., 'fairness': c.initial_fairness, 'identity': c.initial_identity}

    def append(self, record):
        self.people.append(record)

    def acquired(self, rng, token, site=0):
        """Acquired staff keep their recorded care time; other traits are drawn as unobserved."""
        return self.draw(rng, token, site)

    # ---- behavioural paths -------------------------------------------------
    def effort_shift(self, i):
        p = self.people[i]
        return self.c.effort_conscientiousness_coefficient*p['traits']['conscientiousness']-self.c.effort_stress_coefficient*p['stress']

    def cooperation_factor(self, i):
        return max(0., 1+self.c.cooperation_agreeableness_coefficient*self.people[i]['traits']['agreeableness'])

    def participation_shift(self, i, safety):
        p, c = self.people[i], self.c
        return (c.participation_extraversion_coefficient*p['traits']['extraversion']+c.participation_safety_coefficient*(safety-0.5)
                +c.identity_participation_coefficient*(p['identity']-c.initial_identity))

    def decision_noise_factor(self, i, load=0.):
        """Judgment lowers, and concurrent time demands (cognitive load) raise, decision noise."""
        return math.exp(-self.c.judgment_noise_coefficient*self.people[i]['traits']['judgment'])*(1+self.c.load_noise_coefficient*clip(load))

    def learning_factor(self, i):
        t = self.people[i]['traits']
        return max(0., 1+self.c.learning_openness_coefficient*t['openness']+self.c.learning_education_coefficient*t['education'])

    def fatigue_extra(self, i):
        """Static daily fatigue load from physical work capacity and commuting (work days only)."""
        p = self.people[i]
        extra = self.c.commute_fatigue_per_hour*p.get('commute_hours', 0.)
        if p.get('physical_work'):
            extra -= self.c.physical_fatigue_coefficient*p['traits']['physical_capacity']
        return extra

    def help_language_factor(self, i, j):
        if j is None:
            return 1.
        a, b = self.people[i], self.people[j]
        f = 1. if a['language'] == b['language'] else 1-self.c.cross_language_help_discount
        if a.get('ethnicity') != b.get('ethnicity'):
            f *= 1-self.c.cross_ethnic_help_discount
        return f

    def evaluation_shift(self, claimant, reviewer):
        if reviewer is None:
            return 0.
        a, b = self.people[claimant], self.people[reviewer]
        shift = 0. if a['language'] == b['language'] else self.c.cross_group_evaluation_bias
        if a.get('gender') == 'female' and b.get('gender') == 'male':
            shift += self.c.gender_evaluation_bias
        shift += self.c.appearance_evaluation_bias*a['traits'].get('appearance', 0.)
        return shift

    def majority_ethnicity(self):
        return max(self.c.ethnic_groups, key=lambda r: r[1])[0]

    def voice_probability(self, i, safety):
        minority = self.people[i].get('ethnicity', self.majority_ethnicity()) != self.majority_ethnicity()
        return clip(self.c.voice_base_probability+self.c.voice_safety_coefficient*(safety-0.5)+(self.c.minority_voice_shift if minority else 0.))

    def sick(self, i, rng, day, token, workdays):
        """Health-related absence; depends only on the entry witness so it can be independently recomputed."""
        if self.c.annual_sick_days <= 0:
            return False
        p = min(1., self.c.annual_sick_days/max(1, workdays)*math.exp(self.c.sick_health_coefficient*self.people[i]['traits']['health_risk']))
        return rng.uniform('pc_sick', day, token) < p

    def site_capacity(self, i, site, day, month, rng):
        """Climate (physical work only) and institutional-shock capacity multiplier for a site."""
        f = 1.
        c = self.c
        if self.people[i].get('physical_work') and c.site_climate:
            f *= c.site_climate[site % len(c.site_climate)][month-1]
        if c.site_institution_shocks:
            prob, mult, duration = c.site_institution_shocks[site % len(c.site_institution_shocks)]
            daily = 1-(1-prob)**(1/365)
            cache = self._shock_cache
            if cache.get('day') != day:
                cache.clear(); cache['day'] = day
            key = site % len(c.site_institution_shocks)
            if key not in cache:
                cache[key] = any(rng.uniform('pc_institution_shock', key, d) < daily for d in range(max(0, day-duration+1), day+1))
            if cache[key]:
                f *= mult
        return f

    def team_safety(self, ids, agents):
        """Psychological-safety proxy: team mean of trust and perceived fairness (observable to members)."""
        if not ids:
            return 0.5
        return math.fsum((agents[i].trust+self.people[i]['fairness'])/2 for i in ids)/len(ids)

    def annual_quit_probability(self, i):
        c, p = self.c, self.people[i]
        mult = next(r[2] for r in c.employment_mix if r[0] == p['employment'])
        t = p['traits']
        logit_shift = (c.quit_stress_coefficient*p['stress']+c.quit_fairness_coefficient*(c.initial_fairness-p['fairness'])
                       -c.quit_patience_coefficient*t['patience']-c.quit_conscientiousness_coefficient*t['conscientiousness']
                       -c.identity_quit_coefficient*(p.get('identity', c.initial_identity)-c.initial_identity)
                       +c.financial_strain_quit_coefficient*t.get('financial_strain', 0.))
        base = c.baseline_annual_quit_probability*mult*(c.migrant_exit_multiplier if p.get('migrant') else 1.)
        if base <= 0:
            return 0.
        if base >= 1:
            return 1.
        odds = base/(1-base)*math.exp(logit_shift)
        return odds/(1+odds)

    def update_stress(self, i, workload, help_input, autonomy, safety):
        c, p = self.c, self.people[i]
        demand = c.stress_demand_weight*max(0., workload-0.6)
        resource = c.stress_resource_weight*(help_input+autonomy+safety)/3
        sensitivity = max(0., 1+c.stress_neuroticism_coefficient*p['traits']['neuroticism'])
        target = clip(sensitivity*(demand-resource+0.2)+c.financial_strain_stress_coefficient*p['traits'].get('financial_strain', 0.))
        keep = 0.5**(1/c.stress_half_life_workdays)
        p['stress'] = keep*p['stress']+(1-keep)*target

    def rest(self, i):
        p = self.people[i]
        p['stress'] *= 0.5**(1/self.c.stress_half_life_workdays)

    def observe_review(self, i, accepted, honest):
        """Perceived procedural fairness updates from the member's own observable review outcomes."""
        p, r = self.people[i], self.c.fairness_update_rate
        if accepted:
            p['fairness'] += r*(1-p['fairness'])
        elif honest:
            p['fairness'] -= r*p['fairness']
        # Organizational identification follows perceived procedural fairness.
        p['identity'] += self.c.identity_update_rate*(p['fairness']-p['identity'])

    def observe_appeal(self, i, upheld):
        p, r = self.people[i], self.c.fairness_update_rate
        p['fairness'] += (r if upheld else -r)*((1-p['fairness']) if upheld else p['fairness'])

    def witness(self, i):
        p = self.people[i]
        return {'version': VERSION, 'traits': p['traits'], 'education_years': p['education_years'],
                'employment': p['employment'], 'caregiver': p['caregiver'], 'care_hours': p['care_hours'],
                'language': p['language'], 'origin': p['origin'], 'migrant': p['migrant'], 'trust_shift': p['trust_shift'],
                'gender': p['gender'], 'ethnicity': p['ethnicity'], 'physical_work': p['physical_work'],
                'dependents': p['dependents'], 'remote': p['remote'], 'commute_hours': p['commute_hours']}

    def group_summary(self, ids, agents):
        """Formal-share and stress/fairness means by caregiver role, language group and contract."""
        out = {}
        for key in ('caregiver', 'language', 'employment', 'gender', 'origin', 'migrant', 'ethnicity', 'physical_work', 'remote'):
            groups = {}
            for i in ids:
                groups.setdefault(str(self.people[i][key]), []).append(i)
            out[key] = {g: {'members': len(v), 'mean_share': math.fsum(agents[i].share for i in v)/len(v),
                            'mean_stress': math.fsum(self.people[i]['stress'] for i in v)/len(v),
                            'mean_fairness': math.fsum(self.people[i]['fairness'] for i in v)/len(v)}
                        for g, v in sorted(groups.items())}
        return out

    def to_dict(self):
        return {'version': VERSION, 'people': self.people}

    @classmethod
    def restore(cls, config, value):
        if not isinstance(value, dict) or value.get('version') != VERSION or not isinstance(value.get('people'), list):
            raise ValueError('person context state differs')
        obj = cls(config)
        obj.people = value['people']
        return obj

    def factions(self, ids, agents, guild):
        """Informal factions: consecutive blocks of the guild roster sorted by (origin, ethnicity);
        the leader is the member with the highest extraversion plus tenure."""
        if self.c.faction_size <= 0 or self.c.faction_cohesion <= 0:
            return {}
        order = sorted(ids, key=lambda i: (str(self.people[i].get('origin')), str(self.people[i].get('ethnicity')), i))
        leader = {}
        for k in range(0, len(order), self.c.faction_size):
            block = order[k:k+self.c.faction_size]
            head = max(block, key=lambda i: (self.people[i]['traits']['extraversion']+agents[i].tenure, -i))
            for i in block:
                leader[i] = head
        return leader

    def gaps(self, ids, agents):
        """Mean formal-share differences between groups (numeric; positive favours the first-named group)."""
        def gap(key, a, b):
            ga = [agents[i].share for i in ids if self.people[i].get(key) == a]
            gb = [agents[i].share for i in ids if self.people[i].get(key) == b]
            return math.fsum(ga)/len(ga)-math.fsum(gb)/len(gb) if ga and gb else None
        maj = self.majority_ethnicity()
        minority = [agents[i].share for i in ids if self.people[i].get('ethnicity') != maj]
        majority = [agents[i].share for i in ids if self.people[i].get('ethnicity') == maj]
        return {'share_gap_female_minus_male': gap('gender', 'female', 'male'),
                'share_gap_migrant_minus_local': gap('migrant', True, False),
                'share_gap_caregiver_minus_other': gap('caregiver', True, False),
                'share_gap_minority_minus_majority': (math.fsum(minority)/len(minority)-math.fsum(majority)/len(majority)) if minority and majority else None}
