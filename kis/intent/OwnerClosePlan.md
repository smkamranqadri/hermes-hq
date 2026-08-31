# Owner-Close Pass (Standard) — planned 2026-08-31

Goal: work finished outside WM runs can become `done` through a sanctioned, audited
path; dep edges can be removed/repointed; the stale draft goal #15 is deleted.
Owner decisions locked in (interview 2026-08-31): close gate = **manual only**;
**UI button + endpoint**; live closes = **#3 #22 #23 #84 #96 #98 #99 #101 #102 #104
#106 #109**; goal #15 = **delete** (guarded tooling).

## Design

- `close_by_owner(task_id, note=None)` (core/wm_store.py): requires status
  `manual`, else ValueError. Writes status `done`, records a state transition
  (`from_status='manual'`, detail `closed by owner: <note>` — this satisfies the
  `check_integrity` done audit), logs activity `task_closed_by_owner`, closes any
  open review as `waived` (so the new orphan-review finding never fires), then
  `promote_dependents(task_id)` and returns the promoted ids.
- `wm task close <id> --by-owner [-c NOTE]` (core/wm_cli.py). Without
  `--by-owner` the command refuses (reserved namespace for future close kinds).
- `POST /api/task/{tid}/close-owner` (backend/writes.py), optional `Note` body,
  mirrors the `/manual` endpoint; returns task detail.
- TaskDetail.tsx: `Close as done` ActionBtn, visible only when `st === 'manual'`,
  confirm text names the audit consequence; no note field (parity with Take over —
  notes go via CLI).
- `remove_task_dep(task_id, dep_id)` + `wm task undepend <id> <dep_id>`: deletes
  the edge, then `_mark_ready_if_deps_done` on the dependent (release-gate
  semantics unchanged — promotion only under a released goal with all deps done).
  Repoint = `undepend` + existing `depend`; no third command.
- `delete_goal(goal_id)` + `wm goal delete <id>`: refuses unless status `draft`
  AND no tasks AND no schedules reference it; logs activity `goal_deleted` with
  the goal title. (Goals lack AUTOINCREMENT; id reuse is acceptable — nothing
  external references goal ids, unlike task/run rows which are NEVER deleted.)

## Acceptance

1. Tests: close happy path (done + transition + activity + review waived +
   dependents promoted); refuse each non-manual status; undepend promotes a
   now-eligible dependent; goal delete guards (non-draft, referenced) + acts.
2. Suite green (108 + new).
3. Live after restart: the 12 closes via CLI with per-task notes; `wm check`
   green before and after; goal #15 deleted; #82/#85/#86 remain manual; #89
   stays `waiting_approval` (deps #82/#85/#86 still gate it).
4. Playwright live :9010, 390×844 `isMobile` + 1440: `Close as done` visible on
   a manual task (#82), absent on a done task and while running; scrollWidth
   390; screenshots reviewed; no page errors.

## Out of scope

Repointing #89 (waits for fresh GitHub/LinkedIn tasks — then: `wm task undepend
89 82` etc. + `depend` on the new ids), SSE stream shield, feedback-from-manual,
closing #82/#85/#86 (their work is genuinely not done).

## Status

PLANNED — awaiting `/kis:act`.
