"""Per-minute limits shared across processes, 429s read correctly, and one real run at a time."""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from multiprocessing import Process

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gk.providers import (GeminiProvider, LimitedProvider, MockProvider, QuotaExhausted,  # noqa: E402
                          RateLimiter, RateLimitError)
from gk.runlock import RunLock, RunLocked  # noqa: E402

PER_MINUTE = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "quotaValue": "15"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "41s"}]}})
PER_DAY = PER_MINUTE.replace("PerMinute", "PerDay")


def _gemini(body):
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(body.encode()))
    return GeminiProvider("gemini-3.5-flash-lite", api_key="K", opener=opener)


def _burst(path, n):
    lim = RateLimiter(rpm=10, rpd=1000, state_path=path, sleep=lambda s: None)
    for _ in range(n):
        lim.acquire()


class GeminiRateTests(unittest.TestCase):
    def test_per_minute_429_is_waited_out_per_day_429_stops(self):
        with self.assertRaises(RateLimitError) as c:
            _gemini(PER_MINUTE).complete("x")
        self.assertTrue(c.exception.per_minute)
        self.assertEqual(c.exception.retry_after, 41.0)
        with self.assertRaises(QuotaExhausted):
            _gemini(PER_DAY).complete("x")
        with self.assertRaises(RateLimitError) as c:          # unknown 429: not marked as waitable
            _gemini("").complete("x")
        self.assertFalse(c.exception.per_minute)

    def test_limited_provider_waits_twice_then_stops(self):
        with tempfile.TemporaryDirectory() as d:
            slept, calls = [], {"n": 0}

            class Throttled(MockProvider):
                def complete(self, prompt, system="", max_tokens=256):
                    calls["n"] += 1
                    if calls["n"] <= 2:
                        raise RateLimitError("per-minute", retry_after=41, per_minute=True)
                    return super().complete(prompt, system, max_tokens)
            lim = RateLimiter(100, 100, os.path.join(d, "q.json"), sleep=lambda s: None)
            lp = LimitedProvider(Throttled(lambda p: "ok"), lim, sleep=slept.append)
            self.assertEqual(lp.complete("x").text, "ok")
            self.assertEqual(slept, [42.0, 42.0])
            calls["n"] = -10                                   # now it never recovers
            with self.assertRaises(RateLimitError):
                lp.complete("y")

    def test_minute_window_is_shared_between_processes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "q.json")
            ps = [Process(target=_burst, args=(path, 8)) for _ in range(2)]
            for p in ps:
                p.start()
            for p in ps:
                p.join(60)
            st = json.load(open(path))
            day = [k for k in st if not k.startswith("_")][0]
            self.assertEqual(st[day], 16)                      # every request counted once
            # one shared window: 16 requests at rpm=10 means some waited (sleep is a no-op here, so
            # the window simply records them all); what matters is a single file holds both processes
            self.assertEqual(len(st["_minute"]), 16)


class SharedWindowTests(unittest.TestCase):
    def test_other_process_must_wait_when_the_minute_is_used(self):
        with tempfile.TemporaryDirectory() as d:
            path, t = os.path.join(d, "q.json"), [1_800_000_000.0]
            first = RateLimiter(2, 100, path, clock=lambda: t[0], sleep=lambda s: None)
            first.acquire(); first.acquire()
            slept = []
            second = RateLimiter(2, 100, path, clock=lambda: t[0], sleep=slept.append)   # "another process"
            second.acquire()
            self.assertTrue(slept and slept[0] > 55)    # waited for the shared minute to free up


class RunLockTests(unittest.TestCase):
    def test_second_real_run_is_refused_then_allowed_after_release(self):
        with tempfile.TemporaryDirectory() as d:
            a = RunLock(d, "first").acquire()
            with self.assertRaises(RunLocked):
                RunLock(d, "second").acquire()
            a.release()
            RunLock(d, "third").acquire().release()

    def test_stale_lock_is_taken_over(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "real_run.lock")
            json.dump({"token": "dead", "what": "crashed"}, open(p, "w"))
            old = time.time() - 600
            os.utime(p, (old, old))
            RunLock(d, "new").acquire().release()


if __name__ == "__main__":
    unittest.main()
