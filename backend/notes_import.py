"""Second Brain Phase 3 — importing the five note dumps.

Three layers, and the split is deliberate:

1. **Parse** (read-only) — turn the raw dumps into units.
2. **Classify** (read-only) — what the deterministic pre-pass can PROVE about
   each unit's content: quarantine / empty / duplicate / auto-archive, or
   "the librarian must read this". Every unit gets exactly one decision, so the
   totals always reconcile — the property the Phase 3 DoD checks.
3. **Plan + execute** — where each unit LANDS, and the writing itself. Only
   `import_source` and `_attach` touch the database; everything above them is
   pure and testable without one.

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
    def sha256(self):
        """Digest over TITLE + body, not body alone.

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
            (normalize(self.title) + "\n\x00\n" + self.norm
             + "\n\x00\n" + attached).encode("utf-8")
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
    per_source = {}
    for s in sources:
        per_source[s] = dict({d: 0 for d in DECISIONS}, total=0)
    for u in units:
        row = per_source.setdefault(
            u.source, dict({d: 0 for d in DECISIONS}, total=0))
        row["total"] += 1
        row[u.decision] += 1
    totals = dict({d: 0 for d in DECISIONS}, total=len(units))
    for row in per_source.values():
        for d in DECISIONS:
            totals[d] += row[d]
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
    """(source, total, quarantine, empty, duplicate, auto_archive, librarian)."""
    rows = []
    for s in report["sources"]:
        r = report["per_source"].get(s)
        if r is None:
            continue
        rows.append((s, r["total"], r[QUARANTINE], r[EMPTY], r[DUPLICATE],
                     r[AUTO_ARCHIVE], r[LIBRARIAN]))
    t = report["totals"]
    rows.append(("TOTAL", t["total"], t[QUARANTINE], t[EMPTY], t[DUPLICATE],
                 t[AUTO_ARCHIVE], t[LIBRARIAN]))
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


class Plan:
    __slots__ = ("unit", "action", "status", "area_id", "tags", "reason")

    def __init__(self, unit, action, status=None, area_id=None, tags=None, reason=""):
        self.unit = unit
        self.action = action
        self.status = status
        self.area_id = area_id
        self.tags = tags or []
        self.reason = reason


def _clean_title(unit):
    """Apple filenames include `..md`, `.md` and `. (2).md`, whose derived
    titles are empty or bare punctuation. Fall back to the first line of the
    body — create_note requires a real title."""
    t = (unit.title or "").strip()
    if t.strip(". \t-_—–"):
        return t[:300]
    return title_from(unit.body, fallback="%s import" % unit.source)[:300]


def plan_unit(unit, areas_by_name=None):
    """Decide what one classified unit becomes. Pure: no DB writes."""
    areas_by_name = areas_by_name or {}
    if unit.decision == QUARANTINE:
        return Plan(unit, HOLD, reason=unit.reason)
    if unit.decision in (EMPTY, DUPLICATE):
        return Plan(unit, SKIP, reason=unit.reason)

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
    return [plan_unit(u, areas_by_name) for u in units]


def plan_summary(plans):
    out = {"create_inbox": 0, "create_active": 0, "create_archived": 0,
           "skip": 0, "hold": 0}
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
    return {
        "totals": {k: report["totals"][k] for k in
                   ("total",) + tuple(DECISIONS)},
        "per_source": {s: {k: report["per_source"][s][k] for k in
                           ("total",) + tuple(DECISIONS)}
                       for s in report["per_source"]},
        "flagged_secret_keywords": report["flagged_secret_keywords"],
        "quarantined_count": len(report["quarantined"]),
    }


# --- execution (the ONLY part of this module that writes) -------------------

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
    """
    units = classify(read_source(dump_root, source))
    plans = plan_all(units, _areas_by_name(store, db_path=db_path))
    if limit is not None:
        plans = plans[:int(limit)]

    res = {"source": source, "dry_run": bool(dry_run),
           "planned": plan_summary(plans), "created": [], "quarantined": [],
           "already_imported": 0, "attachments": 0, "errors": []}

    for p in plans:
        u = p.unit
        if p.action == SKIP:
            continue
        if p.action == HOLD:
            if not dry_run:
                store.quarantine_unit(u.source, u.ident, u.reason, u.sha256,
                                      u.body, db_path=db_path)
            res["quarantined"].append(u.ident)
            continue

        # idempotency is checked even on a dry run, so the preview tells the
        # truth about what a real run would actually do
        if store.note_by_content_hash(u.sha256, db_path=db_path) is not None:
            res["already_imported"] += 1
            continue
        if dry_run:
            res["created"].append((u.ident, p.status))
            continue
        try:
            nid = store.create_note(
                _clean_title(u), body=u.body, status=p.status,
                area_id=p.area_id, tags=p.tags, authored_by="import",
                content_hash=u.sha256, db_path=db_path)
        except ValueError as e:
            res["errors"].append("%s: %s" % (u.ident, e))
            continue
        res["created"].append((u.ident, p.status))
        if with_attachments:
            res["attachments"] += _attach(store, nid, u, res, db_path=db_path)
    return res


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
