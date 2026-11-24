"""End-to-end plumbing test of the real-model path using fake models (no quota used)."""
import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gk.gate import Gate
from gk.harness import Harness, add_lessons
from gk.providers import MockProvider
from gk.real import LLMProposer, RealEvaluator


def fake_player(prompt: str) -> str:
    """A stand-in 'LLM': makes one avoidable mistake unless its lessons say to count materials."""
    lessons = prompt.split("Observation:")[0].lower()
    obs = prompt.split("Observation:")[1]
    inv_txt = re.search(r"Inventory: (\{.*?\}|empty)", obs).group(1)
    inv = {} if inv_txt == "empty" else ast.literal_eval(inv_txt)
    task = re.search(r"craft a (\w+)", obs).group(1)
    careful = "count" in lessons
    failed = "failed" in obs
    wood = inv.get("wood", 0)
    if not inv.get("table"):
        if wood == 1 and not careful and not failed:
            return "craft table"          # the avoidable mistake
        if wood < 2:
            return "collect wood"
        return "craft table"
    if not inv.get("pickaxe"):
        if wood < 1 + (1 if task == "sword" else 0):
            return "collect wood"
        return "collect stone" if inv.get("stone", 0) < 1 else "craft pickaxe"
    if task == "pickaxe":
        return "craft pickaxe"
    if wood < 1:
        return "collect wood"
    return "collect iron" if inv.get("iron", 0) < 1 else "craft sword"


class RealLoopPlumbing(unittest.TestCase):
    def test_llm_lesson_flows_through_gate(self):
        player = MockProvider(fake_player)
        proposer = MockProvider(lambda p: 'Sure! {"lesson": "Count your materials before crafting."}')
        ev = RealEvaluator(player)
        base = Harness.initial()
        train = ev.run(base, "train")
        cands = LLMProposer(proposer).propose(base, train)
        self.assertEqual(len(cands), 1)
        self.assertIn("Count", cands[0].note)
        child = base.with_patch(cands[0].patch)
        d = Gate(ev).check(base, child, list(cands[0].patch))
        self.assertTrue(d.accepted, d.reason)
        self.assertGreater(ev.mean_true(child, "report"), ev.mean_true(base, "report"))

    def test_placebo_catches_any_text_helps_effect(self):
        """If ANY lesson text changes behaviour (what run 2 found), the lesson must not be credited."""
        from gk.harness import placebo
        def any_text_player(prompt):
            careful = "(none yet)" not in prompt.split("Observation:")[0]
            return fake_player(prompt.replace("Lessons learned so far:", "count" if careful else ""))
        ev = RealEvaluator(MockProvider(any_text_player))
        base = Harness.initial()
        child = base.with_patch(add_lessons(base, [{"tag": "llm", "text": "Count materials."}]))
        g = Gate(ev)
        self.assertTrue(g.check(base, child, ["memory.json"]).accepted)          # fooled without placebo
        d = g.check(base, child, ["memory.json"], placebo=placebo(base))
        self.assertFalse(d.accepted)
        self.assertIn("placebo", d.reason)

    def test_garbage_proposer_output_is_ignored(self):
        ev = RealEvaluator(MockProvider(fake_player))
        base = Harness.initial()
        train = ev.run(base, "train")
        self.assertEqual(LLMProposer(MockProvider(lambda p: "no json here")).propose(base, train), [])

    def test_rejected_lesson_is_not_resubmitted_and_reason_is_explicit(self):
        ev = RealEvaluator(MockProvider(fake_player))
        base = Harness.initial()
        train = ev.run(base, "train")
        same = MockProvider(lambda p: '{"lesson": "Count your materials before crafting."}')
        prop = LLMProposer(same)
        self.assertEqual(prop.propose(base, train, ["count your materials before crafting."]), [])
        self.assertIn("already-rejected", prop.last_status)
        bad = LLMProposer(MockProvider(lambda p: ""))
        self.assertEqual(bad.propose(base, train), [])
        self.assertIn("could not be parsed", bad.last_status)

    def test_rejected_lessons_are_shown_to_the_proposer(self):
        seen = []
        ev = RealEvaluator(MockProvider(fake_player))
        base = Harness.initial()
        LLMProposer(MockProvider(lambda p: seen.append(p) or "{}")).propose(
            base, ev.run(base, "train"), ["Old idea that failed."])
        self.assertIn("Old idea that failed.", seen[0])

    def test_prompt_contains_failures_not_seeds(self):
        seen = []
        ev = RealEvaluator(MockProvider(fake_player))
        base = Harness.initial()
        train = ev.run(base, "train")
        LLMProposer(MockProvider(lambda p: seen.append(p) or "{}")).propose(base, train)
        self.assertIn("no_materials", seen[0])
        self.assertNotIn("seed", seen[0].split("Example failed steps")[0].lower().replace("seeds", ""))


if __name__ == "__main__":
    unittest.main()
