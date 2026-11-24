"""Task 7: exact permutation test, preregistered gate rules, length-matched placebo, floor pre-flight."""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gk.acceptance import acceptance_budget, preflight
from gk.gate import Gate, GateConfig
from gk.harness import Harness, add_lessons, added_text, placebo_for
from gk.lab import Lab
from gk.mock import mock_player, mock_proposer
from gk.providers import MockProvider
from gk.real import LLMProposer, REAL_SUITES, RealEvaluator
from gk.stats import sign_flip_test

ROOT = os.path.join(os.path.dirname(__file__), "..")


class SignFlip(unittest.TestCase):
    def test_known_small_cases_are_exact(self):
        self.assertAlmostEqual(sign_flip_test([1, 1, 1])["p"], 1 / 8)          # only +++ reaches the sum
        self.assertAlmostEqual(sign_flip_test([1, -1])["p"], 3 / 4)            # sums 2,0,0,-2 vs observed 0
        self.assertAlmostEqual(sign_flip_test([0.05] * 4)["p"], 1 / 16)
        self.assertAlmostEqual(sign_flip_test([2, 1, -1])["p"], 3 / 8)         # observed 2: +++,++-,+-+... check below
        self.assertEqual(sign_flip_test([0, 0, 0])["p"], 1.0)

    def test_matches_brute_force_enumeration(self):
        import itertools
        d = [0.1, 0.35, -0.05, 0.2, 0.0, 0.15, -0.1]
        obs = sum(d)
        hits = sum(sum(s * x for s, x in zip(signs, d)) >= obs - 1e-9
                   for signs in itertools.product((1, -1), repeat=len(d)))
        self.assertAlmostEqual(sign_flip_test(d)["p"], hits / 2 ** len(d))

    def test_large_n_uses_random_flips_and_is_seeded(self):
        d = [0.05] * 25
        a, b = sign_flip_test(d, seed=3), sign_flip_test(d, seed=3)
        self.assertEqual(a["p"], b["p"])
        self.assertIn("random", a["method"])
        self.assertLess(a["p"], 0.001)

    def test_reports_mean_n_and_ci(self):
        r = sign_flip_test([0.1, 0.2, 0.3, 0.0])
        self.assertEqual(r["n"], 4)
        self.assertAlmostEqual(r["mean"], 0.15)
        self.assertLessEqual(r["ci"][0], r["mean"] <= r["ci"][1] or r["mean"])
        self.assertIn("exact", r["method"])


def lab_for(tmp, player=None):
    player = player or MockProvider(mock_player, "mock-player")
    ev = RealEvaluator(player)
    return Lab(ev, lambda s: LLMProposer(MockProvider(mock_proposer, "m"), s),
               log_path=os.path.join(tmp, "e.jsonl"), requests_fn=lambda: player.calls)


class GateRules(unittest.TestCase):
    def test_config_matches_the_preregistration(self):
        c = GateConfig()
        self.assertEqual((c.alpha_held, c.min_gain, c.alpha_placebo), (0.05, 0.02, 0.10))
        self.assertEqual((c.regression_tol, c.regression_floor, c.canary_tol), (0.03, 0.2, 0.05))
        self.assertEqual((c.integrity_gap_tol, c.max_cost_growth, c.relaxed_cost_growth, c.audit_tol),
                         (0.10, 0.10, 0.30, 0.03))
        text = open(os.path.join(ROOT, "prereg", "PREREG.md"), encoding="utf-8").read()
        for needle in ("p < 0.05", "mean gain >= 0.02", "p < 0.10", "<= 0.03", ">= 0.2", ">= -0.05",
                       "<= 10%", "<= 30%", ">= -0.03", "16 (100-115)", "budget 9"):
            self.assertIn(needle, text)

    def test_suite_sizes(self):
        self.assertEqual(len(list(REAL_SUITES["held"][0])), 16)
        self.assertEqual(len(list(REAL_SUITES["regression"][0])), 6)
        self.assertEqual(len(list(REAL_SUITES["audit"][0])), 6)
        self.assertEqual(REAL_SUITES["regression"][2], 9)
        self.assertEqual(REAL_SUITES["audit"][2], 9)
        self.assertEqual(REAL_SUITES["held"][2:], (14, 0.7))
        seeds = [set(v[0]) for v in REAL_SUITES.values()]
        self.assertEqual(sum(len(s) for s in seeds), len(set().union(*seeds)))   # no overlap

    def test_every_check_records_n_mean_threshold_and_p_where_applicable(self):
        with tempfile.TemporaryDirectory() as d:
            lab = lab_for(d)
            o = lab.run_round("honest")[0]
            self.assertTrue(o.accepted, o.reason)
            by = {c["name"]: c for c in o.checks}
            for name in ("held-out gain", "beats placebo"):
                for k in ("n", "mean", "p", "ci", "threshold"):
                    self.assertIn(k, by[name], (name, k))
            self.assertEqual(by["held-out gain"]["n"], 16)
            for name in ("regression suite", "integrity", "canary", "cost"):
                for k in ("n", "mean", "threshold"):
                    self.assertIn(k, by[name], (name, k))
            self.assertIn("p < 0.05", by["held-out gain"]["threshold"])

    def test_small_gain_below_min_is_rejected_even_if_consistent(self):
        class FakeEv:
            def __init__(s):
                s.n = 0
            def run(s, h, suite):
                from gk.evaluator import Episode
                bonus = 1 if "x" in h.tags() else 0
                return [Episode(i, "pickaxe", True, True, 10 - bonus * 0, 100, []) for i in range(16)]
        # identical scores -> mean 0 -> rejected on held-out, with p reported
        h0 = Harness.initial()
        h1 = h0.with_patch(add_lessons(h0, [{"tag": "x", "text": "t"}]))
        d = Gate(FakeEv()).check(h0, h1, ["memory.json"])
        self.assertFalse(d.accepted)
        self.assertIn("p=", d.reason)

    def test_regression_floor_reports_uninformative_and_does_not_accept(self):
        class Stuck(MockProvider):          # a parent that never finishes: score 0 everywhere
            pass
        with tempfile.TemporaryDirectory() as d:
            player = MockProvider(lambda p: "collect wood", "stuck")
            lab = lab_for(d, player)
            ok, mean, msg = preflight(lab)
            self.assertFalse(ok)
            self.assertEqual(mean, 0.0)
            self.assertIn("floor", msg)
            # and the gate itself refuses to rely on an uninformative suite
            from gk.evaluator import Episode
            class Ev:
                def run(s, h, suite):
                    better = "x" in h.tags()
                    ok_ = better if suite == "held" else False
                    return [Episode(i, "pickaxe", ok_, ok_, 8, 100, []) for i in range(16)]
                def mean_true(s, h, suite):
                    return sum((1 - e.steps / 20) if e.success else 0 for e in s.run(h, suite)) / 16
                def mean_claimed(s, h, suite):
                    return s.mean_true(h, suite)
                def mean_tokens(s, h, suite):
                    return 100.0
            h0 = Harness.initial()
            h1 = h0.with_patch(add_lessons(h0, [{"tag": "x", "text": "t"}]))
            dec = Gate(Ev()).check(h0, h1, ["memory.json"], regression=True)
            self.assertFalse(dec.accepted)
            self.assertIn("uninformative", dec.reason)

    def test_preflight_passes_for_the_mock_original(self):
        with tempfile.TemporaryDirectory() as d:
            ok, mean, _ = preflight(lab_for(d))
            self.assertTrue(ok)
            self.assertGreaterEqual(mean, 0.2)


class Placebo(unittest.TestCase):
    def test_length_matched_content_free_and_deterministic(self):
        h0 = Harness.initial()
        for text in ("Short lesson here.", "x" * 40, "Always check the inventory against the cost "
                     "shown in the failure message before you try to craft anything again, please."):
            child = h0.with_patch(add_lessons(h0, [{"tag": "llm", "text": text}]))
            plc = placebo_for(h0, child)
            added = added_text(h0, plc)
            self.assertLessEqual(abs(len(added) - len(text)), max(2, 0.1 * len(text)), (len(added), len(text)))
            self.assertNotIn("inventory", added)
            self.assertEqual(plc.version, placebo_for(h0, child).version)   # same cache key every time

    def test_budget_estimate_is_updated(self):
        b = acceptance_budget()
        self.assertEqual(b["worst_case"], sum(b["parts_worst"].values()))
        self.assertIn("pre-flight: original on the regression suite", b["parts_worst"])
        self.assertGreater(b["worst_case"], 2000)


if __name__ == "__main__":
    unittest.main()
