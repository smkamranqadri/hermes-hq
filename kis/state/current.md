# State

## Status
**hermes-hq is the live control plane since 2026-08-29 13:40 UTC.** `hermes-hq serve --host 0.0.0.0 --port 9010 --interval 20` with the dispatcher ON (log `/opt/data/hermes-hq-serve.log`, password `/opt/data/hermes-hq/password`). Old WM crons paused (not deleted): `dfe30ff9e8bf` wm-dispatch, `040334fe79ae` wm completion watchdog, `b84db989076d` wm-planning-pickup. Rollback = `hermes cron resume <id>` ×3 and stop hermes-hq; old `/opt/data/work-manager/` untouched. Legacy dashboard :9009 still up but stale.

## Now
Task: **Group 3a** (plan approved 2026-08-29, `kis/intent/Group3Plan.md`). Not started. Order: stop-run → templates extract → agents API → gateway supervisor → Agents UI.
Verification: pytest (stop on fake wrapper, install on scratch HERMES_HOME, gateway supervisor with fake process), real gateway start for coder on this box, Playwright.

## Next
Group 3b Chat (SSE proxy + Chat page), then Group 4 direct chat scopes.

## Blocker
None.

## Known debt
- No "stop a running run" operation in engine or API yet (see Next).
- `runs/<id>.log` holds only the wrapper's lines; agent transcript is in the Hermes session → Group 3 chat view is where "watch it work" really lands.
- Re-check every new page at 390px; `html/body` have an overflow-x guard but root causes (missing `min-w-0`) must still be fixed per page.
- Snapshot-mode writes were discarded by the cutover import (owner's replies on #84/#96 must be redone on the live side if still wanted).
- The server process must be restarted by hand after backend changes (`pkill -f 'hermes-hq serve'` then the serve line above) — no supervisor yet; add a systemd/s6 unit in the install work.
- Cookie session over plain HTTP; HTTPS via reverse proxy is a later item.
- `readers.py` is a straight port (1100 lines) incl. agents/sessions/files/overview readers not yet exposed; prune or expose as Groups 2–5 need them.
- `tests/core/test_t2/t5/t7.py` fail identically in the source repo (goal lifecycle draft→planned changed after they were written). Not caused by the move; fix when touching goal release.

## How to run
See `README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd frontend && npm run dev` (proxies /api to :9010). Legacy WM dashboard still live on :9009 and untouched. Owner drops reference images in `screenshots/` (git-ignored).

## Proof (latest)
- Cutover 2026-08-29 13:40: `import --force` (15/23/98/211/84 rows, backup kept); first tick claimed review 84 → run #212 reviewer with session id captured → rework → coder run launched; `last_error: null`. Old worktrees symlinked (49) after a false rejection traced to uncopied worktree paths.
- Group 2: `pytest tests/backend` 17 passed (overview stats, unified activity + paging + task filter, run-log incremental tail). Playwright 1440/390: Overview tiles + Needs-you + Working/Queued + feed, Activity page, Task detail log; `scrollWidth == 390` on /, /activity, project and task detail after the overflow fix.
- Stop #98: engine calls `set_paused → mark_stalled(215) → mark_manual(98) → set_project_archived(wm-dashboard) → set_paused(False)`; afterwards 0 running runs, 0 open reviews, no coder processes.
