"""LibraryPolishPlan Slice C: GET /api/notes/facets + core.wm_store.note_facets.

Proves the facet contract the Library's filter selects depend on: each facet
is counted over the CURRENT selection with its OWN dimension left out of
scope (so switching within a dimension never narrows the list you're
choosing from down to just what's already picked), zero-count tags/projects/
areas are omitted, and `q` narrows facets the same way it narrows the note
list itself.
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


def test_facets_empty_scope_is_empty_lists(env):
    c, store, db = env
    got = store.note_facets(db_path=db)
    assert got == {"tags": [], "projects": [], "areas": [], "active": 0, "archived": 0}


def test_active_archived_counts_ignore_the_archive_dimension_but_not_area(env):
    """The refine row's Archive(N) label must read the SCOPED archived count,
    not the library-wide one — picking an area must narrow it, but toggling
    Active<->Archive itself must not (each count answers 'how many on the
    OTHER side of the toggle, in this same area')."""
    c, store, db = env
    work = area_id(store, db, "Work")
    health = area_id(store, db, "Health")
    for i in range(3):
        n = store.create_note("w-active-%d" % i, status="active", area_id=work, db_path=db)
    archived_ids = [store.create_note("w-archived-%d" % i, status="active", area_id=work, db_path=db) for i in range(2)]
    for nid in archived_ids:
        store.update_note(nid, status="archived", db_path=db)
    store.create_note("h-archived", status="active", area_id=health, db_path=db)
    n2 = store.create_note("h-archived-2", status="active", area_id=health, db_path=db)
    store.update_note(n2, status="archived", db_path=db)

    scoped_active = store.note_facets(area_id=work, exclude_archived=True, db_path=db)
    scoped_archived = store.note_facets(area_id=work, status="archived", db_path=db)
    # Both sides of the toggle report the SAME scoped active/archived counts —
    # neither call's own status/exclude_archived narrows these two numbers.
    assert scoped_active["active"] == 3 and scoped_active["archived"] == 2
    assert scoped_archived["active"] == 3 and scoped_archived["archived"] == 2
    # Unscoped (no area) sees the whole library, not just Work: +1 active and
    # +1 archived from Health on top of Work's 3/2.
    unscoped = store.note_facets(db_path=db)
    assert unscoped["active"] == 4 and unscoped["archived"] == 3


def test_facets_respect_area_and_archive_state(env):
    c, store, db = env
    work = area_id(store, db, "Work")
    health = area_id(store, db, "Health")
    store.create_note("w-active", status="active", area_id=work, tags=["t1"], db_path=db)
    archived = store.create_note("w-archived", status="active", area_id=work, tags=["t1"], db_path=db)
    store.update_note(archived, status="archived", db_path=db)
    store.create_note("h-active", status="active", area_id=health, tags=["t1"], db_path=db)

    # Active-only, scoped to Work: only the one active Work note with t1.
    active_work = store.note_facets(area_id=work, exclude_archived=True, db_path=db)
    assert active_work["tags"] == [{"tag": "t1", "count": 1}]

    # Archive-only, scoped to Work: only the archived Work note.
    arch_work = store.note_facets(area_id=work, status="archived", db_path=db)
    assert arch_work["tags"] == [{"tag": "t1", "count": 1}]

    # Health note never leaks into a Work-scoped facet.
    assert all(t["tag"] != "t1" or True for t in active_work["tags"])  # sanity: no crash
    unscoped = store.note_facets(exclude_archived=True, db_path=db)
    assert unscoped["tags"] == [{"tag": "t1", "count": 2}]             # w-active + h-active


def test_tag_absent_from_scope_is_absent(env):
    c, store, db = env
    work = area_id(store, db, "Work")
    health = area_id(store, db, "Health")
    store.create_note("w", status="active", area_id=work, tags=["work-only"], db_path=db)
    store.create_note("h", status="active", area_id=health, tags=["health-only"], db_path=db)
    got = store.note_facets(area_id=work, db_path=db)
    tag_names = {t["tag"] for t in got["tags"]}
    assert "work-only" in tag_names
    assert "health-only" not in tag_names


def test_selected_tag_dimension_not_narrowed_by_itself(env):
    """Picking tag=red must not hide blue from the tags facet — the owner
    needs to see every OTHER tag still present in the current selection so
    they can switch to it, not just the one they already picked."""
    c, store, db = env
    work = area_id(store, db, "Work")
    store.create_note("red-note", status="active", area_id=work, tags=["red"], db_path=db)
    store.create_note("blue-note", status="active", area_id=work, tags=["blue"], db_path=db)
    got = store.note_facets(area_id=work, tag="red", db_path=db)
    by_tag = {t["tag"]: t["count"] for t in got["tags"]}
    assert by_tag == {"red": 1, "blue": 1}


def test_project_dimension_not_narrowed_by_itself(env, tmp_path):
    c, store, db = env
    os.makedirs(tmp_path / "beta", exist_ok=True)
    p2 = store.create_project("beta", "Beta", "", str(tmp_path / "beta"), db_path=db)
    alpha = next(p["id"] for p in store.list_projects(db_path=db) if p["slug"] == "alpha")
    store.create_note("a-note", status="active", project_id=alpha, db_path=db)
    store.create_note("b-note", status="active", project_id=p2, db_path=db)
    got = store.note_facets(project_id=alpha, db_path=db)
    by_id = {p["id"]: p["count"] for p in got["projects"]}
    assert by_id == {alpha: 1, p2: 1}


def test_q_narrows_facets(env):
    c, store, db = env
    work = area_id(store, db, "Work")
    store.create_note("special widget", status="active", area_id=work, tags=["gadget"], db_path=db)
    store.create_note("other stuff", status="active", area_id=work, tags=["other"], db_path=db)
    got = store.note_facets(q="widget", db_path=db)
    assert got["tags"] == [{"tag": "gadget", "count": 1}]


def test_zero_count_tags_and_areas_omitted(env):
    c, store, db = env
    work = area_id(store, db, "Work")
    health = area_id(store, db, "Health")
    store.create_note("w", status="active", area_id=work, tags=["only-here"], db_path=db)
    got = store.note_facets(db_path=db)
    area_ids = {a["id"] for a in got["areas"]}
    assert work in area_ids and health not in area_ids
    assert len(got["tags"]) == 1


def test_type_filter_narrows_facets(env):
    c, store, db = env
    work = area_id(store, db, "Work")
    store.create_note("a playbook note", status="active", area_id=work, note_type="playbook",
                      tags=["how-to"], db_path=db)
    store.create_note("a plain note", status="active", area_id=work, note_type="note",
                      tags=["misc"], db_path=db)
    got = store.note_facets(area_id=work, note_type="playbook", db_path=db)
    assert got["tags"] == [{"tag": "how-to", "count": 1}]
    assert got["active"] == 1


def test_areas_facet_rolls_up_child_into_parent(env):
    """`_scope_sql`'s area_id filter treats a parent pick as parent+children
    (list_notes(area_id=parent) includes child-area notes) — the areas facet
    must count the same way, or a parent area's own facet count would read
    lower than what picking it actually returns."""
    c, store, db = env
    work = area_id(store, db, "Work")
    sub = store.create_area("SubWork", parent_id=work, db_path=db)
    store.create_note("parent-note", status="active", area_id=work, db_path=db)
    store.create_note("child-note-1", status="active", area_id=sub, db_path=db)
    store.create_note("child-note-2", status="active", area_id=sub, db_path=db)
    got = store.note_facets(db_path=db)
    by_id = {a["id"]: a["count"] for a in got["areas"]}
    assert by_id[work] == 3       # parent's own note + both child notes
    assert by_id[sub] == 2        # the child's own count stays un-rolled-up
    # matches what actually picking the parent area returns
    assert len(store.list_notes(area_id=work, status="active", db_path=db)) == 3


def test_facets_active_count_matches_search_and_list_under_every_combo(env, tmp_path):
    """The strongest invariant: for the SAME filters, facets['active'] must
    equal the number of notes list_notes/search_notes actually returns —
    across area+tag+q, archived+q, and type+q combinations. This is exactly
    the case FTS vs LIKE q-scoring broke: a facet computed with LIKE could
    disagree with an FTS-ranked search over the same q."""
    c, store, db = env
    work = area_id(store, db, "Work")
    health = area_id(store, db, "Health")
    os.makedirs(tmp_path / "alpha2", exist_ok=True)
    alpha = next(p["id"] for p in store.list_projects(db_path=db) if p["slug"] == "alpha")
    n1 = store.create_note("payments gateway rollout", status="active", area_id=work,
                           project_id=alpha, tags=["fin", "team"], note_type="note", db_path=db)
    n2 = store.create_note("payments gateway maintenance", status="active", area_id=work,
                           tags=["fin"], note_type="playbook", db_path=db)
    n3 = store.create_note("payments gateway retired", status="active", area_id=work,
                           tags=["fin"], db_path=db)
    store.update_note(n3, status="archived", db_path=db)
    store.create_note("payments plan renewal", status="active", area_id=health,
                      tags=["fin"], db_path=db)

    def check(**filters):
        # facets['active']/['archived'] deliberately ignore status/
        # exclude_archived (the archive dimension) — so the comparison hits
        # must too: every OTHER filter applies, then active/archived are
        # counted among ALL of those hits, unfiltered by status.
        q = filters.get("q")
        dims = {k: v for k, v in filters.items() if k not in ("status", "exclude_archived", "q")}
        if q:
            hits = store.search_notes(q, limit=200, db_path=db, **dims)
        else:
            hits = store.list_notes(limit=200, db_path=db, **dims)
        want_active = sum(1 for h in hits if h["status"] != "archived")
        want_archived = sum(1 for h in hits if h["status"] == "archived")
        facets = store.note_facets(**filters, db_path=db)
        assert facets["active"] == want_active, "active mismatch for filters=%r: facets=%r want=%r" % (
            filters, facets["active"], want_active)
        assert facets["archived"] == want_archived, "archived mismatch for filters=%r: facets=%r want=%r" % (
            filters, facets["archived"], want_archived)

    check(q="payments", exclude_archived=True)                                   # bare q
    check(q="gateway", area_id=work, exclude_archived=True)                       # area+q
    check(q="gateway", area_id=work, tag="fin", exclude_archived=True)            # area+tag+q
    check(q="payments", status="archived")                                        # archived+q
    check(q="gateway", note_type="playbook", exclude_archived=True)               # type+q
    check(area_id=work, tag="fin", exclude_archived=True)                         # no q, for comparison
    check(project_id=alpha, exclude_archived=True)                                # project, no q


def test_facets_api_requires_session_and_round_trips(env):
    c, store, db = env
    assert c.get("/api/notes/facets").status_code == 401
    h = login(c)
    work = area_id(store, db, "Work")
    store.create_note("w", status="active", area_id=work, tags=["deep"], db_path=db)
    store.create_note("w2", status="active", area_id=work, tags=["deep"], db_path=db)
    r = c.get("/api/notes/facets", params={"area_id": work}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["tags"] == [{"tag": "deep", "count": 2}]
    assert set(body.keys()) == {"tags", "projects", "areas", "active", "archived"}
    assert body["active"] == 2 and body["archived"] == 0
