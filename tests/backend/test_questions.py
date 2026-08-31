"""Group 10: mid-run question detection (fence + capped heuristic) for dispatched runs."""
import json, os, sqlite3, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_chat import env, login  # noqa: F401
from tests.backend.test_chat_sessions import _seed_state_db

FENCE = ('Some context first.\n```hq-options\n{"question": "Deploy to staging or prod?", '
         '"mode": "single", "options": [{"label": "staging"}, {"label": "prod"}]}\n```\nWaiting?')


def _mk_run(store, agent, slug, session_id=None):
    db = store.DEFAULT_DB_PATH
    store.create_project(slug, slug.title(), "", "/tmp/" + slug, db_path=db)
    t = store.create_task(slug, "Task " + slug, "", "", db_path=db)
    tid = t if isinstance(t, int) else t["id"]
    rid = store.start_run(tid, agent, db_path=db)
    if session_id:
        con = store._connect(db)
        with con:
            con.execute("UPDATE runs SET session_id=? WHERE id=?", (session_id, rid))
        con.close()
    return tid, rid


def _rows(store, prefix="runq:"):
    rows, _ = store.list_notifications(limit=100, db_path=store.DEFAULT_DB_PATH)
    return [r for r in rows if (r.get("source_key") or "").startswith(prefix)]


def test_fence_detected_deduped_and_beats_heuristic(env):
    c, store, gw, root = env
    from backend import questions
    db = store.DEFAULT_DB_PATH
    _seed_state_db(root, "coder", "qs1", "wm-run-x", [
        ("user", "BRIEF: do the thing. Ready?", None),      # user text never fires
        ("assistant", "Working on it.", None),               # no question
        ("assistant", FENCE, None),                          # fence (message also ends with '?')
    ])
    tid, rid = _mk_run(store, "coder", "demoq1", session_id="qs1")
    assert questions.scan_running_runs(db_path=db) == 1
    rows = _rows(store)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "question" and r["body"] == "Deploy to staging or prod?"
    assert r["href"] == "/chat/coder/qs1" and r["run_id"] == rid and r["task_id"] == tid
    assert r["source_key"].startswith("runq:%s:" % rid) and not r["source_key"].endswith("heuristic")
    # rescan: watermark + idempotent source_key -> nothing new
    assert questions.scan_running_runs(db_path=db) == 0 and len(_rows(store)) == 1
    marks = json.loads(store.get_meta(questions.META_KEY, db_path=db))
    assert str(rid) in marks and marks[str(rid)] > 0


def test_heuristic_capped_at_one_per_run(env):
    c, store, gw, root = env
    from backend import questions
    db = store.DEFAULT_DB_PATH
    _seed_state_db(root, "coder", "qs2", "wm-run-y", [
        ("assistant", "Should I include the pricing table?", None),
        ("assistant", "Also: which currency should the totals use?", None),
    ])
    _, rid = _mk_run(store, "coder", "demoq2", session_id="qs2")
    assert questions.scan_running_runs(db_path=db) == 1
    rows = _rows(store)
    assert len(rows) == 1 and rows[0]["source_key"] == "runq:%s:heuristic" % rid
    assert rows[0]["title"] == "coder may need you (run #%s)" % rid
    assert rows[0]["body"] == "Should I include the pricing table?"


def test_orchestrator_marker_session_in_root_db(env):
    c, store, gw, root = env
    from backend import questions
    db = store.DEFAULT_DB_PATH
    _, rid = _mk_run(store, "orchestrator", "demoq3")   # no captured session_id
    # orchestrator sessions live in the ROOT state.db (sibling of profiles/)
    con = sqlite3.connect(root / "state.db")
    con.executescript("""
      CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL);
      CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, active INTEGER);
    """)
    con.execute("INSERT INTO sessions VALUES ('os1', 'wm-run-%s', %f)" % (rid, time.time()))
    con.execute("INSERT INTO messages (session_id, role, content, active) VALUES ('os1', 'assistant', ?, 1)", (FENCE,))
    con.commit(); con.close()
    assert questions.scan_running_runs(db_path=db) == 1
    rows = _rows(store)
    assert len(rows) == 1 and rows[0]["href"] == "/chat/orchestrator/os1"


def test_never_raises_and_prunes_finished_runs(env):
    c, store, gw, root = env
    from backend import questions
    db = store.DEFAULT_DB_PATH
    # missing profile db, and a session id that exists nowhere: both just skip
    _, rid1 = _mk_run(store, "ghost", "demoq4")
    _, rid2 = _mk_run(store, "coder", "demoq5", session_id="does-not-exist")
    # ghost profile dir does not even exist -> agent_session_db_path miss
    assert questions.scan_running_runs(db_path=db) == 0 and _rows(store) == []
    marks = json.loads(store.get_meta(questions.META_KEY, db_path=db))
    assert set(marks) == {str(rid1), str(rid2)}
    # finish one run -> its watermark is pruned on the next scan
    con = store._connect(db)
    with con:
        con.execute("UPDATE runs SET status='done' WHERE id=?", (rid1,))
    con.close()
    questions.scan_running_runs(db_path=db)
    marks = json.loads(store.get_meta(questions.META_KEY, db_path=db))
    assert set(marks) == {str(rid2)}


def test_brief_carries_ask_owner_section(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    _, rid = _mk_run(store, "coder", "demoq6")
    brief = store.render_brief(rid, db_path=db)
    assert "ASKING THE OWNER" in brief and "hq-options" in brief
    assert brief.index("ASKING THE OWNER") < brief.index("COMPLETION CONTRACT")


def test_answer_endpoint_and_task_question_field(env):
    c, store, gw, root = env
    from backend import questions
    db = store.DEFAULT_DB_PATH
    h = login(c)
    _seed_state_db(root, "coder", "qs9", "wm-run-z", [("assistant", FENCE, None)])
    tid, rid = _mk_run(store, "coder", "demoq9", session_id="qs9")
    questions.scan_running_runs(db_path=db)
    # task payload carries the open question
    t = c.get("/api/task/%d" % tid).json()
    assert t["question"]["body"] == "Deploy to staging or prod?" and t["question"]["run_id"] == rid
    assert t["question"]["href"] == "/chat/coder/qs9"
    # answer: file appended (twice), notifications marked read, question gone
    r = c.post("/api/run/%d/answer" % rid, json={"message": "prod please"}, headers=h)
    assert r.status_code == 200 and r.json()["marked_read"] == 1
    assert c.post("/api/run/%d/answer" % rid, json={"message": "and be careful"}, headers=h).status_code == 200
    content = open(store.answer_path(rid)).read()
    assert "prod please" in content and "and be careful" in content and content.count("[owner ") == 2
    assert c.get("/api/task/%d" % tid).json()["question"] is None
    # the brief names the concrete answer file
    assert store.answer_path(rid) in store.render_brief(rid, db_path=db)
    # guards: unknown run 404, finished run 409
    assert c.post("/api/run/999/answer", json={"message": "x"}, headers=h).status_code == 404
    con = store._connect(db)
    with con:
        con.execute("UPDATE runs SET status='done' WHERE id=?", (rid,))
    con.close()
    assert c.post("/api/run/%d/answer" % rid, json={"message": "late"}, headers=h).status_code == 409


def test_fence_in_codex_message_items(env):
    """Codex-style models keep pre-tool reply text in codex_message_items with
    content='' (observed live) — the fence must be detected there too."""
    import sqlite3 as _sq
    c, store, gw, root = env
    from backend import questions
    db = store.DEFAULT_DB_PATH
    _seed_state_db(root, "coder", "qsc", "wm-run-c", [("user", "brief", None)])
    scon = _sq.connect(root / "profiles" / "coder" / "state.db")
    scon.execute("ALTER TABLE messages ADD COLUMN codex_message_items TEXT")
    items = json.dumps([{"type": "message", "role": "assistant", "phase": "commentary",
                         "content": [{"type": "output_text", "text": FENCE}]}])
    scon.execute("INSERT INTO messages (session_id, role, content, timestamp, active, codex_message_items) "
                 "VALUES ('qsc', 'assistant', '', 1, 1, ?)", (items,))
    scon.commit(); scon.close()
    _, rid = _mk_run(store, "coder", "demoqc", session_id="qsc")
    assert questions.scan_running_runs(db_path=db) == 1
    rows = _rows(store)
    assert any(r["run_id"] == rid and r["body"] == "Deploy to staging or prod?" and
               not r["source_key"].endswith("heuristic") for r in rows)


def test_mark_stalled_label_names_the_origin(env):
    """Owner stops must not read as liveness failures in Task history."""
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    for slug, label, want in (("lbl1", None, "liveness: boom"), ("lbl2", "owner stop", "owner stop: boom")):
        tid, rid = _mk_run(store, "coder", slug)
        con = store._connect(db)
        with con:
            con.execute("UPDATE tasks SET status='running' WHERE id=?", (tid,))
        con.close()
        if label:
            store.mark_stalled(rid, tid, "boom", db_path=db, label=label)
        else:
            store.mark_stalled(rid, tid, "boom", db_path=db)
        con = store._connect(db)
        d = con.execute("SELECT detail FROM state_transitions WHERE task_id=? AND to_status='stalled'", (tid,)).fetchone()["detail"]
        con.close()
        assert d == want
