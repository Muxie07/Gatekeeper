# HANDBACK_8: traceable evidence

## Status: DONE (offline, mock only, zero real calls)

## What changed
- `gk/calllog.py` (new): `CallLog` appends one line to `calls.jsonl` (mock: `calls.mock.jsonl`, every line also `"simulated": true`) for EVERY model request, cache hit or fresh call. `LoggedProvider` is the outermost wrapper on the player and the proposer. Fields: call_id, ts, role, model, cache ("hit"/"miss"), prompt_sha (sha256 of system+prompt; the prompt text itself is not stored), tokens_in/out, latency_ms, http_status, modelVersion, responseId, plus world, suite, harness version, episode id and step for player calls. Failed calls are logged too (with the error type). The API key is never logged.
- `gk/providers.py`: `Completion` gained latency_ms, http_status, model_version, response_id, call_id. `GeminiProvider` now parses `modelVersion` / `responseId` / status / latency from the response. The request body and the cache key formula are untouched (a test asserts the body keys and `generationConfig`, and that the key still equals `sha256([name, model, system, prompt, max_tokens])`). The cache file now also stores a small `meta` object on new lines so a later cache hit can still report the original responseId.
- Episodes carry `call_ids`; the Lab stores per-world scores (and for held: claimed scores and tokens) plus the union of call ids in every experiment record.
- `gk/chain.py` + `Lab.log_experiment`: every record gets `exp_id`, `prev_hash`, `hash` (sha256 of the canonical JSON without "hash"), `prereg_sha` (prereg/PREREG.md) and `code_sha` (gk/env.py + gate.py + evaluator.py + stats.py, line endings normalised). Resume keeps the chain (the Lab reloads the last hash).
- `gk/verify.py`: `python -m gk.verify [experiments.jsonl] [calls.jsonl]` prints PASS/FAIL per check, exit 0/1. Checks: chain link and content hash; every cited call id exists; held-out gain/p, placebo gain/p, regression drop (and parent mean), canary delta, integrity drift, cost growth and audit drop are RECOMPUTED from stored scores and compared with what the gate recorded (pass flags re-derived from the thresholds); decision consistency (accepted iff all seven checks present and passing; rejected iff some check failed); prereg and code hashes match the current files.
- `gk/acceptance.py` / `run_acceptance.py`: ACCEPTANCE.md is built ONLY from records that pass verification (it reports "N of M records verified"), criteria cite experiment ids and a few call ids, and a new criterion 5 "live model decisions" requires a complete real player episode whose every step is `cache: miss` in calls.jsonl (simulated calls never count; **NOT YET** until real data exists).
- `app.py` now also writes `calls.jsonl` / `calls.mock.jsonl`.

## Tests
`python -m unittest discover -s tests` -> **73 tests OK** (63 before; +10 in `tests/test_evidence.py`): hit/miss logging with context and no prompt text; Gemini metadata parsed with request body and cache key unchanged; live-episode rule; mock end-to-end run verifies clean; an edited line breaks the chain (and later records fall); a missing cited call id fails; a doctored score is caught even when the whole chain is rebuilt (recompute mismatch); a changed code/prereg hash is detected; acceptance uses only verified records; resume keeps the chain.
Also ran `python run_acceptance.py --mock` then `python -m gk.verify` on its output: 6 records, 43 checks, 0 failed, VERIFIED.

## Cache check (batch rule)
Report seed 500 step 0 prompt key still present in `cache_gemini_gemini-3.5-flash-lite.jsonl`: **yes**. No prompt, env rule, default harness or max_tokens changed.

## Decisions made without approval
1. Logging at the outermost wrapper (not inside the cache) so one mechanism covers real and mock stacks and records hit vs miss from `Completion.cached`.
2. Call ids are `c<start-time-hex>-<counter>`, unique across resumed processes.
3. Cited call ids per experiment = union of the call ids of the episodes behind the cited suites (including the parent's from earlier rounds) plus the proposer call, so records are large (hundreds of ids) but fully traceable.
4. The code/prereg hash check compares with the CURRENT files, so editing gate/env/evaluator/stats or PREREG.md after a real run makes its records fail verification. That is intended (it is what the hash is for); the real run must not be started and then followed by edits to those files.
5. Train-suite calls and report-suite episodes are logged in calls.jsonl but not cited by experiment records (they are not inputs to a gate decision); the live-episode criterion reads them from the log directly.

## Left undone
- Verification of `experiments.jsonl` files written before this change is not possible (no hashes). The real `experiments.jsonl` has no rows yet, so nothing is affected.
- Per-step latency and live/cached labels in the UI are Handoff 9.
