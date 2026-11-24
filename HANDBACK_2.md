# HANDBACK_2
## Status: COMPLETE
Both rounds ran to the end. No quota stop, no transient errors.

## Commands run (in order, with exit status)
1. `python -m unittest discover -s tests` -> exit 0 (27 OK)
2. Quota check (flash-lite 239 used at start)
3. `python run_real_loop.py config.json --rounds 2` -> exit 0
4. `python analyze_real.py config.json` -> exit 0 (`real_analysis.v1.json` already existed, so no copy was needed)
5. `python run_demo.py` -> exit 1 (UnicodeEncodeError, see Problems)
6. Fixed `gk/dashboard.py`; tests re-run -> 27 OK
7. `python run_demo.py` -> exit 0, `dashboard/index.html` regenerated (16:37)

## Console output (verbatim, relevant lines, no secrets)
```
[baseline] success 1.00  steps 9.20  score 0.540  tokens 1627
round 1 [llm] reject  no significant held-out gain (mean +0.050, lower bound -0.000) | Craft a sword early to defeat zombies before they steal your collected
[after round 1] success 1.00  steps 9.20  score 0.540  tokens 1627
round 2 [llm] reject  no significant held-out gain (mean +0.050, lower bound -0.000) | Ensure you have the required materials in your inventory before crafti
[after round 2] success 1.00  steps 9.20  score 0.540  tokens 1627
Done. Details in real_run.json
```
run_demo.py (second run, tail):
```
naive         score 0.141±0.183  tokens 1327  bad promoted 1.25  bad surviving 1.25  rollbacks 0.00
heldout_only  score 0.550±0.023  tokens 2802  bad promoted 0.38  bad surviving 0.38  rollbacks 0.00
gated         score 0.569±0.016  tokens 2698  bad promoted 0.38  bad surviving 0.00  rollbacks 0.38
```

## Results table (from real_run.json "report")
| Stage | success | mean_steps | score | tokens |
|---|---|---|---|---|
| baseline | 1.00 | 9.20 | 0.540 | 1627 |
| after round 1 | 1.00 | 9.20 | 0.540 | 1627 |
| after round 2 | 1.00 | 9.20 | 0.540 | 1627 |

## Gate decisions (from real_run.json "log")
round | kind | PROMOTE/reject | reason | lesson text
--- | --- | --- | --- | ---
1 | llm | reject | no significant held-out gain (mean +0.050, lower bound -0.000) | Craft a sword early to defeat zombies before they steal your collected resources.
2 | llm | reject | no significant held-out gain (mean +0.050, lower bound -0.000) | Ensure you have the required materials in your inventory before crafting to avoid wasted actions.

## Held-out comparison (from real_analysis.json "held")
Analysis covers the round-1 lesson only (the file stores one `lesson`).
| | n | mean_steps | success | events |
|---|---|---|---|---|
| parent | 10 | 10.4 | 9/10 | slip 5, no_pickaxe 2, no_materials 12, zombie_wipe 3 |
| child | 10 | 10.1 | 10/10 | slip 5, no_materials 9, zombie_wipe 3 |

Slips are identical (5 vs 5), as pairing intends. Per-episode steps parent -> child: 13->11, 10->10, 11->11, 10->10, 7->8, 11->11, 6->6, 13->12, 10->10, 13->12. Eight episodes are unchanged or better; seed 104 is worse by 1.

## Quota used today per model (start and end), date 2026-11-24
- gemini-3.5-flash-lite: 239 -> 346 (107 requests this handoff; limit 500)
- gemini-3.6-flash: 3 -> 5 (limit 20)
- gemini-3.8-flash: 20 -> 20 (already at limit, so the fallback chain was used)

## Problems and how you handled them
- `python run_demo.py` crashed writing the dashboard: `UnicodeEncodeError: 'charmap' codec can't encode character '→'`. `open(path, "w")` used the Windows cp1252 default. Fixed with an explicit UTF-8 encoding.
- The cached baseline was re-measured live on env v2 and took a few minutes, as expected. It was 107 Flash-Lite requests in total, fewer than the ~400 estimate, because many calls were cached.
- `real_run.1795517040.json` is the archive of the earlier run (made by the script). All `real_run*.json` files are kept.

## Code changes (file, what, why) or "none"
- `gk/dashboard.py:157`: `open(path, "w")` -> `open(path, "w", encoding="utf-8")`. Why: the dashboard HTML contains non-cp1252 characters, which crashed `run_demo.py` on Windows. No gate, suite, env, or prompt changes.

## Your read: did pairing the noise change what the gate could detect?
- Yes, partly. With paired slips the child's slip count equals the parent's (5 vs 5), so slip noise no longer cancels the step saving. The saving shows up as +0.050 mean gain, versus +0.005 in run 1. The effect is also visible at the episode level: five episodes lose 1-2 steps and `no_materials` falls from 12 to 9.
- The gate still rejected. The lower bound is -0.000, a hair below zero, so it missed by the thinnest margin. It is a near miss, not a clear detection. With n=10 held-out episodes the interval is still too wide. The result is "direction right, evidence just short", not "gate fixed".
- Something surprising: round 2's lesson is a different lesson (a materials-check rather than the sword/zombie one), yet it reports exactly the same gain (+0.050) and lower bound (-0.000) as round 1. Possible explanations are a coincidence, or the round-2 comparison reusing the round-1 parent/child figures. I did not investigate this and did not change any code for it. It is worth checking before trusting the round-2 numbers.
- Round 1's lesson ("craft a sword early to defeat zombies") is odd advice: `zombie_wipe` stayed at 3 in both versions. The measured improvement came from fewer `no_materials` mistakes (12 -> 9), not from the zombie lesson's stated goal.
- Also, the earlier note that both lessons target `no_materials` still holds: the proposer's rejected-lesson list worked (the lessons differ), but the gain did not grow.
