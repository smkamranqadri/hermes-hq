# Phase-Gate Workflow (Standard) — planned 2026-09-02 — SHIPPED 2026-09-02

Outcome: all five slices done same day (#137 e22d8aa, #138 79195f3+26fb19c,
#139 50f4351, #140 40bd10c, #141 docs/workflow.md + 170126f). Two additions
discovered en route: the hermes-hq-restarter broker service (no setuid sudo on
this box — agents restart via /run/hermes-hq-restart/restart.request), and
task #143 (99c2ceb): completed=manual on a review-required task now routes
through needs_review before landing on the owner's desk (loophole found live
on #140).

Origin: task #130 looped three times — plan and build lived in one task, the
owner's "approved" went through Feedback → rework (which means *redo*), and the
only other button, Approve → done, would have closed the task with nothing
built. #136 shipped a narrower fix (goal-breakdown approval, reviewed, still
unmerged in worktree run-252). Owner approved this design (interview
2026-09-02, artifact "Hermes Phase Gates"): model phases as LINKED TASKS —
PLAN → BUILD → INTEGRATE — using existing `task_deps` + `close_by_owner`
dependent promotion. No schema change, no new engine states.

Owner decisions (2026-09-02):
- The interview follows Matt Pocock's published "grilling" skill, added
  VERBATIM as its own skill (`agents/orchestrator/skills/grilling/`, author
  attribution kept) with orchestrator-intake deferring to it — supersedes
  one-question-at-a-time. Protocol: design tree worked in rounds — each round
  asks the whole frontier as a numbered list, each question carrying a
  recommended answer; facts are the agent's job (look them up, never ask the
  owner); done when the frontier is empty and the owner confirms shared
  understanding. History: first delivered mid-run via the answer channel
  (run #255); the reviewer, seeing only the old task text, bounced it —
  lesson: owner guidance that changes requirements must be edited into the
  task description/DoD, the answer file alone does not bind review.
- Reviewer does NOT vet plans — a PLAN task lands straight on the owner's desk
  (review_policy none); the BUILD task keeps review_policy required.
- Owner gate ON for BUILD completions in deploy-adjacent projects (portfolio,
  hermes-hq itself); off for internal chores — set at intake.
- The intake interview decides task vs chain vs goal (more than one build's
  worth of work → propose a goal); the owner confirms.
- DoD rule: an agent task's definition_of_done may only contain proof agents
  can produce; owner-only proof (deploy, live Lighthouse) becomes the
  INTEGRATE/VERIFY task — never an agent DoD clause (the trap that made #130
  unwinnable even without the loop).

## Slices (board: goal #30, tasks #137–#141)

A. **#137 Integrate run-252** — merge the reviewed #136 approve-plan work from
   `/opt/data/hermes-hq/runs/worktrees/run-252` into main, test, restart.
   Preserves main's unrelated uncommitted files. (deps: none)
B. **#138 Interview-first intake skill** — orchestrator skill: one question at
   a time, the proof-rule question, then create the chain / propose a goal.
   Lives in `agents/orchestrator/skills/`, deployed like hermes-hq-ops.
   (deps: none)
C. **#139 Phased creation** — `wm task create --phased` + UI toggle → PLAN
   (owner gate, no review) + BUILD (is_code, review required) linked in one
   shot. (deps: #137)
D. **#140 Honest buttons + feedback guard** — gated manual task WITH dependents
   says "Approve plan → start build (#N)"; approval-looking text typed into
   Feedback triggers a warning (warn, don't block). (deps: #139)
E. **#141 docs/workflow.md** — the flow documented for new users, accurate
   against shipped code, linked from README. (deps: #138, #139, #140)

Release: goal #30 sits `planned` — the owner releases it (`wm goal release 30`
or the UI button); that is itself the new workflow's "breakdown on your desk"
step, exercised for real.
