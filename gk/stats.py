"""Paired statistics for the gate.

sign_flip_test: one-sided paired sign-flip (randomisation) permutation test of "mean difference > 0".
Under the null the sign of each paired difference is arbitrary, so every one of the 2^n sign
patterns is equally likely. For n <= 20 ALL patterns are enumerated (exactly, via a dynamic
programme over the distinct achievable sums); for larger n, 20,000 seeded random flips are used.
The 95% percentile-bootstrap interval is for DISPLAY only; decisions use the p-value.
"""
from __future__ import annotations

import random

EXACT_MAX_N = 20
N_RANDOM = 20000
N_BOOT = 4000
EPS = 1e-9


def _exact_p(diffs: list[float]) -> float:
    """P(sum of randomly signed diffs >= observed sum), over all 2^n sign patterns."""
    observed = sum(diffs)
    dist = {0.0: 1}
    for d in diffs:
        nxt: dict = {}
        for s, c in dist.items():
            for v in (round(s + d, 9), round(s - d, 9)):
                nxt[v] = nxt.get(v, 0) + c
        dist = nxt
    hits = sum(c for s, c in dist.items() if s >= observed - EPS)
    return hits / 2 ** len(diffs)


def _random_p(diffs: list[float], seed: int) -> float:
    rng = random.Random(seed)
    observed = sum(diffs)
    hits = 1   # the observed pattern itself counts
    for _ in range(N_RANDOM):
        s = sum(d if rng.random() < 0.5 else -d for d in diffs)
        hits += s >= observed - EPS
    return hits / (N_RANDOM + 1)


def bootstrap_ci(diffs: list[float], n: int = N_BOOT, alpha: float = 0.05, seed: int = 0) -> tuple:
    rng, k = random.Random(seed), len(diffs)
    means = sorted(sum(diffs[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return means[int(alpha / 2 * n)], means[int((1 - alpha / 2) * n) - 1]


def sign_flip_test(diffs: list[float], seed: int = 0) -> dict:
    n = len(diffs)
    if n == 0:
        return {"n": 0, "mean": 0.0, "p": 1.0, "ci": (0.0, 0.0), "method": "none"}
    mean = sum(diffs) / n
    if n <= EXACT_MAX_N:
        p, method = _exact_p(diffs), f"exact ({2 ** n} sign patterns)"
    else:
        p, method = _random_p(diffs, seed), f"{N_RANDOM} random sign flips"
    return {"n": n, "mean": mean, "p": p, "ci": bootstrap_ci(diffs, seed=seed), "method": method}
