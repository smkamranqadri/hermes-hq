# Group 3 — Agents + Chat (approved 2026-08-29)

Decisions: chat via the Hermes gateway HTTP API with SSE streaming (like hermes-workspace); one gateway per profile, **managed lazily by hermes-hq** (ports 8650+, keys generated into the profile `.env` only when chat is enabled, idle-stop 15 min); templates extracted from the six live profiles into `agents/` and installed through `hermes profile create`; "Stop run" ships first.

## 3a — Stop run, agents, templates, gateways — ACTIVE
- `POST /api/task/{id}/stop {keep_in_queue?}`: pause-safe kill of wrapper + child, `mark_stalled` (+ `mark_manual` unless keep_in_queue), activity row; Task detail button while running.
- `agents/<name>/{agent.yaml,SOUL.md,skills/<name>-specialist/SKILL.md}` for analyst, writer, marketer, coder, uiux, reviewer + `agents/orchestrator/` overlay (default profile).
- `backend/agents.py`: installed profiles (profiles dir + `profile.yaml` + gateway state), templates, `POST /api/agents/install {template}` (refuses if exists; real `hermes profile create --description` + copy), fallback `POST /api/agents/ask-orchestrator {template}`.
- `backend/gateways.py`: ensure `API_SERVER_PORT`/`API_SERVER_KEY` in profile `.env` (lines marked `# hermes-hq`), start `hermes --profile X gateway run` child with pinned HERMES_HOME, health `/v1/models`, idle-stop, stop-all on serve exit; default profile = existing `:8642` + key from `$HERMES_HOME/.env`. `POST /api/agent/{name}/gateway {enabled}`.
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
