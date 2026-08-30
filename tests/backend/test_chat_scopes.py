"""Group 4: project/task-scoped chat sessions (link table + seeded brief)."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_chat import env, FakeGateway, login  # noqa: F401


def _seed(store):
    db = store.DEFAULT_DB_PATH
    store.create_project("demo", "Demo", "A demo project", "/tmp/demo", db_path=db)
    store.create_goal("demo", "Ship v1", db_path=db)
    tid = store.create_task("demo", "Write docs", "Docs for X", "README updated", db_path=db)
    return tid if isinstance(tid, int) else tid["id"]


def test_project_start_links_and_seeds(env):
    c, store, gw, root = env
    tid = _seed(store); h = login(c)
    r = c.post("/api/chat/start", json={"profile": "orchestrator", "project_id": 1}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"] == "api_1_abc" and d["profile"] == "orchestrator"
    assert d["scope"]["project_slug"] == "demo" and d["scope"]["task_id"] is None
    assert "PROJECT CHAT — Demo" in d["brief"] and "#1 Ship v1 [draft]" in d["brief"] and "#%d Write docs" % tid in d["brief"]
    assert "NOT a dispatched task" in d["brief"]
    assert [p for p, _ in FakeGateway.calls] == ["/api/sessions"]
    assert FakeGateway.calls[0][1]["title"] == "Project: Demo"
    # listed under the project and flagged on the profile's session list/detail
    assert c.get("/api/project/demo/chat-sessions").json()["sessions"][0]["session_id"] == "api_1_abc"
    assert c.get("/api/task/%d/chat-sessions" % tid).json()["sessions"] == []
    rows = store.chat_sessions_for_project(1, db_path=store.DEFAULT_DB_PATH)
    assert rows[0]["profile"] == "orchestrator" and rows[0]["project_name"] == "Demo"
    assert "api_key" not in r.text and "k-test" not in r.text


def test_task_start_uses_task_brief_and_inherits_project(env):
    c, store, gw, root = env
    tid = _seed(store); h = login(c)
    gw.set_enabled("coder", True, db_path=store.DEFAULT_DB_PATH)
    r = c.post("/api/chat/start", json={"profile": "coder", "task_id": tid}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["scope"] == {"project_id": 1, "project_slug": "demo", "project_name": "Demo", "task_id": tid, "task_title": "Write docs"}
    assert d["brief"].startswith("TASK CHAT — #%d Write docs" % tid) and "Definition of done: README updated" in d["brief"]
    assert c.get("/api/task/%d/chat-sessions" % tid).json()["sessions"][0]["task_id"] == tid
    assert c.get("/api/project/demo/chat-sessions").json()["sessions"][0]["task_title"] == "Write docs"
    # task status untouched — a chat is not a run
    assert store.get_task(tid, db_path=store.DEFAULT_DB_PATH)["status"] == "planned"


def test_disabled_agent_writes_no_link_row(env):
    c, store, gw, root = env
    _seed(store); h = login(c)
    r = c.post("/api/chat/start", json={"profile": "coder", "project_id": 1}, headers=h)
    assert r.status_code == 409 and FakeGateway.calls == []
    assert c.get("/api/project/demo/chat-sessions").json()["sessions"] == []
    assert c.post("/api/chat/start", json={"profile": "orchestrator"}, headers=h).status_code == 409
    assert c.post("/api/chat/start", json={"profile": "orchestrator", "project_id": 99}, headers=h).status_code == 409
    assert c.get("/api/project/nope/chat-sessions").status_code == 404
