# Owner Gate Chip Confirmation Modal Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Require an explicit confirmation in the existing custom modal flow before changing a task's owner-approval gate from the Task Detail gate chip.

**Architecture:** Keep the existing audited `POST /api/task/:id/edit` write path unchanged. Extend the `GateChip` component to open the custom `Modal` on click, then perform the same mutation only from the modal's confirm action; cancelling, Escape, and backdrop dismissal must leave the gate unchanged. Reuse the modal/button primitives and copy conventions delivered by Task #163 rather than introducing another confirmation mechanism.

**Tech Stack:** React 19, TypeScript, React Router/Vite frontend, TanStack React Query, existing `Modal` and `Btn` primitives.

---

## Current context and assumptions

- `frontend/src/pages/TaskDetail.tsx:50-64` defines `GateChip`. Its click handler currently calls `m.mutate({ owner_approval: !on })` immediately.
- `frontend/src/pages/TaskDetail.tsx:134` computes `canEdit` and passes it to `GateChip`; the chip remains hidden when disabled and off, and remains visible but non-editable when enabled on a running/done task.
- The audited API contract must remain `POST /api/task/:id/edit` with `{ owner_approval: boolean }`; no backend or database changes are needed.
- `frontend/src/components/Modal.tsx:3-16` provides the project modal shell and `:24-26` provides `Btn`.
- `frontend/src/components/forms.tsx:96-101` contains the legacy `ActionBtn`/`window.confirm` path. This feature should not add another `window.confirm`; it should use the custom modal produced by Task #163.
- Task #163 is currently `planned` and not released according to `.venv/bin/hermes-hq wm task show 163`; it is the prerequisite for the shared custom confirmation modal work. Task #164's live row currently has no recorded dependency despite its brief saying it depends on #163, so the owner/Work Manager should ensure #163 is complete before implementation begins.
- No frontend unit/spec files were found under `frontend`; validation should therefore use the TypeScript/Vite build plus the repository's established browser smoke/Playwright workflow, if available in the implementation environment.

## Proposed interaction

1. Clicking an editable gate chip opens a modal and does not mutate state.
2. Enabling copy should explain that completions will be held for owner approval (for example: `Enable owner approval? Completions will land on your desk until you approve them.`).
3. Disabling copy should explain that the protection is being removed (for example: `Remove the owner-approval gate? Future completions can be marked done without owner approval.`).
4. The modal has an explicit confirm action whose label reflects the operation (`Enable gate` / `Remove gate`) and a `Cancel` action.
5. Confirm invokes the existing mutation exactly once. While pending, disable both modal actions and prevent a second submission; on success close the modal and preserve the existing toast. On error keep the modal open and show the existing error toast behavior.
6. Escape and backdrop dismissal close the modal without a request. The chip remains keyboard accessible and the modal's primary action should be reachable on a 390px mobile viewport.

## Implementation steps

### Task 1: Confirm the shared modal API from Task #163

**Objective:** Verify the exact exported component and button conventions introduced by the prerequisite custom-confirmation work before touching `GateChip`.

**Files:**
- Inspect: `frontend/src/components/Modal.tsx`
- Inspect: `frontend/src/components/forms.tsx`
- Inspect: Task #163's result paths/branch once it completes

**Steps:**
1. Confirm Task #163 is completed and its required review is approved.
2. Read the resulting modal API and identify the smallest reusable shell/action pattern.
3. Keep this feature scoped to `TaskDetail.tsx` unless #163's documented API requires a narrowly targeted shared-component adjustment.

### Task 2: Add modal-driven gate-chip state flow

**Objective:** Replace the immediate gate-chip mutation with a custom confirmation modal while preserving the existing API and success/error handling.

**Files:**
- Modify: `frontend/src/pages/TaskDetail.tsx:50-64`
- Test/inspect: the rendered Task Detail path for an editable gated and ungated task

**Steps:**
1. Add local open/close state to `GateChip`.
2. Change the chip click handler to open the modal only; do not call `m.mutate` there.
3. Render the modal with enable/disable-specific title, explanation, and confirm label.
4. Call `m.mutate({ owner_approval: !on })` only from the confirm action, retaining the existing `ApiError` toast and closing on success.
5. Ensure the modal and chip honor `canEdit`; pending state must not allow duplicate mutation.
6. Keep the existing chip text, styling, tooltip semantics, and `data-gate-chip` hook unless the shared modal contract requires an additive test hook.

### Task 3: Verify behavior and responsive presentation

**Objective:** Prove that accidental clicks no longer mutate the gate and that the intended confirmation path still persists the change.

**Files:**
- Validate: `frontend/src/pages/TaskDetail.tsx`
- Validate: relevant browser smoke/Playwright script or test location discovered during implementation

**Steps:**
1. Run `npm run build` from `frontend/`; expected result is a successful TypeScript check and Vite production build.
2. Run the applicable repository test suite for backend/API regressions; the existing `tests/backend/test_approval_gate.py` should remain green because the endpoint contract is unchanged.
3. In a live/dev UI, open a task with an editable gate chip, click it, and verify no request/state change occurs before confirmation.
4. Cancel, press Escape, and click outside the modal; verify the gate remains unchanged.
5. Confirm both enabling and disabling paths; verify one audited edit request, the expected toast, and the refreshed chip state.
6. Check 390px mobile and desktop widths for clipping, reachable buttons, and no horizontal overflow; record the actual command/results in the task completion report.

## Files likely to change

- `frontend/src/pages/TaskDetail.tsx` — gate-chip state and custom confirmation modal integration.
- Possibly `frontend/src/components/Modal.tsx` or a shared confirmation component only if Task #163's delivered API is incomplete; avoid duplicating modal primitives.
- No backend files, schema migrations, API routes, or Work Manager database changes are expected.

## Risks and tradeoffs

- Implementing before #163 lands could duplicate or conflict with its custom confirmation API; wait for the prerequisite's reviewed result.
- A modal that closes immediately on mutation error would hide the user's unfinished choice; keep it open on error unless the shared component enforces another documented behavior.
- The owner-approval edit endpoint is already audited and guarded by task status; this plan deliberately does not alter those server-side protections.
- Existing `ActionBtn` still uses `window.confirm` elsewhere. This task should not broaden scope into replacing every confirmation unless the owner separately approves that follow-up.

## Acceptance checklist

- [ ] Task #163 is complete and its review is approved.
- [ ] Editable gate-chip click opens the custom modal and sends no mutation until confirmation.
- [ ] Cancel, Escape, and backdrop dismissal make no API request.
- [ ] Enable and disable copy clearly describes the consequence.
- [ ] Confirm performs exactly one existing owner-approval edit and preserves success/error feedback.
- [ ] `npm run build` passes and the relevant backend suite remains green.
- [ ] Mobile (390px) and desktop checks pass without layout regressions.
- [ ] Owner reviews and approves the plan before implementation/release; this plan task stops at that owner gate.
