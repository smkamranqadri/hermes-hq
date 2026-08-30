# Group 4b — Chat polish + notifications (approved in scope 2026-08-30, Phase mode)

Owner asked for the full "high" + "medium" list from the hermes-workspace comparison, plus Ctrl+V image paste, agent-asked questions with options, and notifications (no PRD group covers them; slotted here as 4b-5). Order below is the build order; each phase ships and is proved on its own. Group 5 (files) follows.

Gateway facts (from hermes-workspace code, verify live before use): rename `PATCH /api/sessions/{id} {title}`, `DELETE /api/sessions/{id}`; models `GET /v1/models`, per-turn `model` field on `chat/stream`; custom commands `GET /v1/commands`, a slash command is sent as plain text; attachments = `attachments: [{name, mimeType, data(base64)}]` on `chat/stream` (≤1 MB encoded, images downscaled client-side); context `GET /api/sessions/{id}/runtime` (`context_tokens/context_length/context_percent`); search `GET /api/sessions/search?q=`; `/v1/runs/{id}/steer` exists in the Hermes API listing but nobody has exercised it. Hermes `messages` rows already carry `tool_calls`, `reasoning_content`, `display_metadata`; `sessions` carry `pinned/archived/title/*_tokens`.

## 4b-1 Transcript rendering — DONE 2026-08-30
1. [x] DONE 2026-08-30 Markdown + GFM in assistant bubbles (react-markdown + remark-gfm; code blocks with a Copy button; no heavy highlighter — a small CSS-only mono block), user bubbles stay plain.
2. [x] DONE 2026-08-30 Tool-call cards: collapsed row → expand shows args (`tool_calls` from state.db / SSE `args`), result text, elapsed while live; reasoning stream shown as a collapsible "thinking" block (SSE `tool.progress` live, `reasoning_content` from DB).
3. [x] DONE 2026-08-30 Scroll-to-bottom pill with unread count when scrolled up during a stream; message timestamps on hover; per-message `token_count` badge; session usage strip (tokens, cache, Hermes cost or ≈ models.dev estimate — owner choice 2026-08-30, `backend/pricing.py`).
4. [x] DONE 2026-08-30 Empty state starter chips (per agent: 3 prompts from its template description) and a "thinking…" indicator before the first token.
Proof: Playwright 1440/390 on a real coder turn with a code block + tool call; markdown table renders; copy button copies.

## 4b-2 Sessions — DONE 2026-08-30
1. [x] DONE 2026-08-30 Rename (inline title edit in the header → gateway PATCH, hq `chat_sessions.title` kept in sync), Delete with confirm (gateway DELETE; link row removed), Pin (gateway PATCH `pinned` if accepted, else hq-side) — pinned section on top of the list.
2. [x] DONE 2026-08-30 (already satisfied: hq titles a new session from the first message at creation; gateway PATCH used for rename) Auto-title: after the first reply of an untitled session, PATCH a title from the first user line (≤60 chars).
3. [x] DONE 2026-08-30 Find in conversation (Ctrl+F bar, next/prev), global session search (Ctrl+K modal; hq-side SQL LIKE over each profile's `messages` RO, gateway search if it answers) with snippets.
4. [x] DONE 2026-08-30 Export transcript as Markdown (server renders `GET /api/session/{p}/{id}/export.md`).
Proof: rename/delete round-trip visible in the gateway list; search finds a known phrase from a dispatched run; pytest with the fake gateway for PATCH/DELETE/export.

## 4b-3 Composer + reliability — DONE 2026-08-30
1. [x] DONE 2026-08-30 Slash-command menu — **hq-owned** commands (`/model /reasoning /fast /title /pin /unpin /export /find /new /steer /help`): this Hermes has no `/v1/commands` and its CLI slash commands are not interpreted by the gateway (sent text just reaches the LLM).
2. [x] DONE 2026-08-30 Model / reasoning effort / fast per session (browser-local, sent per turn as `model` + `model_options.reasoning_effort|fast`; `/v1/models` only advertises the virtual `hermes-agent`, so suggestions come from models.dev via `GET /api/chat/models`).
3. [x] DONE 2026-08-30 Image + file attachments: Ctrl+V paste, drag-drop, picker; client downscale to 1920px/JPEG ≤1 MB; text files as text parts; thumbnails in the composer and in the transcript. Verify the `attachments` shape live on coder first.
4. [x] DONE 2026-08-30 Pending send survives reload (a reload aborts the fetch, so the text comes back as "Unsent draft restored") (localStorage, 5-min TTL, re-sent with confirmation), gateway-down banner with Retry, failed sends keep the draft.
5. [x] DONE 2026-08-30 Steer: "Send while running" → `POST /v1/runs/{id}/steer {message}`; if the gateway rejects, fall back to queueing the message for after the turn. Context meter: DONE 2026-08-30 as an estimate (no `/runtime` on this Hermes; see Knowledge); only revisit if a runtime endpoint appears.
Proof: real image turn (agent describes the image), a slash command result, model switch reflected in `session_model_usage`, reload mid-send.

## 4b-4 Agent questions with options — DONE 2026-08-30
Convention (hermes-hq's own; workspace has a renderer but no producer): the agent emits a fenced block
```hq-options
{"question": "...", "mode": "single|multi", "options": [{"label": "A", "detail": "..."}]}
```
UI renders a card with buttons; clicking sends the chosen label(s) as a normal user message. **Instruction delivery (decided 2026-08-30, no template/SOUL edit needed):** hq sends the convention as the gateway's per-turn `system_message`, which the agent appends to its own system prompt (ephemeral, never stored) — so every agent gets it on hq chat turns and the A/B on `agents/` templates is moot. Dispatched runs (CLI sessions) do not carry it; a "Needs you" prompt for a running task asking a question is deferred (runs report blockers through the completion contract).
Proof: orchestrator asked a question with 3 options → card → click → reply continues.

## 4b-5 Notifications — in progress (5.1, 5.2 DONE 2026-08-30)
1. [x] DONE 2026-08-30 (derived from `state_transitions` on read — no engine hooks needed; first sync sets a watermark, no history backfill) hq-side `notifications` table: task needs you (blocked/failed/stalled/waiting_approval/needs_review), run finished, review verdict, chat reply finished for a session you are not viewing, agent asked a question (4b-4). Bell in the top bar with unread count, list with links, mark-read; `GET /api/notifications`, `POST /api/notifications/read`.
2. [x] DONE 2026-08-30 Browser notifications (Notification API; toggles in the bell menu; OS alert only when the tab is hidden/unfocused, ≤3 per poll + a summary) and an optional WebAudio chime (new notifications; finished chat replies while watching).
3. [ ] Web Push for the phone when the app is closed: service worker + local VAPID key pair generated by hermes-hq (no external service), `pywebpush`, subscription stored in hq.db, PWA install banner. Ships last; needs HTTPS — DEFERRED 2026-08-30: no Tailscale/HTTPS on the box; owner to decide the HTTPS route first.
Proof: a task turning blocked shows bell +1 and a browser notification; push received on the phone (if 4b-5.3 is done).

## 4b-6 Mobile — DONE 2026-08-30
1. [x] DONE 2026-08-30 Sessions bottom sheet on phones (replaces the dropdown; same list as the sidebar), keyboard-inset-aware chat card via `visualViewport` → `--hq-kb`, sticky scroll-to-bottom (from 4b-1).
Proof: 390px Playwright with the virtual keyboard emulated; no horizontal scroll.

Out of scope: voice/STT, swarm/external-harness/agora/playground chats, local-provider routing, providers dialog, model suggestions, focus mode/width/avatars/loading styles, terminal/file explorer inside chat (Groups 5–6).
Risks: react-markdown bundle size (+~60 KB gz; acceptable); attachments/steer/PATCH shapes unverified until 4b-3/4b-2 pre-flight; option-card convention depends on agents following the SOUL instruction; Web Push needs HTTPS.
