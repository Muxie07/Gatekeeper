"""Real improvement loop: Gemini plays, Gemini proposes, the gate decides.

    python run_real_loop.py config.json              # 2 rounds
    python run_real_loop.py config.json --rounds 3 --red-team

Free-tier friendly: every model call is cached and the daily quota is enforced.
When the quota runs out the run stops cleanly; run the SAME command after the reset
(midnight Pacific) and it continues from cache at no cost. Progress: real_run.json
"""
import argparse
import json
import os
import sys

from gk.gate import Gate
from gk.harness import Harness, placebo
from gk.proposers import adversarial
from gk.providers import (FallbackProvider, QuotaExhausted, RateLimitError, TransientError,
                          build_provider)
from gk.real import LLMProposer, RealEvaluator


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--red-team", action="store_true",
                    help="also submit planted bad edits (tamper, phantom, bloat)")
    args = ap.parse_args()
    cfg = json.load(open(args.config))
    # Proposer tries the strongest model first and falls back if it is overloaded (HTTP 503),
    # rate-limited, or out of its small daily quota. Override with cfg["proposer"]["models"].
    pmodels = cfg.get("proposer", {}).get("models") or [
        {"model": "gemini-3.8-flash", "rpm": 5, "rpd": 20},
        {"model": "gemini-3.6-flash", "rpm": 5, "rpd": 20},
        {"model": "gemini-3.5-flash-lite", "rpm": 15, "rpd": 500}]
    player = build_provider(cfg)
    proposer = FallbackProvider([build_provider({"provider": cfg["provider"], **m})
                                 for m in pmodels])
    ev = RealEvaluator(player)
    gate, llm = Gate(ev), LLMProposer(proposer)
    if os.path.exists("real_run.json"):   # never overwrite an earlier run's evidence
        os.replace("real_run.json", f"real_run.{int(os.path.getmtime('real_run.json'))}.json")
    parent, log, lineage, rollbacks, rejected = Harness.initial(), [], [], 0, []
    state = {"player": cfg["model"], "proposer": [m["model"] for m in pmodels],
             "log": log, "report": []}

    def save():
        state["lineage"], state["rollbacks"] = lineage, rollbacks
        with open("real_run.json", "w") as f:
            json.dump(state, f, indent=1)

    def report(tag):
        eps = ev.run(parent, "report")
        row = {"stage": tag, "version": parent.version,
               "success": sum(e.success for e in eps) / len(eps),
               "mean_steps": sum(e.steps for e in eps) / len(eps),
               "score": ev.mean_true(parent, "report"), "tokens": ev.mean_tokens(parent, "report")}
        state["report"].append(row)
        print(f"[{tag}] success {row['success']:.2f}  steps {row['mean_steps']:.2f}  "
              f"score {row['score']:.3f}  tokens {row['tokens']:.0f}")

    try:
        report("baseline")
        for r in range(1, args.rounds + 1):
            train = ev.run(parent, "train")
            cands = llm.propose(parent, train, rejected)
            if not cands:
                print(f"round {r}: proposer gave no candidate ({llm.last_status})")
            if args.red_team:
                cands += [adversarial(k, parent, [e.seed for e in train])
                          for k in ("tamper", "phantom", "bloat")]
            if not cands:
                print("stopping: nothing to evaluate")
                break
            plc = placebo(parent)   # same text every round for a given parent -> cached
            pg, pl = gate.paired_gain(parent, plc)
            log.append({"round": r, "kind": "placebo", "note": plc.lessons()[-1]["text"],
                        "accepted": False, "reason": f"diagnostic: placebo vs parent mean "
                        f"{pg:+.3f}, lower bound {pl:+.3f}", "metrics": {"gain": round(pg, 4),
                        "gain_lower": round(pl, 4)}})
            print(f"round {r} [placebo] content-free lesson vs parent: mean {pg:+.3f}, "
                  f"lower bound {pl:+.3f}")
            save()
            ok = []
            for c in cands:
                child = parent.with_patch(c.patch)
                d = gate.check(parent, child, list(c.patch), placebo=plc)
                log.append({"round": r, "kind": c.kind, "note": c.note, "accepted": d.accepted,
                            "reason": d.reason, "metrics": d.metrics})
                print(f"round {r} [{c.kind}] {'PROMOTE' if d.accepted else 'reject '} "
                      f"{d.reason} | {c.note[:70]}")
                if d.accepted:
                    ok.append((d.metrics["gain"], c, child))
                elif c.kind == "llm":
                    rejected.append(c.note)
                save()
            if ok:
                _, c, child = max(ok, key=lambda x: x[0])
                drop = gate.regression_drop(parent, child)
                if drop > gate.cfg.regression_tol:
                    rollbacks += 1
                    log.append({"round": r, "kind": c.kind, "accepted": False,
                                "reason": f"ROLLBACK: pickaxe skill dropped {drop:.2f}"})
                    print(f"round {r} ROLLBACK {c.kind}: pickaxe skill dropped {drop:.2f}")
                else:
                    parent = child
                    lineage.append({"kind": c.kind, "lesson": c.note})
                    print(f"round {r} promoted -> version {parent.version}")
            save()
            report(f"after round {r}")
    except (QuotaExhausted, TransientError, RateLimitError) as e:
        save()
        print(f"\nSTOPPED ({type(e).__name__}): {e}")
        print("This is normal. Run the same command again later (after the quota reset, or when "
              "the service recovers); finished work is cached and free.")
        sys.exit(0)
    save()
    print("\nDone. Details in real_run.json")


if __name__ == "__main__":
    main()
