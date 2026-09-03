"""Auth + write routes over a scratch DB built with the real engine."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    os.makedirs(tmp_path / "alpha")
    store.create_project("alpha", "Alpha", "", str(tmp_path / "alpha"), db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        yield c, store, store.DEFAULT_DB_PATH


def login(c):
    r = c.post("/api/login", json={"password": "pw-test"})
    assert r.status_code == 200
    return {"x-csrf": r.json()["csrf"]}


def test_auth_gate(env):
    c, store, db = env
    assert c.get("/api/tasks").status_code == 401
    assert c.get("/api/health").status_code == 200
    assert c.post("/api/login", json={"password": "nope"}).status_code == 401
    h = login(c)
    assert c.get("/api/tasks").status_code == 200
    assert c.post("/api/tasks", json={"project": "alpha", "title": "x"}).status_code == 403  # no CSRF
    assert c.post("/api/tasks", json={"project": "alpha", "title": "x"}, headers=h).status_code == 200
    assert c.post("/api/logout", headers=h).status_code == 200
    assert c.get("/api/tasks").status_code == 401


def test_task_lifecycle_writes(env):
    c, store, db = env
    h = login(c)
    t = c.post("/api/tasks", json={"project": "alpha", "title": "one", "assignee": "coder", "is_code": True}, headers=h).json()
    tid = t["id"]; assert t["task"]["human"]["state"] == "backlog"
    t2 = c.post("/api/tasks", json={"project": "alpha", "title": "two", "deps": [tid]}, headers=h).json()
    assert t2["task"]["deps"][0]["id"] == tid
    r = c.post("/api/task/%d/mark-ready" % t2["id"], headers=h)
    assert r.status_code == 409 and "dep" in r.json()["detail"].lower()  # engine refusal surfaces verbatim
    assert c.post("/api/task/%d/mark-ready" % tid, headers=h).json()["task"]["status"] == "ready"
    assert c.post("/api/task/%d/manual" % tid, json={"note": "taking over"}, headers=h).json()["task"]["status"] == "manual"
    assert c.post("/api/task/%d/retry" % tid, headers=h).json()["task"]["status"] == "ready"
    assert c.post("/api/task/%d/assign" % tid, json={"assignee": "writer"}, headers=h).json()["task"]["assignee_profile"] == "writer"
    acts = [a["action"] for a in store.recent_activity(db_path=db)] if hasattr(store, "recent_activity") else None
    if acts is not None:
        assert "task_retry" in acts


def test_phased_task_creation_and_owner_approval_promotes_build(env):
    c, store, db = env
    h = login(c)
    goal_id = store.create_goal("alpha", "Feature goal", db_path=db)
    store.request_goal_planning(goal_id, db_path=db)
    store.set_goal_status(goal_id, "planned", db_path=db)
    store.release_goal(goal_id, db_path=db)
    r = c.post("/api/tasks", json={
        "project": "alpha", "title": "Ship feature", "description": "brief",
        "definition_of_done": "verified", "assignee": "coder",
        "goal_id": goal_id, "owner_approval": True, "phased": True,
    }, headers=h)
    assert r.status_code == 200
    plan_id = r.json()["id"]
    plan = store.get_task(plan_id, db_path=db)
    dependents = store.list_deps(plan_id, db_path=db)
    assert plan["title"] == "Ship feature — plan"
    assert plan["assignee_profile"] == "coder"
    assert plan["review_policy"] == "none" and plan["owner_approval"] == 1
    assert plan["is_code"] == 0
    assert len(dependents) == 1
    build_id = dependents[0]["task_id"]
    build = store.get_task(build_id, db_path=db)
    assert build["title"] == "Ship feature — build"
    assert build["is_code"] == 1 and build["review_policy"] == "required"
    assert build["owner_approval"] == 1

    # Releasing the goal satisfies dependencies, but the build remains gated
    # until the owner explicitly approves that task.
    store.mark_manual(plan_id, note="plan ready for owner", db_path=db)
    promoted = store.close_by_owner(plan_id, note="approved", db_path=db)
    assert build_id not in promoted
    assert store.get_task(build_id, db_path=db)["status"] == "waiting_approval"
    store.edit_task(build_id, owner_approval=False, db_path=db)
    assert store.get_task(build_id, db_path=db)["status"] == "ready"


def test_feedback_requires_comment_and_reworks(env):
    c, store, db = env
    h = login(c)
    tid = c.post("/api/tasks", json={"project": "alpha", "title": "fb"}, headers=h).json()["id"]
    assert c.post("/api/task/%d/feedback" % tid, json={"comment": "  "}, headers=h).status_code == 422
    r = c.post("/api/task/%d/feedback" % tid, json={"comment": "do it differently"}, headers=h)
    assert r.status_code == 409  # planned task has produced nothing to give feedback on (engine rule)
    # a blocked task (agent stopped to ask the owner) accepts a reply and becomes rework
    import sqlite3
    con = sqlite3.connect(db); con.execute("UPDATE tasks SET status='blocked' WHERE id=?", (tid,)); con.commit(); con.close()
    r = c.post("/api/task/%d/feedback" % tid, json={"comment": "yes, go ahead"}, headers=h)
    assert r.status_code == 200 and r.json()["task"]["status"] == "rework"
    assert r.json()["task"]["human"]["state"] == "queued" and "yes, go ahead" in (r.json()["task"]["feedback"] or "")


def test_create_project_initializes_kis_when_requested(env, monkeypatch, tmp_path):
    c, store, db = env
    h = login(c)
    from backend import writes
    calls = []

    def fake_initialize_kis(path):
        calls.append(path)
        (tmp_path / "kis-marker").write_text(path)

    monkeypatch.setattr(writes, "initialize_kis", fake_initialize_kis)
    project_path = tmp_path / "kis-project"
    r = c.post("/api/projects", json={
        "slug": "kis-project", "name": "KIS Project",
        "primary_path": str(project_path), "initialize_kis": True,
    }, headers=h)
    assert r.status_code == 200
    assert calls == [str(project_path)]
    assert store.get_project(slug="kis-project", db_path=db)["primary_path"] == str(project_path)


def test_create_project_reports_kis_failure_without_creating_project(env, monkeypatch, tmp_path):
    c, store, db = env
    h = login(c)
    from backend import writes

    def fail_initialize_kis(path):
        raise ValueError("bootstrap failed")

    monkeypatch.setattr(writes, "initialize_kis", fail_initialize_kis)
    project_path = tmp_path / "failed-kis-project"
    r = c.post("/api/projects", json={
        "slug": "failed-kis-project", "name": "Failed KIS",
        "primary_path": str(project_path), "initialize_kis": True,
    }, headers=h)
    assert r.status_code == 502
    assert "KIS initialization failed" in r.json()["detail"]
    assert store.get_project(slug="failed-kis-project", db_path=db) is None


def test_goal_and_project_writes(env):
    c, store, db = env
    h = login(c)
    gid = c.post("/api/goals", json={"project": "alpha", "title": "G"}, headers=h).json()["id"]
    assert c.post("/api/goal/%d/abandon" % gid, headers=h).status_code == 409  # draft, not planning
    assert c.post("/api/goal/%d/release" % gid, headers=h).status_code == 409  # must be planned first
    c.post("/api/goal/%d/plan" % gid, headers=h)
    assert store.get_goal(gid, db_path=db)["status"] == "planning"
    assert c.post("/api/goal/%d/abandon" % gid, headers=h).status_code == 200
    assert store.get_goal(gid, db_path=db)["status"] == "draft"
    assert c.post("/api/projects", json={"slug": "beta", "name": "Beta"}, headers=h).status_code == 200
    assert c.post("/api/project/beta", json={"description": "d"}, headers=h).status_code == 200
    assert c.post("/api/project/beta/archive", headers=h).status_code == 200
    assert [p["slug"] for p in c.get("/api/projects").json()["projects"]] == ["alpha"]


def test_system_pause_resume(env):
    c, store, db = env
    h = login(c)
    assert c.post("/api/system/pause", headers=h).json()["paused"] is True
    assert c.get("/api/system").json()["paused"] is True
    assert c.post("/api/system/resume", headers=h).json()["paused"] is False
    assert "coder" in c.get("/api/system/roster").json()["assignees"]
