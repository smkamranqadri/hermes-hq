---
name: orchestrator-intake
description: Interview the owner (grilling rounds) whenever chat brings new work — a bug report, change request, or feature idea — before any task, goal, or dispatch is created.
version: 1.0.0
author: hermes-hq
license: MIT
metadata:
  hermes:
    tags: [hermes-hq, intake, planning, owner, definition-of-done]
    related_skills: [hermes-hq-ops, grilling]
---

# Orchestrator Intake

## When to Use
Use whenever the owner brings a new piece of work in chat and it may become a Hermes HQ task or goal. Do not create a task, goal, dispatch, or delegation before this intake is complete. For registration, release, dispatch, and review mechanics, use `hermes-hq-ops`.

## Interview first
Before creating any task or goal, conduct the interview according to the `grilling` skill: work the design tree in frontier rounds, ask numbered questions with a recommended answer, look up facts rather than asking the owner, and wait for the owner's decisions. Cover goal, scope, constraints, definition of done, and proof — specifically what proves completion and who can produce it. The interview is done only when the frontier is empty and the owner confirms shared understanding; only then create the tasks or goal.

## Proof and DoD rule
An agent task's `definition_of_done` may contain only proof that the assigned agent can produce and verify from its environment. Do not put owner-only evidence in an agent task DoD. Examples of owner-only proof include deploying or merging to a protected/live system, live Lighthouse or production checks, signing into external accounts, or approving an external action.

For every owner-only proof, append a separate owner-gated task titled `INTEGRATE/VERIFY: <outcome>`. Give it the exact owner-only evidence as its DoD, make its dependency follow the agent work it verifies, and keep it at the end of the chain. It must hand the work back to the owner; never claim that an agent task is done merely because the owner-only step remains.

## Choose task, chain, or goal
After the interview, classify the work before registration:

- **One trivial, self-contained job:** create one task with the refined description and agent-producible DoD.
- **One feature with one build:** create a PLAN task followed by a BUILD task. The PLAN captures the approach and acceptance details; the BUILD implements it. Link BUILD to PLAN with `hermes-hq wm task depend <build_id> <plan_id>`.
- **More than one build's worth of work:** propose a goal and a written breakdown of phases, dependencies, assignees, DoDs, and owner-only INTEGRATE/VERIFY steps. Let the owner confirm that breakdown before creating the goal or any child tasks. Do not turn a large request into an unapproved task chain.

Owner confirmation of a proposed goal breakdown is required before registration. Registration approval is separate from release approval; leave newly created work planned until the owner explicitly releases it.

## PLAN/BUILD recipe
For a one-feature chain, use the existing Hermes HQ task CLI described in `hermes-hq-ops`:

```text
hermes-hq wm task create <slug> "PLAN: <feature>" <description> <plan_dod> --assignee <planner> --goal <goal_id> --owner-approval --review-policy none
hermes-hq wm task create <slug> "BUILD: <feature>" <description> <build_dod> --assignee <builder> --goal <goal_id> --is-code --review-policy required [--owner-approval]
hermes-hq wm task depend <build_id> <plan_id>
```

Set `--owner-approval` on BUILD when the project is deploy-adjacent (including `hermes-hq`, portfolio, or any project whose completion is intended to approach deployment or live validation). Set it on an owner-gated INTEGRATE/VERIFY task as well. PLAN always uses `review_policy none`; BUILD always uses `is_code` and `review_policy required`. Keep each task's description and DoD specific to that phase, and add further dependency edges so the owner-only verification is last.

## Verification
Before reporting that intake created work, verify all of the following:

- No task or goal was created before the interview answers were complete.
- The registered description and DoD match the refined restatement.
- The scope classification is correct: one task, PLAN → BUILD chain, or owner-confirmed goal breakdown.
- BUILD has `is_code` and `review_policy required`; PLAN has `review_policy none`; required owner gates are set.
- Every owner-only proof is isolated in the final `INTEGRATE/VERIFY` task and absent from agent DoDs.
- Dependencies are visible with `hermes-hq wm task show` or `hermes-hq wm status`, and newly created work remains planned until release.

## Pitfalls
- Do not bypass the `grilling` interview protocol or create work before the frontier is empty and the owner confirms shared understanding.
- Do not treat "approved" as proof of implementation or as release authorization.
- Do not let an agent task promise deployment, live metrics, external-account actions, or owner sign-off.
- Do not create a goal merely because a request has several bullets; use the more-than-one-build threshold and obtain confirmation.
- Do not bypass the single authoritative HQ store or hand-edit its database; use the CLI and API from `hermes-hq-ops`.
