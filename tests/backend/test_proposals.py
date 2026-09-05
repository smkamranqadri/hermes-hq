"""Second Brain Phase 2a: librarian proposals + heartbeat ingest.

Proves the 2a DoD lines: the librarian's ONLY write is a proposal (notes stay
owner-session-only; the `wm note` CLI has no note-write command), split/file
proposals round-trip through owner approval, rejection feedback is readable
back through the librarian surface, and a heartbeat schedule with nothing new
records a skipped firing WITHOUT minting a task (= no agent run, no model call).
"""
import io
import json
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    os.makedirs(tmp_path / "alpha")
    store.create_project("alpha", "Alpha", "", str(tmp_path / "alpha"), db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        yield c, store, store.DEFAULT_DB_PATH


def login(c):
    r = c.post("/api/login", json={"password": "pw-test"})
    assert r.status_code == 200
    return {"x-csrf": r.json()["csrf"]}


def area_id(store, db, name):
    return next(a["id"] for a in store.list_areas(db_path=db) if a["name"] == name)


# ---- the write wall -----------------------------------------------------

def test_proposal_routes_require_owner_session(env):
    c, store, db = env
    nid = store.create_note("x", db_path=db)
    pid = store.create_proposal("file", nid, {"area_id": area_id(store, db, "Work")}, db_path=db)
    assert c.get("/api/proposals").status_code == 401
    assert c.post("/api/proposal/%d/approve" % pid).status_code == 401
    h = login(c)
    assert c.post("/api/proposal/%d/approve" % pid).status_code == 403   # no CSRF
    assert c.post("/api/proposal/%d/approve" % pid, headers=h).status_code == 200


def test_librarian_surface_cannot_write_notes(env):
    """The agent surface (store propose fns + `wm note` CLI) never touches the
    note tables — a proposal leaves its note byte-identical until approval."""
    c, store, db = env
    nid = store.create_note("Batch dump", body="a\nb", db_path=db)
    before = store.get_note(nid, db_path=db)
    store.create_proposal("file", nid, {"area_id": area_id(store, db, "Work")}, db_path=db)
    assert store.get_note(nid, db_path=db) == before
    # and the CLI's note group offers no write verb at all
    from core import wm_cli
    note_parser = [a for a in wm_cli.build_parser()._subparsers._group_actions[0]
                   .choices["note"]._subparsers._group_actions[0].choices]
    assert set(note_parser) == {"inbox", "show", "areas", "tags", "proposals",
                                "propose-file", "propose-split"}


# ---- propose + validate -------------------------------------------------

def test_propose_validation(env):
    c, store, db = env
    nid = store.create_note("n", db_path=db)
    with pytest.raises(ValueError):
        store.create_proposal("nope", nid, {}, db_path=db)               # bad kind
    with pytest.raises(ValueError):
        store.create_proposal("file", 999, {"area_id": 1}, db_path=db)   # no note
    with pytest.raises(ValueError):
        store.create_proposal("file", nid, {}, db_path=db)               # empty filing
    with pytest.raises(ValueError):
        store.create_proposal("file", nid, {"area_id": 9999}, db_path=db)  # bad area
    with pytest.raises(ValueError):
        store.create_proposal("split", nid, {"parts": []}, db_path=db)   # no parts
    with pytest.raises(ValueError):
        store.create_proposal("split", nid, {"parts": [{"body": "x"}]}, db_path=db)  # no title
    with pytest.raises(ValueError):
        store.create_proposal("file", nid, {"area_id": 1, "title": "re"}, db_path=db)  # file can't rewrite


def test_new_proposal_supersedes_pending_same_kind(env):
    c, store, db = env
    nid = store.create_note("n", db_path=db)
    work = area_id(store, db, "Work")
    p1 = store.create_proposal("file", nid, {"area_id": work}, db_path=db)
    p2 = store.create_proposal("file", nid, {"area_id": area_id(store, db, "Home")}, db_path=db)
    assert store.get_proposal(p1, db_path=db)["status"] == "superseded"
    assert store.get_proposal(p2, db_path=db)["status"] == "pending"


def test_inbox_list_marks_pending_proposal(env):
    """Note lists carry pending_proposal_id so the UI can say 'librarian
    proposed' instead of inviting a double-filing (owner confusion, 2026-09-06)."""
    c, store, db = env
    h = login(c)
    covered = store.create_note("covered", db_path=db)
    bare = store.create_note("bare", db_path=db)
    pid = store.create_proposal("file", covered, {"area_id": area_id(store, db, "Work")}, db_path=db)
    rows = {n["title"]: n for n in c.get("/api/notes", params={"status": "inbox"}).json()["notes"]}
    assert rows["covered"]["pending_proposal_id"] == pid
    assert rows["bare"]["pending_proposal_id"] is None
    # deciding the proposal clears the mark
    c.post("/api/proposal/%d/approve" % pid, headers=h)
    rows = {n["title"]: n for n in c.get("/api/notes", params={"status": "inbox"}).json()["notes"]}
    assert "covered" not in rows                       # filed out of the inbox
    assert rows["bare"]["pending_proposal_id"] is None


def test_proposal_notifies_owner(env):
    c, store, db = env
    h = login(c)
    nid = store.create_note("Payments idea", db_path=db)
    store.create_proposal("file", nid, {"area_id": area_id(store, db, "Work")},
                          summary="clearly work", db_path=db)
    rows = c.get("/api/notifications").json()["notifications"]
    row = next(n for n in rows if "Librarian" in n["title"])
    assert row["kind"] == "needs_you" and row["href"] == "/brain/review"


# ---- approve / reject round trips --------------------------------------

def test_file_proposal_approve_files_note(env):
    c, store, db = env
    h = login(c)
    nid = store.create_note("Gym plan", body="3x week", db_path=db)
    pid = store.create_proposal(
        "file", nid, {"area_id": area_id(store, db, "Health"), "tags": ["fitness"]},
        summary="health note", classification="routine", db_path=db)
    r = c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert r.status_code == 200
    n = store.get_note(nid, db_path=db)
    assert n["status"] == "active" and n["area"]["name"] == "Health" and n["tags"] == ["fitness"]
    p = r.json()["proposal"]
    assert p["status"] == "approved" and p["decided_at"]
    # revision snapshot credits the librarian
    revs = store._connect(db).execute(
        "SELECT edited_by FROM note_revisions WHERE note_id=?", (nid,)).fetchall()
    assert [x["edited_by"] for x in revs] == ["librarian"]


def test_split_proposal_round_trip(env):
    c, store, db = env
    h = login(c)
    work = area_id(store, db, "Work")
    body = "dentist tuesday\n\nSimpliEd pricing: tier idea\n\nUrdu: نوٹ لکھنا"
    nid = store.create_note("brain dump", body=body, db_path=db)
    parts = [
        {"title": "Dentist Tuesday", "body": "dentist tuesday"},                    # unfiled -> inbox
        {"title": "SimpliEd pricing", "body": "SimpliEd pricing: tier idea",
         "area_id": work, "tags": ["pricing"]},                                     # filed -> active
        {"title": "Urdu note", "body": "Urdu: نوٹ لکھنا", "area_id": area_id(store, db, "Journal")},
    ]
    pid = store.create_proposal("split", nid, {"parts": parts},
                                summary="3 items in one dump", db_path=db)
    r = c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert r.status_code == 200
    ids = r.json()["proposal"]["result"]["note_ids"]
    assert len(ids) == 3
    made = [store.get_note(i, db_path=db) for i in ids]
    assert [m["status"] for m in made] == ["inbox", "active", "active"]
    assert made[1]["tags"] == ["pricing"] and made[0]["authored_by"] == "owner"
    assert store.get_note(nid, db_path=db)["status"] == "archived"       # original archived
    # split parts are searchable
    hits = store.search_notes("نوٹ", db_path=db)
    assert any(x["id"] == ids[2] for x in hits)


def test_split_keep_original(env):
    c, store, db = env
    h = login(c)
    nid = store.create_note("keep me", body="x", db_path=db)
    pid = store.create_proposal("split", nid,
                                {"parts": [{"title": "part", "body": "x"}],
                                 "archive_original": False}, db_path=db)
    c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert store.get_note(nid, db_path=db)["status"] == "inbox"


def test_reject_stores_feedback_librarian_reads_it(env):
    c, store, db = env
    h = login(c)
    nid = store.create_note("mixed", db_path=db)
    pid = store.create_proposal("file", nid, {"area_id": area_id(store, db, "Work")}, db_path=db)
    r = c.post("/api/proposal/%d/reject" % pid,
               json={"feedback": "this is Family, not Work"}, headers=h)
    assert r.status_code == 200
    # note untouched, proposal rejected with feedback on the row
    assert store.get_note(nid, db_path=db)["status"] == "inbox"
    rej = store.list_proposals(status="rejected", db_path=db)
    assert rej[0]["id"] == pid and rej[0]["feedback"] == "this is Family, not Work"
    # the librarian's CLI read shows the feedback
    from core import wm_cli
    buf = io.StringIO()
    real = sys.stdout
    sys.stdout = buf
    try:
        assert wm_cli.main(["note", "proposals", "--status", "rejected"]) == 0
    finally:
        sys.stdout = real
    assert "this is Family, not Work" in buf.getvalue()


def test_approve_is_atomic_on_stale_payload(env):
    """A stale proposal (its area vanished) must fail BEFORE any note changes."""
    c, store, db = env
    h = login(c)
    sub = store.create_area("Temp", parent_id=area_id(store, db, "Work"), db_path=db)
    nid = store.create_note("dump", body="x", db_path=db)
    pid = store.create_proposal(
        "split", nid, {"parts": [{"title": "a", "body": "x"},
                                 {"title": "b", "area_id": sub}]}, db_path=db)
    conn = store._connect(db)
    with conn:
        conn.execute("DELETE FROM areas WHERE id=?", (sub,))
    conn.close()
    r = c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert r.status_code == 409
    assert store.get_note(nid, db_path=db)["status"] == "inbox"          # untouched
    assert store.list_notes(db_path=db, limit=500) and \
        all(n["title"] != "a" for n in store.list_notes(db_path=db, limit=500))
    assert store.get_proposal(pid, db_path=db)["status"] == "pending"    # still decidable


def test_approve_routine_bulk(env):
    c, store, db = env
    h = login(c)
    work = area_id(store, db, "Work")
    n1 = store.create_note("r1", db_path=db)
    n2 = store.create_note("r2", db_path=db)
    n3 = store.create_note("attn", db_path=db)
    store.create_proposal("file", n1, {"area_id": work}, classification="routine", db_path=db)
    store.create_proposal("file", n2, {"area_id": work}, classification="routine", db_path=db)
    store.create_proposal("file", n3, {"area_id": work}, db_path=db)     # needs_attention
    r = c.post("/api/proposals/approve-routine", headers=h)
    assert r.status_code == 200
    assert len(r.json()["approved"]) == 2 and r.json()["failed"] == []
    assert r.json()["counts"] == {"pending": 1, "routine": 0, "needs_attention": 1}
    assert store.get_note(n1, db_path=db)["status"] == "active"
    assert store.get_note(n3, db_path=db)["status"] == "inbox"
    # tree carries the badge count for the sub-nav
    assert c.get("/api/notes/tree").json()["counts"]["proposals_pending"] == 1


# ---- the librarian CLI writes a proposal end-to-end ---------------------

def test_cli_propose_split_and_file(env, tmp_path):
    c, store, db = env
    from core import wm_cli
    nid = store.create_note("cli dump", body="one\n\ntwo", db_path=db)
    parts = tmp_path / "parts.json"
    parts.write_text(json.dumps([{"title": "One", "body": "one"},
                                 {"title": "Two", "body": "two"}]))
    assert wm_cli.main(["note", "propose-split", str(nid), "--parts", str(parts),
                        "--summary", "two items", "--routine"]) == 0
    p = store.list_proposals(status="pending", db_path=db)[0]
    assert p["kind"] == "split" and p["classification"] == "routine" and \
        len(p["payload"]["parts"]) == 2
    nid2 = store.create_note("cli single", db_path=db)
    assert wm_cli.main(["note", "propose-file", str(nid2), "--project", "alpha",
                        "--tags", "ops,alpha", "--summary", "project note"]) == 0
    p2 = store.list_proposals(status="pending", note_id=nid2, db_path=db)[0]
    assert p2["payload"]["project_id"] == store.get_project(slug="alpha", db_path=db)["id"]
    # bad project slug is a clean CLI error, not a traceback
    assert wm_cli.main(["note", "propose-file", str(nid2), "--project", "nope",
                        "--summary", "x"]) == 1


# ---- heartbeat ingest schedule ------------------------------------------

def test_heartbeat_skips_when_nothing_new(env):
    c, store, db = env
    sid = store.create_schedule("Librarian ingest", "0 * * * *", "alpha",
                                "Triage the Second Brain inbox",
                                assignee_profile="librarian",
                                heartbeat="librarian_ingest", db_path=db)
    conn = store._connect(db)
    with conn:
        conn.execute("UPDATE schedules SET next_fire_at=1.0 WHERE id=?", (sid,))
    conn.close()
    fired = store.fire_due(db_path=db)
    assert [(x[0], x[1], x[2]) for x in fired] == [(sid, "skipped", None)]
    # NO task minted => no run, no model call
    assert store.list_tasks(db_path=db) == []
    runs = store.list_schedule_runs(sid, db_path=db)
    assert runs[0]["kind"] == "skipped" and "heartbeat" in runs[0]["detail"]
    assert store.get_schedule(sid, db_path=db)["next_fire_at"] > 1.0     # advanced


def test_heartbeat_fires_on_untriaged_inbox(env):
    c, store, db = env
    sid = store.create_schedule("Librarian ingest", "0 * * * *", "alpha",
                                "Triage the Second Brain inbox",
                                assignee_profile="librarian",
                                heartbeat="librarian_ingest", db_path=db)
    nid = store.create_note("fresh capture", db_path=db)
    conn = store._connect(db)
    with conn:
        conn.execute("UPDATE schedules SET next_fire_at=1.0 WHERE id=?", (sid,))
    conn.close()
    fired = store.fire_due(db_path=db)
    assert fired[0][1] == "late" and fired[0][2] is not None
    t = store.get_task(fired[0][2], db_path=db)
    assert t["assignee_profile"] == "librarian"
    # a pending proposal covers the note -> next tick is quiet again
    store.create_proposal("file", nid,
                          {"area_id": next(a["id"] for a in store.list_areas(db_path=db))},
                          db_path=db)
    conn = store._connect(db)
    with conn:
        conn.execute("UPDATE schedules SET next_fire_at=1.0, last_task_id=NULL WHERE id=?", (sid,))
    conn.close()
    assert store.fire_due(db_path=db)[0][1] == "skipped"


def test_heartbeat_validated_on_create_and_update(env):
    c, store, db = env
    with pytest.raises(ValueError):
        store.create_schedule("bad", "0 * * * *", "alpha", "t",
                              heartbeat="typo_check", db_path=db)
    sid = store.create_schedule("ok", "0 * * * *", "alpha", "t", db_path=db)
    with pytest.raises(ValueError):
        store.update_schedule(sid, heartbeat="typo_check", db_path=db)
    assert store.update_schedule(sid, heartbeat="librarian_ingest",
                                 db_path=db)["heartbeat"] == "librarian_ingest"


def test_librarian_is_assignable(env):
    c, store, db = env
    assert store.validate_assignee("librarian") == "librarian"
    tid = store.create_task("alpha", "ingest", assignee_profile="librarian", db_path=db)
    store.mark_ready(tid, db_path=db)
    assert store.claim_task(tid, db_path=db) is True                     # dispatchable
