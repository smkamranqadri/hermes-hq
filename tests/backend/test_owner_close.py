"""Owner-close pass: close_by_owner (manual-only), undepend, guarded goal delete."""
import os, sys, time
import pytest
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_chat import env, login  # noqa: F401


def _setup(store, db):
    try:
        store.create_project("demo", "Demo", "", "/tmp/demo", db_path=db)
    except Exception:
        pass


def _released_goal(store, db):
    g = store.create_goal("demo", "Ship it", db_path=db)
    gid = g if isinstance(g, int) else g["id"]
    store.set_goal_status(gid, "planned", force=True, db_path=db)
    store.release_goal(gid, db_path=db)
    return gid


def _activity_actions(store, db, tid):
    con = store._connect(db)
    try:
        return [r["action"] for r in con.execute(
            "SELECT action FROM activity WHERE task_id=? ORDER BY id", (tid,))]
    finally:
        con.close()


def test_close_by_owner_closes_waives_and_promotes(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    _setup(store, db)
    gid = _released_goal(store, db)
    dep = store.create_task("demo", "Done by hand", "", "", db_path=db)
    dependent = store.create_task("demo", "Waits on it", "", "", goal_id=gid, db_path=db)
    store.add_task_dep(dependent, dep, db_path=db)
    assert store.get_task(dependent, db_path=db)["status"] == "waiting_approval"
    con = store._connect(db)
    try:
        with con:
            con.execute("INSERT INTO reviews(task_id, reviewer_profile, status, "
                        "requested_at, review_policy) VALUES (?,?,?,?,?)",
                        (dep, "reviewer", "pending", time.time(), "required"))
    finally:
        con.close()
    store.mark_manual(dep, note="owner did it", db_path=db)
    promoted = store.close_by_owner(dep, note="deployed by hand", db_path=db)
    assert promoted == [dependent]
    assert store.get_task(dep, db_path=db)["status"] == "done"
    assert store.get_task(dependent, db_path=db)["status"] == "ready"
    rev = store.list_reviews(task_id=dep, db_path=db)[0]
    assert rev["status"] == "waived" and rev["verdict"] == "waived"
    con = store._connect(db)
    try:
        tr = con.execute("SELECT * FROM state_transitions WHERE task_id=? AND "
                         "to_status='done'", (dep,)).fetchone()
    finally:
        con.close()
    assert tr and tr["from_status"] == "manual" and "deployed by hand" in tr["detail"]
    assert "task_closed_by_owner" in _activity_actions(store, db, dep)
    res = store.check_integrity(db_path=db)
    assert res["ok"], res["findings"]


def test_close_by_owner_refuses_non_manual(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    _setup(store, db)
    tid = store.create_task("demo", "Still queued", "", "", db_path=db)
    with pytest.raises(ValueError, match="not 'manual'"):
        store.close_by_owner(tid, db_path=db)                  # planned
    store.mark_ready(tid, db_path=db)
    store.claim_task(tid, db_path=db)
    with pytest.raises(ValueError, match="not 'manual'"):
        store.close_by_owner(tid, db_path=db)                  # running
    store.mark_manual(tid, db_path=db)
    store.close_by_owner(tid, db_path=db)
    with pytest.raises(ValueError, match="not 'manual'"):
        store.close_by_owner(tid, db_path=db)                  # already done


def test_undepend_removes_edge_and_promotes(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    _setup(store, db)
    gid = _released_goal(store, db)
    done_dep = store.create_task("demo", "Finished", "", "", db_path=db)
    dead_dep = store.create_task("demo", "Never happening", "", "", db_path=db)
    dependent = store.create_task("demo", "Gated", "", "", goal_id=gid, db_path=db)
    store.add_task_dep(dependent, done_dep, db_path=db)
    store.add_task_dep(dependent, dead_dep, db_path=db)
    store.mark_manual(done_dep, db_path=db)
    store.close_by_owner(done_dep, db_path=db)
    assert store.get_task(dependent, db_path=db)["status"] == "waiting_approval"
    assert store.remove_task_dep(dependent, dead_dep, db_path=db) is True
    assert store.get_task(dependent, db_path=db)["status"] == "ready"
    assert store.remove_task_dep(dependent, dead_dep, db_path=db) is False
    assert "task_undep" in _activity_actions(store, db, dependent)


def test_goal_delete_is_guarded_and_audited(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    _setup(store, db)
    g = store.create_goal("demo", "Doomed draft", db_path=db)
    gid = g if isinstance(g, int) else g["id"]
    tid = store.create_task("demo", "Child", "", "", goal_id=gid, db_path=db)
    with pytest.raises(ValueError, match="referenced"):
        store.delete_goal(gid, db_path=db)
    con = store._connect(db)
    try:
        with con:                                   # detach the child for the test
            con.execute("UPDATE tasks SET goal_id=NULL WHERE id=?", (tid,))
    finally:
        con.close()
    g2 = store.create_goal("demo", "Released one", db_path=db)
    gid2 = g2 if isinstance(g2, int) else g2["id"]
    store.set_goal_status(gid2, "planned", force=True, db_path=db)
    store.release_goal(gid2, db_path=db)
    with pytest.raises(ValueError, match="only 'draft'"):
        store.delete_goal(gid2, db_path=db)
    title = store.delete_goal(gid, db_path=db)
    assert title == "Doomed draft"
    con = store._connect(db)
    try:
        assert con.execute("SELECT 1 FROM goals WHERE id=?", (gid,)).fetchone() is None
        act = con.execute("SELECT 1 FROM activity WHERE action='goal_deleted' "
                          "AND detail LIKE ?", ("%%#%d %%" % gid,)).fetchone()
    finally:
        con.close()
    assert act is not None
