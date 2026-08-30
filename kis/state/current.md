# State

## Status
**hermes-hq is the live control plane since 2026-08-29 13:40 UTC** (branch `main`). Process: `nohup .venv/bin/hermes-hq serve --host 0.0.0.0 --port 9010 --interval 20 > /opt/data/hermes-hq-serve.log 2>&1 &` with the dispatcher ON; restart by hand after backend changes (`pkill -f 'hermes-hq serve'` in its own command, then that line). Password file `/opt/data/hermes-hq/password` is `test` for the owner's review (generated one kept in `password.prev`). Frontend builds go live without a restart. HTTPS for the phone: `tailscale serve` on the owner's Mac fronts this VM's `:9010` (Knowledge → Web Push). Legacy WM rollback recipe: `knowledge/technical.md` → Legacy WM.

## Now
Task: **Group 5 — Project files** (browse/edit, project-scoped or global) — not planned yet; start with `/kis:plan`. Inputs: `PRD.md` item 5, hermes-workspace's file explorer (lineage allow-list in `knowledge/project.md`), Tools menu already has a `/files` placeholder route.
Groups 1–4 and 4b (chat polish, option cards, notifications incl. Web Push, mobile IA) are complete as of 2026-08-30 (`intent/Group4bPlan.md`).

## Next
Group 5 project files (plan first), then Group 6 browsers, Group 7 schedules per PRD.

## Blocker
None.

## Known debt (open items only; accepted limits live in Knowledge → Known limits)
- Agent detail history shows 30 rows + "Show more" (40 at a time) over the reader's 120-row cap; the cap itself is unchanged.
- Specialist `.env` files carry the root `API_SERVER_KEY` (template-inherited); per-profile keys are an owner choice.
- `mark_stalled` prefixes the transition detail with `liveness:` even for an owner stop; cosmetic.
- `runs/<id>.log` holds only the wrapper's lines; the agent transcript is the Hermes session (Chat).
- Every new page must be checked at 390 px with mobile emulation (tab bar, safe areas, 16 px fields); overflow causes (missing `min-w-0`) are fixed per page.
- Snapshot-mode writes were lost at cutover; #84 re-fed live (run 217 carried the feedback), #96 not yet if still wanted.
- The orchestrator SOUL overlay is not applied to the live root profile (button on Agents); option cards don't depend on it any more.
- Dispatched task runs (CLI sessions) don't get the hq-options hint; a "Needs you" prompt for a run that asks a question is not built.
- Task detail is long on phones (all blocks stacked); Activity has no "load more".

## How to run
`README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd frontend && npm run dev`. Owner drops reference images in `screenshots/` (git-ignored). Playwright: settings in memory `hermes-hq-ops` (browser path, 390×844 + `isMobile` for phones); scripts are per-session.

## Proof (latest, one line per shipped area — details are in git history)
- Suite: `tests/backend` **51 passed** (2026-08-30).
- Web Push 2026-08-30: owner's iPhone (Home-Screen app over Tailscale https) — `POST /api/push/test` → `{subscriptions: 1, delivered: 1}` after switching the VAPID contact off `@localhost` (Apple 403 BadJwtToken before). Fake-push-service pytest covers aes128gcm body + VAPID header + 410 pruning.
- Notifications 2026-08-30: transitions → bell/Inbox rows (no backfill), chat replies recorded server-side per finished turn and marked read by the watching device (two-browser proof: watcher unread 0, away → phone badge 1), browser alerts + chime with stubbed APIs.
- Phone IA 2026-08-30: floating pill tab bar (Overview · Tasks · Chat · Inbox · More), top row logo · LIVE, safe-area insets, opaque menus, 16 px fields, chat card measured to the visible viewport (keyboard case 533/544), sessions bottom sheet; desktop unchanged. All 11 pages screened at 390.
- Chat 4b-1…4b-4 2026-08-30: markdown/tool cards/thinking, rename/pin/delete/search/export via gateway, attachments as image parts ("Green"), model/provider/effort from Hermes' own sources, slash commands, steer (accepted), draft recovery, option cards (orchestrator emitted the block; click → reply).
- Group 4 2026-08-30: project/task-scoped chats with seeded brief, resume-first, unique titles.
- Cutover 2026-08-29 13:40: `import --force`, first tick claimed review 84 → run #212. Earlier group proofs live in git history of `kis/state`.
