"""Acceptance run: does the gate let a REAL improvement through and stop the bad ones?

    python run_acceptance.py            # prints the request budget and exits (nothing is called)
    python run_acceptance.py --yes      # REAL Gemini run, free tier only; resumable across quota days
    python run_acceptance.py --mock     # SIMULATED dry run into acceptance_simulated/ (no network)

Phases (real Gemini player AND real Gemini proposer):
  baseline        measure the original harness on the unseen report worlds
  honest          honest proposer rounds until one genuine improvement is ACCEPTED
  placebo_overfit submit a content-free placebo note, then a world-specific "overfit" lesson -> reject
  regression      a proposer told to ignore step cost -> must be stopped by the regression check
                  (judged against the current harness AND against the original, off-lineage)
  final           original vs final harness on the unseen report worlds, paired CI
Output: acceptance/ACCEPTANCE.md, summary.json, requests.json (all generated from data) and
experiments.jsonl. On quota exhaustion it stops cleanly; run the SAME command after the reset and
it continues from cache for free.
"""
import argparse
import json
import os
import sys

from gk.acceptance import PHASES, acceptance_budget, final_comparison, preflight, read_rows, write_all
from gk.calllog import CallLog, read_calls
from gk.harness import placebo
from gk.lab import resume_hint
from gk.providers import QuotaExhausted, RateLimitError, TransientError
from gk.setup import make_mock, make_real
from gk.verify import verified_rows

SHOW = {"proposal", "candidate", "gate_check", "decision", "audit", "rollback", "report"}


def printer(t, **d):
    if t not in SHOW:
        return
    if t == "proposal":
        print(f"  [{d['style']}] proposer {d.get('model')}: {d.get('lesson') or 'NO LESSON: ' + d.get('status', '')}")
    elif t == "candidate":
        print(f"  candidate {d['child']} ({d['kind']}{', side branch' if d.get('side') else ''})")
    elif t == "gate_check":
        print(f"    {'PASS' if d['pass'] else 'FAIL'} {d['name']}: {d['detail']}")
    elif t == "decision":
        print(f"  => {'ACCEPTED' if d['accepted'] else 'rejected'}: {d['reason']}  (requests used {d['requests_used']})")
    elif t == "report":
        print(f"  [{d['stage']}] success {d['success']:.0%}  steps {d['mean_steps']:.1f}  score {d['score']:.3f}")
    else:
        print(f"  {t}: {d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--yes", action="store_true",
                    help="I accept the request budget and the spending limit (Rs 0 free tier, or max_spend_inr)")
    ap.add_argument("--mock", action="store_true", help="SIMULATED dry run (no network)")
    ap.add_argument("--honest-rounds", type=int, default=4)
    args = ap.parse_args()

    b = acceptance_budget()
    print("Request budget for the whole acceptance run (one request per agent step):")
    for k, v in b["parts_worst"].items():
        print(f"  {v:5d}  {k}")
    print(f"  worst case {b['worst_case']} requests, typical about {b['typical']} "
          f"(~{b['days_at_500_per_day']} days of the 500/day Flash-Lite free quota; resumable, cached)")
    if not args.mock and not args.yes:
        try:
            cfg = json.load(open(args.config, encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            cfg = {}
        from gk.providers import PAID_PROVIDERS, DEFAULT_USD_INR, call_cost_usd
        if cfg.get("provider") in PAID_PROVIDERS:
            rate = cfg.get("usd_inr", DEFAULT_USD_INR)
            typ = b["typical"] * call_cost_usd(cfg["model"], 450, 4) * rate               # priced per call
            worst = b["worst_case"] * call_cost_usd(cfg["model"], 900, 16) * rate
            print(f"  PAID model {cfg['model']}: about Rs {typ:.2f} typical, at most Rs {worst:.2f} "
                  f"(+ about 20 proposer calls, under Rs 1). Hard cap: Rs {cfg.get('max_spend_inr')}.")
        print("\nNothing was called. Confirm the spending limit (Rs 0 with no billing for Gemini, or the "
              "max_spend_inr cap for a paid model), then run:\n    python run_acceptance.py --yes")
        sys.exit(2)

    if args.mock:
        outdir = "acceptance_simulated"
        os.makedirs(outdir, exist_ok=True)
        log, calls_path = os.path.join(outdir, "experiments.jsonl"), os.path.join(outdir, "calls.mock.jsonl")
        stack = make_mock(CallLog(calls_path, simulated=True))
        print("\n*** SIMULATED RUN: mock models, nothing real is measured ***")
    else:
        from app import _has_key
        from app import key_name
        if not _has_key(args.config):
            sys.exit(f"No {key_name(args.config)} in .env or the environment.")
        outdir, log, calls_path = "acceptance", "experiments.jsonl", "calls.jsonl"
        stack = make_real(args.config, calllog=CallLog(calls_path))
        used_up = stack.exhausted()
        if used_up:
            sys.exit(f"STOP: free quota used up ({used_up}). {resume_hint()}")
    lab = stack.make_lab(log_path=log, emit=printer)
    phases = {p: "not reached" for p in ("baseline",) + PHASES + ("final",)}
    final, stopped = None, ""

    def guard(name, fn):
        """Run one step; turn quota/429/outage into a clean stop."""
        nonlocal stopped
        try:
            return fn()
        except QuotaExhausted as e:
            stopped = f"STOP: quota exhausted ({e}). {resume_hint()}"
        except RateLimitError as e:
            stopped = f"STOP: HTTP 429 ({e}). Not retrying. {resume_hint()}"
        except TransientError as e:
            stopped = f"STOP: provider unavailable ({e}). Safe to resume; work is cached."
        phases[name] = "stopped"

    print("\n== baseline: original harness on unseen report worlds")
    lab.phase = "baseline"
    guard("baseline", lambda: lab.measure("original"))
    if not stopped:
        phases["baseline"] = "done"
        res = guard("baseline", lambda: preflight(lab))
        if res and not res[0]:
            stopped = res[2]
            phases["baseline"] = "regression suite at floor"
        elif res:
            print(f"  pre-flight: {res[2]}")
    if not stopped:
        print("\n== phase 1: honest proposer until one improvement is accepted")
        lab.phase = "honest"
        st = lab.run(args.honest_rounds, "honest", stop_when_accepted=True)
        if st.startswith("STOP") or st.startswith("stopped by"):
            stopped = st
            phases["honest"] = "stopped"
        else:
            phases["honest"] = "done"
    if not stopped:
        print("\n== phase 2: placebo note and overfit lesson must be rejected")
        lab.phase = "placebo_overfit"
        guard("placebo_overfit", lambda: lab.submit("placebo", placebo(lab.current), "content-free placebo note"))
        if not stopped:
            st = lab.run(1, "overfit")
            if st.startswith("STOP"):
                stopped = st
                phases["placebo_overfit"] = "stopped"
            else:
                phases["placebo_overfit"] = "done"
    if not stopped:
        print("\n== phase 3: adversarial lesson (ignore step cost) must be stopped by the regression check")
        lab.phase = "regression"
        st = lab.run(1, "adversarial")
        if st.startswith("STOP"):
            stopped = st
            phases["regression"] = "stopped"
        else:
            guard("regression", lambda: lab.run_side("adversarial"))
            if not stopped:
                guard("regression", lab.audit)
            if not stopped:
                phases["regression"] = "done"
    if not stopped:
        print("\n== phase 4: original vs final on unseen report worlds")
        lab.phase = "final"
        final = guard("final", lambda: final_comparison(lab.ev, lab.original, lab.current))
        if final and not stopped:
            phases["final"] = "done"
            lab.measure("final")

    good, every, results = verified_rows(log, calls_path) if os.path.exists(log) else ([], [], [])
    sel = lambda rs: [r for r in rs if r.get("phase") in PHASES and (args.mock or r.get("mode") == "real")]
    rows = sel(good)
    verification = {"records": len(sel(every)), "verified": len(rows),
                    "failed_checks": [f"{e} {c}: {d}" for e, c, ok, d in results if not ok][:20]}
    if verification["records"] != verification["verified"]:
        print(f"WARNING: {verification['records'] - verification['verified']} record(s) failed "
              "verification and are excluded; run python -m gk.verify")
    models = {"player": stack.player_model, "proposers": stack.proposer_models}
    summary = write_all(outdir, rows, final, stack, stopped or "completed", phases, args.mock, models,
                        calls=read_calls(calls_path), verification=verification)
    print(f"\nWrote {outdir}/ACCEPTANCE.md, summary.json, requests.json")
    for c in summary["criteria"]:
        print(f"  {c['status']:8s} {c['id']}. {c['name']}")
    if stopped:
        print(f"\nPARTIAL: {stopped}\nRun the same command again after the reset; finished work is cached and free.")


if __name__ == "__main__":
    main()
