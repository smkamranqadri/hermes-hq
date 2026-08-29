# hermes-hq — Technical Knowledge

## Architecture (decided 2026-08-29)
- **One Python service** (`hq` package, FastAPI) that: contains the engine (`backend/core/` = moved `wm_store`/`wm_dispatch`/`wm_run_agent`), runs the dispatcher as an in-process background loop (no Hermes cron), exposes REST + WebSocket events, and serves the built React UI.
- **Frontend**: React 19 + Vite + Tailwind 4. Top bar (glass, bordered): **Overview · Projects · Tasks · Agents · Chat**; right side **Tools** menu (Files · Terminal · Memory · Skills · MCP · Schedules), theme picker, LIVE/PAUSED dot, SYSTEM. Reviews live inside Tasks + Task detail; Activity inside Overview feed + Project detail. (Supersedes the 7-tab IA in `hermes-work-manager/design/IA_FLOWS.md`; decided 2026-08-29.)
- **Theming**: all colors are `--hq-*` CSS vars per `[data-theme]` in `frontend/src/index.css`, exposed to Tailwind via `@theme inline`. Six fixed themes, no OS-follow: violet (default, WM tokens), nous, nous-light (only light theme, from workspace `claude-nous-light`), bronze, slate, hermes (palettes from hermes-workspace `styles.css`). Pref in `localStorage['hq-theme']`; `?theme=<id>` overrides (used for screenshots).
- **Fonts**: only Inter + JetBrains Mono bundled (`frontend/public/fonts`, no CDN, no `@fontsource`). One font choice: JetBrains Mono (default, everywhere), Inter, System Sans/Serif/Mono. Pref in `localStorage['hq-fonts']`; `?font=<id>` overrides. Why: the app renders on each viewer's device, so only bundled fonts look identical; owner wants JetBrains.
- **Shell style**: navbar anatomy from WM v0.9 (full-width glass bar, icon tile + `HERMES // HQ` wordmark + version pill, bordered pill-group nav, sysbar with TOOLS, appearance ◐, pulsing LIVE dot, clock, SYSTEM pill); `glass`/`glass-strong` card utilities; orb + dot-grid body background.
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

## Legacy WM snapshot
Live WM (`/opt/data/work-manager/`, crons `wm-dispatch`, `wm completion watchdog`, `wm-planning-pickup`) keeps running until Group 1b cutover. hermes-hq works on an imported copy with dispatcher off; `hermes-hq import` rewrites `/opt/data/work-manager/` path prefixes and skips `runs/worktrees/`. Human state is derived at read time from engine status (`hq/status.py`), never stored.

## Engine rules carried over (non-negotiable)
- Every task belongs to a project with a valid `primary_path` (default `/opt/data/projects/<slug>`, root configurable).
- One persistent Hermes session per task run; session id must be deterministic, never "newest session".
- `planned` never auto-runs; completion only via `runs/<id>.completion.json` (exit code ≠ completion).
- Review = system-created review run (single-model), never a hand-made task; `changes_requested → rework → re-run → re-review` is the only rework path.
- Liveness = process state + Hermes session `last_activity_at` + timeout.

## Environment (this box)
Repo layout: `backend/` (Python package `backend`: app.py, cli.py, dispatcher.py, static/ = built UI), `backend/core/` (engine: wm_store/wm_dispatch/wm_run_agent/wm_cli), `frontend/` (Vite+React), `tests/core/`. Package is named `backend` on purpose (owner choice 2026-08-29; installs into its own venv so the generic name can't collide).

Hermes v0.20.5 at `/opt/hermes`, `HERMES_HOME=/opt/data`, profiles under `/opt/data/profiles/`, gateway :8642, Hermes dashboard :9119, legacy WM dashboard :9009. Node 26, Python 3.13, no pnpm.
