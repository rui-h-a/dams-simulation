"""Counter-based random numbers keyed by world, entity, event and process.

    No mutable RNG cursor: policy branching, entity iteration order and restart
    cannot change exogenous values. SHA-256 is used for stable integer mapping,
    not as a cryptographic protocol or an empirical behavioural model.
"""
from __future__ import annotations

import hashlib
import json
import math


class WorldRandom:
    def __init__(self, seed: int, world: int, *, world_context: str | None = None):
        if world_context is not None and (not isinstance(world_context, str)
                or not world_context or len(world_context) > 256):
            raise ValueError('world_context must be a nonempty string of at most 256 characters')
        self.seed, self.world = seed, world
        self.world_context = world_context

    def uniform(self, process: str, *identity: int | str) -> float:
        if not isinstance(process, str):
            raise ValueError("random process must be a string")
        fields = [self.seed, self.world, process, *identity]
        if self.world_context is not None:
            # An object occupies the frame position where legacy keys have a
            # process string, so variable-length identities cannot alias frames.
            fields = [self.seed, self.world,
                      {'world_context_version': 1, 'world_context': self.world_context},
                      process, *identity]
        if self.world_context is not None and any(type(value) not in (int, str)
                                                   for value in identity):
            raise ValueError("scoped random identities must be integers or strings")
        key = json.dumps(fields, separators=(",", ":")).encode()
        word = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") >> 11
        value = (word + 0.5) / (1 << 53)
        # Binary64 rounds the largest integer midpoint to 1.0. Preserve the
        # historical unscoped mapping, but keep the new scoped stream inside
        # its declared open unit interval even at that exact endpoint.
        if self.world_context is not None:
            return min(value, math.nextafter(1.0, 0.0))
        return value

    def normal(self, process: str, *identity: int | str) -> float:
        u = self.uniform(process, *identity, 0)
        v = self.uniform(process, *identity, 1)
        return math.sqrt(-2*math.log(u)) * math.cos(2*math.pi*v)
