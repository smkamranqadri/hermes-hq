"""Second Brain Phase 3 W0b: dump parsing + pre-pass classification.

The Phase 3 DoD reconciles the import against this analysis, so these tests pin
the parsing rules against synthetic dumps (they run anywhere, with no /opt data
present) and then check the committed baseline against the real dumps when this
box has them.

The properties that matter: every unit gets exactly one decision so the totals
always reconcile; only provably-lossless buckets DROP anything; and the winner
among identical units never depends on filesystem walk order.
"""
import json
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest

from backend import notes_import as ni

REAL_DUMP = "/opt/data/notes-dump"
BASELINE = os.path.join(ROOT, "tools", "notes_dump_baseline.json")


def write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(text)


# ---- normalization ------------------------------------------------------

def test_entities_are_unescaped_before_hashing():
    """78 real files carry 2,338 HTML entities. An escaped copy must dedupe
    against its unescaped twin, which only works if unescape precedes hashing."""
    a = ni.Unit("s", "a", "t", "Tom &amp; Jerry said &quot;hi&quot;")
    b = ni.Unit("s", "b", "t", 'Tom & Jerry said "hi"')
    assert a.norm == b.norm
    assert a.sha256 == b.sha256


def test_bom_and_crlf_do_not_create_false_uniques():
    a = ni.Unit("s", "a", "t", "﻿line one\r\nline two")
    b = ni.Unit("s", "b", "t", "line one\nline two")
    assert a.sha256 == b.sha256


def test_clean_preserves_line_structure_but_normalize_does_not():
    body = ni.clean("a &amp; b\n\nc")
    assert body == "a & b\n\nc"                     # stored form keeps lines
    assert ni.normalize(body) == "a & b c"          # comparison form collapses


# ---- per-source parsing -------------------------------------------------

def test_keep_splits_on_four_blank_lines_only(tmp_path):
    """Five newlines. Three blank lines inside one note must NOT split it."""
    body = "note one\n\n\nstill note one" + "\n" * 5 + "note two"
    write(str(tmp_path / "keep" / "Google Keep Document.txt"), "﻿" + body)
    units = ni.read_source(str(tmp_path), "keep")
    assert len(units) == 2
    assert units[0].body == "note one\n\n\nstill note one"
    assert units[1].body == "note two"
    assert units[0].ident.endswith("#0") and units[1].ident.endswith("#1")


def test_walker_finds_dot_named_files_that_glob_skips(tmp_path):
    """apple-notes really contains `..md`, `.md` and `. (2).md`; glob('**/*.md')
    silently skips every one of them."""
    import glob
    d = str(tmp_path / "apple-notes" / "Notes")
    for name in ("..md", ".md", ". (2).md", "ordinary.md"):
        write(os.path.join(d, name), "body of " + name)
    assert len(glob.glob(str(tmp_path / "apple-notes" / "**" / "*.md"),
                         recursive=True)) == 1        # what the naive way sees
    units = ni.read_source(str(tmp_path), "apple-notes")
    assert len(units) == 4                            # what the walker sees


def test_frontmatter_is_parsed_and_stripped_from_the_body(tmp_path):
    write(str(tmp_path / "focus" / "Tasks" / "Home" / "Pay bill.md"),
          '---\ntitle: "Pay the electricity bill"\ntype: task\ndomain: Home\n'
          'status: open\nfocus_id: F8595E15\n---\n\n# Pay the bill\n\n- [ ] do it\n')
    u = ni.read_source(str(tmp_path), "focus")[0]
    assert u.meta["type"] == "task" and u.meta["domain"] == "Home"
    assert u.title == "Pay the electricity bill"     # frontmatter title wins
    assert "---" not in u.body and "focus_id" not in u.body


def test_repeated_titles_with_distinct_ids_stay_separate_units(tmp_path):
    """focus's three `Multi-branch setup` files carry three DISTINCT ids
    and different created dates — they are separate records. Title similarity
    must never collapse them (128 same-title groups exist across the dumps)."""
    base = str(tmp_path / "focus" / "Tasks" / "Home")
    for n, kid in ((None, "4C08C2B2"), ("(2)", "0FD3951D"), ("(3)", "4E52A02D")):
        name = "Multi-branch setup%s.md" % ((" " + n) if n else "")
        write(os.path.join(base, name),
              "---\ntitle: Multi-branch setup\nfocus_id: %s\n---\n\nbody %s\n"
              % (kid, kid))
    units = ni.classify(ni.read_source(str(tmp_path), "focus"))
    assert len(units) == 3
    assert all(u.decision == ni.LIBRARIAN for u in units)
    assert len({u.meta["focus_id"] for u in units}) == 3


def test_apple_attachment_dirs_bind_to_their_sibling_note(tmp_path):
    d = str(tmp_path / "apple-notes" / "Circadia")
    write(os.path.join(d, "Optimizer.md"), "the optimizer note")
    os.makedirs(os.path.join(d, "Optimizer (Attachments)"))
    for img in ("a.jpg", "b.png"):
        open(os.path.join(d, "Optimizer (Attachments)", img), "wb").write(b"\x00")
    units = ni.read_source(str(tmp_path), "apple-notes")
    assert len(units) == 1                            # the dir is not a unit
    assert len(units[0].meta["attachments"]) == 2


# ---- pre-pass -----------------------------------------------------------

def test_every_unit_gets_exactly_one_decision_and_totals_reconcile(tmp_path):
    write(str(tmp_path / "keep" / "k.txt"),
          "https://example.com/only" + "\n" * 5 + "tiny" + "\n" * 5
          + "a real note with enough words to need actual judgment from a model")
    write(str(tmp_path / "obsidian" / "Notes" / "Live.md"), "shared content")
    write(str(tmp_path / "obsidian" / "Archive" / "Live.md"), "shared content")
    write(str(tmp_path / "apple-notes" / "Notes" / "blank.md"), "   \n\n  ")
    rep = ni.analyze(str(tmp_path))
    assert rep["reconciles"]
    assert rep["totals"]["total"] == 6            # 3 keep + 2 obsidian + 1 apple
    assert all(u.decision in ni.DECISIONS for u in rep["units"])
    # "exactly one" is the property: the buckets partition the units
    assert sum(rep["totals"][d] for d in ni.DECISIONS) == 6


def test_secrets_quarantine_and_beat_every_other_rule(tmp_path):
    """A key-bearing unit must never be routed elsewhere — even when it is also
    a duplicate, which would otherwise silently drop one copy of it."""
    key = "AKIA" + "A" * 16
    write(str(tmp_path / "obsidian" / "Notes" / "one.md"), "creds %s here" % key)
    write(str(tmp_path / "obsidian" / "Notes" / "two.md"), "creds %s here" % key)
    rep = ni.analyze(str(tmp_path))
    assert rep["totals"][ni.QUARANTINE] == 2          # BOTH, not one + a dup
    assert rep["totals"][ni.DUPLICATE] == 0
    assert all(q["reason"] == "matched aws_access_key_id" for q in rep["quarantined"])


def test_secret_keywords_alone_do_not_quarantine(tmp_path):
    """91 real files match password/token/api-key words and only 4 carry real
    material. Quarantining on keywords would satisfy the DoD by destroying the
    library."""
    write(str(tmp_path / "obsidian" / "Notes" / "n.md"),
          "Remember to rotate the password on the router before the trip")
    rep = ni.analyze(str(tmp_path))
    assert rep["totals"][ni.QUARANTINE] == 0
    assert rep["totals"][ni.LIBRARIAN] == 1
    assert rep["flagged_secret_keywords"] == 1        # flagged, not removed


def test_only_empty_and_duplicate_ever_drop_anything(tmp_path):
    """The safety property of the whole pre-pass: a unit with unique text is
    always kept — archived or read, never discarded."""
    write(str(tmp_path / "keep" / "k.txt"),
          "tiny" + "\n" * 5 + "a genuine note with plenty of words to judge")
    write(str(tmp_path / "obsidian" / "Notes" / "Live.md"), "live version")
    write(str(tmp_path / "obsidian" / "Archive" / "Live.md"), "OLDER DRAFT, different")
    write(str(tmp_path / "obsidian" / "Notes" / "empty.md"), "  ")
    rep = ni.analyze(str(tmp_path))
    kept = [u for u in rep["units"]
            if u.decision in (ni.AUTO_ARCHIVE, ni.LIBRARIAN, ni.QUARANTINE)]
    dropped = [u for u in rep["units"] if u.decision in (ni.EMPTY, ni.DUPLICATE)]
    # the differing Archive draft is KEPT (archived), not discarded as a clone
    draft = next(u for u in rep["units"] if "Archive" in u.ident)
    assert draft.decision == ni.AUTO_ARCHIVE
    assert "superseded" in draft.reason
    assert {u.norm for u in dropped} <= {""} | {u.norm for u in kept}


def test_live_copy_always_beats_the_archive_copy_regardless_of_walk_order(tmp_path):
    """`obsidian/Archive` sorts BEFORE `obsidian/Notes`, so a walk-order-driven
    keeper rule silently keeps the Archive copy and drops the live one."""
    write(str(tmp_path / "obsidian" / "Notes" / "Same.md"), "identical text")
    write(str(tmp_path / "obsidian" / "Archive" / "Same.md"), "identical text")
    rep = ni.analyze(str(tmp_path))
    kept = [u for u in rep["units"] if u.decision == ni.LIBRARIAN]
    dropped = [u for u in rep["units"] if u.decision == ni.DUPLICATE]
    assert len(kept) == 1 and len(dropped) == 1
    assert "Archive" not in kept[0].ident            # the LIVE one survived
    assert "Archive" in dropped[0].ident
    assert dropped[0].ref == kept[0].ident           # traceable to its keeper


def test_keep_auto_archive_rule_spares_a_real_reading_list(tmp_path):
    """A bare link or a scrap archives unread; a titled list of links is a real
    note and still gets judged."""
    write(str(tmp_path / "keep" / "k.txt"), "\n\n\n\n\n".join([
        "https://example.com/a",                       # bare link -> archive
        "Fifalwet",                                    # scrap -> archive
        "Read Later\nhttps://example.com/x\nhttps://example.com/y",   # real note
    ]))
    rep = ni.analyze(str(tmp_path))
    by_title = {u.title: u.decision for u in rep["units"]}
    assert by_title["https://example.com/a"] == ni.AUTO_ARCHIVE
    assert by_title["Fifalwet"] == ni.AUTO_ARCHIVE
    assert by_title["Read Later"] == ni.LIBRARIAN


def test_classification_is_deterministic_across_runs(tmp_path):
    write(str(tmp_path / "obsidian" / "Notes" / "a.md"), "alpha content here")
    write(str(tmp_path / "obsidian" / "Archive" / "a.md"), "alpha content here")
    write(str(tmp_path / "apple-notes" / "Notes" / "b.md"), "beta content here")
    first = [(u.ident, u.decision) for u in ni.analyze(str(tmp_path))["units"]]
    second = [(u.ident, u.decision) for u in ni.analyze(str(tmp_path))["units"]]
    assert first == second


def test_analyze_on_a_missing_source_is_empty_not_an_error(tmp_path):
    rep = ni.analyze(str(tmp_path))
    assert rep["reconciles"] and rep["totals"]["total"] == 0


# ---- the committed baseline (real dumps) --------------------------------

@pytest.mark.skipif(not os.path.isdir(REAL_DUMP), reason="no notes dump on this box")
def test_real_dumps_match_the_committed_baseline():
    """This is the Phase 3 DoD's reconciliation check. If the dumps or the
    parsing rules change, this fails and the baseline must be re-recorded
    deliberately (`tools/analyze_notes_dump.py --write-fixture`)."""
    recorded = json.load(open(BASELINE))
    assert ni.fixture_of(ni.analyze(REAL_DUMP)) == recorded


@pytest.mark.skipif(not os.path.isdir(REAL_DUMP), reason="no notes dump on this box")
def test_real_dumps_reconcile_with_nothing_unaccounted():
    rep = ni.analyze(REAL_DUMP)
    assert rep["reconciles"]
    assert rep["accounted"] == rep["totals"]["total"] == 969
    # the four known secrets are held back, and none of them is merely a keyword
    assert rep["totals"][ni.QUARANTINE] == 4
    assert rep["flagged_secret_keywords"] > rep["totals"][ni.QUARANTINE]


# ---- photo-only notes and hostile attachment names ----------------------

def test_a_photo_only_note_is_not_empty(tmp_path):
    """An Apple note holding only a picture has an EMPTY markdown body. Judging
    emptiness on text alone discarded 46 real notes and 48 photos with them."""
    d = str(tmp_path / "apple-notes" / "Trip")
    write(os.path.join(d, "Beach.md"), "")
    os.makedirs(os.path.join(d, "Beach (Attachments)"))
    open(os.path.join(d, "Beach (Attachments)", "IMG_1.jpg"), "wb").write(b"\xff\xd8")
    rep = ni.analyze(str(tmp_path))
    u = rep["units"][0]
    assert u.decision == ni.LIBRARIAN          # kept, not dropped
    assert len(u.meta["attachments"]) == 1


def test_two_photo_only_notes_do_not_dedupe_into_one(tmp_path):
    """Both bodies are empty and both titles could repeat — identity comes from
    the attachments, so they must be part of the digest."""
    d = str(tmp_path / "apple-notes" / "Trip")
    for name, img in (("A", "one.jpg"), ("B", "two.jpg")):
        write(os.path.join(d, name + ".md"), "")
        os.makedirs(os.path.join(d, name + " (Attachments)"))
        open(os.path.join(d, name + " (Attachments)", img), "wb").write(b"\xff\xd8")
    rep = ni.analyze(str(tmp_path))
    assert rep["totals"][ni.DUPLICATE] == 0
    assert rep["totals"][ni.LIBRARIAN] == 2


def test_attachment_dirs_bind_to_dot_named_notes(tmp_path):
    """`.md` and `..md` really exist in the dump, and their attachment dirs are
    ` (Attachments)` and `. (Attachments)`. os.path.splitext('.md') returns
    ('.md', '') and normpath collapses '.', so a path-string key either misses
    them or collides the two together."""
    d = str(tmp_path / "apple-notes" / "Notes")
    write(os.path.join(d, ".md"), "note one")
    write(os.path.join(d, "..md"), "note two")
    for dirname, img in ((" (Attachments)", "a.jpg"), (". (Attachments)", "b.jpg")):
        os.makedirs(os.path.join(d, dirname))
        open(os.path.join(d, dirname, img), "wb").write(b"\xff\xd8")
    units = {u.ident: u for u in ni.read_source(str(tmp_path), "apple-notes")}
    a = units["apple-notes/Notes/.md"].meta.get("attachments") or []
    b = units["apple-notes/Notes/..md"].meta.get("attachments") or []
    assert [os.path.basename(p) for p in a] == ["a.jpg"]
    assert [os.path.basename(p) for p in b] == ["b.jpg"]


def test_two_notes_sharing_a_body_but_not_a_title_are_both_kept(tmp_path):
    """Real case: obsidian's `Prompt 5A`, `Prompt 9` and `Prompt 9B` share a
    body. Hashing the body alone silently collapsed two of the three."""
    d = str(tmp_path / "obsidian" / "Notes")
    for name in ("Prompt 5A", "Prompt 9", "Prompt 9B"):
        write(os.path.join(d, name + ".md"), "the same shared body text")
    rep = ni.analyze(str(tmp_path))
    assert rep["totals"][ni.DUPLICATE] == 0
    assert rep["totals"][ni.LIBRARIAN] == 3


@pytest.mark.skipif(not os.path.isdir(REAL_DUMP), reason="no notes dump on this box")
def test_every_real_attachment_binds_to_a_note():
    """79 images live in (Attachments) dirs; all 79 must reach a note, or the
    import silently loses photos."""
    units = ni.read_source(REAL_DUMP, "apple-notes")
    bound = sum(len(u.meta.get("attachments") or []) for u in units)
    on_disk = sum(1 for r, _, fs in os.walk(os.path.join(REAL_DUMP, "apple-notes"))
                  if "(Attachments)" in r
                  for f in fs if not f.startswith("."))
    assert bound == on_disk == 79
