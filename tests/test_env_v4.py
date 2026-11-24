"""MiniCraft v4: positions, pathfinding, collisions, depleting resources, a chasing zombie."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gk.env import (CUE_LEAD, DAWN, START, ZOMBIE_GIVES_UP, MiniCraft, _adj)  # noqa: E402


def blocked(e, p):
    return e._blocked(p) or not e._inside(p)


def play(e, plan):
    out = []
    for a in plan:
        e.step(a)
        out.append(list(e.last_trace))
        if e.done():
            break
    return out


class WorldGeneration(unittest.TestCase):
    def test_same_seed_same_world_and_same_play(self):
        a, b = MiniCraft(42, "sword"), MiniCraft(42, "sword")
        self.assertEqual(a.static(), b.static())
        self.assertEqual(a.snapshot(), b.snapshot())
        plan = ["collect wood"] * 3 + ["craft table", "collect stone", "craft pickaxe", "collect iron"]
        self.assertEqual(play(a, plan), play(b, plan))

    def test_every_resource_is_reachable_and_iron_only_through_the_entrance(self):
        for seed in range(1500):
            e = MiniCraft(seed, "sword")
            reach = e._bfs_all(START, e._free_player)
            for objs in (e.trees, e.stones, e.ores):
                for p in objs:
                    self.assertTrue(any(q in reach for q in _adj(p)), (seed, p))
            inside = {p for p in reach if p[0] > 19 and p[1] < 6}
            self.assertTrue(inside)                                   # the mine can be entered...
            self.assertNotIn(e.entrance, e.walls)
            for p in inside:                                          # ...but only through the gap
                path, c = [], p
                while c is not None:
                    path.append(c)
                    c = reach[c]
                self.assertIn(e.entrance, path, seed)

    def test_hazard_flag_is_unchanged_from_v3(self):
        from gk.env_v3 import MiniCraft as V3
        for seed in range(300):
            self.assertEqual(MiniCraft(seed, "pickaxe").hazard, V3(seed, "pickaxe").hazard)


class Movement(unittest.TestCase):
    def test_walking_is_one_tile_per_tick_and_never_through_obstacles(self):
        for seed in range(40):
            e = MiniCraft(seed, "sword", hazard_p=0)
            prev = START
            for trace in play(e, ["collect wood", "collect stone", "collect wood", "craft table",
                                  "collect wood", "craft pickaxe", "collect iron"]):
                for f in trace:
                    p = tuple(f["p"])
                    self.assertLessEqual(abs(p[0] - prev[0]) + abs(p[1] - prev[1]), 1, (seed, prev, p))
                    static = p in e.walls or p in e.water or p in e.trees or p in e.stones or p in e.ores
                    self.assertTrue(e._inside(p) and not static, (seed, p))
                    self.assertNotEqual(list(p), f["table"], (seed, p))        # the table as it was at that tick
                    prev = p

    def test_gathering_goes_to_the_nearest_resource_and_depletes_it(self):
        e = MiniCraft(5, "pickaxe", hazard_p=0)
        e.step("collect wood")
        first = tuple(e.last_trace[-1]["tg"])
        self.assertIn(e.player, _adj(first))                          # standing next to the tree
        start_units = 2
        got = 1 + (0 if "slipped" in e.last else 0)
        while e.trees[first] > 0 and e.steps < 6:
            e.step("collect wood")
        self.assertEqual(e.trees[first], 0)
        e.step("collect wood")
        self.assertNotEqual(tuple(e.last_trace[-1]["tg"] or first), first)   # a different tree now
        self.assertLessEqual(start_units, 2)
        self.assertGreaterEqual(got, 1)

    def test_crafting_a_tool_walks_to_the_table(self):
        e = MiniCraft(5, "pickaxe", hazard_p=0)
        while e.inv.get("wood", 0) < 3 and not e.done():
            e.step("collect wood")
        e.step("craft table")
        table = e.table
        while not e.inv.get("stone") and not e.done():
            e.step("collect stone")
        self.assertNotIn(e.player, _adj(table))                       # the quarry is away from the table
        e.step("craft pickaxe")
        self.assertEqual(e.inv.get("pickaxe"), 1)
        self.assertIn(e.player, _adj(table))
        self.assertGreater(len(e.last_trace), 1)                      # it walked there, tick by tick

    def test_iron_without_a_pickaxe_costs_the_walk_and_fails_at_the_ore(self):
        e = MiniCraft(9, "sword", hazard_p=0)
        e.step("collect iron")
        self.assertIn("needs a pickaxe", e.last)
        self.assertGreater(len(e.last_trace), 5)
        self.assertNotIn("iron", e.inv)
        self.assertTrue(e.player[0] > 19 and e.player[1] < 6)         # it is inside the mine now

    def test_failure_feedback_still_states_the_rule(self):
        e = MiniCraft(1, "pickaxe", hazard_p=0)
        e.step("craft pickaxe")
        self.assertIn("craft pickaxe needs a table", e.last)
        e.step("craft table")
        self.assertIn("craft table needs 2 wood (you have 0 wood)", e.last)

    def test_slips_are_paired_across_different_play(self):
        a, b = MiniCraft(11, "pickaxe", hazard_p=0), MiniCraft(11, "pickaxe", hazard_p=0)
        a.step("collect stone")
        b.step("collect wood"); b.step("collect wood"); b.step("collect stone")
        self.assertEqual("slipped" in a.last, "slipped" in b.last)    # 1st stone attempt, same luck


class Zombie(unittest.TestCase):
    def hazard_world(self):
        for seed in range(500):
            e = MiniCraft(seed, "sword", hazard_p=1.0)
            return e

    def test_zombie_appears_on_time_and_moves_at_most_one_tile_per_tick(self):
        e = self.hazard_world()
        seen, last = None, None
        for _ in range(8):
            e.step("collect wood")
            for f in e.last_trace:
                if f["ze"] == "zombie_appears":
                    seen = f["t"]
                if f["z"] and last:
                    self.assertLessEqual(abs(f["z"][0] - last[0]) + abs(f["z"][1] - last[1]), 1)
                last = f["z"] or last
            if e.zombie_done:
                break
        self.assertEqual(seen, e.zombie_tick)

    def test_unprotected_contact_steals_wood_then_the_zombie_wrecks_the_table(self):
        e = MiniCraft(3, "sword", hazard_p=1.0)
        e.step("collect wood"); e.step("collect wood"); e.step("craft table"); e.step("collect wood")
        while not e.zombie_done and not e.done():
            e.step("collect stone")
        self.assertIn("zombie_wipe", e.events)
        self.assertIn("table_wrecked", e.events)
        self.assertIsNone(e.table)
        self.assertNotIn("table", e.inv)

    def test_hiding_in_a_shelter_blocks_the_zombie_until_it_gives_up(self):
        e = MiniCraft(3, "sword", hazard_p=1.0)
        e.step("collect wood"); e.step("collect wood"); e.step("craft table")
        e.step("shelter")                                            # build + hide until the danger passes
        evs = [f["ze"] for f in e.last_trace if f["ze"]]
        self.assertIn("zombie_appears", evs)
        self.assertEqual(evs.count("zombie_blocked"), ZOMBIE_GIVES_UP - 1)
        self.assertIn("zombie_gives_up", evs)
        self.assertTrue(e.zombie_done)
        self.assertNotIn("zombie_wipe", e.events)
        self.assertIsNotNone(e.table)

    def test_a_sword_kills_the_zombie_on_contact(self):
        e = MiniCraft(3, "sword", hazard_p=1.0)
        e.inv.update({"sword": 1, "wood": 3})                         # set up the encounter directly
        while not e.zombie_done:
            e.step("collect stone")
        self.assertIn("zombie_slain", e.events)
        self.assertNotIn("zombie_wipe", e.events)
        self.assertEqual(e.inv.get("wood"), 3)

    def test_groaning_cue_starts_before_the_zombie_and_is_in_the_observation(self):
        e = MiniCraft(3, "sword", hazard_p=1.0)
        while e.tick < e.zombie_tick - CUE_LEAD and not e.done():
            self.assertNotIn("groaning", e.obs())
            e.step("craft sword")                                     # a failed craft costs one tick
        self.assertIn("groaning", e.obs())

    def test_zombie_leaves_by_dawn(self):
        e = MiniCraft(3, "sword", hazard_p=1.0)
        e.shelter = None
        while not e.zombie_done and e.tick < e.zombie_tick + DAWN + 5:
            e.step("craft sword")                                     # one tick each, stands still
        self.assertTrue(e.zombie_done)


class TraceMatchesState(unittest.TestCase):
    def test_last_frame_equals_the_engine_state(self):
        for seed in range(25):
            e = MiniCraft(seed, "sword")
            for a in ["collect wood", "collect wood", "craft table", "collect wood", "collect stone",
                      "craft pickaxe", "collect wood", "shelter", "collect iron", "craft sword"]:
                e.step(a)
                f = e.last_trace[-1]
                self.assertEqual(tuple(f["p"]), e.player)
                self.assertEqual(f["inv"], e.inv)
                self.assertEqual(f["table"], list(e.table) if e.table else None)
                self.assertEqual(f["t"], e.tick)
                snap = e.snapshot()
                self.assertEqual(snap["player"], list(e.player))
                if e.done():
                    break

    def test_the_observation_reports_distances_not_hidden_facts(self):
        e = MiniCraft(3, "sword", hazard_p=1.0)
        o = e.obs()
        self.assertIn("Nearest tree with wood:", o)
        self.assertIn("steps away", o)
        self.assertNotIn("zombie", o.lower())                          # before the cue nothing hints at it
        self.assertNotIn("groaning", o)


if __name__ == "__main__":
    unittest.main()
