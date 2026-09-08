# Scratch kit — build, verify and review a slice without touching the live server

Owner-decided flow (2026-09-08) for multi-slice UI/backend work on hermes-hq. The orchestrator
plans in `kis/`, subagents build and review, the orchestrator merges and deploys. Nothing here
touches `/opt/data/hermes-hq` (live data) or port 9010.

## Roles and models

| role | who | model | notes |
|---|---|---|---|
| plan + KIS + merge + deploy | orchestrator | — | owns `kis/`; subagents never edit it |
| small slice (1–3 files, no schema) | orchestrator directly | — | cheaper than a cold-start agent |
| large slice | builder subagent | **Opus** | Sonnet builds needed 2–3 review rounds each; Opus is cheaper overall |
| review | reviewer subagent | **Opus** | fresh agent, never the author; tags Critical/Important/Minor |
| live check after deploy | reviewer (resumed) or a small agent | Opus/Sonnet | read-only against the real library at 390×844 |

Two slices may run in parallel when they touch different files; anything sharing
`BrainLibrary.tsx` / `brain.tsx` / the tail of `list_notes` runs sequentially.

## Builder recipe (one worktree per slice)

```bash
# orchestrator
git worktree add .claude/worktrees/<slice> -b polish/<slice> main
# builder, inside the worktree
ln -s /opt/data/projects/hermes-hq/frontend/node_modules frontend/node_modules   # no npm ci per worktree
cd frontend && npm run build && cd ..                                            # tsc -b && vite build — never pipe through tail
/opt/data/projects/hermes-hq/.venv/bin/python -m pytest tests/backend -q         # the only sanctioned suite invocation
/opt/data/projects/hermes-hq/tools/scratch/serve.sh . 90NN <slice>               # own command, NO pipe (it hangs the caller)
HERMES_HQ_HOME=/opt/data/hh-scratch/<slice> /opt/data/projects/hermes-hq/.venv/bin/python \
  /opt/data/projects/hermes-hq/tools/scratch/seed.py http://127.0.0.1:90NN       # deterministic library, prints ids
node check.mjs                                                                   # uses pw.mjs, see below
/opt/data/projects/hermes-hq/tools/scratch/stop.sh 90NN <slice>                  # kills the server, deletes the data dir
```

`serve.sh` runs `python -m backend.cli` with the worktree as cwd. The console script `hermes-hq`
imports `backend`/`core` from the LIVE checkout (editable-install `.pth`), which is how a builder
once verified live code while believing it was testing its branch. The script prints a bundle
check: the served `index-*.js` must equal the one in the worktree's `backend/static/index.html`.

Ports: 9011–9019 are scratch; 9010 is live. Data dirs: `/opt/data/hh-scratch/<name>` (the `hermes`
shim can only read under `/opt/data`, so never the session scratchpad).

## Verification with `pw.mjs`

```js
import { open } from '/opt/data/projects/hermes-hq/tools/scratch/pw.mjs'
const s = await open('http://127.0.0.1:9013', { shots: '/tmp/claude-0/<session>/scratchpad/shots' })
await s.goto('/brain/library?area=11')
await s.shot('work-active')            // numbered PNG in shots/
await s.assertWidth()                  // throws unless scrollWidth === 390
s.lastApi('/api/notes?')               // the last /api request matching a fragment
await s.exact('Work').click()          // exact-text locator — plain text= substring-matches
const { errors } = await s.close()     // errors must be 0
```

Playwright is installed once at `/opt/data/hh-scratch/pw`; the browser is the shared
`ms-playwright/chromium-1234`. Never `npm i playwright` per worktree or per session.

Under `isMobile`, a click on a row inside a fixed bottom sheet is occasionally "intercepted" by
a sibling — retry or probe, it is not a layout bug. After a react-query invalidation, assert the
settled state rather than the first paint.

## Review recipe

Reviewer gets: the branch, the base, the spec (owner decisions verbatim, marked "do not
relitigate"), the sibling branch to merge-tree against, and the specific things to trace
(transactions, exclusion chains, query keys). Output: findings with file:line and a one-line fix,
verdict MERGE / MERGE AFTER FIXES / DO NOT MERGE. Critical and Important go back to the same
builder (resume it — context intact); Minors are bundled into the same round. One re-review of the
fix commit only, then merge.

## Deploy (orchestrator)

```bash
git merge --ff-only polish/<slice>              # or a merge commit when only kis/ diverged
.venv/bin/hermes-hq wm backup                   # before every backend deploy
cd frontend && npm run build && cd ..           # live immediately, no restart
.venv/bin/hermes-hq service restart             # backend changed only; wipes login sessions
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9010/api/health   # 200
.venv/bin/hermes-hq wm check                    # Integrity OK
git worktree remove .claude/worktrees/<slice> && git branch -d polish/<slice>
```

After a backend restart the owner's phone keeps the OLD bundle until the Home Screen app is fully
closed and reopened (the login screen appears inside the already-loaded SPA). Say so in the
deploy report. Auto-reload was offered and declined (2026-09-08).

Then a read-only live check on the real library (counts before/after must match), and a KIS sync:
ledger tick in `kis/state/current.md`, one Proof line, durable rules to Knowledge.
