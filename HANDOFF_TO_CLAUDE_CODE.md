# Handoff to Claude Code: run the Gatekeeper real-model loop

You are picking up a project built in another Claude session. That session could not reach Google's
API; this machine can. **Your job: run the real-model experiment, observe, fix only genuine bugs, and
write a report.** Then the other session will continue from your report.

## Project in one paragraph
Gatekeeper is a promotion gate for self-improving agent harnesses. An LLM plays a small crafting game
(`MiniCraft`), a proposer LLM writes one lesson per round from failed episodes, and a gate
(`gk/gate.py`) accepts a lesson only if it improves verified held-out results without breaking
integrity, cost or an older skill (regression rollback). A simulated version of the experiment already
works (`python run_demo.py`). **The real-model run has never been completed.** See `README.md`.

## State you inherit
- Real baseline already measured (Gemini `gemini-3.5-flash-lite`, seeds 500-509): success 10/10,
  mean steps 9.7 (ideal 7.8), score 0.515. Avoidable mistakes: crafting without enough materials (8),
  zombie wipe because the agent never shelters (2), iron before pickaxe (1). 8 random "slips" are noise.
- 24 unit tests pass (`python -m unittest discover -s tests`).
- `run_real_loop.py` crashed once with HTTP 503 from `gemini-3.8-flash` (temporary Google overload).
  Retries + a model fallback chain were added afterwards. Not yet re-run.
- Python 3.10+, Windows + Anaconda PowerShell. `tzdata` is installed.

## Hard rules
1. **Never print, log, commit or paste the API key.** It lives in `.env` (git-ignored). Do not `cat .env`.
2. **Spend no money.** Free tier only. Do not enable billing or add paid models.
3. **Respect the quota.** `rpd` limits are in `config.json` (Flash-Lite 500/day). The limiter persists
   counts in `quota_*.json`; do not delete or edit those files. If the run stops with `QuotaExhausted`,
   that is normal: do NOT work around it. Report and stop.
4. **Do not change** `gk/gate.py` thresholds, `gk/real.py` suites, `gk/env.py` rules or the prompt in
   `gk/harness.py` to get a nicer result. Honest numbers matter more than good numbers.
5. You may fix clear bugs (crashes, wrong parsing, wrong paths). List every change you make.
6. Do not use the network for anything except the Gemini API calls the project makes.

## Steps
1. `python -m unittest discover -s tests` and confirm all pass.
2. `python run_real_loop.py config.json` and let it run (several minutes; calls are paced to 15/min).
   - The `[baseline]` line should print instantly from cache: success 1.00, steps 9.70, score 0.515.
   - On HTTP 503/429 it retries by itself. If it stops with `STOPPED (...)`, wait 2 minutes and rerun the
     same command once. If it stops again for the same reason, stop and report.
3. When it finishes, run `python run_real_loop.py config.json --red-team --rounds 1` **only if** the
   quota counters show more than 150 Flash-Lite requests remaining
   (`type quota_gemini_gemini-3.5-flash-lite.json`; limit 500). Otherwise skip it and say so.
4. Collect results: `real_run.json`, `llm_baseline.json`, the printed console lines.

## Write `HANDBACK.md` in this folder with exactly these sections
```
# HANDBACK
## Commands run (in order, with exit status)
## Console output (verbatim, trimmed to the relevant lines; no secrets)
## Results table
| Stage | success | mean_steps | score | tokens |   <- from real_run.json "report"
## Gate decisions
round | kind | PROMOTE/reject | reason | lesson text        <- from real_run.json "log"
## Quota used today (per model, from quota_*.json)
## Problems hit and how you handled them
## Code changes you made (file, what, why) or "none"
## Your read: did the gate behave sensibly? Anything surprising?
```
Keep raw `real_run.json` and `llm_baseline.json` in the folder. Do not summarize numbers you did not
see. If something could not be run, say exactly why.

## When done
Tell the user: "HANDBACK.md is written. Return to the other Claude chat and say: handback ready."
