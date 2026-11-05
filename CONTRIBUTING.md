# Contributing to hermes-hq

Thanks for helping. hermes-hq runs agents on a real server, so the bar for a change is: it is
small, it is tested, and it does not make the owner's view harder to read.

## Getting set up

```bash
git clone https://github.com/smkamranqadri/hermes-hq.git && cd hermes-hq
uv venv .venv && uv pip install --python .venv/bin/python -e . pytest httpx
(cd frontend && npm ci && npm run build)
```

Run the server without the dispatcher, so it never launches agents:

```bash
HERMES_HOME=/tmp/hq-dev .venv/bin/hermes-hq serve --no-dispatcher --port 9011
```

Pointing `HERMES_HOME` at an empty folder keeps your real Hermes profiles and data out of the
way. For UI work, `cd frontend && npm run dev` serves the UI with hot reload and proxies `/api`
to port 9010.

## Tests

```bash
.venv/bin/python -m pytest tests/backend -q          # main suite
for t in tests/core/test_*.py; do HERMES_HQ_HOME=/tmp/hq-core .venv/bin/python "$t"; done
(cd frontend && npm run build)                        # type-checks and builds the UI
```

Tests use temporary homes and never call a real model. A few tests depend on the host: the
terminal tests expect to switch to a separate terminal user, and some vault tests inspect raw
SQLite pages, so results can differ with your SQLite version. CI runs on Ubuntu.

## What a good pull request looks like

- **One change, with its test.** A bug fix comes with a test that fails without it.
- **Guards are checked by breaking them.** If you add a check (a refusal, a limit, an auth
  rule), break it on purpose, confirm its test fails, then restore it. Say so in the PR.
- **Keep `core/` free of web dependencies.** The engine runs from the CLI too.
- **Look at UI changes.** Include a screenshot at desktop width and at 390 px wide.
- **No new runtime dependencies** without a reason in the PR; prefer the standard library.

## Where things live

```text
core/wm_store.py       the SQLite store and task state machine
core/wm_dispatch.py    claims ready tasks and starts runs
core/wm_run_agent.py   wraps one Hermes session and applies the completion contract
backend/app.py         FastAPI app; routes are split across backend/*.py
frontend/src/          React UI (pages/, components/, api.ts)
agents/<name>/         agent templates: SOUL.md, agent.yaml, a specialist skill
tests/backend/         pytest suite
kis/                   the project's own memory (knowledge, intent, state)
```

## Reporting bugs

Open an issue with your OS, Python, Node and Hermes versions, what you expected, and the relevant
lines from the server log. Remove anything private first: passwords, note contents, file paths
you would rather not share.
