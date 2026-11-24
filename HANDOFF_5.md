# HANDOFF_5: turn on GitHub Pages and finish the repo page

The code is already pushed to https://github.com/Muxie07/gatekeeper (branch `main`).
**Job: enable GitHub Pages from `main` + `/docs`, set the repo description/website/topics, verify the
live dashboard loads, and write `HANDBACK_5.md`.** Do not change any project files or commits.

## Hard rules
1. Do not edit, commit or push any project file. Do not rewrite git history. Do not commit this
   handoff or the handback.
2. Never print or paste the Gemini key or any GitHub token. Do not `cat .env`.
3. Use only GitHub (via `gh` or `git`). No Gemini calls.
4. If `.git/stale-maintenance-lock-from-claude` exists, you may delete it (a leftover, not in use).

## Steps
1. Check the GitHub CLI: `gh --version` and `gh auth status`.
   - If `gh` is missing: install it with `winget install --id GitHub.cli -e`, then reopen the shell.
   - If not logged in: run `gh auth login` and tell the user to complete the browser sign-in
     themselves (do not handle their password). Wait for them, then continue.
2. Enable Pages (if it is already enabled, skip to 3):
   ```
   gh api -X POST repos/Muxie07/gatekeeper/pages -f "source[branch]=main" -f "source[path]=/docs"
   ```
   If that returns 409 (already exists), update it instead:
   ```
   gh api -X PUT repos/Muxie07/gatekeeper/pages -f "source[branch]=main" -f "source[path]=/docs"
   ```
3. Repo page details:
   ```
   gh repo edit Muxie07/gatekeeper --description 'Promotion gate for self-improving LLM agents: placebo-controlled, built at $0 on the Gemini free tier' --homepage "https://muxie07.github.io/gatekeeper/" --add-topic llm --add-topic ai-agents --add-topic evaluation --add-topic gemini --add-topic reward-hacking
   ```
   (Single quotes keep PowerShell from reading `$0` as a variable.)
4. Wait for the build: `gh api repos/Muxie07/gatekeeper/pages` until `"status": "built"` (check
   every 30 s, up to 5 minutes). Then open `https://muxie07.github.io/gatekeeper/` (e.g.
   `curl -s https://muxie07.github.io/gatekeeper/ | findstr /C:"Gatekeeper" /C:"Real model run"`).
5. Write `HANDBACK_5.md`.

## HANDBACK_5.md format
```
# HANDBACK_5
## Status: LIVE | BLOCKED (why)
## Commands run (with exit status; no tokens)
## Pages status JSON (status, html_url, source)
## Live check: did the page contain "Gatekeeper" and "Real model run"?
## Repo description / homepage / topics as now set
## Anything the user must still do
```

## When done
Tell the user: "HANDBACK_5.md is written. Return to the other Claude chat and say: handback 5 ready."
