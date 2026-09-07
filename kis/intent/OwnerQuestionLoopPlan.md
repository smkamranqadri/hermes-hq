# Owner Question Loop Plan

**Status:** APPROVED 2026-09-07 (owner interview in session). Slice A next.
**Work mode:** Phase — two slices, each deployed and owner-reviewed before the next.

## Why

Task #176 burned **34 runs / 16 review cycles over ~19 h** and 59 notifications before
approving; #98 hit 31 cycles / 63 runs; #174 15/29 — ~126 of the system's 452 runs are
build↔review ping-pong. Every #176 review named the same root blocker: *the owner answer
never arrived*. The mechanism to answer mid-run already exists (see Findings); the owner
had no working surface to answer through, and the brief tells a stuck agent to end its run.

Owner's requirement (2026-09-07): **an agent must be able to ask inside its live session —
not close and re-run — and the owner must be able to answer from the UI.**

## Findings (verified in code 2026-09-07 — do not re-derive)

- Agents ask by emitting a ```` ```hq-options ```` fence in reply TEXT; `backend/questions.py`
  scans running runs' transcripts each dispatcher tick (read-only on the foreign profile
  state.db) and raises a `question` notification. Only a TEXT body is stored — the
  structured options are **not** persisted.
- The brief's ASKING THE OWNER section (`wm_store._question_lines`, ~line 3291) tells the
  agent to keep working and poll `answer_path(run_id)` between steps; `POST
  /api/run/{rid}/answer` appends to that file → a live agent picks it up **in-session, no
  relaunch**. This already works.
- The run only dies when the agent gives up: the brief says finish `completed="blocked"`.
  `resume_blocked_run` + `writes.run_answer` then **relaunch the process** (2026-09-06,
  `701027f`) — the close-and-rerun the owner objects to.
- UI gaps: TaskDetail's answer box renders only for `blocked` runs; for a RUNNING run the
  only action is an "Answer in session" link to `/chat/<profile>/<session>` — and
  `backend/chat.py` never writes the answer file, so that path goes nowhere.
- `manual` is already the owner-parking status (approval gate uses it, classify surfaces it
  in Needs-you, `OWNER_FEEDBACK_SOURCE_STATUSES` includes it) — reuse it, don't add a status.

## Decided design (owner-interviewed; do not relitigate)

- **Answer surfaces: Task detail + Inbox.** Chat is explicitly OUT (it doesn't write the
  answer file and making it do so was rejected).
- **Bounded wait, then park.** The agent polls a bounded window (~10 min) while doing
  unrelated work; still unanswered → finish `completed="manual"` with the question as the
  blocker, parking the task "awaiting owner" instead of `blocked`. Not an indefinite wait —
  a forgotten question must not hold a worktree and a concurrency slot open forever.
- **Answering a parked task resumes the SAME run id** via the existing
  `resume_blocked_run` + relaunch. Accepted with eyes open: the process is respawned, so
  continuity is at the run-id/worktree/artifact level, not a live process.
- **Rework cap ships too** as the backstop for loops with no owner question in them
  (a coder and reviewer disagreeing on their own).

## Slices

### Slice A — answer a live question where you already are
1. `GET /api/run/{rid}/question` — re-parse the newest hq-options fence from the run's
   session transcript (reuse `questions.py`'s read-only reader) → `{question, mode,
   options[]}`. No schema change; the transcript stays the single source of truth.
2. TaskDetail question banner for **running** runs: options as buttons + free-text box →
   `POST /api/run/{rid}/answer` → writes the answer file → same run continues.
3. Inbox: the same control inline on `question` notifications (the phone path).
4. Remove the "Answer in session" chat link (it silently goes nowhere today).
**DoD A:** a real dispatched run asks with options → buttons render in TaskDetail AND Inbox
at 390×844; answering writes the file, the agent demonstrably acts on it, **run id unchanged
and no new process spawned** (verified against run history/pid); suite green; 0 page errors.

### Slice B — don't die waiting, and stop runaway loops
5. Brief change: bounded poll window, then `completed="manual"` (never `blocked`) with the
   question as the blocker.
6. Answering a parked task resumes the same run id (extend `resume_blocked_run` beyond the
   `blocked`-only guard; reuse the relaunch in `writes.run_answer`).
7. Rework cap: at **4** cycles `review_verdict(changes_requested)` parks the task `manual`
   with "N rework cycles — last reviewer comment: …" plus one `needs_you`, instead of
   setting `rework` for the dispatcher to re-claim.
**DoD B:** an unanswered question parks the task awaiting-owner (not blocked) and answering
resumes the same run id; a forced 4-cycle rework loop parks as needs-you instead of
re-dispatching; the owner-feedback path still works; suite green; live proof.

## Out of scope
Answering from chat (owner ruled it out). Collapsing the per-cycle notification spam
(59 for one task) — recorded as debt in State. Reworking the heuristic question detector.

## Risks
Touches `review_verdict` and the dispatch/resume paths — the highest-traffic engine code
(mitigated by the existing suite plus new tests, and by shipping in two reviewable slices).
The bounded window still parks a task if the owner is asleep — accepted, and the reason the
resume path must stay solid.
