"""Pilot: measure the ORIGINAL harness with the real Gemini player on every suite, before any
lesson is judged. It answers the two questions the gate depends on:
  1. Is the regression suite informative (original mean >= the floor in prereg/PREREG.md)?
  2. How good is the original agent per suite, and how much do scores vary between worlds?

    python run_pilot.py --dry-run   # NO network: replays the saved cache offline and prints the
                                    # exact number of steps already saved and the remaining upper bound
    python run_pilot.py --yes       # REAL run on the free tier (you confirm: no billing on the project)

It shares the cache and calls.jsonl with run_acceptance.py, so every call made here is reused by
the acceptance run for free. It never creates experiment records and never changes thresholds.
Stopping (Ctrl+C, quota, HTTP 429, outage) is safe: run the same command again and it continues
from the cache; finished calls are never repeated.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone

from gk.env import MiniCraft
from gk.evaluator import suite_opts, task_for, true_score
from gk.harness import Harness
from gk.providers import (CachedProvider, Completion, Provider, QuotaExhausted, RateLimitError,
                          TransientError, check_spend_cap, allowlist_for, PAID_PROVIDERS,
                          DEFAULT_USD_INR, call_cost_usd)
from gk.real import REAL_SUITES, RealEvaluator

ORDER = ("regression", "report", "train", "held", "canary")
PROMPT_TOKENS_TYPICAL, PROMPT_TOKENS_WORST = 450, 900   # pessimistic: measured v4 prompts are ~210-240 tokens   # cheapest, most decisive first
OUT = "pilot"


class CacheMiss(Exception):
    pass


class _OfflineInner(Provider):
    """Stands in for Gemini during --dry-run: any request the cache cannot answer stops the
    episode. Nothing is sent anywhere and nothing is written."""

    def __init__(self, name: str, model: str):
        self.name, self.model = name, model

    def complete(self, prompt, system="", max_tokens=256) -> Completion:
        raise CacheMiss()


class _ReadOnlyCache(CachedProvider):
    def complete(self, prompt, system="", max_tokens=256):
        d = self.store.get(self._key(prompt, system, max_tokens))
        if not d:
            self.misses += 1
            raise CacheMiss()
        self.hits += 1
        return Completion(d["text"], d["i"], d["o"], cached=True, model=self.model)


def worlds(name: str):
    seeds, p, opts = suite_opts(REAL_SUITES[name])
    return [(s, task_for(s, p), opts) for s in seeds]


def dry_run(cfg: dict) -> dict:
    """Replay the original harness on every pilot suite against the saved cache, offline."""
    model, kind = cfg["model"], cfg.get("provider", "gemini")
    cp = _ReadOnlyCache(_OfflineInner(kind, model), f"cache_{kind}_{model}.jsonl")
    ev = RealEvaluator(cp, REAL_SUITES)
    h = Harness.initial()
    rows, saved_total, remaining_total = [], 0, 0
    for name in ORDER:
        saved = remaining = done = 0
        for seed, task, opts in worlds(name):
            ev.context = {"suite": name, "version": h.version}
            before = cp.hits
            try:
                ev.episode(h, seed, task, **opts)
                done += 1
                saved += cp.hits - before
            except CacheMiss:
                got = cp.hits - before
                saved += got
                cap = opts.get("max_steps", MiniCraft(seed, task).max_steps)
                remaining += cap - got
        rows.append({"suite": name, "worlds": len(worlds(name)), "episodes_fully_saved": done,
                     "saved_steps": saved, "remaining_requests_max": remaining})
        saved_total += saved
        remaining_total += remaining
    return {"model": model, "suites": rows, "saved_steps": saved_total,
            "remaining_requests_max": remaining_total}


def summarise(name: str, eps: list) -> dict:
    scores = [true_score(e) for e in eps]
    _, _, opts = suite_opts(REAL_SUITES[name])
    haz = [MiniCraft(e.seed, e.task, **opts).hazard for e in eps]
    return {"suite": name, "n": len(eps),
            "success": sum(e.success for e in eps) / len(eps),
            "mean_steps": sum(e.steps for e in eps) / len(eps),
            "mean_score": sum(scores) / len(scores),
            "score_sd": statistics.pstdev(scores) if len(scores) > 1 else 0.0,
            "hazard_worlds": sum(haz),
            "hazard_success": (sum(e.success for e, z in zip(eps, haz) if z) / sum(haz)) if sum(haz) else None,
            "mistakes": dict(Counter(r for e in eps for r in e.events)),
            "per_world": [{"seed": e.seed, "task": e.task, "hazard": z, "success": e.success,
                           "steps": e.steps, "score": round(s, 4), "mistakes": e.events}
                          for e, s, z in zip(eps, scores, haz)]}


def floor_value() -> float:
    from gk.gate import GateConfig
    return GateConfig().regression_floor


def write(report: dict) -> None:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "pilot.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    lines = ["# Pilot: original harness, real Gemini player", "",
             f"Generated {report['generated']} from real model calls (this file is written by run_pilot.py).",
             f"Player: {report['player_model']}. Status: {report['status']}.", "",
             "| suite | n | success | mean steps | mean score | score SD | hazard worlds (success) | mistakes |",
             "|---|---|---|---|---|---|---|---|"]
    for s in report["suites"]:
        hz = f"{s['hazard_worlds']}" + (f" ({s['hazard_success']:.0%})" if s["hazard_success"] is not None else "")
        lines.append(f"| {s['suite']} | {s['n']} | {s['success']:.0%} | {s['mean_steps']:.1f} | "
                     f"{s['mean_score']:.3f} | {s['score_sd']:.3f} | {hz} | {s['mistakes']} |")
    if report.get("floor_check"):
        lines += ["", f"Regression floor check: {report['floor_check']}"]
    lines += ["", f"Model calls in this pilot: {report['calls']}"]
    with open(os.path.join(OUT, "PILOT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def real_run(cfg_path: str) -> None:
    from app import _has_key
    from gk.calllog import CallLog
    from gk.setup import make_real
    from gk.lab import resume_hint
    if not _has_key(cfg_path):
        from app import key_name
        sys.exit(f"No {key_name(cfg_path)} in .env or the environment.")
    from gk.runlock import RunLock, RunLocked
    try:
        RunLock(".", "run_pilot.py").acquire()
    except RunLocked as e:
        sys.exit(f"STOP: {e}")
    stack = make_real(cfg_path, calllog=CallLog("calls.jsonl"))
    used_up = stack.exhausted()
    if used_up:
        sys.exit(f"STOP: free quota used up ({used_up}). {resume_hint()}")
    os.makedirs(OUT, exist_ok=True)
    lab = stack.make_lab(log_path=os.path.join(OUT, "no_experiments_in_pilot.jsonl"))
    ev, h = lab.ev, lab.original
    req0 = stack.requests()
    report = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "player_model": stack.player_model, "harness": h.version, "suites": [],
              "status": "running", "SIMULATED": False}
    try:
        for name in ORDER:
            print(f"== {name}: {len(worlds(name))} worlds")
            eps = ev.run(h, name)
            s = summarise(name, eps)
            report["suites"].append(s)
            print(f"   success {s['success']:.0%}  steps {s['mean_steps']:.1f}  score {s['mean_score']:.3f}"
                  f"  sd {s['score_sd']:.3f}  mistakes {s['mistakes']}")
            if name == "regression":
                fl = floor_value()
                ok = s["mean_score"] >= fl
                report["floor_check"] = (f"{'OK' if ok else 'AT FLOOR'}: original scores "
                                         f"{s['mean_score']:.3f} (floor {fl})")
                print("   " + report["floor_check"])
        report["status"] = "completed"
    except KeyboardInterrupt:
        report["status"] = "stopped by user (Ctrl+C); run the same command to continue from cache"
    except QuotaExhausted as e:
        report["status"] = f"STOP: quota exhausted ({e}). {resume_hint()}"
    except RateLimitError as e:
        report["status"] = f"STOP: the provider kept refusing requests (HTTP 429: {e}). This is usually the PER-MINUTE limit, not the daily one: wait 2 minutes and run the same command again; finished work is cached."
    except TransientError as e:
        report["status"] = f"STOP: provider unavailable ({e}). Safe to resume; work is cached."
    report["calls"] = {"real_requests_this_run": stack.requests() - req0}
    write(report)
    print(f"\n{report['status']}\nWrote {OUT}/PILOT.md and {OUT}/pilot.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--dry-run", action="store_true", help="offline: count saved and remaining requests")
    ap.add_argument("--yes", action="store_true",
                    help="REAL run; I confirm the spending limit (Rs 0 free tier, or max_spend_inr for a paid model)")
    args = ap.parse_args()
    cfg = json.load(open(args.config, encoding="utf-8"))
    check_spend_cap(cfg)
    allowed = allowlist_for(cfg)
    if cfg["model"] not in allowed:
        sys.exit(f"model {cfg['model']} is not on the allowlist {list(allowed)}")
    if args.dry_run or not args.yes:
        d = dry_run(cfg)
        print(f"Pilot request plan (player {d['model']}, original harness, offline replay of the cache):")
        print(f"  {'suite':11s} worlds  fully saved  saved steps  remaining (max)")
        for r in d["suites"]:
            print(f"  {r['suite']:11s} {r['worlds']:6d}  {r['episodes_fully_saved']:11d}  "
                  f"{r['saved_steps']:11d}  {r['remaining_requests_max']:15d}")
        rpd = cfg.get("rpd", 500)
        print(f"  TOTAL: {d['saved_steps']} steps already saved (free); at most "
              f"{d['remaining_requests_max']} new requests ({d['remaining_requests_max'] / rpd:.0%} "
              f"of one day's {rpd}). Proposer calls: 0.")
        if cfg.get("provider") in PAID_PROVIDERS:
            n, rate = d["remaining_requests_max"], cfg.get("usd_inr", DEFAULT_USD_INR)
            worst = n * call_cost_usd(cfg["model"], PROMPT_TOKENS_WORST, 16) * rate          # priced per call
            typ = int(n * .7) * call_cost_usd(cfg["model"], PROMPT_TOKENS_TYPICAL, 4) * rate
            print(f"  PAID model {cfg['model']}: about Rs {typ:.2f} typical, at most Rs {worst:.2f} for this pilot "
                  f"(cap Rs {cfg['max_spend_inr']}; spend so far is in spend_ledger.json).")
            print("\nNothing was sent. To run it for real:\n    python run_pilot.py --yes")
        else:
            print("\nNothing was sent. To run it for real (free tier, after the daily reset):\n"
                  "    python run_pilot.py --yes")
        if not args.dry_run:
            sys.exit(2)
        return
    real_run(args.config)


if __name__ == "__main__":
    main()
