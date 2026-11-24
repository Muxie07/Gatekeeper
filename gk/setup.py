"""Build the model stack for a run: REAL (Gemini free tier, or the approved paid Claude Haiku) or MOCK.

Real: every model must be on the free-tier allowlist, every call goes cache -> rate limiter ->
ledger -> Gemini, and the limits are enforced client-side before each request.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .calllog import CallLog, LoggedProvider
from .lab import Lab
from .mock import mock_player, mock_proposer
from .providers import (DEFAULT_ALLOWED, PAID_PROVIDERS, SpendMeter, DEFAULT_USD_INR, allowlist_for, check_spend_cap, free_tier_allowlist, FallbackProvider, Ledger, MockProvider, build_provider)
from .real import LLMProposer, RealEvaluator, REAL_SUITES

# Proposer fallback chain: strongest free model first. The 20/day models are proposer-only.
DEFAULT_PROPOSERS = [
    {"model": "gemini-3.8-flash", "rpm": 5, "rpd": 20},
    {"model": "gemini-3.6-flash", "rpm": 5, "rpd": 20},
    {"model": "gemini-3.5-flash-lite", "rpm": 15, "rpd": 500},
]


@dataclass
class Stack:
    mode: str                       # "real" | "mock"
    player: object
    proposer: object
    player_model: str
    proposer_models: list
    ledger: Ledger | None = None
    limits: dict = field(default_factory=dict)   # model -> {"rpd": n, "tpd": n}
    workdir: str = "."
    mock_calls: callable = None
    spend: object = None            # SpendMeter for paid providers (None on the free tier)
    calllog: object = None

    def requests(self) -> int:
        """Cumulative REAL requests today (real) or simulated calls (mock)."""
        if self.mode == "mock":
            return self.mock_calls()
        return self.ledger.total_today()

    def make_lab(self, log_path: str = "experiments.jsonl", control=None, emit=None) -> Lab:
        ev = RealEvaluator(self.player, REAL_SUITES)
        return Lab(ev, lambda style: LLMProposer(self.proposer, style), emit=emit, log_path=log_path,
                   requests_fn=self.requests, mode=self.mode, player_model=self.player_model,
                   control=control, calllog=self.calllog)

    def badge(self) -> str:
        return (f"REAL · {self.player_model}" + (" · PAID" if self.player_model.startswith("claude") else " · free tier")
                if self.mode == "real"
                else "MOCK / SIMULATED · no model was called")

    def quota(self) -> dict:
        """Per-model usage today vs the configured limits (the quota meter)."""
        if self.mode == "mock":
            return {"mode": "mock", "note": "simulated calls only, no quota involved",
                    "models": [{"model": "mock-player", "requests": self.mock_calls(),
                                "rpd": None, "tokens": 0, "tpd": None}]}
        rows = []
        for m in dict.fromkeys([self.player_model] + self.proposer_models):
            lim = self.limits.get(m, {})
            used = self.ledger.today(m)
            rows.append({"model": m, "requests": max(used["requests"], _limiter_count(self.workdir, m)),
                         "rpd": lim.get("rpd"), "tokens": used["tokens"], "tpd": lim.get("tpd") or None})
        out = {"mode": "real", "day": self.ledger.day(), "models": rows}
        if getattr(self, "spend", None) is not None:
            out["spend"] = {"spent_inr": round(self.spend.spent_inr(), 4), "cap_inr": self.spend.cap_inr}
        return out

    def exhausted(self) -> str:
        """Non-empty string if the player model has no free requests left today."""
        if self.mode != "real":
            return ""
        q = next(r for r in self.quota()["models"] if r["model"] == self.player_model)
        if q["rpd"] and q["requests"] >= q["rpd"]:
            return f"{self.player_model}: {q['requests']}/{q['rpd']} requests used today"
        if q["tpd"] and q["tokens"] >= q["tpd"]:
            return f"{self.player_model}: {q['tokens']}/{q['tpd']} tokens used today"
        if getattr(self, "spend", None) is not None and self.spend.spent_inr() >= self.spend.cap_inr:
            return f"spending cap reached: Rs {self.spend.spent_inr():.2f} of Rs {self.spend.cap_inr:.0f}"
        return ""


def _provider_of(model: str) -> str:
    return "anthropic" if model.startswith("claude") else "gemini"


def _limiter_count(workdir: str, model: str) -> int:
    """Requests the rate limiter has counted today (includes work before the ledger existed)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    try:
        with open(os.path.join(workdir, f"quota_{_provider_of(model)}_{model}.json"), encoding="utf-8") as f:
            return json.load(f).get(day, 0)
    except (FileNotFoundError, ValueError):
        return 0


def make_real(config_path: str = "config.json", workdir: str = ".", calllog: CallLog | None = None) -> Stack:
    cfg = json.load(open(config_path))
    check_spend_cap(cfg)
    allowed = list(allowlist_for(cfg))
    ledger = Ledger(os.path.join(workdir, "usage_ledger.json"))
    paid = cfg["provider"] in PAID_PROVIDERS
    default_prop = ([{"model": cfg["model"], "rpm": cfg.get("rpm", 50), "rpd": cfg.get("rpd", 20000)}]
                    if paid else DEFAULT_PROPOSERS)
    pmodels = cfg.get("proposer", {}).get("models") or default_prop
    limits = {cfg["model"]: {"rpd": cfg.get("rpd", 500), "tpd": cfg.get("tpd", 0)}}
    for m in pmodels:
        limits.setdefault(m["model"], {"rpd": m.get("rpd", 20), "tpd": m.get("tpd", 0)})
    base = {"provider": cfg["provider"], "allowed_models": allowed}
    if paid:   # every paid model shares one cap and one spend meter
        base.update(max_spend_inr=cfg["max_spend_inr"], usd_inr=cfg.get("usd_inr", DEFAULT_USD_INR))
    player = build_provider({**base, **{k: v for k, v in cfg.items() if k != "proposer"}}, workdir, ledger)
    proposer = FallbackProvider([build_provider({**base, **m}, workdir, ledger) for m in pmodels])
    if calllog is not None:
        player, proposer = LoggedProvider(player, calllog, "player"), LoggedProvider(proposer, calllog, "proposer")
    st = Stack("real", player, proposer, cfg["model"], [m["model"] for m in pmodels], ledger,
               limits, workdir, calllog=calllog)
    if paid:
        st.spend = SpendMeter(os.path.join(workdir, "spend_ledger.json"), cfg["max_spend_inr"],
                              cfg.get("usd_inr", DEFAULT_USD_INR))
    return st


def make_mock(calllog: CallLog | None = None) -> Stack:
    player = MockProvider(mock_player, "mock-player")
    proposer = MockProvider(mock_proposer, "mock-proposer")
    raw_player, raw_proposer = player, proposer
    counts = lambda: raw_player.calls + raw_proposer.calls
    if calllog is not None:
        calllog.simulated = True
        player, proposer = LoggedProvider(player, calllog, "player"), LoggedProvider(proposer, calllog, "proposer")
    return Stack("mock", player, proposer, "mock-player", ["mock-proposer"],
                 mock_calls=counts, calllog=calllog)

