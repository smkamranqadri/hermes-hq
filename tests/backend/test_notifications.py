"""Group 4b-5.1: notifications derived from state transitions + client events; read state."""
import os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_chat import env, login  # noqa: F401


def _transition(store, task_id, frm, to, detail=""):
    con = store._connect(store.DEFAULT_DB_PATH)
    try:
        with con:
            con.execute("INSERT INTO state_transitions(task_id, run_id, ts, from_status, to_status, detail) VALUES (?,?,?,?,?,?)",
                        (task_id, None, time.time(), frm, to, detail))
    finally:
        con.close()


def test_transitions_become_notifications_without_backfill(env):
    c, store, gw, root = env
    h = login(c); db = store.DEFAULT_DB_PATH
    store.create_project("demo", "Demo", "", "/tmp/demo", db_path=db)
    t = store.create_task("demo", "Write docs", "", "", db_path=db); tid = t if isinstance(t, int) else t["id"]
    _transition(store, tid, "planned", "blocked", "old history")           # before the first sync → never notified
    r = c.get("/api/notifications"); assert r.status_code == 200 and r.json() == {"notifications": [], "unread": 0}
    _transition(store, tid, "ready", "running", "claimed")                 # not interesting
    _transition(store, tid, "running", "needs_review", "completion contract")
    _transition(store, tid, "needs_review", "done", "approved")
    _transition(store, tid, "done", "rework", "owner feedback: fix the title")
    d = c.get("/api/notifications").json()
    assert d["unread"] == 3
    kinds = [(n["kind"], n["title"]) for n in d["notifications"]]
    assert kinds == [("info", "Task #%d sent back for rework" % tid), ("done", "Task #%d is done" % tid), ("needs_you", "Task #%d needs you — needs review" % tid)]
    assert d["notifications"][0]["href"] == "/tasks/%d" % tid and "fix the title" in d["notifications"][0]["body"] and d["notifications"][0]["body"].startswith("Write docs")
    # idempotent: a second sync creates nothing new
    assert c.get("/api/notifications").json()["unread"] == 3
    # read one, then all
    first = d["notifications"][0]["id"]
    assert c.post("/api/notifications/read", json={"ids": [first]}, headers=h).json()["marked"] == 1
    assert c.get("/api/notifications?unread=1").json()["unread"] == 2
    assert c.post("/api/notifications/read", json={}, headers=h).json()["marked"] == 2
    assert c.get("/api/notifications").json()["unread"] == 0


def test_client_events_are_idempotent_by_source_key(env):
    c, store, gw, root = env
    h = login(c)
    body = {"kind": "chat", "title": "orchestrator replied", "href": "/chat/orchestrator/api_1", "source_key": "chat:api_1:run_9"}
    a = c.post("/api/notifications", json=body, headers=h).json()["id"]
    b = c.post("/api/notifications", json=body, headers=h).json()["id"]
    assert a and b is None
    assert c.post("/api/notifications", json={"kind": "nope", "title": "x"}, headers=h).status_code == 409
    d = c.get("/api/notifications").json()
    assert d["unread"] == 1 and d["notifications"][0]["kind"] == "chat"


def test_finished_chat_turn_notifies_server_side(env):
    c, store, gw, root = env
    h = login(c)
    r = c.post("/api/chat/orchestrator/api_1_abc", json={"message": "ping"}, headers=h)
    assert r.status_code == 200
    d = c.get("/api/notifications").json()
    assert d["unread"] == 1 and d["notifications"][0]["title"] == "orchestrator replied" and d["notifications"][0]["body"] == "Hello gnip"
    assert d["notifications"][0]["source_key"] == "chat:api_1_abc:run_9" and d["notifications"][0]["href"] == "/chat/orchestrator/api_1_abc"
    # the watching device marks it read by source_key; a second identical turn id is a no-op insert
    assert c.post("/api/notifications/read", json={"source_key": "chat:api_1_abc:run_9"}, headers=h).json()["marked"] == 1
    assert c.get("/api/notifications").json()["unread"] == 0
