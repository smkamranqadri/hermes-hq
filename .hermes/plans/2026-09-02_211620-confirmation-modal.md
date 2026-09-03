# Confirmation Modal Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Replace every in-app `window.confirm` prompt in Hermes HQ with a reusable, accessible custom confirmation modal that matches the existing UI and preserves destructive-action safeguards.

**Architecture:** Add a small controlled `ConfirmModal` primitive beside the existing `Modal` and `Btn` components. Call sites open it from local React state; confirmation callbacks perform the existing mutation, retain current busy/error handling, and close only after the action succeeds. Do not change browser lifecycle prompts such as `beforeunload`, which are not in-app confirmation dialogs.

**Tech Stack:** React 19, TypeScript, React Router, TanStack Query, Tailwind CSS utility classes, existing `Modal`/`Btn` primitives.

---

## Current context and scope

- The repository is `/opt/data/projects/hermes-hq`.
- Existing modal conventions are in `frontend/src/components/Modal.tsx`; `Modal` already supports Escape and backdrop dismissal, and `Btn` already provides `primary`, `ghost`, and `warn` styles plus busy state.
- A repository-wide search found three native confirmation implementations:
  - `frontend/src/components/forms.tsx:97-100` — reusable `ActionBtn`, used by confirmation-gated controls elsewhere such as `frontend/src/App.tsx`.
  - `frontend/src/pages/Skills.tsx:175` — hub skill uninstall.
  - `frontend/src/components/chat/SessionTools.tsx:33` — transcript deletion.
- `frontend/src/components/terminal/TerminalHost.tsx:70` also uses `window.prompt` for terminal-tab renaming; that is an input dialog rather than a confirmation and is out of scope for this task.
- Existing inline two-step confirmations in `frontend/src/pages/Mcp.tsx` and `frontend/src/pages/Schedules.tsx` are already non-native and should remain behaviorally unchanged in this task unless implementation reuse is trivial.
- `window.beforeunload` guards in `Memory.tsx` and `Files.tsx` are browser lifecycle protection and are out of scope.
- `frontend/package.json` has a build script but no frontend test runner. Backend tests are Python-based and do not cover this UI-only change.

## Acceptance criteria

1. No in-app `window.confirm` usage remains in visible frontend source.
2. The three affected actions still execute only after explicit confirmation and retain their current API calls, success toasts, error toasts, and navigation/refresh behavior.
3. The confirmation UI uses the existing visual language and destructive `warn` styling.
4. Cancel, Escape, backdrop dismissal, and the modal close button do not execute the action.
5. The confirmation modal has an explicit dialog name, `role="dialog"`, `aria-modal="true"`, and a clear focus target; keyboard users can reach Cancel and the destructive action without relying on native browser UI.
6. The destructive action is disabled/busy while its existing mutation is pending, preventing duplicate requests.
7. `npm run build` passes and a source search confirms the native confirmation calls are gone.

## Implementation steps

### Task 1: Add the reusable confirmation primitive

**Objective:** Create a focused `ConfirmModal` API using the existing modal and button primitives.

**Files:**
- Modify: `frontend/src/components/Modal.tsx`
- Test/validation: `frontend/src/components/Modal.tsx` via TypeScript build and manual browser checks

**Steps:**

1. Extend the existing modal implementation only as needed for accessibility: preserve its Escape/backdrop behavior, add dialog semantics and an accessible close-button label without changing existing callers.
2. Export `ConfirmModal` with props equivalent to:
   - `title: string`
   - `message: ReactNode`
   - `onClose: () => void`
   - `onConfirm: () => void | Promise<void>`
   - optional `confirmLabel?: string` (default `Confirm`)
   - optional `busy?: boolean`
3. Render the message and Cancel/confirm buttons using `Btn`, with the confirm button using `kind="warn"`.
4. Keep the modal open while `busy` is true; disable both dismissal paths as appropriate during an in-flight action so a request cannot be accidentally duplicated or abandoned.
5. Give the dialog a stable accessible name (the title), set `aria-modal`, and focus the least destructive safe action (Cancel) on open. Preserve Escape support and ensure focus does not land behind the overlay.

**Validation:**

- Run `cd frontend && npm run build`.
- Confirm TypeScript reports no new errors.
- Check that existing `Modal` callers still compile without prop changes.

### Task 2: Migrate `ActionBtn` to controlled confirmation state

**Objective:** Replace the generic component's native confirmation call without changing its mutation contract.

**Files:**
- Modify: `frontend/src/components/forms.tsx:96-101`

**Steps:**

1. Add local `confirmOpen` state to `ActionBtn`.
2. Change the button click handler to open the confirmation modal when `confirm` is supplied; actions without `confirm` should continue mutating immediately.
3. Extract the existing mutation call into an `execute` callback so the modal's confirm button invokes the same `m.mutate(body, { onError })` path.
4. Close the confirmation modal from the existing mutation success callback after the toast and `onDone` callback run; ensure cancellation only clears state.
5. Render `ConfirmModal` adjacent to the action button with the existing `confirm` string as its message and `m.isPending` as its busy state.

**Validation:**

- Run `cd frontend && npm run build`.
- Search `frontend/src/components/forms.tsx` to confirm it no longer contains `window.confirm`.
- Verify the non-confirming `ActionBtn` path still performs one mutation on one click.

### Task 3: Migrate skill uninstall

**Objective:** Replace the direct native confirmation around hub skill removal.

**Files:**
- Modify: `frontend/src/pages/Skills.tsx:152-180`

**Steps:**

1. Import `ConfirmModal` from `../components/Modal`.
2. Add state for the selected uninstall confirmation (or a boolean tied to the current skill detail).
3. Change the Uninstall button to open the custom modal instead of calling `window.confirm`.
4. Keep the existing uninstall endpoint payload, job handling, toast/error behavior, and detail-modal close behavior unchanged after confirmation.
5. Use a specific title/message that identifies both the skill and profile, and label the destructive action `Uninstall`.

**Validation:**

- Run `cd frontend && npm run build`.
- In the browser, open a hub skill, click Uninstall, verify the custom modal appears, cancel it, reopen it, confirm it, and verify the existing job/toast flow.

### Task 4: Migrate session transcript deletion

**Objective:** Replace the native prompt in the chat session menu while preserving menu and navigation behavior.

**Files:**
- Modify: `frontend/src/components/chat/SessionTools.tsx:13-37`

**Steps:**

1. Import `ConfirmModal` if it is not already exported through the existing modal module.
2. Add a confirmation-open state to `SessionMenu`.
3. On Delete, close the menu and open the modal; do not call `deleteSession` before confirmation.
4. On confirm, reuse the existing `run` callback so busy state, refresh, toast, and navigation away from the current session remain exactly as they are today.
5. Use the current session title/id and profile in the message, and label the destructive action `Delete`.

**Validation:**

- Run `cd frontend && npm run build`.
- In the browser, open a session menu, cancel via button, Escape, and backdrop, then confirm deletion and verify the existing `Session deleted` toast and current-session navigation.

### Task 5: Cross-call-site verification and responsive QA

**Objective:** Prove the native dialogs are fully removed and the custom flow works on desktop and phone layouts.

**Files:**
- No additional production files expected.

**Steps:**

1. Run a source-only search excluding `node_modules` for `window.confirm`, `window.alert`, and bare `confirm(`; expected result is no in-app matches. Do not treat `beforeunload` as a failure.
2. Run `cd frontend && npm run build`.
3. Exercise all three migrated actions in the running Hermes HQ UI at desktop width and a 390px mobile viewport.
4. Verify no horizontal overflow, no console/page errors, correct warning button styling, readable long messages, and correct focus/keyboard behavior.
5. Record the exact build and browser verification results in the implementation task's completion evidence.

## Risks and tradeoffs

- The existing `Modal` closes on Escape and backdrop click. The confirmation primitive must retain that safe cancellation behavior and must not close after confirm until the mutation succeeds.
- Adding focus management to the shared `Modal` can affect every existing dialog. Keep the change minimal, test representative existing dialogs, and prefer confirmation-specific focus behavior if shared changes introduce regressions.
- A reusable `ActionBtn` modal may be rendered inside pages that already have another modal. Confirm that the overlay stacking and close behavior do not create an unusable nested-dialog experience.
- This plan intentionally does not replace already-custom inline confirmations in MCP and Schedules; a later consistency pass can decide whether those should also be promoted to modal confirmations.

## Handoff

This is a plan-only task. The owner must approve the plan before implementation is dispatched. After approval, execute the steps through the normal PLAN/BUILD and required-review workflow; do not treat this plan as implementation or final sign-off.
