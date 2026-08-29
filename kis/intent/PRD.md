# hermes-hq — PRD (v1)

## v1 success
On a fresh server with Hermes installed: one command installs hermes-hq → add agents from templates in the UI → create project + task → task runs in a real session you can open → status correct in the 5-state model → anything stuck appears under "Needs you" with a working unblock action → reviewer gate runs automatically. Legacy `wm.db` imported with history intact.

## Feature groups (in build order)
1. **Work core** — Projects → Goals → Tasks → Runs, deps, release gate, dispatcher, completion contract, owner feedback → rework.
2. **Status & unblock** — Overview led by "Needs you", task detail with live run log, Reviews queue, Activity timeline, retry.
3. **Agents** — list via `hermes profile list`, add from `agents.yaml` templates, agent detail (runs/sessions), open/resume task session as chat.
4. **Direct chat** — start/resume with default profile or any agent; global, per project, or per task (resume the task's previous session).
5. **Project files** — browse/edit, project-scoped or global.
7. **Schedules** — recurring tasks.
6. **Browsers** — terminal, memory, skills, MCP (so Hermes dashboard isn't needed).

Out of scope: multi-user, RBAC, messaging channels, secrets vault, integrations factory.

## Constraints
Single owner, password auth; Linux next to Hermes; responsive for phone over LAN/Tailscale (PWA later); no API keys stored; engine rules in `knowledge/technical.md`.

## Stage
Prototype → daily driver once groups 1–3 land.
