"""The self-improvement loop as one object, shared by the live app and the acceptance run.

    play train episodes with the CURRENT harness (one model call per step)
 -> collect failed episodes -> proposer analyses them and writes ONE lesson
 -> gate: held-out gain, placebo, regression suite, integrity, canary, cost
 -> accept: child becomes current  |  reject: keep parent and remember the lesson
 -> next round: AUDIT the previously accepted version on fresh old-skill worlds and ROLL BACK
    to its parent if it regressed
Every candidate is appended to experiments.jsonl. Every step is emitted as an event.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .chain import GENESIS, code_sha, prereg_sha, record_hash
from .env import ENV_VERSION, MAX_STEPS
from .evaluator import claimed_score, suite_opts, true_score
from .gate import Gate
from .harness import Harness, placebo_for
from .providers import QuotaExhausted, RateLimitError, TransientError
from .real import avoidable_counts


class Stopped(Exception):
    """The user pressed Stop."""


class Control:
    """Pause / resume / stop, checked before every model call."""

    def __init__(self):
        self._stop, self._run = threading.Event(), threading.Event()
        self._run.set()

    def pause(self):
        self._run.clear()

    def resume(self):
        self._run.set()

    def stop(self):
        self._stop.set()
        self._run.set()

    @property
    def paused(self) -> bool:
        return not self._run.is_set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def checkpoint(self):
        while not self._run.wait(0.2):
            pass
        if self._stop.is_set():
            raise Stopped()


def resume_hint(now: datetime | None = None) -> str:
    la = ZoneInfo("America/Los_Angeles")
    now = (now or datetime.now(la)).astimezone(la)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ist = nxt.astimezone(ZoneInfo("Asia/Kolkata"))
    return f"Daily quota resets at midnight Pacific = {ist:%Y-%m-%d %H:%M} IST. Resume after that."


def round_budget(suites: dict, typical: float = 0.7) -> dict:
    """Model calls (one per agent step) for one round, worst case (every episode runs to max_steps)
    and typical (successful episodes end early). Parent results are cached across candidates."""
    def calls(name):
        seeds, _, opts = suite_opts(suites[name])
        return len(list(seeds)) * opts.get("max_steps", MAX_STEPS)
    c = {n: calls(n) for n in suites}
    first_round = {  # nothing cached yet, honest candidate that reaches every check
        "parent: train+held+regression+canary": c["train"] + c["held"] + c["regression"] + c["canary"],
        "child: held+regression+canary": c["held"] + c["regression"] + c["canary"],
        "placebo: held": c["held"],
        "proposer": 1,
    }
    later_round = {  # parent = last accepted child: its suites are already cached
        "train (new parent)": c["train"],
        "child: held+regression+canary": c["held"] + c["regression"] + c["canary"],
        "placebo: held": c["held"],
        "audit (parent+child)": 2 * c["audit"],
        "proposer": 1,
    }
    rejected_early = {"train": c["train"], "child: held": c["held"], "proposer": 1}
    out = {"per_suite_worst": c, "first_round_worst": sum(first_round.values()),
           "later_round_worst": sum(later_round.values()),
           "rejected_candidate_worst": sum(rejected_early.values()),
           "report_suite_worst": c["report"]}
    out["first_round_typical"] = round(out["first_round_worst"] * typical)
    out["later_round_typical"] = round(out["later_round_worst"] * typical)
    out["detail_first_round"], out["detail_later_round"] = first_round, later_round
    return out


@dataclass
class Outcome:
    accepted: bool
    reason: str
    child: Harness
    kind: str
    checks: list
    metrics: dict


class Lab:
    def __init__(self, ev, make_proposer, gate: Gate | None = None, emit=None,
                 log_path: str = "experiments.jsonl", requests_fn=None, mode: str = "mock",
                 player_model: str = "", control: Control | None = None, calllog=None):
        self.ev, self.make_proposer = ev, make_proposer
        self.calllog = calllog
        self.prev_hash, self.rows = GENESIS, 0
        ev.calllog = calllog
        self.gate = gate or Gate(ev)
        self._emit = emit or (lambda t, **k: None)
        self.log_path, self.requests_fn = log_path, requests_fn or (lambda: 0)
        self.mode, self.player_model = mode, player_model
        self.control = control or Control()
        self.original = self.current = Harness.initial()
        self.rejected: list[str] = []
        self.nodes: dict = {self.current.version: self._node(self.current, None, "current",
                                                             "(original harness)", 0, "root")}
        self.pending_audit = None
        self.round, self.phase = 0, "honest"
        self.last_decision: dict = {}
        self.lock = threading.Lock()
        self._seen = self._load_seen()
        ev.emit, ev.checkpoint, ev.on_suite = self.emit, self.control.checkpoint, self._on_suite

    # ---- plumbing -----------------------------------------------------------------------
    @staticmethod
    def _node(h: Harness, parent, status, lesson, rnd, kind) -> dict:
        return {"version": h.version, "parent": parent, "status": status, "lesson": lesson,
                "round": rnd, "kind": kind}

    def emit(self, type_: str, **data) -> None:
        self._emit(type_, **{"ts": time.time(), "mode": self.mode, "phase": self.phase,
                             "round": self.round, **data})

    def _on_suite(self, event: str, **info) -> None:
        eps = info.pop("episodes", None)
        if eps is not None:
            info["success"] = sum(e.success for e in eps) / len(eps)
            info["mean_steps"] = sum(e.steps for e in eps) / len(eps)
            info["score"] = sum(true_score(e) for e in eps) / len(eps)
        self.emit(event, **info)

    def _load_seen(self) -> set:
        seen = set()
        try:
            with open(self.log_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        seen.add((r["phase"], r["round"], r["parent"], r["child"], r["kind"]))
                        self.prev_hash, self.rows = r.get("hash", self.prev_hash), self.rows + 1
                    except (ValueError, KeyError):
                        pass
        except FileNotFoundError:
            pass
        return seen

    def log_experiment(self, rec: dict) -> None:
        rec = {"ts": time.time(), "env_version": ENV_VERSION, "mode": self.mode,
               "player_model": self.player_model, "phase": self.phase, "round": self.round, **rec}
        key = (rec["phase"], rec["round"], rec["parent"], rec["child"], rec["kind"])
        with self.lock:
            if key in self._seen:        # a resumed run replays from cache: do not double-log
                return
            self._seen.add(key)
            rec["exp_id"] = f"e{self.rows + 1:04d}"
            rec["prev_hash"] = self.prev_hash
            rec["prereg_sha"], rec["code_sha"] = prereg_sha(), code_sha()
            rec["hash"] = record_hash(rec)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            self.prev_hash, self.rows = rec["hash"], self.rows + 1
        self.emit("experiment", exp_id=rec["exp_id"], hash=rec["hash"][:12], kind=rec["kind"],
                  decision=rec["decision"])

    def suite_data(self, harnesses: dict, suites=("held", "regression", "canary", "audit")) -> tuple:
        """Per-world scores (and the call ids behind them) for every evaluated (harness, suite):
        stored in the record so a verifier can recompute the gate numbers."""
        data, ids = {}, set()
        for suite in suites:
            row = {}
            for name, h in harnesses.items():
                eps = self.ev.cached(h, suite)
                if not eps:
                    continue
                row[name] = {"version": h.version, "seeds": [e.seed for e in eps],
                             "scores": [round(true_score(e), 6) for e in eps]}
                if suite == "held":
                    row[name]["claimed_scores"] = [round(claimed_score(e, h), 6) for e in eps]
                    row[name]["tokens"] = [e.tokens for e in eps]
                for e in eps:
                    ids.update(e.call_ids or [])
            if row:
                data[suite] = row
        return data, ids

    @staticmethod
    def diff(parent: Harness, child: Harness) -> dict:
        old = {l["text"] for l in parent.lessons()}
        new = {l["text"] for l in child.lessons()}
        files = [k for k in child.files if child.files[k] != parent.files.get(k)]
        return {"added_lessons": sorted(new - old), "removed_lessons": sorted(old - new),
                "files_changed": files}

    # ---- the loop -----------------------------------------------------------------------
    def evaluate(self, parent: Harness, child: Harness, kind: str, note: str,
                 proposer_model: str = "", side: bool = False, proposer_call_id: str = "") -> Outcome:
        """side=True: judge the candidate against `parent` WITHOUT touching the main lineage
        (used to test a tempting-but-bad lesson against the original harness)."""
        touched = [k for k in child.files if child.files[k] != parent.files.get(k)]
        plc = placebo_for(parent, child)
        self.emit("candidate", kind=kind, parent=parent.version, child=child.version, lesson=note,
                  diff=self.diff(parent, child), proposer_model=proposer_model, side=side)
        r0 = self.requests_fn()
        d = self.gate.check(parent, child, touched, placebo=plc, regression=True,
                            on_check=lambda c: self.emit("gate_check", child=child.version, **c))
        used = self.requests_fn() - r0
        verdict = "accepted" if d.accepted else "rejected"
        suites, ids = self.suite_data({"parent": parent, "child": child, "placebo": plc})
        if proposer_call_id:
            ids.add(proposer_call_id)
        self.log_experiment({
            "parent": parent.version, "child": child.version, "kind": kind, "lesson": note,
            "diff": self.diff(parent, child), "proposer_model": proposer_model,
            "checks": d.checks, "metrics": d.metrics, "decision": verdict, "reason": d.reason,
            "requests_used": used, "side_branch": side, "suites": suites,
            "call_ids": sorted(ids), "proposer_call_id": proposer_call_id})
        self.last_decision = {"child": child.version, "accepted": d.accepted, "reason": d.reason,
                              "checks": d.checks, "kind": kind}
        if side:
            pass   # side branch: recorded, but the main lineage is untouched
        elif d.accepted:
            self.nodes[self.current.version]["status"] = (
                "accepted" if self.current.version != self.original.version else "root")
            self.nodes[child.version] = self._node(child, parent.version, "current", note,
                                                   self.round, kind)
            self.current = child
            self.pending_audit = (parent, child)
        else:
            self.nodes.setdefault(child.version, self._node(child, parent.version, "rejected",
                                                            note, self.round, kind))
            if kind == "llm":
                self.rejected.append(note)
        self.emit("decision", child=child.version, accepted=d.accepted, reason=d.reason,
                  kind=kind, requests_used=used, current=self.current.version, side=side)
        return Outcome(d.accepted, d.reason, child, kind, d.checks, d.metrics)

    def run_side(self, style: str, parent: Harness | None = None) -> list[Outcome] | None:
        """Propose from `parent` (default: the original harness) and judge against it, off-lineage."""
        self.control.checkpoint()
        self.round += 1
        parent = parent or self.original
        self.emit("round_start", version=parent.version, style=style, side=True)
        train = self.ev.run(parent, "train")
        prop = self.make_proposer(style)
        cands = prop.propose(parent, train, [])
        model = prop.last_model or getattr(prop.provider, "model", "")
        self.emit("proposal", style=style, status=prop.last_status, model=model,
                  lesson=cands[0].note if cands else "", counts=prop.last_counts, side=True)
        return [self.evaluate(parent, parent.with_patch(c.patch), c.kind, c.note, model, side=True,
                              proposer_call_id=prop.last_call_id)
                for c in cands] if cands else None

    def audit(self):
        """Next-round check of the version accepted last round on FRESH old-skill worlds."""
        if not self.pending_audit:
            return None
        parent, child = self.pending_audit
        self.pending_audit = None
        if self.current.version != child.version:
            return None
        before, after = self.ev.mean_true(parent, "audit"), self.ev.mean_true(child, "audit")
        drop = before - after
        bad = drop > self.gate.cfg.audit_tol
        self.emit("audit", version=child.version, parent=parent.version, before=before,
                  after=after, drop=drop, regressed=bad)
        suites, ids = self.suite_data({"parent": parent, "child": child}, suites=("audit",))
        self.log_experiment({
            "suites": suites, "call_ids": sorted(ids),
            "parent": parent.version, "child": child.version,
            "kind": "rollback" if bad else "audit", "lesson": self.nodes[child.version]["lesson"],
            "diff": self.diff(parent, child), "proposer_model": "",
            "checks": [{"name": "post-acceptance audit", "pass": not bad, "value": round(drop, 4),
                        "detail": f"fresh old-skill worlds: {before:.3f} -> {after:.3f}"}],
            "metrics": {"audit_before": before, "audit_after": after, "audit_drop": round(drop, 4)},
            "decision": "rollback" if bad else "kept",
            "reason": (f"ROLLBACK: audit dropped {drop:.3f}" if bad else "audit passed"),
            "requests_used": 0})
        if bad:
            self.nodes[child.version]["status"] = "rolled_back"
            self.nodes[parent.version]["status"] = "current"
            self.current = parent
            self.emit("rollback", version=child.version, restored=parent.version, drop=drop)
        return bad

    def run_round(self, style: str = "honest") -> list[Outcome] | None:
        self.control.checkpoint()
        self.round += 1
        self.emit("round_start", version=self.current.version, style=style)
        self.audit()
        train = self.ev.run(self.current, "train")
        self.emit("mistakes", counts=dict(avoidable_counts(train)),
                  episodes=[{"seed": e.seed, "task": e.task, "success": e.success,
                             "steps": e.steps, "mistakes": e.events} for e in train])
        prop = self.make_proposer(style)
        cands = prop.propose(self.current, train, self.rejected)
        model = prop.last_model or getattr(prop.provider, "model", "")
        self.emit("proposal", style=style, status=prop.last_status, model=model,
                  lesson=cands[0].note if cands else "", counts=prop.last_counts)
        if not cands:
            self.log_experiment({"parent": self.current.version, "child": "-", "kind": "none",
                                 "lesson": "", "diff": {}, "proposer_model": model, "checks": [],
                                 "metrics": {}, "decision": "none", "reason": prop.last_status,
                                 "requests_used": 0, "suites": {},
                                 "call_ids": [prop.last_call_id] if prop.last_call_id else [],
                                 "proposer_call_id": prop.last_call_id})
            return None
        return [self.evaluate(self.current, self.current.with_patch(c.patch), c.kind, c.note, model,
                              proposer_call_id=prop.last_call_id) for c in cands]

    def submit(self, kind: str, child: Harness, note: str, model: str = "") -> Outcome:
        """Submit an externally built candidate (placebo note, etc.) to the same gate."""
        self.control.checkpoint()
        self.round += 1
        self.emit("round_start", version=self.current.version, style=kind)
        self.audit()
        return self.evaluate(self.current, child, kind, note, model)

    def measure(self, tag: str, harness: Harness | None = None) -> dict:
        h = harness or self.current
        eps = self.ev.run(h, "report")
        row = {"stage": tag, "version": h.version, "n": len(eps),
               "success": sum(e.success for e in eps) / len(eps),
               "mean_steps": sum(e.steps for e in eps) / len(eps),
               "score": self.ev.mean_true(h, "report")}
        self.emit("report", **row)
        return row

    def run(self, rounds: int, style: str = "honest", stop_when_accepted: bool = False) -> str:
        status = "completed"
        try:
            for _ in range(rounds):
                res = self.run_round(style)
                if stop_when_accepted and res and any(o.accepted for o in res):
                    status = "stopped: an improvement was accepted"
                    break
        except Stopped:
            status = "stopped by user"
        except QuotaExhausted as e:
            status = f"STOP: quota exhausted ({e}) {resume_hint()}"
        except RateLimitError as e:
            status = f"STOP: the provider kept refusing requests (HTTP 429: {e}). This is usually the PER-MINUTE limit, not the daily one: wait 2 minutes and run the same command again; finished work is cached."
        except TransientError as e:
            status = f"STOP: provider temporarily unavailable ({e}). Safe to resume later; work is cached."
        self.emit("stopped", reason=status)
        return status

    def snapshot(self) -> dict:
        return {"current": self.current.version, "original": self.original.version,
                "nodes": list(self.nodes.values()), "rejected": list(self.rejected),
                "round": self.round, "phase": self.phase, "last_decision": self.last_decision,
                "lessons": [l["text"] for l in self.current.lessons()]}

