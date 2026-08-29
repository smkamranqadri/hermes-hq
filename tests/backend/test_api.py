"""API tests over a scratch hq.db built with the real engine."""
import os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    home = tmp_path / "hq"
    monkeypatch.setenv("HERMES_HQ_HOME", str(home))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    db = store.DEFAULT_DB_PATH
    os.makedirs(tmp_path / "alpha"); os.makedirs(tmp_path / "zed")
    store.create_project("alpha", "Alpha", "", str(tmp_path / "alpha"), db_path=db)
    store.create_project("zed", "Zed", "", str(tmp_path / "zed"), db_path=db)
    store.create_task("zed", "hidden", "d", "dod", assignee_profile="coder", db_path=db)
    store.set_project_archived("zed", 1, db_path=db)
    t1 = store.create_task("alpha", "first", "d", "dod", assignee_profile="coder", db_path=db)
    t2 = store.create_task("alpha", "second", "d", "dod", assignee_profile="coder", db_path=db)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        c.headers["x-csrf"] = c.post("/api/login", json={"password": "pw-test"}).json()["csrf"]
        yield c, store, db, t1, t2


def test_tasks_envelope_newest_first(client):
    c, store, db, t1, t2 = client
    r = c.get("/api/tasks").json()
    assert set(r) >= {"tasks", "total", "stateCounts", "stateOptions", "limit", "offset"}
    assert r["total"] == 2 and [t["id"] for t in r["tasks"]] == [t2, t1]
    assert r["stateOptions"] == ["backlog"] and r["stateCounts"] == {"backlog": 2}
    assert all(t["human"]["state"] == "backlog" for t in r["tasks"])


def test_state_filter_and_options(client):
    c, store, db, t1, t2 = client
    store.mark_ready(t1, db_path=db)
    r = c.get("/api/tasks?state=queued").json()
    assert [t["id"] for t in r["tasks"]] == [t1]
    assert r["stateOptions"] == ["queued", "backlog"]  # options ignore the state filter


def test_search_by_id_and_paging(client):
    c, *_ = client
    assert c.get("/api/tasks?q=2").json()["total"] >= 1
    r = c.get("/api/tasks?limit=1&offset=1").json()
    assert len(r["tasks"]) == 1 and r["total"] == 2


def test_task_detail_and_404(client):
    c, store, db, t1, t2 = client
    d = c.get("/api/task/%d" % t1).json()
    assert d["human"]["state"] == "backlog" and "runs" in d and "transitions" in d
    assert c.get("/api/task/9999").status_code == 404
    assert c.get("/api/projects").json()["projects"][0]["slug"] == "alpha"


def test_project_detail_tasks_have_human_state(client):
    c, store, db, t1, t2 = client
    d = c.get("/api/project/alpha").json()
    assert [t["id"] for t in d["tasks"]] == [t2, t1]
    assert all("human" in t for t in d["tasks"])


def test_task_detail_deps_normalized(client):
    c, store, db, t1, t2 = client
    store.add_dep(t2, t1, db_path=db) if hasattr(store, "add_dep") else store.add_task_dep(t2, t1, db_path=db)
    d = c.get("/api/task/%d" % t2).json()
    assert d["deps"] == [{"id": t1, "status": "planned", "title": "first"}]
    assert c.get("/api/task/%d" % t1).json()["dependents"][0]["id"] == t2


def test_overview_and_activity(client):
    c, store, db, t1, t2 = client
    store.mark_ready(t1, db_path=db)
    o = c.get("/api/overview").json()
    assert o["stats"]["queued"] == 1 and o["stats"]["backlog"] == 1 and o["stats"]["needsyou"] == 0
    assert [t["id"] for t in o["queued"]] == [t1] and o["needsyou"] == []
    a = c.get("/api/activity").json()
    kinds = {e["kind"] for e in a["events"]}
    assert kinds == {"activity", "transition"} and a["events"][0]["ts"] >= a["events"][-1]["ts"]
    only = c.get("/api/activity?task_id=%d" % t1).json()["events"]
    assert only and all(e["task_id"] == t1 for e in only)
    assert c.get("/api/activity?limit=1").json()["next_before"] is not None


def test_run_log_tail(client, tmp_path):
    c, store, db, t1, t2 = client
    import os
    os.makedirs(store.resolve_runs_dir(), exist_ok=True)
    p = os.path.join(store.resolve_runs_dir(), "7.log")
    open(p, "w").write("hello\nworld\n")
    r = c.get("/api/run/7/log").json()
    assert r["exists"] and r["data"] == "hello\nworld\n" and r["next"] == 12
    open(p, "a").write("more\n")
    r2 = c.get("/api/run/7/log?offset=%d" % r["next"]).json()
    assert r2["data"] == "more\n"
    assert c.get("/api/run/999/log").json()["exists"] is False
