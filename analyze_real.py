"""Replay CACHED real-model episodes (no network, no quota) and summarize what the gate saw.

    python analyze_real.py config.json      # writes real_analysis.json

A cache miss raises instead of calling the API, so this can never spend quota.
"""
import json
import sys
from collections import Counter

from gk.env import ENV_VERSION
from gk.harness import Harness, add_lessons
from gk.providers import CachedProvider, Provider
from gk.real import RealEvaluator

cfg = json.load(open(sys.argv[1]))


class CacheOnly(Provider):
    name, model = cfg["provider"], cfg["model"]

    def complete(self, *a, **k):
        raise RuntimeError("cache miss: this analysis never calls the API")


ev = RealEvaluator(CachedProvider(CacheOnly(), f"cache_{cfg['provider']}_{cfg['model']}.jsonl"))
run = json.load(open("real_run.json"))
entry = next((e for e in run["log"] if e["kind"] == "llm"), None)
if entry is None:
    sys.exit("real_run.json has no LLM-proposed lesson to analyse (see its log); nothing written.")
parent = Harness.initial()
child = parent.with_patch(add_lessons(parent, [{"tag": "llm", "text": entry["note"]}]))
P, C = ev.run(parent, "held"), ev.run(child, "held")


def summ(eps):
    return {"n": len(eps), "mean_steps": sum(e.steps for e in eps) / len(eps),
            "success": sum(e.success for e in eps), "events": dict(Counter(r for e in eps for r in e.events))}


seed = next(p.seed for p in P if "no_materials" in p.events)
out = {
    "env": ENV_VERSION, "model": run["player"], "lesson": entry["note"], "log": run["log"], "gate_reason": entry["reason"],
    "gate_metrics": entry.get("metrics", {}), "report": run["report"],
    "held": {"parent": summ(P), "child": summ(C)},
    "episodes": [{"seed": p.seed, "task": p.task, "p_steps": p.steps, "c_steps": c.steps,
                  "p_nomat": p.events.count("no_materials"), "c_nomat": c.events.count("no_materials"),
                  "p_slip": p.events.count("slip"), "c_slip": c.events.count("slip")}
                 for p, c in zip(P, C)],
    "trace_seed": seed,
    "trace_parent": next(p for p in P if p.seed == seed).trace,
    "trace_child": next(c for c in C if c.seed == seed).trace,
}
try:
    out["red_team"] = json.load(open("real_redteam.json"))
except FileNotFoundError:
    out["red_team"] = []
json.dump(out, open("real_analysis.json", "w"), indent=1)
print("wrote real_analysis.json")
print(json.dumps({k: out[k] for k in ("held",)}, indent=1))
