"""Library polish G: the focus Task/Note MIRROR pair.

The source app creates-and-links — one item can exist as `Tasks/X.md` (whose
body is the `- [ ]` checkbox line) and as `Notes/X.md` (the free text). Their
bodies differ, so the digest correctly keeps both, and importing both is what
produced the duplicate-title pairs the owner found in the library.

Owner decision (2026-09-08, final): `source:` frontmatter + the same title
means the task MIRRORS the note. Its text becomes a Thought ON that note,
verbatim; it is not a second note. These pin the rule, the apply ordering, and
the two halves of its idempotency — including that re-running wave 1, imported
BEFORE this rule existed, still writes nothing.
"""
import json
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest

from backend import notes_import as ni

REAL_DUMP = "/opt/data/notes-dump"
BASELINE = os.path.join(ROOT, "tools", "notes_dump_baseline.json")

FAKE_KEY = "AKIA" + "Q" * 16


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def kf(dump, kind, title, body, domain=None, status=None, source=None,
       sub=None, ntype=None):
    """One focus file. `kind` is the tree it lives in ('Tasks'/'Notes'),
    `ntype` its `type:` frontmatter (defaults to the tree's singular)."""
    fm = ["---", "title: %s" % title,
          "type: %s" % (ntype or {"Tasks": "task", "Notes": "note"}[kind])]
    for key, val in (("domain", domain), ("status", status), ("source", source)):
        if val:
            fm.append("%s: %s" % (key, val))
    # a distinct per-record id, as the real dump has: without it the three
    # byte-identical Multi-branch mirrors would dedupe instead of pairing
    fm.append("focus_id: %s-%s-%s" % (kind, sub or "", title))
    fm.append("---")
    parts = [dump, "focus", kind] + ([sub] if sub else []) + [title + ".md"]
    write(os.path.join(*parts), "\n".join(fm) + "\n\n" + body + "\n")


def plans_by_ident(dump):
    units = ni.classify(ni.read_source(dump, "focus"))
    return {p.unit.ident: p for p in ni.plan_all(units)}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    from backend import notes_import as fresh
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    yield store, fresh, store.DEFAULT_DB_PATH, tmp_path


def entries_of(store, db, title):
    n = next(x for x in store.list_notes(limit=500, db_path=db)
             if x["title"] == title)
    return [e["body"] for e in store.get_note(n["id"], db_path=db)["entries"]]


# ---- the pure rule ------------------------------------------------------

def test_task_with_source_and_a_matching_note_is_planned_as_an_append(tmp_path):
    dump = str(tmp_path / "dump")
    kf(dump, "Notes", "IT inventory list", "switch, 3 laptops", domain="Work")
    kf(dump, "Tasks", "IT inventory list", "- [ ] IT inventory list",
       domain="Work", status="open", source="text")

    plans = plans_by_ident(dump)
    task = plans["focus/Tasks/IT inventory list.md"]
    note = plans["focus/Notes/IT inventory list.md"]
    assert task.action == ni.APPEND
    assert task.target.ident == "focus/Notes/IT inventory list.md"
    assert note.action == ni.CREATE          # the note is planned as ever


def test_same_domain_note_wins_over_a_note_of_another_domain(tmp_path):
    """Two notes can carry one title. The task names its domain, so the pair is
    not a coin flip — and the losing candidate is recorded, not swallowed."""
    dump = str(tmp_path / "dump")
    kf(dump, "Notes", "Q1", "the work plan", domain="Work", sub="a")
    kf(dump, "Notes", "Q1", "the home plan", domain="Home", sub="b")
    kf(dump, "Tasks", "Q1", "- [ ] Q1", domain="Home", status="open", source="note")

    task = plans_by_ident(dump)["focus/Tasks/Q1.md"]
    assert task.action == ni.APPEND
    assert task.target.meta["domain"] == "Home"
    assert len(task.unit.meta["pair_ambiguous"]) == 2


def test_ambiguous_pair_with_no_domain_match_takes_the_first_by_ident(tmp_path):
    dump = str(tmp_path / "dump")
    kf(dump, "Notes", "Q1", "b plan", domain="Work", sub="b")
    kf(dump, "Notes", "Q1", "a plan", domain="Content", sub="a")
    kf(dump, "Tasks", "Q1", "- [ ] Q1", domain="Health", status="open", source="note")

    task = plans_by_ident(dump)["focus/Tasks/Q1.md"]
    assert task.target.ident == "focus/Notes/a/Q1.md"     # sorted, not walked


def test_task_without_source_frontmatter_is_never_paired(tmp_path):
    """`source:` is the app's own record that the task was created FROM a note.
    Without it a same-titled task is its own thing and stays its own note."""
    dump = str(tmp_path / "dump")
    kf(dump, "Notes", "Meeting", "what was said", domain="Work")
    kf(dump, "Tasks", "Meeting", "- [ ] Meeting", domain="Work", status="open")

    task = plans_by_ident(dump)["focus/Tasks/Meeting.md"]
    assert task.action == ni.CREATE and task.target is None


def test_task_with_source_but_no_matching_note_stays_a_note(tmp_path):
    dump = str(tmp_path / "dump")
    kf(dump, "Notes", "Something else", "unrelated", domain="Work")
    kf(dump, "Tasks", "Review merged PR", "- [ ] Review merged PR",
       domain="Work", status="open", source="text")

    task = plans_by_ident(dump)["focus/Tasks/Review merged PR.md"]
    assert task.action == ni.CREATE
    assert task.status == "inbox"            # no areas passed to plan_all here


def test_pairs_never_cross_sources(tmp_path):
    """The analyzer reads every dump at once and the importer reads one. If a
    pair could span sources the committed baseline would describe an import
    that can never happen."""
    dump = str(tmp_path / "dump")
    kf(dump, "Tasks", "IT inventory list", "- [ ] IT inventory list",
       domain="Work", status="open", source="text")
    write(os.path.join(dump, "obsidian", "IT inventory list.md"),
          "---\ntype: note\ntitle: IT inventory list\n---\n\nsomething")

    units = ni.classify(ni.read_all(dump))
    assert [p.action for p in ni.plan_all(units) if p.action == ni.APPEND] == []


def test_a_quarantined_mirror_is_held_not_appended(tmp_path):
    """Precedence: a key-bearing unit must never be routed anywhere else — and
    an entry has no delete path anywhere, so an appended secret is forever."""
    dump = str(tmp_path / "dump")
    kf(dump, "Notes", "Deploy", "how to deploy", domain="Work")
    kf(dump, "Tasks", "Deploy", "- [ ] Deploy with %s" % FAKE_KEY,
       domain="Work", status="open", source="note")

    task = plans_by_ident(dump)["focus/Tasks/Deploy.md"]
    assert task.action == ni.HOLD


def test_plan_summary_counts_appends(tmp_path):
    dump = str(tmp_path / "dump")
    kf(dump, "Notes", "Pair", "the text", domain="Work")
    kf(dump, "Tasks", "Pair", "- [ ] Pair", domain="Work", status="open", source="note")
    kf(dump, "Tasks", "Solo", "- [ ] Solo", domain="Work", status="open")

    summary = ni.plan_summary(list(plans_by_ident(dump).values()))
    assert summary["append"] == 1
    assert summary["create_total"] == 2


# ---- apply --------------------------------------------------------------

def test_apply_appends_the_task_verbatim_and_creates_no_extra_note(env):
    store, imp, db, tmp = env
    dump = str(tmp / "dump")
    kf(dump, "Notes", "IT inventory list", "switch\n3 laptops", domain="Work")
    kf(dump, "Tasks", "IT inventory list", "- [ ] IT inventory list \U0001F4C5 2026-07-22",
       domain="Work", status="open", source="text")

    res = imp.import_source(dump, "focus", store, db_path=db, dry_run=False)

    notes = store.list_notes(limit=500, db_path=db)
    assert len(notes) == 1                     # the note only, no mirror twin
    assert len(res["appended"]) == 1
    # VERBATIM — note_entries has no author column, so a reworded Thought would
    # be indistinguishable from the owner's own words forever after.
    assert entries_of(store, db, "IT inventory list") == \
        ["- [ ] IT inventory list \U0001F4C5 2026-07-22"]


def test_the_note_is_created_before_its_mirror_regardless_of_walk_order(env):
    """`Tasks/` sorts before `Notes/`... no: it sorts AFTER, which is exactly
    why the ordering must be explicit rather than lucky. Two passes: every
    CREATE lands before any APPEND."""
    store, imp, db, tmp = env
    dump = str(tmp / "dump")
    # 'AAA' forces the task to be walked first in both trees' sort order
    kf(dump, "Tasks", "AAA pair", "- [ ] AAA pair", domain="Work",
       status="open", source="note", sub="0")
    kf(dump, "Notes", "AAA pair", "the real text", domain="Work", sub="zzz")

    res = imp.import_source(dump, "focus", store, db_path=db, dry_run=False)
    assert res["errors"] == [] and res["append_fallback"] == []
    assert len(store.list_notes(limit=500, db_path=db)) == 1
    assert entries_of(store, db, "AAA pair") == ["- [ ] AAA pair"]


def test_a_done_mirror_still_appends_and_leaves_the_note_status_alone(env):
    """`- [x]` is information the owner wants kept. It is a Thought about the
    note, not a verdict on it: the note's own status must not move."""
    store, imp, db, tmp = env
    dump = str(tmp / "dump")
    kf(dump, "Notes", "IT inventory list", "switch, 3 laptops", domain="Work")
    kf(dump, "Tasks", "IT inventory list", "- [x] IT inventory list",
       domain="Work", status="done", source="text")

    imp.import_source(dump, "focus", store, db_path=db, dry_run=False)
    notes = store.list_notes(limit=500, db_path=db)
    assert len(notes) == 1
    assert notes[0]["status"] == "active"            # filed by its domain, NOT archived
    assert "- [x] IT inventory list" in entries_of(store, db, "IT inventory list")[0]


def test_rerunning_appends_nothing_and_creates_nothing(env):
    """note_entries has no content_hash column and no delete path, so the
    guard is the entries themselves: a body already there is not re-appended."""
    store, imp, db, tmp = env
    dump = str(tmp / "dump")
    kf(dump, "Notes", "Pair", "the real text", domain="Work")
    kf(dump, "Tasks", "Pair", "- [ ] Pair", domain="Work", status="open", source="note")

    imp.import_source(dump, "focus", store, db_path=db, dry_run=False)
    second = imp.import_source(dump, "focus", store, db_path=db, dry_run=False)

    assert second["created"] == [] and second["appended"] == []
    assert second["already_imported"] == 1            # the note, by content_hash
    assert second["appended_already"] == 1            # the Thought, by body
    assert len(store.list_notes(limit=500, db_path=db)) == 1
    assert len(entries_of(store, db, "Pair")) == 1


def test_byte_identical_mirrors_collapse_into_one_thought(env):
    """The three real `Multi-branch setup for online payments` task files differ
    only by their source id, so they are three units — but one Thought."""
    store, imp, db, tmp = env
    dump = str(tmp / "dump")
    kf(dump, "Notes", "Multi-branch", "multi branch setup", domain="Home")
    for sub in ("a", "b", "c"):
        kf(dump, "Tasks", "Multi-branch", "- [ ] Multi-branch", domain="Home",
           status="open", source="note", sub=sub)

    res = imp.import_source(dump, "focus", store, db_path=db, dry_run=False)
    assert res["planned"]["append"] == 3
    assert len(res["appended"]) == 1 and res["appended_already"] == 2
    assert len(entries_of(store, db, "Multi-branch")) == 1


def test_a_unit_already_imported_as_its_own_note_is_left_alone(env):
    """Wave 1 imported these tasks as notes BEFORE this rule existed. Re-running
    it must stay a no-op: the importer never retroactively merges live notes —
    that is a librarian task."""
    store, imp, db, tmp = env
    dump = str(tmp / "dump")
    kf(dump, "Tasks", "Pair", "- [ ] Pair", domain="Work", status="open", source="note")
    wave1 = imp.import_source(dump, "focus", store, db_path=db, dry_run=False)
    assert len(wave1["created"]) == 1                # no note existed yet to pair with

    kf(dump, "Notes", "Pair", "the real text", domain="Work")   # the note arrives later
    wave2 = imp.import_source(dump, "focus", store, db_path=db, dry_run=False)

    assert wave2["appended"] == [] and wave2["already_imported"] == 1
    titles = sorted(n["title"] for n in store.list_notes(limit=500, db_path=db))
    assert titles == ["Pair", "Pair"]                # untouched; the librarian merges
    assert entries_of(store, db, "Pair") == []


def test_a_held_target_makes_the_mirror_its_own_note_with_the_reason_recorded(env):
    """The target carries a key and is quarantined, so there is no note to
    append to. Losing the task's text would be a silent deletion."""
    store, imp, db, tmp = env
    dump = str(tmp / "dump")
    kf(dump, "Notes", "Deploy", "the key is %s" % FAKE_KEY, domain="Work")
    kf(dump, "Tasks", "Deploy", "- [ ] Deploy", domain="Work",
       status="open", source="note")

    res = imp.import_source(dump, "focus", store, db_path=db, dry_run=False)

    assert res["appended"] == []
    assert len(res["append_fallback"]) == 1
    assert "focus/Notes/Deploy.md" in res["append_fallback"][0]
    notes = store.list_notes(limit=500, db_path=db)
    assert [n["title"] for n in notes] == ["Deploy"]          # the mirror survived
    assert notes[0]["status"] == "active"                     # filed by its own domain
    assert notes[0]["body"] == "- [ ] Deploy"
    assert len(store.list_quarantine(db_path=db)) == 1


def test_a_dry_run_previews_the_append_and_writes_nothing(env):
    store, imp, db, tmp = env
    dump = str(tmp / "dump")
    kf(dump, "Notes", "Pair", "the real text", domain="Work")
    kf(dump, "Tasks", "Pair", "- [ ] Pair", domain="Work", status="open", source="note")

    res = imp.import_source(dump, "focus", store, db_path=db, dry_run=True)
    assert res["planned"]["append"] == 1
    assert len(res["appended"]) == 1
    assert store.list_notes(limit=500, db_path=db) == []


# ---- the committed baseline (real dumps) --------------------------------

@pytest.mark.skipif(not os.path.isdir(REAL_DUMP), reason="no notes dump on this box")
def test_the_real_dump_pairs_are_counted_in_the_committed_baseline():
    """The DoD baseline is produced by the code that runs, so the pair count is
    part of it: change the rule and `--check` fails until it is re-recorded."""
    recorded = json.load(open(BASELINE))
    report = ni.analyze(REAL_DUMP)
    assert ni.fixture_of(report) == recorded
    assert recorded["per_source"]["focus"]["append"] == 8
    assert recorded["totals"]["append"] == 8
    # every one of them is a focus mirror; no other dump has the shape
    assert sum(r["append"] for s, r in recorded["per_source"].items()
               if s != "focus") == 0
    # and 'append' is a SUBSET of the kept units, never a sixth decision
    t = recorded["totals"]
    assert sum(t[d] for d in ni.DECISIONS) == t["total"] == 969


@pytest.mark.skipif(not os.path.isdir(REAL_DUMP), reason="no notes dump on this box")
def test_the_real_dump_pairs_are_the_eight_known_mirrors():
    units = ni.classify(ni.read_source(REAL_DUMP, "focus"))
    pairs = [(p.unit.ident, p.target.ident)
             for p in ni.plan_all(units) if p.action == ni.APPEND]
    assert len(pairs) == 8
    assert sorted(set(t for _, t in pairs)) == [
        "focus/Notes/AI Workflow/Little Coder/"
        "Testing Little Coder with Ollama Qwen.md",
        "focus/Notes/Career/Amna's long-term career language goal.md",
        "focus/Notes/Unfiled/Multi-branch setup for online payments.md",
        "focus/Notes/Unfiled/Test Urdu voice note in Hermes.md",
        "focus/Notes/Work/Hermes Agent/"
        "Social media content manager dashboard using Hermes.md",
        "focus/Notes/Work/IT inventory list.md",
    ]
    # three of the eight are the byte-identical Multi-branch records, which the
    # digest keeps apart (different focus_id) and the entry guard merges
    assert sum(1 for _, t in pairs if "Multi-branch" in t) == 3


@pytest.mark.skipif(not os.path.isdir(REAL_DUMP), reason="no notes dump on this box")
def test_the_real_dump_pairs_read_as_a_task_and_a_note_of_the_same_title():
    units = ni.classify(ni.read_source(REAL_DUMP, "focus"))
    for p in ni.plan_all(units):
        if p.action != ni.APPEND:
            continue
        assert p.unit.meta["type"] == "task" and p.unit.meta["source"]
        assert p.target.meta["type"] == "note"
        assert ni._pair_key(p.unit.title) == ni._pair_key(p.target.title)
