"""Paid provider (GPT-6 Luna): adapter shape, reasoning off, billing errors, allowlist, cap. No network."""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gk.providers import (OpenAIProvider, QuotaExhausted, RateLimitError, TransientError,  # noqa: E402
                          allowlist_for, build_provider, call_cost_usd, check_spend_cap)

MODEL = "gpt-6-luna"
CFG = {"provider": "openai", "model": MODEL, "max_spend_inr": 300, "usd_inr": 95}


class FakeResp(io.BytesIO):
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


class OpenAITests(unittest.TestCase):
    def test_request_shape_reasoning_off_and_parsing(self):
        seen = {}

        def opener(req, timeout):
            seen["url"], seen["auth"], seen["body"] = req.full_url, req.get_header("Authorization"), json.loads(req.data)
            return FakeResp(json.dumps({"id": "resp_1", "model": MODEL, "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": "collect stone"}]}],
                "usage": {"input_tokens": 240, "output_tokens": 3}}).encode())
        c = OpenAIProvider(MODEL, api_key="TEST", opener=opener).complete("obs", system="rules", max_tokens=16)
        self.assertEqual(seen["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(seen["auth"], "Bearer TEST")
        b = seen["body"]
        self.assertEqual((b["model"], b["input"], b["instructions"]), (MODEL, "obs", "rules"))
        self.assertEqual(b["reasoning"], {"effort": "none"})       # no billed thinking
        self.assertEqual((c.text, c.tokens_in, c.tokens_out, c.response_id), ("collect stone", 240, 3, "resp_1"))

    def test_billing_and_rate_errors(self):
        def err(code, body):
            def opener(req, timeout):
                raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body.encode()))
            return OpenAIProvider(MODEL, api_key="K", opener=opener)
        with self.assertRaises(QuotaExhausted):      # OpenAI reports an empty balance as a 429
            err(429, '{"error":{"code":"insufficient_quota"}}').complete("x")
        with self.assertRaises(RateLimitError):
            err(429, '{"error":{"code":"rate_limit_exceeded"}}').complete("x")
        with self.assertRaises(TransientError):
            err(503, "unavailable").complete("x")

    def test_price_cap_and_allowlist(self):
        self.assertAlmostEqual(call_cost_usd(MODEL, 1_000_000, 1_000_000), 0.60)   # standard, not batch
        check_spend_cap(CFG)
        with self.assertRaises(ValueError):
            check_spend_cap({**CFG, "max_spend_inr": 500})
        self.assertEqual(allowlist_for(CFG), (MODEL,))
        for m in ("gpt-6.2-sol", "gpt-6.2-astra", "gpt-6-luna-pro"):
            with self.assertRaises(ValueError):
                build_provider({**CFG, "model": m}, tempfile.mkdtemp())

    def test_stack_has_spend_guard(self):
        p, chain = build_provider(CFG, tempfile.mkdtemp()), []
        while p is not None:
            chain.append(type(p).__name__)
            p = getattr(p, "inner", None)
        self.assertEqual(chain, ["CachedProvider", "LimitedProvider", "SpendProvider", "OpenAIProvider"])


if __name__ == "__main__":
    unittest.main()
