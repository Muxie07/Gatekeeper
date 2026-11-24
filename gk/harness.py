"""A harness is a small set of text files: prompt, memory (lessons), tools.

It is content-hashed, so every version has a stable id, and immutable, so
rollback is just "go back to a previous Harness object".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .env import ACTIONS

DEFAULT_FILES = {
    # env v3: no recipes here. The agent discovers requirements from the game's failure feedback.
    "prompt.md": ("You are a crafting agent in a small survival game. Craft the goal item in as "
                  "few steps as possible. Each action, including a failed one, uses one step. "
                  "Read the feedback after every action."),
    "memory.json": "[]",
    "tools.json": json.dumps(ACTIONS),
}
BASE_TOKENS_PER_STEP = 200  # observation + instructions, same for every version


@dataclass(frozen=True)
class Harness:
    files: dict

    @staticmethod
    def initial() -> "Harness":
        return Harness(dict(DEFAULT_FILES))

    @property
    def version(self) -> str:
        blob = json.dumps(self.files, sort_keys=True).encode()
        return hashlib.sha1(blob).hexdigest()[:10]

    def lessons(self) -> list[dict]:
        return json.loads(self.files.get("memory.json", "[]"))

    def tags(self) -> set[str]:
        return {l["tag"] for l in self.lessons()}

    def memory_text(self) -> str:
        return "\n".join("- " + l["text"] for l in self.lessons())

    def with_patch(self, patch: dict) -> "Harness":
        files = dict(self.files)
        files.update(patch)
        return Harness(files)

    def tokens_per_step(self) -> int:
        extra = (len(self.files["prompt.md"]) + len(self.memory_text())) // 4
        return BASE_TOKENS_PER_STEP + extra


def add_lessons(h: Harness, new: list[dict]) -> dict:
    """Build a patch that appends lessons to memory.json."""
    return {"memory.json": json.dumps(h.lessons() + new)}


PLACEBO_SENTENCE = "Keep this note in mind while you play the game. "


def placebo(h: Harness, length: int = 90) -> Harness:
    """Parent + a content-free lesson of about `length` characters (an attention control)."""
    text = (PLACEBO_SENTENCE * (length // len(PLACEBO_SENTENCE) + 1))[:length].strip()
    return h.with_patch(add_lessons(h, [{"tag": "placebo", "text": text}]))


def added_text(parent: Harness, child: Harness) -> str:
    """The lesson text a candidate adds to its parent (empty if it adds none)."""
    old = {l["text"] for l in parent.lessons()}
    return " ".join(l["text"] for l in child.lessons() if l["text"] not in old)


def placebo_for(parent: Harness, child: Harness, default: int = 90) -> Harness:
    """Content-free control whose length matches the candidate's added text (deterministic, so the
    cache key is stable). Candidates that add no text get the default length."""
    n = len(added_text(parent, child)) or default
    return placebo(parent, max(n, 20))
