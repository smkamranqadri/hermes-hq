# State

## Status
**hermes-hq is the live control plane since 2026-08-29 13:40 UTC.** `hermes-hq serve --host 0.0.0.0 --port 9010 --interval 20` with the dispatcher ON (log `/opt/data/hermes-hq-serve.log`, password `/opt/data/hermes-hq/password`). Old WM crons paused (not deleted): `dfe30ff9e8bf` wm-dispatch, `040334fe79ae` wm completion watchdog, `b84db989076d` wm-planning-pickup. Rollback = `hermes cron resume <id>` ×3 and stop hermes-hq; old `/opt/data/work-manager/` untouched. Legacy dashboard :9009 still up but stale.

## Now
Task: **Group 2 Status & unblock** (`kis/intent/Group2Plan.md`) — backend + pages written and unit-tested (17 passed); visual verification in progress.

## Next
After 1b: Group 2 Overview (Needs-you first) + Reviews-in-Tasks + Activity feed.

## Blocker
None.

## Known debt
- Mobile polish pass done 2026-08-29 (2-row top bar, compact sysbar, overflow fixes); re-check each new page at 390px.
- Snapshot-mode writes were discarded by the cutover import (owner's replies on #84/#96 must be redone on the live side if still wanted).
- The server process must be restarted by hand after backend changes (`pkill -f 'hermes-hq serve'` then the serve line above) — no supervisor yet; add a systemd/s6 unit in the install work.
- Cookie session over plain HTTP; HTTPS via reverse proxy is a later item.
- `readers.py` is a straight port (1100 lines) incl. agents/sessions/files/overview readers not yet exposed; prune or expose as Groups 2–5 need them.
- `tests/core/test_t2/t5/t7.py` fail identically in the source repo (goal lifecycle draft→planned changed after they were written). Not caused by the move; fix when touching goal release.

## How to run
See `README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd frontend && npm run dev` (proxies /api to :9010). Legacy WM dashboard still live on :9009 and untouched. Owner drops reference images in `screenshots/` (git-ignored).

## Proof (cutover, 2026-08-29)
- Waited for old run #211 (coder, #98) to finish; old WM then had 0 running runs, #98 `needs_review`, review 84 pending.
- `hermes-hq import --force` → 15/23/98/211/84 rows, 509 paths rewritten, 620 run files, backup `hq.db.pre-import-20260829-134015`.
- Started with `wm_meta.paused=1`, loop `alive: true`; resumed via `POST /api/system/resume`.
- Tick 1 claimed review 84 → run #212 reviewer, pid live, `session_id=20260829_134056_02cd81` captured; verdict changes_requested → #98 rework → run #213 coder launched. `last_error: null`.
