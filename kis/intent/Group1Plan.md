# Group 1 — Work core (approved 2026-08-29)

Decisions: snapshot the live WM now and cut over at the end of Group 1 (not before); reads before writes; Tasks page = list by default grouped by human state, board optional, newest first.

## 1a Read — DONE 2026-08-29 (see State for proof)
Scope
- `hermes-hq import <wm-dir>`: copy `wm.db` → `hq.db`, copy `runs/` minus `runs/worktrees/`, rewrite `/opt/data/work-manager/` prefixes in `runs.brief_path`, `runs.result_paths`, `runs.workdir`. Idempotent; refuses non-empty `hq.db` without `--force`. Never edits data.
- `backend/status.py`: engine status → human state + reason (single source; UI `status.ts` mirrors it, test asserts agreement).
- `backend/readers.py`: port from `../hermes-work-manager/wm-tool/wm_dash/reader.py`, read-only.
- API: `GET /api/projects?archived`, `/api/project/{slug}`, `/api/goals`, `/api/tasks` (project, state, q, limit, offset; `updated_at DESC`; envelope `{tasks,total,stateCounts,stateOptions,limit,offset}` per the Tasks-tab spec with human states), `/api/task/{id}`.
- UI: Projects list, Project detail, Tasks list (Needs you → Working → Queued → Backlog → Done) + board toggle + project/state/search, Task detail (badge+reason, description/DoD, runs, reviews, transitions, session link or "not mapped yet").

Acceptance
- Import of live dir → 15 projects / 23 goals / 98 tasks / 191 runs / 73 reviews, zero old path prefixes; re-run refuses without `--force`.
- `/api/tasks` default = active projects only, newest first; `state=needsyou` matches CLI blocked/failed/stalled/owner-approval set.
- #98 → Working · "session not mapped yet"; #84 → Needs you · blocked.
- Screenshots of all four pages at 1440 + 390.

Out of scope: writes, Overview, Agents, Chat, Files, cron/cutover, data cleanup.

## 1b Write + cutover — ACTIVE (approved 2026-08-29)
Decisions: password auth ships before any write is exposed; "unblock" for `blocked` = Reply→rework (owner feedback threaded into the next brief), Retry/Take-over secondary; failed/stalled → Retry primary; cutover only at a quiet moment with the owner present.

Phases
1. **Writes + auth (backend)** — `backend/auth.py`: `HERMES_HQ_PASSWORD` or generated (printed on serve + `$HERMES_HQ_HOME/password` 0600), `POST /api/login|logout`, signed cookie session, CSRF header on mutating requests, all `/api/*` behind login. Write routes, each a thin call into `core.wm_store`:
   `POST /api/projects`, `/api/project/{slug}` (name/desc), `/api/project/{slug}/archive`;
   `POST /api/goals`, `/api/goal/{id}/plan|planned|release|abandon`;
   `POST /api/tasks` (project, title, desc, DoD, assignee, goal, review_policy, is_code, deps), `/api/task/{id}/mark-ready|feedback|retry|manual|assign`;
   `POST /api/system/pause|resume|dispatch`.
2. **UI** — login screen; New Project / New Task modals; goal card actions by status; Task detail action row from `human.action`; SYSTEM pause/resume/dispatch-now; toast + refetch after each write; confirms on release/retry/manual/pause; "snapshot mode" banner until cutover.
3. **Cutover runbook** (owner present):
   1. Old WM: `cd /opt/data/work-manager && ./wm status` → no `running` tasks; wait otherwise.
   2. `hermes-hq import /opt/data/work-manager --force` (backup kept).
   3. Disable Hermes crons `wm-dispatch`, `wm completion watchdog`, `wm-planning-pickup` (`hermes cron list` → pause/delete); note their ids for rollback.
   4. Restart `hermes-hq serve` with dispatcher enabled but paused (`wm_meta.paused=1`).
   5. Unpause from SYSTEM; watch first tick in `/api/system.dispatcher`; dispatch one small real task; confirm its run gets a session id.
   6. Rollback = re-enable the crons; old WM untouched on disk.

Acceptance
- 401 without login; 200 after; POST without CSRF → 403.
- UI-created project+task visible via `hermes-hq wm task show`; mark-ready→ready; feedback on a blocked task→rework with text stored; retry→ready; manual→manual; pause→tick reports paused. All writes appear in activity + transitions.
- Screenshots: login, New Task modal, blocked Task detail action row, SYSTEM controls.
- Cutover: crons disabled, dispatcher alive, real task dispatched with session id on the new side.

Out of scope: Overview, agents page, chat, goal edit, project delete, multi-user.
