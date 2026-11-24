"""Run the CURRENT harness with a real model and log every episode.

    export GEMINI_API_KEY=...        # on your machine only; never commit it
    python run_llm_baseline.py config.json 10

Stops cleanly when the daily quota is used (QuotaExhausted) and resumes tomorrow:
finished episodes are cached, so a rerun costs nothing for them.
"""
import json
import sys

from gk.agent import LLMAgent
from gk.env import MiniCraft
from gk.evaluator import SUITES, task_for
from gk.harness import Harness
from gk.providers import QuotaExhausted, RateLimitError, TransientError, build_provider


def main(cfg_path: str, n: int) -> None:
    cfg = json.load(open(cfg_path))
    provider = build_provider(cfg)
    h = Harness.initial()
    seeds, p = SUITES["report"]
    results = []
    try:
        for seed in list(seeds)[:n]:
            env = MiniCraft(seed, task_for(seed, p))
            agent = LLMAgent(h, provider)
            while not env.done():
                env.step(agent.act(env))
            results.append({"seed": seed, "task": env.task, "success": env.success(),
                            "steps": env.steps, "tokens": agent.tokens, "events": env.events})
            print(f"seed {seed} {env.task:8s} success={env.success()} steps={env.steps}")
    except (QuotaExhausted, TransientError, RateLimitError) as e:
        print("Stopped:", e)
    with open("llm_baseline.json", "w") as f:
        json.dump({"model": cfg["model"], "episodes": results}, f, indent=1)
    if results:
        print(f"success rate {sum(r['success'] for r in results) / len(results):.2f} "
              f"over {len(results)} episodes; cache hits {provider.hits}, misses {provider.misses}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 10)
