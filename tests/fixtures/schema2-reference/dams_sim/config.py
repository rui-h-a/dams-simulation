"""Strict, bounded configuration. Days are the simulation time unit."""
from __future__ import annotations

import dataclasses
import math
from .longitudinal import LongitudinalConfig


@dataclasses.dataclass(frozen=True)
class Config:
    n: int = 120
    days: int = 60
    guilds: int = 4
    team_size: int = 5
    sites: int = 2
    seed: int = 42
    world: int = 0
    regime: str = "sublinear"
    alpha: float = 0.8
    update_interval_days: int = 1
    hierarchy_weights: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
    hierarchy_basis: str = "performance"
    decay_per_day: float = 0.01
    review_capacity_per_member_day: float = 0.9
    appeal_capacity_per_member_day: float = 0.1
    appeal_delay_days: int = 3
    review_error_sd: float = 0.2
    decision_noise_sd: float = 0.8
    shared_signal_sd: float = 0.2
    cooperation_strength: float = 0.3
    autonomy_response: float = 0.25
    behavior_rule: str = "linear_response"
    learning_rate: float = 0.01
    backend: str = "central"
    backend_review_multipliers: tuple[float, ...] = (1.0, 1.2, 1.5)
    backend_settlement_days: tuple[int, ...] = (1, 2, 3)
    administrative_censorship_exposure: tuple[float, ...] = (1.0, 0.5, 0.0)
    quorum_unavailable_start_day: int = 0
    quorum_unavailable_stop_day: int = 0
    attack: str = "none"
    attack_budget_hours_per_day: float = 2.0
    attack_cohort_fraction: float = 0.5
    attack_start_day: int = 15
    attack_stop_day: int = 25
    fault_start_day: int = 25
    fault_stop_day: int = 30
    trace_every_days: int = 5
    max_wall_seconds: float = 300.0
    max_output_mb: float = 100.0
    max_rss_mb: float = 2048.0
    max_events: int = 2_000_000
    longitudinal: LongitudinalConfig | None = None

    def validate(self) -> Config:
        if self.longitudinal is not None and not isinstance(self.longitudinal, LongitudinalConfig):
            raise ValueError('longitudinal must be a LongitudinalConfig; use Config.from_dict for JSON')
        horizon_limit = 50_000 if self.longitudinal is not None else 3650
        integers = {"n": (2, 10_000_000), "days": (1, 3650), "guilds": (1, self.n), "team_size": (1, self.n), "sites": (1, self.n), "seed": (0, 2**63-1), "world": (0, 2**63-1), "update_interval_days": (1, self.days), "appeal_delay_days": (1, 3650), "attack_start_day": (0, 3650), "attack_stop_day": (0, 3650), "fault_start_day": (0, 3650), "fault_stop_day": (0, 3650), "quorum_unavailable_start_day": (0,3650), "quorum_unavailable_stop_day": (0,3650), "trace_every_days": (1, 3650), "max_events": (1,1_000_000_000_000)}
        if self.longitudinal is not None:
            for name in ('days','appeal_delay_days','attack_start_day','attack_stop_day','fault_start_day','fault_stop_day','quorum_unavailable_start_day','quorum_unavailable_stop_day','trace_every_days'):
                integers[name] = (integers[name][0], horizon_limit)
        for name, (low, high) in integers.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be integer in [{low}, {high}]")
        if self.regime not in {"equal", "linear", "sublinear", "hierarchy", "hierarchy_tenure"}:
            raise ValueError("unknown authority regime")
        if self.backend not in {"central", "witness", "consensus"}:
            raise ValueError("unknown stylized evidence backend")
        if self.attack not in {"none", "freeride", "forge", "duplicate", "censor"}:
            raise ValueError("unknown attack")
        if self.hierarchy_basis not in {"performance", "tenure"}:
            raise ValueError("hierarchy_basis must be performance or tenure")
        if self.behavior_rule not in {"linear_response", "satisficing", "reinforcement"}:
            raise ValueError("unknown behavior_rule")
        bounded = {"alpha": (0.001, 0.999), "decay_per_day": (0, 1), "review_capacity_per_member_day": (0.001, 10), "appeal_capacity_per_member_day": (0, 2), "review_error_sd": (0, 5), "decision_noise_sd": (0, 5), "shared_signal_sd": (0,5), "cooperation_strength": (0, 2), "autonomy_response": (-2, 2), "learning_rate": (0, 1), "attack_budget_hours_per_day": (0, self.n), "max_wall_seconds": (0.1, 86400), "max_output_mb": (0.1, 10_000_000), "max_rss_mb": (1,4_000_000)}
        for name, (low, high) in bounded.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be finite in [{low}, {high}]")
        if isinstance(self.attack_cohort_fraction, bool) or not isinstance(self.attack_cohort_fraction, (int, float)) or not math.isfinite(self.attack_cohort_fraction) or not 0 < self.attack_cohort_fraction <= 1:
            raise ValueError("attack_cohort_fraction must be finite in (0,1]")
        if self.attack == "censor" and math.ceil(self.n * self.attack_cohort_fraction) >= self.n:
            raise ValueError("censor requires a nonattacking target population")
        if self.attack_start_day > self.attack_stop_day or self.fault_start_day > self.fault_stop_day or self.quorum_unavailable_start_day > self.quorum_unavailable_stop_day:
            raise ValueError("event starts must not exceed stops")
        for name in ("backend_review_multipliers", "backend_settlement_days", "administrative_censorship_exposure"):
            values = getattr(self, name)
            if len(values) != 3 or any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) for v in values):
                raise ValueError(f"{name} must have three finite values in central/witness/consensus order")
        if min(self.backend_review_multipliers) < 1 or max(self.backend_review_multipliers) > 100:
            raise ValueError("backend multipliers must be in [1,100]")
        if min(self.administrative_censorship_exposure) < 0 or max(self.administrative_censorship_exposure) > 1:
            raise ValueError("censorship exposure must be in [0,1]")
        if any(type(v) is not int or not 1 <= v <= 3650 for v in self.backend_settlement_days):
            raise ValueError("settlement delays must be integer days in [1,3650]")
        if 0.1*self.review_capacity_per_member_day + 0.15*self.appeal_capacity_per_member_day > 0.6:
            raise ValueError("review/appeal reservations exceed the daily time budget")
        if self.longitudinal is not None:
            self.longitudinal.validate(self.n, self.days, self.guilds)
            from .longitudinal import estimate_longitudinal
            if estimate_longitudinal(self)['work_events'] > self.max_events:
                raise ValueError('declared longitudinal work-event capacity exceeds max_events; explicitly revise the resource limit')
        if self.longitudinal is None and self.n*self.days > self.max_events:
            raise ValueError("declared work events exceed max_events; explicitly revise resource limit")
        from .authority import tier_authority
        tier_authority([0, 1], self.hierarchy_weights)
        return self

    def to_dict(self) -> dict:
        value = dataclasses.asdict(self)
        if self.longitudinal is None:
            value.pop('longitudinal')
        return value

    @classmethod
    def from_dict(cls, value: dict) -> Config:
        unknown = set(value) - {f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError(f"unknown configuration keys: {sorted(unknown)}")
        value = dict(value)
        if value.get('longitudinal') is not None:
            value['longitudinal'] = LongitudinalConfig.from_dict(value['longitudinal'])
        for name in ("hierarchy_weights", "backend_review_multipliers", "backend_settlement_days", "administrative_censorship_exposure"):
            if name in value:
                value[name] = tuple(value[name])
        return cls(**value).validate()
