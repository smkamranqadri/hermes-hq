"""LibraryPolishPlan Slice B: a note may only END UP `active` with an area OR
a project. Enforced at the store level so every path inherits it — minting
(create_note), owner edit/refile/restore (update_note), and proposal `file`
creation/approval (_validate_proposal_payload, with update_note as the
backstop). Capture (inbox) and archiving are never blocked; only the
transition INTO active requires a home. Non-filing edits (title, body, tags,
pinned, disputed) on a note the store never touches the home of must keep
working even when that note is already a homeless orphan (the lint's own
surface — see core/wm_store.py ~6001).
"""
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


def project_id(store, db, slug):
    return store.get_project(slug=slug, db_path=db)["id"]


def make_orphan_active_note(store, db, title="orphan"):
    """A homeless active note: since create_note now refuses to mint one, the
    only way this state exists is a note that predates the rule (or a bypass
    write) — exactly what the `orphan` lint check exists to surface. Built
    directly via SQL, same as the lint's own fixtures."""
    nid = store.create_note(title, db_path=db)
    conn = store._connect(db)
    with conn:
        conn.execute("UPDATE notes SET status='active' WHERE id=?", (nid,))
    conn.close()
    return nid


ERR = "A note needs an area or a project before it can be filed as active."


# ---- create_note (mint) ----------------------------------------------------

def test_create_note_active_without_home_refused(env):
    c, store, db = env
    with pytest.raises(ValueError, match="area or a project"):
        store.create_note("bare", status="active", db_path=db)


def test_create_note_active_with_home_allowed(env):
    c, store, db = env
    n1 = store.create_note("a", status="active", area_id=area_id(store, db, "Work"), db_path=db)
    assert store.get_note(n1, db_path=db)["status"] == "active"
    n2 = store.create_note("b", status="active", project_id=project_id(store, db, "alpha"), db_path=db)
    assert store.get_note(n2, db_path=db)["status"] == "active"


def test_create_note_active_without_home_refused_via_api(env):
    c, store, db = env
    h = login(c)
    r = c.post("/api/notes", json={"title": "bare", "status": "active"}, headers=h)
    assert r.status_code == 409
    assert ERR in r.json()["detail"]


# ---- update_note (edit/refile/restore) -------------------------------------

def test_update_note_active_without_home_refused(env):
    c, store, db = env
    nid = store.create_note("bare", db_path=db)
    with pytest.raises(ValueError, match="area or a project"):
        store.update_note(nid, status="active", db_path=db)
    n = store.get_note(nid, db_path=db)
    assert n["status"] == "inbox"                # untouched


def test_update_note_active_without_home_refused_via_api(env):
    c, store, db = env
    h = login(c)
    nid = store.create_note("bare", db_path=db)
    r = c.post("/api/note/%d/edit" % nid, json={"status": "active"}, headers=h)
    assert r.status_code == 409
    assert ERR in r.json()["detail"]
    assert store.get_note(nid, db_path=db)["status"] == "inbox"


def test_filing_with_area_or_project_allowed(env):
    c, store, db = env
    n1 = store.create_note("a", db_path=db)
    store.update_note(n1, status="active", area_id=area_id(store, db, "Work"), db_path=db)
    assert store.get_note(n1, db_path=db)["status"] == "active"
    n2 = store.create_note("b", db_path=db)
    store.update_note(n2, status="active", project_id=project_id(store, db, "alpha"), db_path=db)
    assert store.get_note(n2, db_path=db)["status"] == "active"


def test_clearing_both_home_fields_on_a_note_that_has_both_is_refused(env):
    c, store, db = env
    nid = store.create_note("filed", status="active",
                            area_id=area_id(store, db, "Work"),
                            project_id=project_id(store, db, "alpha"), db_path=db)
    with pytest.raises(ValueError, match="area or a project"):
        store.update_note(nid, area_id=None, project_id=None, db_path=db)
    n = store.get_note(nid, db_path=db)
    assert n["status"] == "active" and n["area_id"] is not None and n["project_id"] is not None


def test_api_clear_area_and_clear_project_shape_refused(env):
    """The exact shape FileNoteModal sends when the owner empties both selects."""
    c, store, db = env
    h = login(c)
    nid = store.create_note("filed", status="active",
                            area_id=area_id(store, db, "Work"),
                            project_id=project_id(store, db, "alpha"), db_path=db)
    r = c.post("/api/note/%d/edit" % nid,
               json={"clear_area": True, "clear_project": True}, headers=h)
    assert r.status_code == 409
    assert ERR in r.json()["detail"]
    n = store.get_note(nid, db_path=db)
    assert n["area_id"] is not None and n["project_id"] is not None      # untouched


def test_clearing_area_but_project_present_is_allowed(env):
    c, store, db = env
    nid = store.create_note("both", status="active",
                            area_id=area_id(store, db, "Work"),
                            project_id=project_id(store, db, "alpha"), db_path=db)
    n = store.update_note(nid, area_id=None, db_path=db)
    assert n["status"] == "active" and n["area_id"] is None and n["project_id"] is not None


def test_inbox_and_archived_transitions_untouched(env):
    c, store, db = env
    nid = store.create_note("bare", db_path=db)
    # inbox -> archived with no home: never blocked
    n = store.update_note(nid, status="archived", db_path=db)
    assert n["status"] == "archived"
    # archived -> inbox with no home: never blocked
    n = store.update_note(nid, status="inbox", db_path=db)
    assert n["status"] == "inbox"
    # editing other fields on a homeless inbox note (no status change): fine
    n = store.update_note(nid, body="edited", db_path=db)
    assert n["status"] == "inbox" and n["body"] == "edited"


def test_non_filing_edits_on_a_homeless_active_note_still_work(env):
    """The guard must fire ONLY when the edit touches status/area/project — a
    lint orphan's title/body/tags/pinned/disputed controls must not brick."""
    c, store, db = env
    nid = make_orphan_active_note(store, db)
    n = store.update_note(nid, title="renamed", db_path=db)
    assert n["status"] == "active" and n["title"] == "renamed"
    n = store.update_note(nid, body="new body", db_path=db)
    assert n["status"] == "active" and n["body"] == "new body"
    n = store.update_note(nid, pinned=True, db_path=db)
    assert n["status"] == "active" and n["pinned"] == 1
    n = store.update_note(nid, tags=["deep"], db_path=db)
    assert n["status"] == "active" and n["tags"] == ["deep"]


def test_non_filing_edits_on_a_homeless_active_note_still_work_via_api(env):
    c, store, db = env
    h = login(c)
    nid = make_orphan_active_note(store, db)
    r = c.post("/api/note/%d/edit" % nid, json={"pinned": True}, headers=h)
    assert r.status_code == 200 and r.json()["note"]["pinned"] == 1
    r = c.post("/api/note/%d/edit" % nid, json={"body": "hello"}, headers=h)
    assert r.status_code == 200 and r.json()["note"]["body"] == "hello"


# ---- proposal `file`: refused at CREATION, not just at approval -----------

def test_propose_file_without_home_refused_at_creation(env):
    c, store, db = env
    nid = store.create_note("dump", body="x", db_path=db)
    # structurally valid (has a `type`) but leaves the note homeless once filed
    with pytest.raises(ValueError, match="area or a project"):
        store.create_proposal("file", nid, {"type": "playbook"}, db_path=db)
    with pytest.raises(ValueError, match="area or a project"):
        store.create_proposal("file", nid, {"tags": ["x"], "new_tags": ["x"]}, db_path=db)
    assert store.list_proposals(db_path=db) == []           # nothing was created


def test_cli_propose_file_without_home_refused(env, capsys):
    c, store, db = env
    from core import wm_cli
    nid = store.create_note("cli dump", db_path=db)
    assert wm_cli.main(["note", "propose-file", str(nid), "--tags", "x",
                        "--new-tags", "x", "--summary", "no home"]) == 1
    assert "area or a project" in capsys.readouterr().out
    assert store.list_proposals(db_path=db) == []


def test_file_proposal_with_new_area_allowed(env):
    c, store, db = env
    h = login(c)
    nid = store.create_note("dump", body="x", db_path=db)
    pid = store.create_proposal("file", nid, {"new_area": "Freshly Coined"}, db_path=db)
    r = c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert r.status_code == 200
    n = store.get_note(nid, db_path=db)
    assert n["status"] == "active" and n["area"]["name"] == "Freshly Coined"


def test_file_proposal_archive_needs_no_home(env):
    c, store, db = env
    h = login(c)
    nid = store.create_note("junk", body="x", db_path=db)
    pid = store.create_proposal("file", nid, {"archive": True}, db_path=db)
    r = c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert r.status_code == 200
    assert store.get_note(nid, db_path=db)["status"] == "archived"


def test_edited_homeless_approval_leaves_taxonomy_untouched(env):
    """An owner edit-before-approve that coins a tag but leaves the note
    homeless must 409 AND leave nothing registered — the tag registration and
    the home check now share one transaction (_validate_proposal_payload)."""
    c, store, db = env
    h = login(c)
    nid = store.create_note("dump", body="x", db_path=db)
    pid = store.create_proposal("file", nid, {"area_id": area_id(store, db, "Work")}, db_path=db)
    before = store.taxonomy_tags(db_path=db)
    assert "brand-new-coin" not in before
    r = c.post("/api/proposal/%d/approve" % pid,
               json={"payload": {"tags": ["brand-new-coin"],
                                 "new_tags": ["brand-new-coin"]}}, headers=h)
    assert r.status_code == 409
    assert ERR in r.json()["detail"]
    assert "brand-new-coin" not in store.taxonomy_tags(db_path=db)   # NOT leaked
    assert store.get_note(nid, db_path=db)["status"] == "inbox"      # untouched
    assert store.get_proposal(pid, db_path=db)["status"] == "pending"


# ---- split parts (already correct — pin the behaviour) --------------------

def test_split_unfiled_parts_land_in_inbox_filed_parts_active(env):
    c, store, db = env
    h = login(c)
    nid = store.create_note("dump", body="x", db_path=db)
    parts = [{"title": "no home", "body": "x"},
            {"title": "has home", "body": "y", "area_id": area_id(store, db, "Work")}]
    pid = store.create_proposal("split", nid, {"parts": parts}, db_path=db)
    r = c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert r.status_code == 200
    ids = r.json()["proposal"]["result"]["note_ids"]
    made = [store.get_note(i, db_path=db) for i in ids]
    assert [m["status"] for m in made] == ["inbox", "active"]


# ---- import plan (pin only — do not change) --------------------------------

def test_import_plan_never_actives_a_note_without_an_area():
    from backend.notes_import import Unit, plan_unit, LIBRARIAN
    u = Unit("focus", "id-1", "No domain thing", "do it",
            meta={"status": "open"})
    u.decision = LIBRARIAN
    p = plan_unit(u, areas_by_name={})
    assert p.status == "inbox"
    assert p.area_id is None
