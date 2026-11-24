"""Gatekeeper live app. Python standard library only.

    python app.py            # http://localhost:8000   (binds 127.0.0.1 only)

The self-improvement loop runs in a background thread inside this process. The page is a plain
static file that talks to real endpoints:
    GET  /            the page            GET  /state        snapshot (JSON)
    GET  /events      live Server-Sent Events (add ?after=<id> to resume)
    GET  /budget      request budget for one round            GET  /history   saved experiments (REPLAY)
    GET  /experiments?child=<version>   the experiment record(s) behind a version-tree node
    GET  /replays     recorded event files                    GET  /replay?file=<name>   one of them
    POST /control     {action: start|pause|resume|stop, mode: mock|real, rounds: n, confirm_no_billing}
    POST /play        {mode, confirm_no_billing}: ONE new game with the current harness on a fresh demo
                      world, every step a live model call (cache bypassed)
Limits are enforced HERE, not in the page. Requests whose Host or Origin is not local are refused.
Every event the page receives is also appended to events.jsonl (mock: events.mock.jsonl) so a run can
be replayed later with  /?replay=<file>  (permanent REPLAY badge).
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from gk.calllog import CallLog, LoggedProvider
from gk.env import ACTIONS, MiniCraft
from gk.evaluator import task_for
from gk.harness import Harness
from gk.lab import Control, resume_hint, round_budget
from gk.providers import QuotaExhausted, RateLimitError, TransientError
from gk.real import REAL_SUITES, RealEvaluator
from gk.setup import make_mock, make_real

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_ROUNDS = 6      # server-side cap, whatever the page asks for
MAX_EVENTS = 20000  # in-memory event log (events.jsonl and experiments.jsonl are the permanent records)
HUMAN_BASE = 800000  # human-played worlds: never used by any suite, never sent to a model
DEMO_MOCK_PACE = 0.9  # seconds per step for a MOCK demo game, so it can be watched
DEMO_BASE = 900000  # demo worlds: seeds 900000+counter, never used by any suite, so never cached
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


class Runner:
    """Owns the background run, the event log and the snapshot served to the page."""

    def __init__(self, workdir: str = HERE, config: str = "config.json", delay: float = 0.15,
                 log_path: str | None = None, events_path: str | None = None):
        self.workdir, self.config_path, self.delay = workdir, config, delay
        self.log_override = log_path   # tests; otherwise mock and real use SEPARATE files
        self.events_override = events_path
        self.log_path = log_path or os.path.join(workdir, "experiments.jsonl")
        self.events_path = events_path or os.path.join(workdir, "events.mock.jsonl")
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.status, self.mode, self.badge = "idle", "", "IDLE · nothing running"
        self.thread: threading.Thread | None = None
        self.control = Control()
        self.stack = self.lab = None
        self.reports: dict = {}
        self.game: dict = {}
        self.error = ""
        self.params: dict = {}
        self.playing = False
        self.demo_threads: list = []
        self.human_env, self.human_n, self.human_lock = None, 0, threading.Lock()
        self._events_lock = threading.Lock()

    # ---- events -------------------------------------------------------------------------
    def _set_files(self, mode: str) -> None:
        self.log_path = self.log_override or os.path.join(
            self.workdir, "experiments.jsonl" if mode == "real" else "experiments.mock.jsonl")
        self.events_path = self.events_override or os.path.join(
            self.workdir, "events.jsonl" if mode == "real" else "events.mock.jsonl")

    def post(self, type_: str, **data) -> None:
        with self.cond:
            ev = {"id": (self.events[-1]["id"] + 1) if self.events else 1, "type": type_, **data}
            self.events.append(ev)
            if len(self.events) > MAX_EVENTS:
                del self.events[: len(self.events) - MAX_EVENTS]
            if type_ == "step":
                self.game = ev
            elif type_ == "episode_start":
                self.game = {**ev, "step": 0, "inventory": {}, "sheltered": False, "cue": False,
                             "outcome": "", "raw": "", "action": ""}
            elif type_ == "report":
                self.reports[data["stage"]] = {k: v for k, v in data.items() if k != "ts"}
            self.cond.notify_all()
        try:                                  # the permanent record of what the page was shown
            with self._events_lock, open(self.events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        except OSError:
            pass

    def wait_events(self, after: int, timeout: float = 15.0) -> list[dict]:
        with self.cond:
            if not (self.events and self.events[-1]["id"] > after):
                self.cond.wait(timeout)
            return [e for e in self.events if e["id"] > after]

    # ---- control ------------------------------------------------------------------------
    def control_action(self, body: dict) -> tuple[int, dict]:
        action = body.get("action")
        if action == "start":
            return self._start(body)
        if action in ("pause", "resume", "stop"):
            if self.status not in ("running", "paused"):
                return 409, {"error": f"nothing to {action}: status is {self.status}"}
            getattr(self.control, action)()
            if action == "pause":
                self.status = "paused"
            elif action == "resume":
                self.status = "running"
            self.post("control", action=action)
            return 200, {"ok": True, "status": self.status}
        return 400, {"error": f"unknown action {action!r}"}

    def _build_stack(self, mode: str, body: dict):
        """-> (stack, None) or (None, (http_code, error_dict)). Real mode is guarded here."""
        if mode == "real":
            if not body.get("confirm_no_billing"):
                return None, (400, {"error": "confirm that the AI Studio project has NO billing "
                                             "attached (confirm_no_billing)"})
            try:
                stack = make_real(os.path.join(self.workdir, self.config_path), self.workdir,
                                  CallLog(os.path.join(self.workdir, "calls.jsonl")))
            except (FileNotFoundError, ValueError, KeyError) as e:
                return None, (400, {"error": f"cannot start real mode: {e}"})
            if not _has_key():
                return None, (400, {"error": "no GEMINI_API_KEY found in .env or the environment"})
            used_up = stack.exhausted()
            if used_up:
                return None, (429, {"error": f"STOP: free quota used up ({used_up}). {resume_hint()}"})
            return stack, None
        return make_mock(CallLog(os.path.join(self.workdir, "calls.mock.jsonl"), simulated=True)), None

    def _start(self, body: dict) -> tuple[int, dict]:
        if self.status in ("running", "paused"):
            return 409, {"error": "a run is already in progress"}
        if self.playing:
            return 409, {"error": "a demo game is being played; wait for it to finish"}
        mode = body.get("mode", "mock")
        if mode not in ("mock", "real"):
            return 400, {"error": "mode must be mock or real"}
        try:
            rounds = max(1, min(int(body.get("rounds", 2)), MAX_ROUNDS))
        except (TypeError, ValueError):
            return 400, {"error": "rounds must be an integer"}
        stack, err = self._build_stack(mode, body)
        if err:
            return err
        self.stack, self.mode, self.badge = stack, mode, stack.badge()
        self.lab = None
        self._set_files(mode)
        self.control = Control()
        self.reports, self.game, self.error, self.status = {}, {}, "", "running"
        self.params = {"mode": mode, "rounds": rounds}
        self.thread = threading.Thread(target=self._work, args=(rounds,), daemon=True)
        self.thread.start()
        return 200, {"ok": True, "badge": self.badge, "rounds": rounds,
                     "budget": round_budget(REAL_SUITES)}

    def _work(self, rounds: int) -> None:
        stack = self.stack
        try:
            self.lab = lab = stack.make_lab(self.log_path, self.control, self.post)
            if self.mode == "mock":
                lab.ev.checkpoint = lambda: (self.control.checkpoint(), time.sleep(self.delay))
            self.post("run_start", badge=self.badge, player=stack.player_model,
                      proposers=stack.proposer_models, rounds=rounds, mode=self.mode,
                      budget=round_budget(REAL_SUITES))
            lab.measure("original")
            lab.phase = "live"
            status = lab.run(rounds)
            if status == "completed" or status.startswith("stopped: an improvement"):
                lab.measure("current")
            self.status = "stopped" if status != "completed" else "finished"
            self.post("quota", **stack.quota())
        except Exception as e:      # surface bugs in the page instead of dying silently
            self.error = f"{type(e).__name__}: {e}"
            self.status = "error"
            self.post("error", message=self.error)

    # ---- "start a new game" -------------------------------------------------------------
    def _next_demo_seed(self) -> int:
        path = os.path.join(self.workdir, "demo_counter.json")
        try:
            with open(path, encoding="utf-8") as f:
                n = int(json.load(f).get("n", 0))
        except (FileNotFoundError, ValueError, AttributeError):
            n = 0
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"n": n + 1}, f)
        return DEMO_BASE + n

    def play(self, body: dict) -> tuple[int, dict]:
        if self.status == "running":
            return 409, {"error": "the improvement loop is running; pause or stop it first, or wait"}
        if self.playing:
            return 409, {"error": "a demo game is already being played"}
        mode = body.get("mode") or self.mode or "mock"
        if mode not in ("mock", "real"):
            return 400, {"error": "mode must be mock or real"}
        if self.stack is None or self.stack.mode != mode:
            stack, err = self._build_stack(mode, body)
            if err:
                return err
            self.stack, self.mode, self.badge, self.lab = stack, mode, stack.badge(), None
            self._set_files(mode)
        elif mode == "real":
            used_up = self.stack.exhausted()
            if used_up:
                return 429, {"error": f"STOP: free quota used up ({used_up}). {resume_hint()}"}
        harness = self.lab.current if self.lab else Harness.initial()
        seed = self._next_demo_seed()
        task = task_for(seed, 0.5)
        self.playing = True
        t = threading.Thread(target=self._play_work, args=(self.stack, harness, seed, task), daemon=True)
        self.demo_threads.append(t)
        t.start()
        return 200, {"ok": True, "seed": seed, "task": task, "version": harness.version,
                     "badge": self.badge, "live_calls": mode == "real"}

    def _play_work(self, stack, harness: Harness, seed: int, task: str) -> None:
        try:
            ev = RealEvaluator(_live_provider(stack))
            ev.emit = lambda t, **k: self.post(t, mode=stack.mode, **k)
            ev.calllog, ev.label = stack.calllog, "demo game"
            ev.context = {"suite": "demo", "version": harness.version}
            if stack.mode == "mock":   # slow enough to watch on the game canvas
                ev.checkpoint = lambda: time.sleep(max(self.delay, DEMO_MOCK_PACE) if self.delay else 0)  # 0 in tests
            self.post("demo_start", seed=seed, task=task, version=harness.version, badge=self.badge,
                      mode=stack.mode, lessons=[l["text"] for l in harness.lessons()])
            ep = ev.episode(harness, seed, task, max_steps=14, hazard_p=0.5)
            self.post("demo_end", seed=seed, success=ep.success, steps=ep.steps, mistakes=ep.events,
                      mode=stack.mode)
        except (QuotaExhausted, RateLimitError, TransientError) as e:
            self.post("stopped", reason=f"STOP: {type(e).__name__}: {e}. {resume_hint()}")
        except Exception as e:
            self.post("error", message=f"demo game failed: {type(e).__name__}: {e}")
        finally:
            self.playing = False

    # ---- human play: the person picks actions, the Python engine decides what happens ---------
    def human(self, verb: str, body: dict) -> tuple[int, dict]:
        """No model, no quota, no experiment records. The browser only sends the chosen action;
        every outcome below comes from MiniCraft itself."""
        with self.human_lock:
            if verb == "new":
                self.human_n += 1
                seed = HUMAN_BASE + self.human_n
                task = body.get("task") if body.get("task") in ("pickaxe", "sword") else task_for(seed, 0.5)
                self.human_env = MiniCraft(seed, task, max_steps=14, hazard_p=0.5)
                return 200, self._human_state("")
            if verb != "act":
                return 404, {"error": "not found"}
            env = self.human_env
            if env is None:
                return 409, {"error": "start a new world first"}
            if env.done():
                return 409, {"error": "this game is over; start a new world"}
            action = body.get("action")
            if action not in ACTIONS:
                return 400, {"error": f"action must be one of {ACTIONS}"}
            env.step(action)
            return 200, self._human_state(action)

    def _human_state(self, action: str) -> dict:
        env = self.human_env
        return {"mode": "human", "suite": "human", "label": "you", "version": "-",
                "seed": env.seed, "task": env.task, "hazard": env.hazard, "max_steps": env.max_steps,
                "step": env.steps, "action": action, "raw": action, "outcome": env.last,
                "inventory": dict(env.inv), "sheltered": env.sheltered, "cue": env.cue(),
                "done": env.done(), "success": env.success(), "mistakes": list(env.events)}

    # ---- snapshot -----------------------------------------------------------------------
    def snapshot(self) -> dict:
        lab = self.lab
        snap = {"status": self.status, "mode": self.mode, "badge": self.badge, "error": self.error,
                "params": self.params, "game": self.game, "reports": self.reports,
                "playing": self.playing,
                "last_event_id": self.events[-1]["id"] if self.events else 0,
                "limits": {"max_rounds": MAX_ROUNDS}}
        snap["quota"] = (self.stack.quota() if self.stack else
                         {"mode": "none", "models": [], "note": "no run yet"})
        snap["harness"] = lab.snapshot() if lab else None
        return snap

    def _read_rows(self, paths) -> list[dict]:
        rows = []
        for p in paths:
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            pass
            except FileNotFoundError:
                pass
        return rows

    def _exp_paths(self) -> list[str]:
        return ([self.log_override] if self.log_override else
                [os.path.join(self.workdir, n) for n in ("experiments.jsonl", "experiments.mock.jsonl")])

    def history(self) -> dict:
        rows = self._read_rows(self._exp_paths())   # each row carries its own "mode"
        acc = None
        try:
            with open(os.path.join(self.workdir, "acceptance", "summary.json"), encoding="utf-8") as f:
                acc = json.load(f)
        except (FileNotFoundError, ValueError):
            pass
        return {"label": "REPLAY (saved results, not live)", "experiments": rows, "acceptance": acc}

    def experiments_for(self, version: str) -> list[dict]:
        """Records about one version (as child), trimmed: big per-world arrays are summarised."""
        out = []
        for r in self._read_rows(self._exp_paths()):
            if r.get("child") == version:
                r = dict(r)
                r["call_ids"] = f"{len(r.get('call_ids', []))} calls (first: {', '.join(r.get('call_ids', [])[:2])})"
                r["suites"] = {s: {w: {"n": len(d.get("scores", [])), "mean": (sum(d["scores"]) / len(d["scores"]))
                                       if d.get("scores") else None} for w, d in who.items()}
                               for s, who in r.get("suites", {}).items()}
                out.append(r)
        return out

    def replay_files(self) -> list[str]:
        try:
            return sorted(n for n in os.listdir(self.workdir)
                          if n.startswith("events") and n.endswith(".jsonl"))
        except OSError:
            return []


def _live_provider(stack):
    """The player WITHOUT its cache, so every demo step is a fresh, rate-limited, metered call.
    (Real stack: Logged(Cached(Limited(Metered(Gemini)))) -> Logged(Limited(Metered(Gemini))).)"""
    p = stack.player
    if stack.mode == "mock":
        return p
    cached = p.inner if isinstance(p, LoggedProvider) else p
    limited = getattr(cached, "inner", cached)
    return LoggedProvider(limited, stack.calllog, "player") if stack.calllog else limited


def _has_key() -> bool:
    from gk.dotenv import load_dotenv
    load_dotenv()
    return bool(os.environ.get("GEMINI_API_KEY"))


def _host_ok(value: str | None) -> bool:
    """Host header: localhost / 127.0.0.1 / ::1 (any port). Blocks DNS-rebinding style access."""
    if not value:
        return False
    host = value.rsplit(":", 1)[0] if not value.startswith("[") else value.split("]")[0] + "]"
    return host in LOCAL_HOSTS


def _origin_ok(value: str | None) -> bool:
    if not value:
        return True            # not a browser cross-origin request (curl, tests, same-origin GET)
    u = urlparse(value)
    return u.scheme in ("http", "https") and (u.hostname or "") in ("localhost", "127.0.0.1", "::1")


def make_handler(runner: Runner):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):   # keep the console quiet (and key-free)
            pass

        def _json(self, code: int, obj) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _local_only(self) -> bool:
            if not _host_ok(self.headers.get("Host")) or not _origin_ok(self.headers.get("Origin")):
                self._json(403, {"error": "forbidden: only local requests are accepted"})
                return False
            return True

        def do_GET(self):
            if not self._local_only():
                return
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html", "/game.js"):
                name = "game.js" if u.path == "/game.js" else "index.html"
                with open(os.path.join(HERE, "web", name), "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8" if name == "game.js"
                                 else "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif u.path == "/state":
                self._json(200, runner.snapshot())
            elif u.path == "/budget":
                self._json(200, round_budget(REAL_SUITES))
            elif u.path == "/history":
                self._json(200, runner.history())
            elif u.path == "/experiments":
                self._json(200, {"records": runner.experiments_for(q.get("child", [""])[0])})
            elif u.path == "/replays":
                self._json(200, {"files": runner.replay_files()})
            elif u.path == "/replay":
                name = q.get("file", [""])[0]
                if name not in runner.replay_files():     # whitelist: only recorded event files
                    return self._json(404, {"error": "no such replay file"})
                with open(os.path.join(runner.workdir, name), "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif u.path == "/events":
                self._sse(int(q.get("after", ["0"])[0] or 0))
            else:
                self._json(404, {"error": "not found"})

        def _sse(self, after: int) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    evs = runner.wait_events(after)
                    if not evs:
                        self.wfile.write(b": keepalive\n\n")
                    for e in evs:
                        after = e["id"]
                        self.wfile.write(f"id: {e['id']}\ndata: {json.dumps(e, default=str)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def do_POST(self):
            if not self._local_only():
                return
            path = urlparse(self.path).path
            if path not in ("/control", "/play", "/human/new", "/human/act"):
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad JSON"})
            if path.startswith("/human/"):
                code, obj = runner.human(path.rsplit("/", 1)[1], body)
            else:
                code, obj = (runner.control_action if path == "/control" else runner.play)(body)
            self._json(code, obj)

    return Handler


def serve(runner: Runner, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("the server binds 127.0.0.1 only")
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(runner))
    srv.daemon_threads = True
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()
    runner = Runner(config=args.config)
    srv = serve(runner, port=args.port)
    print(f"Gatekeeper live app: http://localhost:{args.port}  (127.0.0.1 only; Ctrl+C to quit)")
    print("Real mode needs config.json + .env; it asks you to confirm free tier and shows the budget.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        runner.control.stop()
