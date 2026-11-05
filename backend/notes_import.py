"""Second Brain Phase 3 — importing the five note dumps.

Three layers, and the split is deliberate:

1. **Parse** (read-only) — turn the raw dumps into units.
2. **Classify** (read-only) — what the deterministic pre-pass can PROVE about
   each unit's content: quarantine / empty / duplicate / auto-archive, or
   "the librarian must read this". Every unit gets exactly one decision, so the
   totals always reconcile — the property the Phase 3 DoD checks.
3. **Plan + execute** — where each unit LANDS, and the writing itself. Only
   `import_source`, `_append_mirror` and `_attach` touch the database;
   everything above them is pure and testable without one. Planning is also
   where MIRROR PAIRS are resolved (`find_mirror_pairs`): a source that
   creates-and-links can hold one item twice, and the second copy becomes a
   Thought on the first rather than a near-duplicate note.

`tools/analyze_notes_dump.py` runs layers 1–2 and nothing else, which is why
the DoD's baseline cannot drift from the importer: they are the same code.

The raw dumps are treated as immutable — nothing here ever writes to them.
"""
import hashlib
import html
import json
import os
import re

# --- decisions the pre-pass can PROVE (anything else goes to the librarian) --
# Order is precedence, and QUARANTINE IS FIRST on purpose: a unit carrying key
# material must never be routed anywhere else by a later rule.
QUARANTINE = "quarantine"      # high-confidence secret: no note row, ever
EMPTY = "empty"                # nothing left after normalization
DUPLICATE = "duplicate"        # byte-identical to a unit we already kept
AUTO_ARCHIVE = "auto_archive"  # archived searchable WITHOUT a model read
LIBRARIAN = "librarian"        # the librarian reads and judges this one

# Only EMPTY and DUPLICATE are drops, and both are provable losses of nothing:
# an empty unit has no content, a duplicate's content survives in its keeper.
# Anything with unique text is kept — archived if there is no judgment left to
# make, read by the librarian otherwise.
DECISIONS = (QUARANTINE, EMPTY, DUPLICATE, AUTO_ARCHIVE, LIBRARIAN)

SOURCES = ("focus", "old-hermes", "obsidian", "apple-notes", "keep")

# Keep exports separate notes with FOUR blank lines, i.e. five newlines. Pinned
# in a constant because `\n{4,}` happens to give the same 402 on this dump and
# would silently mean something else on the next one.
KEEP_SEPARATOR = re.compile(r"\n{5,}")

# A Keep unit this small or this bare is a link stash entry, not a note: it goes
# straight to Archive (searchable, reversible) without spending a model read.
KEEP_AUTO_ARCHIVE_CHARS = 40
_URL_LINE = re.compile(r"^(https?://|www\.)\S+$")

# HIGH-CONFIDENCE secret patterns only. Keyword hits (password/token/api key)
# are NOT quarantined — 91 files match those words and only a handful carry real
# material; quarantining all of them would satisfy "zero plaintext secrets" by
# destroying the library. Keyword hits are flagged for the librarian instead.
SECRET_PATTERNS = (
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
)
SECRET_KEYWORDS = re.compile(
    r"\b(password|passwd|api[_ -]?key|secret|bearer token|access token)\b", re.I)


class Unit:
    """One importable thing. `ident` is stable across runs so a reconciliation
    row can always be traced back to the byte range it came from."""

    __slots__ = ("source", "ident", "title", "body", "norm", "mtime", "meta",
                 "decision", "reason", "ref")

    def __init__(self, source, ident, title, body, mtime=None, meta=None):
        self.source = source
        self.ident = ident
        self.title = title
        self.body = body
        self.norm = normalize(body)
        self.mtime = mtime
        self.meta = meta or {}
        self.decision = None
        self.reason = ""
        self.ref = None

    @property
    def source_id(self):
        """A stable per-record id the SOURCE itself assigned, if it has one.

        focus stamps every record with `focus_id`. Two records
        carrying different source ids are different records by definition — even
        when their title and body are byte-identical, which really happens: the
        three `Multi-branch setup for online payments` files differ ONLY by this
        id and their created date. Without it the digest merges three of the
        owner's todos into one.
        """
        for key in ("focus_id", "uuid", "guid"):
            v = (self.meta.get(key) or "").strip()
            if v:
                return v
        return ""

    @property
    def sha256(self):
        """Digest over source id + TITLE + body, not body alone.

        Two notes can share body text and still be different notes — "Pay bill"
        and "Call bank" both reading "do the thing" are two todos, and hashing
        the body alone silently collapses one into the other. Dropping a unit
        whose title differs loses information, which breaks the rule that only
        provably-lossless buckets may drop anything.
        """
        # Attachment names are part of the identity too: two photo-only notes
        # have identical (empty) bodies, and without this the second one's
        # photos would be dropped as a duplicate of the first.
        attached = ",".join(sorted(os.path.basename(p) for p in
                                   (self.meta.get("attachments") or [])))
        return hashlib.sha256(
            (self.source_id + "\n\x00\n" + normalize(self.title) + "\n\x00\n"
             + self.norm + "\n\x00\n" + attached).encode("utf-8")
        ).hexdigest()

    def __repr__(self):                                  # pragma: no cover
        return "<Unit %s %s %r>" % (self.source, self.ident, self.title[:40])


def normalize(text):
    """Collapse to comparable text for hashing and emptiness.

    Entities are unescaped BEFORE hashing: 78 files in the real dumps carry
    2,338 of them, and an escaped copy would otherwise never dedupe against its
    unescaped twin.
    """
    t = html.unescape((text or "").replace("\r\n", "\n").replace("﻿", ""))
    return re.sub(r"\s+", " ", t).strip()


def clean(text):
    """Body text as it should be stored: entities resolved, BOM and CRLF gone,
    line structure preserved (unlike `normalize`, which is for comparison)."""
    return html.unescape((text or "").replace("\r\n", "\n").replace("﻿", ""))


def title_from(body, fallback="untitled"):
    """First non-empty line, trimmed of markdown heading marks. Capture keeps
    the FULL text as the body — the first line only NAMES the note (P1 rule)."""
    for line in (body or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:300]
    return fallback


# --- frontmatter ---------------------------------------------------------

_FM = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


def split_frontmatter(text):
    """Return (meta dict, remaining body). Deliberately minimal: these dumps use
    flat `key: value` frontmatter, and pulling in a YAML dependency to read
    eight known keys would be a dependency for nothing."""
    m = _FM.match(text or "")
    if not m:
        return {}, text or ""
    meta = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        meta[k.strip()] = v
    return meta, text[m.end():]


# --- per-source readers --------------------------------------------------

def _walk_md(root, source):
    """Every .md under root. Uses os.walk, NOT glob: `glob('**/*.md')` skips
    dot-named files, and apple-notes really contains `..md`, `.md` and
    `. (2).md`. Sorted so a run is reproducible on any filesystem."""
    for dirpath, dirnames, filenames in sorted(os.walk(root)):
        dirnames.sort()
        if os.path.basename(dirpath) == ".obsidian":
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d != ".obsidian"]
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            try:
                raw = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            meta, body = split_frontmatter(raw)
            # strip the blank line frontmatter leaves behind; inner structure kept
            body = clean(body).strip("\n")
            rel = os.path.relpath(path, os.path.dirname(root))
            title = meta.get("title") or os.path.basename(name)[:-3].strip() \
                or title_from(body, fallback=rel)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = None
            yield Unit(source, rel, title, body, mtime=mtime, meta=meta)


def read_markdown_source(root, source):
    return list(_walk_md(root, source))


def read_apple_notes(root, source="apple-notes"):
    """Apple's export puts a note's images in a sibling `<title> (Attachments)`
    directory. Every one of the 67 in the real dump pairs to a `.md`, so the
    convention is reliable — but an unpaired one is recorded rather than
    silently dropped."""
    units = read_markdown_source(root, source)
    # Key by (directory, stem) TUPLES, never by a joined path string.
    # os.path.splitext('.md') returns ('.md', '') — Python treats a leading dot
    # as "no extension" — and os.path.join/normpath collapse a '.' segment, so
    # both `.md` and `..md` (which really exist here) would either miss their
    # attachment dir or collide with each other. Tuples sidestep all of it.
    def key(rel):
        d, name = os.path.split(rel)
        return (d, name[:-3] if name.endswith(".md") else name)

    by_key = {key(u.ident): u for u in units}
    parent = os.path.dirname(root)
    orphans = []
    for dirpath, dirnames, filenames in sorted(os.walk(root)):
        dirnames.sort()
        base = os.path.basename(dirpath)
        if not base.endswith(" (Attachments)"):
            continue
        stem = base[:-len(" (Attachments)")]
        owner_key = (os.path.relpath(os.path.dirname(dirpath), parent), stem)
        files = [os.path.join(dirpath, f) for f in sorted(filenames)
                 if not f.startswith(".")]
        owner = by_key.get(owner_key)
        if owner is None:
            orphans.append((owner_key, files))
            continue
        # extend, never assign: two dirs can legitimately resolve to one note
        owner.meta.setdefault("attachments", []).extend(files)
    if orphans:
        # surfaced through the report; never silently discarded
        for u in units:
            u.meta.setdefault("_orphan_attachment_dirs", len(orphans))
    return units


def read_keep(root, source="keep"):
    """The whole export is ONE text file; units are separated by four blank
    lines. Idents carry the unit index so a row traces back to its byte range."""
    units = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not name.endswith(".txt"):
            continue
        path = os.path.join(root, name)
        raw = open(path, encoding="utf-8-sig", errors="replace").read()
        rel = os.path.relpath(path, os.path.dirname(root))
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        for i, chunk in enumerate(KEEP_SEPARATOR.split(raw)):
            if not chunk.strip():
                continue
            body = clean(chunk).strip("\n")
            units.append(Unit(source, "%s#%d" % (rel, i),
                              title_from(body, fallback="keep #%d" % i),
                              body, mtime=mtime))
    return units


READERS = {
    "apple-notes": read_apple_notes,
    "keep": read_keep,
}


def read_source(dump_root, source):
    root = os.path.join(dump_root, source)
    if not os.path.isdir(root):
        return []
    reader = READERS.get(source)
    if reader is not None:
        return reader(root)
    return read_markdown_source(root, source)


def read_all(dump_root, sources=SOURCES):
    units = []
    for s in sources:
        units.extend(read_source(dump_root, s))
    return units


# --- secret scan ---------------------------------------------------------

def scan_secrets(text):
    """High-confidence matches only. Returns a list of pattern names."""
    return [name for name, rx in SECRET_PATTERNS if rx.search(text or "")]


def has_secret_keywords(text):
    """Keyword hit — NOT grounds for quarantine, only for flagging."""
    return bool(SECRET_KEYWORDS.search(text or ""))


# --- pre-pass ------------------------------------------------------------

def _is_archive_path(unit):
    return unit.source == "obsidian" and re.match(
        r"obsidian/Archive( \d+)?/", unit.ident.replace(os.sep, "/")) is not None


def _keeper_rank(unit):
    """Deterministic winner among identical units. Explicitly ranked so the
    survivor never depends on filesystem walk order — changing directory order
    otherwise silently flips WHICH copy of a note survives."""
    return (1 if _is_archive_path(unit) else 0,
            unit.ident.count(os.sep) + unit.ident.count("/"),
            unit.ident)


def keep_auto_archives(unit):
    """A Keep fragment with no judgment left to make: a bare link, or too short
    to mean anything without its original context."""
    if unit.source != "keep":
        return False
    if len(unit.norm) <= KEEP_AUTO_ARCHIVE_CHARS:
        return True
    lines = [l.strip() for l in unit.body.strip().splitlines() if l.strip()]
    return bool(lines) and all(_URL_LINE.match(l) for l in lines)


def classify(units):
    """Assign exactly one decision to every unit. Mutates and returns them.

    Every unit gets a decision, so the totals always reconcile: that is the
    property the Phase 3 DoD checks.
    """
    units = list(units)

    # 1. secrets first — nothing else may route a key-bearing unit
    remaining = []
    for u in units:
        hits = scan_secrets(u.body)
        if hits:
            u.decision, u.reason = QUARANTINE, "matched %s" % ", ".join(hits)
        else:
            remaining.append(u)

    # 2. provably empty — of TEXT *and* attachments.
    #    An Apple note holding only a photo has an empty markdown body and a
    #    sibling `(Attachments)` dir; treating it as empty throws the photo
    #    away. 2c already settled that a photo-with-no-text note is real work
    #    (only VOICE-only notes are skipped, because nothing can read audio).
    rest = []
    for u in remaining:
        if not u.norm and not (u.meta.get("attachments") or []):
            u.decision, u.reason = EMPTY, "no content and no attachments"
        else:
            rest.append(u)

    # 3. byte-identical duplicates, with an explicit keeper rule
    groups = {}
    for u in rest:
        groups.setdefault(u.sha256, []).append(u)
    survivors = []
    for _, group in sorted(groups.items()):
        group.sort(key=_keeper_rank)
        keeper = group[0]
        survivors.append(keeper)
        for dup in group[1:]:
            dup.decision = DUPLICATE
            dup.reason = "identical to %s" % keeper.ident
            dup.ref = keeper.ident

    # 4. everything left has unique text, so nothing more is DROPPED. What is
    #    left to decide is only whether a model needs to read it.
    #
    #    An obsidian Archive/ file sharing a live file's NAME is a superseded
    #    draft, not a clone — an identical one was already caught in step 3, so
    #    reaching here means its text DIFFERS. Archive it searchable rather than
    #    discarding content that exists nowhere else.
    live_names = {os.path.basename(u.ident) for u in survivors
                  if u.source == "obsidian" and not _is_archive_path(u)}
    for u in survivors:
        if _is_archive_path(u) and os.path.basename(u.ident) in live_names:
            u.decision = AUTO_ARCHIVE
            u.reason = "superseded Archive draft of a live file of the same name"
        elif keep_auto_archives(u):
            u.decision = AUTO_ARCHIVE
            u.reason = ("bare link" if len(u.norm) > KEEP_AUTO_ARCHIVE_CHARS
                        else "<=%d chars" % KEEP_AUTO_ARCHIVE_CHARS)
        else:
            u.decision = LIBRARIAN
            u.reason = "needs judgment"
            if has_secret_keywords(u.body):
                u.meta["secret_keyword_flag"] = True
    return units


# --- report --------------------------------------------------------------

def analyze(dump_root, sources=SOURCES):
    """Full read-only pass. Returns a reconciliation report."""
    units = classify(read_all(dump_root, sources))
    # The pair count comes from the REAL planner, not a second implementation
    # of the rule: `plan_all` is what the importer runs. Areas are irrelevant
    # to whether a unit is a mirror, so it is called without them.
    appends = {p.unit.ident for p in plan_all(units) if p.action == APPEND}
    per_source = {}
    for s in sources:
        per_source[s] = dict({d: 0 for d in DECISIONS}, total=0, append=0)
    for u in units:
        row = per_source.setdefault(
            u.source, dict({d: 0 for d in DECISIONS}, total=0, append=0))
        row["total"] += 1
        row[u.decision] += 1
        if u.ident in appends:
            row["append"] += 1
    totals = dict({d: 0 for d in DECISIONS}, total=len(units), append=0)
    for row in per_source.values():
        for d in DECISIONS:
            totals[d] += row[d]
        totals["append"] += row["append"]
    accounted = sum(totals[d] for d in DECISIONS)
    return {
        "dump_root": dump_root,
        "sources": list(sources),
        "per_source": per_source,
        "totals": totals,
        "accounted": accounted,
        "reconciles": accounted == len(units),
        "flagged_secret_keywords": sum(
            1 for u in units if u.meta.get("secret_keyword_flag")),
        "quarantined": [
            {"source": u.source, "ident": u.ident, "reason": u.reason,
             "sha256": u.sha256}
            for u in units if u.decision == QUARANTINE],
        "units": units,
    }


def report_rows(report):
    """(source, total, quarantine, empty, duplicate, auto_archive, librarian,
    append).

    `append` is NOT a sixth decision and does not enter the reconciliation: it
    is a SUBSET of the kept units (see `find_mirror_pairs`) that land as a
    Thought on another unit's note instead of as a note of their own.
    """
    rows = []
    for s in report["sources"]:
        r = report["per_source"].get(s)
        if r is None:
            continue
        rows.append((s, r["total"], r[QUARANTINE], r[EMPTY], r[DUPLICATE],
                     r[AUTO_ARCHIVE], r[LIBRARIAN], r["append"]))
    t = report["totals"]
    rows.append(("TOTAL", t["total"], t[QUARANTINE], t[EMPTY], t[DUPLICATE],
                 t[AUTO_ARCHIVE], t[LIBRARIAN], t["append"]))
    return rows


# --- what each unit BECOMES (the second layer, on top of the pre-pass) ------
#
# The pre-pass answers "what can code prove about this unit's CONTENT".
# Planning answers "where does it land". They are separate on purpose: a
# focus task is in the `librarian` bucket because nothing about its text
# lets us drop it, yet its frontmatter says exactly where it belongs, so it is
# filed rather than queued.

SKIP = "skip"            # empty or duplicate — provably lossless
CREATE = "create"
HOLD = "hold"            # quarantined: no note row, ever
APPEND = "append"        # a mirror of a note unit: a Thought on it, not a note


class Plan:
    __slots__ = ("unit", "action", "status", "area_id", "tags", "reason", "target")

    def __init__(self, unit, action, status=None, area_id=None, tags=None,
                 reason="", target=None):
        self.unit = unit
        self.action = action
        self.status = status
        self.area_id = area_id
        self.tags = tags or []
        self.reason = reason
        # APPEND only: the Unit whose note receives this unit's text.
        self.target = target


# Folder segments that group without describing. `Tasks/`, `Notes/`, `Unfiled/`
# are containers every file in a source sits in; `Other/` and `Archive/` are
# filing mechanics. None of them says what a note is ABOUT, so none becomes a
# tag. `Tutorials` is handled separately -- it is a KIND, worth exactly one tag.
_CONTAINER_SEGMENTS = frozenset({
    "archive", "archive 2", "other", "tasks", "notes", "unfiled", "tutorials",
})


def _kebab(s):
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-").lower()
    return re.sub(r"-{2,}", "-", s)


def folder_tags(unit, area_names=()):
    """Topic tags derived from where the note sat in its source tree.

    The dump's folders WERE the organization -- `Tutorials/NestJs/` is a course,
    `Acme/Feedback/` is one note per person -- and the first waves imported
    that structure as nothing at all, leaving 58% of notes untagged and the
    Library unnavigable (owner, 2026-09-09).

    Rules, in order:
      * the source root is dropped FIRST, so `obsidian` / `keep` / `focus`
        can never become a tag. **Provenance is not a topic** and the no-origin-tag
        decision of 2026-09-08 still holds -- `source_of_content_hash` remains the
        provenance story.
      * the filename is dropped (it is the title).
      * container segments are dropped (see `_CONTAINER_SEGMENTS`).
      * a segment naming an AREA is dropped: focus foldered by `domain:`,
        which already becomes the note's area, so tagging it too says it twice.
      * whatever survives is kebab-cased, and a path through `Tutorials/` adds
        the single tag `tutorial`.
    """
    ident = getattr(unit, "ident", "") or ""
    segs = ident.split("/")[1:-1]                 # drop source root AND filename
    areas = {_kebab(a) for a in (area_names or ())}
    out = []
    for raw in segs:
        k = _kebab(raw)
        if not k or k in _CONTAINER_SEGMENTS or k in areas:
            continue
        if k not in out:
            out.append(k)
    if any(_kebab(p) == "tutorials" for p in ident.split("/")) and "tutorial" not in out:
        out.append("tutorial")
    return out


def _import_tags(unit, area_names=()):
    """Tags an imported note is BORN with: its FOLDER topics, never its origin.

    Owner decision (2026-09-08, still in force): NO origin tag. Stamping every
    imported note `focus` / `obsidian` / `keep` put hundreds of unrelated
    notes into one shared-tags group and drowned the Related card; it was
    stripped from all 97 live notes and must not come back. `folder_tags` drops
    the source root before anything else, so that cannot happen here.

    What DOES come through is topic: the folder tree the dumps were organized
    by. Provenance still does not depend on tags — `authored_by='import'` says a
    note was imported and `notes.content_hash` IS `Unit.sha256`, so
    `source_of_content_hash` names the exact source file.
    """
    return folder_tags(unit, area_names)


def _clean_title(unit):
    """Apple filenames include `..md`, `.md` and `. (2).md`, whose derived
    titles are empty or bare punctuation. Fall back to the first line of the
    body — create_note requires a real title."""
    t = (unit.title or "").strip()
    if t.strip(". \t-_—–"):
        return t[:300]
    return title_from(unit.body, fallback="%s import" % unit.source)[:300]


def _unit_type(unit):
    return (unit.meta.get("type") or "").strip().lower()


def _pair_key(title):
    """Titles compare case-insensitively and whitespace-insensitively — the
    mirror pair is created by the SOURCE APP from one string, so anything that
    survives a round trip through two files still names the same item."""
    return normalize(title or "").casefold()


def find_mirror_pairs(units):
    """Map `ident -> note Unit` for every task unit that MIRRORS a note unit.

    focus creates-and-links: one item can exist twice in the dump, as
    `Tasks/<Title>.md` (whose body is the `- [ ]` / `- [x]` checkbox line) and
    as `Notes/<Title>.md` (the free text). The two bodies differ, so the digest
    correctly keeps both — and importing both produced the duplicate-title
    pairs the owner found in the library.

    Owner decision (2026-09-08, final): `source:` frontmatter + the same title
    means the task is the note's mirror. The task's text becomes a Thought ON
    the note; it is not a second note. A `type: task` unit WITHOUT `source:`,
    or one whose title names no note, is untouched and lands as its own note.

    Pure — it only annotates `unit.meta` (`pair_target`, and `pair_ambiguous`
    when more than one note claimed the title) so a report can explain itself.
    """
    # Keyed by (source, title): pairing is a fact about ONE dump's internal
    # shape. Without the source in the key the analyzer (which reads every
    # dump at once) would find pairs the importer (which reads one) cannot,
    # and the committed baseline would describe an import that never happens.
    notes_by_title = {}
    for u in units:
        if _unit_type(u) == "note":
            notes_by_title.setdefault((u.source, _pair_key(u.title)), []).append(u)
    for cands in notes_by_title.values():
        cands.sort(key=lambda u: u.ident)       # deterministic, not walk order

    pairs = {}
    for u in units:
        if _unit_type(u) != "task" or not (u.meta.get("source") or "").strip():
            continue
        cands = notes_by_title.get((u.source, _pair_key(u.title)))
        if not cands:
            continue
        domain = (u.meta.get("domain") or "").strip().lower()
        same_domain = [c for c in cands
                       if (c.meta.get("domain") or "").strip().lower() == domain]
        # Prefer the note filed in the same domain; otherwise the first by
        # ident, and SAY SO rather than picking silently.
        target = (same_domain or cands)[0]
        if len(cands) > 1:
            u.meta["pair_ambiguous"] = [c.ident for c in cands]
        u.meta["pair_target"] = target.ident
        pairs[u.ident] = target
    return pairs


def plan_unit(unit, areas_by_name=None, target=None):
    """Decide what one classified unit becomes. Pure: no DB writes.

    `target` is the mirror note from `find_mirror_pairs` (see `plan_all`); it
    is only ever honoured for a unit the pre-pass would otherwise CREATE, so a
    quarantined or duplicate task can never leak in as somebody's Thought.
    """
    areas_by_name = areas_by_name or {}
    if unit.decision == QUARANTINE:
        return Plan(unit, HOLD, reason=unit.reason)
    if unit.decision in (EMPTY, DUPLICATE):
        return Plan(unit, SKIP, reason=unit.reason)

    if target is not None:
        return Plan(unit, APPEND, target=target,
                    tags=_import_tags(unit, areas_by_name.keys()),
                    reason="mirrors the note %s — appended there as a Thought"
                           % target.ident)

    tags = _import_tags(unit, areas_by_name.keys())
    if unit.decision == AUTO_ARCHIVE:
        return Plan(unit, CREATE, status="archived", tags=tags, reason=unit.reason)

    # LIBRARIAN by content — but focus frontmatter already says where it
    # goes and whether it is finished, so it is filed instead of queued (owner
    # decision: import as notes, no HQ tasks; done -> archived, open -> its area).
    area_id = areas_by_name.get((unit.meta.get("domain") or "").strip().lower())
    if unit.source == "focus":
        if (unit.meta.get("status") or "").strip().lower() == "done":
            return Plan(unit, CREATE, status="archived", area_id=area_id, tags=tags,
                        reason="focus status: done")
        if area_id is not None:
            return Plan(unit, CREATE, status="active", area_id=area_id, tags=tags,
                        reason="filed by focus domain")
    return Plan(unit, CREATE, status="inbox", area_id=area_id, tags=tags,
                reason="needs librarian triage")


def plan_all(units, areas_by_name=None):
    """Plan a WHOLE source at once.

    Pairing is a cross-unit fact, so it lives here rather than in `plan_unit`:
    the analyzer calls `plan_all` too, which is what keeps the committed
    baseline's pair count identical to what the importer will really do.
    """
    units = list(units)
    pairs = find_mirror_pairs(units)
    return [plan_unit(u, areas_by_name, target=pairs.get(u.ident)) for u in units]


def plan_summary(plans):
    out = {"create_inbox": 0, "create_active": 0, "create_archived": 0,
           "skip": 0, "hold": 0, "append": 0}
    for p in plans:
        if p.action == CREATE:
            out["create_" + p.status] += 1
        else:
            out[p.action] += 1
    out["create_total"] = (out["create_inbox"] + out["create_active"]
                           + out["create_archived"])
    return out


def fixture_of(report):
    """The comparable shape the DoD reconciles against — counts only, no
    content, so it is safe to commit and stable across runs."""
    keys = ("total",) + tuple(DECISIONS) + ("append",)
    return {
        "totals": {k: report["totals"][k] for k in keys},
        "per_source": {s: {k: report["per_source"][s][k] for k in keys}
                       for s in report["per_source"]},
        "flagged_secret_keywords": report["flagged_secret_keywords"],
        "quarantined_count": len(report["quarantined"]),
    }


def source_of_content_hash(content_hash, dump_root, sources=SOURCES):
    """`(source, ident)` of the dump record an imported note came from, or None.

    This is the whole provenance story now that imported notes carry no origin
    tag: `notes.content_hash` IS `Unit.sha256`, so the immutable dumps are the
    ledger and no schema column had to be added to keep one.
    """
    for u in read_all(dump_root, sources):
        if u.sha256 == content_hash:
            return (u.source, u.ident)
    return None


# --- execution (the ONLY part of this module that writes) -------------------

# Stands in for the row a dry run would have created, so the preview reports
# an append rather than a fallback it would never actually take.
_PLANNED = "planned"

ATTACH_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".heic": "image/heic", ".webp": "image/webp"}


def _areas_by_name(store, db_path=None):
    """Lowercased area name -> id. focus `domain:` values map 1:1 onto
    the seeded areas, so filing that source needs no judgment."""
    return {a["name"].strip().lower(): a["id"]
            for a in store.list_areas(db_path=db_path)}


def import_source(dump_root, source, store, db_path=None, dry_run=True,
                  with_attachments=True, limit=None):
    """Import one dump. Returns a result dict; writes NOTHING when dry_run.

    Idempotent: a unit whose content_hash is already on a note is reported as
    `already_imported` and skipped, so re-running a wave neither duplicates
    notes nor re-quarantines bytes.

    TWO passes, and the order is load-bearing: every CREATE runs before any
    APPEND, so a mirror task can always find the note it belongs on even when
    the walk happened to reach `Tasks/` first.
    """
    units = classify(read_source(dump_root, source))
    areas = _areas_by_name(store, db_path=db_path)
    plans = plan_all(units, areas)
    if limit is not None:
        plans = plans[:int(limit)]

    res = {"source": source, "dry_run": bool(dry_run),
           "planned": plan_summary(plans), "created": [], "quarantined": [],
           "already_imported": 0, "attachments": 0, "errors": [],
           "appended": [], "appended_already": 0, "append_fallback": [],
           "merge_needed": [], "warnings": []}
    note_ids = {}                       # unit ident -> the note row it became

    for p in plans:
        u = p.unit
        if p.action in (SKIP, APPEND):
            continue                    # APPEND is the second pass, below
        if p.action == HOLD:
            if not dry_run:
                store.quarantine_unit(u.source, u.ident, u.reason, u.sha256,
                                      u.body, db_path=db_path)
            res["quarantined"].append(u.ident)
            continue

        # idempotency is checked even on a dry run, so the preview tells the
        # truth about what a real run would actually do
        existing = store.note_by_content_hash(u.sha256, db_path=db_path)
        if existing is not None:
            res["already_imported"] += 1
            note_ids[u.ident] = existing
            continue
        if dry_run:
            res["created"].append((u.ident, p.status))
            note_ids[u.ident] = _PLANNED    # so an APPEND can see its target
            continue
        _create(p, store, res, note_ids, with_attachments, db_path=db_path)

    seen_entries = {}                   # target ident -> normalized entry bodies
    for p in plans:
        if p.action == APPEND:
            _append_mirror(p, store, res, note_ids, seen_entries, dry_run,
                           areas, with_attachments, db_path=db_path)
    return res


def _create(plan, store, res, note_ids, with_attachments, db_path=None):
    """Create the note one plan asks for, and record it. Returns the id or None.

    The mirror fallback lands here too, so a task that could not be appended is
    filed by exactly the same rules as one that was never a mirror.
    """
    u = plan.unit
    try:
        nid = store.create_note(
            _clean_title(u), body=u.body, status=plan.status,
            area_id=plan.area_id, tags=plan.tags, authored_by="import",
            content_hash=u.sha256, db_path=db_path)
    except ValueError as e:
        res["errors"].append("%s: %s" % (u.ident, e))
        return None
    note_ids[u.ident] = nid
    res["created"].append((u.ident, plan.status))
    if with_attachments:
        res["attachments"] += _attach(store, nid, u, res, db_path=db_path)
    return nid


def _target_note_id(plan, store, note_ids, db_path=None):
    """The note this mirror belongs on: the row created in THIS run, or the one
    a previous wave already created from the same target unit."""
    t = plan.target
    nid = note_ids.get(t.ident)
    if nid is not None:
        return nid
    return store.note_by_content_hash(t.sha256, db_path=db_path)


def _entry_bodies(plan, store, target_id, seen, db_path=None):
    """The normalized entry bodies already on this mirror's target note.

    Cached per target and UPDATED as this run appends, which is what makes a
    dry run and a real run agree: three byte-identical mirrors collapse into
    one Thought in the preview exactly as they do on disk.
    """
    key = plan.target.ident
    if key not in seen:
        bodies = set()
        if isinstance(target_id, int):
            note = store.get_note(target_id, db_path=db_path) or {}
            bodies = {normalize(e["body"]) for e in note.get("entries") or []}
        seen[key] = bodies
    return seen[key]


def _append_mirror(plan, store, res, note_ids, seen_entries, dry_run,
                   areas_by_name, with_attachments=True, db_path=None):
    """Land one APPEND plan: the task's text as a Thought on its note.

    Idempotency has TWO halves, because a re-run can arrive from two different
    pasts:

    * wave 1 imported these tasks as notes of their own, BEFORE this rule
      existed. Such a unit's content_hash is already on a note, so it stays
      `already_imported` — the importer never retroactively merges live notes.
      It does REPORT the split (`merge_needed`) so the librarian pass has the
      two ids instead of having to rediscover them.
    * a wave imported under this rule left no note for the task, so the
      content_hash check finds nothing. `note_entries` has no content_hash
      column and no delete path, so the guard is the entry bodies themselves.
    """
    u = plan.unit
    own = store.note_by_content_hash(u.sha256, db_path=db_path)
    if own is not None:
        res["already_imported"] += 1
        target_id = _target_note_id(plan, store, note_ids, db_path=db_path)
        note_ids[u.ident] = own
        if target_id is not None and target_id != own:
            # Two notes for one item, and neither row says so. This is the ONLY
            # signal the librarian gets, so it carries both ids: the fallback
            # (or an older wave) is permanent, and a later wave importing the
            # target would otherwise re-split in silence.
            res["merge_needed"].append({
                "title": _clean_title(u), "ident": u.ident,
                "target_ident": plan.target.ident, "note_id": own,
                "target_note_id": target_id if isinstance(target_id, int) else None})
        return

    target_id = _target_note_id(plan, store, note_ids, db_path=db_path)
    if target_id is None:
        # The note was held back (secret), dropped (empty/duplicate), errored,
        # or fell outside a --limit slice. Losing the task's text to a missing
        # target would be a silent deletion, so it becomes its own note again,
        # filed by its OWN frontmatter exactly as an unpaired task would be.
        own_plan = plan_unit(u, areas_by_name)
        res["append_fallback"].append(
            "%s: mirror target %s has no note (held, skipped or out of range)"
            " — created as its own note" % (u.ident, plan.target.ident))
        if dry_run:
            res["created"].append((u.ident, own_plan.status))
            return
        _create(own_plan, store, res, note_ids, with_attachments, db_path=db_path)
        return

    bodies = _entry_bodies(plan, store, target_id, seen_entries, db_path=db_path)
    body = u.body                      # VERBATIM: the checkbox line says open/done
    if normalize(body) in bodies:
        res["appended_already"] += 1
        return
    bodies.add(normalize(body))
    if not dry_run:
        try:
            store.add_note_entry(target_id, body, db_path=db_path)
        except ValueError as e:
            res["errors"].append("%s -> note #%s: %s" % (u.ident, target_id, e))
            return
        if with_attachments and (u.meta.get("attachments") or []):
            # A mirror has no note row of its own, so its files would vanish.
            # None of the 8 real mirrors carries any; if one ever does, it is
            # bound to the target and SAID, never dropped quietly.
            n = _attach(store, target_id, u, res, db_path=db_path)
            res["attachments"] += n
            res["warnings"].append(
                "%s: mirror carried %d attachment(s) — bound to its target"
                " note #%s" % (u.ident, n, target_id))
    res["appended"].append((u.ident, plan.target.ident))
    # No `link_note` here on purpose: the mirror never becomes a row of its own,
    # so there is no second id to link, and note_links has no unit-level kind.


def _attach(store, note_id, unit, res, db_path=None):
    """Apple's `(Attachments)` images, bound to the note they belong to.

    Failures are recorded, never raised: a note that imported fine must not be
    lost because one of its images could not be read.
    """
    n = 0
    for path in unit.meta.get("attachments") or []:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            if not data:
                continue
            ext = os.path.splitext(path)[1].lower()
            store.add_attachment(note_id, data, kind="photo",
                                 mime=ATTACH_MIME.get(ext),
                                 orig_name=os.path.basename(path),
                                 db_path=db_path)
            n += 1
        except (OSError, ValueError) as e:
            res["errors"].append("%s attachment %s: %s"
                                 % (unit.ident, os.path.basename(path), e))
    return n


# =========================================================================
# Phase 4 slice A — restoring an export (intent/Phase4Plan.md)
#
# The owner chose "readable AND provably re-importable", which means the round
# trip runs through THIS module rather than a test-only reader: the zip is the
# library's real backup, so the thing that reads it has to be the thing that
# ships.
#
# Kept apart from the five dump readers on purpose. A dump is other people's
# data being interpreted — classified, deduped, auto-archived, quarantined. An
# export is OUR data coming home, where every one of those judgements has
# already been made and re-applying them would silently drop notes: the library
# legitimately holds byte-identical archived twins that `DUPLICATE` would eat.
# =========================================================================

EXPORT = "export"                  # deliberately NOT in SOURCES: that tuple is
                                   # the dump baseline the analyzer reconciles.


def _yaml_lite(text):
    """Parse the frontmatter `backend/notes_export.py` writes, and only that.

    Scalars, double-quoted strings with `\\"` and `\\\\` escapes, and flow
    sequences of the same. `split_frontmatter` cannot be reused here — it hands
    back raw strings with the quotes stripped and no unescaping, which turns a
    title containing a quote into a different title.
    """
    out = {}
    for line in (text or "").splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = _yaml_value(v.strip())
    return out


def _yaml_value(v):
    if v.startswith("[") and v.endswith("]"):
        return [_yaml_value(p.strip()) for p in _split_flow(v[1:-1]) if p.strip()]
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return _unescape(v[1:-1])
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1]
    if v in ("null", "~", ""):
        return None
    if v == "true":
        return True
    if v == "false":
        return False
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _split_flow(s):
    """Split a flow sequence on commas that are NOT inside a quoted string."""
    parts, buf, in_q, esc = [], [], False, False
    for ch in s:
        if esc:
            buf.append(ch)
            esc = False
            continue
        if ch == "\\" and in_q:
            buf.append(ch)
            esc = True
            continue
        if ch == '"':
            in_q = not in_q
            buf.append(ch)
            continue
        if ch == "," and not in_q:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _unescape(s):
    out, esc = [], False
    for ch in s:
        if esc:
            out.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        else:
            out.append(ch)
    return "".join(out)


def _split_document(text):
    """(body, thought list) from everything below the frontmatter.

    The body is returned byte-for-byte: the export writes its Thoughts and
    attachment blocks BELOW a delimiter comment precisely so a restore can hand
    back exactly what the owner typed.
    """
    from backend import notes_export as nx
    cut = len(text)
    for mark in (nx.THOUGHTS_MARK, nx.ATTACH_MARK):
        i = text.find("\n" + mark)
        if i != -1:
            cut = min(cut, i)
    body = text[:cut]
    thoughts = []
    ti = text.find("\n" + nx.THOUGHTS_MARK)
    if ti != -1:
        rest = text[ti + len(nx.THOUGHTS_MARK) + 1:]
        ai = rest.find("\n" + nx.ATTACH_MARK)
        if ai != -1:
            rest = rest[:ai]
        cur_ts, cur = None, []
        for line in rest.splitlines():
            m = nx.THOUGHT_HEAD.match(line)
            if m:
                if cur_ts is not None or cur:
                    thoughts.append((cur_ts, "\n".join(cur).strip("\n")))
                cur_ts, cur = m.group(1), []
                continue
            cur.append(line)
        if cur_ts is not None or cur:
            thoughts.append((cur_ts, "\n".join(cur).strip("\n")))
    return body, [(ts, b) for ts, b in thoughts if b.strip()]


def read_export(root, source=EXPORT):
    """Every note file in an unzipped export, as Units carrying their own
    frontmatter. `_hermes/` and the README are structure, not notes."""
    from backend import notes_export as nx
    manifest = read_export_manifest(root)
    attach_meta = manifest.get("attachments") or {}
    units = []
    for dirpath, dirnames, filenames in sorted(os.walk(root)):
        dirnames.sort()
        dirnames[:] = [d for d in dirnames if d not in ("_hermes", "attachments")]
        for name in sorted(filenames):
            if not name.endswith(".md") or name == "README.md":
                continue
            path = os.path.join(dirpath, name)
            try:
                raw = open(path, encoding="utf-8").read()
            except OSError:
                continue
            m = _FM.match(raw)
            if not m:
                continue                      # not one of ours; never guessed at
            meta = _yaml_lite(m.group(1))
            body, thoughts = _split_document(raw[m.end():])
            rel = os.path.relpath(path, root)
            nid = meta.get("id")
            title = meta.get("title") or os.path.basename(name)[:-3]
            # Identity for the idempotency check: the note's ORIGINAL
            # content_hash when it had one, else a stable per-note id. Either
            # way a second restore of the same zip creates nothing.
            meta["uuid"] = str(meta.get("content_hash") or "%s-note-%s" % (source, nid))
            files = []
            for a in attach_meta.get(str(nid), []):
                p = os.path.join(os.path.dirname(path), "attachments", a["file"])
                if os.path.exists(p):
                    files.append(p)
                    a["path"] = p
            meta["attachments"] = files
            meta["attachment_meta"] = attach_meta.get(str(nid), [])
            meta["thoughts"] = thoughts
            u = Unit(source, rel, title, body, meta=meta)
            u.decision, u.reason = LIBRARIAN, "restore"
            units.append(u)
    return units


def read_export_manifest(root):
    from backend import notes_export as nx
    path = os.path.join(root, nx.MANIFEST_PATH)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _restore_areas_and_tags(root, store, db_path=None):
    """Create every area the export knew about and re-register the whole tag
    vocabulary BEFORE any note lands, so filings resolve and the closed
    taxonomy comes back at full size rather than shrinking to tags in use."""
    manifest = read_export_manifest(root)
    made = []
    by_name = {a["name"].strip().lower(): a["id"]
               for a in store.list_areas(db_path=db_path, archived=True)}
    for a in manifest.get("areas") or []:
        key = (a.get("name") or "").strip().lower()
        if not key or key in by_name:
            continue
        parent = by_name.get((a.get("parent") or "").strip().lower())
        aid = store.create_area(a["name"], parent_id=parent,
                                description=a.get("description"), db_path=db_path)
        by_name[key] = aid
        made.append(a["name"])
        if a.get("archived"):
            store.update_area(aid, archived=True, db_path=db_path)
    tags = [t.get("tag") for t in (manifest.get("tags") or []) if t.get("tag")]
    if tags:
        store.register_tags(tags, added_by="import", db_path=db_path)
    return made, len(tags)


def plan_export_unit(unit, areas_by_name, projects_by_slug):
    """A restore honours the frontmatter and derives NOTHING.

    `folder_tags` must not run here: it would mint `areas`, `archive` and
    `inbox` tags out of the export's own PARA folders — the exact origin-tag
    mistake the owner had stripped from the library on 2026-09-08.
    """
    meta = unit.meta
    status = (meta.get("status") or "inbox").strip().lower()
    area_id = areas_by_name.get((meta.get("area") or "").strip().lower())
    project_id = projects_by_slug.get((meta.get("project") or "").strip().lower())
    tags = [t for t in (meta.get("tags") or []) if isinstance(t, str) and t.strip()]
    p = Plan(unit, CREATE, status=status, area_id=area_id, tags=tags,
             reason="restore")
    p.target = None
    return p, project_id


def import_export(root, store, db_path=None, dry_run=True, with_attachments=True,
                  limit=None):
    """Restore an unzipped export. Returns the same result shape as
    `import_source` so one CLI printer serves both.

    REFUSES a library that already holds notes the manifest does not account
    for. Restoring a backup on top of a live library is not a half-safe
    operation: without this a stray `--apply` would clone all 945 notes.
    """
    from backend import notes_export as nx
    units = read_export(root, EXPORT)
    res = {"source": EXPORT, "dry_run": bool(dry_run),
           "planned": {}, "created": [], "quarantined": [], "already_imported": 0,
           "attachments": 0, "errors": [], "appended": [], "appended_already": 0,
           "append_fallback": [], "merge_needed": [], "warnings": [],
           "entries": 0, "links": 0, "areas_created": []}
    if not units:
        res["warnings"].append("no note files under %s — is this an export?" % root)
        res["planned"] = plan_summary([])
        return res

    known = {u.sha256 for u in units}
    existing = store.list_notes(limit=100000, db_path=db_path)
    stranger = [n for n in existing if (n.get("content_hash") or "") not in known]
    if stranger:
        res["errors"].append(
            "refusing: this library holds %d note(s) the export does not account "
            "for (e.g. #%s %r). Restore into an EMPTY library."
            % (len(stranger), stranger[0]["id"], stranger[0]["title"][:60]))
        res["planned"] = plan_summary([])
        return res

    if not dry_run:
        made, _n = _restore_areas_and_tags(root, store, db_path=db_path)
        res["areas_created"] = made
    areas = _areas_by_name(store, db_path=db_path)
    projects = {p["slug"].strip().lower(): p["id"]
                for p in store.list_projects(db_path=db_path)}
    plans = []
    for u in units:
        p, project_id = plan_export_unit(u, areas, projects)
        if p.status == "active" and p.area_id is None and project_id is None:
            # The home rule refuses a homeless active note, and an export
            # restored onto a box that does not have the linked PROJECT would
            # otherwise fail the whole note. Land it in the inbox and say so —
            # a note in the wrong tray is recoverable, a note that errored out
            # of the restore is not.
            res["warnings"].append(
                "note #%s (%r) was filed to %s, which does not exist here — "
                "restored to the inbox"
                % (u.meta.get("id"), (u.title or "")[:50],
                   u.meta.get("project") and "project %s" % u.meta["project"]
                   or "area %s" % u.meta.get("area")))
            p.status = "inbox"
        plans.append((p, project_id))
    if limit is not None:
        plans = plans[:int(limit)]
    res["planned"] = plan_summary([p for p, _ in plans])

    id_map = {}                     # old note id -> new note id
    for p, project_id in plans:
        u = p.unit
        found = store.note_by_content_hash(u.sha256, db_path=db_path)
        if found is not None:
            res["already_imported"] += 1
            id_map[u.meta.get("id")] = found
            continue
        if dry_run:
            res["created"].append((u.ident, p.status))
            continue
        nid = _restore_note(p, project_id, store, res, db_path=db_path)
        if nid is not None:
            id_map[u.meta.get("id")] = nid
            if with_attachments:
                res["attachments"] += _restore_attachments(u, nid, store, res,
                                                           db_path=db_path)
            _restore_provenance(u, nid, store, db_path=db_path)
    if not dry_run:
        res["links"] = _restore_links(plans, id_map, store, res, db_path=db_path)
    return res


def _restore_note(plan, project_id, store, res, db_path=None):
    u = plan.unit
    meta = u.meta
    try:
        nid = store.create_note(
            _clean_title(u), body=u.body,
            note_type=(meta.get("type") or "note"),
            status=plan.status, area_id=plan.area_id, project_id=project_id,
            tags=plan.tags, authored_by=(meta.get("authored_by") or "import"),
            content_hash=u.sha256, db_path=db_path)
    except ValueError as e:
        res["errors"].append("%s: %s" % (u.ident, e))
        return None
    res["created"].append((u.ident, plan.status))
    for ts, body in meta.get("thoughts") or []:
        try:
            from backend import notes_export as nx
            store.add_note_entry(nid, body, created_at=nx.parse_iso(ts),
                                 db_path=db_path)
            res["entries"] += 1
        except ValueError as e:
            res["errors"].append("%s thought: %s" % (u.ident, e))
    return nid


def _restore_attachments(unit, note_id, store, res, db_path=None):
    n = 0
    for a in unit.meta.get("attachment_meta") or []:
        path = a.get("path")
        if not path:
            res["errors"].append("%s: attachment %s missing from the zip"
                                 % (unit.ident, a.get("file")))
            continue
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            store.add_attachment(note_id, data, kind=(a.get("kind") or "photo"),
                                 mime=a.get("mime"), orig_name=a.get("orig_name"),
                                 duration_ms=a.get("duration_ms"), db_path=db_path)
            n += 1
        except (OSError, ValueError) as e:
            res["errors"].append("%s attachment %s: %s"
                                 % (unit.ident, a.get("file"), e))
    return n


def _restore_provenance(unit, note_id, store, db_path=None):
    """Timestamps LAST: adding a Thought or an attachment stamps `updated_at`,
    so restoring the dates before them would leave every note reading as
    edited at restore time."""
    from backend import notes_export as nx
    meta = unit.meta
    try:
        store.set_note_provenance(
            note_id,
            created_at=nx.parse_iso(meta.get("created")),
            updated_at=nx.parse_iso(meta.get("updated")),
            pinned=bool(meta.get("pinned")), disputed=bool(meta.get("disputed")),
            db_path=db_path)
    except ValueError:
        pass


def _restore_links(plans, id_map, store, res, db_path=None):
    """Note links are remapped through the new ids; task and schedule links are
    restored only when that row exists — an export of the library carries no
    tasks, so a restore into a fresh box legitimately has nowhere to point."""
    n = 0
    for p, _project_id in plans:
        src = id_map.get(p.unit.meta.get("id"))
        if src is None:
            continue
        for spec in p.unit.meta.get("links") or []:
            kind, _, target = str(spec).partition(":")
            if not target.isdigit():
                continue
            tid = int(target)
            if kind == "note":
                tid = id_map.get(tid)
                if tid is None:
                    res["warnings"].append(
                        "note #%s links to note %s, which is not in this export"
                        % (p.unit.meta.get("id"), target))
                    continue
            try:
                store.link_note(src, kind, tid, db_path=db_path)
                n += 1
            except ValueError:
                res["warnings"].append(
                    "note #%s: %s %s no longer exists — link dropped"
                    % (p.unit.meta.get("id"), kind, target))
    return n
