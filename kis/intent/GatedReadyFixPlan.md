# Gated-Ready Fix (Standard) — planned 2026-09-04

Origin: after the dispatch safeguard (`OwnerGatedDispatchPlan.md`, `6a0d02f`), a
gated task pushed to `ready` via `mark_ready` (orchestrator "unstuck" #175/#183
on 2026-09-04) sits "Queued · slots 0/3 busy" forever: the safeguard refuses to
claim it, `classify()` doesn't flag it, and the only escape is the undiscoverable
gate-chip toggle. The fix task #183 was itself gated (never ran) and mis-specced
(APPROVE via mark-ready — the exact op that doesn't clear the gate).

Owner decisions (interview 2026-09-04): don't unstick anything yet; fix all
three layers; Claude implements directly, pipeline verifies via re-specced #183.

## Scope
1. **Engine** (`core/wm_store.py`): `mark_ready` and `retry_task` REFUSE
   `owner_approval=1` tasks with an instructive ValueError naming the real
   release (clear the gate; API surfaces it as 409 via `_engine`).
   DESIGN CHANGE from the interviewed "land in waiting_approval": silently
   holding was flawed — `_mark_ready_if_deps_done` never promotes goalless
   tasks (it requires a released goal), so a goalless gated task held in
   waiting_approval would be stuck even AFTER approval. A loud refusal has no
   such gap and teaches the caller (orchestrator/CLI/UI) the sanctioned op.
   Gated-ready still becomes impossible to create; no repair migration:
   `claim_task` only checks the flag, so legacy gated-ready rows (#175/#183)
   dispatch as soon as the gate clears.
2. **Visibility** (`backend/status.py` + `frontend/src/status.ts` mirror):
   any gated task in `waiting_approval`/`ready`/`rework` classifies as
   **needsyou · "Awaiting approval"** (same label as gated manual); parity
   test extended. Non-gated behavior byte-identical.
3. **UI** (`frontend/src/pages/TaskDetail.tsx`): consequence-named Approve
   button on gated non-`running`/non-`done`/non-`manual` statuses (manual
   already has one), wired to the audited edit path
   (`POST /api/task/{id}/edit {owner_approval:false}`), which auto-promotes
   (`_mark_ready_if_deps_done`) and lets the next tick dispatch. Gate chip stays.
4. **#183 re-spec** (audited `edit_task`): becomes INTEGRATE/VERIFY —
   independently verify the deployed fix live; gate stays for the owner.

## Out of scope
Approving #175/#176/#177 (owner clicks), dispatcher changes, feedback-clears-
gate semantics (per safeguard plan, feedback records but never makes a gated
task runnable — after feedback the owner Approves to launch the rework),
orchestrator skill edits.

## Acceptance
- mark_ready/retry on gated task → `waiting_approval`, audited, no run/worktree.
- Gated waiting_approval/ready/rework → needsyou "Awaiting approval" in BOTH
  layers (parity-pinned); appears in the Needs-you group; non-gated unchanged.
- Approve button on gated #175/#183; absent on non-gated + running/done;
  click clears gate → scratch-DB launcher-seam proof: no dispatch before,
  exactly one after, no duplicate on repeat tick.
- Suite green as root; `npm run build`; Playwright 390×844 `isMobile` + 1440,
  scrollWidth 390, no page errors; service restarted (sanctioned path;
  owner re-login expected); #183 re-spec carries `task_edited` audit.

## Risks
Parity test must move with classify; restart wipes login sessions; assumes
`waiting_approval` is the canonical held state (it is — `_mark_ready_if_deps_done`
promotes from it on approval).
