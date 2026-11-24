"""Paid provider (Claude Haiku 5.5): adapter, allowlist and the rupee cap. No network, no key."""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gk.providers import (AnthropicProvider, QuotaExhausted, RateLimitError, SpendMeter,  # noqa: E402
                          SpendProvider, TransientError, allowlist_for, build_provider, call_cost_usd,
                          check_spend_cap, Completion, Provider)

MODEL = "claude-haiku-5-5"
CFG = {"provider": "anthropic", "model": MODEL, "max_spend_inr": 300, "usd_inr": 95}


class FakeResp(io.BytesIO):
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


class AdapterTests(unittest.TestCase):
    def test_request_shape_and_parsing(self):
        seen = {}

        def opener(req, timeout):
            seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
            return FakeResp(json.dumps({"id": "msg_1", "model": MODEL, "content": [{"type": "text", "text": "collect wood"}],
                                        "usage": {"input_tokens": 230, "output_tokens": 3}}).encode())
        c = AnthropicProvider(MODEL, api_key="TEST", opener=opener).complete("obs", system="rules", max_tokens=16)
        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/messages")
        h = {k.lower(): v for k, v in seen["headers"].items()}
        self.assertEqual((h["x-api-key"], h["anthropic-version"]), ("TEST", "2023-06-01"))
        self.assertEqual(seen["body"]["system"], "rules")
        self.assertEqual(seen["body"]["messages"], [{"role": "user", "content": "obs"}])
        self.assertEqual((seen["body"]["max_tokens"], seen["body"]["temperature"]), (16, 0))
        self.assertEqual((c.text, c.tokens_in, c.tokens_out, c.response_id), ("collect wood", 230, 3, "msg_1"))

    def test_errors_map_to_safe_stops(self):
        def err(code, body):
            def opener(req, timeout):
                raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body.encode()))
            return AnthropicProvider(MODEL, api_key="K", opener=opener)
        with self.assertRaises(RateLimitError):
            err(429, "rate limited").complete("x")
        with self.assertRaises(TransientError):
            err(529, "overloaded").complete("x")
        with self.assertRaises(QuotaExhausted):
            err(400, '{"error":{"message":"Your credit balance is too low"}}').complete("x")
        with self.assertRaises(RuntimeError):
            err(401, "invalid x-api-key").complete("x")


class MoneyTests(unittest.TestCase):
    def test_prices(self):
        self.assertAlmostEqual(call_cost_usd(MODEL, 100_000, 0), 0.01)        # $0.10 per million input
        self.assertAlmostEqual(call_cost_usd(MODEL, 0, 1_000_000), 0.50)      # $0.50 per million output
        self.assertAlmostEqual(call_cost_usd(MODEL, 200_000, 0), 0.10)       # >100K prompt: $0.50 per million

    def test_cap_must_be_explicit_and_at_most_300(self):
        check_spend_cap(CFG)
        for v in (0, None, 301, 1000, "300", True):
            with self.assertRaises(ValueError):
                check_spend_cap({**CFG, "max_spend_inr": v})
        with self.assertRaises(ValueError):          # Gemini stays at Rs 0
            check_spend_cap({"provider": "gemini", "max_spend_inr": 300})

    def test_only_haiku_is_approved(self):
        self.assertEqual(allowlist_for(CFG), (MODEL,))
        for m in ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2"):
            with self.assertRaises(ValueError):
                build_provider({**CFG, "model": m}, tempfile.mkdtemp())
        with self.assertRaises(ValueError):
            allowlist_for({**CFG, "allowed_models": ["claude-opus-5-5"]})

    def test_meter_refuses_before_crossing_the_cap(self):
        class Fake(Provider):
            name, model = "anthropic", MODEL
            calls = 0
            def complete(self, prompt, system="", max_tokens=256):
                Fake.calls += 1
                return Completion("collect wood", 300, 4)
        path = os.path.join(tempfile.mkdtemp(), "spend.json")
        cap = 0.10   # ten paise, so a few calls hit it
        p = SpendProvider(Fake(), SpendMeter(path, cap, 95))
        n = 0
        with self.assertRaises(QuotaExhausted):
            for _ in range(100000):
                p.complete("x" * 900, max_tokens=16)
                n += 1
        spent = json.load(open(path))["total_inr"]
        self.assertLessEqual(spent, cap)                 # never above the cap
        self.assertEqual(Fake.calls, n)                  # the refused call never reached the API
        self.assertGreater(n, 10)

    def test_stack_is_cached_limited_and_metered(self):
        d = tempfile.mkdtemp()
        p = build_provider(CFG, d)
        chain, x = [], p
        while x is not None:
            chain.append(type(x).__name__)
            x = getattr(x, "inner", None)
        self.assertEqual(chain, ["CachedProvider", "LimitedProvider", "SpendProvider", "AnthropicProvider"])

    def test_make_real_paid_stack(self):
        from gk.setup import make_real
        d = tempfile.mkdtemp()
        path = os.path.join(d, "config.json")
        json.dump(CFG, open(path, "w"))
        st = make_real(path, workdir=d)
        self.assertEqual((st.player_model, st.proposer_models), (MODEL, [MODEL]))
        self.assertIn("PAID", st.badge())
        self.assertEqual(st.quota()["spend"], {"spent_inr": 0.0, "cap_inr": 300.0})


if __name__ == "__main__":
    unittest.main()
