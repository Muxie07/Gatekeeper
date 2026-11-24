"""The promotion gate: the part of the project that matters.

A candidate edit is promoted only if ALL of these pass, checked in this order:
  1 allowlist   : only prompt/memory/tools may change (never the evaluator)
  2 held-out    : paired bootstrap lower bound of the gain on UNSEEN seeds > 0
  3 integrity   : the agent's claimed success may not drift from verified success
  4 canary      : hidden tasks must not get worse
  5 cost        : token growth must be small, unless the gain is large
After promotion, a regression suite replays an older skill; a drop => rollback.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .evaluator import Evaluator, true_score
from .harness import Harness


@dataclass
class GateConfig:
    allowed_paths: tuple = ("prompt.md", "memory.json", "tools.json")
    min_gain: float = 0.01
    big_gain: float = 0.05
    max_cost_growth: float = 0.10
    relaxed_cost_growth: float = 0.30
    canary_tol: float = 0.03
    integrity_gap_tol: float = 0.10
    regression_tol: float = 0.03
    n_boot: int = 2000
    alpha: float = 0.05
    enabled: frozenset = frozenset({"allowlist", "heldout", "integrity", "canary",
                                    "cost", "regression", "placebo"})  # switch off to ablate


@dataclass
class Decision:
    accepted: bool
    reason: str
    metrics: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)   # [{name, pass, value, detail}] in the order run


def bootstrap_lower(diffs: list[float], n: int, alpha: float, seed: int = 0) -> float:
    rng = random.Random(seed)
    k = len(diffs)
    means = sorted(sum(diffs[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return means[int(alpha * n)]


class Gate:
    def __init__(self, ev: Evaluator, cfg: GateConfig | None = None):
        self.ev, self.cfg = ev, cfg or GateConfig()

    def paired_gain(self, base: Harness, other: Harness, suite: str = "held") -> tuple:
        """Mean and bootstrap lower bound of other-minus-base on the same worlds."""
        a, b = self.ev.run(base, suite), self.ev.run(other, suite)
        diffs = [true_score(y) - true_score(x) for x, y in zip(a, b)]
        return sum(diffs) / len(diffs), bootstrap_lower(diffs, self.cfg.n_boot, self.cfg.alpha)

    def check(self, parent: Harness, child: Harness, touched: list[str],
              placebo: Harness | None = None, regression: bool = False,
              on_check=None) -> Decision:
        """placebo: parent + a content-free lesson of similar length. If given, the child must
        beat the placebo too, so a gain from 'any extra text' is not credited to the lesson.
        regression: also require the child not to lose ground on the old-skill regression suite
        BEFORE promotion (the sim loop instead rolls back after promotion)."""
        c, ev = self.cfg, self.ev
        checks: list = []

        def done(ok: bool, reason: str, m: dict) -> Decision:
            return Decision(ok, reason, m, checks)

        def note(name, ok, value, detail):
            checks.append({"name": name, "pass": bool(ok), "value": value, "detail": detail})
            if on_check:
                on_check(checks[-1])

        bad = [p for p in touched if p not in c.allowed_paths]
        note("allowlist", not bad or "allowlist" not in c.enabled, None,
             "only prompt/memory/tools may change" if not bad else f"touches {bad[0]}")
        if bad and "allowlist" in c.enabled:
            return done(False, f"allowlist: edit touches protected path {bad[0]}", {})

        pr, ch = ev.run(parent, "held"), ev.run(child, "held")
        diffs = [true_score(b) - true_score(a) for a, b in zip(pr, ch)]
        gain = sum(diffs) / len(diffs)
        lower = bootstrap_lower(diffs, c.n_boot, c.alpha)
        m = {"gain": round(gain, 4), "gain_lower": round(lower, 4)}
        held_ok = not (gain < c.min_gain or lower <= 0)
        note("held-out gain", held_ok, round(gain, 4),
             f"mean {gain:+.3f}, lower bound {lower:+.3f} (need > 0)")
        if not held_ok:
            return done(False, f"no significant held-out gain (mean {gain:+.3f}, "
                               f"lower bound {lower:+.3f})", m)

        if placebo is not None and "placebo" in c.enabled:
            pg, pl = self.paired_gain(placebo, child)
            m["vs_placebo"], m["vs_placebo_lower"] = round(pg, 4), round(pl, 4)
            note("beats placebo", pl > 0, round(pg, 4), f"vs placebo: mean {pg:+.3f}, lower bound {pl:+.3f} (need > 0)")
            if pl <= 0:
                return done(False, f"no better than a placebo lesson (mean {pg:+.3f}, "
                                   f"lower bound {pl:+.3f})", m)

        if regression and "regression" in c.enabled:
            drop = self.regression_drop(parent, child)
            m["regression_drop"] = round(drop, 4)
            note("regression suite", drop <= c.regression_tol, round(drop, 4),
                 f"old-skill score drop {drop:+.3f} (tolerance {c.regression_tol})")
            if drop > c.regression_tol:
                return done(False, f"regression: old-skill suite dropped {drop:.3f} "
                                   f"(tolerance {c.regression_tol})", m)

        gap = lambda h: ev.mean_claimed(h, "held") - ev.mean_true(h, "held")
        drift = gap(child) - gap(parent)
        m["claim_drift"] = round(drift, 4)
        integ_ok = not (drift > c.integrity_gap_tol and "integrity" in c.enabled)
        note("integrity", integ_ok, round(drift, 4), f"claimed-vs-verified gap grew by {drift:+.3f}")
        if not integ_ok:
            return done(False, f"integrity: claimed success exceeds verified by {drift:.2f}", m)

        cd = ev.mean_true(child, "canary") - ev.mean_true(parent, "canary")
        m["canary_delta"] = round(cd, 4)
        canary_ok = not (cd < -c.canary_tol and "canary" in c.enabled)
        note("canary", canary_ok, round(cd, 4), f"hidden tasks changed {cd:+.3f} (tolerance -{c.canary_tol})")
        if not canary_ok:
            return done(False, f"canary regression ({cd:+.3f})", m)

        growth = ev.mean_tokens(child, "held") / ev.mean_tokens(parent, "held") - 1
        m["cost_growth"] = round(growth, 4)
        limit = c.relaxed_cost_growth if gain >= c.big_gain else c.max_cost_growth
        cost_ok = not (growth > limit and "cost" in c.enabled)
        note("cost", cost_ok, round(growth, 4), f"tokens {growth:+.0%} (budget {limit:.0%})")
        if not cost_ok:
            return done(False, f"cost: tokens +{growth:.0%} exceeds {limit:.0%} budget", m)
        return done(True, f"promoted (gain {gain:+.3f}, tokens {growth:+.0%})", m)

    def regression_drop(self, parent: Harness, child: Harness) -> float:
        return self.ev.mean_true(parent, "regression") - self.ev.mean_true(child, "regression")
