# Worktree Start-Point + Land Plan

**Status:** **COMPLETE 2026-09-11.** Both slices shipped and proven. Suite **544 → 565** (+21, all new tests green, zero regressions). Slice A: `resolve_start_point()` + a real start-point on `git worktree add`. Slices B+C: `land_approved_run()` / `_land_and_report()` behind `code_land`, wired into `review_verdict()` and `close_by_owner()`, gating promotion. Owner-interviewed; the three decisions below were locked and held.

**One deliberate deviation from the precedence written below, made during build:** the rule keys off RUN RECENCY, not `task["status"] == 'rework'`. Two reasons, both found in the code rather than guessed. `claim_task()` flips ready/rework to 'running' BEFORE the dispatcher reaches `run_workdir`, so the status is already gone. And a plain "rework resumes its own branch" rule is wrong when the parent BUILD rebuilds while a REVIEW is pending — exactly the #280/#281 shape — where the review must re-base on the NEW build instead of resuming its own stale branch. Comparing run ids handles both; `test_parent_rebuild_wins_over_this_tasks_own_stale_branch` pins it.
**Parent:** the Known-debt item "Every isolated run's worktree is branched from `main`, so a REVIEW run structurally cannot see the BUILD it is reviewing", opened 2026-09-11 after it nearly pushed the wrong tree to a public repo.
**Work mode:** Standard — two slices (A start-point, B+C land).

## Goal

Make it impossible for a review to approve a tree the build never produced, and give approved work one defined way to reach `main` — so an INTEGRATE task has something correct to push without a human moving git by hand.

## The defect, as measured on 2026-09-11

`core/wm_dispatch.py:88` runs `git worktree add -b wm/run-<id> <path>` **with no start-point**, so git branches from the primary repo's current HEAD. Every isolated run starts at `main`, build and review alike — but a build's work only ever exists on `wm/run-<build_id>`, and **nothing in the codebase ever merges a `wm/*` branch back** (`grep` for merge in `core/` returns only the two brief strings that forbid agents from doing it, `wm_store.py:2435` and `:3800`). A reviewer therefore reviews a tree the coder never produced.

Proven on project 7 (Personal Brand):

| fact | value |
|---|---|
| build commit | `1d6642b` on `wm/run-565` |
| review commit | `dcd2d19` on `wm/run-566` |
| relationship | **siblings** — both parented on `d678fb7` |
| difference | 9 files, 52 insertions / 66 deletions, including **opposite renames** of the website snapshot files |
| cycles burned | runs 560 → 569, **six BUILD↔REVIEW rounds in 90 minutes** |
| consequence | run 566 approved anyway; a push would have published a tree missing the owner's requested rework |

Runs 562 and 563 refused correctly with "BUILD commit not in ancestry". Run 566 stopped saying it and approved. **The false approval is the defect's payload, not a one-off bad agent.**

What finally worked was luck, not a fix: run 570 reviewed the build **by path** (`worktrees/run-565`) rather than by branch. Its own review commit `a518e4f` is *still* a sibling of the build, so the ancestry check still fails today.

## Decisions locked in the 2026-09-11 interview

1. **Start-point + land, not start-point alone.** Fixing only the start-point makes reviews honest but leaves `main` advancing solely by hand, so INTEGRATE still has nothing correct to push. Both halves ship.
2. **The land hooks into `review_verdict()` on `approved`** — the single place the system declares a task's work good, already atomic with the status update, and per-task rather than per-chain. Validation: review 179 approved #280 at 11:37 on 2026-09-11, which is exactly when `main` should have moved to `1d6642b`; instead a human moved it at 13:17. For `owner_approval` tasks the verdict yields `manual`, so the land defers to `wm task close` and the owner still gates.
3. **A land that is not a clean fast-forward refuses and surfaces.** Never force, never merge-commit, never rebase. An automatic merge or rebase produces a tree nobody reviewed — the exact failure class this plan exists to remove — and a rebase additionally rewrites the approved SHA, voiding the ancestry proof the whole design rests on.

## Start-point precedence

Not "the parent's branch" — that phrasing hides two traps. The rule is:

```
1. task is in `rework`        -> this task's OWN latest branched run
2. else exactly ONE completed parent with a branch -> that branch
3. else                       -> main   (today's behaviour, unchanged)
```

**Trap 1 — "latest run" is not "latest branched run."** Task #280's latest run is 567 with `branch=None` (a reviewer run). A naive lookup finds no branch, falls back to `main`, and the fix silently does nothing while appearing to work. `get_task_last_run()` currently dodges this only because it filters `review_id IS NULL`; that is luck, not contract, and must be pinned by a test.

**Trap 2 — rework is the bigger half of the ping-pong.** On `changes_requested` the task re-runs from a fresh worktree off `main`, so the coder loses its own prior work every cycle and starts from scratch. That is a strong candidate for why #280 burned six runs. A rework re-run must resume its own branch — branching it from the parent would be just as wrong as branching it from `main`.

**Multiple parents refuse rather than guess.** `task_deps` is many-to-many; with two branched parents there is no defensible pick, so fall back to `main` **and say so in the brief**, so the reviewer knows it is not looking at a build.

## Scope

- **A.** Start-point precedence in `run_workdir()`, with the chosen branch recorded on the run and the reason named in the brief.
- **B.** ff-only land in `review_verdict()` on `approved`: fast-forward the project's primary repo `main` to the approved run's branch. Deferred to `wm task close` for `owner_approval` tasks. Gated on `wm_meta` key `code_land`, default `"1"` — the same pattern as the existing `code_worktree` gate, so there is one config flip to stop it.
- **C.** Land failure path: `main` untouched, branch kept, task → `needs_attention`, notification carrying the reason.

## Out of scope

The push itself (agents run as uid 10000 with no git credentials — separate open debt); changing the rule that agents never merge to `main`; back-filling the ~20 orphan `wm/*` branches already in personal-brand; retro-landing any past task.

## Files likely involved

| file | change |
|---|---|
| `core/wm_dispatch.py` | `run_workdir()` start-point precedence; brief-visible reason |
| `core/wm_store.py` | `review_verdict()` land hook; `wm task close` deferred land; brief text |
| `core/worktree_gc.py` | compatibility check only — branches become merged |
| `tests/core/test_t7.py` | existing worktree isolation checks (9) must still pass |
| `tests/backend/` | new: start-point precedence, land, land refusal |

## Proof (2026-09-11)

**End-to-end replay of the chain shape that broke**, on a scratch repo — BUILD v1 → `changes_requested` → BUILD v2 → approve → REVIEW:

```
BUILD v1 on wm/run-1
verdict -> task rework | main still 6ddb033          (a rejected review never lands)
BUILD v2 branched from wm/run-1 <- resumes its own work
  v2 sees v1's content: True                          (Trap 2 fixed)
approved -> task done | main now 2de594e == build 2de594e
REVIEW branched from wm/run-2
  review contains the build commit: True              (THE FIX)
  review sees: 'v2 final'
RESULT main==build: True | review has build: True
```

**Suite:** `tests/backend` 544 → **565**, 0 failures (`pytest tests/backend -q`). `tests/core`: 9 of 10 scripts pass; `test_run_wrapper_orchestrator.py` fails **identically at HEAD** (33 passed / 2 failed before and after — the installed Hermes CLI exposes an `orchestrator` profile), so it is environmental and pre-existing, not a regression.

**`wm check`:** `Integrity OK — no anomalies`, exit 0. This mattered: the first cut set a refused land to `needs_attention`, which is a PROPOSAL classification and NOT in `TASK_STATUSES` — precisely what the integrity audit exists to catch. It uses `blocked`.

**GC compatibility (Challenge 4):** verified empirically after a land — `git branch -d` still refuses a branch in use by a worktree, and still refuses an unmerged branch. Landing only makes already-safe branches eligible once their worktree is gone. No safety property weakened.

**A real accident, recorded because it nearly reached the owner's data:** the first run of `test_worktree_start_point.py` created `run-1`..`run-4` in the LIVE `/opt/data/hermes-hq/runs/worktrees/`, pointing at pytest tmp repos. `resolve_runs_dir()` falls back to a module-level `RUNS_DIR` computed at IMPORT time, so a fixture setting `HERMES_HQ_HOME` moves the database and nothing else — the same hazard `conftest.py` documents for `WM_DB`. The strays were removed (live worktrees back to the 13 real ones, no stray branches in any real repo) and the fixture now sets `WM_RUNS_DIR`, with the reason written into its docstring.

## Acceptance checks

1. A REVIEW task whose parent BUILD has a branch gets a worktree whose HEAD has the **build commit** as an ancestor. (Today: sibling. This is the fix.)
2. A parent whose latest run has `branch=None` still resolves to its latest **branched** run — Trap 1.
3. A rework re-run starts from its own previous branch — not `main`, not the parent — Trap 2.
4. Two completed branched parents → falls back to `main` **and the brief says so**.
5. `review_verdict(approved)` fast-forwards the primary repo's `main` to the run branch.
6. `owner_approval` task: the verdict yields `manual` and `main` does **not** move; it moves on `wm task close`.
7. `main` moved under it, or the primary tree dirty → `main` unchanged, branch intact, task `needs_attention`, notification raised, clean exit.
8. `code_land="0"` → no land; everything else unchanged.
9. Non-code tasks and non-git projects: unchanged from today.

## Risks and assumptions

- **Blast radius is the whole engine** — every run takes the start-point path, every approval takes the land path. Both are additive and fall back to exactly today's behaviour; both are config-gated.
- **This auto-advances hermes-hq's own `main`**, since the engine builds this repo. Intended, but new. `code_land` is the stop button.
- **GC will begin reaping now-merged `wm/*` branches** that `git branch -d` previously refused. Desirable, but it is a behaviour change in a subsystem with four proven safety properties — verify against them rather than assume.
- **Assumption to confirm in code, not by eye:** that the approved run's branch head IS the commit the reviewer read. If the coder commits again after the review starts, it is not — so acceptance check 1 pins the **commit**, not the branch.

## Verification method

A unit test per acceptance check, plus — per this repo's standard — a **negative control for each of the three guards** (ff-only, `code_land`, multi-parent refusal), each proven to FAIL when its guard is removed. The precedent is the notification-dedupe work, where the first three tests passed against deliberately broken code.

Then a live end-to-end replay of today's chain shape on a scratch repo: BUILD → REVIEW → INTEGRATE, asserting the review's HEAD contains the build commit and `main` fast-forwards exactly once.

## Specialist

Opus builder + Opus reviewer, per the standing subagent build cycle.

## Shipped

| file | what |
|---|---|
| `core/wm_dispatch.py` | `resolve_start_point()`; `run_workdir()` passes a start-point and returns `(workdir, branch, base_branch, base_reason)`; falls back to the primary HEAD and RECORDS it when a start-point is unusable (GC may have pruned the branch) |
| `core/wm_store.py` | `runs.base_branch` + `runs.base_reason` (schema + idempotent migration); `get_task_last_branched_run()`; `_run_col()`; `land_approved_run()` + `_land_and_report()`; land wired into `review_verdict()` and `close_by_owner()`, gating promotion; brief names the base and demands an ancestry check |
| `tests/backend/test_worktree_start_point.py` | 10 tests, 2 negative controls |
| `tests/backend/test_land_on_approval.py` | 11 tests, 1 negative control |

## Still open after this

**Agents still cannot push to any remote** (uid 10000, no credentials), so an INTEGRATE task still ends `blocked` at the push itself. Untouched by this plan, still on the Known-debt list. What changed is that `main` now CONTAINS the reviewed work when it gets there, so the push is a one-line owner action rather than a manual fast-forward plus a push.
