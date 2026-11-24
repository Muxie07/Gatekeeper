"""Two agents with the same interface: act(env) -> action string or "DONE".

SimAgent  : rule-based stand-in for an LLM. Its mistakes are removed by lessons
            in the harness memory, so "learning" is measurable without API calls.
LLMAgent  : the real path. Builds a prompt from the harness and calls a Provider.
"""
from __future__ import annotations

from .env import ACTIONS, MiniCraft
from .harness import Harness


class SimAgent:
    def __init__(self, harness: Harness, env_seed: int):
        lessons = harness.lessons()
        self.tags = harness.tags()
        self.shortcut = any(l["tag"] == "seed_shortcut" and env_seed in l.get("seeds", [])
                            for l in lessons)
        self.claim_always = "always_claim_success" in self.tags
        self._table_slip = False
        self._iron_slip = False
        self._rush_waste = 0

    def act(self, env: MiniCraft) -> str:
        if self.claim_always:
            return "DONE"  # reports success without doing the work
        t, inv = self.tags, env.inv
        mistakes_on = not self.shortcut
        rush = "rush" in t
        if env.hazard and env.steps >= 3 and not env.sheltered and "dodge_zombie" in t:
            return "shelter"
        if rush and env.task == "pickaxe" and self._rush_waste < 2:
            self._rush_waste += 1
            return "craft sword"  # rush hurts a skill the agent already had
        if rush and env.task == "sword":
            mistakes_on = False
        need_wood = ((0 if inv.get("table") else 2)
                     + (0 if inv.get("pickaxe") else 1)
                     + (1 if env.task == "sword" and not inv.get("sword") else 0))
        if inv.get("wood", 0) < need_wood:
            return "collect wood"
        if not inv.get("pickaxe") and inv.get("stone", 0) < 1:
            return "collect stone"
        if not inv.get("table"):
            if mistakes_on and "table_first" not in t and not self._table_slip:
                self._table_slip = True
                return "craft pickaxe"  # mistake: no table yet
            return "craft table"
        if not inv.get("pickaxe"):
            if (env.task == "sword" and mistakes_on and "tool_before_iron" not in t
                    and not self._iron_slip):
                self._iron_slip = True
                return "collect iron"  # mistake: no pickaxe yet
            return "craft pickaxe"
        if inv.get("iron", 0) < 1:
            return "collect iron"
        return "craft sword"


class LLMAgent:
    """Real-model agent. Works with any Provider (Gemini, mock, ...)."""

    def __init__(self, harness: Harness, provider):
        self.harness, self.provider = harness, provider
        self.tokens = 0
        self.last_prompt = self.last_raw = ""   # exposed for live displays
        self.last_cached = False
        self.last_call_id = ""
        self.last_latency_ms = 0
        self.call_ids: list = []

    def prompt(self, env: MiniCraft) -> str:
        mem = self.harness.memory_text() or "(none yet)"
        return (f"Lessons learned so far:\n{mem}\n\nObservation: {env.obs()}\n"
                f"Valid actions: {', '.join(ACTIONS)}\n"
                "Reply with exactly one valid action and nothing else.")

    def act(self, env: MiniCraft) -> str:
        self.last_prompt = self.prompt(env)
        c = self.provider.complete(self.last_prompt, system=self.harness.files["prompt.md"],
                                   max_tokens=16)
        self.last_raw, self.last_cached = c.text, c.cached
        self.last_call_id, self.last_latency_ms = c.call_id, c.latency_ms
        if c.call_id:
            self.call_ids.append(c.call_id)
        self.tokens += c.tokens_in + c.tokens_out
        text = c.text.strip().lower()
        for a in sorted(ACTIONS, key=len, reverse=True):
            if a in text:
                return a
        return "invalid"
