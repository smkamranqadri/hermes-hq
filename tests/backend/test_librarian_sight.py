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
