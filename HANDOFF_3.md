# HANDOFF_3: placebo control run (one round, ~100 requests)

Continue the Gatekeeper project. Context: `HANDBACK_2.md` (your last report) and the README section
"Second real run". **Job: run ONE round with the new placebo control, regenerate the dashboard, write
`HANDBACK_3.md`.** Do not redesign anything.

## Why
Run 2 found that two different Gemini lessons produced identical actions in all 10 held-out episodes
(+0.050 gain each). Hypothesis: *any* extra text in memory changes the agent's behaviour, so the gain
is not caused by the lesson's content. The new placebo control tests this: a content-free note of
similar length ("Keep this note in mind while you play the game...") is added to the parent and
scored on the same held-out worlds. If the placebo also gains about +0.05, the hypothesis holds.

## What changed (already in the code, 29 tests)
- `gk/harness.py`: `placebo()` builds the content-free control.
- `gk/gate.py`: optional placebo check: a lesson must also beat the placebo (lower bound > 0).
- `run_real_loop.py`: each round logs and prints a `[placebo]` diagnostic (placebo vs parent), then
  passes the placebo to the gate.
- `gk/dashboard.py`: real section shows every gate decision, including placebo rows.

## Hard rules (unchanged)
1. Never print, log, commit or paste the API key. Do not `cat .env`.
2. No money. Free tier only. 3. Do not touch `quota_*.json`; on `QuotaExhausted`, stop and report.
4. Do not change thresholds, suites, env rules, the agent prompt or the proposer prompt. Fix only
   clear bugs and list every change. 5. No network use except the project's Gemini calls.
6. No `--red-team`.

## Run it today, before the 1:30 pm IST quota reset
It must run today: round 1's proposer call and the lesson's episodes are cached from run 2, so only
the placebo episodes are new (~100 Flash-Lite requests; about 150 remain today). After the reset the
proposer would answer differently and the comparison with run 2 would be lost.

1. `python -m unittest discover -s tests` -> expect 29 OK.
2. Note `quota_gemini_gemini-3.5-flash-lite.json`.
3. `python run_real_loop.py config.json --rounds 1`
   - Expect the `[baseline]` line instantly (cached: 1.00 / 9.20 / 0.540) and the same round-1 lesson
     as run 2 ("Craft a sword early ..."). If the lesson is different, say so in the report.
   - On `STOPPED (TransientError/RateLimitError)`: wait 2 minutes, rerun once.
   - On `STOPPED (QuotaExhausted)`: stop, report PARTIAL.
4. If complete: `python analyze_real.py config.json` then `python run_demo.py`.
5. Write `HANDBACK_3.md`.

## HANDBACK_3.md format
```
# HANDBACK_3
## Status: COMPLETE | PARTIAL (quota) | FAILED (why)
## Commands run (with exit status)
## Console output (verbatim, relevant lines, no secrets)
## Placebo vs parent (mean, lower bound) and lesson vs parent (mean, lower bound)
## Was the lesson identical to run 2's round-1 lesson? (yes/no + text)
## Per-episode steps on held-out: parent / placebo / lesson (if you can get them from cache)
## Quota used (start and end, per model)
## Problems, and code changes (or "none")
## Your read: does the placebo explain run 2's gain?
```

## When done
Tell the user: "HANDBACK_3.md is written. Return to the other Claude chat and say: handback 3 ready."
