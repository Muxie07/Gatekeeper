"""Build acceptance/summary.json, acceptance/requests.json and ACCEPTANCE.md FROM DATA.

Nothing here is hand-written: every PASS/FAIL comes from rows in experiments.jsonl (written by the
Lab as it ran) and from the measured report-suite results. If a criterion did not occur it is
reported as NOT YET (or FAIL if the phase ran and it still did not occur) with what was tried.
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone

from .evaluator import true_score
from .lab import round_budget
from .real import REAL_SUITES

PHASES = ("honest", "placebo_overfit", "regression")


def bootstrap_ci(diffs: list[float], n: int = 4000, alpha: float = 0.05, seed: int = 1) -> tuple:
    rng, k = random.Random(seed), len(diffs)
    means = sorted(sum(diffs[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return means[int(alpha / 2 * n)], means[int((1 - alpha / 2) * n) - 1]


def acceptance_budget(suites=None) -> dict:
    """Worst-case and typical model calls for the whole acceptance run."""
    b = round_budget(suites or REAL_SUITES)
    c = b["per_suite_worst"]
    parts = {
        "baseline report (original)": c["report"],
        "pre-flight: original on the regression suite": c["regression"],
        "phase 1 honest, first round (to the first acceptance)": b["first_round_worst"],
        "phase 2 placebo (held only) + overfit round": c["held"] + c["held"],
        "phase 3 adversarial vs current + vs original (side branch)":
            c["held"] + c["held"] + c["held"] + c["regression"],
        "audit of accepted versions": 2 * c["audit"],
        "final report (improved)": c["report"],
    }
    worst = sum(parts.values())
    return {"parts_worst": parts, "worst_case": worst, "typical": round(worst * 0.7),
            "days_at_500_per_day": round(worst * 0.7 / 500, 1)}


def preflight(lab) -> tuple:
    """Measure the ORIGINAL harness on the regression suite before judging any candidate. At floor
    the suite cannot show a regression, so refuse to start. Returns (ok, parent_mean, message)."""
    mean = lab.ev.mean_true(lab.original, "regression")
    floor = lab.gate.cfg.regression_floor
    if mean < floor:
        return False, mean, (f"STOP: regression suite at floor (original scores {mean:.3f} < {floor}); "
                             "no regression could be seen, so no candidate was evaluated. "
                             "Make the regression suite easier (e.g. a larger step budget) and restart.")
    return True, mean, f"regression suite informative (original scores {mean:.3f} >= {floor})"


def read_rows(path: str) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    except FileNotFoundError:
        pass
    return rows


def _gain(row) -> str:
    m = row.get("metrics", {})
    return f"gain {m.get('gain', 0):+.3f} (lower bound {m.get('gain_lower', 0):+.3f})" if "gain" in m else ""


def criteria(rows: list[dict], final: dict | None, phases_run: dict) -> list[dict]:
    main = [r for r in rows if not r.get("side_branch")]
    out = []

    acc = [r for r in main if r["phase"] == "honest" and r["kind"] == "llm" and r["decision"] == "accepted"]
    if acc:
        r = acc[0]
        ev = (f"version {r['parent']} -> {r['child']} accepted in round {r['round']}: "
              f"\"{r['lesson']}\" ({_gain(r)}; proposer {r['proposer_model']}). "
              f"Honest rounds that ran: {sum(1 for x in main if x['phase'] == 'honest' and x['kind'] in ('llm', 'none'))}.")
        out.append({"id": 1, "name": ">=1 real improvement accepted", "status": "PASS", "evidence": ev})
    else:
        tried = [f"round {x['round']}: {x['reason']}" for x in main if x["phase"] == "honest"]
        out.append({"id": 1, "name": ">=1 real improvement accepted",
                    "status": "FAIL" if phases_run.get("honest") == "done" else "NOT YET",
                    "evidence": "no honest lesson was accepted. Tried: " + ("; ".join(tried) or "nothing yet")})

    rej = [r for r in main if r["kind"] in ("placebo", "overfit") and r["decision"] == "rejected"]
    if rej:
        out.append({"id": 2, "name": ">=1 placebo or overfit candidate rejected", "status": "PASS",
                    "evidence": "; ".join(f"{r['kind']} rejected: {r['reason']}" for r in rej)})
    else:
        ran = [r for r in main if r["kind"] in ("placebo", "overfit")]
        out.append({"id": 2, "name": ">=1 placebo or overfit candidate rejected",
                    "status": "FAIL" if ran else "NOT YET",
                    "evidence": ("; ".join(f"{r['kind']}: {r['decision']} ({r['reason']})" for r in ran)
                                 or "phase not reached yet")})

    reg = [r for r in rows if (r["decision"] == "rejected" and r["reason"].startswith("regression"))
           or r["kind"] == "rollback"]
    adv = [r for r in rows if r["kind"] == "adversarial"]
    if reg:
        out.append({"id": 3, "name": ">=1 regression rejection or rollback", "status": "PASS",
                    "evidence": "; ".join(
                        f"{r['kind']}{' (side branch vs original)' if r.get('side_branch') else ''} "
                        f"\"{r['lesson'][:80]}\": {r['reason']}" for r in reg)})
    else:
        out.append({"id": 3, "name": ">=1 regression rejection or rollback",
                    "status": "FAIL" if adv and phases_run.get("regression") == "done" else "NOT YET",
                    "evidence": ("adversarial candidates were stopped by other checks or accepted: "
                                 + "; ".join(f"{r['decision']} ({r['reason']})" for r in adv)
                                 if adv else "phase not reached yet")})
    roll = [r for r in rows if r["kind"] == "rollback"]
    out[-1]["rollback_demonstrated_on_real_version"] = bool(roll)

    if final and final.get("orig") and final.get("final"):
        o, f = final["orig"], final["final"]
        out.append({"id": 4, "name": "original vs improved on unseen scenarios (numbers)", "status": "PASS",
                    "evidence": (f"original {o['version']}: success {o['success']:.0%}, steps {o['mean_steps']:.1f}, "
                                 f"score {o['score']:.3f}; final {f['version']}: success {f['success']:.0%}, "
                                 f"steps {f['mean_steps']:.1f}, score {f['score']:.3f}; paired mean "
                                 f"{final['paired_mean']:+.3f}, 95% CI [{final['ci'][0]:+.3f}, {final['ci'][1]:+.3f}]"
                                 f" -> {'CI excludes 0' if final['significant'] else 'CI includes 0 (not significant)'}"),
                    "significant": final["significant"]})
    else:
        out.append({"id": 4, "name": "original vs improved on unseen scenarios (numbers)", "status": "NOT YET",
                    "evidence": "final measurement not completed"})
    return out


def final_comparison(ev, original, final_h) -> dict:
    """Paired comparison on the report suite (seeds never used for train/held/canary/regression/audit)."""
    a, b = ev.run(original, "report"), ev.run(final_h, "report")
    diffs = [true_score(y) - true_score(x) for x, y in zip(a, b)]
    lo, hi = bootstrap_ci(diffs)
    row = lambda h, eps: {"version": h.version, "n": len(eps), "success": sum(e.success for e in eps) / len(eps),
                          "mean_steps": sum(e.steps for e in eps) / len(eps),
                          "score": sum(true_score(e) for e in eps) / len(eps)}
    return {"orig": row(original, a), "final": row(final_h, b), "paired_mean": sum(diffs) / len(diffs),
            "ci": [lo, hi], "significant": lo > 0,
            "per_world": [{"seed": x.seed, "task": x.task, "orig_steps": x.steps, "orig_ok": x.success,
                           "final_steps": y.steps, "final_ok": y.success} for x, y in zip(a, b)]}


def requests_report(stack) -> dict:
    if stack.mode != "real":
        return {"SIMULATED": True, "note": "mock run: no real requests were made", "mock_calls": stack.requests()}
    days = stack.ledger.all()
    out = {"limits": stack.limits, "per_day": days, "violations": []}
    for day, models in days.items():
        for m, r in models.items():
            lim = stack.limits.get(m, {})
            if lim.get("rpd") and r["requests"] > lim["rpd"]:
                out["violations"].append(f"{day} {m}: {r['requests']} > {lim['rpd']}")
    out["within_free_tier_limits"] = not out["violations"]
    out["note"] = ("counts are real API attempts recorded by the ledger since it was introduced; "
                   "cache hits are free and not counted")
    return out


def write_all(outdir: str, rows: list[dict], final: dict | None, stack, status: dict, phases_run: dict,
              simulated: bool, models: dict) -> dict:
    os.makedirs(outdir, exist_ok=True)
    crit = criteria(rows, final, phases_run)
    summary = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "SIMULATED": simulated, "models": models, "phases": phases_run, "status": status,
        "criteria": crit, "all_pass": all(c["status"] == "PASS" for c in crit),
        "final_comparison": final, "experiments": len(rows),
        "budget_estimate": acceptance_budget()}
    reqs = requests_report(stack)
    with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    with open(os.path.join(outdir, "requests.json"), "w", encoding="utf-8") as f:
        json.dump(reqs, f, indent=1)
    with open(os.path.join(outdir, "ACCEPTANCE.md"), "w", encoding="utf-8") as f:
        f.write(markdown(summary, rows, reqs))
    return summary


def markdown(s: dict, rows: list[dict], reqs: dict) -> str:
    L = []
    if s["SIMULATED"]:
        L += ["> **SIMULATED RUN: mock models, no Gemini call was made. NOT acceptance evidence.**", ""]
    L += ["# Acceptance", "", f"Generated from data: {s['generated']}. Player `{s['models']['player']}`, "
          f"proposers `{', '.join(s['models']['proposers'])}`.", "",
          "| # | Criterion | Result | Evidence |", "|---|---|---|---|"]
    for c in s["criteria"]:
        L.append(f"| {c['id']} | {c['name']} | **{c['status']}** | {c['evidence'].replace('|', '/')} |")
    L += ["", f"Overall: **{'ALL PASS' if s['all_pass'] else 'NOT ALL PASS'}**", "",
          f"Run status: {s['status']}", "", "## Experiments (experiments.jsonl)", "",
          "| phase | round | kind | parent -> child | decision | reason | requests |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['phase']} | {r['round']} | {r['kind']}{' (side)' if r.get('side_branch') else ''} | "
                 f"{r['parent']} -> {r['child']} | {r['decision']} | {r['reason'].replace('|', '/')} | {r.get('requests_used', 0)} |")
    fc = s.get("final_comparison")
    if fc:
        L += ["", "## Original vs final on unseen report worlds", "",
              "| harness | success | mean steps | score |", "|---|---|---|---|"]
        for k in ("orig", "final"):
            x = fc[k]
            L.append(f"| {k} `{x['version']}` | {x['success']:.0%} | {x['mean_steps']:.1f} | {x['score']:.3f} |")
        L += ["", f"Paired score difference {fc['paired_mean']:+.3f}, 95% bootstrap CI "
              f"[{fc['ci'][0]:+.3f}, {fc['ci'][1]:+.3f}] over {fc['orig']['n']} worlds."]
    L += ["", "## Requests (free-tier proof, see requests.json)", ""]
    if reqs.get("SIMULATED"):
        L.append("Simulated: no real requests.")
    else:
        for day, models in reqs["per_day"].items():
            for m, r in models.items():
                lim = reqs["limits"].get(m, {}).get("rpd")
                L.append(f"- {day} `{m}`: {r['requests']} requests{f' / {lim} limit' if lim else ''}, {r['tokens']} tokens")
        L.append(f"- Within free-tier limits: **{reqs['within_free_tier_limits']}**")
    return "\n".join(L) + "\n"
