---
name: hermes-hq-ops
description: Operate Hermes HQ managed projects — goals, tasks, dispatch, review gates — via the hermes-hq CLI and API.
version: 1.0.0
author: hermes-hq
license: MIT
metadata:
  hermes:
    tags: [hermes-hq, orchestration, dispatch, release-gate, review, tasks]
    related_skills: [hermes-multiagent-coordination]
---

# Hermes HQ Ops

## When to Use
Use whenever work is planned, registered, approved, released, dispatched, or reviewed through Hermes HQ managed projects — "register a project", "release/approve a goal or task", "set up a task graph", "why is task #N blocked", or reading status from the store. For new owner work, load `orchestrator-intake` first so the owner is interviewed before any task or goal is created.

## What Hermes HQ is
Hermes HQ is the control plane for a Hermes multi-agent team: one SQLite store + a web UI/API + a built-in single-flight dispatcher + an independent Reviewer gate, all in one supervised service.

- **Home:** `$HERMES_HQ_HOME`, default `<HERMES_HOME>/hermes-hq` (holds `hq.db`, `runs/`, `password`). Never hand-edit this directory.
- **Server:** `hermes-hq serve` — UI + API + dispatcher in one process (default `127.0.0.1:9010`; the dispatcher ticks on `--interval` seconds, no cron needed).
- **Service:** `hermes-hq service install|status|restart|update|auto-update` integrates with the box's supervisor (detect it — s6, systemd, launchd; never assume). Restart after backend changes: `hermes-hq service restart`. Frontend builds go live without a restart.
- **CLI:** `hermes-hq wm <args>` — the engine CLI, bound to `hq.db`.
- **Auth:** password file `<hq home>/password` (or `$HERMES_HQ_PASSWORD`); `POST /api/login {password}` → cookie + `csrf` token; mutations need the `x-csrf` header.
- **Code:** the hermes-hq checkout (this box: `/opt/data/projects/hermes-hq`). Edit/test there, commit, then `hermes-hq service restart`.

## ONE store — the honesty rule
`hq.db` is the ONLY authoritative task store. The owner's dashboard reads it; your answers must come from it (`hermes-hq wm status`, `wm task show`, or the `:9010` API). Never operate a second store, a leftover legacy CLI, or another dashboard port — a diverged store makes your reports contradict what the owner sees, which is worse than no report. Read with the CLI first; raw DB access is read-only (`file:...hq.db?mode=ro`) and only when the CLI can't express the query. Never `UPDATE` task/review/run rows directly except sanctioned registration housekeeping (fixing `goal_id`, `is_code`, `review_policy` right after create).

## CLI essentials
```
hermes-hq wm project create <slug> --name <name> --path <path> [--description]
hermes-hq wm goal create <slug> <title> [desc] [acceptance_criteria]
hermes-hq wm goal release <goal_id>      # approval gate: goal's tasks -> waiting_approval/ready
hermes-hq wm task create <slug> <title> [desc] [dod] --assignee <agent> --goal <goal_id> [--is-code] [--review-policy none|required|optional]
hermes-hq wm task depend <id> <depends_on_id>
hermes-hq wm task mark-ready <id>        # approval gate for a single task
hermes-hq wm task list --project <slug> --status <s> | hermes-hq wm task show <id>
hermes-hq wm task assign <id> <agent>
hermes-hq wm review <id> --verdict approved|changes_requested [-c comment] | --waive
hermes-hq wm dispatch                    # run a dispatch tick NOW (server ticks on its own)
hermes-hq wm status                      # authoritative grouped readout
hermes-hq wm pause | resume | retry <id> | mark manual <id> [note]
hermes-hq wm backup | check | prune --days N
```

## Statuses, human states, and gates (invariants — never bypass)
- Engine statuses: `planned, waiting_approval, ready, running, needs_review, rework, done, failed, stalled, blocked, manual`.
- The UI derives human states at read time (`backend/status.py`): blocked/failed/stalled → **Needs you**; running/needs_review → Working; ready/rework/waiting_approval → Queued; planned/draft → Backlog; done/manual → Done.
- **`planned` NEVER auto-runs.** Only `goal release` (whole goal) or `task mark-ready` (single task) makes work eligible. The dispatcher promotes only `waiting_approval → ready` as deps finish.
- **Planning ≠ authorization.** Design the graph, present it, WAIT for explicit approval to register; register-approval is NOT execution-approval — hold again until told to release. Release phases one at a time in dependency order.
- **Completion contract:** a run counts only when the agent writes valid `runs/<id>.completion.json` (`completed=done|blocked|failed`). Exit code ≠ completion.
- **Reviewer verdict is authoritative.** `review_policy required|optional` auto-creates a review on completion; `changes_requested` → `rework` → re-dispatch → re-review. Never waive a `required` review.
- Code tasks (`--is-code`) run in per-run git worktrees (`wm/run-<id>` branches); non-code work in the project `primary_path`.
- Registration gotchas: always pass `--goal <id>` (a `goal_id=NULL` task stays `planned` forever); no `--weight` flag (encode `W<n>` in the description); avoid backticks/`$(` in descriptions created via shell.

## Blocked means the OWNER is needed — do not retry-loop
When a run completes `blocked`, read its `error` before anything else (`hermes-hq wm task show <id>` / run row). If the blocker is a missing capability of the environment — deploy credentials, repo push auth, an external approval — **retrying cannot succeed**: the same agent will re-run, re-block, and burn a paid run each time. Report the exact blocker to the owner with the choices (provide the credential, do the step themselves then `mark manual <id>`, or descope). Retry only after the environment actually changed. A task the owner completed out-of-band is closed with `hermes-hq wm mark manual <id> "<note>"` — never by editing the DB.

## Execution discipline (the Orchestrator coordinates, agents implement)
1. Release the approved goal (`goal release`) or `task mark-ready`.
2. Let the dispatcher claim ready tasks and spawn the assignee profiles; `hermes-hq wm dispatch` only forces an immediate tick.
3. Monitor via `wm status` / `wm task show` — the dispatcher also runs between your turns, so RE-READ state at the start of every turn instead of reporting from memory; stale reports are how you contradict the owner's dashboard.
4. Do not hand-write product code for managed work and do not bypass HQ with ad-hoc `hermes --profile <agent> chat` dispatches; reviews go through the review gate, not direct profile chats.
5. A task assigned to `orchestrator` will not launch — assign one of the specialist profiles.

## Delivery after review (code tasks)
An approved review means correct **on its branch**, not shipped. Inventory where the work physically landed (branch commit, uncommitted worktree, or uncommitted main tree — check all three), commit the reviewed state, then fast-forward/merge `wm/run-<id>` chains onto `main` in dependency order; grep for conflict markers before committing; never pipe `git merge` through `tail` (swallows the exit code). Then verify the LIVE service, not the branch: for hermes-hq itself, `hermes-hq service restart` and curl the real endpoint.

## Fresh installation quickstart
1. Install the package from the hermes-hq checkout (`pip install -e .` into the Hermes venv) → `hermes-hq` on PATH.
2. `hermes-hq serve` once to generate the password file, or set `$HERMES_HQ_PASSWORD`.
3. `hermes-hq service install` to run supervised (detect the box's supervisor); enable `service auto-update` if wanted.
4. Migrating from a legacy Work Manager (`wm.db` + `runs/`): `hermes-hq import <legacy dir>` — then retire the legacy stack completely (stop its server/crons, archive its DB). Two live stores WILL diverge.
5. Install agent templates from `agents/` (`backend/agents.py` install flow) so specialists and the orchestrator overlay carry their skills.

## Pitfalls
- No `sqlite3` binary on a minimal box — use the venv python for read-only queries.
- `heartbeat_at` is written once at claim: equal to `started_at` does NOT mean stale. Judge liveness by the run's process and the profile's `state.db` session activity.
- `blocked` ≠ `failed`: blocked is a contract-honest handoff to the owner (see above); failed/stalled are retryable.
- The owner's dashboard is never "stale browser cache" when the API disagrees with you — if your view contradicts the UI, YOU are probably reading the wrong store. Check which DB and port you queried before telling the owner to clear anything.
