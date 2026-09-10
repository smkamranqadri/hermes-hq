"""Second Brain Phase 4 slice A — export, and the restore that proves it.

The owner's 2026-09-10 decision was "readable AND provably re-importable", so
the load-bearing test here is the ROUND TRIP: export a library, restore it into
an empty one, and reconcile notes, Thoughts, links, tags, areas and attachment
bytes. A zip that opens in Obsidian but cannot come back is a souvenir, not the
backup this phase is meant to hand the owner before the vault's delete path
lands.
"""
import json
import os
import sys
import zipfile

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
    os.makedirs(tmp_path / "alpha", exist_ok=True)
    store.create_project("alpha", "Alpha", "", str(tmp_path / "alpha"),
                         db_path=store.DEFAULT_DB_PATH)
    yield store, store.DEFAULT_DB_PATH, tmp_path


def area_id(store, db, name):
    return next(a["id"] for a in store.list_areas(db_path=db) if a["name"] == name)


def build_library(store, db):
    """A small library that carries every shape the export has to preserve."""
    work = area_id(store, db, "Work")
    ids = {}
    ids["plain"] = store.create_note("Server Setup", body="# Setup\n\n- one\n- two",
                                     status="active", area_id=work,
                                     tags=["docker"], db_path=db)
    # same title, same area: the 128-same-title-group case from the import
    ids["twin"] = store.create_note("Server Setup", body="the other one",
                                    status="active", area_id=work,
                                    tags=["docker"], db_path=db)
    ids["slash"] = store.create_note('Work/Life "balance"', body="tricky title",
                                     status="inbox", db_path=db)
    ids["archived"] = store.create_note("Old thing", body="museum piece",
                                        status="archived", area_id=work, db_path=db)
    ids["urdu"] = store.create_note("اردو نوٹس", body="اردو متن", status="inbox",
                                    db_path=db)
    store.add_note_entry(ids["plain"], "first thought", created_at=1_700_000_000.0,
                         db_path=db)
    store.add_note_entry(ids["plain"], "second thought\nwith two lines",
                         created_at=1_700_086_400.0, db_path=db)
    store.link_note(ids["plain"], "note", ids["twin"], db_path=db)
    store.add_attachment(ids["plain"], b"\x89PNG-not-really", kind="photo",
                         mime="image/png", orig_name="IMG_0001.png", db_path=db)
    store.add_attachment(ids["urdu"], b"voice-bytes", kind="voice",
                         mime="audio/mp4", orig_name="rec.m4a", duration_ms=2280,
                         db_path=db)
    store.register_tags(["unused-vocab"], added_by="owner", db_path=db)
    return ids


def do_export(store, db, tmp_path, name="brain.zip", **kw):
    from backend import notes_export as nx
    out = str(tmp_path / name)
    stats = nx.export_zip(store, out, db_path=db, **kw)
    return out, stats


def unzip(path, dest):
    with zipfile.ZipFile(path) as z:
        z.extractall(dest)
    return dest


# ---- layout -------------------------------------------------------------

def test_export_lays_out_para_folders_with_manifest(env, tmp_path):
    store, db, _ = env
    build_library(store, db)
    out, stats = do_export(store, db, tmp_path)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        manifest = json.loads(z.read("_hermes/manifest.json"))
    assert any(n.startswith("Areas/Work/") and n.endswith(".md") for n in names)
    assert any(n.startswith("Inbox/") for n in names)
    assert any(n.startswith("Archive/Work/") for n in names)
    assert "README.md" in names
    assert manifest["export_version"] == 1
    assert manifest["counts"]["notes"] == stats["notes"] == 5
    assert manifest["counts"]["entries"] == 2
    assert {t["tag"] for t in manifest["tags"]} >= {"docker", "unused-vocab"}


def test_every_filename_carries_the_note_id(env, tmp_path):
    """Two notes called `Server Setup` in one area are two files. The import
    measured 128 same-title groups; without the id one silently eats the other."""
    store, db, _ = env
    ids = build_library(store, db)
    out, _ = do_export(store, db, tmp_path)
    with zipfile.ZipFile(out) as z:
        work = [n for n in z.namelist() if n.startswith("Areas/Work/")
                and n.endswith(".md")]
    assert len(work) == 2
    assert {os.path.basename(n) for n in work} == {
        "Server Setup (#%d).md" % ids["plain"], "Server Setup (#%d).md" % ids["twin"]}


def test_hostile_title_cannot_escape_its_folder(env, tmp_path):
    store, db, _ = env
    ids = build_library(store, db)
    out, _ = do_export(store, db, tmp_path)
    with zipfile.ZipFile(out) as z:
        hit = [n for n in z.namelist() if "#%d" % ids["slash"] in n]
    assert len(hit) == 1
    assert hit[0] == "Inbox/Work-Life -balance- (#%d).md" % ids["slash"]


def test_attachments_are_written_beside_their_note(env, tmp_path):
    store, db, _ = env
    ids = build_library(store, db)
    out, stats = do_export(store, db, tmp_path)
    with zipfile.ZipFile(out) as z:
        att = [n for n in z.namelist() if "/attachments/" in n]
        doc = z.read([n for n in z.namelist()
                      if "(#%d).md" % ids["plain"] in n][0]).decode()
        raw = z.read(att[0])
    assert stats["attachments"] == 2
    assert all(os.path.basename(n).split("-")[0].isdigit() for n in att)
    assert raw == b"\x89PNG-not-really"
    assert "![[%d-" % ids["plain"] in doc          # Obsidian embed


def test_missing_bytes_are_reported_not_fatal(env, tmp_path):
    store, db, _ = env
    ids = build_library(store, db)
    a = store.list_attachments(ids["plain"], db_path=db)[0]
    os.remove(store.attachment_path(ids["plain"], a["stored_name"]))
    out, stats = do_export(store, db, tmp_path)
    assert stats["notes"] == 5                     # the other notes still export
    assert stats["missing_attachments"] == [
        {"note_id": ids["plain"], "stored_name": a["stored_name"]}]


# ---- the document -------------------------------------------------------

def test_body_round_trips_byte_for_byte(env, tmp_path):
    """Thoughts and attachments sit BELOW a delimiter for exactly this reason."""
    store, db, _ = env
    from backend import notes_import as ni
    ids = build_library(store, db)
    out, _ = do_export(store, db, tmp_path)
    with zipfile.ZipFile(out) as z:
        doc = z.read([n for n in z.namelist()
                      if "(#%d).md" % ids["plain"] in n][0]).decode()
    body, thoughts = ni._split_document(doc.split("---\n", 2)[2])
    assert body == store.get_note(ids["plain"], db_path=db)["body"]
    assert [t[1] for t in thoughts] == ["first thought",
                                        "second thought\nwith two lines"]


def test_quotes_and_backslashes_survive_the_frontmatter(env, tmp_path):
    store, db, _ = env
    from backend import notes_import as ni
    nid = store.create_note(r'He said "hi" \ then left', body="x", status="inbox",
                            db_path=db)
    out, _ = do_export(store, db, tmp_path)
    with zipfile.ZipFile(out) as z:
        doc = z.read([n for n in z.namelist() if "(#%d).md" % nid in n][0]).decode()
    meta = ni._yaml_lite(doc.split("---\n")[1])
    assert meta["title"] == r'He said "hi" \ then left'


# ---- the route ----------------------------------------------------------

def test_export_route_is_owner_only_and_returns_a_zip(env, tmp_path):
    store, db, _ = env
    build_library(store, db)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        assert c.get("/api/brain/export.zip").status_code == 401
        c.post("/api/login", json={"password": "pw-test"})
        r = c.get("/api/brain/export.zip")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        path = tmp_path / "from-route.zip"
        path.write_bytes(r.content)
        with zipfile.ZipFile(path) as z:
            assert json.loads(z.read("_hermes/manifest.json"))["counts"]["notes"] == 5


# ---- the round trip -----------------------------------------------------

def restore_into_fresh(store, tmp_path, monkeypatch, src_zip, name="hq2"):
    """Restore into a second library whose attachments live somewhere else, so
    a note id colliding across the two DBs cannot cross-contaminate bytes."""
    from backend import notes_import as ni
    root = unzip(src_zip, str(tmp_path / (name + "-unzipped")))
    db2 = str(tmp_path / (name + ".db"))
    store.init_db(db_path=db2)
    monkeypatch.setenv("HERMES_HQ_ATTACHMENTS_DIR", str(tmp_path / (name + "-att")))
    res = ni.import_export(root, store, db_path=db2, dry_run=False)
    return db2, res, root


def test_round_trip_reconciles_the_whole_library(env, tmp_path, monkeypatch):
    store, db, _ = env
    ids = build_library(store, db)
    out, stats = do_export(store, db, tmp_path)
    db2, res, root = restore_into_fresh(store, tmp_path, monkeypatch, out)

    assert res["errors"] == []
    assert len(res["created"]) == stats["notes"] == 5
    assert res["entries"] == stats["entries"] == 2
    assert res["links"] == stats["links"] == 1
    assert res["attachments"] == stats["attachments"] == 2

    before = {n["title"] for n in store.list_notes(limit=1000, db_path=db)}
    after = {n["title"] for n in store.list_notes(limit=1000, db_path=db2)}
    assert before == after
    assert len(store.list_areas(db_path=db2, archived=True)) == \
        len(store.list_areas(db_path=db, archived=True))
    assert store.taxonomy_tags(db_path=db2) == store.taxonomy_tags(db_path=db)

    src = store.get_note(ids["plain"], db_path=db)
    dst = next(n for n in store.list_notes(limit=1000, db_path=db2)
               if n["title"] == "Server Setup" and n["body"] == src["body"])
    full = store.get_note(dst["id"], db_path=db2)
    assert full["body"] == src["body"]
    assert full["tags"] == src["tags"]
    assert full["status"] == src["status"] and full["type"] == src["type"]
    assert full["authored_by"] == src["authored_by"]
    assert int(full["created_at"]) == int(src["created_at"])
    assert int(full["updated_at"]) == int(src["updated_at"])
    assert [e["body"] for e in full["entries"]] == [e["body"] for e in src["entries"]]
    assert [int(e["created_at"]) for e in full["entries"]] == \
           [int(e["created_at"]) for e in src["entries"]]
    assert full["area"]["name"] == src["area"]["name"]
    assert [l["kind"] for l in full["links"]] == ["note"]


def test_restored_attachment_bytes_are_identical(env, tmp_path, monkeypatch):
    store, db, _ = env
    ids = build_library(store, db)
    out, _ = do_export(store, db, tmp_path)
    db2, res, _ = restore_into_fresh(store, tmp_path, monkeypatch, out)
    got = {}
    for n in store.list_notes(limit=1000, db_path=db2):
        for a in store.list_attachments(n["id"], db_path=db2):
            with open(store.attachment_path(n["id"], a["stored_name"]), "rb") as fh:
                got[a["kind"]] = (fh.read(), a["mime"], a["orig_name"],
                                  a["duration_ms"])
    assert got["photo"][:3] == (b"\x89PNG-not-really", "image/png", "IMG_0001.png")
    assert got["voice"] == (b"voice-bytes", "audio/mp4", "rec.m4a", 2280)


def test_restore_is_idempotent(env, tmp_path, monkeypatch):
    store, db, _ = env
    build_library(store, db)
    out, _ = do_export(store, db, tmp_path)
    db2, _res, root = restore_into_fresh(store, tmp_path, monkeypatch, out)
    from backend import notes_import as ni
    again = ni.import_export(root, store, db_path=db2, dry_run=False)
    assert again["created"] == []
    assert again["already_imported"] == 5
    assert len(store.list_notes(limit=1000, db_path=db2)) == 5


def test_restore_refuses_a_library_it_does_not_account_for(env, tmp_path, monkeypatch):
    """A stray --apply against the live library would otherwise clone all 945."""
    store, db, _ = env
    build_library(store, db)
    out, _ = do_export(store, db, tmp_path)
    from backend import notes_import as ni
    root = unzip(out, str(tmp_path / "unz-refuse"))
    db2 = str(tmp_path / "occupied.db")
    store.init_db(db_path=db2)
    store.create_note("someone else's note", status="inbox", db_path=db2)
    res = ni.import_export(root, store, db_path=db2, dry_run=False)
    assert res["created"] == [] and res["errors"]
    assert "Restore into an EMPTY library" in res["errors"][0]
    assert len(store.list_notes(limit=1000, db_path=db2)) == 1


def test_restore_derives_no_tags_from_its_own_folders(env, tmp_path, monkeypatch):
    """`folder_tags` on an export would mint `areas`/`inbox`/`archive` tags —
    the origin-tag mistake stripped from the library on 2026-09-08."""
    store, db, _ = env
    build_library(store, db)
    out, _ = do_export(store, db, tmp_path)
    db2, _res, _ = restore_into_fresh(store, tmp_path, monkeypatch, out)
    for n in store.list_notes(limit=1000, db_path=db2):
        assert not ({"areas", "inbox", "archive", "work"} & set(n["tags"]))
    assert store.taxonomy_tags(db_path=db2) == store.taxonomy_tags(db_path=db)


def test_dry_run_writes_nothing(env, tmp_path, monkeypatch):
    store, db, _ = env
    build_library(store, db)
    out, _ = do_export(store, db, tmp_path)
    from backend import notes_import as ni
    root = unzip(out, str(tmp_path / "unz-dry"))
    db2 = str(tmp_path / "dry.db")
    store.init_db(db_path=db2)
    res = ni.import_export(root, store, db_path=db2, dry_run=True)
    assert len(res["created"]) == 5
    assert store.list_notes(limit=1000, db_path=db2) == []
    assert len(store.list_areas(db_path=db2)) > 0     # seeded, not restored yet
