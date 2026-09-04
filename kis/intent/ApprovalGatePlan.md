# Approval Gate Bundle (Standard) — planned 2026-09-01 — COMPLETE 2026-09-01 (proof in State git history)

Origin: task #130 ("plan and get my approval before coding") went straight to
done — the coder honored the prose gate (wrote a plan, no code) but the
completion contract only knows done|blocked|failed, and review_policy inserts
an AGENT reviewer, not the owner. Owner approved this design (interview
2026-09-01): engine-enforced owner gate as an orthogonal task field, plus the
agent-initiated hand-over verdict, plus readable results on TaskDetail.

## Slices

A. **`owner_approval` field (engine-enforced gate)**: additive INTEGER column
   (default 0) + migration; `create_task`/`edit_task` accept it (edit under the
   SAME gates as description/DoD — refused running/done), CLI
   `--owner-approval`, `POST /api/task/{id}/edit`. In `record_completion` and
   the review-approved verdict path: when the task would become `done` and
   `owner_approval=1`, land on status `manual` with `human.label`
   **"Awaiting approval"** (extend the existing label parity, do not duplicate)
   + an unread `needs_you` notification (verify `sync_notifications` covers a
   run-driven `→ manual` transition; add if not). Gate fires AFTER the agent
   review resolves (all three policies). Owner acts with the EXISTING buttons:
   Close-as-done (= approve; promotes dependents) or Feedback (= redirect →
   rework). Brief gains one line telling the agent the task ends at the owner.
B. **`"completed": "manual"` contract verdict (agent-initiated hand-over)**:
   `wm_run_agent._read_completion` accepts the fourth value and routes it to
   slice A's landing (same label + notification); `blocker` REQUIRED = what the
   agent needs from the owner. Review runs keep the 3-verdict contract
   ("manual" from a review run = contract error → failed). Completion-contract
   brief text (single shared function) gains one line: use ONLY when the task
   explicitly requires an owner decision to continue.
C. **Readable results on TaskDetail**: backend resolves each task
   `result_path` against the files roots (reuse `files.py` containment) into
   `{root, rel, kind}`; TaskDetail Results rows become tappable — markdown/text
   ≤1 MB expand inline rendered with the chat `Markdown` component (fold
   pattern, collapsed by default), images preview inline, everything else (and
   an edit affordance) deep-links to Files via `?root=…&path=…`. Paths outside
   any root stay plain text. Read-only — no inline editing.
D. **Retrofit (ops, after A ships)**: set `owner_approval=1` on the seeded
   outward-facing tasks #122/#123/#124 (riyadh sends) and #127 (social
   publish) via the audited edit path; verify read-only after.

## Acceptance

1. Suite green (117 + new `test_approval_gate.py`): gated done → `manual` +
   "Awaiting approval" + unread needs_you; policy none = no review spawned,
   policy required = review runs then gate; close-as-done promotes dependents;
   feedback → rework; `completed:"manual"` lands identically; review-run
   "manual" rejected; label parity test extended (backend + status.ts mirror).
2. Live :9010 scratch e2e (dispatcher on): real gated run → phone chip →
   approve path; second scratch → feedback path; `wm check` green before/after;
   scratch tasks owner-closed and notifications read after.
3. Playwright 390 `isMobile` + 1440: "Awaiting approval" chip distinct from
   "Handed over"; TaskDetail renders run #232's real plan file
   (`.hermes/plans/2026-09-01_201750-fix-performance.md`) inline; deep link
   lands in Files; scrollWidth 390, no page errors; screenshots reviewed.
4. Migration applied to live hq.db (additive), service restarted clean.
5. Retrofit: #122/#123/#124/#127 read `owner_approval=1` with audit rows.

## Out of scope

Toggling the flag on running tasks; approval audit UI; multi-approver; inline
editing in TaskDetail; question-fence changes.

## Status

COMPLETE 2026-09-01 — all four slices shipped same day; proof in
`kis/state/current.md`. One deviation, an improvement: the `manual` verdict
also SETS `owner_approval=1` (sticky gate) so the landing and the
continuation both read/behave as awaiting approval.
