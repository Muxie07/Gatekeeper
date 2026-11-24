# Resume tomorrow: the real run

**When:** after **2026-11-25 13:30 IST**. That is when Google resets the free daily quota (midnight Pacific).
Before then the run would stop immediately because Flash-Lite shows 500/500 for today.

## What to run (PowerShell, in the project folder)
1. Start the run (free tier only):
   ```
   python run_acceptance.py --yes
   ```
   It first prints the request budget, measures the baseline (already saved, so it costs nothing), checks that
   the regression suite is usable, then judges the first honest lesson, the placebo and overfit lessons, and the
   "ignore step cost" lesson, and finally compares the original and final harness on unseen worlds.
2. To WATCH it, in a second PowerShell window: `python app.py`, then open http://localhost:8000.
   (The page shows what the app itself runs. For the acceptance run, the evidence is in the files below. The page
   has a History tab and a replay option once events are recorded. Do not start a second real run from the page
   while the first one is going: both would use the same daily quota.)
3. Check the evidence when it stops or finishes:
   ```
   python -m gk.verify
   ```
   It checks the hash chain, that every cited model call exists, and recomputes every gate number. It prints
   PASS/FAIL per check and ends with VERIFIED or NOT VERIFIED. `acceptance/ACCEPTANCE.md` is built only from
   records that pass this check.

## How many requests
About **1,800 typical (2,600 worst case)** in total, so roughly **4 quota days** at 500 per day. Some of it is
already saved (baseline, training episodes, the first lesson), which helps on day 1. The gate rejects weak
lessons early, which is cheap; a lesson that reaches every check costs the most.

## If it stops on quota again
It prints `PARTIAL: STOP: quota exhausted ...` and the next reset time in IST, and writes a partial
`acceptance/ACCEPTANCE.md` (unfinished criteria say NOT YET). That is normal. After the next 13:30 IST, run the
**same command** again: finished work is replayed from saved results for free and it continues.
Only the 20-per-day models (the proposer's first choices) run out quickly; it then falls back to Flash-Lite.

If it stops with "regression suite at floor", the old-skill test is too hard to show a regression: it stops BEFORE
judging any lesson, so no quota is wasted on judging. Tell me and I will adjust the suite.

## Please do not
- Edit `gk/gate.py`, `gk/env.py`, `gk/evaluator.py`, `gk/stats.py` or `prereg/PREREG.md` while the run is in
  progress or after it starts. Their fingerprints are stored in every record and the verifier will flag a change.
- Delete `cache_*.jsonl`, `quota_*.json`, `usage_ledger.json`, `calls.jsonl` or `experiments.jsonl`.

## Money
**Nothing costs money.** Only free-tier models are allowed (anything else is refused before a call is made), the
run stops on the first 429 or quota error instead of retrying, and `acceptance/requests.json` records the real
requests per model per day. Make sure your AI Studio project has no billing attached (you confirmed this earlier;
the `--yes` flag means you still confirm it).
