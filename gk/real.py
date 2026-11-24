"""Real-model pieces: an evaluator that plays with an LLM, and an LLM proposer.

Everything goes through the Provider stack, so every call is cached (resume is free)
and the daily quota is enforced. Runs are small on purpose: free-tier quota is tiny.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from .agent import LLMAgent
from .env import MiniCraft
from .evaluator import Episode, Evaluator
from .harness import Harness, add_lessons
from .proposers import Candidate

# Small suites sized for ~500 free requests/day. (seeds, probability of a sword task)
REAL_SUITES = {
    "train":      (range(0, 6), 0.6),
    "held":       (range(100, 110), 0.7),
    "canary":     (range(200, 205), 0.5),
    "regression": (range(300, 305), 0.0),
    "report":     (range(500, 510), 0.5),   # same worlds as the baseline run
}


class RealEvaluator(Evaluator):
    def __init__(self, provider, suites=None):
        super().__init__(0, suites or REAL_SUITES)
        self.provider = provider

    def episode(self, h: Harness, seed: int, task: str) -> Episode:
        env, agent = MiniCraft(seed, task), LLMAgent(h, self.provider)
        trace = []
        while not env.done():
            a = agent.act(env)
            env.step(a)
            trace.append(f"{a} -> {env.last}")
        ok = env.success()
        return Episode(seed, task, ok, ok, env.steps, agent.tokens, list(env.events), trace)


PROPOSER_PROMPT = """You improve an AI agent's memory. The agent plays a small crafting game.
Its current lessons:
{lessons}

Avoidable mistakes seen in recent episodes (counts): {counts}
Example failed steps:
{examples}

Already tried and rejected by the gate (do NOT repeat or rephrase these):
{rejected}

Write ONE new lesson (max 25 words) that prevents an avoidable mistake. Prefer the mistake that
wastes the most steps (a zombie wipe costs about 4 steps; a failed craft costs 1).
It must be general advice, never mention specific worlds or seeds.
Reply with JSON only: {{"lesson": "..."}}"""

AVOIDABLE = {"no_materials", "no_table", "no_pickaxe", "zombie_wipe", "bad_action"}


def avoidable_counts(episodes) -> Counter:
    return Counter(r for e in episodes for r in e.events if r in AVOIDABLE)


class LLMProposer:
    def __init__(self, provider):
        self.provider = provider
        self.last_status = ""   # why propose() returned nothing, so the loop can say it plainly

    def propose(self, parent: Harness, train: list[Episode],
                rejected: list[str] | None = None) -> list[Candidate]:
        counts = avoidable_counts(train)
        if not counts:
            self.last_status = "no avoidable mistakes in the training episodes"
            return []
        ex = [t for e in train for t in (e.trace or []) if "failed" in t or "stole" in t][:6]
        prompt = PROPOSER_PROMPT.format(
            lessons=parent.memory_text() or "(none yet)", counts=dict(counts),
            examples="\n".join("- " + x for x in ex) or "(none)",
            rejected="\n".join("- " + r for r in (rejected or [])) or "(nothing yet)")
        out = self.provider.complete(prompt, max_tokens=1024).text
        m = re.search(r"\{.*\}", out, re.S)
        try:
            lesson = json.loads(m.group(0))["lesson"].strip()[:200]
        except (AttributeError, KeyError, ValueError, TypeError):
            self.last_status = f"proposer output could not be parsed: {out[:80]!r}"
            return []
        if lesson.lower() in {r.lower() for r in (rejected or [])}:
            self.last_status = "proposer repeated an already-rejected lesson"
            return []
        return [Candidate("llm", False, add_lessons(parent, [{"tag": "llm", "text": lesson}]),
                          lesson)]
