"""Gatekeeper live app. Python standard library only.

    python app.py            # http://localhost:8000

The self-improvement loop runs in a background thread inside this process. The page is a plain
static file that talks to real endpoints:
    GET  /            the page            GET  /state     snapshot (JSON)
    GET  /events      live Server-Sent Events (add ?after=<id> to resume)
    GET  /budget      request budget for one round        GET  /history  saved experiments (REPLAY)
    POST /control     {action: start|pause|resume|stop, mode: mock|real, rounds: n,
                       confirm_no_billing: bool}   -> limits are enforced HERE, not in the page
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from gk.lab import Control, resume_hint, round_budget
from gk.real import REAL_SUITES
from gk.setup import make_mock, make_real

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_ROUNDS = 6      # server-side cap, whatever the page asks for
MAX_EVENTS = 20000  # in-memory event log (the experiments file is the permanent record)


class Runner:
    """Owns the background run, the event log and the snapshot served to the page."""

    def __init__(self, workdir: str = HERE, config: str = "config.json", delay: float = 0.15,
                 log_path: str | None = None):
        self.workdir, self.config_path, self.delay = workdir, config, delay
        self.log_override = log_path   # tests; otherwise mock and real use SEPARATE files
        self.log_path = log_path or os.path.join(workdir, "experiments.jsonl")
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

    # ---- events -------------------------------------------------------------------------
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

    def _start(self, body: dict) -> tuple[int, dict]:
        if self.status in ("running", "paused"):
            return 409, {"error": "a run is already in progress"}
        mode = body.get("mode", "mock")
        if mode not in ("mock", "real"):
            return 400, {"error": "mode must be mock or real"}
        try:
            rounds = max(1, min(int(body.get("rounds", 2)), MAX_ROUNDS))
        except (TypeError, ValueError):
            return 400, {"error": "rounds must be an integer"}
        if mode == "real":
            if not body.get("confirm_no_billing"):
                return 400, {"error": "confirm that the AI Studio project has NO billing attached "
                                      "(confirm_no_billing)"}
            try:
                stack = make_real(os.path.join(self.workdir, self.config_path), self.workdir)
            except (FileNotFoundError, ValueError, KeyError) as e:
                return 400, {"error": f"cannot start real mode: {e}"}
            if not _has_key():
                return 400, {"error": "no GEMINI_API_KEY found in .env or the environment"}
            used_up = stack.exhausted()
            if used_up:
                return 429, {"error": f"STOP: free quota used up ({used_up}). {resume_hint()}"}
        else:
            stack = make_mock()
        self.stack, self.mode, self.badge = stack, mode, stack.badge()
        self.log_path = self.log_override or os.path.join(
            self.workdir, "experiments.jsonl" if mode == "real" else "experiments.mock.jsonl")
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

    # ---- snapshot -----------------------------------------------------------------------
    def snapshot(self) -> dict:
        lab = self.lab
        snap = {"status": self.status, "mode": self.mode, "badge": self.badge, "error": self.error,
                "params": self.params, "game": self.game, "reports": self.reports,
                "last_event_id": self.events[-1]["id"] if self.events else 0,
                "limits": {"max_rounds": MAX_ROUNDS}}
        snap["quota"] = (self.stack.quota() if self.stack else
                         {"mode": "none", "models": [], "note": "no run yet"})
        snap["harness"] = lab.snapshot() if lab else None
        return snap

    def history(self) -> dict:
        rows = []
        paths = ([self.log_override] if self.log_override else
                 [os.path.join(self.workdir, n) for n in ("experiments.jsonl", "experiments.mock.jsonl")])
        for p in paths:
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        try:
                            rows.append(json.loads(line))   # each row carries its own "mode"
                        except ValueError:
                            pass
            except FileNotFoundError:
                pass
        acc = None
        try:
            with open(os.path.join(self.workdir, "acceptance", "summary.json"), encoding="utf-8") as f:
                acc = json.load(f)
        except (FileNotFoundError, ValueError):
            pass
        return {"label": "REPLAY (saved results, not live)", "experiments": rows, "acceptance": acc}


def _has_key() -> bool:
    from gk.dotenv import load_dotenv
    load_dotenv()
    return bool(os.environ.get("GEMINI_API_KEY"))


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

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "web", "index.html"), "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif u.path == "/state":
                self._json(200, runner.snapshot())
            elif u.path == "/budget":
                self._json(200, round_budget(REAL_SUITES))
            elif u.path == "/history":
                self._json(200, runner.history())
            elif u.path == "/events":
                self._sse(int(parse_qs(u.query).get("after", ["0"])[0] or 0))
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
                        self.wfile.write(f"id: {e['id']}\ndata: {json.dumps(e)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def do_POST(self):
            if urlparse(self.path).path != "/control":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad JSON"})
            code, obj = runner.control_action(body)
            self._json(code, obj)

    return Handler


def serve(runner: Runner, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(runner))
    srv.daemon_threads = True
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()
    runner = Runner(config=args.config)
    srv = serve(runner, port=args.port)
    print(f"Gatekeeper live app: http://localhost:{args.port}  (Ctrl+C to quit)")
    print("Real mode needs config.json + .env; it asks you to confirm free tier and shows the budget.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        runner.control.stop()
