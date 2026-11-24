# HANDOFF_2: real run on env v2 (paired slip noise)

You are continuing the Gatekeeper project for another Claude session. Read `HANDBACK.md` (your
predecessor's report) for context. **Job: re-measure the real baseline on env v2, run the improvement
loop, analyse it, regenerate the dashboard, and write `HANDBACK_2.md`.** Do not redesign anything.

## Why this run exists
Run 1 (env v1) showed the LLM's lesson cut "not enough materials" failures from 11 to 5 on held-out
episodes, but random slips (5 -> 8) cancelled the step saving, so the gate rejected it (+0.005, lower
bound -0.020). Env v2 makes slips **paired**: the n-th attempt of an action in a world always has the
same outcome, so two harness versions face the same luck. Question for this run: **with the noise
paired, can the gate now detect a real improvement, and does it promote it?** Either answer is fine.

## What changed since HANDBACK.md (already in the code, tested: 27 tests)
- `gk/env.py`: paired slips (ENV_VERSION = 2). This invalidates most cached episodes, so the
  baseline is re-measured with live calls.
- `gk/real.py`: proposer is told which lessons were rejected and must not repeat them; prefers the
  mistake that wastes most steps (zombie wipe ~4 steps vs failed craft 1); explains when it returns
  nothing. Your `max_tokens=1024` fix is kept.
- `run_real_loop.py`: never overwrites an earlier `real_run.json` (archives it as `real_run.<time>.json`).
- `analyze_real.py`: replays cached calls only (cannot spend quota) and writes `real_analysis.json`.

## Hard rules (same as before)
1. Never print, log, commit or paste the API key (`.env`). Do not `cat .env`.
2. Spend no money. Free tier only.
3. Do not edit or delete `quota_*.json`. If a run stops with `QuotaExhausted`, that is expected: stop.
4. Do not change gate thresholds, suites, env rules, the agent prompt in `gk/harness.py`, or the
   proposer prompt to get a nicer result. Fix only clear bugs, and list every change.
5. No network use other than the project's own Gemini calls.
6. Do **not** use `--red-team` this time (it was done in run 1 and costs ~200 requests).

## Quota facts
- Daily limits reset at **midnight Pacific = 1:30 pm IST** (November, PST).
- `gemini-3.5-flash-lite`: 500/day (player, and last-resort proposer). `gemini-3.8-flash` and
  `gemini-3.6-flash`: 20/day each (proposer). Counts: `type quota_gemini_*.json`.
- One full round costs roughly 400-450 Flash-Lite requests on env v2 (baseline 10 + train 6 +
  held-out 10 for parent and child + canary 5 x2 + regression 5 x2 episodes, ~10 calls each, minus
  cache hits). Two rounds will span two quota days. That is fine: every call is cached.

## Steps
1. `python -m unittest discover -s tests` -> expect 27 OK. If not, stop and report.
2. Check `quota_gemini_gemini-3.5-flash-lite.json`. Note today's count in the report.
3. `python run_real_loop.py config.json --rounds 2`
   - The `[baseline]` line will now take a few minutes (live calls), and its numbers will differ
     from run 1. That is expected.
   - On `STOPPED (TransientError/RateLimitError)`: wait 2 minutes, rerun the same command once.
   - On `STOPPED (QuotaExhausted)`: do not retry. Go to step 5 and report it as partial.
4. If the run completed: `python analyze_real.py config.json`, then `python run_demo.py`
   (regenerates `dashboard/index.html` with the new real section). Before running analyze, copy the
   existing `real_analysis.json` to `real_analysis.v1.json` if that file does not exist yet.
5. Write `HANDBACK_2.md` (format below). Keep all `real_run*.json` files.

## HANDBACK_2.md format
```
# HANDBACK_2
## Status: COMPLETE | PARTIAL (quota) | FAILED (why)
## Commands run (in order, with exit status)
## Console output (verbatim, relevant lines, no secrets)
## Results table (from real_run.json "report")
| Stage | success | mean_steps | score | tokens |
## Gate decisions (from real_run.json "log")
round | kind | PROMOTE/reject | reason | lesson text
## Held-out comparison (from real_analysis.json "held", if produced)
parent vs child: mean_steps, success, events
## Quota used today per model (start and end)
## Problems and how you handled them
## Code changes (file, what, why) or "none"
## Your read: did pairing the noise change what the gate could detect?
```
If PARTIAL: tell the user to run `python run_real_loop.py config.json --rounds 2` again after
1:30 pm IST; it resumes from cache for free, then do steps 4-5.

## When done
Tell the user: "HANDBACK_2.md is written. Return to the other Claude chat and say: handback 2 ready."
