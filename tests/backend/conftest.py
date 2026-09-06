"""Engine-environment isolation for the whole backend suite.

The engine resolves its database as `WM_DB or DEFAULT_DB_PATH`
(`core/wm_store.resolve_db`), and the dispatcher exports `WM_DB=<live hq.db>`
into every agent run (`core/wm_dispatch.py`). Test fixtures set
`HERMES_HQ_HOME`, which only moves DEFAULT_DB_PATH — so when the suite runs
INSIDE a dispatched agent, any code path that omits an explicit `db_path`
(notably `wm_cli.main(...)`, the librarian CLI tests) wrote straight into the
owner's LIVE library. That really happened: 33 phantom proposals landed on
live note #1 on 2026-09-05/06, and the same tests failed spuriously for the
agent.

Clearing the overrides here — once, for every test — makes it impossible for
any test to reach a real database or profile tree, whoever runs it.
Test-specific fixtures that set these vars themselves still win: their
`setenv` runs after this autouse fixture.
"""
import pytest

_ENGINE_ENV_OVERRIDES = ("WM_DB", "WM_RUNS_DIR", "WM_PROFILES_DIR", "WM_HERMES")


@pytest.fixture(autouse=True)
def isolate_engine_env(monkeypatch):
    for var in _ENGINE_ENV_OVERRIDES:
        monkeypatch.delenv(var, raising=False)
