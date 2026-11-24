"""Provider layer: swap models by changing config, never code.

Stack (outermost first):  Cached -> Limited -> [Spend] -> Metered -> Gemini | Anthropic | Mock
Gemini runs on the free tier with a hard Rs 0 cap. Anthropic is a PAID provider: it is allowed
only for the models the user approved (claude-haiku-5-5) and only with an explicit max_spend_inr of
at most Rs 300; a persistent spend meter refuses any call that could cross that cap.
A cache hit never touches the rate limiter, so repeated runs cost no quota.
The Gemini adapter has NOT been exercised against the live API from the build
sandbox (no network route); it is tested with an injected fake transport.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
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
    model: str = ""      # which model produced the text (filled in by CachedProvider)
    # evidence fields (never part of the request or the cache key)
    latency_ms: int = 0
    http_status: int | None = None
    model_version: str = ""    # Gemini "modelVersion", when returned
    response_id: str = ""      # Gemini "responseId", when returned
    call_id: str = ""          # id of the line this call got in calls.jsonl


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
        t0 = time.time()
        try:
            with self.opener(req, timeout=self.timeout) as r:
                status = getattr(r, "status", None)
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
        return Completion(text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0),
                          latency_ms=int((time.time() - t0) * 1000),
                          http_status=status if isinstance(status, int) else 200,
                          model_version=str(data.get("modelVersion", "")),
                          response_id=str(data.get("responseId", "")))


class AnthropicProvider(Provider):
    """Claude Messages API over plain HTTPS (no SDK needed). PAID: always wrapped in a SpendProvider."""
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, api_key: str | None = None, timeout: int = 60,
                 opener=urllib.request.urlopen):
        self.model, self.timeout, self.opener = model, timeout, opener
        load_dotenv()
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def complete(self, prompt, system="", max_tokens=256):
        if not self.api_key:
            raise RuntimeError("No Anthropic API key found. Paste it into the .env file after "
                               "ANTHROPIC_API_KEY= (never commit that file).")
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0,
                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        req = urllib.request.Request(
            self.URL, data=json.dumps(body).encode(),
            headers={"content-type": "application/json", "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        t0 = time.time()
        try:
            with self.opener(req, timeout=self.timeout) as r:
                status = getattr(r, "status", None)
                data = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            if e.code == 429:
                raise RateLimitError(f"HTTP 429 {detail}") from e
            if e.code in (500, 502, 503, 504, 529):
                raise TransientError(f"HTTP {e.code} from provider (temporary)") from e
            if "credit balance" in detail.lower():
                raise QuotaExhausted("Anthropic credit balance is too low; add credits in the Console") from e
            raise RuntimeError(f"Anthropic API HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise TransientError(f"network problem: {e}") from e
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        u = data.get("usage", {})
        return Completion(text, u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                          + u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0),
                          latency_ms=int((time.time() - t0) * 1000),
                          http_status=status if isinstance(status, int) else 200,
                          model_version=str(data.get("model", "")), response_id=str(data.get("id", "")))


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
            except RateLimitError:
                raise   # HTTP 429: stop and let the caller report when to resume; no retry loop
            except TransientError:
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
            meta = d.get("meta", {})
            return Completion(d["text"], d["i"], d["o"], cached=True, model=self.model,
                              http_status=meta.get("status"), model_version=meta.get("mv", ""),
                              response_id=meta.get("rid", ""))
        self.misses += 1
        c = self.inner.complete(prompt, system, max_tokens)
        c.model = self.model
        d = {"k": k, "text": c.text, "i": c.tokens_in, "o": c.tokens_out}
        if c.model_version or c.response_id or c.http_status:
            d["meta"] = {"status": c.http_status, "mv": c.model_version, "rid": c.response_id}
        self.store[k] = d
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d) + "\n")
        return c


class Ledger:
    """Persistent per-day, per-model count of REAL requests (cache hits never reach it) and
    tokens. Proves free-tier limits were respected; also enforces an optional tokens/day cap."""

    def __init__(self, path: str, clock=time.time):
        self.path, self.clock = path, clock
        self.lock = threading.Lock()

    def day(self) -> str:
        return datetime.fromtimestamp(self.clock(), ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def record(self, model: str, tokens: int = 0) -> None:
        with self.lock:
            d = self._load()
            row = d.setdefault(self.day(), {}).setdefault(model, {"requests": 0, "tokens": 0})
            row["requests"] += 1
            row["tokens"] += tokens
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=1)

    def today(self, model: str) -> dict:
        return self._load().get(self.day(), {}).get(model, {"requests": 0, "tokens": 0})

    def total_today(self) -> int:
        return sum(r["requests"] for r in self._load().get(self.day(), {}).values())

    def all(self) -> dict:
        return self._load()


class MeteredProvider(Provider):
    """Counts every real API attempt in the Ledger and refuses once tokens/day is used up."""

    def __init__(self, inner: Provider, ledger: Ledger, tpd: int = 0):
        self.inner, self.ledger, self.tpd = inner, ledger, tpd
        self.name, self.model = inner.name, inner.model

    def complete(self, prompt, system="", max_tokens=256):
        if self.tpd and self.ledger.today(self.model)["tokens"] >= self.tpd:
            raise QuotaExhausted(f"{self.tpd} tokens/day used for {self.model}; resets midnight Pacific.")
        try:
            c = self.inner.complete(prompt, system, max_tokens)
        except Exception:
            self.ledger.record(self.model, 0)   # a failed attempt still counts as a request
            raise
        self.ledger.record(self.model, c.tokens_in + c.tokens_out)
        return c


# ---- money: only for paid providers ---------------------------------------------------------
# List prices, USD per million tokens (platform.claude.com/docs/en/about-claude/pricing, 2026-11-24).
PRICES_USD_PER_MTOK = {"claude-haiku-5-5": {"in": 0.10, "out": 0.50, "in_long": 0.50, "out_long": 2.50,
                                            "long_from": 100_000}}


def call_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    p = PRICES_USD_PER_MTOK[model]
    long = tokens_in > p["long_from"]
    return (tokens_in * (p["in_long"] if long else p["in"]) + tokens_out * (p["out_long"] if long else p["out"])) / 1e6


class SpendMeter:
    """Persistent running total of what paid calls cost (list price x the tokens the API reported).
    Before every call it adds a WORST-CASE estimate of that call and refuses if the total could
    cross the cap, so the cap is never exceeded. Anthropic's Console is the authority on billing;
    this meter is a client-side guard. Credit purchases may also carry taxes (not counted here)."""

    def __init__(self, path: str, cap_inr: float, usd_inr: float, clock=time.time):
        self.path, self.cap_inr, self.usd_inr, self.clock = path, float(cap_inr), float(usd_inr), clock
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"total_usd": 0.0, "calls": 0, "by_day": {}}

    def spent_inr(self) -> float:
        return self._load()["total_usd"] * self.usd_inr

    def check(self, model: str, prompt: str, system: str, max_tokens: int) -> None:
        est_in = (len(prompt) + len(system)) // 3 + 64          # generous: ~3 characters per token
        worst = call_cost_usd(model, est_in, max_tokens) * self.usd_inr
        spent = self.spent_inr()
        if spent + worst > self.cap_inr:
            raise QuotaExhausted(f"spending cap reached: Rs {spent:.2f} of Rs {self.cap_inr:.0f} used "
                                 f"(this call could cost up to Rs {worst:.4f}). Raise max_spend_inr only "
                                 "with explicit permission.")

    def record(self, model: str, tokens_in: int, tokens_out: int) -> None:
        usd = call_cost_usd(model, tokens_in, tokens_out)
        with self.lock:
            d = self._load()
            d["total_usd"] = d.get("total_usd", 0.0) + usd
            d["calls"] = d.get("calls", 0) + 1
            day = datetime.fromtimestamp(self.clock(), ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
            row = d.setdefault("by_day", {}).setdefault(day, {}).setdefault(
                model, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "usd": 0.0})
            row["calls"] += 1
            row["tokens_in"] += tokens_in
            row["tokens_out"] += tokens_out
            row["usd"] += usd
            d["cap_inr"], d["usd_inr"] = self.cap_inr, self.usd_inr
            d["total_inr"] = d["total_usd"] * self.usd_inr
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=1)


class SpendProvider(Provider):
    """Wraps a PAID provider: refuses a call that could cross the cap, records what each call cost.
    Failed requests are not billed by Anthropic, so only successful calls are recorded."""

    def __init__(self, inner: Provider, meter: SpendMeter):
        self.inner, self.meter = inner, meter
        self.name, self.model = inner.name, inner.model

    def complete(self, prompt, system="", max_tokens=256):
        self.meter.check(self.model, prompt, system, max_tokens)
        c = self.inner.complete(prompt, system, max_tokens)
        self.meter.record(self.model, c.tokens_in, c.tokens_out)
        return c


# Free-tier models only (AI Studio table). Anything else is refused: no accidental paid model.
DEFAULT_ALLOWED = ("gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash")


MAX_SPEND_INR = 0          # free providers (Gemini): the cap is Rs 0, always
PAID_PROVIDERS = {"anthropic": ("claude-haiku-5-5",)}   # the only paid models the user has approved
PAID_CEILING_INR = 300     # the user's limit; going above it needs a code change AND explicit permission
DEFAULT_USD_INR = 95.0     # approx. mid-market rate on 2026-11-24; config "usd_inr" can update it


def check_spend_cap(cfg: dict) -> None:
    """Free providers: max_spend_inr must be 0. Paid providers: it must be set explicitly, > 0 and
    <= Rs 300 (the user's limit)."""
    kind = cfg.get("provider", "gemini")
    v = cfg.get("max_spend_inr", 0)
    if kind in PAID_PROVIDERS:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= PAID_CEILING_INR:
            raise ValueError(f"{kind} is a PAID provider: set max_spend_inr to an amount above 0 and at most "
                             f"Rs {PAID_CEILING_INR} in config.json (got {v!r}).")
        return
    if v != MAX_SPEND_INR:
        raise ValueError(f"max_spend_inr={v!r} refused: the spending cap is Rs {MAX_SPEND_INR}. Only free-tier "
                         "models can be called; raising the cap requires a code change and explicit permission.")


def allowlist_for(cfg: dict) -> tuple:
    """Models a config may use: the free-tier list for Gemini, the approved paid list for Anthropic.
    A config can only narrow it."""
    kind = cfg.get("provider", "gemini")
    if kind in PAID_PROVIDERS:
        base, req = PAID_PROVIDERS[kind], cfg.get("allowed_models")
        extra = [m for m in (req or []) if m not in base]
        if extra:
            raise ValueError(f"allowed_models {extra} are not approved for {kind} {list(base)}; refusing.")
        return tuple(req) if req else base
    return free_tier_allowlist(cfg.get("allowed_models"))


def free_tier_allowlist(requested=None) -> tuple:
    """A config may only narrow the free-tier allowlist, never add a model to it."""
    if not requested:
        return DEFAULT_ALLOWED
    extra = [m for m in requested if m not in DEFAULT_ALLOWED]
    if extra:
        raise ValueError(f"allowed_models {extra} are not on the free-tier allowlist {list(DEFAULT_ALLOWED)}; "
                         "refusing (could bill).")
    return tuple(requested)


def build_provider(cfg: dict, workdir: str = ".", ledger: "Ledger | None" = None) -> Provider:
    """cfg example: {"provider":"gemini","model":"gemini-3.5-flash-lite","rpm":15,"rpd":500}
    Optional: "tpd" (tokens/day cap), "allowed_models" (can only NARROW the free-tier allowlist),
    "max_spend_inr" (must be 0: no paid path exists in this code)."""
    kind = cfg["provider"]
    if kind == "gemini":
        check_spend_cap(cfg)
        allowed = free_tier_allowlist(cfg.get("allowed_models"))
        if cfg["model"] not in allowed:
            raise ValueError(f"model {cfg['model']!r} is not on the free-tier allowlist {list(allowed)}; "
                             "refusing to call it (could bill).")
        inner: Provider = GeminiProvider(cfg["model"])
    elif kind == "anthropic":
        check_spend_cap(cfg)
        allowed = allowlist_for(cfg)
        if cfg["model"] not in allowed:
            raise ValueError(f"model {cfg['model']!r} is not approved for paid use {list(allowed)}; refusing.")
        inner = AnthropicProvider(cfg["model"])
    elif kind == "mock":
        inner = MockProvider(lambda p: "collect wood", cfg.get("model", "mock"))
    else:
        raise ValueError(f"unknown provider {kind!r}; add an adapter in providers.py")
    if ledger is not None:
        inner = MeteredProvider(inner, ledger, cfg.get("tpd", 0))
    if kind in PAID_PROVIDERS:   # innermost money guard: every attempt is checked, cache hits are free
        inner = SpendProvider(inner, SpendMeter(os.path.join(workdir, "spend_ledger.json"),
                                                cfg["max_spend_inr"], cfg.get("usd_inr", DEFAULT_USD_INR)))
    limiter = RateLimiter(cfg.get("rpm", 15), cfg.get("rpd", 500),
                          os.path.join(workdir, f"quota_{kind}_{cfg['model']}.json"))
    return CachedProvider(LimitedProvider(inner, limiter),
                          os.path.join(workdir, f"cache_{kind}_{cfg['model']}.jsonl"))
