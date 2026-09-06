import threading

import pytest

from core import wm_store as store


def blocked_run(tmp_path, monkeypatch):
    monkeypatch.setenv("WM_RUNS_DIR", str(tmp_path / "runs"))
    db = str(tmp_path / "wm.db")
    store.init_db(db_path=db)
    store.create_project("p", "P", primary_path=str(tmp_path), db_path=db)
    tid = store.create_task("p", "question", db_path=db)
    store.mark_ready(tid, db_path=db)
    store.claim_task(tid, db_path=db)
    rid = store.start_run(tid, "coder", db_path=db)
    store.finish_run(rid, "blocked", error="need owner", db_path=db)
    store.complete_run(tid, "blocked", error="need owner", run_id=rid, db_path=db)
    store.add_notification("question", "Question", "pick one", "/chat/coder/s",
                           task_id=tid, run_id=rid, source_key=f"runq:{rid}:1",
                           db_path=db)
    return db, tid, rid


def test_resume_preserves_blocked_history_and_same_run(tmp_path, monkeypatch):
    db, tid, rid = blocked_run(tmp_path, monkeypatch)
    assert store.resume_blocked_run(rid, "pick A", db_path=db)["id"] == rid
    assert store.get_run(rid, db_path=db)["status"] == "running"
    assert store.get_task(tid, db_path=db)["status"] == "running"
    transitions = store.list_transitions(tid, db_path=db)
    assert [(x["from_status"], x["to_status"], x["run_id"]) for x in transitions[:2]] == [
        ("blocked", "running", rid), ("running", "blocked", rid)]
    assert "pick A" in open(store.answer_path(rid), encoding="utf-8").read()


def test_read_or_duplicate_question_cannot_resume_again(tmp_path, monkeypatch):
    db, tid, rid = blocked_run(tmp_path, monkeypatch)
    store.mark_run_questions_read(rid, db_path=db)
    with pytest.raises(ValueError, match="current unanswered"):
        store.resume_blocked_run(rid, "late", db_path=db)
    # Re-blocking without a fresh question remains protected.
    con = store._connect(db)
    with con:
        con.execute("UPDATE tasks SET status='blocked' WHERE id=?", (tid,))
        con.execute("UPDATE runs SET status='blocked' WHERE id=?", (rid,))
    con.close()
    with pytest.raises(ValueError, match="current unanswered"):
        store.resume_blocked_run(rid, "duplicate", db_path=db)


def test_concurrent_answers_only_one_claims_run(tmp_path, monkeypatch):
    db, tid, rid = blocked_run(tmp_path, monkeypatch)
    results = []

    def answer(text):
        try:
            store.resume_blocked_run(rid, text, db_path=db)
            results.append("ok")
        except ValueError:
            results.append("rejected")

    a = threading.Thread(target=answer, args=("A",)); b = threading.Thread(target=answer, args=("B",))
    a.start(); b.start(); a.join(); b.join()
    assert sorted(results) == ["ok", "rejected"]
    assert store.get_run(rid, db_path=db)["status"] == "running"


def test_failure_finalization_closes_run_and_links_audit(tmp_path, monkeypatch):
    db, tid, rid = blocked_run(tmp_path, monkeypatch)
    # Reopen only to model a resumed process whose brief cannot be read.
    store.resume_blocked_run(rid, "continue", db_path=db)
    assert store.fail_run(rid, tid, "brief missing", db_path=db) == 1
    run = store.get_run(rid, db_path=db)
    task = store.get_task(tid, db_path=db)
    assert run["status"] == task["status"] == "failed"
    assert any(x["to_status"] == "failed" and x["run_id"] == rid
               for x in store.list_transitions(tid, db_path=db))
