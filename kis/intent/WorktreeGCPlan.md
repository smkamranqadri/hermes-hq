# Worktree GC Plan

**Status:** **COMPLETE 2026-09-11.** Slice A (`62bd010`) cleared the backlog — 49 worktrees → 4, 75 MB → 6.5 MB, 38 branches pruned, 54 artifact links rescued. Slice B (`f4b0b5d`) shipped the standing policy: `core/worktree_gc.py` behind both the hourly dispatcher sweep and `wm gc`, with the link rescue carried INTO the reap. Suite 423 → 537. Owner-interviewed; the four decisions below are locked — do not relitigate in build tasks.
**Parent:** the long-standing Known-debt item "Worktree GC policy is still unowned", open since 2026-09-02.
**Work mode:** Standard — two slices, built direct in-session.

## Goal

Stop run worktrees accumulating without ever deleting something that mattered, and stop artifact links pointing into directories that are about to disappear.

## Measured baseline (2026-09-11, not remembered — the 2026-09-07 audit numbers were stale)

| fact | value |
|---|---|
| worktrees under `/opt/data/hermes-hq/runs/worktrees/` | **47** (75 MB) |
| owning repos | **personal-brand 46 · portfolio-site 1 · hermes-hq 0** |
| clean | **28** |
| dirty | **19** — 18 hold exactly one untracked review draft, each a DISTINCT file; 1 is run-407 |
| `wm/*` branches in personal-brand | **46**, of which **6 are unmerged** |
| **`wm/run-407` commits main lacks** | **0** |
| task `result_paths` | **420 total · 175 point into a worktree · 239 already broken** |

**Two corrections to what State recorded.** The 2026-09-07 audit said 59 worktrees across three repos with hermes-hq holding 21 — hermes-hq now has none and the count is 47. And the entry's sharpest warning — that run-407/411/412 hold "an uncommitted, unbacked-up reorganization ... the exact reorg the owner ruled out of scope in #177" — **does not hold**: `wm/run-407` carries zero commits `main` lacks, and its 69 dirty entries are uncommitted *deletions*, so dropping them leaves the files in place. That is the safe direction, not the dangerous one.

## Decisions locked in the 2026-09-11 interview

1. **The engine reaps only where there is provably nothing to lose.** A worktree whose task reached a terminal state and whose tree is CLEAN is removed automatically. Anything with uncommitted or untracked content is never touched automatically — it waits for a manual `wm gc` that parachutes the content first.
2. **Artifact links are rescued by worth, not wholesale.** On reap, a `result_paths` entry pointing into the worktree is repointed at the **main checkout** when the file exists there (the agent committed and it merged — where the link should have pointed all along). When it does not, the file is copied to a durable artifacts dir **only if the task is done**; links from failed or abandoned attempts are allowed to break, as 239 already have.
3. **The first sweep clears the whole backlog:** parachute the 18 distinct drafts, remove all 47 directories, and prune the **40 merged** `wm/*` branches, keeping the 6 unmerged.
4. **A 24-hour grace window.** Reaping runs on the dispatcher tick and only touches worktrees whose task terminated more than 24 h ago — a clean worktree holds nothing to debug by definition, but a just-failed run is exactly what the owner would open.

### Assumptions (correct at any slice gate)
- Parachutes at `/opt/data/hermes-hq/runs/parachutes/worktree-drafts-<date>.tar.gz`; rescued artifacts at `<hq_home>/artifacts/run-<id>/`.
- The 24 h clock runs off the task's terminal state transition timestamp.
- `wm gc` sits OUTSIDE the `wm note` group, like `brain-import`/`brain-export`: that group is the librarian's read-only surface and deleting directories is not the librarian's business.

## The four safety properties this plan exists to satisfy

These are the reason the slice is worth doing carefully rather than quickly. Each gets a negative control — break it, watch the test fail, restore.

1. **Path confinement.** Nothing is removed whose *resolved* path is not strictly under the worktrees root. `os.path.join` with an absolute second argument discards the base, which is precisely how the SPA fallback served the owner's password file on 2026-09-10.
2. **The clean check fails CLOSED.** An unreadable `.git`, a git error, a timeout — every one of them means "dirty", never "empty". This check is the only thing standing between the engine and someone's work.
3. **A live run is untouchable.** Guard on the task's status AND a live run pid: a task can read `running` with a dead process, and the reverse inside a tick.
4. **Iterate REPOS, not branches.** `git worktree list` from hermes-hq shows only hermes-hq's worktrees — which is how the 2026-09-07 audit saw 36 of 59 and never looked at two other repos. The owning repo comes from reading each directory's `.git` file; removal, `prune` and `branch -d` all run against THAT repo.

## Slices

### Slice A — the one-off sweep (direct in-session) — **SHIPPED 2026-09-11** ✅ (`62bd010`)
`tools/worktree_gc.py`, **dry run by default**. Walks the worktrees root, resolves each directory's owning repo from its `.git` file, classifies clean / dirty / unreadable, and prints the table for the owner to read BEFORE anything is deleted. On `--apply`: tar the distinct untracked drafts to the parachute, `git worktree remove` each directory in its owning repo, `git worktree prune` there, then `git branch -d` each `wm/*` branch — **keeping whatever git refuses**, because lowercase `-d` refusing an unmerged branch is the safety net, not an error to suppress.

**DoD:** dry run lists exactly 47 with owning repo and verdict; the parachute holds **18 distinct files** and opens; after apply — **0 directories**, `git worktree list` clean in both repos, **6 branches survive and are exactly the unmerged ones**, `wm check` Integrity OK, health 200. A `wm backup` is taken before `--apply` touches anything.

### Slice B — the standing policy (direct in-session) — **SHIPPED 2026-09-11** ✅ (`f4b0b5d`)
- **Dispatcher-tick sweep:** reap when the task is terminal, the run has no live pid, the tree is clean, and it terminated > 24 h ago.
- **`wm gc`:** owner-only, dry-run by default, `--apply` to act, parachutes dirty content before removing.
- **`result_paths` rewrite** per decision 2, one audited transaction per task.

**DoD:** a clean + terminal + >24 h worktree is reaped on a tick; a <24 h one is not; a RUNNING task's is not, proven with a live pid; a dirty one is never auto-removed; a path outside the root is refused; an unreadable `.git` counts as dirty; the rewrite moves a link to the main checkout and leaves a failed task's link broken; `wm gc` dry-run writes nothing — **proven by spying on the remove call, not by counting rows afterwards**. Suite green from the 526 baseline.

## What slice A found that the plan did not predict

1. **The tool had a silent undercount that would have destroyed work while reporting success.** Plain `git status --porcelain` **collapses an untracked directory to a single line**, so run-407 reported 7 untracked paths while holding **63 files**. The parachute would have archived 18 files and dropped 60-odd. `-uall` is mandatory anywhere a decision is made from untracked paths.
2. **The live-run guard belonged in slice A, not slice B.** It was specced as part of the standing policy; the owner flagged an agent mid-run on personal-brand and it was added before applying. It then held back **run-411 and run-412** — whose runs are `blocked`, not finished — which this very plan had written off as dead reorg leftovers. Judgement from a stale note would have deleted them.
3. **The numbers move while you plan.** 47 worktrees when the plan was written, 48 at the first dry run, 49 at apply; unmerged branches 6 → 8. **Re-measure at apply time; a plan's figures are a snapshot, never the target.**
4. **Applying A before B cost 43 artifact links.** Decision 2's rescue is slice B logic, so the sweep broke links it was meant to repoint (239 → 282 broken). Applied retroactively the same day: 54 repointed, 228 broken, better than the starting point — but the ordering was wrong and a future sweep must carry the rescue with it.

## Out of scope
A general artifact store. Reaping worktrees belonging to non-`wm/*` branches. Touching the 6 unmerged branches. Anything inside `/opt/data/projects/*` checkouts themselves.

## Risks
- The clean check is the load-bearing safety property; it must fail closed.
- 175 `result_paths` point into worktrees and the rewrite is a DB write on live data — backup first, audited per task.
- Reaping during a live run — guarded on status AND pid.
- The parachute will probably never be opened. It is cheap insurance against a judgement made by a script.
