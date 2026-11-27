"""MiniCraft v4: a small SPATIAL survival-crafting game. This file is the single source of truth.

The world is a grid with real positions: forest (trees), quarry (stones), a walled mine with one
entrance (iron ore), a pond, the player, the table and shelter the player builds, and a zombie.
The agent (Gemini, a mock, or a human) makes one high-level DECISION per step, e.g. "collect wood".
The engine carries it out tick by tick: breadth-first pathfinding to the nearest reachable
resource around walls, water and objects, then the interaction. Resources run out. Crafting a
tool needs materials AND walking to the table. In hazard worlds a zombie appears at a fixed tick
(the groaning starts earlier), chases the player along its own path, and on contact steals all the
player's wood and then walks to the table and wrecks it. A shelter protects only while the player
is inside it; a sword kills the zombie on contact.

Every tick is recorded (`last_trace`) with the player's and zombie's positions and what happened,
so a viewer can replay exactly what the engine did. Nothing about the world is invented elsewhere.

Unchanged from v3 on purpose: the decision budget (steps), the recipes, the failure messages that
state the rule you broke, the hazard flag per world, and paired slips (common random numbers:
the n-th attempt of an action in a world always has the same slip outcome).
"""
from __future__ import annotations

import random
import zlib
from collections import deque

ENV_VERSION = 4  # v4: real positions, pathfinding, depleting resources, a chasing zombie
ACTIONS = ["collect wood", "collect stone", "collect iron",
           "craft table", "craft pickaxe", "craft sword", "shelter"]
MAX_STEPS = 14           # decisions per episode (one model call each)
SLIP = 0.10              # chance a gather attempt fails ("it slipped")
HAZARD_P = 0.4
W, H = 26, 15
START = (12, 7)
CUE_LEAD = 6             # the groaning starts this many ticks before the zombie appears
ZOMBIE_GIVES_UP = 3      # ticks spent blocked at a shelter before the zombie leaves
DAWN = 40                # ticks after it appears, the zombie leaves anyway

HIDE_MAX = DAWN + CUE_LEAD + ZOMBIE_GIVES_UP + 1   # "shelter" hides until the danger has passed (the zombie leaves by dawn at the latest)
RECIPES = {"table": {"wood": 2}, "pickaxe": {"wood": 1, "stone": 1}, "sword": {"wood": 1, "iron": 1}}
NEEDS_TABLE = ("pickaxe", "sword")
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
# kept for code that still refers to the v3 constants (the cue now comes from ticks, not steps)
CUE_STEP, ZOMBIE_STEP = 3, 5


def _adj(p):
    return [(p[0] + dx, p[1] + dy) for dx, dy in DIRS]


class MiniCraft:
    def __init__(self, seed: int, task: str, max_steps: int = MAX_STEPS, hazard_p: float = HAZARD_P):
        assert task in ("pickaxe", "sword")
        self.seed, self.task, self.max_steps = seed, task, max_steps
        self.hazard = random.Random(seed * 7919 + 13).random() < hazard_p   # same flag as v3
        self.inv: dict[str, int] = {}
        self.steps = 0
        self.tick = 0
        self.events: list[str] = []
        self.last = ""
        self._tries: dict[str, int] = {}
        self.table = None
        self.shelter = None
        self.player = START
        self._layout(seed)
        self.zombie = None       # {"x","y","state": hunt|blocked|wreck, "blocked": n}
        self.zombie_done = not self.hazard
        self.last_trace: list[dict] = []
        self.last_plan = ""      # what the engine did for the last decision, in words

    # ---- world generation (deterministic per seed) ----------------------------------------
    def _layout(self, seed: int) -> None:
        r = random.Random(seed * 104729 + 7)
        for _ in range(50):
            self.walls, self.water = set(), set()
            for y in range(0, 7):
                self.walls.add((19, y))
            for x in range(19, W):
                self.walls.add((x, 6))
            self.entrance = r.choice([(19, 2), (19, 4), (21, 6), (23, 6)])
            self.walls.discard(self.entrance)
            py = r.randint(1, 4)
            self.water = {(x, y) for x in (15, 16) for y in range(py, py + 4)}
            taken = set(self.walls) | self.water | {START}

            def scatter(x0, x1, y0, y1, n, units):
                out = {}
                for _ in range(600):
                    if len(out) >= n:
                        break
                    p = (r.randint(x0, x1), r.randint(y0, y1))
                    if any(max(abs(p[0] - q[0]), abs(p[1] - q[1])) < 2 for q in taken):
                        continue
                    out[p] = units
                    taken.add(p)
                return out

            self.trees = scatter(1, 8, 1, 13, 12, 2)
            self.stones = scatter(9, 15, 10, 13, 5, 2)
            self.ores = scatter(21, 24, 1, 4, 4, 1)
            self.zombie_start = (W - 1, r.randint(9, 13))
            self.zombie_tick = 12 + r.randint(0, 6)
            if self._valid():
                return
        raise RuntimeError(f"could not generate a valid world for seed {seed}")

    def _valid(self) -> bool:
        reach = self._bfs_all(START, self._free_player)
        for objs in (self.trees, self.stones, self.ores):
            for p in objs:
                if not any(q in reach for q in _adj(p)):
                    return False
        return self.zombie_start in reach and len(self.trees) >= 10 and len(self.ores) >= 3

    # ---- grid queries -----------------------------------------------------------------------
    def _inside(self, p) -> bool:
        return 0 <= p[0] < W and 0 <= p[1] < H

    def _blocked(self, p) -> bool:
        return (p in self.walls or p in self.water or p in self.trees or p in self.stones
                or p in self.ores or p == self.table)

    def _free_player(self, p) -> bool:
        return self._inside(p) and not self._blocked(p)

    def _free_zombie(self, p) -> bool:
        return self._free_player(p) and p != self.shelter and p != self.player

    def _bfs_all(self, start, free) -> dict:
        seen, q = {start: None}, deque([start])
        while q:
            c = q.popleft()
            for n in _adj(c):
                if n not in seen and free(n):
                    seen[n] = c
                    q.append(n)
        return seen

    def _path(self, start, goals: set, free) -> list | None:
        """Shortest path (list of tiles after start) to the nearest goal tile, or None."""
        if start in goals:
            return []
        seen, q = {start: None}, deque([start])
        while q:
            c = q.popleft()
            for n in _adj(c):
                if n in seen or not free(n):
                    continue
                seen[n] = c
                if n in goals:
                    path = [n]
                    while seen[path[-1]] != start:
                        path.append(seen[path[-1]])
                    return path[::-1]
                q.append(n)
        return None

    def _approach(self, objs) -> tuple:
        """Nearest object (by walking distance) among `objs`, with the path to stand next to it."""
        stand = {}
        for o in sorted(objs, key=lambda p: (p[1], p[0])):
            for a in _adj(o):
                if self._free_player(a) or a == self.player:
                    stand.setdefault(a, o)
        if not stand:
            return None, None
        path = self._path(self.player, set(stand), self._free_player)
        if path is None:
            return None, None
        end = path[-1] if path else self.player
        return stand[end], path

    def distance_to(self, objs) -> int | None:
        _, path = self._approach(objs) if objs else (None, None)
        return None if path is None else len(path)

    # ---- state ------------------------------------------------------------------------------
    @property
    def sheltered(self) -> bool:
        return self.shelter is not None and self.player == self.shelter

    def success(self) -> bool:
        return self.inv.get(self.task, 0) >= 1

    def done(self) -> bool:
        return self.success() or self.steps >= self.max_steps

    def _danger(self) -> bool:
        if not self.hazard or self.zombie_done:
            return False
        return self.zombie is not None or self.tick >= self.zombie_tick - CUE_LEAD

    def cue(self) -> bool:
        return self._danger() and not self.sheltered

    def _dist_text(self, d, what):
        return f"{what}: none reachable" if d is None else f"{what}: {d} steps away"

    def obs(self) -> str:
        s = (f"Goal: craft a {self.task}. Step {self.steps}/{self.max_steps}. "
             f"Inventory: {self.inv or 'empty'}.")
        s += f" You are at {self.player} (time: tick {self.tick})."
        near = [self._dist_text(self.distance_to({p for p, n in self.trees.items() if n}), "Nearest tree with wood"),
                self._dist_text(self.distance_to({p for p, n in self.stones.items() if n}), "Nearest stone"),
                self._dist_text(self.distance_to({p for p, n in self.ores.items() if n}), "Nearest iron ore (in the mine)")]
        near.append("Table: none" if self.table is None else self._dist_text(self.distance_to({self.table}), "Table"))
        if self.shelter is not None and not self.sheltered:
            near.append(f"Shelter: {len(self._path(self.player, {self.shelter}, self._free_player) or [])} steps away")
        s += " " + ". ".join(near) + "."
        if self.last:
            s += f" {self.last}"
        if self.sheltered:
            s += " You are in a shelter."
        if self.zombie is not None:
            d = abs(self.zombie["x"] - self.player[0]) + abs(self.zombie["y"] - self.player[1])
            s += f" A zombie is {d} tiles away!"
        elif self.cue():
            s += " You hear groaning nearby."
        return s

    # ---- the clock: every tick moves the zombie and records a frame ---------------------------
    def _frame(self, act: str, target=None, note: str = "", left=None) -> None:
        self.tick += 1
        zev = self._zombie_tick()
        z = None if self.zombie is None else [self.zombie["x"], self.zombie["y"], self.zombie["state"]]
        self.last_trace.append({"t": self.tick, "p": list(self.player), "a": act,
                                "tg": list(target) if target else None, "n": note, "z": z, "ze": zev,
                                "inv": dict(self.inv), "table": list(self.table) if self.table else None,
                                "shelter": list(self.shelter) if self.shelter else None, "left": left})

    def _zombie_tick(self) -> str:
        if self.zombie_done:
            return ""
        if self.zombie is None:
            if self.tick >= self.zombie_tick:
                self.zombie = {"x": self.zombie_start[0], "y": self.zombie_start[1], "state": "hunt", "blocked": 0}
                return "zombie_appears"
            return ""
        z = self.zombie
        zp = (z["x"], z["y"])
        if self.tick >= self.zombie_tick + DAWN:
            self._zombie_leaves()
            return "zombie_leaves_dawn"
        if z["state"] == "wreck":
            if self.table is None:
                self._zombie_leaves()
                return "zombie_leaves"
            if zp in _adj(self.table):
                self.table = None
                self.inv.pop("table", None)
                self.events.append("table_wrecked")
                self._zombie_leaves()
                return "table_wrecked"
            self._zombie_step(set(_adj(self.table)))
            return ""
        if zp not in _adj(self.player):
            self._zombie_step(set(_adj(self.player)))
            zp = (z["x"], z["y"])
            if zp not in _adj(self.player):
                if z["state"] == "blocked":
                    z["state"] = "hunt"
                return ""
        # contact
        if self.inv.get("sword"):
            self.events.append("zombie_slain")
            self._zombie_leaves()
            return "zombie_slain"
        if self.sheltered:
            z["state"] = "blocked"
            z["blocked"] += 1
            if z["blocked"] >= ZOMBIE_GIVES_UP:
                self._zombie_leaves()
                return "zombie_gives_up"
            return "zombie_blocked"
        self.inv.pop("wood", None)
        self.events.append("zombie_wipe")
        self._zombie_hit = True
        if self.table is not None:
            z["state"] = "wreck"
        else:
            self._zombie_leaves()
        return "zombie_attack"

    def _zombie_step(self, goals: set) -> None:
        z = self.zombie
        path = self._path((z["x"], z["y"]), goals, self._free_zombie)
        if path:
            z["x"], z["y"] = path[0]

    def _zombie_leaves(self) -> None:
        self.zombie, self.zombie_done = None, True

    # ---- one decision ---------------------------------------------------------------------
    def _slips(self, action: str) -> bool:
        k = self._tries.get(action, 0)
        self._tries[action] = k + 1
        key = zlib.crc32(f"{self.seed}:{action}:{k}".encode())
        return random.Random(key).random() < SLIP

    def _fail(self, reason: str, msg: str) -> None:
        self.events.append(reason)
        self._msg = msg

    def _walk(self, path) -> None:
        for p in path:
            self.player = p
            self._frame("walk")

    def step(self, action: str) -> str:
        self.steps += 1
        self.last_trace, self._msg, self._zombie_hit = [], "", False
        n_before = len(self.events)
        self._do(action)
        new = [e for e in self.events[n_before:]
               if e not in ("zombie_wipe", "table_wrecked", "zombie_slain")]
        self.last = (f"Last action '{action}' failed: {self._msg}." if new
                     else f"Last action '{action}' worked{': ' + self.last_plan if self.last_plan else ''}.")
        if self._zombie_hit:
            self.last += " A zombie attacked: it stole all your wood" + (
                " and is going for your table." if self.zombie and self.zombie["state"] == "wreck" else ".")
        if "table_wrecked" in self.events[n_before:]:
            self.last += " The zombie wrecked your table."
        if "zombie_slain" in self.events[n_before:]:
            self.last += " You killed the zombie with your sword."
        return self.obs()

    def _gather(self, item: str, objs: dict, kind: str) -> None:
        live = {p for p, n in objs.items() if n > 0}
        target, path = self._approach(live)
        if target is None:
            self._fail("no_target", f"no reachable {kind} left")
            self._frame("think")
            self.last_plan = ""
            return
        self._walk(path)
        if item == "iron" and not self.inv.get("pickaxe"):
            self._fail("no_pickaxe", "collect iron needs a pickaxe in your inventory")
            self._frame("fail", target)
            self.last_plan = ""
            return
        if self._slips(f"collect {item}"):
            self._fail("slip", "it slipped, try again")
            self._frame("slip", target)
            return
        objs[target] -= 1
        self.inv[item] = self.inv.get(item, 0) + 1
        self._frame("gather", target, f"+1 {item}", left=objs[target])
        self.last_plan = f"walked {len(path)} steps to the {kind} at {target} and got 1 {item}"

    def _has(self, *items) -> str:
        return ", ".join(f"{self.inv.get(i, 0)} {i}" for i in items)

    def _do(self, action: str) -> None:
        self.last_plan = ""
        inv = self.inv
        if action == "collect wood":
            return self._gather("wood", self.trees, "tree")
        if action == "collect stone":
            return self._gather("stone", self.stones, "stone")
        if action == "collect iron":
            return self._gather("iron", self.ores, "iron ore")
        if action == "craft table":
            if inv.get("wood", 0) < 2:
                self._fail("no_materials", f"craft table needs 2 wood (you have {self._has('wood')})")
                return self._frame("think")
            spot = next((a for a in _adj(self.player) if self._free_player(a) and a != self.shelter), None)
            if spot is None:
                self._fail("no_space", "no free tile next to you for a table")
                return self._frame("think")
            inv["wood"] -= 2
            self.table, inv["table"] = spot, 1
            self._frame("craft", spot, "+ table")
            self.last_plan = f"built a table at {spot}"
            return
        if action in ("craft pickaxe", "craft sword"):
            item = action.split()[1]
            if self.table is None:
                self._fail("no_table", f"{action} needs a table")
                return self._frame("think")
            need = RECIPES[item]
            if any(inv.get(k, 0) < v for k, v in need.items()):
                self._fail("no_materials", f"{action} needs " + " and ".join(f"{v} {k}" for k, v in need.items())
                           + f" (you have {self._has(*need)})")
                return self._frame("think")
            _, path = self._approach({self.table})
            if path is None:
                self._fail("no_target", "cannot reach your table")
                return self._frame("think")
            self._walk(path)
            # the zombie may have struck during the walk: check again on arrival
            if self.table is None:
                self._fail("no_table", f"{action} needs a table (yours was wrecked)")
                return self._frame("fail")
            if any(inv.get(k, 0) < v for k, v in need.items()):
                self._fail("no_materials", f"{action} needs " + " and ".join(f"{v} {k}" for k, v in need.items())
                           + f" (you have {self._has(*need)})")
                return self._frame("fail", self.table)
            for k, v in need.items():
                inv[k] -= v
            inv[item] = 1
            self._frame("craft", self.table, f"+ {item}")
            self.last_plan = f"walked {len(path)} steps to the table and crafted a {item}"
            return
        if action == "shelter":
            if self.shelter is None:
                self.shelter = self.player
                self._frame("build", self.player)
                self._frame("build", self.player, "+ shelter")
                built = f"built a shelter at {self.shelter}"
            else:
                path = self._path(self.player, {self.shelter}, self._free_player)
                self._walk(path or [])
                built = f"walked {len(path or [])} steps back into the shelter"
            waited = 0
            while self._danger() and waited < HIDE_MAX:
                self._frame("hide", self.shelter)
                waited += 1
            self.last_plan = built + (f" and hid for {waited} ticks until the danger passed" if waited else "")
            return
        self._fail("bad_action", f"'{action}' is not a valid action")
        self._frame("think")

    # ---- for viewers (never shown to the agent) -------------------------------------------
    def still_needed(self) -> list[str]:
        inv, out = self.inv, []
        if self.success():
            return []
        need = dict(RECIPES[self.task])
        if self.task == "sword" and not inv.get("pickaxe") and inv.get("iron", 0) < 1:
            need["pickaxe (to mine iron)"] = 1
        if self.table is None:
            out.append("a table (2 wood)")
        for k, v in need.items():
            if k.startswith("pickaxe"):
                out.append("a pickaxe (1 wood + 1 stone) to mine iron")
            elif inv.get(k, 0) < v:
                out.append(f"{v - inv.get(k, 0)} more {k}")
        return out

    def static(self) -> dict:
        return {"w": W, "h": H, "walls": sorted(map(list, self.walls)), "water": sorted(map(list, self.water)),
                "entrance": list(self.entrance), "start": list(START), "recipes": RECIPES}

    def snapshot(self) -> dict:
        z = None if self.zombie is None else [self.zombie["x"], self.zombie["y"], self.zombie["state"]]
        return {"tick": self.tick, "player": list(self.player),
                "trees": [[p[0], p[1], n] for p, n in sorted(self.trees.items())],
                "stones": [[p[0], p[1], n] for p, n in sorted(self.stones.items())],
                "ores": [[p[0], p[1], n] for p, n in sorted(self.ores.items())],
                "table": list(self.table) if self.table else None,
                "shelter": list(self.shelter) if self.shelter else None,
                "zombie": z, "zombie_coming": bool(self.hazard and not self.zombie_done and self.zombie is None
                                                   and self.tick >= self.zombie_tick - CUE_LEAD),
                "in_shelter": self.sheltered, "needed": self.still_needed()}
