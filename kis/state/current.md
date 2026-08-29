# State

## Status
**hermes-hq is the live control plane since 2026-08-29 13:40 UTC.** `hermes-hq serve --host 0.0.0.0 --port 9010 --interval 20` with the dispatcher ON (log `/opt/data/hermes-hq-serve.log`, password file `/opt/data/hermes-hq/password` — set to `test` for the owner's review on 2026-08-29, previous generated one kept in `password.prev`). Frontend builds are picked up live (`index.html` served `no-cache`); backend changes need the manual restart below. Latest commit at sync: `5168a08`. Old WM crons paused (not deleted): `dfe30ff9e8bf` wm-dispatch, `040334fe79ae` wm completion watchdog, `b84db989076d` wm-planning-pickup. Rollback = `hermes cron resume <id>` ×3 and stop hermes-hq; old `/opt/data/work-manager/` untouched. Legacy dashboard :9009 still up but stale.

## Now
Task: **Group 4 — direct chat scopes** — not planned yet; start the next session with `/kis:plan`. Inputs: `PRD.md` Group 4, the deferred "Chat about this project" (Project detail → new orchestrator session seeded with a project brief), and the Chat proxy/session API already in place (`backend/chat.py`).
Groups 1–3 (incl. 3c live-run highlighting) are complete as of 2026-08-29.

## Next
Group 4 direct chat scopes (project-scoped chat with the orchestrator, seeded brief), then Group 5 per PRD.

## Blocker
None.

## Known debt
- Agent detail history is one unified list capped at 120 rows with no paging/"show more"; add when it gets long.
- Specialist `.env` files carry the root `API_SERVER_KEY` (template-inherited); fine on loopback, per-profile keys are an owner choice.
- `mark_stalled` prefixes the transition detail with `liveness:` even for an owner stop (history shows "liveness: stopped by owner"); cosmetic, fix when touching the engine.
- `runs/<id>.log` holds only the wrapper's lines; agent transcript is in the Hermes session → Group 3 chat view is where "watch it work" really lands.
- Re-check every new page at 390px; `html/body` have an `overflow-x: clip` guard (not `hidden` — that breaks sticky) but root causes (missing `min-w-0`) must still be fixed per page.
- Snapshot-mode writes were discarded by the cutover import; #84 was re-fed on the live side (run 217 carried the OWNER FEEDBACK), #96 not yet if still wanted.
- Orchestrator chat via `:8642` is covered by the proxy test only; not yet exercised through the UI. The orchestrator SOUL overlay (`agents/orchestrator`) is still not applied to the live root profile (button on the Agents page).
- The server process must be restarted by hand after backend changes (`pkill -f 'hermes-hq serve'` then the serve line above) — no supervisor yet; add a systemd/s6 unit in the install work.
- `IdleSweeper` runs only when the dispatcher is enabled (`--no-dispatcher` dev mode never idle-stops gateways).
- Cookie session over plain HTTP; HTTPS via reverse proxy is a later item.
- `readers.py` is a straight port (1100 lines) incl. agents/sessions/files/overview readers not yet exposed; prune or expose as Groups 2–5 need them.
- `tests/core/test_t2/t5/t7.py` fail identically in the source repo (goal lifecycle draft→planned changed after they were written). Not caused by the move; fix when touching goal release.

## How to run
See `README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd frontend && npm run dev` (proxies /api to :9010). Legacy WM dashboard still live on :9009 and untouched. Owner drops reference images in `screenshots/` (git-ignored).

## Proof (latest)
- 2026-08-29 owner review round (all live, Playwright 1440/390): sticky top bar with real glass (`backdrop-filter` restored — the minifier kept only `-webkit-`, which Chrome ignores; unprefixed declaration must come last), chat transcript scrolls inside a viewport-height card and auto-scrolls to the bottom, Agent detail unified History (title = session → Chat, `Task #n` pill, `wm-run-<id>` marker fallback; coder 82 rows / 0 unmatched), Overview feed capped at 10, task page patches its cache from write responses and polls 3 s while in motion.
- Engine bug fixed 2026-08-29 (found via task #84): `latest_owner_feedback` only returned the comment while status was `rework`, but the dispatcher claims (→ `running`) before `render_brief`, so owner feedback never reached a rework brief (1 of 216 briefs had an OWNER FEEDBACK section vs 45 with review comments). Now also accepted when the last two transitions are `running` after `rework`. Regression test `tests/backend/test_owner_feedback_brief.py`; suite **38 passed**. Verified live: run 217's brief for #84 contains the feedback + repo URL.
- Group 3c live-run highlighting 2026-08-29: `/api/system` running/cap, overview `slots_used`, agents `live[]`, session `live_run`; scratch instance (cap 1, fake live run, real `state.db` schema) proved the top-bar pill, Working tile, "waiting for a free slot", pulsing badges, Agent "Running now" + Watch log, Chat live banner with composer hidden, at 1440/390.
- Group 3b Chat 2026-08-29: proxy (fake-gateway pytest: 409 when disabled, SSE pass-through with tool events, key never leaks, idle touch, stop, 502 mapping) + real coder turn (`pong`, 6.5 s; transcript in `state.db`); Chat page real streaming turn with mid-turn Stop; Task #98 → Open session continued the reviewer's CLI-created session ("I worked on task #98."). Gateways enabled for proofs were disabled afterwards.
- Group 3a 2026-08-29: Stop run (fake sh+sleep wrapper killed as a group; manual / re-queue paths), templates extracted verbatim + idempotent, agents API with real `hermes profile create` on `/opt/data/hh-scratch` + orchestrator overlay with backup, gateways supervisor-agnostic (service path proved live on coder `:8653`; no-service spawn path in tests), Agents UI with real Enable/Disable chat.
- Cutover 2026-08-29 13:40: `import --force`, first tick claimed review 84 → run #212 → rework → coder run; old worktrees symlinked (49). Group 2 and the #98 stop proofs are in git history (`kis/state` before `2d25748`).
