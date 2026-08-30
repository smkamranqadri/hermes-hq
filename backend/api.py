"""Read API for Group 1a. Every handler is read-only over hq.db."""
from fastapi import APIRouter, HTTPException, Query

from backend import agents as ag, chat, overview as ov, readers, sysinfo, tasks as tq
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


@router.get("/system/stats")
def system_stats():
    return sysinfo.collect()


@router.get("/overview")
def overview():
    return ov.overview(_db())


@router.get("/activity")
def activity(project: str | None = None, agent: str | None = None, task_id: int | None = None,
             before: float | None = None, limit: int = Query(100, ge=1, le=500)):
    return ov.activity(_db(), project=project, agent=agent, task_id=task_id, before=before, limit=limit)


@router.get("/run/{run_id}/log")
def run_log(run_id: int, offset: int = Query(0, ge=0)):
    return ov.run_log(run_id, offset)


@router.get("/agents")
def agents():
    return {"agents": ag.list_agents(_db()), "templates": ag.list_templates()}


@router.get("/agents/templates")
def agent_templates():
    return {"templates": ag.list_templates()}


@router.get("/agent/{name}")
def agent(name: str):
    try:
        return ag.agent_detail(name, _db())
    except ValueError:
        raise HTTPException(404, "no such agent")


@router.get("/agent/{name}/sessions")
def agent_sessions(name: str, limit: int = Query(100, ge=1, le=500)):
    try:
        return {"sessions": chat.sessions(name, limit, db_path=_db())}
    except ValueError:
        raise HTTPException(404, "no such agent")


@router.get("/project/{slug}/chat-sessions")
def project_chat_sessions(slug: str):
    p = wm_store.get_project(slug=slug, db_path=_db())
    if p is None:
        raise HTTPException(404, "no such project")
    return {"sessions": wm_store.chat_sessions_for_project(p["id"], db_path=_db())}


@router.get("/task/{task_id}/chat-sessions")
def task_chat_sessions(task_id: int):
    if wm_store.get_task(task_id, db_path=_db()) is None:
        raise HTTPException(404, "no such task")
    return {"sessions": wm_store.chat_sessions_for_task(task_id, db_path=_db())}


@router.get("/session/{profile}/{session_id}")
def session(profile: str, session_id: str, limit: int = Query(400, ge=1, le=2000)):
    try:
        d = chat.transcript(profile, session_id, limit, db_path=_db())
    except ValueError as e:
        raise HTTPException(404, str(e))
    if d is None:
        raise HTTPException(404, "no such session")
    return d
