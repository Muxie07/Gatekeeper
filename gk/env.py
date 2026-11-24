"""MiniCraft: a tiny deterministic crafting game.

Not Crafter. It is a purpose-built environment where mistakes are well defined
(so the gate can be tested), episodes are cheap, and every outcome is verified
from environment state rather than from what the agent says.
"""
from __future__ import annotations

import random
import zlib

ENV_VERSION = 2  # v2: paired (common-random-number) slips
ACTIONS = ["collect wood", "collect stone", "collect iron",
           "craft table", "craft pickaxe", "craft sword", "shelter"]
MAX_STEPS = 13
SLIP = 0.10  # chance a collect action silently fails


class MiniCraft:
    def __init__(self, seed: int, task: str, max_steps: int = MAX_STEPS):
        assert task in ("pickaxe", "sword")
        self.seed, self.task, self.max_steps = seed, task, max_steps
        self.hazard = random.Random(seed * 7919 + 13).random() < 0.4
        self.inv: dict[str, int] = {}
        self.steps = 0
        self.sheltered = False
        self.events: list[str] = []  # failure reasons, used to "learn from mistakes"
        self.last = ""               # feedback on the previous action, shown to the agent
        self._tries: dict[str, int] = {}

    def success(self) -> bool:
        return self.inv.get(self.task, 0) >= 1

    def done(self) -> bool:
        return self.success() or self.steps >= self.max_steps

    def obs(self) -> str:
        s = (f"Goal: craft a {self.task}. Step {self.steps}/{self.max_steps}. "
             f"Inventory: {self.inv or 'empty'}.")
        if self.last:
            s += f" {self.last}"
        if self.hazard and self.steps >= 3 and not self.sheltered:
            s += " You hear groaning nearby."
        return s

    def _slips(self, action: str) -> bool:
        # Common random numbers (env v2): the n-th attempt of the same action in a world always
        # has the same outcome, whatever happened before. Two harness versions playing the same
        # world therefore face the same luck, so paired comparisons cancel the noise.
        # (v1 keyed on the step number, so any change in play reshuffled every later slip.)
        k = self._tries.get(action, 0)
        self._tries[action] = k + 1
        key = zlib.crc32(f"{self.seed}:{action}:{k}".encode())
        return random.Random(key).random() < SLIP

    def _fail(self, reason: str) -> None:
        self.events.append(reason)

    HINT = {"no_materials": "not enough materials", "no_table": "you need a table first",
            "no_pickaxe": "you need a pickaxe first", "slip": "it slipped, try again",
            "bad_action": "not a valid action", "zombie_wipe": "a zombie stole your wood"}

    def step(self, action: str) -> str:
        n_before = len(self.events)
        self._do(action)
        new = self.events[n_before:]
        self.last = (f"Last action '{action}' failed: {self.HINT.get(new[0], new[0])}."
                     if new and new[0] != "zombie_wipe" else f"Last action '{action}' worked.")
        if "zombie_wipe" in new:
            self.last += " " + self.HINT["zombie_wipe"] + "."
        return self.obs()

    def _do(self, action: str) -> None:
        self.steps += 1
        inv = self.inv
        if action.startswith("collect "):
            item = action[8:]
            if item not in ("wood", "stone", "iron"):
                self._fail("bad_action")
            elif item == "iron" and not inv.get("pickaxe"):
                self._fail("no_pickaxe")
            elif self._slips(action):
                self._fail("slip")
            else:
                inv[item] = inv.get(item, 0) + 1
        elif action == "craft table":
            if inv.get("wood", 0) >= 2:
                inv["wood"] -= 2
                inv["table"] = 1
            else:
                self._fail("no_materials")
        elif action == "craft pickaxe":
            if not inv.get("table"):
                self._fail("no_table")
            elif inv.get("wood", 0) >= 1 and inv.get("stone", 0) >= 1:
                inv["wood"] -= 1
                inv["stone"] -= 1
                inv["pickaxe"] = 1
            else:
                self._fail("no_materials")
        elif action == "craft sword":
            if not inv.get("table"):
                self._fail("no_table")
            elif inv.get("wood", 0) >= 1 and inv.get("iron", 0) >= 1:
                inv["wood"] -= 1
                inv["iron"] -= 1
                inv["sword"] = 1
            else:
                self._fail("no_materials")
        elif action == "shelter":
            self.sheltered = True
        else:
            self._fail("bad_action")
        if self.hazard and self.steps == 5 and not self.sheltered:
            inv.pop("wood", None)
            self._fail("zombie_wipe")
