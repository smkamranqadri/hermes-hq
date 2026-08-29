# State

## Status
Group 1a Read DONE 2026-08-29 on `main`. Server on :9010 (`--no-dispatcher`) over an imported snapshot of the live WM at `/opt/data/hermes-hq/hq.db` (imported 2026-08-29 ~11:54; live WM keeps changing).

## Now
Task: **Group 1b Write + cutover** (plan approved 2026-08-29, `kis/intent/Group1Plan.md`). Not started. Order: auth → write API + tests → UI → screenshots → cutover runbook with owner.
Verification: pytest auth/writes on scratch DB, manual walk on :9010 (snapshot mode), headless screenshots, live cutover checks.

## Next
After 1b: Group 2 Overview (Needs-you first) + Reviews-in-Tasks + Activity feed.

## Blocker
None.

## Known debt
- Task detail action button is a disabled placeholder until 1b.
- `readers.py` is a straight port (1100 lines) incl. agents/sessions/files/overview readers not yet exposed; prune or expose as Groups 2–5 need them.
- `tests/core/test_t2/t5/t7.py` fail identically in the source repo (goal lifecycle draft→planned changed after they were written). Not caused by the move; fix when touching goal release.
- Mobile top bar stacks into 3 rows (brand / sysbar / tabs); acceptable for now, revisit with Overview.

## How to run
See `README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd frontend && npm run dev` (proxies /api to :9010). Legacy WM dashboard still live on :9009 and untouched. Owner drops reference images in `screenshots/` (git-ignored).

## Proof (Group 1a)
- `hermes-hq import /opt/data/work-manager` → 15 projects / 23 goals / 98 tasks / 193 runs / 75 reviews (live had moved past the plan's 191/73), 447 path values rewritten, 566 run files copied, 0 old-prefix values left in path columns; re-run without `--force` refused; `--force` kept a `.pre-import-*` backup.
- `pytest tests/backend` → 10 passed (status mapping incl. UI-mirror check, tasks envelope/newest-first/state filter/search/paging, task detail + deps, project detail human states, 404s).
- Live API: `/api/tasks` → 60 active-project tasks, stateCounts {done 45, backlog 11, needsyou 2, queued 1, working 1}; #84 → Needs you · blocked with run error; #89 → Queued · waiting on #82,#84,#85,#86; #98 → Working · reviewer checking.
- Screenshots 1440px: Projects, Tasks list, Tasks board, Project detail (Needs-you strip + tabs), Task detail (deps, runs w/ session id, reviews, history); 390px Tasks + Task detail. Bugs caught by screenshots and fixed: badge overflow on long reasons, project-detail tasks all "Backlog", deps ids missing, board column collapse.
