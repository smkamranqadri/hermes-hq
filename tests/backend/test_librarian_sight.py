"""Second Brain Phase 3 wave 0: the librarian can SEE the library and can
propose a Thought onto an existing note.

Before this, the librarian's only action for related content was a NEW note:
its CLI had no search command (so it could not discover a sibling's id) and
PROPOSAL_KINDS offered no way to append an entry. Importing 969 units through
that shreds the history into near-duplicates instead of letting notes
accumulate Thoughts.

These tests pin the three pieces: search is real and read-only, append_entry
round-trips through owner approval without ever letting an agent write a note,
and the inbox worklist puts the owner's own captures ahead of an import wave.
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


# ---- search: the librarian can find what already exists -----------------

def test_search_finds_filed_notes_the_inbox_never_shows(env):
    """The gap wave 0 closes: `wm note inbox` lists ONLY untriaged captures, so
    a filed note was undiscoverable — the librarian had no id to point at."""
    c, store, db = env
    filed = store.create_note("Lisbon visa sponsorship", body="Pinecrest needs a work visa",
                              status="active", area_id=area_id(store, db, "Career"),
                              db_path=db)
    store.create_note("unrelated capture", body="buy milk", db_path=db)
    # the old worklist cannot see it at all
    assert filed not in [n["id"] for n in store.list_notes(status="inbox", db_path=db)]
    # search can
    hits = store.search_notes("sponsorship", db_path=db)
    assert [n["id"] for n in hits] == [filed]
    # hits carry entry_count — the librarian judges whether a note is already
    # an accumulating thread before proposing to append to it
    assert hits[0]["entry_count"] == 0
    # ...and search covers Thoughts too, not just the body (FTS syncs on append)
    store.add_note_entry(filed, "Larkfield confirmed they sponsor", db_path=db)
    again = store.search_notes("Larkfield", db_path=db)
    assert filed in [n["id"] for n in again]
    assert next(n for n in again if n["id"] == filed)["entry_count"] == 1


def test_search_is_read_only(env):
    c, store, db = env
    nid = store.create_note("Standup", body="9:30 daily", db_path=db)
    before = store.get_note(nid, db_path=db)
    store.search_notes("standup", db_path=db)
    store.search_notes('bogus" OR 1=1 --', db_path=db)     # FTS syntax cannot escape
    assert store.get_note(nid, db_path=db) == before


# ---- append_entry: propose ----------------------------------------------

def test_append_entry_is_never_routine(env):
    """Entries have no delete path anywhere, so this kind can never be
    bulk-approved — enforced in the store, not only in the CLI."""
    c, store, db = env
    src = store.create_note("follow-up", body="also they pay relocation", db_path=db)
    tgt = store.create_note("Lisbon search", body="notes", status="active", db_path=db)
    assert "append_entry" in store.OWNER_EYES_KINDS
    with pytest.raises(ValueError, match="needs_attention"):
        store.create_proposal("append_entry", src, {"target_note_id": tgt},
                              classification="routine", db_path=db)


def test_append_entry_payload_validation(env):
    c, store, db = env
    src = store.create_note("s", body="b", db_path=db)
    tgt = store.create_note("t", body="b", status="active", db_path=db)
    bad = [
        ({}, "target_note_id"),                                  # missing target
        ({"target_note_id": "7"}, "target_note_id"),             # not an int
        ({"target_note_id": 99999}, "no such note"),             # unknown note
        ({"target_note_id": src}, "cannot be appended to itself"),
        ({"target_note_id": tgt, "body": "  "}, "non-empty"),    # blank explicit body
        ({"target_note_id": tgt, "nope": 1}, "unknown fields"),
        ({"target_note_id": tgt, "archive_source": "yes"}, "true/false"),
    ]
    for payload, msg in bad:
        with pytest.raises(ValueError, match=msg):
            store.create_proposal("append_entry", src, payload, db_path=db)


def test_append_entry_refuses_an_archived_target(env):
    """Appending a live Thought to museum material buries it where the owner
    will not look. File the capture instead."""
    c, store, db = env
    src = store.create_note("s", body="b", db_path=db)
    tgt = store.create_note("t", body="b", status="archived", db_path=db)
    with pytest.raises(ValueError, match="archived"):
        store.create_proposal("append_entry", src, {"target_note_id": tgt}, db_path=db)


# ---- append_entry: approve ----------------------------------------------

def test_approval_appends_verbatim_and_creates_no_new_note(env):
    """The whole point of wave 0: one note gains a Thought, and the note count
    does NOT grow."""
    c, store, db = env
    tgt = store.create_note("Lisbon search", body="tracking the lane",
                            status="active", db_path=db)
    src = store.create_note("Pinecrest sponsors", body="Pinecrest confirmed sponsorship",
                            db_path=db)
    n_before = len(store.list_notes(limit=500, db_path=db))
    pid = store.create_proposal("append_entry", src, {"target_note_id": tgt},
                                summary="belongs on the Lisbon note", db_path=db)
    h = login(c)
    r = c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert r.status_code == 200, r.text

    after = store.get_note(tgt, db_path=db)
    # appended VERBATIM — entries carry no author, so the owner's words must
    # never be silently replaced by a librarian paraphrase
    assert [e["body"] for e in after["entries"]] == ["Pinecrest confirmed sponsorship"]
    # no note was created, and the source left the inbox rather than lingering
    assert len(store.list_notes(limit=500, db_path=db)) == n_before
    assert store.get_note(src, db_path=db)["status"] == "archived"
    # cross-linked both ways round-trips to the target
    assert tgt in [l["target_id"] for l in store.get_note(src, db_path=db)["links"]
                   if l["kind"] == "note"]
    res = store.get_proposal(pid, db_path=db)["result"]
    assert res["target_note_id"] == tgt and res["archived_source"] is True


def test_approval_honours_an_explicit_body_and_keep_source(env):
    c, store, db = env
    tgt = store.create_note("Standup", body="9:30", status="active", db_path=db)
    src = store.create_note("dump", body="line one\nstandup moved to 10:00\nline three",
                            db_path=db)
    pid = store.create_proposal("append_entry", src,
                                {"target_note_id": tgt,
                                 "body": "standup moved to 10:00",
                                 "archive_source": False},
                                summary="extract the standup line", db_path=db)
    h = login(c)
    assert c.post("/api/proposal/%d/approve" % pid, headers=h).status_code == 200
    assert [e["body"] for e in store.get_note(tgt, db_path=db)["entries"]] == \
        ["standup moved to 10:00"]
    assert store.get_note(src, db_path=db)["status"] == "inbox"   # source kept


def test_approval_fails_cleanly_when_the_target_vanished(env):
    """Re-validation happens against CURRENT state: a target archived after the
    proposal was written must not append anything."""
    c, store, db = env
    tgt = store.create_note("t", body="b", status="active", db_path=db)
    src = store.create_note("s", body="follow-up", db_path=db)
    pid = store.create_proposal("append_entry", src, {"target_note_id": tgt}, db_path=db)
    store.update_note(tgt, edited_by="owner", status="archived", db_path=db)
    h = login(c)
    r = c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert r.status_code == 409          # store ValueError -> 409, as 2a set
    assert "archived" in r.json()["detail"]
    assert store.get_note(tgt, db_path=db)["entries"] == []
    assert store.get_note(src, db_path=db)["status"] == "inbox"    # untouched
    assert store.get_proposal(pid, db_path=db)["status"] == "pending"


def test_empty_capture_cannot_be_appended_verbatim(env):
    """A voice-only capture has no text; appending it verbatim would add an
    empty Thought that can never be deleted."""
    c, store, db = env
    tgt = store.create_note("t", body="b", status="active", db_path=db)
    src = store.create_note("voice memo", body="", db_path=db)
    pid = store.create_proposal("append_entry", src, {"target_note_id": tgt}, db_path=db)
    h = login(c)
    r = c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert r.status_code == 409
    assert "nothing to append" in r.json()["detail"]
    assert store.get_note(tgt, db_path=db)["entries"] == []


# ---- worklist ordering: an import wave must not bury a real capture -----

def test_inbox_worklist_puts_owner_captures_ahead_of_imports(env):
    """Phase 3: a wave puts hundreds of authored_by='import' notes in the
    inbox. The librarian's worklist is this list, so without owner-first the
    owner's own capture waits behind the wave."""
    c, store, db = env
    for i in range(5):
        store.create_note("imported %d" % i, body="old", authored_by="import", db_path=db)
    mine = store.create_note("my capture", body="new", db_path=db)

    # default ordering is newest-first and is unchanged for the Library UI
    plain = store.list_notes(status="inbox", db_path=db)
    assert len(plain) == 6
    # worklist ordering floats the owner's capture to the top
    first = store.list_notes(status="inbox", owner_first=True, db_path=db)[0]
    assert first["id"] == mine

    # and it is ORDERING only — nothing is hidden from either view
    assert {n["id"] for n in plain} == {n["id"] for n in
                                        store.list_notes(status="inbox", owner_first=True,
                                                         db_path=db)}


def test_owner_first_survives_an_older_owner_capture(env):
    """Ordering must key on authorship, not recency — an owner note captured
    BEFORE the wave still comes first."""
    c, store, db = env
    mine = store.create_note("older capture", body="mine", db_path=db)
    for i in range(3):
        store.create_note("imported %d" % i, body="x", authored_by="import", db_path=db)
    rows = store.list_notes(status="inbox", owner_first=True, db_path=db)
    assert rows[0]["id"] == mine


# ---- area coinage: the owner is the taxonomy authority for AREAS too ----

def test_librarian_can_propose_an_area_that_does_not_exist(env):
    """Note #43 in the real library: the owner rejected a filing with "This is
    related to DIY area, shopping tag". The librarian could coin the TAG but
    had no way to coin the AREA, so it filed with no area and explained the
    problem in prose. rules.md said to apply the tag shape to areas; this is it.
    """
    c, store, db = env
    nid = store.create_note("desk pet", body="ESP32 + OLED shopping list", db_path=db)
    before = {a["name"] for a in store.list_areas(db_path=db)}
    assert "DIY" not in before

    pid = store.create_proposal("file", nid,
                                {"new_area": {"name": "DIY"}, "tags": ["shopping"],
                                 "new_tags": ["shopping"]},
                                summary="needs a DIY area", db_path=db)
    # proposing creates NOTHING — the area appears only at approval
    assert {a["name"] for a in store.list_areas(db_path=db)} == before

    h = login(c)
    r = c.post("/api/proposal/%d/approve" % pid, headers=h)
    assert r.status_code == 200, r.text
    areas = {a["name"]: a for a in store.list_areas(db_path=db)}
    assert "DIY" in areas
    note = store.get_note(nid, db_path=db)
    assert note["area_id"] == areas["DIY"]["id"]     # and the note landed in it
    assert note["status"] == "active"
    assert "shopping" in store.taxonomy_tags(db_path=db)
    res = store.get_proposal(pid, db_path=db)["result"]
    assert res["new_area_name"] == "DIY" and res["new_area_id"] == areas["DIY"]["id"]


def test_area_coinage_can_nest_under_an_existing_parent(env):
    c, store, db = env
    nid = store.create_note("client X", body="notes", db_path=db)
    work = next(a["id"] for a in store.list_areas(db_path=db) if a["name"] == "Work")
    pid = store.create_proposal("file", nid,
                                {"new_area": {"name": "Clients", "parent_id": work}},
                                summary="sub-area of Work", db_path=db)
    h = login(c)
    assert c.post("/api/proposal/%d/approve" % pid, headers=h).status_code == 200
    made = next(a for a in store.list_areas(db_path=db) if a["name"] == "Clients")
    assert made["parent_id"] == work


def test_area_coinage_validation(env):
    c, store, db = env
    nid = store.create_note("n", body="b", db_path=db)
    work = next(a["id"] for a in store.list_areas(db_path=db) if a["name"] == "Work")
    bad = [
        ({"new_area": {"name": "  "}}, "non-empty name"),
        ({"new_area": {"name": "X", "nope": 1}}, "unknown fields"),
        ({"new_area": {"name": "X", "parent_id": 9999}}, "no such parent"),
        # already exists: file into it instead of making a second "Work"
        ({"new_area": {"name": "work"}}, "already exists"),
        # exclusive with area_id — one or the other, never both
        ({"new_area": {"name": "X"}, "area_id": work}, "exclusive"),
        # Archive is disposal, not a place
        ({"new_area": {"name": "X"}, "archive": True}, "exclusive"),
    ]
    for payload, msg in bad:
        with pytest.raises(ValueError, match=msg):
            store.create_proposal("file", nid, payload, db_path=db)


def test_new_area_alone_satisfies_the_file_payload(env):
    """A filing whose ONLY instruction is "put it in a new area" must be legal —
    otherwise the librarian has to invent a tag to get the proposal accepted."""
    c, store, db = env
    nid = store.create_note("n", body="b", db_path=db)
    pid = store.create_proposal("file", nid, {"new_area": "Hobby"}, db_path=db)
    assert pid > 0                                    # bare string form accepted
    h = login(c)
    assert c.post("/api/proposal/%d/approve" % pid, headers=h).status_code == 200
    assert "Hobby" in {a["name"] for a in store.list_areas(db_path=db)}


def test_a_failed_area_approval_creates_no_area(env):
    """The owner edits the coinage to a name that already exists: the approval
    must fail cleanly, leaving no half-made area behind."""
    c, store, db = env
    nid = store.create_note("n", body="b", db_path=db)
    pid = store.create_proposal("file", nid, {"new_area": {"name": "Gardening"}}, db_path=db)
    before = len(store.list_areas(db_path=db))
    h = login(c)
    r = c.post("/api/proposal/%d/approve" % pid, headers=h,
               json={"payload": {"new_area": {"name": "Work"}}})
    assert r.status_code == 409
    assert len(store.list_areas(db_path=db)) == before
    assert store.get_proposal(pid, db_path=db)["status"] == "pending"
    assert store.get_proposal(pid, db_path=db)["payload"]["new_area"]["name"] == "Gardening"


# ---- the Library's default view must hide the museum --------------------

def test_exclude_archived_returns_inbox_and_active_but_not_archive(env):
    """`status` holds ONE value, so "not archived" cannot be expressed as a
    status — which is why the Library default (labelled "Active") listed
    archived notes beside live ones. Wave 1 made that unusable: 57 archived
    notes interleaved with the ones the owner is triaging."""
    c, store, db = env
    inbox = store.create_note("captured", body="x", db_path=db)
    active = store.create_note("filed", body="x", status="active", db_path=db)
    arch = store.create_note("museum", body="x", status="archived", db_path=db)

    every = {n["id"] for n in store.list_notes(limit=500, db_path=db)}
    assert every == {inbox, active, arch}            # unfiltered is unchanged

    live = {n["id"] for n in store.list_notes(exclude_archived=True, limit=500,
                                              db_path=db)}
    assert live == {inbox, active}                   # the museum is gone
    assert arch not in live


def test_exclude_archived_composes_with_the_other_filters(env):
    """2b-ii's rule is that filters compose. Archive-hiding must narrow an area
    selection, not replace it."""
    c, store, db = env
    work = area_id(store, db, "Work")
    home = area_id(store, db, "Home")
    keep = store.create_note("work live", body="x", status="active",
                             area_id=work, db_path=db)
    store.create_note("work museum", body="x", status="archived",
                      area_id=work, db_path=db)
    store.create_note("home live", body="x", status="active",
                      area_id=home, db_path=db)
    got = store.list_notes(area_id=work, exclude_archived=True, limit=500, db_path=db)
    assert [n["id"] for n in got] == [keep]


def test_notes_api_exposes_the_filter(env):
    c, store, db = env
    store.create_note("live one", body="x", status="active", db_path=db)
    store.create_note("museum one", body="x", status="archived", db_path=db)
    login(c)
    every = c.get("/api/notes").json()["notes"]
    live = c.get("/api/notes?exclude_archived=true").json()["notes"]
    assert len(every) == 2 and len(live) == 1
    assert live[0]["title"] == "live one"


# ---- search narrows the current view instead of replacing it ------------

def test_search_respects_the_area_you_are_looking_at(env):
    """Typing in the box used to throw the owner's selection away and search
    the whole Library, so results appeared from areas they had filtered out."""
    c, store, db = env
    work, home = area_id(store, db, "Work"), area_id(store, db, "Home")
    a = store.create_note("invoice process", body="stamp the invoice",
                          status="active", area_id=work, db_path=db)
    b = store.create_note("invoice for plumber", body="stamp the invoice",
                          status="active", area_id=home, db_path=db)
    assert {n["id"] for n in store.search_notes("invoice", db_path=db)} == {a, b}
    assert [n["id"] for n in store.search_notes("invoice", area_id=work,
                                                db_path=db)] == [a]


def test_search_hides_the_archive_when_asked(env):
    c, store, db = env
    live = store.create_note("standup live", body="daily standup", status="active",
                             db_path=db)
    store.create_note("standup old", body="daily standup", status="archived",
                      db_path=db)
    assert len(store.search_notes("standup", db_path=db)) == 2
    assert [n["id"] for n in store.search_notes("standup", exclude_archived=True,
                                                db_path=db)] == [live]


def test_search_scope_applies_to_the_like_fallback_too(env):
    """A query FTS cannot parse falls back to LIKE — the scope must survive the
    fallback, or filters silently stop working on exactly the odd queries."""
    c, store, db = env
    work = area_id(store, db, "Work")
    a = store.create_note("odd one", body="pipe | star * paren (", status="active",
                          area_id=work, db_path=db)
    store.create_note("odd two", body="pipe | star * paren (", status="active",
                      db_path=db)
    got = store.search_notes("star *", area_id=work, db_path=db)
    assert [n["id"] for n in got] == [a]


def test_search_api_carries_the_filters(env):
    c, store, db = env
    work = area_id(store, db, "Work")
    a = store.create_note("api scoped", body="findme", status="active",
                          area_id=work, db_path=db)
    store.create_note("api global", body="findme", status="active", db_path=db)
    login(c)
    assert len(c.get("/api/notes?q=findme").json()["notes"]) == 2
    scoped = c.get("/api/notes?q=findme&area_id=%d" % work).json()["notes"]
    assert [n["id"] for n in scoped] == [a]


def test_unscoped_search_is_unchanged_for_the_librarian(env):
    """`wm note search` passes no filters and must keep seeing everything —
    scoping is a UI concern, not a new restriction on the agent."""
    c, store, db = env
    store.create_note("filed", body="needle", status="active",
                      area_id=area_id(store, db, "Work"), db_path=db)
    store.create_note("archived", body="needle", status="archived", db_path=db)
    store.create_note("inbox", body="needle", db_path=db)
    assert len(store.search_notes("needle", db_path=db)) == 3
