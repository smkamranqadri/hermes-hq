# hermes-hq — Project Knowledge

## What
A portable control plane for a Hermes Agent multi-agent team. Owner creates projects and tasks, agents run them in real Hermes sessions, owner watches status and unblocks. Installs with one command on any server where `hermes` is already set up.

## Who / why
Single owner: Kamran Qadri, running an Orchestrator + specialist agents (analyst, writer, marketer, coder, uiux, reviewer) as Hermes profiles. Previous attempts (Hermes kanban, hermes-work-manager) were rejected or stalled because: no real chat session per task, task status hard to read, weak project grouping, UI slow to build, install not portable.

## Lineage (what was taken from where — nothing else is copied)
| Source | Taken | Rejected |
|---|---|---|
| `../hermes-work-manager` (owner's, Python/SQLite, live v0.9.0 at :9009) | Engine: `wm_store`/`wm_dispatch`/`wm_run_agent`, completion contract, review gate, task/goal state machine, `design/IA_FLOWS.md` top-bar IA, live `wm.db` history | vanilla-JS dashboard, `deploy.sh` + Hermes-cron install, 11-status UI |
| `../hermes-workspace` (upstream clone, TanStack/React 19/Tailwind 4) | Design language + components, overview layout, one-command `install.sh` pattern, `swarm.yaml`-style agent roster, chat/terminal/memory/skills/MCP browsers | sidebar shell (→ top bar), Electron, 3D world, swarm mode, gateway-centric agent model |
| `../hermeshq` (upstream clone, FastAPI/Postgres) | `agent_supervisor` patterns: durable queue, stream buffer, WebSocket event broker, schedules | Postgres, Docker-first, RBAC/users, channels, secrets vault, integration factory |

## Scope
In v1: work core, status & unblock, agents, direct chat, project files, terminal/memory/skills/MCP browsers, schedules. Out: multi-user, RBAC, Telegram/WhatsApp, secrets vault, integrations factory.
