"""Second Brain — Library polish Slice D: the note page's Related card.

`core.wm_store.related_notes` / `GET /api/note/{id}/related` returns three
labelled groups (linked / shared_tags / same_area), each capped with a
`more` count, with an exclusion chain so nothing shows twice: linked notes
never reappear in shared_tags, and neither reappears in same_area.
"""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)

from tests.backend.test_notes import env, login  # noqa: F401


def _note(store, db, title, **kw):
    return store.create_note(title, body=kw.pop("body", ""), db_path=db, **kw)


def _work_area(store, db):
    return next(a for a in store.list_areas(db_path=db) if a["name"] == "Work")


# ---- 404 ----------------------------------------------------------------

def test_unknown_note_404(env):
    c, store, db = env
    login(c)
    assert c.get("/api/note/999999/related").status_code == 404


# ---- empty note -----------------------------------------------------------

def test_note_with_no_relations_has_empty_groups(env):
    c, store, db = env
    login(c)
    nid = _note(store, db, "lonely note")
    r = c.get("/api/note/%d/related" % nid).json()
    assert r == {
        "linked": {"items": [], "more": 0},
        "shared_tags": {"items": [], "more": 0},
        "same_area": {"items": [], "more": 0},
    }


def test_no_area_means_empty_same_area_group(env):
    c, store, db = env
    login(c)
    nid = _note(store, db, "homeless", tags=["x"])
    other = _note(store, db, "other homeless", tags=["x"])
    r = c.get("/api/note/%d/related" % nid).json()
    assert r["same_area"] == {"items": [], "more": 0}
    # sanity: the tag match still surfaces in shared_tags
    assert [it["id"] for it in r["shared_tags"]["items"]] == [other]


# ---- linked: both directions ---------------------------------------------

def test_linked_includes_forward_and_reverse_notes(env):
    c, store, db = env
    login(c)
    a = _note(store, db, "A")
    b = _note(store, db, "B")
    store.link_note(a, "note", b, db_path=db)   # forward only: A -> B
    ra = c.get("/api/note/%d/related" % a).json()
    rb = c.get("/api/note/%d/related" % b).json()
    assert [it["id"] for it in ra["linked"]["items"] if it["kind"] == "note"] == [b]
    # B never called link_note itself — this MUST be a reverse hit, not a miss.
    assert [it["id"] for it in rb["linked"]["items"] if it["kind"] == "note"] == [a]


def test_linked_dedupes_a_bidirectional_pair(env):
    c, store, db = env
    login(c)
    a = _note(store, db, "A")
    b = _note(store, db, "B")
    # A contradiction approval links BOTH directions (two distinct rows:
    # (a,note,b) and (b,note,a)) — A's own page must show B once, not twice.
    store.link_note(a, "note", b, db_path=db)
    store.link_note(b, "note", a, db_path=db)
    r = c.get("/api/note/%d/related" % a).json()
    note_items = [it for it in r["linked"]["items"] if it["kind"] == "note"]
    assert [it["id"] for it in note_items] == [b]


def test_linked_null_created_at_does_not_500(env):
    c, store, db = env
    login(c)
    a = _note(store, db, "A")
    b = _note(store, db, "B")
    conn = store._connect(db)
    with conn:
        conn.execute("INSERT INTO note_links(note_id, kind, target_id, created_at) "
                     "VALUES(?,?,?,NULL)", (a, "note", b))
    conn.close()
    r = c.get("/api/note/%d/related" % a)
    assert r.status_code == 200
    note_items = [it for it in r.json()["linked"]["items"] if it["kind"] == "note"]
    assert [it["id"] for it in note_items] == [b]


def test_linked_cap_and_more(env):
    c, store, db = env
    login(c)
    base = _note(store, db, "base")
    targets = [_note(store, db, "linked %d" % i) for i in range(8)]
    for t in targets:
        store.link_note(base, "note", t, db_path=db)
    r = c.get("/api/note/%d/related" % base).json()
    assert len(r["linked"]["items"]) == 6
    assert r["linked"]["more"] == 2


def test_linked_carries_task_and_schedule_shape(env):
    c, store, db = env
    h = login(c)
    p = store.get_project(slug="alpha", db_path=db)
    nid = c.post("/api/notes", json={"title": "with links", "project_id": p["id"]}, headers=h).json()["id"]
    tid = c.post("/api/note/%d/new-task" % nid, json={}, headers=h).json()["id"]
    r = c.get("/api/note/%d/related" % nid).json()
    items = r["linked"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "task"
    assert item["target_id"] == tid
    assert item["target"]["title"] == "with links"


# ---- shared tags: ordering + exclusion ------------------------------------

def test_shared_tags_ordered_by_count_then_recency(env):
    c, store, db = env
    login(c)
    base = _note(store, db, "base", tags=["a", "b", "c"])
    two_tags = _note(store, db, "two", tags=["a", "b"])
    one_tag = _note(store, db, "one", tags=["c"])
    no_tags = _note(store, db, "none")
    r = c.get("/api/note/%d/related" % base).json()
    ids = [it["id"] for it in r["shared_tags"]["items"]]
    assert ids == [two_tags, one_tag]
    assert no_tags not in ids
    top = r["shared_tags"]["items"][0]
    assert sorted(top["shared_tags"]) == ["a", "b"]


def test_shared_tags_excludes_self_and_linked(env):
    c, store, db = env
    login(c)
    base = _note(store, db, "base", tags=["shared"])
    linked = _note(store, db, "linked", tags=["shared"])
    store.link_note(base, "note", linked, db_path=db)
    r = c.get("/api/note/%d/related" % base).json()
    linked_ids = {it["id"] for it in r["linked"]["items"]}
    shared_ids = {it["id"] for it in r["shared_tags"]["items"]}
    assert linked == list(linked_ids)[0]
    assert linked not in shared_ids            # already in Linked
    assert base not in shared_ids              # self never included
    assert base not in linked_ids


def test_shared_tags_excludes_archived(env):
    c, store, db = env
    login(c)
    base = _note(store, db, "base", tags=["shared"])
    archived = _note(store, db, "archived", tags=["shared"], status="active",
                     area_id=_work_area(store, db)["id"])
    store.update_note(archived, edited_by="owner", status="archived", db_path=db)
    r = c.get("/api/note/%d/related" % base).json()
    assert archived not in {it["id"] for it in r["shared_tags"]["items"]}


# ---- same area: ordering + exclusion chain --------------------------------

def test_same_area_excludes_self_linked_and_shared(env):
    c, store, db = env
    login(c)
    work = _work_area(store, db)["id"]
    base = _note(store, db, "base", tags=["shared"], area_id=work, status="active")
    linked = _note(store, db, "linked", area_id=work, status="active")
    store.link_note(base, "note", linked, db_path=db)
    shared = _note(store, db, "shared-tag", tags=["shared"], area_id=work, status="active")
    plain = _note(store, db, "plain same-area", area_id=work, status="active")
    r = c.get("/api/note/%d/related" % base).json()
    same_area_ids = {it["id"] for it in r["same_area"]["items"]}
    assert same_area_ids == {plain}
    assert linked not in same_area_ids
    assert shared not in same_area_ids
    assert base not in same_area_ids


def test_same_area_excludes_all_shared_tag_matches_beyond_the_cap(env):
    c, store, db = env
    login(c)
    work = _work_area(store, db)["id"]
    base = _note(store, db, "base", tags=["shared"], area_id=work, status="active")
    # 9 notes qualify for BOTH shared_tags (same tag) and same_area (same
    # area). shared_tags shows only 6 of them, but ALL 9 must still be
    # excluded from same_area — not just the 6 that made the cut.
    overlap = [_note(store, db, "overlap %d" % i, tags=["shared"], area_id=work,
                     status="active") for i in range(9)]
    r = c.get("/api/note/%d/related" % base).json()
    assert len(r["shared_tags"]["items"]) == 6
    assert r["shared_tags"]["more"] == 3
    same_area_ids = {it["id"] for it in r["same_area"]["items"]}
    assert same_area_ids.isdisjoint(overlap)
    assert same_area_ids == set()


def test_same_area_excludes_archived(env):
    c, store, db = env
    login(c)
    work = _work_area(store, db)["id"]
    base = _note(store, db, "base", area_id=work, status="active")
    archived = _note(store, db, "archived-same-area", area_id=work, status="active")
    store.update_note(archived, edited_by="owner", status="archived", db_path=db)
    r = c.get("/api/note/%d/related" % base).json()
    assert archived not in {it["id"] for it in r["same_area"]["items"]}


# ---- caps + more -----------------------------------------------------------

def test_same_area_cap_and_more(env):
    c, store, db = env
    login(c)
    work = _work_area(store, db)["id"]
    base = _note(store, db, "base", area_id=work, status="active")
    for i in range(9):
        _note(store, db, "sibling %d" % i, area_id=work, status="active")
    r = c.get("/api/note/%d/related" % base).json()
    assert len(r["same_area"]["items"]) == 6
    assert r["same_area"]["more"] == 3


def test_shared_tags_cap_and_more(env):
    c, store, db = env
    login(c)
    base = _note(store, db, "base", tags=["popular"])
    for i in range(9):
        _note(store, db, "tagged %d" % i, tags=["popular"])
    r = c.get("/api/note/%d/related" % base).json()
    assert len(r["shared_tags"]["items"]) == 6
    assert r["shared_tags"]["more"] == 3
