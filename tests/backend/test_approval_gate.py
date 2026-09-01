"""Approval gate: owner_approval field + the 'manual' completion verdict.

A gated task never closes itself: completions (and approved reviews) land on
'manual' — "Awaiting approval" — and only the owner's close/feedback moves it.
"""
import json
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_chat import env, login  # noqa: F401


def _setup(store, db):
    try:
        store.create_project("demo", "Demo", "", "/tmp/demo", db_path=db)
    except Exception:
        pass


def _run(store, db, tid, profile="coder"):
    store.mark_ready(tid, db_path=db)
    store.claim_task(tid, db_path=db)
    return store.start_run(tid, profile, db_path=db)


def test_gate_lands_manual_and_notifies(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    _setup(store, db)
    store.sync_notifications(db_path=db)  # set the watermark
    g = store.create_goal("demo", "Gated goal", db_path=db)
    gid = g if isinstance(g, int) else g["id"]
    store.set_goal_status(gid, "planned", force=True, db_path=db)
    store.release_goal(gid, db_path=db)
    tid = store.create_task("demo", "Plan the fix", "", "", owner_approval=True,
                            db_path=db)
    dependent = store.create_task("demo", "Implement", "", "", goal_id=gid,
                                  db_path=db)
    store.add_task_dep(dependent, tid, db_path=db)
    assert store.get_task(dependent, db_path=db)["status"] == "waiting_approval"
    rid = _run(store, db, tid)
    ts, rs = store.record_completion(rid, tid, "done", summary="plan written",
                                     result_paths=["/tmp/demo/plan.md"],
                                     db_path=db)
    assert (ts, rs) == ("manual", "done")
    t = store.get_task(tid, db_path=db)
    assert t["status"] == "manual" and t["owner_approval"] == 1
    # no review was spawned (policy none) and the dependent stayed parked
    assert store.list_reviews(task_id=tid, db_path=db) == []
    assert store.get_task(dependent, db_path=db)["status"] == "waiting_approval"
    # the manual transition raised an unread needs_you notification
    made = store.sync_notifications(db_path=db)
    rows, unread = store.list_notifications(db_path=db)
    ours = [r for r in rows if r["id"] in made and r["task_id"] == tid]
    assert ours and ours[0]["kind"] == "needs_you"
    # readable results: the result_path resolves to a files-API address
    s = login(c)
    detail = c.get("/api/task/%d" % tid, headers=s).json()
    art = detail["artifacts"][0]
    assert art["path"] == "/tmp/demo/plan.md"
    assert art["root"] == "project:demo" and art["rel"] == "plan.md"
    assert detail["human"]["label"] == "Awaiting approval"
    assert detail["human"]["state"] == "needsyou"
    # approve: owner close promotes the dependent (its only dep is now done)
    promoted = store.close_by_owner(tid, note="approved", db_path=db)
    assert store.get_task(tid, db_path=db)["status"] == "done"
    assert dependent in promoted


def test_gate_fires_after_review_approval(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    _setup(store, db)
    tid = store.create_task("demo", "Gated + reviewed", "", "",
                            review_policy="required", owner_approval=True,
                            db_path=db)
    rid = _run(store, db, tid)
    ts, rs = store.record_completion(rid, tid, "done", db_path=db)
    assert ts == "needs_review"  # review still precedes the owner
    ts2, rev_status, promoted = store.review_verdict(tid, "approved",
                                                     comment="LGTM", db_path=db)
    assert ts2 == "manual" and rev_status == "done" and promoted == []
    t = store.get_task(tid, db_path=db)
    assert t["status"] == "manual"
    con = store._connect(db)
    try:
        tr = con.execute(
            "SELECT detail FROM state_transitions WHERE task_id=? AND "
            "to_status='manual' ORDER BY id DESC", (tid,)).fetchone()
    finally:
        con.close()
    assert "awaiting owner approval" in tr["detail"]
    # feedback path from the gate: manual -> rework with the owner's words
    store.owner_feedback(tid, "tighten the caching slice", db_path=db)
    assert store.get_task(tid, db_path=db)["status"] == "rework"


def test_manual_verdict_hands_over_without_review(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    _setup(store, db)
    tid = store.create_task("demo", "Undeclared hand-over", "", "",
                            review_policy="required", db_path=db)
    rid = _run(store, db, tid)
    ts, rs = store.record_completion(rid, tid, "manual",
                                     blocker="need owner to pick option A/B",
                                     db_path=db)
    assert (ts, rs) == ("manual", "done")
    t = store.get_task(tid, db_path=db)
    assert t["status"] == "manual"
    # the hand-over sets the gate: the landing reads "Awaiting approval" and
    # the continuation stays gated until the owner untoggles it
    assert t["owner_approval"] == 1
    # never routes to review — the reviewer's turn comes on real done
    assert store.list_reviews(task_id=tid, db_path=db) == []


def test_review_run_cannot_hand_over(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    _setup(store, db)
    from core import wm_run_agent as wra
    tid = store.create_task("demo", "Reviewed work", "", "",
                            review_policy="required", db_path=db)
    rid = _run(store, db, tid)
    store.record_completion(rid, tid, "done", db_path=db)
    review = store.get_open_review(tid, db_path=db)
    rrun = store.start_run(tid, "reviewer", db_path=db)
    with open(store.completion_path(rrun), "w") as f:
        json.dump({"completed": "manual", "summary": "", "result_paths": [],
                   "blocker": "handing over"}, f)
    status = wra._finalize(rrun, tid, 0, db, review_id=review["id"])
    assert status == "failed"


def test_edit_task_toggles_gate_with_audit(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    _setup(store, db)
    tid = store.create_task("demo", "Toggle me", "", "", db_path=db)
    assert store.edit_task(tid, owner_approval=True, db_path=db) == \
        ["owner_approval"]
    assert store.get_task(tid, db_path=db)["owner_approval"] == 1
    # idempotent: same value edits nothing
    assert store.edit_task(tid, owner_approval=True, db_path=db) == []
    con = store._connect(db)
    try:
        row = con.execute(
            "SELECT detail FROM activity WHERE task_id=? AND "
            "action='task_edited' ORDER BY id DESC", (tid,)).fetchone()
    finally:
        con.close()
    assert "owner_approval was: 0" in row["detail"]
    # API round-trip honours the same gates
    s = login(c)
    r = c.post("/api/task/%d/edit" % tid, json={"owner_approval": False},
               headers=s)
    assert r.status_code == 200
    assert store.get_task(tid, db_path=db)["owner_approval"] == 0
