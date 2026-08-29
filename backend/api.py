"""Read API for Group 1a. Every handler is read-only over hq.db."""
from fastapi import APIRouter, HTTPException, Query

from backend import readers, tasks as tq
from core import wm_store

router = APIRouter(prefix="/api")


def _db():
    return wm_store.DEFAULT_DB_PATH


@router.get("/projects")
def projects(archived: int = 0):
    rows = readers.list_projects(_db(), wm_store.resolve_profiles_dir())
    if not archived:
        rows = [p for p in rows if not p.get("archived")]
    return {"projects": rows}


@router.get("/project/{slug}")
def project(slug: str):
    d = readers.project_detail(_db(), slug, wm_store.resolve_profiles_dir())
    if d is None:
        raise HTTPException(404, "no such project")
    # tasks with human state, newest first (the reader's rows carry engine status only)
    d["tasks"] = tq.list_tasks(_db(), project=slug, archived=True)["tasks"]
    return d


@router.get("/goals")
def goals(project: str | None = None):
    return {"goals": readers.list_goals(_db(), project)}


@router.get("/tasks")
def tasks(project: str | None = None, state: str | None = None, q: str | None = None,
          limit: int | None = Query(None, ge=1, le=500), offset: int = Query(0, ge=0), archived: int = 0):
    return tq.list_tasks(_db(), project=project, state=state, q=q, limit=limit, offset=offset, archived=bool(archived))


@router.get("/task/{task_id}")
def task(task_id: int):
    t = tq.task_detail(_db(), task_id)
    if t is None:
        raise HTTPException(404, "no such task")
    return t
