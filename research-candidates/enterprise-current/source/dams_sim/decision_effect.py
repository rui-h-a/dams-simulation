"""Opt-in channel from decision quality to operations (research arms only).

Without this block a guild's daily binary proposal decision only produces a regret
figure that is reported and never feeds back.  With a `decision_effect` block every
guild carries a decision-quality debt D in [0, 1]:

  * after each workday on which the guild decided,
        D <- D + (min(1, loss / reference_loss) - D) / memory_workdays
    where `loss` is the journalled decision loss (|theta| for a wrong or unresolved
    decision, else 0) and `reference_loss` the loss of a decision that is always wrong;
  * on the next workday every member of the guild has potential output multiplied by
        1 - gamma * D
    using the value held in the morning, before that day's decision.

The block is absent by default; every earlier arm keeps its exact behaviour, its random
streams and its configuration digest.  All three numbers are declared design assumptions.
"""
from __future__ import annotations

import dataclasses
import math

VERSION = 'decision-effect-1'


@dataclasses.dataclass(frozen=True)
class DecisionEffectConfig:
    schema_version: int = 1
    # Share of a guild's output that depends on decision quality (declared design assumption).
    gamma: float = 0.05
    # Workdays over which the decision-quality debt moves to the loss of the latest decision
    # (declared design assumption).
    memory_workdays: int = 20
    # E|theta| for a unit-normal proposal value, sqrt(2/pi): the loss of a decision that is always wrong
    # (a mathematical constant of the declared proposal distribution, not a free parameter).
    reference_loss: float = 0.7978845608

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, cls):
            return value.validate()
        if not isinstance(value, dict):
            raise ValueError('decision_effect configuration must be an object')
        unknown = set(value)-{f.name for f in dataclasses.fields(cls)}
        if unknown:
            raise ValueError('unknown decision_effect fields: '+str(sorted(unknown)))
        return cls(**value).validate()

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('unknown decision_effect schema')
        for name, low, high in (('gamma', 0., 1.), ('reference_loss', 1e-9, 1e6)):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
                raise ValueError(f'{name} must be finite in [{low}, {high}]')
        if type(self.memory_workdays) is not int or not 1 <= self.memory_workdays <= 100_000:
            raise ValueError('memory_workdays must be an integer in [1, 100000]')
        return self


def update_debt(config, debt, loss):
    """One workday's update of a guild's decision-quality debt after a decision with `loss`."""
    return debt+(min(1., loss/config.reference_loss)-debt)/config.memory_workdays


def output_factor(config, debt):
    """Multiplier of potential output for members of a guild holding the morning debt `debt`."""
    return 1-config.gamma*debt


def merge_debt(debt_source, n_source, debt_target, n_target):
    """Debt of the successor of a merged guild: member-weighted mean (plain mean for two empty guilds),
    the rule the engine uses for organizational memory and routine."""
    total = n_source+n_target
    return (debt_source*n_source+debt_target*n_target)/total if total else (debt_source+debt_target)/2
