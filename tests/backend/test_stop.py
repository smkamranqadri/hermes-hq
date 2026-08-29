"""Owner stop: kills the wrapper's process group, finalizes run+task, writes activity."""
import os, subprocess, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest
from tests.backend.test_writes import env, login  # noqa: F401  (fixture reuse)


def _alive(pid):
    try:
        os.kill(pid, 0); return True
    except ProcessLookupError:
        return False


def _fake_wrapper():
    # wrapper (sh) + child (sleep) in a fresh session, exactly like _launch()
    return subprocess.Popen(["sh", "-c", "sleep 300 & wait"], start_new_session=True)


def _running_task(c, store, db, h, title):
    tid = c.post("/api/tasks", json={"project": "alpha", "title": title, "assignee": "coder"}, headers=h).json()["id"]
    store.mark_ready(tid, db_path=db)
    store.claim_task(tid, db_path=db)
    rid = store.start_run(tid, "coder", db_path=db)
    proc = _fake_wrapper()
    store.set_run_pid(rid, proc.pid, db_path=db)
    assert store.get_task(tid, db_path=db)["status"] == "running"
    return tid, rid, proc


def _child_pids(pgid):
    out = subprocess.run(["ps", "-o", "pid=", "-g", str(pgid)], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out]


def test_stop_kills_group_and_marks_manual(env):
    c, store, db = env
    h = login(c)
    tid, rid, proc = _running_task(c, store, db, h, "stop me")
    time.sleep(0.2)
    kids = _child_pids(proc.pid)
    assert len(kids) >= 2  # sh + sleep
    r = c.post("/api/task/%d/stop" % tid, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task"]["status"] == "manual"
    assert body["stop"]["run_id"] == rid and "SIGTERM" in body["stop"]["kill"]
    time.sleep(0.3)
    assert not _alive(proc.pid)
    assert not any(_alive(p) for p in kids), "child of the wrapper survived"
    run = store.get_run(rid, db_path=db)
    assert run["status"] == "failed" and run["error"] == "stopped by owner"
    acts = [a["action"] for a in store.recent_activity(db_path=db)] if hasattr(store, "recent_activity") else None
    if acts is None:
        conn = store._connect(db); acts = [x[0] for x in conn.execute("SELECT action FROM activity").fetchall()]; conn.close()
    assert "task_stopped" in acts and "task_stalled" in acts and "task_manual" in acts
    # idempotent refusal: nothing running any more
    assert c.post("/api/task/%d/stop" % tid, headers=h).status_code == 409


def test_stop_keep_in_queue_requeues(env):
    c, store, db = env
    h = login(c)
    tid, rid, proc = _running_task(c, store, db, h, "stop and requeue")
    r = c.post("/api/task/%d/stop?keep_in_queue=1" % tid, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "ready"
    assert store.get_run(rid, db_path=db)["status"] == "failed"
    time.sleep(0.3)
    assert not _alive(proc.pid)


def test_stop_survives_dead_pid(env):
    """Wrapper already gone (crash) but never finalized: stop still finalizes."""
    c, store, db = env
    h = login(c)
    tid, rid, proc = _running_task(c, store, db, h, "already dead")
    proc.kill(); proc.wait()
    r = c.post("/api/task/%d/stop" % tid, headers=h)
    assert r.status_code == 200 and "already gone" in r.json()["stop"]["kill"]
    assert r.json()["task"]["status"] == "manual"
