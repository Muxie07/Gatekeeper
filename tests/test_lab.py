"""Env v3, the live loop (Lab), rollback, quota stop and the app's real endpoints. MOCK models only:
no network, no quota."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as appmod
from gk.env import MiniCraft
from gk.harness import DEFAULT_FILES, Harness, add_lessons, placebo
from gk.lab import Lab, Stopped, resume_hint, round_budget
from gk.mock import mock_player, mock_proposer
from gk.providers import MockProvider, QuotaExhausted
from gk.real import LLMProposer, RealEvaluator, REAL_SUITES


def make_lab(tmp, player=None, events=None):
    player = player or MockProvider(mock_player, "mock-player")
    ev = RealEvaluator(player)
    lab = Lab(ev, lambda s: LLMProposer(MockProvider(mock_proposer, "mock-proposer"), s),
              emit=(lambda t, **k: events.append((t, k))) if events is not None else None,
              log_path=os.path.join(tmp, "exp.jsonl"), requests_fn=lambda: player.calls)
    return lab, player


class EnvV3(unittest.TestCase):
    def test_default_prompt_contains_no_recipes(self):
        p = DEFAULT_FILES["prompt.md"].lower()
        for word in ("wood", "stone", "iron", "table costs", "needs a table", "pickaxe costs"):
            self.assertNotIn(word, p)

    def test_failure_feedback_states_the_rule_truthfully(self):
        e = MiniCraft(1, "pickaxe", hazard_p=0)
        e.step("craft pickaxe")
        self.assertIn("craft pickaxe needs a table", e.last)
        e.step("craft table")
        self.assertIn("craft table needs 2 wood (you have 0 wood)", e.last)
        e.step("collect iron")
        self.assertIn("needs a pickaxe", e.last)

    def test_zombie_wipes_wood_and_table_unless_sheltered(self):
        def play(shelter):
            e = MiniCraft(1, "sword", hazard_p=1.0)
            plan = ["collect wood", "collect wood", "craft table", "collect wood", "collect wood"]
            if shelter:
                plan[3] = "shelter"
            for a in plan:
                e.step(a)
            return e
        unsafe, safe = play(False), play(True)
        self.assertTrue(unsafe.hazard)
        self.assertNotIn("table", unsafe.inv)
        self.assertNotIn("wood", unsafe.inv)
        self.assertIn("zombie_wipe", unsafe.events)
        self.assertIn("table", safe.inv)
        self.assertNotIn("zombie_wipe", safe.events)

    def test_cue_starts_at_step_3_and_shelter_clears_it(self):
        e = MiniCraft(1, "pickaxe", hazard_p=1.0)
        e.step("collect wood"); e.step("collect wood")
        self.assertNotIn("groaning", e.obs())
        e.step("collect wood")
        self.assertIn("groaning", e.obs())
        e.step("shelter")
        self.assertNotIn("groaning", e.obs())
        self.assertIn("shelter", e.obs())

    def test_tight_regression_budget_punishes_a_wasted_step(self):
        # 6 steps is the minimum pickaxe plan; budget 7 leaves exactly one step of slack
        h0 = Harness.initial()
        _, p = make_lab(tempfile.mkdtemp())
        ev = RealEvaluator(p)
        careful = h0.with_patch(add_lessons(h0, [{"tag": "x", "text": "Count your inventory first."}]))
        wasteful = careful.with_patch(add_lessons(careful, [{"tag": "y", "text": "very first action shelter"}]))
        s_c, s_w = ev.mean_true(careful, "regression"), ev.mean_true(wasteful, "regression")
        self.assertGreater(s_c - s_w, 0.03)


class LabLoop(unittest.TestCase):
    def test_honest_lesson_is_accepted_and_every_check_has_a_number(self):
        with tempfile.TemporaryDirectory() as d:
            events = []
            lab, _ = make_lab(d, events=events)
            res = lab.run_round("honest")
            self.assertTrue(res[0].accepted, res[0].reason)
            names = [c["name"] for c in res[0].checks]
            self.assertEqual(names, ["allowlist", "held-out gain", "beats placebo",
                                     "regression suite", "integrity", "canary", "cost"])
            self.assertTrue(all(c["pass"] and c["value"] is not None or c["name"] == "allowlist"
                                for c in res[0].checks))
            self.assertNotEqual(lab.current.version, lab.original.version)
            types = {t for t, _ in events}
            self.assertTrue({"step", "episode_start", "episode_end", "proposal", "candidate",
                             "gate_check", "decision", "mistakes"} <= types)

    def test_placebo_and_overfit_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            lab, _ = make_lab(d)
            self.assertFalse(lab.submit("placebo", placebo(lab.current), "placebo").accepted)
            o = lab.run_round("overfit")[0]
            self.assertFalse(o.accepted)
            self.assertEqual(o.kind, "overfit")

    def test_regression_check_rejects_a_lesson_that_helps_held_out_but_wastes_steps(self):
        with tempfile.TemporaryDirectory() as d:
            lab, _ = make_lab(d)
            o = lab.run_side("adversarial")[0]     # judged against the ORIGINAL harness
            self.assertFalse(o.accepted)
            self.assertTrue(o.reason.startswith("regression"), o.reason)
            held = next(c for c in o.checks if c["name"] == "held-out gain")
            self.assertTrue(held["pass"])          # it genuinely looked good on hazard worlds
            self.assertEqual(lab.current.version, lab.original.version)   # lineage untouched

    def test_rollback_of_an_accepted_version_that_regresses_on_fresh_worlds(self):
        with tempfile.TemporaryDirectory() as d:
            events = []
            lab, _ = make_lab(d, events=events)
            bad = lab.original.with_patch(add_lessons(lab.original, [
                {"tag": "w", "text": "Always build a shelter as your very first action."}]))
            lab.nodes[bad.version] = lab._node(bad, lab.original.version, "current", "shelter first", 1, "llm")
            lab.current, lab.pending_audit = bad, (lab.original, bad)
            self.assertTrue(lab.audit())
            self.assertEqual(lab.current.version, lab.original.version)
            self.assertEqual(lab.nodes[bad.version]["status"], "rolled_back")
            self.assertIn("rollback", {t for t, _ in events})
            rows = [json.loads(l) for l in open(os.path.join(d, "exp.jsonl"))]
            self.assertEqual(rows[-1]["kind"], "rollback")
            self.assertEqual(rows[-1]["decision"], "rollback")

    def test_good_version_survives_the_audit(self):
        with tempfile.TemporaryDirectory() as d:
            lab, _ = make_lab(d)
            lab.run_round("honest")
            self.assertFalse(lab.audit())

    def test_experiments_file_has_required_fields_and_is_not_double_logged(self):
        with tempfile.TemporaryDirectory() as d:
            lab, _ = make_lab(d)
            lab.run_round("honest")
            path = os.path.join(d, "exp.jsonl")
            rows = [json.loads(l) for l in open(path)]
            for k in ("ts", "round", "parent", "child", "lesson", "diff", "proposer_model", "checks",
                      "metrics", "decision", "reason", "requests_used", "mode", "env_version"):
                self.assertIn(k, rows[0])
            self.assertEqual(rows[0]["proposer_model"], "mock-proposer")
            self.assertGreater(rows[0]["requests_used"], 0)
            lab2, _ = make_lab(d)               # a resumed run replays the same experiment
            lab2.run_round("honest")
            self.assertEqual(len(open(path).read().splitlines()), len(rows))

    def test_rejected_lesson_is_remembered_and_not_repeated(self):
        with tempfile.TemporaryDirectory() as d:
            lab, _ = make_lab(d)
            lab.rejected.append("When you hear groaning, build a shelter right away before continuing.")
            res = lab.run_round("honest")
            self.assertNotIn("groaning", res[0].child.lessons()[-1]["text"])

    def test_quota_exhaustion_stops_with_a_resume_time_and_no_retry(self):
        with tempfile.TemporaryDirectory() as d:
            class Dies(MockProvider):
                def complete(self, *a, **k):
                    if self.calls >= 5:
                        raise QuotaExhausted("500 requests/day used")
                    return super().complete(*a, **k)
            events = []
            lab, p = make_lab(d, Dies(mock_player, "mock-player"), events)
            status = lab.run(2)
            self.assertIn("quota exhausted", status)
            self.assertIn("IST", status)
            self.assertEqual(p.calls, 5)               # it stopped; nothing kept calling
            self.assertEqual(events[-1][0], "stopped")

    def test_stop_control_ends_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            lab, p = make_lab(d)
            lab.control.stop()
            self.assertEqual(lab.run(3), "stopped by user")
            self.assertEqual(p.calls, 0)

    def test_pause_blocks_until_resumed(self):
        with tempfile.TemporaryDirectory() as d:
            lab, p = make_lab(d)
            lab.control.pause()
            t = threading.Thread(target=lab.run, args=(1,), daemon=True)
            t.start(); time.sleep(0.5)
            self.assertEqual(p.calls, 0)
            lab.control.resume()
            t.join(60)
            self.assertGreater(p.calls, 0)

    def test_budget_math(self):
        b = round_budget(REAL_SUITES)
        self.assertEqual(b["per_suite_worst"]["regression"], 5 * 7)
        self.assertEqual(b["first_round_worst"], 603)
        self.assertLess(b["first_round_typical"], b["first_round_worst"])
        self.assertIn("IST", resume_hint())


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read()


def _post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class AppEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.runner = appmod.Runner(workdir=cls.tmp, delay=0)
        cls.srv = appmod.serve(cls.runner, port=0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_1_page_and_idle_state(self):
        code, html = _get(self.base + "/")
        self.assertEqual(code, 200)
        self.assertIn(b"EventSource", html)
        s = json.loads(_get(self.base + "/state")[1])
        self.assertEqual(s["status"], "idle")

    def test_2_real_mode_refused_without_billing_confirmation_and_without_config(self):
        code, j = _post(self.base + "/control", {"action": "start", "mode": "real"})
        self.assertEqual(code, 400)
        self.assertIn("billing", j["error"])
        code, j = _post(self.base + "/control", {"action": "start", "mode": "real", "confirm_no_billing": True})
        self.assertEqual(code, 400)       # no config.json in the temp workdir -> refuses, no call made
        self.assertEqual(json.loads(_get(self.base + "/state")[1])["status"], "idle")

    def test_3_mock_run_end_to_end_with_live_events(self):
        code, j = _post(self.base + "/control", {"action": "start", "mode": "mock", "rounds": 999})
        self.assertEqual(code, 200)
        self.assertLessEqual(j["rounds"], appmod.MAX_ROUNDS)      # server-side cap
        self.assertIn("MOCK", j["badge"])
        self.assertEqual(_post(self.base + "/control", {"action": "start", "mode": "mock"})[0], 409)
        self.runner.thread.join(120)
        s = json.loads(_get(self.base + "/state")[1])
        self.assertIn(s["status"], ("finished", "stopped"), s.get("error"))
        self.assertIn("MOCK", s["badge"])
        self.assertIn("original", s["reports"])
        self.assertIn("current", s["reports"])
        self.assertTrue(s["harness"]["nodes"])
        # the SSE endpoint streams the genuine event log
        with urllib.request.urlopen(self.base + "/events?after=0", timeout=10) as r:
            seen, buf = set(), b""
            while len(seen) < 6:
                line = r.readline()
                if line.startswith(b"data:"):
                    seen.add(json.loads(line[5:])["type"])
                buf += line
                if len(buf) > 400000:
                    break
        self.assertIn("run_start", seen)
        self.assertEqual(self.runner.game["type"], "step")   # /state serves the last real step
        types = {e["type"] for e in self.runner.events}
        self.assertTrue({"step", "episode_end", "proposal", "gate_check", "decision", "report"} <= types)
        step = next(e for e in self.runner.events if e["type"] == "step")
        for k in ("prompt", "raw", "action", "outcome", "inventory", "sheltered", "cached"):
            self.assertIn(k, step)

    def test_4_history_is_labelled_replay_and_marks_mock_rows(self):
        h = json.loads(_get(self.base + "/history")[1])
        self.assertIn("REPLAY", h["label"])
        for row in h["experiments"]:
            self.assertEqual(row["mode"], "mock")

    def test_5_pause_without_a_run_is_refused(self):
        self.runner.status = "idle"
        self.assertEqual(_post(self.base + "/control", {"action": "pause"})[0], 409)


if __name__ == "__main__":
    unittest.main()
