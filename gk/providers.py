"""Provider layer: swap models by changing config, never code.

Stack (outermost first):  Cached -> Limited -> Gemini (or Mock)
A cache hit never touches the rate limiter, so repeated runs cost no quota.
The Gemini adapter has NOT been exercised against the live API from the build
sandbox (no network route); it is tested with an injected fake transport.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .dotenv import load_dotenv


@dataclass
class Completion:
    text: str
    tokens_in: int
    tokens_out: int
    cached: bool = False


class RateLimitError(Exception):
    """HTTP 429 from the provider."""


class TransientError(Exception):
    """Temporary provider problem (HTTP 5xx, timeout, network blip): safe to retry."""


class QuotaExhausted(Exception):
    """Daily request quota used up; stop and resume after the reset."""


class Provider:
    name = "base"
    model = ""

    def complete(self, prompt: str, system: str = "", max_tokens: int = 256) -> Completion:
        raise NotImplementedError


class MockProvider(Provider):
    name = "mock"

    def __init__(self, responder, model: str = "mock"):
        self.responder, self.model, self.calls = responder, model, 0

    def complete(self, prompt, system="", max_tokens=256):
        self.calls += 1
        text = self.responder(prompt)
        return Completion(text, len(prompt) // 4, len(text) // 4)


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, model: str, api_key: str | None = None, timeout: int = 60,
                 opener=urllib.request.urlopen):
        self.model, self.timeout, self.opener = model, timeout, opener
        load_dotenv()
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")

    def complete(self, prompt, system="", max_tokens=256):
        if not self.api_key:
            raise RuntimeError("No API key found. Paste it into the .env file after "
                               "GEMINI_API_KEY= (never commit that file).")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimitError(str(e)) from e
            if e.code in (500, 502, 503, 504):
                raise TransientError(f"HTTP {e.code} from provider (temporary)") from e
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise TransientError(f"network problem: {e}") from e
        parts = data["candidates"][0]["content"].get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        u = data.get("usageMetadata", {})
        return Completion(text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0))


class RateLimiter:
    """Enforces requests/minute and requests/day (day resets at midnight Pacific).
    Counts are persisted so a restart cannot overspend the daily quota."""

    def __init__(self, rpm: int, rpd: int, state_path: str,
                 clock=time.time, sleep=time.sleep):
        self.rpm, self.rpd, self.path = rpm, rpd, state_path
        self.clock, self.sleep = clock, sleep
        self.recent: deque[float] = deque()

    def _day(self) -> str:
        return datetime.fromtimestamp(self.clock(), ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")

    def _load(self) -> dict:
        try:
            with open(self.path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def used_today(self) -> int:
        return self._load().get(self._day(), 0)

    def acquire(self) -> None:
        state, day = self._load(), self._day()
        if state.get(day, 0) >= self.rpd:
            raise QuotaExhausted(f"{self.rpd} requests/day used; resets midnight Pacific.")
        now = self.clock()
        while self.recent and now - self.recent[0] >= 60:
            self.recent.popleft()
        if len(self.recent) >= self.rpm:
            self.sleep(60 - (now - self.recent[0]) + 0.05)
        self.recent.append(self.clock())
        state[day] = state.get(day, 0) + 1
        with open(self.path, "w") as f:
            json.dump(state, f)


class LimitedProvider(Provider):
    def __init__(self, inner: Provider, limiter: RateLimiter, retries: int = 3,
                 sleep=time.sleep):
        self.inner, self.limiter, self.retries, self.sleep = inner, limiter, retries, sleep
        self.name, self.model = inner.name, inner.model

    def complete(self, prompt, system="", max_tokens=256):
        for attempt in range(self.retries + 1):
            self.limiter.acquire()  # failed attempts count too: assume the worst
            try:
                return self.inner.complete(prompt, system, max_tokens)
            except (RateLimitError, TransientError):
                if attempt == self.retries:
                    raise
                self.sleep(2 ** attempt * 5)


class FallbackProvider(Provider):
    """Try providers in order; move on when one is overloaded, rate-limited or out of quota.
    Wrap each member in its own cache first, so resuming a run stays free."""

    def __init__(self, providers: list):
        self.providers = providers
        self.name, self.model = providers[0].name, providers[0].model

    def complete(self, prompt, system="", max_tokens=256):
        last = None
        for p in self.providers:
            try:
                return p.complete(prompt, system, max_tokens)
            except (TransientError, RateLimitError, QuotaExhausted) as e:
                last = e
        raise last


class CachedProvider(Provider):
    """Append-only JSON-lines cache. No file locking needed (SQLite failed on
    network/mounted drives), human-readable, and a crash can lose at most one line."""

    def __init__(self, inner: Provider, path: str):
        self.inner, self.name, self.model, self.path = inner, inner.name, inner.model, path
        self.store: dict = {}
        self.hits = self.misses = 0
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        self.store[d["k"]] = d
                    except (json.JSONDecodeError, KeyError):
                        continue  # ignore a torn last line
        except FileNotFoundError:
            pass

    def _key(self, prompt, system, max_tokens) -> str:
        raw = json.dumps([self.name, self.model, system, prompt, max_tokens])
        return hashlib.sha256(raw.encode()).hexdigest()

    def complete(self, prompt, system="", max_tokens=256):
        k = self._key(prompt, system, max_tokens)
        d = self.store.get(k)
        if d:
            self.hits += 1
            return Completion(d["text"], d["i"], d["o"], cached=True)
        self.misses += 1
        c = self.inner.complete(prompt, system, max_tokens)
        d = {"k": k, "text": c.text, "i": c.tokens_in, "o": c.tokens_out}
        self.store[k] = d
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d) + "\n")
        return c


def build_provider(cfg: dict, workdir: str = ".") -> Provider:
    """cfg example: {"provider":"gemini","model":"gemini-3.5-flash-lite","rpm":15,"rpd":500}"""
    kind = cfg["provider"]
    if kind == "gemini":
        inner: Provider = GeminiProvider(cfg["model"])
    elif kind == "mock":
        inner = MockProvider(lambda p: "collect wood", cfg.get("model", "mock"))
    else:
        raise ValueError(f"unknown provider {kind!r}; add an adapter in providers.py")
    limiter = RateLimiter(cfg.get("rpm", 15), cfg.get("rpd", 500),
                          os.path.join(workdir, f"quota_{kind}_{cfg['model']}.json"))
    return CachedProvider(LimitedProvider(inner, limiter),
                          os.path.join(workdir, f"cache_{kind}_{cfg['model']}.jsonl"))
