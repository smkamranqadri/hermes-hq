# Group 4 — Direct chat scopes (approved 2026-08-30) — COMPLETE 2026-08-30 (Standard mode)

Decisions (owner, 2026-08-30): project/task chat can use **any agent, orchestrator preselected**; the brief is sent as the **first visible user turn** (no system-prompt seeding); sessions are **linked in `state.db`** so scopes resume; **task scope** gets the same seeded "Chat about this task" beside the existing "Open session"; **`/chat` defaults to the orchestrator**.

## Steps
1. [x] DONE 2026-08-30 Store — `chat_sessions(id, profile, session_id, project_id NULL, task_id NULL, title, created_at)` + migration; `link_chat_session`, `chat_sessions_for_project`, `chat_sessions_for_task`, `chat_session_scope(profile, session_id)`.
2. [x] DONE 2026-08-30 Briefs — `render_project_brief(project)` (name, description, primary_path, goals + status, open tasks ≤15) and `render_task_brief(task)` (task goal/DoD/project path pieces of `render_brief`); both end with "this is a conversation, not a dispatch — acknowledge in one line and wait". Keep ≤ ~2 KB.
3. [x] DONE 2026-08-30 API — `POST /api/chat/start {profile, project_id?, task_id?, title?}` → `create_session` → link row → `{id, profile, brief}` (409 chat disabled, no link row on failure); `GET /api/project/{id}/chat-sessions`, `GET /api/task/{id}/chat-sessions`; `GET /api/session/{profile}/{id}` gains `scope`. The frontend streams the brief through the existing `POST /api/chat/{profile}/{id}` so the reply is visible and Stop works.
4. [x] DONE 2026-08-30 UI — Project detail: "Chat about this project" card (agent Select, orchestrator preselected; New chat busy; linked sessions list → Resume). Task detail: "Chat about this task" + linked list. Chat page: `/chat` → `/chat/orchestrator`; scope chip (Project / Task #n links) in header and session list; New chat from Chat stays unlinked.
5. [x] DONE 2026-08-30 Tests + proof — pytest `tests/backend/test_chat_scopes.py` (fake gateway: link row, brief content, 409 → no row); live proof on :9010: orchestrator project chat, coder project chat, task chat (task status unchanged); Playwright 1440/390 Project detail, Task detail, Chat with chip; re-disable specialist gateways afterwards.

Out of scope: per-project filter on global Chat, session rename/delete/fork, system-prompt seeding, PWA, Group 5.

Risks: brief turn is a real agent turn (cost/time) — short-reply footer; long first message assumed fine (only short ones proved in 3b); orchestrator SOUL may try to "complete" a task — brief states it is not a dispatch; failed brief stream leaves a linked empty session (accepted).

## Acceptance
- New chat from Project detail creates a real session, brief is the first visible user turn, short reply, session listed under the project and on Chat with a "Project <name>" chip; Resume continues it. Same for a specialist and for a task ("Task #n" chip).
- Disabled agent → inline Enable chat, no link row. `/chat` opens on orchestrator. No key in any response. pytest green; 390px has no horizontal scroll.
