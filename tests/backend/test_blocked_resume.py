from tests.backend.test_chat import env, login
from tests.backend.test_questions import _mk_run


def test_blocked_question_answer_resumes_same_run_through_api(env, monkeypatch):
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    h = login(c)
    tid, rid = _mk_run(store, "coder", "blocked-api")
    store.finish_run(rid, "blocked", error="owner input", db_path=db)
    store.complete_run(tid, "blocked", error="owner input", run_id=rid, db_path=db)
    store.add_notification("question", "Question", "Choose A or B", "/chat/coder/s",
                           task_id=tid, run_id=rid, source_key=f"runq:{rid}:1", db_path=db)
    launched = []
    monkeypatch.setattr("core.wm_dispatch._launch",
                        lambda *args, **kwargs: launched.append(args[0]) or True)
    response = c.post(f"/api/run/{rid}/answer", json={"message": "A"}, headers=h)
    assert response.status_code == 200
    assert response.json()["resumed"] is True
    assert launched == [rid]
    assert store.get_run(rid, db_path=db)["status"] == "running"
    assert store.get_task(tid, db_path=db)["status"] == "running"
    # A later blocked state without a fresh unread question cannot reuse the
    # consumed question as an authorization token.
    con = store._connect(db)
    with con:
        con.execute("UPDATE tasks SET status='blocked' WHERE id=?", (tid,))
        con.execute("UPDATE runs SET status='blocked' WHERE id=?", (rid,))
    con.close()
    assert c.post(f"/api/run/{rid}/answer", json={"message": "late"}, headers=h).status_code == 409


def test_blocked_without_question_stays_protected(env):
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    h = login(c)
    tid, rid = _mk_run(store, "coder", "blocked-no-question")
    store.finish_run(rid, "blocked", error="ordinary block", db_path=db)
    store.complete_run(tid, "blocked", error="ordinary block", run_id=rid, db_path=db)
    response = c.post(f"/api/run/{rid}/answer", json={"message": "guess"}, headers=h)
    assert response.status_code == 409
    assert store.get_run(rid, db_path=db)["status"] == "blocked"
    assert store.get_task(tid, db_path=db)["status"] == "blocked"
