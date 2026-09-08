"""AreaDescriptionsPlan step 1: areas carry a description, and an area is
removed by RETIRING it (archiving) rather than deleted -- matching the
archive-only note lifecycle.

The two rules that matter and are easy to get wrong:

1. `list_areas`/`notes_tree` filter `archived=0`, so ONE flip hides an area
   from every filing surface. That is what makes retiring cheap, and it is why
   retiring must be GUARDED: an area still holding live notes would strand
   them (an `active` note losing the home policy 12 requires, an `inbox` note
   pointing at an area no surface can show).
2. Label resolution must still see a retired area, or a note filed before the
   retirement renders with no home at all and reads as though it was never
   filed. `list_areas(archived=True)` and `get_note` are that path.
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


def _area(store, db, name, description=None, parent_id=None):
    """Reuse a SEEDED area when the name is one of them (Journal, Home, Work,
    Study, Content ... are seeded into every fresh DB) and create otherwise, so
    the tests can use the REAL names this pass retires rather than stand-ins."""
    hit = [a for a in store.list_areas(db_path=db, archived=True)
           if a["name"] == name and a["parent_id"] == parent_id]
    if hit:
        aid = hit[0]["id"]
        if description is not None:
            store.update_area(aid, description=description, db_path=db)
        return aid
    return store.create_area(name, parent_id=parent_id, description=description,
                             db_path=db)


# ---- description round-trip ------------------------------------------------

def test_description_round_trips_through_create_and_update(env):
    _, store, db = env
    aid = _area(store, db, "Reading", description="  What I consume.  ")
    # Trimmed on the way in, not stored with the owner's stray whitespace.
    assert store.get_area(aid, db_path=db)["description"] == "What I consume."
    store.update_area(aid, description="What I consume. Publishing lives in Content.",
                      db_path=db)
    assert store.get_area(aid, db_path=db)["description"].endswith("lives in Content.")
    # An all-whitespace description CLEARS rather than storing blanks.
    store.update_area(aid, description="   ", db_path=db)
    assert store.get_area(aid, db_path=db)["description"] is None
    # And it reaches every list consumer.
    got = [a for a in store.list_areas(db_path=db) if a["id"] == aid][0]
    assert "description" in got


def test_create_area_without_description_leaves_it_null(env):
    _, store, db = env
    aid = _area(store, db, "Games")
    assert store.get_area(aid, db_path=db)["description"] is None


def test_update_area_rejects_empty_patch_and_bad_types(env):
    _, store, db = env
    aid = _area(store, db, "Space")
    with pytest.raises(ValueError, match="nothing to update"):
        store.update_area(aid, db_path=db)
    with pytest.raises(ValueError, match="must be text"):
        store.update_area(aid, description=42, db_path=db)
    with pytest.raises(ValueError, match="no such area"):
        store.update_area(99999, description="x", db_path=db)


def test_rename_refuses_a_name_already_taken(env):
    _, store, db = env
    _area(store, db, "DevOps")
    other = _area(store, db, "DIY")
    with pytest.raises(ValueError, match="already exists"):
        store.update_area(other, name="DevOps", db_path=db)


# ---- the retire guard ------------------------------------------------------

def test_retire_refused_while_active_notes_remain(env):
    _, store, db = env
    aid = _area(store, db, "Journal")
    store.create_note("a filed note", status="active", area_id=aid, db_path=db)
    with pytest.raises(ValueError) as e:
        store.update_area(aid, archived=True, db_path=db)
    # The refusal NAMES the area and counts what is in the way, so the caller
    # knows what to refile rather than guessing.
    assert "Journal" in str(e.value) and "1 note" in str(e.value)
    assert store.get_area(aid, db_path=db)["archived"] == 0


def test_retire_refused_while_inbox_notes_remain(env):
    """An inbox note is not homeless, but it is still REACHABLE — retiring its
    area would point it at somewhere no surface can show."""
    _, store, db = env
    aid = _area(store, db, "Home")
    store.create_note("untriaged", status="inbox", area_id=aid, db_path=db)
    with pytest.raises(ValueError, match="still filed here"):
        store.update_area(aid, archived=True, db_path=db)


def test_retire_succeeds_when_only_archived_notes_trail_it(env):
    _, store, db = env
    aid = _area(store, db, "Home")
    nid = store.create_note("old", status="active", area_id=aid, db_path=db)
    store.update_note(nid, status="archived", db_path=db)
    store.update_area(aid, archived=True, db_path=db)
    assert store.get_area(aid, db_path=db)["archived"] == 1
    # Gone from every filing surface in one flip...
    assert aid not in [a["id"] for a in store.list_areas(db_path=db)]
    assert aid not in [a["id"] for a in store.notes_tree(db_path=db)["areas"]]
    # ...but still resolvable as a LABEL.
    assert aid in [a["id"] for a in store.list_areas(db_path=db, archived=True)]


def test_retire_refused_while_a_live_sub_area_hangs_off_it(env):
    _, store, db = env
    parent = _area(store, db, "Work")
    _area(store, db, "Client", parent_id=parent)
    with pytest.raises(ValueError, match="live sub-area"):
        store.update_area(parent, archived=True, db_path=db)


def test_a_retired_area_can_be_restored(env):
    _, store, db = env
    aid = _area(store, db, "Study")
    store.update_area(aid, archived=True, db_path=db)
    store.update_area(aid, archived=False, db_path=db)
    assert aid in [a["id"] for a in store.list_areas(db_path=db)]


# ---- the label path (the edge case this slice exists to not ship broken) ----

def test_archived_note_in_a_retired_area_still_reports_its_home(env):
    _, store, db = env
    aid = _area(store, db, "Home", description="retired")
    nid = store.create_note("Multi-branch setup", status="active", area_id=aid, db_path=db)
    store.update_note(nid, status="archived", db_path=db)
    store.update_area(aid, archived=True, db_path=db)
    n = store.get_note(nid, db_path=db)
    assert n["area"] is not None, "a retired area must not blank the note's home"
    assert n["area"]["name"] == "Home"
    assert n["area"]["archived"] == 1        # so the UI can mark it retired
    assert n["area_id"] == aid               # the filing itself is untouched


# ---- audit -----------------------------------------------------------------

def test_every_area_edit_writes_an_audit_row(env):
    _, store, db = env
    aid = _area(store, db, "Content")
    store.update_area(aid, description="What I publish.", db_path=db)
    store.update_area(aid, name="Content ", db_path=db)
    import sqlite3
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    edits = [dict(r) for r in conn.execute(
        "SELECT * FROM activity WHERE action='area_updated' ORDER BY id")]
    conn.close()
    assert len(edits) == 2
    assert "description" in edits[0]["detail"] or "description" in edits[1]["detail"]
    # rename trimmed
    assert store.get_area(aid, db_path=db)["name"] == "Content"


# ---- HTTP surface ----------------------------------------------------------

def test_api_patch_and_include_archived(env):
    c, store, db = env
    h = login(c)
    aid = _area(store, db, "Reading")

    r = c.patch(f"/api/areas/{aid}", json={"description": "What I consume."}, headers=h)
    assert r.status_code == 200
    assert r.json()["area"]["description"] == "What I consume."
    # GET carries the description to the UI.
    assert [a for a in c.get("/api/areas").json()["areas"]
            if a["id"] == aid][0]["description"] == "What I consume."

    # Retire it, then prove the two GET shapes differ exactly as intended.
    assert c.patch(f"/api/areas/{aid}", json={"archived": True}, headers=h).status_code == 200
    assert aid not in [a["id"] for a in c.get("/api/areas").json()["areas"]]
    assert aid in [a["id"] for a in
                   c.get("/api/areas?include_archived=1").json()["areas"]]


def test_api_retire_refusal_is_a_409_the_ui_can_show(env):
    c, store, db = env
    h = login(c)
    aid = _area(store, db, "Journal")
    store.create_note("live one", status="active", area_id=aid, db_path=db)
    r = c.patch(f"/api/areas/{aid}", json={"archived": True}, headers=h)
    assert r.status_code == 409
    assert "refile" in r.json()["detail"]


def test_api_create_area_accepts_a_description(env):
    c, store, db = env
    h = login(c)
    r = c.post("/api/areas", json={"name": "Reading", "description": "What I consume."},
               headers=h)
    assert r.status_code == 200
    assert store.get_area(r.json()["id"], db_path=db)["description"] == "What I consume."


# ---- the agent surface -----------------------------------------------------

def test_wm_note_areas_prints_the_description(env, capsys):
    _, store, db = env
    store.create_area("DevOps", description="Servers and containers. App code lives in Development.",
                      db_path=db)
    from core import wm_cli
    wm_cli.cmd_note_areas(object())
    out = capsys.readouterr().out
    assert "DevOps" in out
    assert "App code lives in Development." in out
    # An area with no description says so rather than looking finished.
    assert "no description yet" in out


def test_patch_that_does_not_mention_description_leaves_it_alone(env):
    """Regression: `description: str | None = None` cannot distinguish "absent"
    from "explicit null", so forwarding it unconditionally wiped the field on
    every rename. Only fields actually SENT are forwarded."""
    c, store, db = env
    h = login(c)
    aid = _area(store, db, "Reading", description="What I consume.")

    assert c.patch(f"/api/areas/{aid}", json={"name": "Reading list"},
                   headers=h).status_code == 200
    assert store.get_area(aid, db_path=db)["description"] == "What I consume."

    # An EXPLICIT null still clears it — that is the other half of the contract.
    assert c.patch(f"/api/areas/{aid}", json={"description": None},
                   headers=h).status_code == 200
    assert store.get_area(aid, db_path=db)["description"] is None


def test_patch_with_no_fields_is_refused(env):
    c, store, db = env
    h = login(c)
    aid = _area(store, db, "Reading")
    assert c.patch(f"/api/areas/{aid}", json={}, headers=h).status_code == 409
