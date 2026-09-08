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
        return Plan(unit, APPEND, target=target, tags=[unit.source],
                    reason="mirrors the note %s — appended there as a Thought"
                           % target.ident)

    tags = [unit.source]
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
           "appended": [], "appended_already": 0, "append_fallback": []}
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
        try:
            nid = store.create_note(
                _clean_title(u), body=u.body, status=p.status,
                area_id=p.area_id, tags=p.tags, authored_by="import",
                content_hash=u.sha256, db_path=db_path)
        except ValueError as e:
            res["errors"].append("%s: %s" % (u.ident, e))
            continue
        note_ids[u.ident] = nid
        res["created"].append((u.ident, p.status))
        if with_attachments:
            res["attachments"] += _attach(store, nid, u, res, db_path=db_path)

    for p in plans:
        if p.action == APPEND:
            _append_mirror(p, store, res, note_ids, dry_run, areas,
                           with_attachments, db_path=db_path)
    return res


def _target_note_id(plan, store, note_ids, db_path=None):
    """The note this mirror belongs on: the row created in THIS run, or the one
    a previous wave already created from the same target unit."""
    t = plan.target
    nid = note_ids.get(t.ident)
    if nid is not None:
        return nid
    return store.note_by_content_hash(t.sha256, db_path=db_path)


def _append_mirror(plan, store, res, note_ids, dry_run, areas_by_name,
                   with_attachments=True, db_path=None):
    """Land one APPEND plan: the task's text as a Thought on its note.

    Idempotency has TWO halves, because a re-run can arrive from two different
    pasts:

    * wave 1 imported these tasks as notes of their own, BEFORE this rule
      existed. Such a unit's content_hash is already on a note, so it is
      `already_imported` exactly as before — re-running wave 1 stays a no-op
      and nothing is retroactively merged (the live pairs are the librarian's
      job, not the importer's).
    * a wave imported under this rule left no note for the task, so the
      content_hash check finds nothing. `note_entries` has no content_hash
      column and no delete path, so the guard is the entries themselves:
      an entry whose normalized body already matches is not appended again.
      That also collapses the three byte-identical `Multi-branch setup for
      online payments` mirrors into ONE Thought rather than three.
    """
    u = plan.unit
    if store.note_by_content_hash(u.sha256, db_path=db_path) is not None:
        res["already_imported"] += 1
        return

    target_id = _target_note_id(plan, store, note_ids, db_path=db_path)
    if target_id is None:
        # The note was held back (secret), dropped (empty/duplicate), errored,
        # or fell outside a --limit slice. Losing the task's text to a missing
        # target would be a silent deletion, so it becomes its own note again.
        # Filed by its OWN frontmatter, exactly as an unpaired task would be —
        # the fallback must not also downgrade where the note lands.
        own = plan_unit(u, areas_by_name)
        res["append_fallback"].append(
            "%s: mirror target %s has no note (held, skipped or out of range)"
            " — created as its own note" % (u.ident, plan.target.ident))
        if dry_run:
            res["created"].append((u.ident, own.status))
            return
        try:
            nid = store.create_note(
                _clean_title(u), body=u.body, status=own.status,
                area_id=own.area_id, tags=own.tags, authored_by="import",
                content_hash=u.sha256, db_path=db_path)
        except ValueError as e:
            res["errors"].append("%s: %s" % (u.ident, e))
            return
        note_ids[u.ident] = nid
        res["created"].append((u.ident, own.status))
        if with_attachments:
            res["attachments"] += _attach(store, nid, u, res, db_path=db_path)
        return

    if dry_run:
        res["appended"].append((u.ident, plan.target.ident))
        return
    note = store.get_note(target_id, db_path=db_path) or {}
    body = u.body                      # VERBATIM: the checkbox line says open/done
    if any(normalize(e["body"]) == normalize(body)
           for e in note.get("entries") or []):
        res["appended_already"] += 1
        return
    try:
        store.add_note_entry(target_id, body, db_path=db_path)
    except ValueError as e:
        res["errors"].append("%s -> note #%s: %s" % (u.ident, target_id, e))
        return
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
