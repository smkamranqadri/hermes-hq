"""Agent default-model selection: routes over a mocked bridge (model_get /
model_set), validation, the expensive-model confirm loop, and the audit row.
The bridge itself runs Hermes' own _apply_model_assignment_sync — nothing to
unit-test on that side here."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"; profiles = hermes / "profiles"
    (profiles / "coder").mkdir(parents=True); (hermes / "memories").mkdir()
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    monkeypatch.setenv("WM_PROFILES_DIR", str(profiles))
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend import memory
    calls = []
    state = {"provider": "openai-codex", "model": "gpt-5.6-luna-900k", "base_url": "", "effort": "medium"}
    def fake(home, op, body=None, timeout=60):
        calls.append((os.path.basename(home), op, body))
        if op == "model_get":
            return dict(state)
        if op == "model_set":
            if body.get("model") == "expensive-9000" and not body.get("confirm"):
                return {"ok": False, "confirm_required": True, "confirm_message": "$99/Mtok"}
            if body.get("model") == "broken":
                return {"ok": False, "status": 400, "error": "provider and model required for main"}
            if body.get("model"):
                state.update(provider=body["provider"], model=body["model"])
            if body.get("effort"):
                state.update(effort=body["effort"])
            return {"ok": True, **state}
        raise AssertionError("unexpected op %r" % op)
    monkeypatch.setattr(memory, "bridge", fake)
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        yield c, calls, store, state


def login(c):
    r = c.post("/api/login", json={"password": "pw-test"})
    c.headers.update({"x-csrf": r.json()["csrf"]})


def test_get_model(env):
    c, calls, store, state = env
    assert c.get("/api/agent/coder/model").status_code == 401     # owner wall
    login(c)
    r = c.get("/api/agent/coder/model")
    assert r.status_code == 200 and r.json()["model"] == "gpt-5.6-luna-900k"
    assert c.get("/api/agent/nope/model").status_code == 404


def test_set_model_validation(env):
    c, calls, store, state = env
    login(c)
    assert c.post("/api/agent/nope/model", json={"model": "m", "provider": "p"}).status_code == 404
    assert c.post("/api/agent/coder/model", json={"model": "m"}).status_code == 400       # provider+model together
    assert c.post("/api/agent/coder/model", json={"provider": "p"}).status_code == 400
    assert c.post("/api/agent/coder/model", json={}).status_code == 422                    # nothing to change
    assert c.post("/api/agent/coder/model", json={"effort": "hyper"}).status_code == 400   # closed ladder
    # bridge-side refusal surfaces as its status
    r = c.post("/api/agent/coder/model", json={"provider": "p", "model": "broken"})
    assert r.status_code == 400 and "required" in r.json()["detail"]


def test_set_model_and_effort_audited(env):
    c, calls, store, state = env
    login(c)
    r = c.post("/api/agent/coder/model",
               json={"provider": "anthropic", "model": "claude-sonnet-5", "effort": "high"})
    assert r.status_code == 200
    assert r.json()["model"] == "claude-sonnet-5" and r.json()["effort"] == "high"
    assert state["provider"] == "anthropic"
    sent = next(b for (_, op, b) in calls if op == "model_set")
    assert sent["confirm"] is False
    acts = store._connect(store.DEFAULT_DB_PATH).execute(
        "SELECT action, detail FROM activity WHERE action='agent_model_set'").fetchall()
    assert len(acts) == 1 and "claude-sonnet-5" in acts[0]["detail"]


def test_effort_only_change(env):
    c, calls, store, state = env
    login(c)
    r = c.post("/api/agent/coder/model", json={"effort": "low"})
    assert r.status_code == 200 and state["effort"] == "low" and state["model"] == "gpt-5.6-luna-900k"


def test_expensive_confirm_loop(env):
    c, calls, store, state = env
    login(c)
    r = c.post("/api/agent/coder/model", json={"provider": "openai-api", "model": "expensive-9000"})
    assert r.status_code == 200 and r.json()["confirm_required"] and "$99" in r.json()["confirm_message"]
    assert state["model"] == "gpt-5.6-luna-900k"                  # nothing changed yet
    # no audit row for a not-applied change
    assert not store._connect(store.DEFAULT_DB_PATH).execute(
        "SELECT 1 FROM activity WHERE action='agent_model_set'").fetchall()
    r = c.post("/api/agent/coder/model",
               json={"provider": "openai-api", "model": "expensive-9000", "confirm": True})
    assert r.status_code == 200 and state["model"] == "expensive-9000"
