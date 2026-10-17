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

VERSION = 'longitudinal-enterprise-person-context-11'
TRAITS = ('conscientiousness', 'agreeableness', 'neuroticism', 'openness',
          'extraversion', 'risk_tolerance', 'patience', 'judgment', 'education')


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


def clip(x, low=0., high=1.):
    return max(low, min(high, x))


class PersonContextState:
    """Per-person latent traits and dynamic stress/fairness, aligned with agent ids."""

    def __init__(self, config):
        self.c = config.person_context
        self.lower = cholesky(self.c.matrix())
        self.people = []

    def draw(self, rng, token):
        c = self.c
        z = [rng.normal('pc_trait', token, k) for k in range(len(TRAITS))]
        x = [math.fsum(self.lower[i][k]*z[k] for k in range(i+1)) for i in range(len(TRAITS))]
        traits = dict(zip(TRAITS, x))
        employment = _pick(c.employment_mix, rng.uniform('pc_employment', token))[0]
        caregiver = rng.uniform('pc_caregiver', token) < c.caregiver_share
        mean = c.caregiver_care_hours if caregiver else c.other_care_hours
        care = clip(mean+c.care_hours_sd*rng.normal('pc_care', token), 0., 0.6)
        language = _pick(c.language_groups, rng.uniform('pc_language', token))[0]
        education = max(0., c.education_mean_years+c.education_sd_years*traits['education'])
        return {'traits': traits, 'education_years': education, 'employment': employment,
                'caregiver': caregiver, 'care_hours': care, 'language': language,
                'stress': 0., 'fairness': c.initial_fairness}

    def append(self, record):
        self.people.append(record)

    def acquired(self, rng, token):
        """Acquired staff keep their recorded care time; other traits are drawn as unobserved."""
        return self.draw(rng, token)

    # ---- behavioural paths -------------------------------------------------
    def effort_shift(self, i):
        p = self.people[i]
        return self.c.effort_conscientiousness_coefficient*p['traits']['conscientiousness']-self.c.effort_stress_coefficient*p['stress']

    def cooperation_factor(self, i):
        return max(0., 1+self.c.cooperation_agreeableness_coefficient*self.people[i]['traits']['agreeableness'])

    def participation_shift(self, i, safety):
        p = self.people[i]
        return self.c.participation_extraversion_coefficient*p['traits']['extraversion']+self.c.participation_safety_coefficient*(safety-0.5)

    def decision_noise_factor(self, i):
        return math.exp(-self.c.judgment_noise_coefficient*self.people[i]['traits']['judgment'])

    def learning_factor(self, i):
        return max(0., 1+self.c.learning_openness_coefficient*self.people[i]['traits']['openness'])

    def help_language_factor(self, i, j):
        if j is None or self.people[i]['language'] == self.people[j]['language']:
            return 1.
        return 1-self.c.cross_language_help_discount

    def evaluation_shift(self, claimant, reviewer):
        if reviewer is None or self.people[claimant]['language'] == self.people[reviewer]['language']:
            return 0.
        return self.c.cross_group_evaluation_bias

    def voice_probability(self, i, safety):
        return clip(self.c.voice_base_probability+self.c.voice_safety_coefficient*(safety-0.5))

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
                       -c.quit_patience_coefficient*t['patience']-c.quit_conscientiousness_coefficient*t['conscientiousness'])
        base = c.baseline_annual_quit_probability*mult
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
        target = clip(sensitivity*(demand-resource+0.2))
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

    def observe_appeal(self, i, upheld):
        p, r = self.people[i], self.c.fairness_update_rate
        p['fairness'] += (r if upheld else -r)*((1-p['fairness']) if upheld else p['fairness'])

    def witness(self, i):
        p = self.people[i]
        return {'version': VERSION, 'traits': p['traits'], 'education_years': p['education_years'],
                'employment': p['employment'], 'caregiver': p['caregiver'], 'care_hours': p['care_hours'],
                'language': p['language']}

    def group_summary(self, ids, agents):
        """Formal-share and stress/fairness means by caregiver role, language group and contract."""
        out = {}
        for key in ('caregiver', 'language', 'employment'):
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
