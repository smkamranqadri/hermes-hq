# hermes-hq — Technical Knowledge

## Architecture (decided 2026-08-29)
- **One Python service** (`hermes_hq` package, FastAPI) that: contains the engine (`hermes_hq/engine/` = moved `wm_store`/`wm_dispatch`/`wm_run_agent`), runs the dispatcher as an in-process background loop (no Hermes cron), exposes REST + WebSocket events, and serves the built React UI.
- **Frontend**: React 19 + Vite + Tailwind 4, top-bar navigation (7 tabs per `hermes-work-manager/design/IA_FLOWS.md`: Overview · Projects · Tasks · Chat · Agents · Reviews · Activity; System secondary).
- **Data**: SQLite at `$HERMES_HOME/hermes-hq/hq.db`. One-time importer for legacy `/opt/data/work-manager/wm.db`.
- **Agents**: Hermes profiles, managed only via `hermes profile` CLI (`list`, `create --description`, `describe`, `show`, `export/import`, `install <git|dir>`). Roster templates ship in repo `agents.yaml` (+ SOUL.md and role skill per template). Add-agent flow: pick template → `hermes profile create` + apply template files; fallback → spawn a default-profile session with a prompt to create it. Never a custom profile-copy script.
- **Model/provider config** stays in Hermes; hermes-hq stores no API keys.
- **Ports**: hermes-hq default :9010 (legacy WM dashboard stays on :9009).
- **Install**: single command (`curl … | bash` or `pipx install hermes-hq && hermes-hq serve`) on a box with `hermes` on PATH.

## Status model
Engine keeps its precise state machine (`planned, waiting_approval, ready, running, needs_review, rework, blocked, failed, stalled, done, manual`; goals `draft/planning/planned/released`). UI shows 5 human states + reason line:
- **Backlog** = planned/draft
- **Queued** = ready, or dependency-gated (reason "waiting on #N")
- **Working** = running; `needs_review` shown as "Working · reviewer checking" (review is auto-dispatched to reviewer agent — verified `wm_store.py:1699`, `wm_dispatch.py:264`)
- **Needs you** = owner approval, blocked, failed, stalled — each with the one unblocking action
- **Done** = done, manual/archived

## Engine rules carried over (non-negotiable)
- Every task belongs to a project with a valid `primary_path` (default `/opt/data/projects/<slug>`, root configurable).
- One persistent Hermes session per task run; session id must be deterministic, never "newest session".
- `planned` never auto-runs; completion only via `runs/<id>.completion.json` (exit code ≠ completion).
- Review = system-created review run (single-model), never a hand-made task; `changes_requested → rework → re-run → re-review` is the only rework path.
- Liveness = process state + Hermes session `last_activity_at` + timeout.

## Environment (this box)
Repo layout: `hermes_hq/` (app.py, cli.py, dispatcher.py, engine/, static/ = built UI), `web/` (Vite+React), `tests/engine/`.

Hermes v0.20.5 at `/opt/hermes`, `HERMES_HOME=/opt/data`, profiles under `/opt/data/profiles/`, gateway :8642, Hermes dashboard :9119, legacy WM dashboard :9009. Node 26, Python 3.13, no pnpm.
