"""Project files (Group 5): browse/edit inside two kinds of roots.

Roots (owner decision 2026-08-30, plan `kis/intent/Group5Plan.md`):
  projects        -> WM_PROJECTS_ROOT or <hq home>/../projects
  project:<slug>  -> that project's primary_path
Every path is resolved with realpath and must stay inside the selected root;
symlinks that escape are refused (403), never followed. Rename/delete refuse a
path that IS some project's primary_path (the dispatcher hands it to runs).
"""
import mimetypes
import os
import shutil
import stat as _st
import tempfile
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.readers import connect_ro, _fetchall, _fetchone, _parse_paths
from core import wm_store as store

router = APIRouter(prefix="/api/files")

# Directories filtered out of listings unless hidden=1 (size/noise).
EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".tox",
                ".idea", ".vscode", "dist", "build", "target"}
LIST_MAX_ENTRIES = 5000
TEXT_MAX_BYTES = 1_000_000          # editor cap (plan 5-1)
UPLOAD_MAX_BYTES = 20_000_000       # per file
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}


def _db():
    return store.DEFAULT_DB_PATH


def projects_root():
    return store.resolve_projects_root() or os.path.join(os.path.dirname(store.hq_home()), "projects")


# ---- roots + containment ---------------------------------------------------

def _projects():
    con = connect_ro(_db())
    try:
        return _fetchall(con, "SELECT slug, name, primary_path, archived FROM projects ORDER BY name")
    finally:
        con.close()


def _root_dir(root: str) -> tuple[str, str]:
    """(realpath of the root, human label). 404 for unknown roots."""
    if root == "projects":
        return os.path.realpath(projects_root()), "Projects"
    if root.startswith("project:"):
        slug = root[len("project:"):]
        con = connect_ro(_db())
        try:
            p = _fetchone(con, "SELECT name, primary_path FROM projects WHERE slug=?", (slug,))
        finally:
            con.close()
        if p is None or not p["primary_path"]:
            raise HTTPException(404, "unknown project root")
        return os.path.realpath(p["primary_path"]), p["name"]
    raise HTTPException(404, "unknown root")


def _inside(root_real: str, target: str) -> bool:
    rp = os.path.realpath(target)
    return rp == root_real or rp.startswith(os.path.join(root_real, ""))


def _resolve(root: str, rel: str, must_exist: bool = True) -> tuple[str, str]:
    """Return (root_real, absolute path) for a root-relative path, or raise.
    Absolute inputs, NUL bytes and anything whose realpath (including a
    trailing symlink) leaves the root are refused with 403."""
    root_real, _ = _root_dir(root)
    rel = (rel or "").replace("\\", "/")
    if "\x00" in rel or rel.startswith("/"):
        raise HTTPException(403, "outside root")
    rel = rel.strip("/")
    abs_path = os.path.normpath(os.path.join(root_real, rel)) if rel else root_real
    if not _inside(root_real, abs_path):
        raise HTTPException(403, "outside root")
    # a symlink as the final component must also land inside the root
    if os.path.islink(abs_path) and not _inside(root_real, os.path.realpath(abs_path)):
        raise HTTPException(403, "outside root")
    if must_exist and not os.path.lexists(abs_path):
        raise HTTPException(404, "not found")
    return root_real, abs_path


def _rel(root_real: str, abs_path: str) -> str:
    r = os.path.relpath(abs_path, root_real)
    return "" if r == "." else r.replace(os.sep, "/")


def _is_primary_path(abs_path: str) -> bool:
    """True when abs_path is, or contains, some project's primary_path."""
    rp = os.path.realpath(abs_path)
    for p in _projects():
        if p["primary_path"] and _inside(rp, p["primary_path"]):
            return True
    return False


def _safe_name(name: str) -> str:
    return name.encode("utf-8", "replace").decode("utf-8")


def _write_atomic(dst: str, chunks) -> int:
    """Write chunks to a fresh O_EXCL temp file in dst's folder, then os.replace.
    Never follows a pre-existing symlink (review 2026-08-30 #1); cleans up on error."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst), prefix=".hq-tmp-")
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in chunks:
                size += len(chunk)
                if size > UPLOAD_MAX_BYTES:
                    raise HTTPException(413, f"uploads up to {UPLOAD_MAX_BYTES // 1_000_000} MB")
                f.write(chunk)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return size


def _entry(abs_path: str, name: str):
    try:
        st = os.lstat(abs_path)
    except OSError:
        return None
    is_link = _st.S_ISLNK(st.st_mode)
    is_dir = os.path.isdir(abs_path)
    e = {"name": _safe_name(name), "is_dir": is_dir, "mtime": st.st_mtime,
         "size": None if is_dir else (os.stat(abs_path).st_size if is_link and os.path.exists(abs_path) else st.st_size)}
    if is_link:
        e["symlink"] = True
    if name.startswith(".") or (is_dir and name in EXCLUDE_DIRS):
        e["hidden"] = True
    return e


# ---- read ------------------------------------------------------------------

@router.get("/roots")
def roots():
    pr = projects_root()
    out = [{"root": "projects", "label": "Projects", "path": pr, "exists": os.path.isdir(pr)}]
    for p in _projects():
        if not p["primary_path"]:
            continue
        out.append({"root": "project:" + p["slug"], "label": p["name"], "slug": p["slug"],
                    "path": p["primary_path"], "exists": os.path.isdir(p["primary_path"]),
                    "archived": bool(p["archived"])})
    return {"roots": out}


@router.get("/list")
def list_dir(root: str, path: str = "", hidden: int = 0):
    root_real, abs_dir = _resolve(root, path)
    if not os.path.isdir(abs_dir):
        raise HTTPException(400, "not a directory")
    entries, truncated = [], False
    try:
        with os.scandir(abs_dir) as it:
            for de in it:
                e = _entry(de.path, de.name)
                if e is None:
                    continue
                if e.get("hidden") and not hidden:
                    continue
                # an entry whose symlink escapes the root is shown but flagged
                if e.get("symlink") and not _inside(root_real, os.path.realpath(de.path)):
                    e["outside"] = True
                entries.append(e)
                if len(entries) >= LIST_MAX_ENTRIES:
                    truncated = True
                    break
    except PermissionError:
        raise HTTPException(403, "permission denied")
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return {"root": root, "path": _rel(root_real, abs_dir), "entries": entries, "truncated": truncated}


def _kind(abs_path: str, head: bytes) -> str:
    ext = os.path.splitext(abs_path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if b"\x00" in head:
        return "binary"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


@router.get("/read")
def read_file(root: str, path: str):
    root_real, abs_path = _resolve(root, path)
    if os.path.isdir(abs_path):
        raise HTTPException(400, "is a directory")
    if os.path.islink(abs_path) and not _inside(root_real, os.path.realpath(abs_path)):
        raise HTTPException(403, "outside root")
    if not os.path.isfile(abs_path):
        raise HTTPException(404, "not found")
    st = os.stat(abs_path)
    with open(abs_path, "rb") as f:
        head = f.read(8192)
    kind = _kind(abs_path, head)
    out = {"root": root, "path": _rel(root_real, abs_path), "name": os.path.basename(abs_path),
           "kind": kind, "size": st.st_size, "mtime": st.st_mtime}
    if kind == "text":
        if st.st_size > TEXT_MAX_BYTES:
            out["too_large"] = True
        else:
            with open(abs_path, "rb") as f:
                data = f.read(TEXT_MAX_BYTES + 1)
            try:
                out["content"] = data.decode("utf-8")
            except UnicodeDecodeError:
                out["kind"] = "binary"
    return out


@router.get("/raw")
def raw_file(root: str, path: str, download: int = 0):
    root_real, abs_path = _resolve(root, path)
    if os.path.isdir(abs_path):
        raise HTTPException(400, "is a directory")
    if os.path.islink(abs_path) and not _inside(root_real, os.path.realpath(abs_path)):
        raise HTTPException(403, "outside root")
    if not os.path.isfile(abs_path):
        raise HTTPException(404, "not found")
    name = os.path.basename(abs_path)
    media = mimetypes.guess_type(name)[0] or "application/octet-stream"
    # active content from a checkout must never run on the app origin (review #2)
    if media in ("text/html", "application/xhtml+xml", "image/svg+xml"):
        download = 1
    disp = "attachment" if download else "inline"
    headers = {"Content-Disposition": f"{disp}; filename*=UTF-8''{quote(_safe_name(name))}",
               "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "sandbox; default-src 'none'"}
    return FileResponse(abs_path, media_type=media, headers=headers)


# ---- write -----------------------------------------------------------------

class WriteIn(BaseModel):
    root: str
    path: str
    content: str
    mtime: float | None = None      # the mtime the client loaded; None = create new
    force: bool = False


@router.post("/write")
def write_file(body: WriteIn):
    root_real, abs_path = _resolve(body.root, body.path, must_exist=False)
    if abs_path == root_real or os.path.isdir(abs_path):
        raise HTTPException(400, "is a directory")
    data = body.content.encode("utf-8")
    if len(data) > TEXT_MAX_BYTES:
        raise HTTPException(413, f"text files up to {TEXT_MAX_BYTES // 1_000_000} MB")
    exists = os.path.lexists(abs_path)
    if not body.force:
        if exists and body.mtime is None:
            raise HTTPException(409, "already exists")
        if not exists and body.mtime is not None:
            raise HTTPException(409, "deleted on disk")
        if exists and abs(os.stat(abs_path).st_mtime - body.mtime) > 1e-6:
            raise HTTPException(409, "changed on disk")
    parent = os.path.dirname(abs_path)
    if not os.path.isdir(parent):
        raise HTTPException(404, "parent folder missing")
    if os.path.islink(abs_path):   # write through an in-root symlink (target already contained)
        abs_path = os.path.realpath(abs_path)
    _write_atomic(abs_path, [data])
    st = os.stat(abs_path)
    return {"ok": True, "path": _rel(root_real, abs_path), "size": st.st_size, "mtime": st.st_mtime, "created": not exists}


class PathIn(BaseModel):
    root: str
    path: str


@router.post("/mkdir")
def mkdir(body: PathIn):
    root_real, abs_path = _resolve(body.root, body.path, must_exist=False)
    if abs_path == root_real:
        raise HTTPException(400, "bad name")
    if os.path.lexists(abs_path):
        raise HTTPException(409, "already exists")
    try:
        os.makedirs(abs_path)
    except OSError as e:
        raise HTTPException(409, f"cannot create: {e.strerror}")
    return {"ok": True, "path": _rel(root_real, abs_path)}


class RenameIn(BaseModel):
    root: str
    path: str
    to: str   # new root-relative path (a bare name renames in place)


@router.post("/rename")
def rename(body: RenameIn):
    root_real, src = _resolve(body.root, body.path)
    if src == root_real:
        raise HTTPException(400, "cannot rename the root")
    if _is_primary_path(src):
        raise HTTPException(409, "this folder is a project's primary path")
    to = body.to.strip()
    if "/" not in to:
        to = _rel(root_real, os.path.join(os.path.dirname(src), to))
    _, dst = _resolve(body.root, to, must_exist=False)
    if dst == root_real or os.path.lexists(dst):
        raise HTTPException(409, "target exists")
    if not os.path.isdir(os.path.dirname(dst)):
        raise HTTPException(404, "target folder missing")
    try:
        os.rename(src, dst)
    except OSError as e:
        raise HTTPException(409, f"cannot rename: {e.strerror}")
    return {"ok": True, "path": _rel(root_real, dst)}


class DeleteIn(BaseModel):
    root: str
    path: str
    recursive: bool = False


@router.post("/delete")
def delete(body: DeleteIn):
    root_real, abs_path = _resolve(body.root, body.path)
    if abs_path == root_real:
        raise HTTPException(400, "cannot delete the root")
    if _is_primary_path(abs_path):
        raise HTTPException(409, "this folder is a project's primary path")
    if os.path.islink(abs_path) or not os.path.isdir(abs_path):
        os.remove(abs_path)
    elif body.recursive:
        shutil.rmtree(abs_path)
    else:
        try:
            os.rmdir(abs_path)
        except OSError:
            raise HTTPException(409, "folder not empty")
    return {"ok": True}


@router.post("/upload")
async def upload(root: str = Form(...), path: str = Form(""), files: list[UploadFile] = File(...)):
    root_real, abs_dir = _resolve(root, path)
    if not os.path.isdir(abs_dir):
        raise HTTPException(400, "not a directory")
    saved = []
    for up in files:
        name = os.path.basename((up.filename or "").replace("\\", "/"))
        if not name or name in (".", ".."):
            raise HTTPException(400, "bad file name")
        _, dst = _resolve(root, _rel(root_real, os.path.join(abs_dir, name)), must_exist=False)
        if os.path.lexists(dst):
            raise HTTPException(409, {"error": f"{name} already exists", "saved": saved})
        chunks = []
        size = 0
        while True:
            chunk = await up.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > UPLOAD_MAX_BYTES:
                raise HTTPException(413, {"error": f"{name}: uploads up to {UPLOAD_MAX_BYTES // 1_000_000} MB", "saved": saved})
            chunks.append(chunk)
        _write_atomic(dst, chunks)
        saved.append({"name": _safe_name(name), "path": _rel(root_real, dst), "size": size})
    return {"ok": True, "files": saved}


# ---- artifacts (result_paths of a project's runs/tasks) --------------------

artifacts_router = APIRouter(prefix="/api")


@artifacts_router.get("/project/{slug}/artifacts")
def project_artifacts(slug: str):
    con = connect_ro(_db())
    try:
        p = _fetchone(con, "SELECT id, primary_path FROM projects WHERE slug=?", (slug,))
        if p is None:
            raise HTTPException(404, "unknown project")
        primary = os.path.realpath(p["primary_path"]) if p["primary_path"] else ""
        rows = _fetchall(con,
            "SELECT t.id AS task_id, t.title AS task_title, t.result_paths AS task_paths, "
            "r.id AS run_id, r.agent_profile AS agent, r.session_id AS session_id, "
            "r.result_paths AS run_paths FROM runs r LEFT JOIN tasks t ON t.id = r.task_id "
            "WHERE t.project_id=? ORDER BY r.id ASC", (p["id"],))
    finally:
        con.close()
    out, seen = [], set()
    for r in rows:
        for path in _parse_paths(r.get("run_paths")) + _parse_paths(r.get("task_paths")):
            if not path or path in seen:
                continue
            seen.add(path)
            if not os.path.isabs(path) and primary:
                path = os.path.join(primary, path)
            if not os.path.lexists(path):
                continue
            is_dir = os.path.isdir(path)
            inside = bool(primary) and _inside(primary, path)
            out.append({"path": path, "name": os.path.basename(path.rstrip("/")), "is_dir": is_dir,
                        "size": None if is_dir else os.path.getsize(path),
                        "inside_primary": inside,
                        "rel": _rel(primary, os.path.realpath(path)) if inside else None,
                        "task_id": r["task_id"], "task_title": r["task_title"],
                        "agent": r["agent"], "run_id": r["run_id"], "session_id": r["session_id"]})
    return {"slug": slug, "primary_path": p["primary_path"], "artifacts": out}
