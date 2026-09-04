# Owner-gated dispatch safeguard and task #174 recovery

Status: COMPLETE 2026-09-03 — chain #178–#182 all done; safeguard commit `6a0d02f` pushed to origin/main and deployed; #174 recovered (owner-closed done). Follow-up UI task #183 (dashboard approve action) filed separately.

## Scope and existing task graph

Goal #24 is already `released`, and its required work is already registered in the authoritative HQ store. Do not create duplicate tasks or re-run the goal lifecycle. The existing graph is:

- #178 `PLAN: Owner-gated dispatch safeguard and task #174 recovery` (orchestrator)
- #179 `BUILD: Implement owner-gated dispatch safeguard` (coder), depends on #178, required review
- #180 `COMMIT: Record verified dispatcher safeguard` (coder), depends on #179
- #181 `PUSH: Publish verified safeguard to remote main` (coder), depends on #180
- #182 `INTEGRATE/VERIFY: Recover task #174 and deploy safeguard` (coder), depends on #181

The final task is the owner/live-system gate. It must not be treated as proof supplied by the build task.

## Root cause and invariants

Current owner-gated tasks use `tasks.owner_approval`, but the dispatch eligibility paths do not consistently enforce it:

- `core/wm_store.py:1350` `mark_ready` can move a gated task to `ready`.
- `core/wm_store.py:1399` `claim_task` accepts both `ready` and `rework` without an owner-approval predicate.
- `core/wm_store.py:1411` `next_ready_tasks` selects both statuses without the gate.
- `core/wm_store.py:1795` `_mark_ready_if_deps_done` and `:1833` `promote_waiting_approval_ready` can promote a gated child after dependency completion.
- `core/wm_store.py:1851` `promote_dependents` reaches the same helper after a parent completion.
- `core/wm_store.py:2349` `owner_feedback` intentionally sends eligible work to `rework`; this must remain recorded but must not make a gated task runnable.
- `core/wm_store.py:2497` `retry_task` currently reopens blocked/failed/stalled work to `ready`; this must preserve the owner hold.
- `core/wm_dispatch.py:236-241` obtains candidates and claims them; the defense must exist in the store's atomic claim path as well as candidate selection, so manual and automatic ticks cannot bypass it.

Non-negotiable invariants after the build:

1. A task with `owner_approval=1` cannot be claimed or spawned from `ready` or `rework`.
2. Dependency completion cannot promote a gated task to `ready`; it must remain in the explicit held state (`waiting_approval`, or the existing owner-awaiting state where applicable).
3. Explicit release/retry/feedback paths cannot silently clear the gate.
4. Approval is the only action that clears the hold. Once approval clears it, the task becomes eligible automatically if its goal is released and all dependencies are done; otherwise it remains dependency-gated.
5. A non-gated task retains existing behavior, including automatic `rework` dispatch and dependency promotion.
6. Every refused/preserved transition is auditable and does not create a run, worktree, or duplicate review.

## Approval auto-resume contract

The implementation must define one engine-level approval operation (using existing sanctioned CLI/API write paths, not direct SQL) with these semantics:

- Approval clears `owner_approval` only for a valid owner-approved task state; it must reject running/done/unknown tasks and stale plan approvals according to existing guards.
- If the task is under a released goal and dependencies are all done, approval transitions it to `ready` and records a transition/activity entry. The next dispatcher tick may claim it.
- If dependencies are unfinished, approval leaves it `waiting_approval` (or the project’s canonical dependency-held status), and the existing dependency-completion path promotes it only after all predecessors are done.
- Approval must not promote dependents prematurely, must not bypass a required Reviewer review, and must not launch synchronously inside the approval mutation.
- The operation is idempotent or returns a clear engine error when repeated; no duplicate transition/activity/run is allowed.
- The API and CLI must expose the same engine behavior. The UI is not in scope for #179 unless an existing route needs only wiring to the engine operation.

## Implementation and regression coverage

Coder #179 should inspect and update the smallest shared predicates/helpers rather than adding separate caller-specific exceptions. Cover these state paths with isolated scratch databases and a real dispatcher tick/launch seam:

### State-machine tests

- `ready + owner_approval=1`: explicit mark-ready/retry cannot make it dispatchable; claim returns false or the sanctioned refusal is raised; status and gate remain intact.
- `rework + owner_approval=1`: owner feedback is recorded, but claim/candidate selection cannot run it.
- `waiting_approval + owner_approval=1` with all dependencies done: dependency promotion leaves it held.
- Gated child whose parent becomes done: `promote_dependents` leaves child held and does not return it as promoted.
- Approval after no dependencies: task becomes `ready`; approval after unfinished dependency: task remains held; later dependency completion makes it ready.
- Approval followed by a dispatcher tick: exactly one work run is created/launched; a second tick cannot duplicate it.
- Required-review task: approval does not bypass `needs_review`; reviewer completion still precedes owner approval where that policy applies.
- Non-gated control cases: ready dispatch, rework dispatch, `promote_waiting_approval_ready`, and parent completion promotion continue to work exactly as before.
- Invalid/repeated approval and running/done refusal paths preserve state and audit integrity.

### Live-dispatch evidence required by #179

Use the existing test launcher seam and a scratch project, not the production `hq.db`. Assert from the database and dispatcher summary that:

- before approval: `wm dispatch` reports no dispatched run for the gated task, and no run/worktree is created;
- after approval: the next tick reports exactly one dispatched run and the task is `running`;
- a repeated tick while that run is active reports no duplicate dispatch;
- the same sequence for a non-gated task still dispatches normally.

Run focused tests first, then the relevant backend/core suite, `git diff --check`, and Python syntax checks. Record exact commands and outputs in the build completion artifact. Reviewer #179's required gate must independently verify the diff and tests before #180.

## Safe recovery procedure for task #174

This is operationally separate from organizing the personal-brand files. No #174 file action, owner decision, move, rename, archive, or deletion is authorized by this plan.

1. Snapshot current authoritative state with `wm task show 174`, `wm status`, and the latest run/process information. Preserve the existing review ledger and result path.
2. Pause the HQ dispatcher using the CLI before touching the active run; do not edit `hq.db` or use SQL.
3. Stop/hand over the currently running #174 execution through the sanctioned owner-stop/mark-manual mechanism, preserving its existing brief, run, worktree, and owner-action-set text. Do not use retry while it is running.
4. Verify #174 is no longer `running`, no new run is created on a manual dispatch tick while its owner decision remains outstanding, and the task remains visibly surfaced for the owner rather than silently auto-retried.
5. Leave #174 parked until Kamran supplies the batch action set. If the owner wants to resume it later, use the normal feedback/rework or explicit approval path; do not infer approval from the existing review ledger.
6. Resume dispatch only after the safeguard is installed and the owner has approved that operational step. Verify the service/log and authoritative `wm status` after restart; record any limitation if live verification cannot be performed by the assigned agent.
7. Rollback is limited to sanctioned CLI state operations and service controls: restore the dispatcher pause state and leave #174 manual/held. Do not restore by deleting task/run rows or editing the database.

The procedure is reversible because it changes only dispatcher pause/run ownership through supported commands and leaves all task/run/history rows and personal-brand files intact. The final #182 owner-gated integration task owns production/live verification and any deployment decision.

## Acceptance for this plan task

This file identifies every affected transition/dispatch path, specifies approval auto-resume semantics, lists state and live-dispatch regressions, and gives a reversible recovery procedure for #174. It intentionally makes no source or database changes.
