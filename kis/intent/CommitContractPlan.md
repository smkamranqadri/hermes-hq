# Commit Contract Plan

**Status:** APPROVED 2026-09-07 (owner interview in session). Slice A next.
**Work mode:** Phase — two slices; the gate ships first because it stops the bleeding.

## Why

Agents get an isolated `wm/run-<id>` branch + worktree per `is_code` run and are told only
*"do not touch other branches"*. **Nothing in the engine ever tells them to commit** — the word
"merge" appears exactly once in `core/wm_store.py`, in an unrelated comment. Measured
2026-09-07: **13 of 31 run worktrees hold uncommitted work** (up to 10 files each), and **every
one belongs to a run marked `done` on a task marked `done`** — #165, #179, #191, #196 (which
alone left seven). Only 4 runs in the project's history ever produced a commit. The work
usually got re-landed by hand later, so it was rarely lost, but it was always wasted.

Task #191 is the worked example: the coder built and tested the reviewer-CLI migration, the
reviewer **approved it by reading the uncommitted working tree**, the task went `done`, and
`main` never received a line of it — leaving #192 (deploy) with nothing to deploy. One
feedback round fixed it (commit `7638cd0`, clean tree, one commit), which is evidence the
agents comply happily when asked. Nobody had asked.

Owner's question that framed this: *"how fix it, it happened before as well, may prompt or
agent memory should handle it"*. Answer, in order of what each buys: **memory is the wrong
layer** (per-profile, advisory, silently skippable); **the brief is necessary but insufficient**
(#191 ignored a far more explicit DoD for six runs); **the engine gate is the fix** —
`record_completion` already overrides the agent's own verdict for review and approval gates,
so this is the same idea applied to "your code is actually committed".

## Decided design (owner-interviewed; do not relitigate)

- **Refuse, don't auto-commit.** A dirty tree at `done` returns the task to `rework` with the
  uncommitted paths in the blocker. The engine never authors commits on an agent's behalf.
- **Gate on a DIRTY TREE only, never on "has commits".** A clean tree with zero commits means
  the run genuinely changed nothing — legitimate for the INTEGRATE/VERIFY tasks that are common
  in this pipeline (#183, #192). Requiring a commit would falsely block all of them.
- **Fail OPEN.** No workdir, missing directory, not a git repo, git absent, timeout → allow the
  `done`. A broken check must never strand a finished task. This is the one rule that keeps a
  new subprocess dependency in the engine's hottest path acceptable.
- **Cleanup is salvage-first and conservative** (owner narrowed it after seeing the risks):
  remove a worktree only when it passes ALL of — changes landed-or-junk, branch carries no
  unmerged commits, and no task `result_paths` points into it. **31 tasks and 176 runs have
  `result_paths` inside worktrees**, so removal genuinely breaks artifact links.
- **Never delete `wm/run-275`, `-279`, `-298`, `-456`** — each carries an unmerged commit;
  `run-456` is #191's work still pending #192's integration.

## Slices

### Slice A — the gate and the briefs — SHIPPED 2026-09-07 ✅
(`3263043` + `b8dc12d`; deployed, proven on a real dispatched run — see State → Proof. Two
defects the verification itself caught: `get_run` returns a `sqlite3.Row` with no `.get()`
(would have crashed `record_completion` in production — caught by the tests before deploy),
and the refusal could **re-dispatch forever** because `rework` is dispatcher-claimable while
the review cap only fires for tasks that have a review — task #202 ran three times in six
minutes before I parked it. Both loop guards now share `_rework_cycles` and park at the cap.)
1. `record_completion`: when the run has a `workdir` and reports `completed="done"`, run
   `git status --porcelain` there (bounded timeout). Non-empty → override to `rework` with a
   blocker naming the uncommitted paths. Fail open on every error path.
2. Work brief: the `Git branch/worktree:` line gains the contract — commit your work on this
   branch before finishing; a dirty tree means the work is invisible and the engine sends the
   task back.
3. Reviewer brief: verify the branch actually carries the commits under review.
4. `agents/coder/skills/coder-specialist/SKILL.md` Verification section: same rule; sync the
   installed profile.
**DoD A:** a dirty-worktree `done` lands in `rework` with the paths listed, and the same run
clean passes (both tested); fail-open proven for no-workdir / missing dir / non-repo; a REAL
dispatched code run that forgets to commit is sent back; suite green.

### Slice B — audit and clean the existing 13 — SHIPPED 2026-09-07 ✅
(proof in State. Pile had grown to 36 worktrees / 19 dirty by the time it ran, thanks to the
gate's own verification runs.)
5. Diff each dirty worktree against `main`; classify landed / unlanded / junk.
6. Parachute **every** unlanded diff into one tarball before touching anything.
7. Remove only worktrees passing all three safety tests; report every kept one with its reason.
**DoD B:** tarball exists and is readable; the four commit-carrying branches still exist; no
task whose `result_paths` point into a worktree lost its worktree; before/after survey recorded.

## Out of scope
Automatic worktree GC policy (when the engine should reap a worktree) and rewriting
`result_paths` to main-checkout paths — both belong to the existing worktree-lifecycle debt
item in State, which needs its own interview. Agent memory changes (wrong layer).

## Risks
`record_completion` is the engine's highest-traffic path and currently shells out to nothing —
mitigated by fail-open plus tests on every error path. Cleanup can break artifact links, which
is precisely why removal is gated on the `result_paths` check.
