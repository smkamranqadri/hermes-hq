# Feedback-from-manual + Task Edit (Standard) — planned 2026-08-31 — COMPLETE 2026-08-31 (proof in State git history)

Goal: owner context reaches a brief without superseding the task. Owner locked
(interview 2026-08-31): scope C — feedback from `manual` PLUS an audited
description/DoD edit with inline UI; fields = description + definition_of_done
only; feedback from `ready` stays refused (the edit is the right tool there).

## Design

- `OWNER_FEEDBACK_SOURCE_STATUSES` gains `'manual'` — manual → rework with the
  owner's words in the transition marker (brief threading and open-review
  closing already work; demote logic only applies to `done` parents, untouched).
  TaskDetail `canFeedback` gains `'manual'`.
- `edit_task(task_id, description=None, definition_of_done=None)`
  (core/wm_store.py): at least one field required; refused when status is
  `running` (brief already rendered at claim) or `done` (historical record);
  updates fields + `updated_at`, NO status change/transition; activity
  `task_edited` with detail naming each changed field and its OLD value
  (truncated ~200 chars) — that is the audit trail.
- CLI `wm task edit <id> [--description X] [--dod X]`; API
  `POST /api/task/{tid}/edit {description?, definition_of_done?}` (409 via the
  usual engine-ValueError mapping).
- TaskDetail: pencil affordance on the Description and Definition-of-done
  blocks (hidden while running/done) → textarea (16 px font) + Save (busy
  state) / Cancel; refetch on save.
- Verify during implementation that `render_brief` reads description/DoD from
  the task row at claim time, so an edit lands in the next run's brief.

## Acceptance

1. Tests: feedback from manual → rework (marker + review-close + brief words);
   edit happy path + activity row; refuse running/done/empty edit; endpoint
   status codes. Suite green (113 + new).
2. Live :9010 (DISPATCHER PAUSED for the rework window — a rework scratch task
   is claimable bait; pattern from the Group 10 e2e): scratch task → manual →
   API edit (survives reload) → feedback → rework proven, then take over +
   `wm task close --by-owner`, resume dispatcher, notifications cleaned.
3. Playwright 390×844 `isMobile` + 1440 on the scratch manual task: feedback
   button visible, edit flow (open, type, save, new text shown), scrollWidth
   390, no page errors, screenshots reviewed.

## Out of scope

Feedback from `ready`; title edits; editing during `running`; any change to
review attribution.

## Status

COMPLETE 2026-08-31 — all acceptance checks passed; proof in `kis/state/current.md`.
