"""Versioned scientific designs; resources never silently change a design."""
from __future__ import annotations
import dataclasses
import math
from .config import Config
from .storage import canonical, digest

RESOURCE_FIELDS = ('max_wall_seconds', 'max_output_mb', 'max_rss_mb', 'max_events')

@dataclasses.dataclass(frozen=True)
class ScientificSpec:
    name: str
    n: int
    days: int
    pilot_worlds: int
    confirmation_min: int
    confirmation_max: int
    exploratory_worlds: int
    trajectories: int
    trajectory_worlds: int
    recovery_train_worlds: int
    recovery_holdout_worlds: int
    backends: tuple[str, ...]
    cadences: tuple[int, ...]
    stages: tuple[str, ...]
    schema_version: int = 2
    seed: int = 20261007
    guilds: int = 4
    sites: int = 2
    team_size: int = 5
    mc_target_halfwidth: float = .01
    substantive_threshold: float = .02
    inferential_scope: str = "conditional fixed-model Monte Carlo comparison"

    def to_dict(self): return dataclasses.asdict(self)
    @property
    def sha256(self): return digest(canonical(self.to_dict()))
    def base(self, **resources):
        return Config(n=self.n, days=self.days, guilds=self.guilds, sites=self.sites,
                      team_size=self.team_size, seed=self.seed, trace_every_days=1, **resources).validate()
    def confirmation_count(self, paired_sd):
        requested = math.ceil((1.959963984540054*paired_sd/self.mc_target_halfwidth)**2)
        return min(self.confirmation_max, max(self.confirmation_min, requested)), requested


def resolve_spec(name: str, scale: int | None = None) -> ScientificSpec:
    all_stages = ('pilot', 'confirmation', 'mechanisms', 'stress', 'sensitivity', 'scenarios', 'extended', 'recovery')
    if name == 'full-study':
        spec = ScientificSpec(name, 120, 60, 4, 32, 64, 8, 8, 3, 8, 4,
                              ('central','witness','consensus'), (1,14), all_stages)
    elif name == 'validation':
        # Explicit reduced design exercises every stage. This is not confirmation
        # at the full-study precision and cannot substitute for a formal batch.
        spec = ScientificSpec(name, 24, 30, 2, 2, 2, 2, 1, 2, 2, 2,
                              ('central','witness','consensus'), (1,14), all_stages)
    elif name == 'scale-confirmation':
        spec = ScientificSpec(name, 10000, 30, 0, 8, 8, 0, 0, 0, 0, 0,
                              ('central',), (1,), ('confirmation',))
    elif name == 'governance-scale':
        # A distinct preregistered governance scaling study: all five allocation
        # rules, daily central records, eight independent primary worlds; two
        # exploratory paired worlds per equal-cost attack and shock contrast.
        spec = ScientificSpec(name, 10000, 30, 4, 8, 16, 2, 0, 0, 0, 0,
                              ('central',), (1,), ('pilot','confirmation','stress','extended'))
    else:
        raise ValueError('unknown scientific spec: '+name)
    if scale is not None:
        if type(scale) is not int or not 12 <= scale <= 10_000_000:
            raise ValueError('scale must be integer people in [12,10000000]')
        spec = dataclasses.replace(spec, n=scale)
    if name == 'scale-confirmation' and spec.n == 10_000_000:
        spec = dataclasses.replace(spec, confirmation_min=2, confirmation_max=2,
                                  inferential_scope='exploratory two paired worlds; inadequate for confirmatory precision; do not pool across scales')
    return spec


def scientific_config(config: Config) -> dict:
    return {k:v for k,v in config.to_dict().items() if k not in RESOURCE_FIELDS}


def case_key(config: Config) -> str:
    return digest(canonical(scientific_config(config)))


def workload(config: Config) -> dict:
    """Conservative exact-reference bounds, not hardware certification.

    max_events remains the Config work-creation bound. The scheduler separately
    accounts for voting, claim creation, review/appeal and commitment operations.
    Duplicate submissions and fallible appeals are included in the upper bound.
    RAM includes population/topology, intra-day snapshots/heaps, retained IDs,
    delay lists, JSON restore, and serialization sorting; measured RSS follows.
    """
    n, d = config.n, config.days
    return {'work_events': n*d, 'operation_upper_bound': (12*n+4*config.guilds)*d,
            'estimated_peak_rss_bytes': 64_000_000+n*(12_000+400*d),
            'estimated_output_bytes': 1_000_000+n*(2200+40*d),
            'estimate_kind': 'conservative preflight design bound; requires measured validation'}
