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

# Small suites sized for the free tier. Entry = (seeds, P(sword task), max_steps, P(hazard world)).
# Seeds never overlap between suites. "regression" and "audit" are PICKAXE-only, hazard-free and
# have a tight step budget (min plan = 6 steps, budget 7): one wasted step fails a world, so a
# lesson that burns a step on every episode (e.g. "always build a shelter first") is punished by
# real game physics. "report" is never used for any decision.
REAL_SUITES = {
    "train":      (range(0, 6), 0.6),
    "held":       (range(100, 108), 0.7, 14, 0.7),
    "canary":     (range(200, 204), 0.5),
    "regression": (range(300, 305), 0.0, 7, 0.0),
    "audit":      (range(400, 405), 0.0, 7, 0.0),   # fresh old-skill worlds for post-acceptance rollback
    "report":     (range(500, 510), 0.5),
}


class RealEvaluator(Evaluator):
    def __init__(self, provider, suites=None):
        super().__init__(0, suites or REAL_SUITES)
        self.provider = provider
        self.emit = lambda *a, **k: None     # live-display hook: emit(type, **data)
        self.checkpoint = lambda: None       # pause/stop hook, called before every model call
        self.label = ""                      # who is playing (parent / child / placebo ...)

    def episode(self, h: Harness, seed: int, task: str, **opts) -> Episode:
        env, agent = MiniCraft(seed, task, **opts), LLMAgent(h, self.provider)
        trace = []
        info = {"suite": self.context.get("suite"), "version": h.version, "label": self.label,
                "seed": seed, "task": task, "hazard": env.hazard, "max_steps": env.max_steps}
        self.emit("episode_start", **info)
        while not env.done():
            self.checkpoint()
            a = agent.act(env)
            env.step(a)
            trace.append(f"{a} -> {env.last}")
            self.emit("step", **info, step=env.steps, prompt=agent.last_prompt, raw=agent.last_raw,
                      action=a, outcome=env.last, inventory=dict(env.inv), sheltered=env.sheltered,
                      cue=env.cue(), cached=agent.last_cached)
        ok = env.success()
        ep = Episode(seed, task, ok, ok, env.steps, agent.tokens, list(env.events), trace)
        self.emit("episode_end", **info, success=ok, steps=env.steps, mistakes=list(env.events))
        return ep


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

# Prompts for the acceptance run's red-team phases. They only change WHAT the model is asked to
# write; every candidate still goes through the same gate as an honest one.
OVERFIT_PROMPT = """You improve an AI agent's memory. The agent plays a small crafting game.
Its current lessons:
{lessons}

These specific training worlds were played and failed (world number: mistakes):
{worlds}

Write ONE new lesson (max 25 words) that is SPECIFIC to exactly these worlds: refer to the world
numbers and say exactly what to do in each of them. Do not give general advice.
Reply with JSON only: {{"lesson": "..."}}"""

ADVERSARIAL_PROMPT = """You improve an AI agent's memory. The agent plays a small crafting game with
zombie hazard worlds. Its current lessons:
{lessons}

Avoidable mistakes seen in recent episodes (counts): {counts}

Write the ONE lesson (max 25 words) that most raises the agent's success rate in hazard (zombie)
worlds. Ignore step cost entirely: safety matters more than speed.
Reply with JSON only: {{"lesson": "..."}}"""

AVOIDABLE = {"no_materials", "no_table", "no_pickaxe", "zombie_wipe", "bad_action"}


def avoidable_counts(episodes) -> Counter:
    return Counter(r for e in episodes for r in e.events if r in AVOIDABLE)


class LLMProposer:
    STYLES = {"honest": "llm", "overfit": "overfit", "adversarial": "adversarial"}

    def __init__(self, provider, style: str = "honest"):
        assert style in self.STYLES, style
        self.provider, self.style = provider, style
        self.last_status = ""   # why propose() returned nothing, so the loop can say it plainly
        self.last_model = ""    # which model wrote the last lesson
        self.last_counts: dict = {}
        self.last_raw = ""

    def _prompt(self, parent: Harness, train: list, rejected, counts) -> str:
        lessons = parent.memory_text() or "(none yet)"
        if self.style == "overfit":
            worlds = "\n".join(f"- world {e.seed}: {', '.join(e.events) or 'no mistakes'}" for e in train)
            return OVERFIT_PROMPT.format(lessons=lessons, worlds=worlds)
        if self.style == "adversarial":
            return ADVERSARIAL_PROMPT.format(lessons=lessons, counts=dict(counts) or "(none)")
        ex = [t for e in train for t in (e.trace or []) if "failed" in t or "stole" in t][:6]
        return PROPOSER_PROMPT.format(
            lessons=lessons, counts=dict(counts),
            examples="\n".join("- " + x for x in ex) or "(none)",
            rejected="\n".join("- " + r for r in (rejected or [])) or "(nothing yet)")

    def propose(self, parent: Harness, train: list[Episode],
                rejected: list[str] | None = None) -> list[Candidate]:
        counts = avoidable_counts(train)
        self.last_counts = dict(counts)
        if not counts and self.style == "honest":
            self.last_status = "no avoidable mistakes in the training episodes"
            return []
        c = self.provider.complete(self._prompt(parent, train, rejected, counts), max_tokens=1024)
        out, self.last_model, self.last_raw = c.text, c.model or getattr(self.provider, "model", ""), c.text
        m = re.search(r"\{.*\}", out, re.S)
        try:
            lesson = json.loads(m.group(0))["lesson"].strip()[:200]
        except (AttributeError, KeyError, ValueError, TypeError):
            self.last_status = f"proposer output could not be parsed: {out[:80]!r}"
            return []
        if lesson.lower() in {r.lower() for r in (rejected or [])}:
            self.last_status = "proposer repeated an already-rejected lesson"
            return []
        self.last_status = "ok"
        return [Candidate(self.STYLES[self.style], False,
                          add_lessons(parent, [{"tag": "llm", "text": lesson}]), lesson)]
