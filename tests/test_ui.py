"""Task 9: the live UI backend over real HTTP (mock mode; real mode only for REFUSAL paths, no calls)."""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as appmod
from gk.calllog import LoggedProvider
from gk.export_replay import export, main as export_main
from gk.providers import CachedProvider

ROOT = os.path.join(os.path.dirname(__file__), "..")


def request(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    h = dict(headers or {})
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, data


def wait_idle(runner, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if runner.status not in ("running", "paused") and not runner.playing:
            return
        time.sleep(0.05)
    raise AssertionError("run did not finish")


class UiBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.runner = appmod.Runner(workdir=cls.tmp, delay=0)
        cls.srv = appmod.serve(cls.runner, port=0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        code, _ = request(cls.port, "POST", "/control", {"action": "start", "mode": "mock", "rounds": 2})
        assert code == 200
        cls.runner.thread.join(120)
        wait_idle(cls.runner)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    # ---- live / cached / simulated + call ids ----------------------------------------
    def test_steps_carry_call_id_latency_and_cache_flag_and_mode(self):
        steps = [e for e in self.runner.events if e["type"] == "step"]
        self.assertTrue(steps)
        for e in steps[:50]:
            for k in ("call_id", "latency_ms", "cached", "mode"):
                self.assertIn(k, e)
            self.assertEqual(e["mode"], "mock")          # the page will label these SIMULATED, never LIVE
        html = request(self.port, "GET", "/")[1].decode()
        for needle in ("LIVE · ", "CACHED", "SIMULATED", "Start a new game", "REPLAY", "?replay", "verdict",
                       "ROLLED BACK", "placebo", "threshold"):
            self.assertIn(needle, html)

    # ---- /play -----------------------------------------------------------------------
    def test_play_one_fresh_demo_game_mock(self):
        code, j = request(self.port, "POST", "/play", {"mode": "mock"})
        self.assertEqual(code, 200)
        self.assertGreaterEqual(j["seed"], appmod.DEMO_BASE)
        self.assertFalse(j["live_calls"])                 # mock: simulated, not live
        wait_idle(self.runner)
        types = [e["type"] for e in self.runner.events if e.get("seed") == j["seed"] or e["type"].startswith("demo")]
        self.assertIn("demo_start", types)
        self.assertIn("demo_end", types)
        demo_steps = [e for e in self.runner.events if e["type"] == "step" and e.get("seed") == j["seed"]]
        self.assertTrue(demo_steps)
        self.assertTrue(all(e["suite"] == "demo" and e["mode"] == "mock" for e in demo_steps))
        code2, j2 = request(self.port, "POST", "/play", {"mode": "mock"})
        wait_idle(self.runner)
        self.assertEqual(j2["seed"], j["seed"] + 1)       # the counter moves on: never the same world twice
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "demo_counter.json")))

    def test_play_refused_while_the_loop_is_running(self):
        self.runner.status = "running"
        try:
            self.assertEqual(request(self.port, "POST", "/play", {"mode": "mock"})[0], 409)
        finally:
            self.runner.status = "finished"

    def test_play_real_mode_refusals_make_no_call(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "config.json"), "w") as f:
            json.dump({"provider": "gemini", "model": "gemini-3.5-flash-lite", "rpm": 15, "rpd": 3}, f)
        day = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
        with open(os.path.join(tmp, "quota_gemini_gemini-3.5-flash-lite.json"), "w") as f:
            json.dump({day: 3}, f)                         # the daily quota is used up
        runner = appmod.Runner(workdir=tmp, delay=0)
        srv = appmod.serve(runner, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        old = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "TEST-NOT-A-KEY"
        try:
            self.assertEqual(request(port, "POST", "/play", {"mode": "real"})[0], 400)          # no confirmation
            code, j = request(port, "POST", "/play", {"mode": "real", "confirm_no_billing": True})
            self.assertEqual(code, 429)
            self.assertIn("STOP", j["error"])
            self.assertIn("IST", j["error"])
            code, j = request(port, "POST", "/control", {"action": "start", "mode": "real", "confirm_no_billing": True})
            self.assertEqual(code, 429)
            self.assertFalse(runner.playing)
            self.assertFalse(os.path.exists(os.path.join(tmp, "calls.jsonl")) and
                             os.path.getsize(os.path.join(tmp, "calls.jsonl")) > 0)       # nothing was logged = nothing called
        finally:
            srv.shutdown()
            if old is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = old

    def test_demo_player_bypasses_the_cache(self):
        from gk.setup import make_real
        from gk.calllog import CallLog
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "config.json"), "w") as f:
            json.dump({"provider": "gemini", "model": "gemini-3.5-flash-lite", "rpm": 15, "rpd": 500}, f)
        stack = make_real(os.path.join(tmp, "config.json"), tmp, CallLog(os.path.join(tmp, "c.jsonl")))
        self.assertIsInstance(stack.player.inner, CachedProvider)             # the loop uses the cache
        live = appmod._live_provider(stack)
        self.assertIsInstance(live, LoggedProvider)
        self.assertNotIsInstance(live.inner, CachedProvider)                  # the demo game does not
        self.assertEqual(type(live.inner).__name__, "LimitedProvider")        # but is rate-limited and metered

    # ---- local only ------------------------------------------------------------------
    def test_non_local_host_or_origin_is_refused(self):
        self.assertEqual(request(self.port, "GET", "/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(request(self.port, "GET", "/state", headers={"Host": f"localhost:{self.port}"})[0], 200)
        bad = request(self.port, "POST", "/control", {"action": "pause"}, {"Origin": "http://evil.example"})
        self.assertEqual(bad[0], 403)
        bad = request(self.port, "POST", "/play", {"mode": "mock"}, {"Origin": "http://evil.example"})
        self.assertEqual(bad[0], 403)
        good = request(self.port, "POST", "/control", {"action": "pause"}, {"Origin": f"http://localhost:{self.port}"})
        self.assertEqual(good[0], 409)                     # accepted as local; refused only because nothing runs
        with self.assertRaises(ValueError):
            appmod.serve(self.runner, host="0.0.0.0", port=0)
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    # ---- events file, replay, export, version tree ---------------------------------------
    def test_events_are_recorded_and_replay_endpoint_serves_the_recorded_file(self):
        path = os.path.join(self.tmp, "events.mock.jsonl")
        self.assertTrue(os.path.exists(path))
        recorded = [json.loads(l) for l in open(path, encoding="utf-8")]
        self.assertEqual({e["type"] for e in recorded} >= {"run_start", "step", "gate_check", "decision"}, True)
        self.assertEqual(request(self.port, "GET", "/replays")[1]["files"], ["events.mock.jsonl"])
        code, raw = request(self.port, "GET", "/replay?file=events.mock.jsonl")
        self.assertEqual(code, 200)
        served = [json.loads(l) for l in raw.decode().splitlines()]
        self.assertEqual(len(served), len(recorded))
        self.assertEqual(request(self.port, "GET", "/replay?file=../app.py")[0], 404)       # whitelist only
        self.assertEqual(request(self.port, "GET", "/replay?file=config.json")[0], 404)

    def test_export_replay_for_static_hosting(self):
        src = os.path.join(self.tmp, "events.mock.jsonl")
        dst = os.path.join(self.tmp, "replay.json")
        info = export(src, dst)
        doc = json.load(open(dst, encoding="utf-8"))
        self.assertTrue(doc["simulated"])
        self.assertEqual(doc["events"][0]["type"], "run_start")
        self.assertEqual(len(doc["events"]), info["events"])
        self.assertEqual(export_main([src, os.path.join("docs", "index.html")]), 2)         # never overwrites the page
        self.assertEqual(export_main([src]), 2)

    def test_version_tree_node_serves_its_experiment_record(self):
        decisions = [e for e in self.runner.events if e["type"] == "decision"]
        self.assertTrue(decisions)
        child = decisions[0]["child"]
        code, j = request(self.port, "GET", f"/experiments?child={child}")
        self.assertEqual(code, 200)
        rec = j["records"][0]
        for k in ("exp_id", "hash", "prev_hash", "checks", "decision", "diff"):
            self.assertIn(k, rec)
        self.assertIn("calls", rec["call_ids"])            # summarised, not the full list
        st = request(self.port, "GET", "/state")[1]
        self.assertTrue(st["harness"]["nodes"])
        # the gate's per-check evidence reaches the page
        gate = [e for e in self.runner.events if e["type"] == "gate_check" and e["name"] == "held-out gain"][0]
        for k in ("n", "mean", "p", "ci", "threshold"):
            self.assertIn(k, gate)


if __name__ == "__main__":
    unittest.main()
