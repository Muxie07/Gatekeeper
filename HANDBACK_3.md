# HANDBACK_3
## Status: COMPLETE

## Commands run (with exit status)
1. `python -m unittest discover -s tests` -> exit 0 (29 OK)
2. Quota check (flash-lite 346 used at start)
3. `python run_real_loop.py config.json --rounds 1` -> exit 0
4. `python analyze_real.py config.json` -> first call showed exit -1 only because I piped it into `Select-Object -First 3` (broken pipe); rerun without the pipe -> exit 0, `real_analysis.json` written
5. `python run_demo.py` -> exit 0, dashboard regenerated
6. Extra, read-only: `placebo_eps.py` (scratchpad, replays cached calls only, cannot call the API) -> exit 0, used for the per-episode table below

## Console output (verbatim, relevant lines, no secrets)
```
[baseline] success 1.00  steps 9.20  score 0.540  tokens 1627
round 1 [placebo] content-free lesson vs parent: mean +0.045, lower bound -0.010
round 1 [llm] reject  no significant held-out gain (mean +0.050, lower bound -0.000) | Craft a sword early to defeat zombies before they steal your collected
[after round 1] success 1.00  steps 9.20  score 0.540  tokens 1627
Done. Details in real_run.json
```
The baseline line printed from cache as expected.

## Placebo vs parent (mean, lower bound) and lesson vs parent (mean, lower bound)
| Comparison | mean gain | lower bound |
|---|---|---|
| placebo vs parent | +0.045 | -0.010 |
| lesson vs parent | +0.050 | -0.000 |

Gate decision for the lesson: reject, "no significant held-out gain (mean +0.050, lower bound -0.000)". The lesson was rejected on the existing no-significant-gain criterion; the log gives no separate beat-the-placebo reason.

## Was the lesson identical to run 2's round-1 lesson? (yes/no + text)
Yes. "Craft a sword early to defeat zombies before they steal your collected resources." Same gain (+0.050) and lower bound (-0.000) as in run 2.

## Per-episode steps on held-out: parent / placebo / lesson (from cache)
| seed | task | parent | placebo | lesson |
|---|---|---|---|---|
| 100 | sword | 13 | 11 | 11 |
| 101 | sword | 10 | 10 | 10 |
| 102 | sword | 11 | 11 | 11 |
| 103 | sword | 10 | 10 | 10 |
| 104 | pickaxe | 7 | 9 | 8 |
| 105 | sword | 11 | 11 | 11 |
| 106 | pickaxe | 6 | 6 | 6 |
| 107 | sword | 13 | 12 | 12 |
| 108 | sword | 10 | 10 | 10 |
| 109 | sword | 13 | 12 | 12 |

Totals: parent mean_steps 10.4, success 9/10, no_materials 12. Placebo mean_steps 10.2, success 10/10, no_materials 10. Lesson mean_steps 10.1, success 10/10, no_materials 9.
Lesson and placebo have the same step counts in 9 of 10 episodes, but identical full action traces in only 4 of 10.

Placebo text as stored (the log shows it truncated): "Keep this note in mind while you play the game. Keep this note in mind while you play the ..."

## Quota used (start and end, per model), date 2026-11-24
- gemini-3.5-flash-lite: 346 -> 393 (47 requests)
- gemini-3.6-flash: 5 -> 5
- gemini-3.8-flash: 20 -> 20

## Problems, and code changes (or "none")
- Problems: none. No retries or quota stops.
- Code changes: none.

## Your read: does the placebo explain run 2's gain?
- Mostly yes. A content-free note gives +0.045 against the lesson's +0.050, so it reproduces about 90% of the gain. Per episode, the placebo gets the same savings as the lesson in 9 of 10 episodes (seeds 100, 107, 109 each save 1-2 steps) and is worse than the lesson only on seed 104. This supports the hypothesis that extra text in memory, not its content, drives most of the effect.
- Caveat: n=10 worlds and both lower bounds are about zero (-0.010 and -0.000), so neither is significant alone. The data cannot separate +0.045 from +0.050, and a small content effect cannot be ruled out. The lesson's no_materials count (9) is lower than the placebo's (10), but that's one event.
- The lesson and placebo actions differ in 6 of 10 episodes while ending with nearly the same step counts, so the content changes behaviour a little without changing the outcome.
- Implication: the gate's placebo check is doing its job here. A lesson like this one should not be credited with a gain that a blank note matches.
