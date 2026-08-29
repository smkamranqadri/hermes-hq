# Group 1 — Work core (approved 2026-08-29)

Decisions: snapshot the live WM now and cut over at the end of Group 1 (not before); reads before writes; Tasks page = list by default grouped by human state, board optional, newest first.

## 1a Read — ACTIVE
Scope
- `hermes-hq import <wm-dir>`: copy `wm.db` → `hq.db`, copy `runs/` minus `runs/worktrees/`, rewrite `/opt/data/work-manager/` prefixes in `runs.brief_path`, `runs.result_paths`, `runs.workdir`. Idempotent; refuses non-empty `hq.db` without `--force`. Never edits data.
- `hermes_hq/status.py`: engine status → human state + reason (single source; UI `status.ts` mirrors it, test asserts agreement).
- `hermes_hq/readers.py`: port from `../hermes-work-manager/wm-tool/wm_dash/reader.py`, read-only.
- API: `GET /api/projects?archived`, `/api/project/{slug}`, `/api/goals`, `/api/tasks` (project, state, q, limit, offset; `updated_at DESC`; envelope `{tasks,total,stateCounts,stateOptions,limit,offset}` per the Tasks-tab spec with human states), `/api/task/{id}`.
- UI: Projects list, Project detail, Tasks list (Needs you → Working → Queued → Backlog → Done) + board toggle + project/state/search, Task detail (badge+reason, description/DoD, runs, reviews, transitions, session link or "not mapped yet").

Acceptance
- Import of live dir → 15 projects / 23 goals / 98 tasks / 191 runs / 73 reviews, zero old path prefixes; re-run refuses without `--force`.
- `/api/tasks` default = active projects only, newest first; `state=needsyou` matches CLI blocked/failed/stalled/owner-approval set.
- #98 → Working · "session not mapped yet"; #84 → Needs you · blocked.
- Screenshots of all four pages at 1440 + 390.

Out of scope: writes, Overview, Agents, Chat, Files, cron/cutover, data cleanup.

## 1b Write — NEXT
Create project, create task, goal create/plan/release, mark-ready, approve, owner feedback → rework, retry failed, dispatcher pause/resume. Then cutover: fresh re-import, disable old crons (`wm-dispatch`, `wm completion watchdog`, `wm-planning-pickup`), enable new dispatcher.
