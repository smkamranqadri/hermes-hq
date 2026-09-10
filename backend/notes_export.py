"""Second Brain export (intent/Phase4Plan.md, slice A).

A markdown zip that opens as a sane Obsidian vault AND re-imports through the
production import path — the owner's choice at the 2026-09-10 interview. That
second half is what makes this a backup rather than a souvenir: without it the
zip returns the words and loses the areas, types, links, Thought dates and
provenance that took Phase 3 to build.

Layout:

    Areas/<Area>/<Title> (#12).md        active notes filed to an area
    Projects/<Project>/<Title> (#13).md  active notes filed to a project
    Inbox/<Title> (#14).md               untriaged
    Archive/<Area>/<Title> (#15).md      archived, under its area when it has one
    <folder>/attachments/<id>-<name>     the note's own bytes, beside it
    _hermes/manifest.json                areas, tag taxonomy, projects, counts

Two rules the layout exists to satisfy:

- **Every filename carries its note id.** The import measured 128 same-title
  groups across the dumps; `Areas/Work/Server Setup.md` written twice is one
  file and a silently lost note. 66 live titles also contain a `/`.
- **Folders are cosmetic; frontmatter is the truth.** Status, area, project and
  type all round-trip through the frontmatter, so re-filing the zip by hand in
  Obsidian cannot corrupt a restore.
"""
import json
import os
import re
import time
import zipfile

EXPORT_VERSION = 1
MANIFEST_PATH = "_hermes/manifest.json"
SOURCE_NAME = "export"

# Delimiters, not headings: the body above them is byte-for-byte the note's
# body, so a round trip can hand back exactly what the owner wrote.
THOUGHTS_MARK = "<!-- hermes-hq:thoughts -->"
ATTACH_MARK = "<!-- hermes-hq:attachments -->"
THOUGHT_HEAD = re.compile(r"^### (\S+)\s*$")

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WS = re.compile(r"\s+")
NAME_MAX = 80


def iso(ts):
    if ts is None:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))


def parse_iso(s):
    if not s:
        return None
    try:
        return time.mktime(time.strptime(s.strip(), "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (ValueError, OverflowError):
        return None


# --- yaml-lite emit ------------------------------------------------------
#
# The reader half lives in notes_import.py. Both are deliberately small and
# own BOTH ends of one format: pulling in a YAML dependency to write eleven
# known keys would be a dependency for nothing (the same call the dump readers
# made for `split_frontmatter`).

def q(s):
    """A double-quoted YAML scalar. Escapes are the two YAML requires."""
    return '"%s"' % str(s).replace("\\", "\\\\").replace('"', '\\"')


def _scalar(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return q(v)


def _seq(vals):
    return "[%s]" % ", ".join(_scalar(v) for v in vals)


def frontmatter(note, area_name, project_slug, links):
    """Every field a restore needs, in a block Obsidian is happy to ignore."""
    out = [
        "id: %d" % note["id"],
        "title: %s" % q(note["title"]),
        "type: %s" % (note.get("type") or "note"),
        "status: %s" % (note.get("status") or "inbox"),
        "area: %s" % _scalar(area_name),
        "project: %s" % _scalar(project_slug),
        "tags: %s" % _seq(note.get("tags") or []),
        "authored_by: %s" % (note.get("authored_by") or "owner"),
        "content_hash: %s" % _scalar(note.get("content_hash")),
        "created: %s" % _scalar(iso(note.get("created_at"))),
        "updated: %s" % _scalar(iso(note.get("updated_at"))),
        "pinned: %s" % _scalar(bool(note.get("pinned"))),
        "disputed: %s" % _scalar(bool(note.get("disputed"))),
        "links: %s" % _seq("%s:%s" % (l["kind"], l["target_id"]) for l in links),
    ]
    return "---\n%s\n---\n" % "\n".join(out)


# --- naming --------------------------------------------------------------

def safe_name(title, note_id):
    """`<title> (#id).md`, always with the id.

    Sanitizing alone is not enough: 66 live titles contain `/`, and the id is
    what makes two notes called `Server Setup` two files instead of one.
    """
    t = _WS.sub(" ", _UNSAFE.sub("-", str(title or "")).strip())
    t = t.strip(". ")[:NAME_MAX].strip(". ") or "untitled"
    return "%s (#%d).md" % (t, note_id)


def safe_folder(name):
    n = _WS.sub(" ", _UNSAFE.sub("-", str(name or "")).strip()).strip(". ")
    return n[:NAME_MAX] or "_unnamed"


def area_path(area_id, areas_by_id):
    """`Parent/Child` for a two-level area, `Name` for a top-level one."""
    a = areas_by_id.get(area_id)
    if a is None:
        return None
    parent = areas_by_id.get(a.get("parent_id"))
    if parent is not None:
        return "%s/%s" % (safe_folder(parent["name"]), safe_folder(a["name"]))
    return safe_folder(a["name"])


def folder_for(note, areas_by_id, projects_by_id):
    """Where a note sits in the PARA tree. Status decides the top folder; the
    home decides the rest, so an archived Work note stays under Work."""
    home = area_path(note.get("area_id"), areas_by_id)
    if home is None:
        proj = projects_by_id.get(note.get("project_id"))
        home = safe_folder(proj["name"]) if proj else None
    status = note.get("status") or "inbox"
    if status == "inbox":
        return "Inbox"
    if status == "archived":
        return "Archive/%s" % home if home else "Archive"
    if note.get("area_id") and home:
        return "Areas/%s" % home
    if home:
        return "Projects/%s" % home
    return "Inbox"          # an active note without a home cannot exist (home rule)


# --- document ------------------------------------------------------------

def thoughts_block(entries):
    if not entries:
        return ""
    parts = [THOUGHTS_MARK]
    for e in entries:
        parts.append("### %s" % (iso(e.get("created_at")) or "unknown"))
        parts.append(e.get("body") or "")
    return "\n" + "\n\n".join(parts) + "\n"


def attachments_block(files):
    """Obsidian embeds, by bare filename. Names are unique vault-wide (they
    carry the note id), so the wikilink resolves wherever the note is moved."""
    if not files:
        return ""
    return "\n" + "\n".join([ATTACH_MARK]
                            + ["![[%s]]" % os.path.basename(f) for f in files]) + "\n"


def note_document(note, area_name, project_slug, links, entries, files):
    body = note.get("body") or ""
    return (frontmatter(note, area_name, project_slug, links)
            + body + thoughts_block(entries) + attachments_block(files))


# --- the walk ------------------------------------------------------------

def export_zip(store, out_path, db_path=None, with_attachments=True):
    """Write the whole library to `out_path`. Returns a stats dict.

    Streams into the zip note by note — the live library is 945 notes and
    31 MB of attachments, and holding that twice in memory to build a byte
    string first is the difference between a button and an outage.
    """
    snap = store.export_snapshot(db_path=db_path)
    areas_by_id = {a["id"]: a for a in snap["areas"]}
    projects_by_id = {p["id"]: p for p in snap["projects"]}
    started = time.time()
    stats = {"notes": 0, "attachments": 0, "attachment_bytes": 0,
             "entries": 0, "links": 0, "missing_attachments": [],
             "areas": len(snap["areas"]), "tags": len(snap["tags"]),
             "by_status": {}, "paths": {}}
    manifest = {
        "export_version": EXPORT_VERSION,
        "generated_at": iso(started),
        "areas": [{"id": a["id"], "name": a["name"], "description": a.get("description"),
                   "archived": bool(a.get("archived")), "position": a.get("position"),
                   "parent": (areas_by_id.get(a.get("parent_id")) or {}).get("name")}
                  for a in snap["areas"]],
        "tags": snap["tags"],
        "projects": [{"slug": p["slug"], "name": p["name"]} for p in snap["projects"]],
        "notes": [], "attachments": {},
    }

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for note in snap["notes"]:
            nid = note["id"]
            folder = folder_for(note, areas_by_id, projects_by_id)
            path = "%s/%s" % (folder, safe_name(note["title"], nid))
            area = areas_by_id.get(note.get("area_id"))
            proj = projects_by_id.get(note.get("project_id"))
            entries = snap["entries"].get(nid, [])
            links = snap["links"].get(nid, [])
            rows = snap["attachments"].get(nid, []) if with_attachments else []

            files = []
            for a in rows:
                name = "%d-%s" % (nid, a["stored_name"])
                src = store.attachment_path(nid, a["stored_name"])
                arc = "%s/attachments/%s" % (folder, name)
                try:
                    z.write(src, arc)
                except OSError:
                    # A row whose bytes vanished is reported, never fatal: the
                    # other 944 notes must still export.
                    stats["missing_attachments"].append(
                        {"note_id": nid, "stored_name": a["stored_name"]})
                    continue
                files.append(name)
                stats["attachments"] += 1
                stats["attachment_bytes"] += int(a.get("bytes") or 0)
                manifest["attachments"].setdefault(str(nid), []).append(
                    {"file": name, "kind": a.get("kind"), "mime": a.get("mime"),
                     "bytes": a.get("bytes"), "duration_ms": a.get("duration_ms"),
                     "orig_name": a.get("orig_name"),
                     "created": iso(a.get("created_at"))})

            doc = note_document(note, area["name"] if area else None,
                                proj["slug"] if proj else None,
                                links, entries, files)
            z.writestr(path, doc)
            stats["notes"] += 1
            stats["entries"] += len(entries)
            stats["links"] += len(links)
            st = note.get("status") or "inbox"
            stats["by_status"][st] = stats["by_status"].get(st, 0) + 1
            stats["paths"][nid] = path
            manifest["notes"].append({"id": nid, "file": path})

        manifest["counts"] = {k: stats[k] for k in
                              ("notes", "entries", "links", "attachments",
                               "areas", "tags")}
        z.writestr(MANIFEST_PATH, json.dumps(manifest, indent=2, ensure_ascii=False))
        z.writestr("README.md", _readme(stats))

    stats["seconds"] = round(time.time() - started, 2)
    stats["zip_bytes"] = os.path.getsize(out_path)
    return stats


def _readme(stats):
    return (
        "# Hermes HQ — Second Brain export\n\n"
        "%d notes · %d Thoughts · %d attachments · %d areas · %d tags\n\n"
        "Open this folder as an Obsidian vault. Folders are for reading; the\n"
        "`---` frontmatter on each note is what a restore reads, so re-filing\n"
        "a note here by hand cannot corrupt one.\n\n"
        "Restore into an EMPTY library with:\n\n"
        "    wm brain-import run export --root <unzipped dir> --apply\n"
        % (stats["notes"], stats["entries"], stats["attachments"],
           stats["areas"], stats["tags"]))
