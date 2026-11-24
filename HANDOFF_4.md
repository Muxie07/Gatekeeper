# HANDOFF_4: prepare the repo for publishing (local only, no push)

Continue the Gatekeeper project. **Job: clean the folder, check for secrets, make a local git commit,
and write `HANDBACK_4.md`. Do NOT create a GitHub repo, do NOT push, do NOT change any code or
results.** The user will review, then push themselves.

## Hard rules
1. Never print, log, commit or paste the API key. Do not `cat .env`. When checking for secrets,
   print only file names and line numbers, never the matching text.
2. No network use at all (no Gemini calls, no `git push`, no `gh`).
3. Do not edit any `.py`, `.json` results, `README.md` or the dashboard, except regenerating the
   dashboard with `python run_demo.py` (step 3).
4. Do not delete anything not listed in step 2.

## Steps
1. `python -m unittest discover -s tests` -> expect 29 OK. If not, stop and report.
2. Delete these leftovers if present: `gatekeeper.zip`, `cache_gemini_gemini-3.5-flash-lite.sqlite`,
   `cache_gemini_gemini-3.5-flash-lite.sqlite-journal`, any `__pycache__/` folders.
3. `python run_demo.py` (now also writes `docs/index.html`, which GitHub Pages can serve).
4. Secret scan over every file that git would commit (respecting `.gitignore`). Look for:
   the literal value of `GEMINI_API_KEY` from `.env` (compare in code; never print it), the pattern
   `AIza[0-9A-Za-z_-]{30,}`, and the strings `api_key=` or `x-goog-api-key` followed by a real value.
   Report only `file:line` for hits. Test files legitimately contain `api_key="TEST"` / `"K"`;
   list those as expected. If a real key is found anywhere: STOP, do not commit, report.
5. `git init` (if not already a repo), `git add .`, then `git status --short`.
   Confirm `.env`, `config.json` and `quota_*.json` are NOT staged. If any is: `git rm --cached` it,
   fix nothing else, and report.
6. `git commit -m "Gatekeeper: promotion gate for self-improving LLM agents"` with these lines at
   the end of the message:
   ```
   Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
   Claude-Session: https://claude.ai/code/session_018sTtMpwuNKQW4W9A4c7z9s
   ```
   If git has no user name/email configured, do NOT set a global one; stop before committing and
   say so in the report.
7. Write `HANDBACK_4.md`.

## HANDBACK_4.md format
```
# HANDBACK_4
## Status: READY TO PUSH | BLOCKED (why)
## Commands run (with exit status)
## Files deleted
## Secret scan results (file:line only; expected test hits listed separately)
## git status --short (before commit), confirming .env / config.json / quota_*.json are absent
## Commit hash and file count
## Repo size (du of the folder excluding .git, and of .git)
## Anything the user must do before pushing
```

## When done
Tell the user: "HANDBACK_4.md is written. Return to the other Claude chat and say: handback 4 ready."
