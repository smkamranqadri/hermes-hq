# Group 3 — Agents + Chat (approved 2026-08-29)

Decisions: chat via the Hermes gateway HTTP API with SSE streaming (like hermes-workspace); one gateway per profile, **managed lazily by hermes-hq** (ports 8650+, keys generated into the profile `.env` only when chat is enabled, idle-stop 15 min); templates extracted from the six live profiles into `agents/` and installed through `hermes profile create`; "Stop run" ships first.

## 3a — Stop run, agents, templates, gateways — ACTIVE
- [x] DONE 2026-08-29 — `POST /api/task/{id}/stop?keep_in_queue=0|1` (`backend/stop.py`): holds the dispatch flock instead of toggling pause, `killpg` wrapper session (SIGTERM→SIGKILL, 5s grace), `mark_stalled("stopped by owner")`, then `mark_manual` or (`keep_in_queue`) `retry_task`→ready; a review run also sets its review failed; activity `task_stopped`. Task detail: Stop / Stop & re-queue while running.
- [x] DONE 2026-08-29 — `agents/<name>/{agent.yaml,SOUL.md,skills/<name>-specialist/SKILL.md}` for the six specialists, extracted verbatim by `scripts/extract_agent_templates.py` (idempotent; re-run after editing a live SOUL). `agents/orchestrator/` is a hand-written overlay (`overlay: true`, no skills) derived from the team block; the live root SOUL.md is still stock Hermes — applying the overlay is a later, explicit step. `agent.yaml` = name/description/soul/skills/overlay; no model/config (all six share the root model block).
- [x] DONE 2026-08-29 — `backend/agents.py`: `GET /api/agents` (installed + template + `.env` gateway `configured/port`, `running` filled by the supervisor later), `GET /api/agents/templates`, `GET /api/agent/{name}` (runs + sessions), `POST /api/agents/install {template, force?}` (real `hermes profile create --no-alias --description` under root HERMES_HOME, then SOUL + specialist skill layered; 409 if exists/bad name/CLI failure), **orchestrator template = overlay applied to root `SOUL.md` with `SOUL.md.bak-<ts>` backup** (owner-requested option; `force` re-applies), `POST /api/agents/ask-orchestrator {template, project}` files a task for the Orchestrator.
- [x] DONE 2026-08-29 — `backend/gateways.py` — **revised 2026-08-29 (pre-flight):** hermes-hq runs inside the Hermes s6 container, which already registers a supervised `gateway-<profile>` slot per profile (`hermes gateway run` refuses a second instance). So hermes-hq does not spawn gateway children: it ensures `API_SERVER_PORT`/`API_SERVER_KEY` in the profile `.env` (lines marked `# hermes-hq`; ports analyst 8650 … reviewer 8655), drives s6 through `hermes --profile X gateway start|stop`, checks health with `GET /v1/models` + bearer key, idle-stops (15 min since last chat use) from a small sweeper thread, and on serve exit stops the specialist gateways it started. Default profile = existing `:8642` + key from `$HERMES_HOME/.env`, never written or stopped. `POST /api/agent/{name}/gateway {enabled}`; `GET /api/agents` shows `gateway.running`.
- UI: Agents page (cards + Add from template), Agent detail (runs, sessions, gateway toggle).

## 3b — Chat — NEXT
- `POST /api/chat/{profile}` `{message, session_id?}` → SSE proxy (`stream:true`, `X-Hermes-Session-Id`), returns session id in a header/first event; `GET /api/agent/{name}/sessions`, `GET /api/session/{profile}/{id}` (transcript from `state.db`).
- Chat page: agent picker, session list, streaming transcript, resume; "Open session" from Task detail (disabled while the task is running — the agent's own turn would interleave); "Chat about this project" from Project detail.

## Acceptance
- Stop: processes gone, run failed with note, task manual/stalled, activity written.
- Agents API lists installed + templates with gateway state; install on a scratch HERMES_HOME creates the profile via the real CLI with SOUL + skill present.
- Enable chat for coder: `.env` gains PORT/KEY, gateway child healthy through hermes-hq; disable stops it; no orphans after serve exit.
- Chat streams tokens; second message resumes the same session; transcript from `state.db` matches; a done task's session can be continued.
- pytest with a fake SSE gateway; Playwright 1440/390: Agents, Agent detail, Chat streaming, Task→session.

Out of scope: files/terminal/memory/skills/MCP, schedules, multi-user, gateway pools, voice.
