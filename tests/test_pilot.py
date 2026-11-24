"""Pilot script, spending cap and allowlist. No network: Gemini is replaced by the mock player."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_pilot  # noqa: E402
from gk.harness import Harness  # noqa: E402
from gk.mock import mock_player  # noqa: E402
from gk.providers import (CachedProvider, Completion, FallbackProvider, Provider,  # noqa: E402
                          QuotaExhausted, build_provider, check_spend_cap, free_tier_allowlist)
from gk.real import REAL_SUITES, RealEvaluator  # noqa: E402

MODEL = "gemini-3.5-flash-lite"


class FakeGemini(Provider):
    """Counts every request that would have reached Google; can run out of quota after `limit`."""
    name = "gemini"

    def __init__(self, limit=None):
        self.model, self.calls, self.limit = MODEL, 0, limit

    def complete(self, prompt, system="", max_tokens=256):
        if self.limit is not None and self.calls >= self.limit:
            raise QuotaExhausted("fake daily quota used")
        self.calls += 1
        return Completion(mock_player(prompt), 10, 2)


class InTmp(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        with open("config.json", "w") as f:
            json.dump({"provider": "gemini", "model": MODEL, "rpm": 15, "rpd": 500}, f)

    def tearDown(self):
        os.chdir(self.old)

    def play(self, inner, suites):
        ev = RealEvaluator(CachedProvider(inner, f"cache_gemini_{MODEL}.jsonl"), REAL_SUITES)
        h = Harness.initial()
        return {s: ev.run(h, s) for s in suites}


class DryRunTests(InTmp):
    def test_counts_saved_and_remaining_without_calling(self):
        eps = self.play(FakeGemini(), ["report"])["report"]
        steps = sum(e.steps for e in eps)
        d = run_pilot.dry_run({"model": MODEL})
        rows = {r["suite"]: r for r in d["suites"]}
        self.assertEqual(rows["report"]["saved_steps"], steps)
        self.assertEqual(rows["report"]["remaining_requests_max"], 0)
        self.assertEqual(rows["report"]["episodes_fully_saved"], 10)
        # identical game states in different worlds share one cached answer, so this is an upper bound
        self.assertLessEqual(rows["held"]["remaining_requests_max"], 16 * 14)
        self.assertGreater(rows["held"]["remaining_requests_max"], 0)
        self.assertLessEqual(rows["regression"]["remaining_requests_max"], 6 * 9)
        self.assertFalse(os.path.exists(f"quota_gemini_{MODEL}.json"))   # dry run writes nothing

    def test_dry_run_cli_sends_nothing(self):
        buf = io.StringIO()
        sys.argv = ["run_pilot.py", "--dry-run"]
        with redirect_stdout(buf):
            run_pilot.main()
        self.assertIn("Nothing was sent", buf.getvalue())


class ResumeTests(InTmp):
    def test_stop_and_resume_never_repeats_a_call(self):
        first = FakeGemini(limit=20)
        with self.assertRaises(QuotaExhausted):
            self.play(first, run_pilot.ORDER)
        self.assertEqual(first.calls, 20)
        second = FakeGemini()
        self.play(second, run_pilot.ORDER)
        keys = [json.loads(l)["k"] for l in open(f"cache_gemini_{MODEL}.jsonl", encoding="utf-8")]
        self.assertEqual(len(keys), len(set(keys)))                    # no duplicate record
        self.assertEqual(first.calls + second.calls, len(keys))        # every request saved once
        third = FakeGemini()
        self.play(third, run_pilot.ORDER)
        self.assertEqual(third.calls, 0)                               # a finished pilot is free to rerun


class SpendTests(unittest.TestCase):
    def test_spend_cap_is_zero(self):
        check_spend_cap({})
        check_spend_cap({"max_spend_inr": 0})
        for v in (300, 1, "300"):
            with self.assertRaises(ValueError):
                check_spend_cap({"max_spend_inr": v})

    def test_config_can_only_narrow_the_allowlist(self):
        self.assertEqual(free_tier_allowlist([MODEL]), (MODEL,))
        with self.assertRaises(ValueError):
            free_tier_allowlist([MODEL, "gemini-3-pro"])
        with self.assertRaises(ValueError):
            build_provider({"provider": "gemini", "model": "gemini-3-pro",
                            "allowed_models": ["gemini-3-pro"]}, tempfile.mkdtemp())
        with self.assertRaises(ValueError):
            build_provider({"provider": "gemini", "model": MODEL, "max_spend_inr": 300}, tempfile.mkdtemp())

    def test_player_has_no_fallback_and_proposers_are_free_tier(self):
        from gk.setup import make_real
        d = tempfile.mkdtemp()
        p = os.path.join(d, "config.json")
        with open(p, "w") as f:
            json.dump({"provider": "gemini", "model": MODEL, "rpm": 15, "rpd": 500}, f)
        st = make_real(p, workdir=d)
        self.assertNotIsInstance(st.player, FallbackProvider)
        self.assertEqual(st.player.model, MODEL)
        self.assertIsInstance(st.proposer, FallbackProvider)
        self.assertTrue(all(m in free_tier_allowlist() for m in st.proposer_models))


if __name__ == "__main__":
    unittest.main()
