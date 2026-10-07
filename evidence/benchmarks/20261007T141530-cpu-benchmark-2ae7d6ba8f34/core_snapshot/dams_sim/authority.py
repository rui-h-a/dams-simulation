"""Formal authority shares. No function claims to measure actual influence or justice."""
from __future__ import annotations

import math
from collections.abc import Sequence


def nonnegative(values: Sequence[float]) -> list[float]:
    x = [float(v) for v in values]
    if not x:
        raise ValueError("eligible set is empty: no decision can be made")
    if any(not math.isfinite(v) or v < 0 for v in x):
        raise ValueError("contributions must be finite and nonnegative")
    return x


def authority(contribution: Sequence[float], alpha: float, *, zero_policy: str) -> list[float]:
    """a_i=C_i**alpha/sum(C_j**alpha); alpha=0 is explicitly equal eligibility.

    All-zero policy must be chosen by the caller. Log scaling avoids overflow
    at finite extreme inputs, and zero contributions stay exactly zero for alpha>0.
    """
    x = nonnegative(contribution)
    if not math.isfinite(alpha) or alpha < 0:
        raise ValueError("alpha must be finite and >= 0")
    if zero_policy not in {"equal", "reject"}:
        raise ValueError("zero_policy must be equal or reject")
    if alpha == 0:
        return [1 / len(x)] * len(x)
    if max(x) == 0:
        if zero_policy == "reject":
            raise ValueError("all-zero contribution: policy suspends the decision")
        return [1 / len(x)] * len(x)
    maximum = max(x)
    # log(x/max) cannot overflow even if x is subnormal and max is near DBL_MAX.
    logs = [math.log(v) - math.log(maximum) if v else -math.inf for v in x]
    weights = [math.exp(alpha * v) if v != -math.inf else 0.0 for v in logs]
    total = math.fsum(weights)
    return [v / total for v in weights]


def tier_authority(score: Sequence[float], weights: Sequence[float]) -> list[float]:
    """Equal-sized rank tiers, with average positional weights for exact ties.

    Tier headcounts and weights are design parameters, not an empirical hierarchy.
    A tie spanning boundaries has no ID-dependent privilege.
    """
    x = nonnegative(score)
    w = nonnegative(weights)
    if min(w) <= 0 or any(b < a for a, b in zip(w, w[1:])):
        raise ValueError("tier weights must be positive and nondecreasing")
    n = len(x)
    order = sorted(range(n), key=x.__getitem__)
    out = [0.0] * n
    start = 0
    while start < n:
        stop = start + 1
        while stop < n and x[order[stop]] == x[order[start]]:
            stop += 1
        tied = math.fsum(w[min(len(w)-1, r * len(w) // n)] for r in range(start, stop)) / (stop-start)
        for r in range(start, stop):
            out[order[r]] = tied
        start = stop
    total = math.fsum(out)
    return [v / total for v in out]


def cap_shares(shares: Sequence[float], cap: float) -> list[float]:
    """Proportional water filling; infeasible caps fail instead of silently changing."""
    x = nonnegative(shares)
    if not math.isfinite(cap) or cap <= 0 or cap > 1 or cap * len(x) < 1-1e-12:
        raise ValueError("share cap is infeasible")
    remaining = set(range(len(x)))
    out = [0.0] * len(x)
    mass = 1.0
    while remaining:
        total = math.fsum(x[i] for i in remaining)
        proposed = {i: mass * x[i] / total if total else mass / len(remaining) for i in remaining}
        capped = [i for i in remaining if proposed[i] > cap]
        if not capped:
            for i in remaining:
                out[i] = proposed[i]
            break
        for i in capped:
            out[i] = cap
            remaining.remove(i)
            mass -= cap
    return out


def total_variation(a: Sequence[float], b: Sequence[float]) -> float:
    x, y = nonnegative(a), nonnegative(b)
    if len(x) != len(y) or not math.isclose(math.fsum(x), 1, abs_tol=1e-10) or not math.isclose(math.fsum(y), 1, abs_tol=1e-10):
        raise ValueError("TV requires equal-length normalized distributions")
    return math.fsum(abs(u-v) for u, v in zip(x, y)) / 2


def gini(values: Sequence[float]) -> float:
    x = sorted(nonnegative(values))
    if not x[-1]:
        return 0.0
    # Scaling prevents overflow without changing Gini.
    x = [v/x[-1] for v in x]
    n = len(x)
    return math.fsum((2*i-n-1)*v for i, v in enumerate(x, 1)) / (n*math.fsum(x))


def signed_concentration_gap(a: Sequence[float], contribution: Sequence[float]) -> float:
    return gini(a) - gini(contribution)


def split_gain(contribution: Sequence[float], actor: int, accounts: int, alpha: float) -> dict[str, float]:
    x = nonnegative(contribution)
    if not 0 <= actor < len(x) or not isinstance(accounts, int) or accounts < 1 or x[actor] == 0:
        raise ValueError("positive actor contribution and >=1 accounts required")
    before = authority(x, alpha, zero_policy="equal")[actor]
    others = x[:actor] + x[actor+1:]
    after = math.fsum(authority([x[actor]/accounts]*accounts + others, alpha, zero_policy="equal")[:accounts])
    return {"authority_before": before, "authority_after": after, "within_regime_gain": after/before}
