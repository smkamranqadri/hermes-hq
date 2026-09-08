"""Second Brain API (intent/SecondBrainPlan.md, Phase 1).

Owner-only by construction: every route sits behind the cookie-session
AuthMiddleware, none of this is exposed through the MCP or CLI surfaces
agents use, and Phase 2's librarian gets separate propose-* endpoints —
agents must never gain a direct note write path. ValueError from the store
becomes 409 with the store's own message (same contract as writes.py).
"""
import os

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend import tasks as tq
from backend.files import UPLOAD_MAX_BYTES   # one cap for every upload path
from core import wm_store as store

router = APIRouter(prefix="/api")


def _db():
    return store.DEFAULT_DB_PATH


def _engine(fn, *a, **kw):
    try:
        return fn(*a, db_path=_db(), **kw)
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- areas -------------------------------------------------------------
class AreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_id: int | None = None
    description: str | None = None


class AreaPatch(BaseModel):
    """Every field optional: a PATCH that names none of them is refused by the
    store ("nothing to update") rather than silently no-op'ing."""
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None
    archived: bool | None = None


@router.get("/areas")
def areas(include_archived: bool = False):
    """Filing surfaces take the default (retired areas absent, so nothing can
    be filed into one). `include_archived=1` is for LABEL resolution — a note
    filed before its area was retired still has to render its home."""
    return {"areas": store.list_areas(db_path=_db(), archived=include_archived)}


@router.post("/areas")
def create_area(body: AreaIn):
    aid = _engine(store.create_area, body.name, parent_id=body.parent_id,
                  description=body.description)
    return {"id": aid, "areas": store.list_areas(db_path=_db())}


@router.patch("/areas/{area_id}")
def update_area(area_id: int, body: AreaPatch):
    """Only the fields the caller actually SENT are forwarded.

    `description: str | None` cannot tell "absent" from "explicit null" on its
    own, so a PATCH that renames an area would otherwise wipe its description.
    `model_fields_set` is that distinction, and it is what lets an explicit
    `{"description": null}` still clear the field on purpose.
    """
    sent = body.model_fields_set
    patch = {k: getattr(body, k) for k in ("name", "description", "archived")
             if k in sent}
    if not patch:
        raise HTTPException(409, "nothing to update")
    area = _engine(store.update_area, area_id, **patch)
    return {"area": area, "areas": store.list_areas(db_path=_db())}


# ---- notes -------------------------------------------------------------
class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    type: str = "note"
    status: str = "inbox"
    area_id: int | None = None
    project_id: int | None = None
    tags: list[str] = Field(default_factory=list)


class NoteEdit(BaseModel):
    title: str | None = None
    body: str | None = None
    type: str | None = None
    status: str | None = None
    area_id: int | None = None
    project_id: int | None = None
    tags: list[str] | None = None
    pinned: bool | None = None
    disputed: bool | None = None     # owner clears a contradiction flag once resolved
    # explicit clears — PATCH-style Nones are ambiguous, so unlinking an
    # area/project is its own flag rather than "field present but null"
    clear_area: bool = False
    clear_project: bool = False


class EntryIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


class NoteTaskIn(BaseModel):
    title: str | None = None
    description: str = ""
    project: str | None = None       # slug; defaults to the note's project
    assignee: str = "owner"          # owner todo by default; agents allowed
    ready: bool = True


class NoteReminderIn(BaseModel):
    name: str | None = None
    cron: str
    zone: str | None = None
    project: str | None = None       # slug; defaults to the note's project
    title: str | None = None         # task title the schedule mints
    one_shot: bool = False           # one-time reminder: fires once, then disables


@router.get("/notes")
def notes(status: str | None = None, area_id: int | None = None,
          project_id: int | None = None, type: str | None = None,
          tag: str | None = None, q: str | None = None,
          exclude_archived: bool = False,
          limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    # Slice E: every list answer carries `total` (the unpaged count under the
    # same filters) so the Library can page with "Load more (shown / total)".
    total = _engine(store.count_notes, status=status, area_id=area_id,
                    project_id=project_id, note_type=type, tag=tag, q=q,
                    exclude_archived=exclude_archived)
    page = {"total": total, "limit": limit, "offset": offset}
    if q:
        # Search narrows the CURRENT view rather than replacing it: typing in
        # the box inside an area used to silently drop the area.
        return {"notes": _engine(store.search_notes, q, limit=limit, offset=offset,
                                 status=status, area_id=area_id,
                                 project_id=project_id, note_type=type, tag=tag,
                                 exclude_archived=exclude_archived), "q": q, **page}
    return {"notes": _engine(store.list_notes, status=status, area_id=area_id,
                             project_id=project_id, note_type=type, tag=tag,
                             exclude_archived=exclude_archived,
                             limit=limit, offset=offset), **page}


@router.get("/notes/tags")
def notes_tags():
    """The closed taxonomy with in-use counts (Library tag filter + edit UIs)."""
    return {"tags": store.list_note_tags(db_path=_db())}


@router.get("/notes/tree")
def notes_tree():
    return store.notes_tree(db_path=_db())


@router.get("/notes/facets")
def notes_facets(status: str | None = None, area_id: int | None = None,
                 project_id: int | None = None, type: str | None = None,
                 tag: str | None = None, q: str | None = None,
                 exclude_archived: bool = False):
    """Facet counts for the Library's filter selects — same filters as
    GET /api/notes, each dimension counted with itself left out of scope so
    switching within a dimension (a new tag, a new project) never narrows the
    list the owner is choosing from."""
    return _engine(store.note_facets, status=status, area_id=area_id,
                  project_id=project_id, note_type=type, tag=tag, q=q,
                  exclude_archived=exclude_archived)


@router.get("/note/{note_id}")
def note(note_id: int):
    n = store.get_note(note_id, db_path=_db())
    if n is None:
        raise HTTPException(404, "no such note")
    return n


@router.get("/note/{note_id}/related")
def note_related(note_id: int):
    """Related-notes card: linked / shared-tags / same-area, each capped and
    labelled with a `more` count (core.wm_store.related_notes)."""
    r = store.related_notes(note_id, db_path=_db())
    if r is None:
        raise HTTPException(404, "no such note")
    return r


@router.post("/notes")
def create_note(body: NoteIn):
    nid = _engine(store.create_note, body.title, body=body.body,
                  note_type=body.type, status=body.status,
                  area_id=body.area_id, project_id=body.project_id,
                  tags=body.tags, authored_by="owner")
    nudged = 0
    if body.status == "inbox":
        # 2b-i capture nudge: don't make a fresh capture wait out the cron gap.
        # Debounced in the store — a burst of captures collapses to one run.
        nudged = store.nudge_heartbeat_schedules("librarian_ingest", db_path=_db())
    return {"id": nid, "note": store.get_note(nid, db_path=_db()), "nudged": nudged}


@router.post("/note/{note_id}/edit")
def edit_note(note_id: int, body: NoteEdit):
    fields = {}
    for k in ("title", "body", "tags", "pinned", "disputed"):
        v = getattr(body, k)
        if v is not None:
            fields[k] = v
    if body.area_id is not None:
        fields["area_id"] = body.area_id
    if body.clear_area:
        fields["area_id"] = None
    if body.project_id is not None:
        fields["project_id"] = body.project_id
    if body.clear_project:
        fields["project_id"] = None
    if not fields and body.type is None and body.status is None:
        raise HTTPException(422, "nothing to update")
    n = _engine(store.update_note, note_id, edited_by="owner",
                note_type=body.type, status=body.status, **fields)
    return {"ok": True, "note": n}


@router.post("/note/{note_id}/entries")
def add_entry(note_id: int, body: EntryIn):
    eid = _engine(store.add_note_entry, note_id, body.body)
    return {"id": eid, "note": store.get_note(note_id, db_path=_db())}


def _note_project_slug(n, explicit_slug):
    if explicit_slug:
        return explicit_slug
    if n.get("project"):
        return n["project"]["slug"]
    raise HTTPException(409, "note has no project — pass one to create against")


@router.post("/note/{note_id}/new-task")
def new_task(note_id: int, body: NoteTaskIn):
    """Create-and-link: a NEW task from this note. The note stays a note."""
    n = store.get_note(note_id, db_path=_db())
    if n is None:
        raise HTTPException(404, "no such note")
    slug = _note_project_slug(n, body.project)
    title = (body.title or n["title"]).strip()
    desc = body.description or ("From note #%d: %s" % (note_id, n["title"]))
    tid = _engine(store.create_task, slug, title, desc, "",
                  assignee_profile=body.assignee or store.OWNER_ASSIGNEE)
    if body.ready:
        _engine(store.mark_ready, tid)
    _engine(store.link_note, note_id, "task", tid)
    return {"id": tid, "task": tq.task_detail(_db(), tid),
            "note": store.get_note(note_id, db_path=_db())}


@router.post("/note/{note_id}/new-reminder")
def new_reminder(note_id: int, body: NoteReminderIn):
    """Create-and-link: a NEW reminder (schedule minting an owner task)."""
    n = store.get_note(note_id, db_path=_db())
    if n is None:
        raise HTTPException(404, "no such note")
    slug = _note_project_slug(n, body.project)
    name = (body.name or n["title"]).strip()
    title = (body.title or n["title"]).strip()
    kwargs = {"assignee_profile": store.OWNER_ASSIGNEE,
              "description": "Reminder from note #%d" % note_id,
              "one_shot": body.one_shot}
    if body.zone:
        kwargs["zone"] = body.zone
    sid = _engine(store.create_schedule, name, body.cron, slug, title, **kwargs)
    _engine(store.link_note, note_id, "schedule", sid)
    return {"id": sid, "note": store.get_note(note_id, db_path=_db())}


# ---- librarian proposals (Phase 2a) ------------------------------------
# The OWNER's side of the proposal loop: list, approve, reject, bulk-approve.
# Agents file proposals through `wm note propose-*` (CLI) — they can never
# reach these session-guarded routes, and these routes are the only place a
# proposal turns into an actual Library change.
class RejectIn(BaseModel):
    feedback: str = ""


class ApproveIn(BaseModel):
    # edit-before-approve: the owner's edited payload replaces the librarian's
    # (validated + persisted in the store before anything is applied)
    payload: dict | None = None


@router.get("/proposals")
def proposals(status: str | None = "pending", classification: str | None = None,
              note_id: int | None = None, limit: int = Query(100, ge=1, le=500)):
    rows = _engine(store.list_proposals, status=status or None,
                   classification=classification, note_id=note_id, limit=limit)
    return {"proposals": rows, "counts": store.proposal_counts(db_path=_db())}


@router.get("/proposals/counts")
def proposals_counts():
    return store.proposal_counts(db_path=_db())


@router.post("/proposal/{pid}/approve")
def approve_proposal(pid: int, body: ApproveIn | None = None):
    p = _engine(store.approve_proposal, pid,
                payload_override=body.payload if body else None)
    return {"ok": True, "proposal": p, "counts": store.proposal_counts(db_path=_db())}


@router.post("/proposal/{pid}/reject")
def reject_proposal(pid: int, body: RejectIn):
    p = _engine(store.reject_proposal, pid, feedback=body.feedback)
    return {"ok": True, "proposal": p, "counts": store.proposal_counts(db_path=_db())}


@router.post("/proposals/approve-routine")
def approve_routine():
    res = _engine(store.approve_routine_proposals)
    res["counts"] = store.proposal_counts(db_path=_db())
    return res


@router.post("/brain/triage-now")
def triage_now():
    """Owner's impatience button: fire the librarian ingest schedule right now.

    All gating lives in the store (trigger_heartbeat_schedule), sharing
    fire_due's policy — heartbeat idle and the schedule's overlap setting —
    so a click can never quietly spend a model run.
    """
    return _engine(store.trigger_heartbeat_schedule, "librarian_ingest")


# ---- 2c: attachments (photo + voice) -----------------------------------
#
# Owner-only like everything else in this module. Agents never reach these:
# a photo is opaque to the librarian and audio doubly so, and note content is
# data-not-instructions — bytes from the owner's camera are no exception.

@router.post("/note/{note_id}/attachments")
async def add_attachment(note_id: int, kind: str = Form("photo"),
                         duration_ms: int | None = Form(None),
                         file: UploadFile = File(...)):
    if store.get_note(note_id, db_path=_db()) is None:
        raise HTTPException(404, "no such note")
    if kind not in store.ATTACHMENT_KINDS:
        raise HTTPException(400, "kind must be one of %s" % (store.ATTACHMENT_KINDS,))
    # Bounded read: never let a phone stream an unbounded body into memory.
    data, size = [], 0
    while True:
        chunk = await file.read(1 << 20)
        if not chunk:
            break
        size += len(chunk)
        if size > UPLOAD_MAX_BYTES:
            raise HTTPException(413, "attachments up to %d MB"
                                % (UPLOAD_MAX_BYTES // 1_000_000))
        data.append(chunk)
    if not size:
        raise HTTPException(400, "empty file")
    att = _engine(store.add_attachment, note_id, b"".join(data), kind=kind,
                  mime=file.content_type, orig_name=file.filename,
                  duration_ms=duration_ms)
    return {"ok": True, "attachment": att,
            "note": store.get_note(note_id, db_path=_db())}


@router.get("/attachment/{attachment_id}")
def read_attachment(attachment_id: int):
    a = store.get_attachment(attachment_id, db_path=_db())
    if a is None:
        raise HTTPException(404, "no such attachment")
    try:
        path = store.attachment_path(a["note_id"], a["stored_name"])
    except ValueError:
        raise HTTPException(404, "no such attachment")
    if not os.path.isfile(path):
        # The row outlived its bytes. Say so plainly instead of serving a
        # zero-length body that renders as a silently broken image.
        raise HTTPException(410, "attachment file is missing on disk")
    # inline: these are the owner's own photos and voice memos, and the point
    # is to see/hear them in the page. Content-Disposition names the original.
    name = os.path.basename(a["orig_name"] or a["stored_name"])
    return FileResponse(path, media_type=a["mime"] or "application/octet-stream",
                        headers={"Content-Disposition":
                                 'inline; filename="%s"' % name.replace('"', "")})


@router.delete("/attachment/{attachment_id}")
def remove_attachment(attachment_id: int):
    """Removing an attachment is NOT note deletion — notes stay archive-only."""
    if not store.delete_attachment(attachment_id, db_path=_db()):
        raise HTTPException(404, "no such attachment")
    return {"ok": True}


@router.get("/project/{slug}/notes")
def project_notes(slug: str):
    p = store.get_project(slug=slug, db_path=_db())
    if p is None:
        raise HTTPException(404, "no such project")
    return {"notes": store.notes_for_project(p["id"], db_path=_db())}


@router.get("/task/{task_id}/notes")
def task_notes(task_id: int):
    if store.get_task(task_id, db_path=_db()) is None:
        raise HTTPException(404, "no such task")
    return {"notes": store.notes_for_task(task_id, db_path=_db())}
