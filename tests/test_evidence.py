"""Task 8: per-call log, hash-chained experiments, verifier, acceptance built from verified records."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gk.acceptance import live_episode
from gk.calllog import CallLog, LoggedProvider, read_calls
from gk.chain import record_hash
from gk.harness import placebo
from gk.providers import CachedProvider, GeminiProvider, MockProvider
from gk.setup import make_mock
from gk.verify import main as verify_main, verified_rows, verify_files


def mock_run(tmp):
    calls = os.path.join(tmp, "calls.mock.jsonl")
    exp = os.path.join(tmp, "exp.jsonl")
    stack = make_mock(CallLog(calls, simulated=True))
    lab = stack.make_lab(log_path=exp)
    lab.run_round("honest")
    lab.submit("placebo", placebo(lab.current), "placebo")
    lab.run_side("adversarial")
    lab.audit()
    return exp, calls, lab


def rewrite(path, fn):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    fn(rows)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def rechain(path):
    """What a determined editor would do: recompute every hash so the chain looks intact."""
    def fix(rows):
        prev = "0" * 64
        for r in rows:
            r["prev_hash"] = prev
            r["hash"] = record_hash(r)
            prev = r["hash"]
    rewrite(path, fix)


class CallLogTests(unittest.TestCase):
    def test_every_call_is_logged_hit_or_miss_with_context(self):
        with tempfile.TemporaryDirectory() as d:
            log = CallLog(os.path.join(d, "calls.jsonl"))
            inner = MockProvider(lambda p: "collect wood", "m")
            p = LoggedProvider(CachedProvider(inner, os.path.join(d, "c.jsonl")), log, "player")
            log.set_context(world=7, suite="held", version="abc", episode="held:abc:7", step=1)
            a = p.complete("hello", "sys")
            b = p.complete("hello", "sys")
            rows = read_calls(log.path)
            self.assertEqual([r["cache"] for r in rows], ["miss", "hit"])
            self.assertEqual((a.call_id, b.call_id), (rows[0]["call_id"], rows[1]["call_id"]))
            for k in ("ts", "role", "model", "prompt_sha", "tokens_in", "tokens_out", "latency_ms", "world", "step"):
                self.assertIn(k, rows[0])
            self.assertNotIn("hello", json.dumps(rows))       # the prompt itself is not stored
            self.assertEqual(rows[0]["prompt_sha"], rows[1]["prompt_sha"])
            self.assertEqual(inner.calls, 1)

    def test_gemini_metadata_parsed_without_touching_request_or_cache_key(self):
        class Resp:
            status = 200
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, *a): return json.dumps({
                "candidates": [{"content": {"parts": [{"text": "shelter"}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1},
                "modelVersion": "gemini-3.5-flash-lite-001", "responseId": "resp-xyz"}).encode()
        seen = {}

        def opener(req, timeout):
            seen["body"] = json.loads(req.data)
            return Resp()
        g = GeminiProvider("gemini-3.5-flash-lite", api_key="K", opener=opener)
        c = g.complete("p", system="s", max_tokens=16)
        self.assertEqual((c.model_version, c.response_id, c.http_status), ("gemini-3.5-flash-lite-001", "resp-xyz", 200))
        self.assertEqual(set(seen["body"]), {"contents", "generationConfig", "systemInstruction"})
        self.assertEqual(seen["body"]["generationConfig"], {"maxOutputTokens": 16, "temperature": 0})
        with tempfile.TemporaryDirectory() as d:
            cp = CachedProvider(g, os.path.join(d, "c.jsonl"))
            k = cp._key("p", "s", 16)
            cp.complete("p", "s", 16)
            hit = CachedProvider(g, os.path.join(d, "c.jsonl")).complete("p", "s", 16)
            self.assertTrue(hit.cached)
            self.assertEqual(hit.response_id, "resp-xyz")     # meta survives in the cache
            self.assertEqual(list(cp.store)[0], k)             # key formula unchanged

    def test_live_episode_needs_every_step_a_miss(self):
        mk = lambda i, cache, ep="e1": {"call_id": f"c{i}", "role": "player", "episode": ep, "step": i,
                                        "cache": cache, "model": "m"}
        self.assertIsNone(live_episode([mk(1, "miss"), mk(2, "hit"), mk(3, "miss")]))
        self.assertEqual(live_episode([mk(1, "miss"), mk(2, "miss")])["steps"], 2)
        sim = [dict(mk(1, "miss"), simulated=True)]
        self.assertIsNone(live_episode(sim))                   # simulated calls never count


class EvidenceTests(unittest.TestCase):
    def test_mock_end_to_end_run_verifies_clean_and_cites_calls(self):
        with tempfile.TemporaryDirectory() as d:
            exp, calls, lab = mock_run(d)
            rows, results, ok = verify_files(exp, calls)
            self.assertGreaterEqual(len(rows), 4)
            bad = [r for r in results if not r[2]]
            self.assertEqual(bad, [])
            self.assertTrue(all(ok))
            self.assertEqual(verify_main([exp, calls]), 0)
            r = rows[0]
            self.assertTrue(r["call_ids"])
            self.assertEqual(r["prev_hash"], "0" * 64)
            self.assertEqual(rows[1]["prev_hash"], rows[0]["hash"])
            for k in ("exp_id", "prereg_sha", "code_sha", "suites"):
                self.assertIn(k, r)
            self.assertEqual(set(r["suites"]["held"]), {"parent", "child", "placebo"})

    def test_an_edited_line_breaks_the_chain(self):
        with tempfile.TemporaryDirectory() as d:
            exp, calls, _ = mock_run(d)
            rewrite(exp, lambda rows: rows[0].update(lesson="something else entirely"))
            rows, results, ok = verify_files(exp, calls)
            self.assertFalse(ok[0])
            self.assertFalse(all(ok[1:]))                     # later records fall with it
            self.assertTrue(any(c == "chain" and not good for _, c, good, _d in results))
            self.assertEqual(verify_main([exp, calls]), 1)

    def test_a_missing_call_id_fails(self):
        with tempfile.TemporaryDirectory() as d:
            exp, calls, _ = mock_run(d)
            lines = open(calls, encoding="utf-8").read().splitlines()
            first = json.loads(open(exp, encoding="utf-8").readline())
            victim = first["call_ids"][5]                  # a call that a record actually cites
            with open(calls, "w", encoding="utf-8") as f:
                f.write("\n".join(l for l in lines if victim not in l) + "\n")
            _, results, _ = verify_files(exp, calls)
            self.assertTrue(any(c == "calls" and not good for _, c, good, _d in results))
            self.assertEqual(verify_main([exp, calls]), 1)

    def test_a_doctored_score_is_caught_even_if_the_chain_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as d:
            exp, calls, _ = mock_run(d)
            def doctor(rows):
                r = next(x for x in rows if x["kind"] == "llm")
                r["suites"]["held"]["child"]["scores"] = [s + 0.2 for s in r["suites"]["held"]["child"]["scores"]]
            rewrite(exp, doctor)
            rechain(exp)
            _, results, ok = verify_files(exp, calls)
            self.assertTrue(any("recomputed" in c and not good for _, c, good, _d in results))
            self.assertFalse(all(ok))

    def test_changed_gate_code_or_prereg_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            exp, calls, _ = mock_run(d)
            rewrite(exp, lambda rows: rows[0].update(code_sha="0" * 64))
            rechain(exp)
            _, results, _ = verify_files(exp, calls)
            self.assertTrue(any(c == "code hash" and not good for _, c, good, _d in results))

    def test_acceptance_uses_only_verified_records(self):
        with tempfile.TemporaryDirectory() as d:
            exp, calls, _ = mock_run(d)
            good, every, _ = verified_rows(exp, calls)
            self.assertEqual(len(good), len(every))
            rewrite(exp, lambda rows: rows[-1].update(reason="edited"))
            good, every, _ = verified_rows(exp, calls)
            self.assertEqual(len(good), len(every) - 1)

    def test_resume_does_not_break_the_chain(self):
        with tempfile.TemporaryDirectory() as d:
            exp, calls, _ = mock_run(d)
            stack = make_mock(CallLog(calls, simulated=True))
            lab2 = stack.make_lab(log_path=exp)             # a resumed process
            lab2.run_round("honest")
            lab2.submit("placebo", placebo(lab2.current), "placebo")
            lab2.run_side("adversarial")
            lab2.audit()
            _, results, ok = verify_files(exp, calls)
            self.assertEqual([r for r in results if not r[2]], [])


if __name__ == "__main__":
    unittest.main()
