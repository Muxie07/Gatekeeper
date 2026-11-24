"""The visual game's backend: human play runs on the Python engine, never a model; the renderer is served."""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as appmod  # noqa: E402
from gk.env import MiniCraft  # noqa: E402


def request(port, method, path, body=None):
    import http.client, json
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    c.request(method, path, json.dumps(body) if body is not None else None,
              {"Content-Type": "application/json"} if body is not None else {})
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, data

ROOT = os.path.join(os.path.dirname(__file__), "..")


class GameViewBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.runner = appmod.Runner(workdir=cls.tmp, delay=0)
        cls.srv = appmod.serve(cls.runner, port=0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_renderer_and_page_are_served(self):
        code, body = request(self.port, "GET", "/game.js")
        self.assertEqual(code, 200)
        self.assertIn(b"class GameView", body)
        code, page = request(self.port, "GET", "/")
        self.assertIn(b'src="game.js"', page)
        self.assertIn(b'id="world"', page)
        self.assertIn(b'id="hworld"', page)

    def test_human_play_matches_the_python_engine_exactly(self):
        code, s = request(self.port, "POST", "/human/new", {"task": "pickaxe"})
        self.assertEqual(code, 200)
        self.assertEqual((s["mode"], s["step"], s["inventory"]), ("human", 0, {}))
        twin = MiniCraft(s["seed"], "pickaxe", max_steps=14, hazard_p=0.5)   # same world, played directly
        for a in ["craft pickaxe", "collect wood", "collect wood", "craft table", "collect wood",
                  "collect stone", "shelter", "craft pickaxe"]:
            code, s = request(self.port, "POST", "/human/act", {"action": a})
            self.assertEqual(code, 200)
            twin.step(a)
            self.assertEqual(s["inventory"], twin.inv)
            self.assertEqual(s["outcome"], twin.last)
            self.assertEqual((s["step"], s["sheltered"], s["cue"]), (twin.steps, twin.sheltered, twin.cue()))
            if s["done"]:
                break
        self.assertEqual(s["success"], twin.success())

    def test_human_play_validates_and_never_calls_a_model(self):
        request(self.port, "POST", "/human/new", {})
        code, err = request(self.port, "POST", "/human/act", {"action": "teleport to the sword"})
        self.assertEqual(code, 400)
        self.assertIsNone(self.runner.stack)          # no model stack was ever built
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "calls.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "calls.mock.jsonl")))
        self.assertEqual(self.runner.events, [])      # not in the event stream or any recording

    def test_human_game_over_is_refused(self):
        request(self.port, "POST", "/human/new", {"task": "sword"})
        for _ in range(14):
            request(self.port, "POST", "/human/act", {"action": "shelter"})
        code, err = request(self.port, "POST", "/human/act", {"action": "collect wood"})
        self.assertEqual(code, 409)

    def test_human_worlds_are_outside_every_suite(self):
        from gk.real import REAL_SUITES
        seeds = {s for v in REAL_SUITES.values() for s in v[0]}
        self.assertTrue(all(s < appmod.HUMAN_BASE for s in seeds))
        self.assertLess(appmod.HUMAN_BASE + 10**5, appmod.DEMO_BASE + 1)


if __name__ == "__main__":
    unittest.main()
