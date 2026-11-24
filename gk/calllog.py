"""Per-call evidence log: one line in calls.jsonl for EVERY model request, cache hit or fresh call.

Fields: call_id, ts, role (player/proposer), model, cache ("hit"/"miss"), prompt_sha (sha256 of
system+prompt; prompts are game text, the hash is enough to identify them), tokens_in, tokens_out,
latency_ms, http_status, modelVersion and responseId (when Gemini returned them), and the world,
suite, harness version, episode and step when known. The API key is never logged.
Mock runs write calls.mock.jsonl and every line says "simulated": true.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

from .providers import Completion, Provider


def prompt_sha(system: str, prompt: str) -> str:
    return hashlib.sha256((system + "\n\x00\n" + prompt).encode("utf-8")).hexdigest()


class CallLog:
    def __init__(self, path: str, simulated: bool = False):
        self.path, self.simulated = path, simulated
        self.lock = threading.Lock()
        self.context: dict = {}       # world / suite / version / episode / step, set by the evaluator
        self.n = self._existing()
        self.stamp = int(time.time())

    def _existing(self) -> int:
        try:
            with open(self.path, encoding="utf-8") as f:
                return sum(1 for _ in f)
        except FileNotFoundError:
            return 0

    def set_context(self, **ctx) -> None:
        self.context = ctx

    def clear_context(self) -> None:
        self.context = {}

    def mark(self) -> int:
        return self.n

    def log(self, **fields) -> str:
        with self.lock:
            self.n += 1
            call_id = f"c{self.stamp:x}-{self.n:06d}"
            rec = {"call_id": call_id, "ts": round(time.time(), 3), **fields}
            if self.simulated:
                rec["simulated"] = True
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return call_id


class LoggedProvider(Provider):
    """Outermost wrapper: logs the request and stamps the returned Completion with its call_id."""

    def __init__(self, inner: Provider, calllog: CallLog, role: str):
        self.inner, self.calllog, self.role = inner, calllog, role
        self.name, self.model = inner.name, inner.model

    def complete(self, prompt, system="", max_tokens=256) -> Completion:
        t0 = time.time()
        ctx = dict(self.calllog.context) if self.role == "player" else {}
        try:
            c = self.inner.complete(prompt, system, max_tokens)
        except Exception as e:
            self.calllog.log(role=self.role, model=self.model, cache="miss", error=type(e).__name__,
                             prompt_sha=prompt_sha(system, prompt),
                             latency_ms=int((time.time() - t0) * 1000), **ctx)
            raise
        c.call_id = self.calllog.log(
            role=self.role, model=c.model or self.model, cache="hit" if c.cached else "miss",
            prompt_sha=prompt_sha(system, prompt), tokens_in=c.tokens_in, tokens_out=c.tokens_out,
            latency_ms=c.latency_ms if not c.cached else int((time.time() - t0) * 1000),
            http_status=c.http_status, modelVersion=c.model_version or None,
            responseId=c.response_id or None, **ctx)
        return c


def read_calls(path: str) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    except FileNotFoundError:
        pass
    return rows
