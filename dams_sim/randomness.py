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
    def __init__(self, seed: int, world: int):
        self.seed, self.world = seed, world

    def uniform(self, process: str, *identity: int | str) -> float:
        key = json.dumps([self.seed, self.world, process, *identity], separators=(",", ":")).encode()
        word = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") >> 11
        return (word + 0.5) / (1 << 53)

    def normal(self, process: str, *identity: int | str) -> float:
        u = self.uniform(process, *identity, 0)
        v = self.uniform(process, *identity, 1)
        return math.sqrt(-2*math.log(u)) * math.cos(2*math.pi*v)
