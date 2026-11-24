import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gk.agent import LLMAgent
from gk.env import MiniCraft
from gk.evaluator import Evaluator
from gk.gate import Gate
from gk.harness import Harness, add_lessons
from gk.loop import run_trial
from gk.providers import (CachedProvider, FallbackProvider, GeminiProvider, LimitedProvider,
                          MockProvider, QuotaExhausted, RateLimiter, RateLimitError,
                          TransientError, build_provider)
from gk.proposers import adversarial

H0 = Harness.initial()


def gate_for():
    ev = Evaluator()
    return ev, Gate(ev)


def decide(kind):
    ev, g = gate_for()
    c = adversarial(kind, H0)
    return g.check(H0, H0.with_patch(c.patch), list(c.patch))


class GateTests(unittest.TestCase):
    def test_tamper_blocked_by_allowlist(self):
        d = decide("tamper")
        self.assertFalse(d.accepted)
        self.assertIn("allowlist", d.reason)

    def test_cheat_rejected(self):
        self.assertFalse(decide("cheat").accepted)

    def test_overfit_rejected_on_unseen_seeds(self):
        d = decide("overfit")
        self.assertFalse(d.accepted)
        self.assertIn("held-out", d.reason)

    def test_phantom_guardrail_rejected(self):
        self.assertFalse(decide("phantom").accepted)

    def test_bloat_rejected_on_cost(self):
        d = decide("bloat")
        self.assertFalse(d.accepted)
        self.assertIn("cost", d.reason)

    def test_honest_lesson_promoted(self):
        ev, g = gate_for()
        p = add_lessons(H0, [{"tag": "dodge_zombie", "text": "shelter"}])
        self.assertTrue(g.check(H0, H0.with_patch(p), list(p)).accepted)

    def test_rush_passes_gate_but_regression_suite_catches_it(self):
        ev, g = gate_for()
        c = adversarial("rush", H0)
        child = H0.with_patch(c.patch)
        self.assertTrue(g.check(H0, child, list(c.patch)).accepted)
        self.assertGreater(g.regression_drop(H0, child), g.cfg.regression_tol)

    def test_placebo_check(self):
        from gk.harness import placebo
        ev, g = gate_for()
        plc = placebo(H0)
        self.assertLessEqual(len(plc.lessons()[-1]["text"]), 90)
        good = H0.with_patch(add_lessons(H0, [{"tag": "dodge_zombie", "text": "shelter"}]))
        self.assertTrue(g.check(H0, good, ["memory.json"], placebo=plc).accepted)
        # an edit whose effect is exactly what a placebo does must be rejected
        same_as_placebo = H0.with_patch(add_lessons(H0, [{"tag": "dodge_zombie", "text": "x"}]))
        d = g.check(H0, same_as_placebo, ["memory.json"], placebo=same_as_placebo)
        self.assertFalse(d.accepted)
        self.assertIn("placebo", d.reason)

    def test_full_loop_never_keeps_a_bad_edit(self):
        for t in range(4):
            self.assertEqual(run_trial("gated", t)["bad_surviving"], 0)

    def test_naive_loop_keeps_bad_edits(self):
        self.assertTrue(any(run_trial("naive", t)["bad_surviving"] > 0 for t in range(4)))


class EnvTests(unittest.TestCase):
    def test_ideal_pickaxe_plan_succeeds(self):
        e = MiniCraft(seed=1, task="pickaxe")
        e.hazard = False
        for a in ["collect wood"] * 3 + ["collect stone", "craft table", "craft pickaxe"]:
            e.step(a)
            if e.events and e.events[-1] == "slip":
                self.skipTest("slip in this seed")
        self.assertTrue(e.success())

    def test_slips_are_paired_across_different_play(self):
        """n-th attempt of an action has the same slip outcome whatever came before."""
        def stone_outcomes(prefix):
            e = MiniCraft(seed=7, task="pickaxe")
            e.hazard = False
            for a in prefix:
                e.step(a)
            out = []
            for _ in range(6):
                n = len(e.events)
                e.step("collect stone")
                out.append(len(e.events) > n)
            return out
        self.assertEqual(stone_outcomes([]),
                         stone_outcomes(["collect wood", "craft table", "collect wood"]))

    def test_verified_success_ignores_claims(self):
        e = MiniCraft(seed=1, task="sword")
        self.assertFalse(e.success())


class ProviderTests(unittest.TestCase):
    def test_cache_hit_costs_no_calls(self):
        with tempfile.TemporaryDirectory() as d:
            inner = MockProvider(lambda p: "collect wood")
            cp = CachedProvider(inner, os.path.join(d, "c.jsonl"))
            cp.complete("hi")
            r = cp.complete("hi")
            self.assertTrue(r.cached)
            self.assertEqual(inner.calls, 1)

    def test_daily_quota_enforced_and_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "q.json")
            lim = RateLimiter(rpm=100, rpd=3, state_path=path, clock=lambda: 1_700_000_000.0,
                              sleep=lambda s: None)
            for _ in range(3):
                lim.acquire()
            lim2 = RateLimiter(rpm=100, rpd=3, state_path=path, clock=lambda: 1_700_000_000.0,
                               sleep=lambda s: None)  # "restart"
            with self.assertRaises(QuotaExhausted):
                lim2.acquire()

    def test_rpm_limit_sleeps(self):
        with tempfile.TemporaryDirectory() as d:
            t = [1_700_000_000.0]
            slept = []
            lim = RateLimiter(rpm=2, rpd=100, state_path=os.path.join(d, "q.json"),
                              clock=lambda: t[0], sleep=lambda s: slept.append(s))
            lim.acquire(); lim.acquire(); lim.acquire()
            self.assertTrue(slept)

    def test_429_retries_then_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            calls = {"n": 0}

            class Flaky(MockProvider):
                def complete(self, prompt, system="", max_tokens=256):
                    calls["n"] += 1
                    if calls["n"] < 3:
                        raise RateLimitError("429")
                    return super().complete(prompt, system, max_tokens)

            lim = RateLimiter(100, 100, os.path.join(d, "q.json"), sleep=lambda s: None)
            lp = LimitedProvider(Flaky(lambda p: "ok"), lim, sleep=lambda s: None)
            self.assertEqual(lp.complete("x").text, "ok")
            self.assertEqual(lim.used_today(), 3)  # failed tries count against quota

    def test_gemini_adapter_with_fake_transport(self):
        class Resp:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, *a): return json.dumps({
                "candidates": [{"content": {"parts": [{"text": "collect wood"}]}}],
                "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 3}}).encode()
        seen = {}

        def opener(req, timeout):
            seen["url"], seen["key"] = req.full_url, req.get_header("X-goog-api-key")
            return Resp()
        g = GeminiProvider("gemini-3.5-flash-lite", api_key="TEST", opener=opener)
        c = g.complete("p", system="s")
        self.assertEqual((c.text, c.tokens_in, c.tokens_out), ("collect wood", 11, 3))
        self.assertIn("gemini-3.5-flash-lite:generateContent", seen["url"])
        self.assertEqual(seen["key"], "TEST")

    def test_provider_is_swappable_by_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = build_provider({"provider": "mock", "model": "m1"}, d)
            self.assertEqual(p.complete("x").text, "collect wood")
            with self.assertRaises(ValueError):
                build_provider({"provider": "nope", "model": "m"}, d)

    def test_http_503_becomes_transient_error_and_is_retried(self):
        import io
        import urllib.error

        def opener503(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO())
        g = GeminiProvider("m", api_key="K", opener=opener503)
        with self.assertRaises(TransientError):
            g.complete("x")
        with tempfile.TemporaryDirectory() as d:
            lim = RateLimiter(100, 100, os.path.join(d, "q.json"), sleep=lambda s: None)
            lp = LimitedProvider(g, lim, retries=2, sleep=lambda s: None)
            with self.assertRaises(TransientError):
                lp.complete("x")
            self.assertEqual(lim.used_today(), 3)  # 1 try + 2 retries all counted

    def test_network_error_is_transient(self):
        import urllib.error

        def bad(req, timeout):
            raise urllib.error.URLError("boom")
        with self.assertRaises(TransientError):
            GeminiProvider("m", api_key="K", opener=bad).complete("x")

    def test_fallback_moves_on_when_first_model_fails(self):
        class Down(MockProvider):
            def complete(self, *a, **k):
                raise TransientError("503")

        class OutOfQuota(MockProvider):
            def complete(self, *a, **k):
                raise QuotaExhausted("used up")
        ok = MockProvider(lambda p: "from-third")
        fb = FallbackProvider([Down(lambda p: ""), OutOfQuota(lambda p: ""), ok])
        self.assertEqual(fb.complete("x").text, "from-third")
        with self.assertRaises(QuotaExhausted):
            FallbackProvider([OutOfQuota(lambda p: "")]).complete("x")

    def test_llm_agent_parses_actions(self):
        env = MiniCraft(1, "pickaxe")
        agent = LLMAgent(H0, MockProvider(lambda p: "I will craft table now."))
        self.assertEqual(agent.act(env), "craft table")
        agent = LLMAgent(H0, MockProvider(lambda p: "dance"))
        self.assertEqual(agent.act(env), "invalid")


if __name__ == "__main__":
    unittest.main()
