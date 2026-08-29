"""Live-run visibility: capacity counts, per-agent live runs, session ownership."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_writes import env, login  # noqa: F401


def _running(c, store, db, h, title, agent="coder"):
    tid = c.post("/api/tasks", json={"project": "alpha", "title": title, "assignee": agent}, headers=h).json()["id"]
    store.mark_ready(tid, db_path=db); store.claim_task(tid, db_path=db)
    return tid, store.start_run(tid, agent, db_path=db)


def test_system_and_overview_capacity(env):
    c, store, db = env
    h = login(c)
    assert c.get("/api/system").json()["running"] == 0 and c.get("/api/system").json()["cap"] == 3
    _running(c, store, db, h, "one"); _running(c, store, db, h, "two", agent="reviewer")
    sysd = c.get("/api/system").json(); assert (sysd["running"], sysd["cap"]) == (2, 3)
    st = c.get("/api/overview").json()["stats"]; assert st["slots_used"] == 2 and st["cap"] == 3


def test_agent_live_runs_and_session_ownership(env):
    c, store, db = env
    h = login(c)
    tid, rid = _running(c, store, db, h, "live one")
    agents = {a["name"]: a for a in c.get("/api/agents").json()["agents"]}
    assert agents["coder"]["live"][0]["run_id"] == rid and agents["coder"]["live"][0]["task_title"] == "live one"
    assert agents["writer"]["live"] == []
    from backend import chat
    # marker title while the id is not captured yet
    assert chat.live_run_for_session("coder", "sess-x", "wm-run-%d" % rid, db)["run_id"] == rid
    assert chat.live_run_for_session("writer", "sess-x", "wm-run-%d" % rid, db) is None
    store.set_run_session(rid, "sess-real", db_path=db) if hasattr(store, "set_run_session") else None
    store.finish_run(rid, "done", db_path=db)
    assert chat.live_run_for_session("coder", "sess-x", "wm-run-%d" % rid, db) is None   # finished -> not live
