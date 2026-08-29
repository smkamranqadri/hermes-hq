"""Regression: owner feedback must reach the brief of the run claimed out of rework
(the dispatcher claims BEFORE rendering the brief, so the task is already 'running')."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_writes import env, login  # noqa: F401


def test_owner_feedback_survives_claim(env):
    c, store, db = env
    h = login(c)
    tid = c.post("/api/tasks", json={"project": "alpha", "title": "site refresh", "assignee": "coder"}, headers=h).json()["id"]
    store.mark_ready(tid, db_path=db); store.claim_task(tid, db_path=db)
    rid = store.start_run(tid, "coder", db_path=db)
    store.finish_run(rid, "blocked", error="needs approval", db_path=db)
    store.record_completion(tid, rid, "blocked", db_path=db) if hasattr(store, "record_completion") else None
    conn = store._connect(db); conn.execute("UPDATE tasks SET status='blocked' WHERE id=?", (tid,)); conn.commit(); conn.close()
    assert c.post("/api/task/%d/feedback" % tid, json={"comment": "clone https://example.com/repo and work there"}, headers=h).json()["task"]["status"] == "rework"
    assert store.latest_owner_feedback(tid, db_path=db).startswith("clone https://example.com/repo")   # while in rework
    assert store.claim_task(tid, db_path=db)                                                            # dispatcher claims first...
    rid2 = store.start_run(tid, "coder", db_path=db)
    assert store.latest_owner_feedback(tid, db_path=db).startswith("clone https://example.com/repo")   # ...then renders: still there
    brief = store.render_brief(rid2, db_path=db)
    assert "OWNER FEEDBACK" in brief and "https://example.com/repo" in brief
    # once the run finishes and the task moves on, it is not re-injected
    store.finish_run(rid2, "done", db_path=db)
    conn = store._connect(db); conn.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,)); conn.commit(); conn.close()
    store._record_transition_conn  # exists
    conn = store._connect(db)
    with conn:
        store._record_transition_conn(conn, tid, "done", from_status="running", detail="completion contract")
    conn.close()
    assert store.latest_owner_feedback(tid, db_path=db) is None
