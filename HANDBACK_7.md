# HANDBACK_7: statistics and suite fixes

## Status: DONE (offline, mock only, zero real calls)

## What changed
- `gk/stats.py` (new): one-sided paired sign-flip permutation test. Exact for n <= 20 (enumerates all 2^n sign patterns via a dynamic programme over the distinct achievable sums, so it is exact and fast); 20,000 seeded random flips for larger n. Also a 95% percentile-bootstrap CI, for display only.
- `gk/gate.py` rewritten to the preregistered rules; `GateConfig` fields match `prereg/PREREG.md` (a test checks both agree):
  held-out p < 0.05 and mean >= 0.02; placebo p < 0.10 and mean > 0; regression drop <= 0.03 and parent mean >= 0.2 (else "uninformative", candidate NOT accepted); canary >= -0.05; integrity <= 0.10; cost <= 10% (30% if gain >= 0.05); audit current - parent >= -0.03 else rollback. Every check now records n, mean, p (where applicable), CI and the threshold text.
- `prereg/PREREG.md` (new): suites, rule, reasons (incl. why regression/audit budget is 9).
- `gk/real.py` suites: held 8 -> 16 worlds (seeds 100-115; P(sword) 0.7, budget 14, P(hazard) 0.7 unchanged); regression and audit 5 -> 6 worlds, budget 7 -> 9.
- `gk/harness.py`: `placebo_for(parent, child)` builds a content-free placebo whose length equals the candidate's added text (deterministic, so its cache key is stable). `gk/lab.py` uses it. Audit now uses `audit_tol` (0.03).
- `gk/acceptance.py`: `preflight(lab)` measures the original on the regression suite and refuses (message "regression suite at floor") if it scores < 0.2; `run_acceptance.py` calls it right after the baseline, before any candidate. `acceptance_budget()` includes the pre-flight and the new suite sizes.
- Tests: new `tests/test_stats_gate.py` (known small permutation cases, brute-force cross-check, seeded large-n path, config-vs-PREREG agreement, suite sizes and no-overlap, check fields, floor path, placebo length/determinism, budget). `tests/test_lab.py` budget test updated.

## Tests
`python -m unittest discover -s tests` -> **63 tests OK** (51 before).

## New request estimate (printed by `python run_acceptance.py`, no calls made)
| part | worst-case requests |
|---|---|
| baseline report (original) | 140 |
| pre-flight: original on regression suite | 54 |
| phase 1 honest, first round | 977 |
| phase 2 placebo + overfit round | 448 |
| phase 3 adversarial (current + original side branch) | 726 |
| audit of accepted versions | 108 |
| final report | 140 |
| **total** | **2593 worst, ~1815 typical (~3.6 quota days at 500/day)** |
This is higher than the old 1548/1084 because held-out doubled. It assumes the first honest candidate is accepted; each extra honest round costs up to ~750 worst case (or ~200 if rejected at the held-out check). Today's real cache still covers the baseline and the train suite.

## Cache check (batch rule)
Recomputed the prompt for report seed 500, step 0 and looked for its key in `cache_gemini_gemini-3.5-flash-lite.jsonl`: **present**. The report and train suites, env rules/observation text, default harness and LLMAgent prompt were not touched.

## Decisions made without approval
1. Exact p-value via a sums-distribution DP instead of literal 2^n loops: same result, much faster (verified against `itertools.product` brute force in a test).
2. Placebo for a candidate that adds no text (e.g. a tamper edit) uses the default 90 characters; minimum placebo length 20.
3. The placebo submitted as a candidate in phase 2 is judged against a placebo of its own length, so it should be rejected at the held-out check.
4. Kept the shelter-first unit test unchanged: at budget 9 the score gap is still 0.05 > 0.03, so no adjustment was needed.
5. The old simulation (`gk/loop.py`, `SUITES`) keeps its own tests and still passes with the new gate; it does not use the regression pre-check.

## Left undone
Nothing for this task. Note for the real run: the held-out parent episodes for worlds 100-107 may be partly cached from the interrupted gate; worlds 108-115 and everything on the regression/audit suites are new (their prompts changed with the budget).
