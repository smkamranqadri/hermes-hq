# State

## Status
Group 1b phases 1–2 (auth + writes + UI) DONE 2026-08-29 on `main`; phase 3 cutover NOT done. Server on :9010 `--no-dispatcher`, login password in `/opt/data/hermes-hq/password`, snapshot-mode banner shown.

## Now
Waiting for the owner to pick a quiet moment for the **cutover runbook** (`kis/intent/Group1Plan.md` §1b phase 3). Precondition: old WM has no `running` task (`cd /opt/data/work-manager && ./wm status`).

## Next
After 1b: Group 2 Overview (Needs-you first) + Reviews-in-Tasks + Activity feed.

## Blocker
None.

## Known debt
- Mobile polish pass done 2026-08-29 (2-row top bar, compact sysbar, overflow fixes); re-check each new page at 390px.
- Writes made in snapshot mode (task #101 smoke, #96 reply) are throwaway — the cutover re-imports with `--force`.
- Cookie session over plain HTTP; HTTPS via reverse proxy is a later item.
- `readers.py` is a straight port (1100 lines) incl. agents/sessions/files/overview readers not yet exposed; prune or expose as Groups 2–5 need them.
- `tests/core/test_t2/t5/t7.py` fail identically in the source repo (goal lifecycle draft→planned changed after they were written). Not caused by the move; fix when touching goal release.

## How to run
See `README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd frontend && npm run dev` (proxies /api to :9010). Legacy WM dashboard still live on :9009 and untouched. Owner drops reference images in `screenshots/` (git-ignored).

## Proof (Group 1b phases 1–2)
- `pytest tests/backend` → 15 passed: 401 gate, 403 without CSRF, login/logout, task create→mark-ready→manual→retry→assign, engine refusals as 409 (dep not done, retry done), feedback refused on planned / accepted on blocked → rework, goal plan/abandon/release rules, project create/patch/archive, pause/resume. Engine t1/t4/t6 still pass after the `OWNER_FEEDBACK_SOURCE_STATUSES` extension.
- Live API walk (bash, `scratchpad/walk.sh`): created #101 → ready → manual → ready; feedback on blocked #96 → `rework`, feedback text stored, activity/transitions rows written; pause/resume reflected in `/api/system.paused`; `hermes-hq wm task show 101` sees it.
- Playwright screenshots (1440 + 390): login, Tasks with + Task, New Task modal, #84 action row + reply modal, project header actions, System controls. Bug caught: hooks-after-early-return crash in TaskDetail (React #310) — fixed.
