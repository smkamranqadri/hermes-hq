"""LibraryPolishPlan Slice E — paging: every list answer carries the unpaged
`total` under the same filters, list AND search page with limit/offset, and
the paged sequence is the unpaged list (no duplicates, no gaps, owner_first
ordering intact)."""
import os
import sys

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


def seed(store, db, n_work=12, n_arch=3):
    work = store.list_areas(db_path=db)[0]["id"]
    ids = []
    for i in range(n_work):
        ids.append(store.create_note("Work note %02d" % i, body="alpha body %d" % i,
                                     status="active", area_id=work, tags=["t%d" % (i % 3)],
                                     authored_by="import" if i % 4 else "owner", db_path=db))
    for i in range(n_arch):
        nid = store.create_note("Old note %02d" % i, body="alpha old", status="active",
                                area_id=work, db_path=db)
        store.update_note(nid, status="archived", db_path=db)
    return work, ids


def test_total_matches_unpaged_list_and_pages_tile_it(env):
    c, store, db = env
    work, _ = seed(store, db)
    h = login(c)
    live = {"area_id": work, "exclude_archived": "true"}
    full = c.get("/api/notes", params={**live, "limit": 500}, headers=h).json()
    assert full["total"] == 12 and len(full["notes"]) == 12
    everything = c.get("/api/notes", params={"area_id": work, "limit": 500}, headers=h).json()
    assert everything["total"] == 15  # no archive filter = the museum too, same as the list
    seen = []
    for off in (0, 5, 10):
        page = c.get("/api/notes", params={**live, "limit": 5, "offset": off}, headers=h).json()
        assert page["total"] == 12 and page["limit"] == 5 and page["offset"] == off
        seen += [n["id"] for n in page["notes"]]
    assert seen == [n["id"] for n in full["notes"]]  # same order, no dup, no gap
    # total honours the archive dimension the same way the list does
    arch = c.get("/api/notes", params={"area_id": work, "status": "archived", "limit": 1}, headers=h).json()
    assert arch["total"] == 3 and len(arch["notes"]) == 1


def test_search_pages_and_counts_the_same_hits(env):
    c, store, db = env
    work, _ = seed(store, db)
    h = login(c)
    full = c.get("/api/notes", params={"q": "alpha", "limit": 500}, headers=h).json()
    assert full["total"] == len(full["notes"]) == 15  # q alone is global: live + archived
    # exclude_archived narrows both the hits and the total identically
    live = c.get("/api/notes", params={"q": "alpha", "exclude_archived": "true", "limit": 500}, headers=h).json()
    assert live["total"] == len(live["notes"]) == 12
    p1 = c.get("/api/notes", params={"q": "alpha", "limit": 4, "offset": 0}, headers=h).json()
    p2 = c.get("/api/notes", params={"q": "alpha", "limit": 4, "offset": 4}, headers=h).json()
    assert len(p1["notes"]) == 4 and len(p2["notes"]) == 4
    assert not {n["id"] for n in p1["notes"]} & {n["id"] for n in p2["notes"]}
    assert p1["total"] == p2["total"] == full["total"]


def test_owner_first_ordering_survives_paging(env):
    c, store, db = env
    work, _ = seed(store, db)
    full = store.list_notes(area_id=work, owner_first=True, exclude_archived=True, limit=500, db_path=db)
    paged = []
    for off in range(0, 12, 4):
        paged += store.list_notes(area_id=work, owner_first=True, exclude_archived=True, limit=4, offset=off, db_path=db)
    assert [n["id"] for n in paged] == [n["id"] for n in full]
    assert [n["authored_by"] for n in full][:3] == ["owner"] * 3


def test_count_notes_agrees_with_facets_active(env):
    c, store, db = env
    work, _ = seed(store, db)
    h = login(c)
    for params in ({"area_id": work, "tag": "t1"}, {"area_id": work, "q": "alpha"}, {"status": "archived"}):
        lst = c.get("/api/notes", params={**params, "limit": 500}, headers=h).json()
        assert lst["total"] == len(lst["notes"])
