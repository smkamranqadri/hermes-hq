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
    # A run that ASKED stays answerable after its question is read (2026-09-07:
    # the read state was the token, so tapping the ping in the Inbox made the
    # owner's own answer undeliverable). Blocking again with the same question
    # on record therefore takes a second answer — what protects the endpoint is
    # 'this run asked', not 'nobody has looked at the ping yet'.
    con = store._connect(db)
    with con:
        con.execute("UPDATE tasks SET status='blocked' WHERE id=?", (tid,))
        con.execute("UPDATE runs SET status='blocked' WHERE id=?", (rid,))
    con.close()
    assert c.post(f"/api/run/{rid}/answer", json={"message": "late"}, headers=h).status_code == 200


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


def test_answer_survives_a_read_notification(env, monkeypatch):
    """The owner tapping the question in the Inbox (which marks it read) must
    not make their answer undeliverable."""
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    h = login(c)
    tid, rid = _mk_run(store, "coder", "blocked-read")
    store.finish_run(rid, "blocked", error="Which colour?", db_path=db)
    store.complete_run(tid, "blocked", error="Which colour?", run_id=rid, db_path=db)
    store.add_notification("question", "Question", "Which colour?", "/chat/coder/s",
                           task_id=tid, run_id=rid, source_key=f"runq:{rid}:1", db_path=db)
    store.mark_notifications_read(db_path=db)                  # as the Inbox does
    monkeypatch.setattr("core.wm_dispatch._launch", lambda *a, **k: True)
    r = c.post(f"/api/run/{rid}/answer", json={"message": "violet"}, headers=h)
    assert r.status_code == 200 and r.json()["resumed"] is True
    assert store.get_run(rid, db_path=db)["status"] == "running"


def test_rework_cap_parks_task_instead_of_redispatching(env):
    """#98 ran 31 rework cycles / 63 runs with nothing bounding the loop. At the
    cap the task lands on the owner's desk with the reviewer's words."""
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    tid, rid = _mk_run(store, "coder", "cap-demo")
    con = store._connect(db)
    with con:
        con.execute("UPDATE tasks SET review_policy='required' WHERE id=?", (tid,))
    con.close()
    seen = []
    for i in range(store.MAX_REWORK_CYCLES):
        store.create_review(tid, review_policy="required", db_path=db)
        status, _rs, _p = store.review_verdict(
            tid, "changes_requested", comment="fix issue %d" % i, db_path=db)
        seen.append(status)
        if status == "rework":                       # dispatcher would re-claim
            con = store._connect(db)
            with con:
                con.execute("UPDATE tasks SET status='needs_review' WHERE id=?", (tid,))
            con.close()
    assert seen[:-1] == ["rework"] * (store.MAX_REWORK_CYCLES - 1)
    assert seen[-1] == "manual"                      # parked, not re-dispatched
    t = store.get_task(tid, db_path=db)
    assert t["status"] == "manual" and "fix issue" in (t["feedback"] or "")
    rows, _ = store.list_notifications(limit=50, db_path=db)
    capped = [r for r in rows if (r.get("source_key") or "") == "reworkcap:%d" % tid]
    assert len(capped) == 1 and "rework cycles" in capped[0]["title"]
    # and a capped task is NOT claimable by the dispatcher
    assert tid not in [x["id"] for x in store.next_ready_tasks(50, db_path=db)]


def test_brief_tells_the_agent_to_wait_before_parking(env):
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    tid, rid = _mk_run(store, "coder", "brief-wait")
    brief = store.render_brief(rid, db_path=db)
    assert "%d minutes" % (store.OWNER_ANSWER_WAIT_SECONDS // 60) in brief
    assert "resumes THIS run" in brief
    assert "Never end the run the moment you ask" in brief
