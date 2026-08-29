# State

## Status
**hermes-hq is the live control plane since 2026-08-29 13:40 UTC.** `hermes-hq serve --host 0.0.0.0 --port 9010 --interval 20` with the dispatcher ON (log `/opt/data/hermes-hq-serve.log`, password `/opt/data/hermes-hq/password`). Old WM crons paused (not deleted): `dfe30ff9e8bf` wm-dispatch, `040334fe79ae` wm completion watchdog, `b84db989076d` wm-planning-pickup. Rollback = `hermes cron resume <id>` ×3 and stop hermes-hq; old `/opt/data/work-manager/` untouched. Legacy dashboard :9009 still up but stale.

## Now
Task: **Group 3a** (plan approved 2026-08-29, `kis/intent/Group3Plan.md`). Order: stop-run → templates extract → agents API → gateway supervisor → Agents UI.
Ledger: [x] stop-run (done 2026-08-29, see Proof) · [x] templates extract (done 2026-08-29) · [x] agents API (done 2026-08-29) · [x] gateway supervisor (done 2026-08-29, supervisor-agnostic: service first, else owned `gateway run` child) · [ ] Agents UI.
Verification (rest of 3a): pytest (install on scratch HERMES_HOME, gateway supervisor with fake process), real gateway start for coder on this box, Playwright.

## Next
Group 3b Chat (SSE proxy + Chat page), then Group 4 direct chat scopes.

## Blocker
None.

## Known debt
- `mark_stalled` prefixes the transition detail with `liveness:` even for an owner stop (history shows "liveness: stopped by owner"); cosmetic, fix when touching the engine.
- `runs/<id>.log` holds only the wrapper's lines; agent transcript is in the Hermes session → Group 3 chat view is where "watch it work" really lands.
- Re-check every new page at 390px; `html/body` have an overflow-x guard but root causes (missing `min-w-0`) must still be fixed per page.
- Snapshot-mode writes were discarded by the cutover import (owner's replies on #84/#96 must be redone on the live side if still wanted).
- The server process must be restarted by hand after backend changes (`pkill -f 'hermes-hq serve'` then the serve line above) — no supervisor yet; add a systemd/s6 unit in the install work.
- `IdleSweeper` runs only when the dispatcher is enabled (`--no-dispatcher` dev mode never idle-stops gateways).
- Cookie session over plain HTTP; HTTPS via reverse proxy is a later item.
- `readers.py` is a straight port (1100 lines) incl. agents/sessions/files/overview readers not yet exposed; prune or expose as Groups 2–5 need them.
- `tests/core/test_t2/t5/t7.py` fail identically in the source repo (goal lifecycle draft→planned changed after they were written). Not caused by the move; fix when touching goal release.

## How to run
See `README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd frontend && npm run dev` (proxies /api to :9010). Legacy WM dashboard still live on :9009 and untouched. Owner drops reference images in `screenshots/` (git-ignored).

## Proof (latest)
- Gateways 2026-08-29 (re-proved after the supervisor-agnostic refactor: service path on this box enable 3–4s / disable <1s, `spawned pid`=0; no-service path covered by `test_spawns_gateway_when_no_service`, 31 passed): `pytest tests/backend` 30 passed (3 new in `test_gateways.py` with a fake `hermes` whose `gateway start` serves `/v1/models`: enable appends PORT/KEY marked `# hermes-hq` leaving owner lines intact, idempotent, disable stops, activity `gateway_start/stop`; idle sweep after 15 min; `ensure_running`; `stop_started` on exit; refusals for default/unknown/not-installed/not-enabled). Real on this box: `set_enabled("coder", True)` → `.env` +2 lines, s6 `gateway-coder` PID up in 3.8s, `:8653` LISTEN, `/v1/models` with the key returns model `coder`; `set_enabled(False)` → port closed, no gateway process, owner `.env` lines byte-identical. Stray `gateway-probe` s6 slot from the earlier scratch profile unregistered via Hermes' own `_maybe_unregister_gateway_service`.
- Agents API 2026-08-29: `pytest tests/backend` 27 passed (5 new in `test_agents.py` with a fake `hermes` shim: list/templates, install layers SOUL+skill and keeps CLI skills, 409 on exists/bad name/CLI failure, overlay with backup + force, ask-orchestrator task). Real CLI on `/opt/data/hh-scratch`: `install("coder")` → `hermes profile create` ran, SOUL `# Coder`, 16 skills incl. `coder-specialist`, second install refused, orchestrator overlay applied with backup, `hermes profile list` shows coder. Live server restarted; `/api/agents` returns 7 installed agents + 7 templates, overlay_applied=false on root.
- Templates 2026-08-29: `scripts/extract_agent_templates.py` → 6× ok; `cmp` verbatim vs `/opt/data/profiles/*/{SOUL.md,skills/*-specialist/SKILL.md}`; re-run checksums identical; `pytest tests/backend` 22 passed (2 new in `test_templates.py`: every assignee has a well-formed template, extractor idempotent + never copies `.env`). Probe on scratch `HERMES_HOME=/opt/data/hh-scratch`: `hermes profile create probe --no-alias --description …` writes profile.yaml + stock SOUL + 14 bundled skills, no config.yaml.
- Stop-run 2026-08-29: `pytest tests/backend` 20 passed (3 new in `test_stop.py`: sh+sleep group killed, run failed "stopped by owner", task manual / ready with keep_in_queue, 409 when nothing running, dead-pid case). Scratch serve on :9011 + Playwright: Task detail 1440/390 shows Stop / Stop & re-queue while running, `scrollWidth==390`; clicking Stop → engine manual, run FAILED, activity `task_stopped→task_stalled→task_manual`, `sleep` group gone. Live server restarted (route deployed), health 200.
- Cutover 2026-08-29 13:40: `import --force` (15/23/98/211/84 rows, backup kept); first tick claimed review 84 → run #212 reviewer with session id captured → rework → coder run launched; `last_error: null`. Old worktrees symlinked (49) after a false rejection traced to uncopied worktree paths.
- Group 2: `pytest tests/backend` 17 passed (overview stats, unified activity + paging + task filter, run-log incremental tail). Playwright 1440/390: Overview tiles + Needs-you + Working/Queued + feed, Activity page, Task detail log; `scrollWidth == 390` on /, /activity, project and task detail after the overflow fix.
- Stop #98: engine calls `set_paused → mark_stalled(215) → mark_manual(98) → set_project_archived(wm-dashboard) → set_paused(False)`; afterwards 0 running runs, 0 open reviews, no coder processes.
