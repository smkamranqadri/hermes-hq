"""Second Brain Phase 3 W0c: the importer's writing half.

W0b proved the parsing and classification. These prove what actually reaches
the database: a dry run writes nothing, a real run is idempotent, secrets never
land in hq.db or in a backup, focus files itself from its frontmatter,
and the importer stays OFF the librarian's CLI surface.
"""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest

FAKE_KEY = "AKIA" + "Q" * 16


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    from backend import notes_import as ni
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    yield store, ni, store.DEFAULT_DB_PATH, tmp_path


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def kf_note(dump, name, domain=None, status=None, ntype="task", body="do the thing"):
    fm = ["---", "title: %s" % name, "type: %s" % ntype]
    if domain:
        fm.append("domain: %s" % domain)
    if status:
        fm.append("status: %s" % status)
    fm.append("---")
    write(os.path.join(dump, "focus", "Tasks", name + ".md"),
          "\n".join(fm) + "\n\n" + body + "\n")


# ---- dry run ------------------------------------------------------------

def test_dry_run_writes_absolutely_nothing(env):
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    kf_note(dump, "Pay bill", domain="Home", status="open")
    write(os.path.join(dump, "obsidian", "Notes", "leak.md"), "key %s here" % FAKE_KEY)

    res = ni.import_source(dump, "focus", store, db_path=db, dry_run=True)
    ni.import_source(dump, "obsidian", store, db_path=db, dry_run=True)

    assert res["planned"]["create_total"] == 1
    assert store.list_notes(limit=500, db_path=db) == []
    assert store.list_quarantine(db_path=db) == []
    assert not os.path.exists(store.quarantine_dir())


def test_dry_run_reports_already_imported_truthfully(env):
    """The preview must describe what a real run WOULD do, so idempotency is
    evaluated on the dry run too — otherwise it promises writes it won't make."""
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    kf_note(dump, "Pay bill", domain="Home", status="open")
    ni.import_source(dump, "focus", store, db_path=db, dry_run=False)
    again = ni.import_source(dump, "focus", store, db_path=db, dry_run=True)
    assert again["already_imported"] == 1
    assert again["created"] == []


# ---- apply --------------------------------------------------------------

def test_apply_creates_notes_with_import_provenance(env):
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    kf_note(dump, "Pay bill", domain="Home", status="open", body="pay it &amp; log it")
    ni.import_source(dump, "focus", store, db_path=db, dry_run=False)

    notes = store.list_notes(limit=500, db_path=db)
    assert len(notes) == 1
    n = store.get_note(notes[0]["id"], db_path=db)
    assert n["authored_by"] == "import"
    assert n["content_hash"]                      # reversible / dedupe handle
    assert "focus" in n["tags"]            # origin is just a tag
    assert n["body"] == "pay it & log it"         # entities resolved on the way in
    # owner/import writes ARE the taxonomy authority, so the origin tag registers
    assert "focus" in store.taxonomy_tags(db_path=db)


def test_reimport_is_a_no_op(env):
    """Re-running a wave must not duplicate. This is what makes the import
    safe to retry after a partial failure."""
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    kf_note(dump, "Pay bill", domain="Home", status="open")
    kf_note(dump, "Call bank", domain="Finance", status="open")
    first = ni.import_source(dump, "focus", store, db_path=db, dry_run=False)
    second = ni.import_source(dump, "focus", store, db_path=db, dry_run=False)
    assert len(first["created"]) == 2
    assert second["created"] == [] and second["already_imported"] == 2
    assert len(store.list_notes(limit=500, db_path=db)) == 2


def test_focus_files_itself_from_frontmatter(env):
    """Owner decision: done -> archived, open -> filed to its domain area, and
    NO HQ tasks are created from the 41 open todos."""
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    kf_note(dump, "Finished thing", domain="Work", status="done")
    kf_note(dump, "Open thing", domain="Health", status="open")
    kf_note(dump, "No domain thing", status="open")
    tasks_before = len(store.list_tasks(db_path=db))
    ni.import_source(dump, "focus", store, db_path=db, dry_run=False)

    by_title = {n["title"]: n for n in store.list_notes(limit=500, db_path=db)}
    health = next(a["id"] for a in store.list_areas(db_path=db) if a["name"] == "Health")
    assert by_title["Finished thing"]["status"] == "archived"
    assert by_title["Open thing"]["status"] == "active"
    assert by_title["Open thing"]["area_id"] == health
    # an unmappable domain is NOT guessed — it goes to the inbox for triage
    assert by_title["No domain thing"]["status"] == "inbox"
    assert len(store.list_tasks(db_path=db)) == tasks_before      # no todos minted


def test_keep_fragments_archive_and_real_notes_reach_the_inbox(env):
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    write(os.path.join(dump, "keep", "k.txt"), "\n\n\n\n\n".join([
        "https://example.com/bare",
        "a genuine captured thought with enough words to deserve triage",
    ]))
    ni.import_source(dump, "keep", store, db_path=db, dry_run=False)
    by_title = {n["title"]: n["status"]
                for n in store.list_notes(limit=500, db_path=db)}
    assert by_title["https://example.com/bare"] == "archived"
    assert by_title["a genuine captured thought with enough words to deserve triage"] \
        == "inbox"


def test_degenerate_apple_filenames_still_produce_a_title(env):
    """`..md` and `.md` derive an empty or punctuation-only title, and
    create_note refuses those — fall back to the first line of the body."""
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    write(os.path.join(dump, "apple-notes", "Notes", "..md"), "the real first line\nmore")
    write(os.path.join(dump, "apple-notes", "Notes", ".md"), "another note entirely")
    res = ni.import_source(dump, "apple-notes", store, db_path=db, dry_run=False)
    assert res["errors"] == []
    titles = {n["title"] for n in store.list_notes(limit=500, db_path=db)}
    assert titles == {"the real first line", "another note entirely"}


def test_apple_attachments_are_bound_to_their_note(env):
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    d = os.path.join(dump, "apple-notes", "Circadia")
    write(os.path.join(d, "Optimizer.md"), "the optimizer note")
    os.makedirs(os.path.join(d, "Optimizer (Attachments)"))
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    with open(os.path.join(d, "Optimizer (Attachments)", "shot.png"), "wb") as fh:
        fh.write(png)
    res = ni.import_source(dump, "apple-notes", store, db_path=db, dry_run=False)
    assert res["attachments"] == 1
    n = store.get_note(store.list_notes(limit=5, db_path=db)[0]["id"], db_path=db)
    assert len(n["attachments"]) == 1
    assert n["attachments"][0]["mime"] == "image/png"


def test_one_bad_attachment_does_not_lose_the_note(env):
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    d = os.path.join(dump, "apple-notes", "X")
    write(os.path.join(d, "Note.md"), "body text here")
    os.makedirs(os.path.join(d, "Note (Attachments)"))
    open(os.path.join(d, "Note (Attachments)", "empty.jpg"), "wb").close()   # 0 bytes
    res = ni.import_source(dump, "apple-notes", store, db_path=db, dry_run=False)
    assert len(store.list_notes(limit=500, db_path=db)) == 1     # note survived
    assert res["attachments"] == 0


# ---- secrets ------------------------------------------------------------

def test_secret_never_reaches_the_database_or_a_note(env):
    """The DoD line, checked the only way worth checking it: grep the db file."""
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    write(os.path.join(dump, "obsidian", "Notes", "creds.md"),
          "deploy notes\naws key %s\nmore text" % FAKE_KEY)
    ni.import_source(dump, "obsidian", store, db_path=db, dry_run=False)

    assert store.list_notes(limit=500, db_path=db) == []          # no note row
    rows = store.list_quarantine(db_path=db)
    assert len(rows) == 1 and rows[0]["reason"] == "matched aws_access_key_id"
    # the secret text is nowhere in hq.db — not in a note, not in the row
    with open(db, "rb") as fh:
        assert FAKE_KEY.encode() not in fh.read()
    # ...but it IS recoverable from the 0600 file, which is the point of holding it
    with open(rows[0]["stored_path"], encoding="utf-8") as fh:
        assert FAKE_KEY in fh.read()


def test_quarantine_lives_outside_hq_home_and_is_0600(env):
    """Outside hq_home so `wm backup` never sweeps key material into a backup
    that gets copied around; 0600 from creation, never create-then-chmod."""
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    write(os.path.join(dump, "obsidian", "Notes", "creds.md"), "key %s" % FAKE_KEY)
    ni.import_source(dump, "obsidian", store, db_path=db, dry_run=False)

    path = store.list_quarantine(db_path=db)[0]["stored_path"]
    hq = os.path.abspath(store.hq_home())
    assert not os.path.abspath(path).startswith(hq + os.sep)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(store.quarantine_dir()).st_mode & 0o777) == "0o700"


def test_quarantine_is_idempotent(env):
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    write(os.path.join(dump, "obsidian", "Notes", "creds.md"), "key %s" % FAKE_KEY)
    ni.import_source(dump, "obsidian", store, db_path=db, dry_run=False)
    ni.import_source(dump, "obsidian", store, db_path=db, dry_run=False)
    assert len(store.list_quarantine(db_path=db)) == 1


def test_keyword_only_files_import_normally(env):
    store, ni, db, tmp = env
    dump = str(tmp / "dump")
    write(os.path.join(dump, "obsidian", "Notes", "n.md"),
          "remember to change the router password before the trip")
    ni.import_source(dump, "obsidian", store, db_path=db, dry_run=False)
    assert len(store.list_notes(limit=500, db_path=db)) == 1
    assert store.list_quarantine(db_path=db) == []


# ---- the write wall -----------------------------------------------------

def test_importer_is_not_on_the_librarian_cli_surface(env):
    """The importer creates notes DIRECTLY, so it must not live under `wm note`
    — that group is the librarian's entire surface and stays write-free."""
    store, ni, db, tmp = env
    from core import wm_cli
    groups = wm_cli.build_parser()._subparsers._group_actions[0].choices
    note_verbs = set(groups["note"]._subparsers._group_actions[0].choices)
    assert not any("import" in v for v in note_verbs)
    assert "brain-import" in groups            # it lives out here instead
