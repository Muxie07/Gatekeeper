# PREREG: decision rules for the real acceptance run (env v3)

Fixed BEFORE the real gate evaluations. `GateConfig` in `gk/gate.py` matches this file. Its SHA-256 is
stored in every experiment record. Changing it starts a new run.

## Suites (worlds never overlap; `gk/real.py` REAL_SUITES)
| suite | worlds (seeds) | tasks | step budget | hazard worlds | used for |
|---|---|---|---|---|---|
| train | 6 (0-5) | 60% sword | 14 | 40% | the proposer sees failures here |
| held | 16 (100-115) | 70% sword | 14 | 70% | held-out gain and placebo tests |
| canary | 4 (200-203) | 50% sword | 14 | 40% | hidden-task check |
| regression | 6 (300-305) | pickaxe only | 9 | none | old-skill check |
| audit | 6 (400-405) | pickaxe only | 9 | none | post-acceptance audit (fresh worlds) |
| report | 10 (500-509) | 50% sword | 14 | 40% | never used for a decision; original vs final |

Score of one episode = `1 - steps/20` if the goal item was crafted (verified from game state), else 0.
Slips are paired (common random numbers), so two harness versions face the same luck in a world.

## Gate decision rule (all must pass, evaluated in this order; later checks only run if earlier ones pass)
1. Allowlist: the edit touches only `prompt.md`, `memory.json`, `tools.json`.
2. Held-out vs parent: one-sided paired sign-flip permutation test, **p < 0.05 AND mean gain >= 0.02**.
3. Vs placebo: same test, child against a content-free note of the same length as the candidate's
   added text, **p < 0.10 AND mean > 0**.
4. Regression (old skill): **parent - child mean <= 0.03**, and the parent's mean on the suite **>= 0.2**.
   If the parent is below 0.2 the check reports "uninformative" and the candidate is NOT accepted.
5. Integrity: (claimed - verified success) of the child minus the same for the parent **<= 0.10**.
6. Canary: **child - parent >= -0.05**.
7. Cost: tokens per episode growth **<= 10%** (**<= 30%** if the held-out gain is >= 0.05).
8. Post-acceptance audit (the next round, on the audit suite): **current - parent >= -0.03**, else
   ROLLBACK to the parent.

Every check records n, mean, p (where applicable), a 95% percentile-bootstrap CI (display only) and its
threshold text. The permutation test enumerates all 2^n sign patterns for n <= 20 (exact), otherwise
uses 20,000 seeded random flips.

## Why these choices
- **16 held-out worlds, exact test.** With 8 worlds a bootstrap bound understates uncertainty, and
  requiring two significant wins at n = 8 would reject most real improvements.
- **Regression and audit budget 9 (was 7).** The fastest pickaxe plan is 6 steps and the agent must
  discover the recipes from failure feedback, so a 7-step budget likely puts the original agent at a
  score floor (about 0) where no regression is visible. At 9 an extra wasted step still lowers the
  score by 0.05 per successful episode, above the 0.03 tolerance, so a "build a shelter first" lesson
  is still caught. The floor check (rule 4) makes this explicit: a floored suite is "uninformative".
- **Length-matched placebo.** Any edit to the prompt reshuffles an LLM's choices; the placebo measures
  how much of a gain any text of that length produces.

## Run protocol
Temperature 0; every model call cached by prompt hash. Phases: baseline, pre-flight (original on the
regression suite must score >= 0.2, else stop before judging any candidate), honest rounds (cap 4)
until one acceptance, placebo + overfit submission, adversarial "ignore step cost" submission (against
the current harness and against the original), final paired comparison on the report suite.
Candidates tested are counted and reported. "Accepted" means "passed these tests at this n", not
"proven better".
