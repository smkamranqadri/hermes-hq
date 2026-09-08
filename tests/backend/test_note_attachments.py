"""Second Brain 2c: photo + voice attachments.

Bytes on disk under <hq_home>/attachments/<note_id>/, index rows in
note_attachments. Flat by NOTE ID on purpose — an id never changes, while a
note's filing is rewritten constantly by the review queue, so any path derived
from area/project would break links on every approval.

Owner-only, like every other note route: a photo is opaque to the librarian
and audio doubly so.
"""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)

from tests.backend.test_notes import env, login  # noqa: F401

JPEG = b"\xff\xd8\xff\xe0" + b"fake jpeg payload" * 4
M4A = b"\x00\x00\x00\x20ftypM4A " + b"fake audio" * 8


def _note(c, h, title="capture"):
    return c.post("/api/notes", json={"title": title, "body": title}, headers=h).json()["id"]


def _upload(c, h, nid, data=JPEG, kind="photo", mime="image/jpeg",
            name="IMG_0001.jpg", extra=None):
    return c.post("/api/note/%d/attachments" % nid,
                  files={"file": (name, data, mime)},
                  data={"kind": kind, **(extra or {})}, headers=h)


# ---- the owner boundary -------------------------------------------------

def test_attachments_are_owner_only(env):
    c, store, db = env
    h = login(c)
    nid = _note(c, h)
    r = _upload(c, h, nid)
    aid = r.json()["attachment"]["id"]

    fresh = c.__class__(c.app)          # a client with no session cookie
    assert fresh.get("/api/attachment/%d" % aid).status_code == 401
    assert fresh.post("/api/note/%d/attachments" % nid).status_code == 401
    assert fresh.delete("/api/attachment/%d" % aid).status_code == 401
    # session but no CSRF header: mutations still refused
    assert c.post("/api/note/%d/attachments" % nid).status_code == 403
    assert c.delete("/api/attachment/%d" % aid).status_code == 403


# ---- round trip ---------------------------------------------------------

def test_photo_round_trip_lands_on_the_note_and_serves_back(env):
    c, store, db = env
    h = login(c)
    nid = _note(c, h, "dentist")
    r = _upload(c, h, nid)
    assert r.status_code == 200
    a = r.json()["attachment"]
    assert a["kind"] == "photo" and a["mime"] == "image/jpeg"
    assert a["bytes"] == len(JPEG) and a["renderable"] is True
    assert a["orig_name"] == "IMG_0001.jpg"
    # the stored name is GENERATED — never the phone's, which is attacker-ish
    # input and also collides across photos ("IMG_0001.jpg" every time)
    assert a["stored_name"] != "IMG_0001.jpg" and a["stored_name"].endswith(".jpg")

    # it is on the note the owner reads
    note = c.get("/api/note/%d" % nid, headers=h).json()
    assert [x["id"] for x in note["attachments"]] == [a["id"]]

    # and the bytes come back verbatim, with the right type for <img>
    got = c.get("/api/attachment/%d" % a["id"], headers=h)
    assert got.status_code == 200
    assert got.content == JPEG
    assert got.headers["content-type"].startswith("image/jpeg")

    on_disk = store.attachment_path(nid, a["stored_name"])
    assert os.path.isfile(on_disk)
    assert os.path.dirname(on_disk) == store.attachments_dir(nid)


def test_voice_round_trip_keeps_mime_and_duration(env):
    """MediaRecorder emits audio/mp4 on iOS and audio/webm on Chrome. Storing
    the blob's REAL mime is what makes <audio> play on both."""
    c, store, db = env
    h = login(c)
    nid = _note(c, h, "memo")
    r = _upload(c, h, nid, data=M4A, kind="voice", mime="audio/mp4",
                name="memo.m4a", extra={"duration_ms": "4200"})
    a = r.json()["attachment"]
    assert a["kind"] == "voice" and a["mime"] == "audio/mp4"
    assert a["duration_ms"] == 4200 and a["stored_name"].endswith(".m4a")
    got = c.get("/api/attachment/%d" % a["id"], headers=h)
    assert got.content == M4A and got.headers["content-type"].startswith("audio/mp4")

    webm = _upload(c, h, nid, data=b"webmbytes", kind="voice",
                   mime="audio/webm;codecs=opus", name="memo.webm").json()["attachment"]
    assert webm["mime"] == "audio/webm" and webm["stored_name"].endswith(".webm")


def test_heic_is_stored_but_flagged_unrenderable(env):
    """iPhones shoot HEIC on 'High Efficiency' and no browser paints it in an
    <img>. Keep the file, but let the UI say so honestly."""
    c, store, db = env
    h = login(c)
    nid = _note(c, h)
    a = _upload(c, h, nid, data=b"heicbytes", mime="image/heic",
                name="IMG_2.HEIC").json()["attachment"]
    assert a["renderable"] is False and a["stored_name"].endswith(".heic")
    assert c.get("/api/attachment/%d" % a["id"], headers=h).content == b"heicbytes"


# ---- refusals -----------------------------------------------------------

def test_refusals_leave_the_note_untouched(env):
    c, store, db = env
    h = login(c)
    nid = _note(c, h)
    assert _upload(c, h, nid, data=b"").status_code == 400          # empty
    assert _upload(c, h, nid, kind="movie").status_code == 400      # bad kind
    assert _upload(c, h, 999999).status_code == 404                 # no note
    assert c.get("/api/note/%d" % nid, headers=h).json()["attachments"] == []
    assert not os.path.isdir(store.attachments_dir(nid)) or \
        not os.listdir(store.attachments_dir(nid))


def test_oversized_upload_is_refused(env, monkeypatch):
    c, store, db = env
    from backend import notes as api
    monkeypatch.setattr(api, "UPLOAD_MAX_BYTES", 1000)
    h = login(c)
    nid = _note(c, h)
    assert _upload(c, h, nid, data=b"x" * 5000).status_code == 413
    assert c.get("/api/note/%d" % nid, headers=h).json()["attachments"] == []


def test_missing_file_reports_gone_not_a_broken_image(env):
    """A row that outlived its bytes must not serve an empty 200 — that renders
    as a silently broken image on the phone."""
    c, store, db = env
    h = login(c)
    nid = _note(c, h)
    a = _upload(c, h, nid).json()["attachment"]
    os.remove(store.attachment_path(nid, a["stored_name"]))
    assert c.get("/api/attachment/%d" % a["id"], headers=h).status_code == 410


# ---- removal (NOT note deletion) ----------------------------------------

def test_removing_an_attachment_keeps_the_note(env):
    """Notes stay archive-only (owner decision 2026-09-07). Without per-file
    removal a mis-shot photo would be permanent."""
    c, store, db = env
    h = login(c)
    nid = _note(c, h)
    a = _upload(c, h, nid).json()["attachment"]
    b = _upload(c, h, nid, data=M4A, kind="voice", mime="audio/mp4").json()["attachment"]
    path = store.attachment_path(nid, a["stored_name"])

    assert c.delete("/api/attachment/%d" % a["id"], headers=h).status_code == 200
    assert not os.path.exists(path)
    note = c.get("/api/note/%d" % nid, headers=h).json()
    assert note["id"] == nid                                  # the note survives
    assert [x["id"] for x in note["attachments"]] == [b["id"]]
    assert c.delete("/api/attachment/%d" % a["id"], headers=h).status_code == 404


def test_stored_name_cannot_escape_the_note_directory(env):
    c, store, db = env
    base = os.path.realpath(store.attachments_dir(1))
    for hostile in ("../../etc/passwd", "..", "/etc/passwd"):
        try:
            p = store.attachment_path(1, hostile)
        except ValueError:
            continue                                          # refused outright
        assert os.path.realpath(p).startswith(base)           # or sanitised


# ---- slice B: the librarian's boundary ---------------------------------

def test_voice_only_note_is_the_owners_queue_not_the_librarians(env):
    """A memo with no text cannot be triaged by an agent that cannot hear it.
    The heartbeat must not mint a run for it, or every recording costs a model
    call to be told 'cannot tell'."""
    c, store, db = env
    h = login(c)
    # a normal capture DOES give the librarian work
    typed = _note(c, h, "dentist tuesday")
    assert store.heartbeat_check("librarian_ingest", db_path=db)[0] is True

    # ...but a voice-only capture does not
    c.post("/api/note/%d/edit" % typed, json={"status": "archived"}, headers=h)
    voice = c.post("/api/notes", json={"title": "Voice memo · now", "body": ""},
                   headers=h).json()["id"]
    _upload(c, h, voice, data=M4A, kind="voice", mime="audio/mp4", name="memo.m4a")
    has_work, detail = store.heartbeat_check("librarian_ingest", db_path=db)
    assert has_work is False, detail
    assert "0 untriaged" in detail

    # the flag is on the row the owner sees, per the standing rule that
    # anything awaiting the owner shows on every surface
    row = [n for n in store.list_notes(status="inbox", db_path=db) if n["id"] == voice][0]
    assert row["awaiting_transcript"] is True
    assert row["attachment_counts"] == {"voice": 1}

    # adding text releases it to the librarian
    c.post("/api/note/%d/edit" % voice, json={"body": "it was about the dentist"}, headers=h)
    assert store.heartbeat_check("librarian_ingest", db_path=db)[0] is True
    row = [n for n in store.list_notes(status="inbox", db_path=db) if n["id"] == voice][0]
    assert row["awaiting_transcript"] is False


def test_a_photo_note_with_text_is_still_the_librarians_work(env):
    """Only VOICE with no text is excluded — a photo alongside a written
    capture is ordinary triageable content."""
    c, store, db = env
    h = login(c)
    nid = _note(c, h, "whiteboard from standup")
    _upload(c, h, nid)
    assert store.heartbeat_check("librarian_ingest", db_path=db)[0] is True
    row = [n for n in store.list_notes(status="inbox", db_path=db) if n["id"] == nid][0]
    assert row["awaiting_transcript"] is False
    assert row["attachment_counts"] == {"photo": 1}


def test_wm_note_show_reports_attachments_but_never_bytes(env, capsys):
    """The librarian must know a note HAS a photo/memo — otherwise it files an
    apparently-empty body on the title alone. It must never get the bytes."""
    c, store, db = env
    h = login(c)
    nid = _note(c, h, "Voice memo · now")
    c.post("/api/note/%d/edit" % nid, json={"body": ""}, headers=h)
    _upload(c, h, nid, data=M4A, kind="voice", mime="audio/mp4", name="memo.m4a")
    from core import wm_cli
    wm_cli.cmd_note_show(type("A", (), {"id": nid})())
    out = capsys.readouterr().out
    assert "attachments: 1 voice" in out
    assert "VOICE-ONLY, UNTRANSCRIBED" in out and "do not propose a filing" in out
    assert "fake audio" not in out                     # no bytes, ever
    assert store.attachments_dir(nid) not in out       # not even the path


# ---- slice B: backup ----------------------------------------------------

def test_backup_copies_the_attachments_next_to_the_db(env, tmp_path):
    """The DB is half the second brain now: a backup with the index but not
    the bytes restores to broken images."""
    c, store, db = env
    h = login(c)
    nid = _note(c, h)
    a = _upload(c, h, nid).json()["attachment"]
    dest = store.backup_db(db_path=db, backup_dir=str(tmp_path / "bk"))
    assert os.path.isfile(dest)
    copied = dest.replace(".backup.db", ".attachments")
    assert os.path.isfile(os.path.join(copied, str(nid), a["stored_name"]))
    with open(os.path.join(copied, str(nid), a["stored_name"]), "rb") as fh:
        assert fh.read() == JPEG


def test_backup_still_succeeds_when_attachments_cannot_be_copied(env, tmp_path, monkeypatch):
    """A failed file copy must never lose the database backup that already
    succeeded — attachments are best-effort, the DB is not."""
    c, store, db = env
    h = login(c)
    _upload(c, h, _note(c, h))
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(store.shutil, "copytree", boom)
    dest = store.backup_db(db_path=db, backup_dir=str(tmp_path / "bk2"))
    assert os.path.isfile(dest)
