# hermes-hq

**A portable control plane for a [Hermes Agent](https://github.com/NousResearch/hermes-agent) team.**
Projects → tasks → real agent sessions, with status you can actually read and a "Needs you" list you can act on.

> Early development. The shell, theming and engine are in place; data views and write flows are being built (see `kis/intent/`).

## Why

Running several Hermes profiles as a team (orchestrator + analyst, writer, coder, reviewer, …) works, but coordinating them from chat sessions doesn't survive a crash, and the built-in kanban doesn't create a real session per task or make status readable. hermes-hq is the third attempt, built from the best parts of two earlier ones:

- the tested **work-manager engine** (SQLite store, dispatcher, one persistent Hermes session per task run, completion contract, automatic reviewer gate),
- a **UI** that borrows hermes-workspace's glass/mission-control look — top bar, six themes, bundled fonts — without the sidebar or the kitchen sink,
- an **install story** that works on any server where `hermes` is already set up: one command, agents added from templates via the Hermes CLI.

## Status model

The engine keeps its precise state machine (`planned, waiting_approval, ready, running, needs_review, rework, blocked, failed, stalled, done, manual`). The UI shows five human states plus a reason line:

| Human state | Engine statuses |
|---|---|
| **Backlog** | planned, draft |
| **Queued** | ready, dependency-gated (“waiting on #81”), rework |
| **Working** | running, needs_review (“reviewer checking”) |
| **Needs you** | owner approval, blocked, failed, stalled — each with the one action that unblocks it |
| **Done** | done, manual |

## Run (dev)

Requires Python ≥ 3.11 with [`uv`](https://github.com/astral-sh/uv), Node ≥ 22, and `hermes` on `PATH`.

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e .
(cd frontend && npm install && npm run build)     # builds the UI into backend/static
.venv/bin/hermes-hq serve                     # http://127.0.0.1:9010
.venv/bin/hermes-hq serve --no-dispatcher     # UI/API only, never launches agents
.venv/bin/hermes-hq wm status                 # engine CLI passthrough
```

State lives at `$HERMES_HOME/hermes-hq/` (`hq.db`, `runs/`); override with `HERMES_HQ_HOME`. Model/provider configuration stays in Hermes — hermes-hq stores no API keys.

Frontend dev loop: `cd frontend && npm run dev` (proxies `/api` to `:9010`).

## Tests

```bash
for t in tests/core/test_*.py; do HERMES_HQ_HOME=/tmp/hq-test python3 "$t"; done
```

`test_t2/t5/t7` are known-failing (outdated by a goal-lifecycle change in the source project; tracked in `kis/state/current.md`).

## Layout

```
core/             the engine: store, dispatcher, run wrapper, wm CLI (no web deps)
backend/          FastAPI service (app.py), CLI (cli.py), in-process dispatcher
frontend/         Vite + React 19 + Tailwind 4 UI → built into backend/static
tests/core/       engine test suite
kis/              project memory (knowledge / intent / state) — read state first
```

## Lineage

- Engine and IA: the author's earlier *hermes-work-manager* (Python/SQLite).
- Visual language and theme palettes: [outsourc-e/hermes-workspace](https://github.com/outsourc-e/hermes-workspace) (MIT).
- Supervisor patterns (durable queue, event stream): [jpalmae/hermeshq](https://github.com/jpalmae/hermeshq) (MIT).

## License

MIT — see [LICENSE](LICENSE).


## Notifications and Web Push

hermes-hq records notifications (tasks that need you, finished work, chat replies, agent questions) in `hq.db`
and shows them in the bell (desktop) / Inbox tab (phones). Three delivery layers:

1. **In-app** — always on while the app is open.
2. **Browser alerts** — the Notification API; fires when the tab/app is not in front. iPhone only allows it for
   a web app added to the Home Screen and served over https.
3. **Web Push** — alerts while the app is closed or the phone is locked. Requirements: the app must be served
   over **https** (e.g. `tailscale serve --bg --https=443 http://localhost:9010` on the host, or any reverse proxy
   with a certificate); on iPhone, add it to the Home Screen first. Turn on *Push to this device* in Inbox.

Nothing is configured per install: the VAPID key pair is generated on first use into
`<hermes-hq home>/vapid_private.pem` (keep it — subscriptions are bound to it), subscriptions live in
`push_subscriptions`, and pushes go out from the dispatcher loop, so they do not depend on any browser being
open. Optional `HERMES_HQ_PUSH_CONTACT=you@example.com` sets the contact address push services may use (must be a
routable domain — Apple rejects `@localhost` with 403 BadJwtToken; the default is `hermes-hq@example.com`).
Dead subscriptions (404/410 from the push service, or 8 failures in a row) are removed automatically.
