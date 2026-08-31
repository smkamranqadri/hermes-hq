"""WM-fix Fast sweep: mark_ready guards, notification auto-read, orphan-review audit."""
import os, sys, time
import pytest
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_chat import env, login  # noqa: F401
from tests.backend.test_notifications import _transition


def _mk_task(store, db, title="Guarded"):
    try:
        store.create_project("demo", "Demo", "", "/tmp/demo", db_path=db)
    except Exception:
        pass  # already created earlier in the same test
    t = store.create_task("demo", title, "", "", db_path=db)
    return t if isinstance(t, int) else t["id"]


def _transition_count(store, db, tid):
    con = store._connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM state_transitions WHERE task_id=?",
                           (tid,)).fetchone()[0]
    finally:
        con.close()


def test_mark_ready_noops_on_ready_and_refuses_running(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    tid = _mk_task(store, db)
    store.mark_ready(tid, db_path=db)
    assert store.get_task(tid, db_path=db)["status"] == "ready"
    before = _transition_count(store, db, tid)
    store.mark_ready(tid, db_path=db)          # already ready → silent no-op
    assert _transition_count(store, db, tid) == before
    store.claim_task(tid, db_path=db)
    with pytest.raises(ValueError, match="running"):
        store.mark_ready(tid, db_path=db)       # the #109 double-claim shape
    assert store.get_task(tid, db_path=db)["status"] == "running"
    assert _transition_count(store, db, tid) == before + 1  # only the claim


def test_transition_autoreads_older_attention_rows(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    tid = _mk_task(store, db)
    assert store.sync_notifications(db_path=db) == []       # watermark only
    _transition(store, tid, "running", "needs_review", "contract")
    assert len(store.sync_notifications(db_path=db)) == 1
    rows, unread = store.list_notifications(db_path=db)
    assert unread == 1 and rows[0]["kind"] == "needs_you" and rows[0]["read_at"] is None
    # the task moves on; a question scanned AFTER that transition must survive
    _transition(store, tid, "needs_review", "running", "claimed")
    time.sleep(0.01)
    store.add_notification("question", "Deploy where?", task_id=tid, run_id=1,
                           source_key="runq:1:1", db_path=db)
    store.sync_notifications(db_path=db)
    rows, unread = store.list_notifications(db_path=db)
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["needs_you"]["read_at"] is not None       # stale row retired
    assert by_kind["question"]["read_at"] is None            # fresh question kept
    assert unread == 1
    # the NEXT transition retires the question too and raises a fresh row
    time.sleep(0.01)
    _transition(store, tid, "running", "needs_review", "contract again")
    assert len(store.sync_notifications(db_path=db)) == 1
    rows, unread = store.list_notifications(db_path=db)
    assert unread == 1
    fresh = [r for r in rows if r["read_at"] is None]
    assert len(fresh) == 1 and fresh[0]["kind"] == "needs_you" and "again" in fresh[0]["body"]
    q = [r for r in rows if r["kind"] == "question"][0]
    assert q["read_at"] is not None


def test_check_flags_orphan_review_and_close_path(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    tid = _mk_task(store, db)
    con = store._connect(db)
    try:
        with con:
            con.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
            con.execute("INSERT INTO reviews(task_id, reviewer_profile, status, "
                        "requested_at, review_policy) VALUES (?,?,?,?,?)",
                        (tid, "reviewer", "reviewed", time.time(), "required"))
    finally:
        con.close()
    store.record_transition(tid, "done", from_status="needs_review", detail="test close")
    res = store.check_integrity(db_path=db)
    assert not res["ok"]
    orphan = [f for f in res["findings"] if "orphaned review" in f]
    assert len(orphan) == 1 and ("task #%d" % tid) in orphan[0] and "--close-orphan" in orphan[0]
    # only done tasks qualify; a second close finds nothing open
    rid = store.close_orphan_review(tid, comment="stuck since reviewer run", db_path=db)
    rev = [r for r in store.list_reviews(task_id=tid, db_path=db) if r["id"] == rid][0]
    assert rev["status"] == "waived" and rev["verdict"] == "waived"
    assert store.get_task(tid, db_path=db)["status"] == "done"
    res2 = store.check_integrity(db_path=db)
    assert res2["ok"], res2["findings"]
    with pytest.raises(ValueError, match="no open review"):
        store.close_orphan_review(tid, db_path=db)
    other = _mk_task(store, db, title="Not done")
    with pytest.raises(ValueError, match="not 'done'"):
        store.close_orphan_review(other, db_path=db)
