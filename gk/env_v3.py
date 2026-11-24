"""LEGACY MiniCraft v3 (no positions). Kept unchanged for the scripted simulation (gk/loop.py,
SimAgent) and its tests. Real runs, the live app and human play use the spatial v4 engine in gk/env.py.

MiniCraft: a tiny deterministic crafting game.

Not Crafter. It is a purpose-built environment where mistakes are well defined
(so the gate can be tested), episodes are cheap, and every outcome is verified
from environment state rather than from what the agent says.

v3: the agent is NOT told the recipes. The game states the rule when you break it
("craft pickaxe needs a table"), so requirements are discovered from failure feedback.
Zombies are decisive: an unsheltered agent in a hazard world loses all its wood AND its
table at step 5. A shelter costs one step, which a tight step budget makes expensive.
"""
from __future__ import annotations

import random
import zlib

ENV_VERSION = 3  # v3: unknown recipes, truthful rule feedback, table-wrecking zombies
ACTIONS = ["collect wood", "collect stone", "collect iron",
           "craft table", "craft pickaxe", "craft sword", "shelter"]
MAX_STEPS = 14
SLIP = 0.10  # chance a collect action silently fails
HAZARD_P = 0.4
ZOMBIE_STEP = 5
CUE_STEP = 3


class MiniCraft:
    def __init__(self, seed: int, task: str, max_steps: int = MAX_STEPS, hazard_p: float = HAZARD_P):
        assert task in ("pickaxe", "sword")
        self.seed, self.task, self.max_steps = seed, task, max_steps
        self.hazard = random.Random(seed * 7919 + 13).random() < hazard_p
        self.inv: dict[str, int] = {}
        self.steps = 0
        self.sheltered = False
        self.events: list[str] = []  # failure reasons, used to "learn from mistakes"
        self.last = ""               # feedback on the previous action, shown to the agent
        self._msg = ""
        self._tries: dict[str, int] = {}

    def success(self) -> bool:
        return self.inv.get(self.task, 0) >= 1

    def done(self) -> bool:
        return self.success() or self.steps >= self.max_steps

    def cue(self) -> bool:
        return self.hazard and self.steps >= CUE_STEP and not self.sheltered

    def obs(self) -> str:
        s = (f"Goal: craft a {self.task}. Step {self.steps}/{self.max_steps}. "
             f"Inventory: {self.inv or 'empty'}.")
        if self.last:
            s += f" {self.last}"
        if self.sheltered:
            s += " You are in a shelter."
        elif self.cue():
            s += " You hear groaning nearby."
        return s

    def _slips(self, action: str) -> bool:
        # Common random numbers: the n-th attempt of the same action in a world always has
        # the same outcome, whatever happened before. Two harness versions playing the same
        # world therefore face the same luck, so paired comparisons cancel the noise.
        k = self._tries.get(action, 0)
        self._tries[action] = k + 1
        key = zlib.crc32(f"{self.seed}:{action}:{k}".encode())
        return random.Random(key).random() < SLIP

    def _fail(self, reason: str, msg: str = "") -> None:
        self.events.append(reason)
        self._msg = msg or reason

    def step(self, action: str) -> str:
        n_before = len(self.events)
        self._do(action)
        new = self.events[n_before:]
        fails = [e for e in new if e != "zombie_wipe"]
        self.last = (f"Last action '{action}' failed: {self._msg}." if fails
                     else f"Last action '{action}' worked.")
        if "zombie_wipe" in new:
            self.last += " A zombie attacked: it stole all your wood and wrecked your table."
        return self.obs()

    def _has(self, *items) -> str:
        return ", ".join(f"{self.inv.get(i, 0)} {i}" for i in items)

    def _do(self, action: str) -> None:
        self.steps += 1
        inv = self.inv
        if action.startswith("collect "):
            item = action[8:]
            if item not in ("wood", "stone", "iron"):
                self._fail("bad_action", f"'{action}' is not a valid action")
            elif item == "iron" and not inv.get("pickaxe"):
                self._fail("no_pickaxe", "collect iron needs a pickaxe in your inventory")
            elif self._slips(action):
                self._fail("slip", "it slipped, try again")
            else:
                inv[item] = inv.get(item, 0) + 1
        elif action == "craft table":
            if inv.get("wood", 0) >= 2:
                inv["wood"] -= 2
                inv["table"] = 1
            else:
                self._fail("no_materials", f"craft table needs 2 wood (you have {self._has('wood')})")
        elif action == "craft pickaxe":
            if not inv.get("table"):
                self._fail("no_table", "craft pickaxe needs a table")
            elif inv.get("wood", 0) >= 1 and inv.get("stone", 0) >= 1:
                inv["wood"] -= 1
                inv["stone"] -= 1
                inv["pickaxe"] = 1
            else:
                self._fail("no_materials",
                           f"craft pickaxe needs 1 wood and 1 stone (you have {self._has('wood', 'stone')})")
        elif action == "craft sword":
            if not inv.get("table"):
                self._fail("no_table", "craft sword needs a table")
            elif inv.get("wood", 0) >= 1 and inv.get("iron", 0) >= 1:
                inv["wood"] -= 1
                inv["iron"] -= 1
                inv["sword"] = 1
            else:
                self._fail("no_materials",
                           f"craft sword needs 1 wood and 1 iron (you have {self._has('wood', 'iron')})")
        elif action == "shelter":
            self.sheltered = True
        else:
            self._fail("bad_action", f"'{action}' is not a valid action")
        if self.hazard and self.steps == ZOMBIE_STEP and not self.sheltered:
            inv.pop("wood", None)
            inv.pop("table", None)
            self.events.append("zombie_wipe")


MiniCraftV3 = MiniCraft
