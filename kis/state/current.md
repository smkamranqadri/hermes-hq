# State

## Status
**hermes-hq is the live control plane since 2026-08-29 13:40 UTC** (branch `main`). Process: `nohup .venv/bin/hermes-hq serve --host 0.0.0.0 --port 9010 --interval 20 > /opt/data/hermes-hq-serve.log 2>&1 &` with the dispatcher ON; restart by hand after backend changes (`pkill -f 'hermes-hq serve'` in its own command, then that line). Password file `/opt/data/hermes-hq/password` is `test` for the owner's review (generated one kept in `password.prev`). Frontend builds go live without a restart. Legacy WM rollback recipe: `knowledge/technical.md` → Legacy WM. Latest commit at sync: see `git log`.

## Now
Task: **Group 4 — direct chat scopes** — not planned yet; start the next session with `/kis:plan`. Inputs: `PRD.md` Group 4, the deferred "Chat about this project" (Project detail → new orchestrator session seeded with a project brief), and the Chat proxy/session API already in place (`backend/chat.py`).
Groups 1–3 (incl. 3c live-run highlighting) are complete as of 2026-08-29.

## Next
Group 4 direct chat scopes (project-scoped chat with the orchestrator, seeded brief), then Group 5 per PRD.

## Blocker
None.

## Known debt (open items only; accepted limits live in Knowledge → Known limits)
- Agent detail history is one unified list capped at 120 rows with no paging/"show more".
- Specialist `.env` files carry the root `API_SERVER_KEY` (template-inherited); per-profile keys are an owner choice.
- `mark_stalled` prefixes the transition detail with `liveness:` even for an owner stop; cosmetic.
- `runs/<id>.log` holds only the wrapper's lines; the agent transcript is the Hermes session (Chat).
- Re-check every new page at 390px; `html/body` use `overflow-x: clip` but real causes (missing `min-w-0`) must be fixed per page.
- Snapshot-mode writes were lost at cutover; #84 re-fed live (run 217 carried the feedback), #96 not yet if still wanted.
- Orchestrator chat via `:8642` covered by the proxy test only, not exercised in the UI; the orchestrator SOUL overlay is not applied to the live root profile (button on Agents).

## How to run
`README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd frontend && npm run dev`. Owner drops reference images in `screenshots/` (git-ignored).

## Proof (latest)
- 2026-08-29 owner review round (all live, Playwright 1440/390): sticky top bar with real glass (`backdrop-filter` restored — the minifier kept only `-webkit-`, which Chrome ignores; unprefixed declaration must come last), chat transcript scrolls inside a viewport-height card and auto-scrolls to the bottom, Agent detail unified History (title = session → Chat, `Task #n` pill, `wm-run-<id>` marker fallback; coder 82 rows / 0 unmatched), Overview feed capped at 10, task page patches its cache from write responses and polls 3 s while in motion.
- Engine bug fixed 2026-08-29 (found via task #84): `latest_owner_feedback` only returned the comment while status was `rework`, but the dispatcher claims (→ `running`) before `render_brief`, so owner feedback never reached a rework brief (1 of 216 briefs had an OWNER FEEDBACK section vs 45 with review comments). Now also accepted when the last two transitions are `running` after `rework`. Regression test `tests/backend/test_owner_feedback_brief.py`; suite **38 passed**. Verified live: run 217's brief for #84 contains the feedback + repo URL.
- Group 3c live-run highlighting 2026-08-29: `/api/system` running/cap, overview `slots_used`, agents `live[]`, session `live_run`; scratch instance (cap 1, fake live run, real `state.db` schema) proved the top-bar pill, Working tile, "waiting for a free slot", pulsing badges, Agent "Running now" + Watch log, Chat live banner with composer hidden, at 1440/390.
- Group 3b Chat 2026-08-29: proxy (fake-gateway pytest: 409 when disabled, SSE pass-through with tool events, key never leaks, idle touch, stop, 502 mapping) + real coder turn (`pong`, 6.5 s; transcript in `state.db`); Chat page real streaming turn with mid-turn Stop; Task #98 → Open session continued the reviewer's CLI-created session ("I worked on task #98."). Gateways enabled for proofs were disabled afterwards.
- Group 3a 2026-08-29: Stop run (fake sh+sleep wrapper killed as a group; manual / re-queue paths), templates extracted verbatim + idempotent, agents API with real `hermes profile create` on `/opt/data/hh-scratch` + orchestrator overlay with backup, gateways supervisor-agnostic (service path proved live on coder `:8653`; no-service spawn path in tests), Agents UI with real Enable/Disable chat.
- Cutover 2026-08-29 13:40: `import --force`, first tick claimed review 84 → run #212 → rework → coder run; old worktrees symlinked (49). Group 2 and the #98 stop proofs are in git history (`kis/state` before `2d25748`).
