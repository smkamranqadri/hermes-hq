# KIS initialization for new projects

## Outcome

When creating a project from Hermes HQ, the owner can opt into KIS initialization. The flow creates the project first, then creates the first task in that project to initialize KIS. This keeps project creation and project-memory setup observable as normal Work Manager work instead of hiding a networked bootstrap inside the project write.

## Scope

- Add an `Initialize KIS skill` checkbox to the New Project modal.
- Submit the opt-in choice with project creation.
- Create and persist the project before creating the initialization task.
- When enabled, create the first task in the new project with a clear title such as `Initialize KIS project memory`, assigned to the appropriate implementation agent, with a concrete definition of done and the required review policy.
- When disabled, create only the project and no KIS task.
- Preserve the existing automatic slug/path behavior and project navigation.
- Ensure the initialization task runs in the project’s configured git worktree when it is a code task, and is independently reviewed before completion.

## Out of scope

- Changing the KIS repository or bootstrap script.
- Automatically releasing or dispatching the generated task without the normal approval gate.
- Running KIS initialization in the browser or blocking project creation on a hidden subprocess.
- Deploying to production.

## Files likely involved

- `frontend/src/components/forms.tsx` — checkbox and request payload.
- `backend/writes.py` — project-create request and project-then-task orchestration.
- `core/wm_store.py` / task creation helpers — only if the transaction or task metadata requires a small backend adjustment.
- `tests/backend/test_writes.py` — API tests for enabled, disabled, and failure paths.
- Relevant frontend tests or build checks if present.

## Acceptance checks

1. Modal shows an unchecked KIS option and sends the selected value.
2. Disabled flow creates exactly one project and no initialization task.
3. Enabled flow creates the project first, then exactly one initialization task under that project; the task is visible through the normal task API/UI.
4. The generated task has a precise title, actionable description, agent-verifiable DoD, code/worktree metadata as appropriate, and `review_policy=required`.
5. The generated task does not auto-run merely because the project was created; normal planned/approval/release rules remain intact.
6. Failed task creation does not leave a silently half-configured project or an unreported error; the API/UI reports the failure honestly.
7. Existing project creation, slug/path derivation, and navigation behavior remain passing.
8. Backend tests pass and the frontend production build passes.

## Risks and assumptions

- The owner’s intended behavior is task-based initialization, replacing the current direct bootstrap call during project creation.
- The first task should be a code task so the dispatcher gives it an isolated `wm/run-<id>` git worktree; the task brief must not claim owner-only live/deployment proof.
- Any live throwaway-project test is owner/integration verification, not the coder’s DoD. It should be performed after review and approval, with the disposable project cleaned up only through supported Work Manager operations.
- The generated task must not use the orchestrator as its implementation assignee; route implementation to `coder` and retain the independent Reviewer gate.

## Verification method

- Unit/API tests covering ordering, task metadata, disabled behavior, and error handling.
- Frontend production build.
- After the coder/reviewer cycle, owner-side integration test: create a throwaway project with KIS enabled, confirm the project exists and the first task is present with the expected metadata, then inspect the task/run result for initialized `kis/` contents.

## Execution routing

- Plan: owner-facing, no reviewer gate.
- Build (#161): `coder`, `is_code=1`, `review_policy=required`, `owner_approval=1`; run in the standard per-run git worktree.
- Integration/verification: owner approval and live throwaway-project check after the reviewed build; do not treat this plan as final sign-off.
