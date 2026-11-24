# HANDBACK_9: live UI that proves it is live

## Status: DONE (offline, mock only, zero real calls)

## What changed
- **LIVE / CACHED / SIMULATED per step** (`web/index.html`, `app.py`): every step row and the game panel show a tag taken from the call log fields on the step event: `LIVE · <latency> ms` (fresh call), `CACHED`, or `SIMULATED` (mock; never labelled LIVE), plus the call id.
- **`POST /play` / "Start a new game"**: plays ONE episode with the current harness (the loop's current version, or the original if no run yet) on a fresh demo world, seed `900000 + counter` (counter persisted in `demo_counter.json`, so a restart never reuses a world; these seeds belong to no suite). In real mode the demo player is the player WITHOUT its cache (Logged → Limited → Metered → Gemini), so every step is a live, rate-limited, ledger-counted call; the cache cannot answer them. (Note: cache keys depend on the prompt text only, and step-0 prompts do not mention the world, so a cached player could have served "fresh" worlds from cache. That is why the cache is bypassed.) Refused with a clear error if: real mode without the no-billing confirmation (400), no key (400), quota used up (429, with the IST resume time), or the improvement loop is running / another demo game is playing (409). Works in mock mode.
- **Gate panel**: every check shows value, n, mean, p, 95% CI and the threshold text, appears in order as the events arrive (green/red), unrun checks say "not run", then a verdict box: ACCEPTED / REJECTED / ROLLED BACK. A candidate bar sits beside a placebo bar (placebo gain over the parent = candidate gain minus candidate-vs-placebo).
- **Version tree**: built from the event stream (works live and in replay), status colours, click a node to see its lesson, diff, checks and decision; live mode also fetches its experiment record from the new `GET /experiments?child=<version>` (exp id, hash, prev hash, proposer model, requests, call count).
- **`events.jsonl`** (mock: `events.mock.jsonl`): every event the page receives is appended. **Replay**: `/?replay=<file>` (+ `&speed=instant`) plays a recorded file back with a permanent REPLAY badge, controls hidden, and a banner saying nothing is live; `GET /replays` lists recordings (whitelist: only `events*.jsonl` in the work folder can be read), `GET /replay?file=` serves one, and the History tab links to them. If the server route does not exist (static hosting) the page falls back to fetching the given relative file, so it can later run on GitHub Pages.
- **Export**: `python -m gk.export_replay events.jsonl docs/replay.json [--all]` writes `{label, events, modes, simulated}` for the last run; it refuses to write `docs/index.html` and I did NOT touch `docs/`.
- **Local only**: the server binds `127.0.0.1` (`serve()` raises for any other host). Every request must have a local `Host` header (blocks DNS-rebinding) and any `Origin` must be local; otherwise 403, for GET, `/control` and `/play`.
- Also: `.gitignore` gets `demo_counter.json`; `ui_check/` and `events.mock.jsonl` were already ignored.

## Tests
`python -m unittest discover -s tests` -> **82 tests OK** (73 before; +9 in `tests/test_ui.py`, all over real HTTP, mock mode; real mode only for refusal paths, no calls): step events carry call_id/latency/cached/mode and the page contains the LIVE/CACHED/SIMULATED/REPLAY strings; `/play` plays a fresh demo game and the seed advances; `/play` is refused while the loop runs; real-mode refusals (no confirmation, quota used up with a temp quota file, nothing logged = nothing called); the demo player is uncached but rate-limited; foreign Host/Origin refused, local accepted, non-local bind refused; events file recorded and served by `/replay` (whitelist blocks `../app.py` and `config.json`); export produces a valid static file and refuses `docs/index.html`; the experiment endpoint returns the record behind a node; gate-check events carry n/mean/p/ci/threshold. Page JavaScript passes `node --check`.

## Visual check (done)
Edge was already installed, so I used its headless screenshot (no new dependency) on a MOCK run served from a temp folder, in replay mode (the live page's open event stream never lets a headless page "finish", so I could not screenshot the live page itself; the replay page runs the same rendering code). Screenshots are in `ui_check/` (git-ignored): `replay_desktop.png`, `replay_narrow.png`, `replay_phone.png`. Bugs found and fixed: (1) live controls still visible in replay mode (an inline style beat the `hidden` class); (2) version-tree rows clipped instead of wrapping; (3) quota panel showed a bare dash in replay; (4) possible horizontal overflow on narrow screens (grid minimum width and long unbreakable strings) now wraps. Not checked visually: the live (non-replay) page with its Start/Pause buttons enabled, the History tab, and light mode.

## Cache check (batch rule)
Report seed 500 step 0 cache key present: **yes**. No prompt/env/default-harness/max_tokens change.

## Decisions made without approval
1. `/play` refuses while the improvement loop is "running" (shared rate limiter and ledger would race) but is allowed when idle, paused, finished or stopped.
2. Demo games use hazard probability 0.5, 14 steps, task from `task_for(seed, 0.5)`; they are not experiments and write nothing to `experiments.jsonl` (but their calls ARE in `calls.jsonl` and count against the ledger).
3. A static-hosting replay is a single JSON file; the live server records JSON lines. The page accepts both.
4. The tree is derived from events rather than `/state`, so replay needs no server.

## Left undone
- The live page has not been screenshotted (see above); the real-mode UI path has been exercised only through its refusal branches because real calls are off limits in this batch.
- Nothing was exported to `docs/` (per the handoff).
