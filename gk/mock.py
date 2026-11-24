"""Mock models for development and tests (badge: MOCK / SIMULATED, never mixed with real runs).

mock_player   : a naive stand-in for an LLM that plays MiniCraft v3. It reads the observation and
                the harness memory. It blunders in a few realistic ways (crafts a table with 1 wood,
                ignores the zombie cue) unless a lesson in memory tells it otherwise.
mock_proposer : reads the failure counts in the proposer prompt and writes one lesson, like the real
                proposer would. Style is detected from the prompt (honest / overfit / adversarial).
"""
from __future__ import annotations

import ast
import re

LESSONS = {   # failure reason -> lesson text, in priority order
    "zombie_wipe": "When you hear groaning, build a shelter right away before continuing.",
    "no_materials": "Count your inventory against the cost in the failure message before crafting.",
    "no_table": "Build a table before crafting any tool.",
    "no_pickaxe": "Craft a pickaxe before trying to collect iron.",
}


def mock_player(prompt: str) -> str:
    mem, obs = prompt.split("Observation:")[0].lower(), prompt.split("Observation:")[1]
    inv_txt = re.search(r"Inventory: (\{.*?\}|empty)", obs).group(1)
    inv = {} if inv_txt == "empty" else ast.literal_eval(inv_txt)
    task = re.search(r"craft a (\w+)", obs).group(1)
    failed = "failed" in obs
    sheltered = "in a shelter" in obs
    cue = "groaning" in obs
    careful = "count" in mem
    o = obs.split("Valid actions")[0].lower()
    built = "in a shelter" in o or "shelter:" in o or "built a shelter" in o
    if "very first action" in mem and not built:   # v4: build it once, at the start
        return "shelter"
    if cue and "groan" in mem and not sheltered:
        return "shelter"
    wood = inv.get("wood", 0)
    if not inv.get("table"):
        if wood == 1 and not careful and not failed:
            return "craft table"          # the avoidable mistake: only 1 wood
        return "collect wood" if wood < 2 else "craft table"
    if not inv.get("pickaxe"):
        if wood < 1 + (1 if task == "sword" else 0):
            return "collect wood"
        return "collect stone" if inv.get("stone", 0) < 1 else "craft pickaxe"
    if task == "pickaxe":
        return "craft pickaxe"
    if wood < 1:
        return "collect wood"
    return "collect iron" if inv.get("iron", 0) < 1 else "craft sword"


def mock_proposer(prompt: str) -> str:
    if "Ignore step cost" in prompt or "Ignore step cost".lower() in prompt.lower():
        return '{"lesson": "Always build a shelter as your very first action, and hide in it whenever you hear groaning."}'
    if "SPECIFIC to exactly these worlds" in prompt:
        return '{"lesson": "In worlds 3 and 5 collect iron first, then skip the table."}'
    counts = {}
    m = re.search(r"\(counts\): (\{.*?\})", prompt)
    if m:
        counts = ast.literal_eval(m.group(1))
    current = prompt.split("Avoidable mistakes")[0].lower()
    rejected = prompt.split("Already tried and rejected")[-1].lower() if "Already tried" in prompt else ""
    order = list(LESSONS)   # v4: fix the most frequent mistake first (ties: the order above)
    for reason in sorted(LESSONS, key=lambda r: (-counts.get(r, 0), order.index(r))):
        text = LESSONS[reason]
        if counts.get(reason) and text.lower() not in current and text.lower() not in rejected:
            return '{"lesson": "%s"}' % text
    return "{}"
