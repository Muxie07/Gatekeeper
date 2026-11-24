"""Self-improvement loops: naive (accept if the train score rises) vs gated."""
from __future__ import annotations

from .evaluator import Evaluator, claimed_score
from .gate import Gate, GateConfig
from .harness import Harness
from .proposers import propose

MODES = ("naive", "heldout_only", "gated")


def run_trial(mode: str, trial: int, rounds: int = 6, ev: Evaluator | None = None) -> dict:
    """naive        : accept if the agent-reported TRAIN score rises
    heldout_only : verified held-out gain only (no allowlist/integrity/canary/cost/rollback)
    gated        : the full gate + post-promotion regression rollback"""
    assert mode in MODES
    ev = ev or Evaluator(offset=trial * 1000)
    cfg = GateConfig(enabled=frozenset({"heldout"})) if mode == "heldout_only" else GateConfig()
    gate = Gate(ev, cfg)
    parent = Harness.initial()
    lineage: list = []                 # candidates currently baked into `parent`
    log: list = []
    promoted_bad = rollbacks = 0
    curve = [ev.mean_true(parent, "report")]
    tok = [ev.mean_tokens(parent, "report")]

    for r in range(1, rounds + 1):
        train = ev.run(parent, "train")
        cands = propose(parent, train, r, trial)
        rows = []
        for c in cands:
            child = parent.with_patch(c.patch)
            if mode == "naive":
                score = sum(claimed_score(e, child) for e in ev.run(child, "train")) / len(train)
                rows.append([c, child, score, None])
            else:
                rows.append([c, child, None, gate.check(parent, child, list(c.patch))])

        chosen = None
        if mode == "naive":
            base = sum(claimed_score(e, parent) for e in train) / len(train)
            best = max(rows, key=lambda x: x[2])
            if best[2] > base:
                chosen = best
            for row in rows:
                ok = row is chosen
                log.append({"round": r, "kind": row[0].kind, "bad": row[0].bad, "note": row[0].note,
                            "accepted": ok,
                            "reason": f"train score {row[2]:.3f} vs {base:.3f}"})
        else:
            ok_rows = [x for x in rows if x[3].accepted]
            if ok_rows:
                chosen = max(ok_rows, key=lambda x: x[3].metrics["gain"])
            for row in rows:
                log.append({"round": r, "kind": row[0].kind, "bad": row[0].bad, "note": row[0].note,
                            "accepted": row is chosen, "reason": row[3].reason})

        if chosen:
            c, child = chosen[0], chosen[1]
            promoted_bad += c.bad
            if "regression" in cfg.enabled and mode != "naive":
                drop = gate.regression_drop(parent, child)
                if drop > gate.cfg.regression_tol:
                    rollbacks += 1
                    log.append({"round": r, "kind": c.kind, "bad": c.bad, "accepted": False,
                                "note": "post-promotion regression suite",
                                "reason": f"ROLLBACK: pickaxe skill dropped {drop:.2f}"})
                    for x in log:  # the promotion above is undone
                        if x["round"] == r and x["kind"] == c.kind and x["accepted"]:
                            x["accepted"] = False
                            x["reason"] += " -> rolled back"
                    chosen = None
            if chosen:
                parent = child
                lineage.append(c)
        curve.append(ev.mean_true(parent, "report"))
        tok.append(ev.mean_tokens(parent, "report"))

    return {"mode": mode, "trial": trial, "curve": curve, "tokens": tok, "log": log,
            "final_score": curve[-1], "final_tokens": tok[-1],
            "bad_promoted": promoted_bad, "bad_surviving": sum(c.bad for c in lineage),
            "rollbacks": rollbacks, "lineage": [c.kind for c in lineage]}
