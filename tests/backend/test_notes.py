"""Second Brain Phase 1: notes/areas store + API, owner-assignee dispatch skip.

Proves the plan's P1 DoD lines: agent-write refusal (no session -> 401, and
the dispatcher can never claim an owner task), entries append-log, FTS search
including Urdu, create-and-link graduation, and the mine filter.
"""
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


# ---- agent-write refusal boundary --------------------------------------

def test_notes_require_owner_session(env):
    c, store, db = env
    # no session: reads and writes both refused — an agent process without the
    # owner cookie can never touch notes through HTTP.
    assert c.get("/api/notes").status_code == 401
    assert c.post("/api/notes", json={"title": "x"}).status_code == 401
    h = login(c)
    # session but no CSRF header: mutation still refused
    assert c.post("/api/notes", json={"title": "x"}).status_code == 403
    assert c.post("/api/notes", json={"title": "x"}, headers=h).status_code == 200


# ---- areas -------------------------------------------------------------

def test_areas_seeded_and_two_level(env):
    c, store, db = env
    h = login(c)
    names = [a["name"] for a in c.get("/api/areas").json()["areas"]]
    assert "Work" in names and "Family" in names and len(names) == 11
    work = next(a for a in store.list_areas(db_path=db) if a["name"] == "Work")
    r = c.post("/api/areas", json={"name": "SimpliEd", "parent_id": work["id"]}, headers=h)
    assert r.status_code == 200
    sub_id = r.json()["id"]
    # duplicate at the same level refused
    assert c.post("/api/areas", json={"name": "SimpliEd", "parent_id": work["id"]}, headers=h).status_code == 409
    # third level refused
    assert c.post("/api/areas", json={"name": "Deep", "parent_id": sub_id}, headers=h).status_code == 409
    # seeding is once-only: re-init must not duplicate
    store.init_db(db_path=db)
    assert len(store.list_areas(db_path=db)) == 12


# ---- note lifecycle ----------------------------------------------------

def test_note_crud_entries_revisions(env):
    c, store, db = env
    h = login(c)
    r = c.post("/api/notes", json={"title": "1:1 — Hamna", "body": "Standing context",
                                   "tags": ["1:1", "team"]}, headers=h)
    nid = r.json()["id"]
    assert r.json()["note"]["status"] == "inbox"
    assert r.json()["note"]["authored_by"] == "owner"

    # file it: inbox -> active with an area
    work = next(a for a in store.list_areas(db_path=db) if a["name"] == "Work")
    r = c.post("/api/note/%d/edit" % nid, json={"status": "active", "area_id": work["id"]}, headers=h)
    assert r.status_code == 200 and r.json()["note"]["status"] == "active"

    # edit snapshots a revision
    c.post("/api/note/%d/edit" % nid, json={"body": "Standing context v2"}, headers=h)
    revs = store._connect(db).execute("SELECT * FROM note_revisions WHERE note_id=?", (nid,)).fetchall()
    assert len(revs) == 2 and revs[1]["body"] == "Standing context"

    # entries append and surface newest-first
    c.post("/api/note/%d/entries" % nid, json={"body": "Discuss A-Level timeline"}, headers=h)
    c.post("/api/note/%d/entries" % nid, json={"body": "Workload check ok"}, headers=h)
    n = c.get("/api/note/%d" % nid).json()
    assert [e["body"] for e in n["entries"]] == ["Workload check ok", "Discuss A-Level timeline"]

    # invalid enum moves refused
    assert c.post("/api/note/%d/edit" % nid, json={"status": "nope"}, headers=h).status_code == 409
    assert c.post("/api/note/%d/edit" % nid, json={"type": "nope"}, headers=h).status_code == 409

    # archive keeps it out of the tree counts but in search
    c.post("/api/note/%d/edit" % nid, json={"status": "archived"}, headers=h)
    tree = c.get("/api/notes/tree").json()
    assert tree["counts"]["archived"] == 1


def test_search_fts_and_urdu(env):
    c, store, db = env
    h = login(c)
    c.post("/api/notes", json={"title": "Payments gateway", "body": "IBFT per merchant id"}, headers=h)
    c.post("/api/notes", json={"title": "Urdu نوٹ", "body": "گیٹ وے کی جانچ"}, headers=h)
    hits = c.get("/api/notes", params={"q": "merchant"}).json()["notes"]
    assert len(hits) == 1 and hits[0]["title"] == "Payments gateway"
    hits = c.get("/api/notes", params={"q": "گیٹ"}).json()["notes"]
    assert len(hits) == 1 and hits[0]["title"] == "Urdu نوٹ"
    # entries are searchable too
    nid = c.get("/api/notes", params={"q": "merchant"}).json()["notes"][0]["id"]
    c.post("/api/note/%d/entries" % nid, json={"body": "willow qr integration"}, headers=h)
    assert any(n["id"] == nid for n in c.get("/api/notes", params={"q": "willow"}).json()["notes"])
    # FTS syntax characters must not 500
    assert c.get("/api/notes", params={"q": 'a"b OR *'}).status_code == 200


# ---- owner assignee: the dispatcher must never claim -------------------

def test_owner_task_never_dispatched(env):
    c, store, db = env
    assert store.validate_assignee("owner") == "owner"
    tid = store.create_task("alpha", "buy milk", assignee_profile="owner", db_path=db)
    store.mark_ready(tid, db_path=db)
    agent_tid = store.create_task("alpha", "agent job", assignee_profile="coder", db_path=db)
    store.mark_ready(agent_tid, db_path=db)
    ready = [t["id"] for t in store.next_ready_tasks(10, db_path=db)]
    assert agent_tid in ready and tid not in ready
    assert store.claim_task(tid, db_path=db) is False       # predicate holds even on a direct claim
    assert store.claim_task(agent_tid, db_path=db) is True
    # owner can still close their own task
    store.close_by_owner(tid, note="done myself", db_path=db)
    assert store.get_task(tid, db_path=db)["status"] == "done"


def test_mine_filter(env):
    c, store, db = env
    h = login(c)
    store.create_task("alpha", "mine", assignee_profile="owner", db_path=db)
    store.create_task("alpha", "theirs", assignee_profile="coder", db_path=db)
    rows = c.get("/api/tasks", params={"assignee": "owner"}).json()["tasks"]
    assert [t["title"] for t in rows] == ["mine"]


# ---- graduation: create-and-link ---------------------------------------

def test_new_task_from_note(env):
    c, store, db = env
    h = login(c)
    p = store.get_project(slug="alpha", db_path=db)
    nid = c.post("/api/notes", json={"title": "Webhook for FB form", "project_id": p["id"]}, headers=h).json()["id"]
    r = c.post("/api/note/%d/new-task" % nid, json={}, headers=h)
    assert r.status_code == 200
    tid = r.json()["id"]
    t = store.get_task(tid, db_path=db)
    assert t["assignee_profile"] == "owner" and t["status"] == "ready"
    # linked both ways; note is still a note
    links = r.json()["note"]["links"]
    assert links and links[0]["kind"] == "task" and links[0]["target"]["id"] == tid
    assert c.get("/api/task/%d/notes" % tid).json()["notes"][0]["id"] == nid
    # dispatcher still refuses it
    assert store.claim_task(tid, db_path=db) is False
    # a note without a project needs an explicit one
    nid2 = c.post("/api/notes", json={"title": "floating"}, headers=h).json()["id"]
    assert c.post("/api/note/%d/new-task" % nid2, json={}, headers=h).status_code == 409
    assert c.post("/api/note/%d/new-task" % nid2, json={"project": "alpha"}, headers=h).status_code == 200


def test_new_reminder_from_note(env):
    c, store, db = env
    h = login(c)
    p = store.get_project(slug="alpha", db_path=db)
    nid = c.post("/api/notes", json={"title": "Review finances", "project_id": p["id"]}, headers=h).json()["id"]
    r = c.post("/api/note/%d/new-reminder" % nid, json={"cron": "0 9 1 * *"}, headers=h)
    assert r.status_code == 200
    sid = r.json()["id"]
    s = store.get_schedule(sid, db_path=db)
    assert s["assignee_profile"] == "owner" and s["cron"] == "0 9 1 * *"
    links = r.json()["note"]["links"]
    assert links[0]["kind"] == "schedule" and links[0]["target"]["id"] == sid


def test_one_shot_reminder_fires_once_then_retires(env):
    c, store, db = env
    h = login(c)
    p = store.get_project(slug="alpha", db_path=db)
    nid = c.post("/api/notes", json={"title": "Dentist Tuesday", "project_id": p["id"]}, headers=h).json()["id"]
    r = c.post("/api/note/%d/new-reminder" % nid,
               json={"cron": "0 15 1 1 *", "one_shot": True}, headers=h)
    assert r.status_code == 200
    sid = r.json()["id"]
    assert store.get_schedule(sid, db_path=db)["one_shot"] == 1
    # pretend the moment arrived
    conn = store._connect(db)
    with conn:
        conn.execute("UPDATE schedules SET next_fire_at=? WHERE id=?", (1.0, sid))
    conn.close()
    fired = store.fire_due(db_path=db)
    assert [(x[0], x[1]) for x in fired] == [(sid, "late")]
    tid = fired[0][2]
    t = store.get_task(tid, db_path=db)
    assert t["assignee_profile"] == "owner"
    s = store.get_schedule(sid, db_path=db)
    assert s["enabled"] == 0 and s["next_fire_at"] is None   # retired, not deleted
    assert store.fire_due(db_path=db) == []                  # never fires again


# ---- project surface ---------------------------------------------------

def test_project_notes(env):
    c, store, db = env
    h = login(c)
    p = store.get_project(slug="alpha", db_path=db)
    c.post("/api/notes", json={"title": "alpha note", "project_id": p["id"]}, headers=h)
    c.post("/api/notes", json={"title": "loose note"}, headers=h)
    rows = c.get("/api/project/alpha/notes").json()["notes"]
    assert [n["title"] for n in rows] == ["alpha note"]
    assert c.get("/api/project/nope/notes").status_code == 404
