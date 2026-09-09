"""LibraryReconcilePlan slice 1: the tag vocabulary becomes navigable.

Three pieces, all of which the audit of 2026-09-09 showed were missing:
  * `folder_tags` recovers the topic structure the dumps were organized by,
    WITHOUT ever re-introducing an origin tag (the 2026-09-08 decision).
  * `rename_tag` / `retire_tag` — the closed taxonomy had no way to fix itself,
    which is how `BrightLane` and `brightlane` coexisted for days.
  * `add_note_tags` merges vocabulary without bumping `updated_at`, so a
    backfill does not reorder the whole Library.
"""
import os, sys, time
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
    yield store, store.DEFAULT_DB_PATH


class U:
    """Minimal stand-in for a parsed Unit — folder_tags only reads `ident`."""
    def __init__(self, ident): self.ident = ident


# ---- folder_tags -----------------------------------------------------------

def test_folder_tags_recovers_the_topic_tree():
    from backend import notes_import as NI
    assert NI.folder_tags(U("obsidian/Tutorials/NestJs/3 - Setup Env.md")) == ["nestjs", "tutorial"]
    assert NI.folder_tags(U("obsidian/BrightLane/Feedback/Teammate.md")) == ["brightlane", "feedback"]
    assert NI.folder_tags(U("obsidian/Hermes Agent OS/Prompt 10.md")) == ["hermes-agent-os"]
    # 'Other' is filing mechanics, not a topic; 'Server Setup' is the topic.
    assert NI.folder_tags(U("obsidian/Tutorials/Other/Server Setup/SSH Setup.md")) \
        == ["server-setup", "tutorial"]


def test_folder_tags_never_reintroduces_an_origin_tag():
    """The 2026-09-08 decision: no note is tagged with the dump it came from.
    The source root is the FIRST segment and is dropped before anything else."""
    from backend import notes_import as NI
    for ident in ("obsidian/Tutorials/NestJs/1 - Initialize.md",
                  "keep/keep-0007.txt",
                  "focus/Tasks/Work/Something.md",
                  "apple-notes/Notes/Trip.md",
                  "old-hermes/notes.md"):
        tags = NI.folder_tags(U(ident))
        assert not ({"obsidian", "keep", "focus", "apple-notes",
                     "old-hermes"} & set(tags)), (ident, tags)


def test_folder_tags_drops_containers_and_area_names():
    from backend import notes_import as NI
    # 'Tasks' is a container; 'Work' is an AREA, so the area already says it.
    assert NI.folder_tags(U("focus/Tasks/Work/Thing.md"),
                          area_names=["work", "career"]) == []
    # Without the area list the segment survives — the caller decides.
    assert NI.folder_tags(U("focus/Tasks/Work/Thing.md")) == ["work"]
    # Archive is filing mechanics, never a topic.
    assert NI.folder_tags(U("obsidian/Archive/Tutorials/NestJs/1 - Initialize.md")) \
        == ["nestjs", "tutorial"]


def test_a_file_at_the_source_root_gets_no_tags():
    from backend import notes_import as NI
    assert NI.folder_tags(U("obsidian/Prompt.md")) == []


# ---- rename_tag ------------------------------------------------------------

def _note(store, db, title, tags):
    return store.create_note(title, body="b", status="inbox", tags=tags, db_path=db)


def test_rename_tag_rewrites_notes_and_taxonomy(env):
    store, db = env
    a = _note(store, db, "one", ["BrightLane", "docker"])
    changed = store.rename_tag("BrightLane", "brightlane", db_path=db)
    assert changed == 1
    assert store.get_note(a, db_path=db)["tags"] == ["brightlane", "docker"]
    tags = {t["tag"] for t in store.list_note_tags(db_path=db)}
    assert "BrightLane" not in tags and "brightlane" in tags


def test_rename_MERGES_when_the_target_already_exists(env):
    """The real case: a note carrying BOTH spellings must end with one tag,
    not a duplicate. This is why it cannot be a plain UPDATE."""
    store, db = env
    a = _note(store, db, "both", ["BrightLane", "brightlane", "docker"])
    store.rename_tag("BrightLane", "brightlane", db_path=db)
    assert store.get_note(a, db_path=db)["tags"] == ["brightlane", "docker"]


def test_rename_tag_refuses_unknown_and_no_op(env):
    store, db = env
    _note(store, db, "x", ["docker"])
    with pytest.raises(ValueError, match="no such tag"):
        store.rename_tag("nope", "other", db_path=db)
    with pytest.raises(ValueError, match="the same"):
        store.rename_tag("docker", "docker", db_path=db)


def test_rename_tag_writes_an_audit_row(env):
    import sqlite3
    store, db = env
    _note(store, db, "x", ["docker"])
    store.rename_tag("docker", "containers", db_path=db)
    conn = sqlite3.connect(db)
    rows = [r[0] for r in conn.execute(
        "SELECT detail FROM activity WHERE action='note_tag_renamed'")]
    conn.close()
    assert rows and "docker -> containers" in rows[0]


# ---- retire_tag ------------------------------------------------------------

def test_retire_tag_removes_it_everywhere_but_keeps_the_notes(env):
    store, db = env
    a = _note(store, db, "fixture", ["zzz-test", "docker"])
    store.retire_tag("zzz-test", db_path=db)
    assert store.get_note(a, db_path=db)["tags"] == ["docker"]
    assert store.get_note(a, db_path=db)["id"] == a          # the note survives
    assert "zzz-test" not in {t["tag"] for t in store.list_note_tags(db_path=db)}


# ---- add_note_tags ---------------------------------------------------------

def test_add_note_tags_merges_without_bumping_updated_at(env):
    """A backfill is not the owner editing his notes: bumping the stamp would
    reorder every list in the Library."""
    store, db = env
    a = _note(store, db, "n", ["docker"])
    before = store.get_note(a, db_path=db)["updated_at"]
    time.sleep(0.01)
    out = store.add_note_tags(a, ["nestjs", "docker"], db_path=db)
    after = store.get_note(a, db_path=db)
    assert out == ["docker", "nestjs"]
    assert after["tags"] == ["docker", "nestjs"]
    assert after["updated_at"] == before
    # and it registers in the closed taxonomy so agents may use it
    assert "nestjs" in {t["tag"] for t in store.list_note_tags(db_path=db)}


def test_add_note_tags_rejects_empty_and_unknown_note(env):
    store, db = env
    a = _note(store, db, "n", ["docker"])
    with pytest.raises(ValueError, match="no tags given"):
        store.add_note_tags(a, [], db_path=db)
    with pytest.raises(ValueError, match="no such note"):
        store.add_note_tags(999999, ["x"], db_path=db)
