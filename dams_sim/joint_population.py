"""Opt-in Gaussian-copula entry assumptions for existing behavioral state.

These coefficients are NOT calibrated population facts. No demographic labels
are read. The synthetic latent skill multiplier, observed appointment tenure and
care-time constraint have their legacy marginals; only their joint dependence
changes. Recruits have zero appointment tenure, and transferred M&A personnel
retain their original profiles. No new inert agent attribute is introduced.
"""
from __future__ import annotations

import dataclasses
import math
from statistics import NormalDist

from .storage import canonical, digest

VERSION = 'longitudinal-enterprise-integrated-6-joint-1'
ALGORITHM = 'conditional-gaussian-copula-entry-1'
NORMAL = NormalDist()


@dataclasses.dataclass(frozen=True)
class JointPopulationConfig:
    schema_version: int = 1
    skill_tenure_latent_correlation: float = 0.
    skill_care_latent_correlation: float = 0.
    tenure_care_latent_correlation: float = 0.

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('unknown joint population schema')
        values = self.correlations()
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not -1 < value < 1:
                raise ValueError('joint latent correlations must be finite in (-1,1)')
        st, sc, tc = values
        determinant = 1 + 2*st*sc*tc - st*st - sc*sc - tc*tc
        # Reject, rather than silently repair, nearly singular assumptions.
        if 1-st*st <= 1e-8 or determinant <= 1e-8:
            raise ValueError('joint correlation matrix must be positive definite with numerical margin')
        return self

    def correlations(self):
        return (self.skill_tenure_latent_correlation,
                self.skill_care_latent_correlation,
                self.tenure_care_latent_correlation)

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError('joint population configuration must be an object')
        unknown = set(value) - {f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError('unknown joint population fields: '+str(sorted(unknown)))
        return cls(**value).validate()


def _quantile(value):
    # The unscoped legacy midpoint mapper can round its largest midpoint to 1.
    # Move only an exactly represented endpoint to the adjacent open-interval
    # float for inverse-CDF evaluation; do not change the legacy RNG or zero path.
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('joint inverse-normal input outside unit interval')
    if value == 0.: value = math.nextafter(0., 1.)
    if value == 1.: value = math.nextafter(1., 0.)
    return NORMAL.inv_cdf(value)


def draw_entry_traits(config, rng, token, *, initial):
    config.validate()
    if type(initial) is not bool or not isinstance(token, str) or not token:
        raise ValueError('joint entry requires a stable person token and Boolean initial flag')
    st, sc, tc = config.correlations()
    skill_z = rng.normal('skill', token)
    tenure_u = rng.uniform('tenure', token) if initial else 0.
    care_u = rng.uniform('care', token)
    if (st, sc, tc) != (0., 0., 0.):
        care_z = _quantile(care_u)
        if initial:
            tenure_z = _quantile(tenure_u)
            residual = math.sqrt(1-st*st)
            cross = (tc-st*sc)/residual
            conditional_care = sc*skill_z + cross*tenure_z + math.sqrt(1-sc*sc-cross*cross)*care_z
            tenure_u = NORMAL.cdf(st*skill_z + residual*tenure_z)
        else:
            # Conditioning on entrant status sets appointment tenure to zero;
            # the two-dimensional skill/care marginal retains the declared sc.
            conditional_care = sc*skill_z + math.sqrt(1-sc*sc)*care_z
        care_u = NORMAL.cdf(conditional_care)
    return {'skill': math.exp(.4*skill_z), 'tenure': tenure_u,
            'care_hours': .3*care_u, 'autonomy': rng.uniform('autonomy', token),
            'reciprocity': rng.uniform('reciprocity', token)}


def generation_witness(config, traits, *, initial):
    return {'schema_version': 1, 'algorithm': ALGORITHM, 'initial': initial,
            'configuration_sha256': digest(canonical(config.to_dict())),
            'entry_traits': dict(traits)}


def verify_joint_person_state(config, people, agents, day, clock, *, acquired_ids=()):
    """Check immutable draws and appointment-time accumulation on recovery.

    This is an engineering invariant, not an independently calibrated model.
    Full raw entry evidence is separately checked by the publication validator.
    """
    c = config.joint_population
    if c is None: return
    from .randomness import WorldRandom
    rng = WorldRandom(config.seed, config.world, world_context=config.longitudinal.world_context)
    acquired_ids = set(acquired_ids)
    # O(T+N), rather than reconstructing T calendar increments for every person.
    prefix = [0.]
    for when in range(day): prefix.append(prefix[-1]+1/clock.days_in_year(when))
    for i, (person, agent) in enumerate(zip(people, agents)):
        if i in acquired_ids: continue
        if 'source_company' in person:
            raise ValueError('joint population cannot exempt an unrecognized acquisition identity')
        initial = i < config.n
        traits = draw_entry_traits(c, rng, person['token'], initial=initial)
        for field in ('skill', 'care_hours', 'autonomy', 'reciprocity'):
            if getattr(agent, field) != traits[field]:
                raise ValueError('joint generated immutable trait differs: '+field)
        start = person['entered_day']
        end = day if person['exited_day'] is None else person['exited_day']
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= day:
            raise ValueError('joint appointment interval differs')
        expected = traits['tenure'] + prefix[end] - prefix[start]
        if not math.isclose(agent.tenure, expected, rel_tol=1e-12, abs_tol=1e-10):
            raise ValueError('joint appointment tenure differs from retained calendar')
