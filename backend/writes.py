"""Write API. Every route is a thin call into core.wm_store — the engine owns
all policy (release gate, rework path, refusals). ValueError from the engine
becomes a 409 with the engine's own message, never a fabricated success."""
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend import auth as A
from backend import agents as ag, chat, gateways as gw, stop as stopmod, tasks as tq
from core import wm_dispatch, wm_store as store

router = APIRouter(prefix="/api")


def _db():
    return store.DEFAULT_DB_PATH


def _engine(fn, *a, **kw):
    try:
        return fn(*a, db_path=_db(), **kw)
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- auth --------------------------------------------------------------
class Login(BaseModel):
    password: str


def make_auth_routes(sessions: A.Sessions, password: str):
    r = APIRouter(prefix="/api")

    @r.post("/login")
    def login(body: Login, response: Response):
        if not A.check_password(body.password, password):
            raise HTTPException(401, "wrong password")
        tok, csrf = sessions.create()
        response.set_cookie(A.COOKIE, tok, httponly=True, samesite="lax", max_age=A.SESSION_TTL)
        return {"ok": True, "csrf": csrf}

    @r.get("/session")
    def session(request: Request):
        return {"authenticated": True, "csrf": request.state.session["csrf"]}

    @r.post("/logout")
    def logout(request: Request, response: Response):
        sessions.drop(request.cookies.get(A.COOKIE))
        response.delete_cookie(A.COOKIE)
        return {"ok": True}

    return r


# ---- projects ----------------------------------------------------------
class ProjectIn(BaseModel):
    slug: str
    name: str
    description: str = ""
    primary_path: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None


@router.post("/projects")
def create_project(body: ProjectIn):
    import os
    path = body.primary_path.strip()
    if not path:
        root = store.resolve_projects_root() or os.path.join(os.path.dirname(store.hq_home()), "projects")
        path = os.path.join(root, body.slug)
    os.makedirs(path, exist_ok=True)
    pid = _engine(store.create_project, body.slug, body.name, body.description, path)
    return {"id": pid, "slug": body.slug}


@router.post("/project/{slug}")
def update_project(slug: str, body: ProjectPatch):
    return _engine(store.update_project, slug, name=body.name, description=body.description)


@router.post("/project/{slug}/archive")
def archive_project(slug: str, archived: int = 1):
    return _engine(store.set_project_archived, slug, int(bool(archived)))


# ---- goals -------------------------------------------------------------
class GoalIn(BaseModel):
    project: str
    title: str
    description: str = ""
    acceptance_criteria: str = ""


@router.post("/goals")
def create_goal(body: GoalIn):
    return {"id": _engine(store.create_goal, body.project, body.title, body.description, body.acceptance_criteria)}


@router.post("/goal/{goal_id}/plan")
def plan_goal(goal_id: int):
    return _engine(store.request_goal_planning, goal_id)


@router.post("/goal/{goal_id}/planned")
def goal_planned(goal_id: int):
    return _engine(store.set_goal_status, goal_id, "planned")


@router.post("/goal/{goal_id}/release")
def release_goal(goal_id: int):
    return _engine(store.release_goal, goal_id)


@router.post("/goal/{goal_id}/abandon")
def abandon_goal(goal_id: int):
    g = store.get_goal(goal_id, db_path=_db())
    if not g:
        raise HTTPException(404, "no such goal")
    if g["status"] != "planning":
        raise HTTPException(409, "only a goal in planning can be abandoned back to draft (goal is %s)" % g["status"])
    return _engine(store.set_goal_status, goal_id, "draft",
                   detail="goal #%d abandoned: planning -> draft (re-plan)" % goal_id)


# ---- tasks -------------------------------------------------------------
class TaskIn(BaseModel):
    project: str
    title: str
    description: str = ""
    definition_of_done: str = ""
    assignee: str | None = None
    goal_id: int | None = None
    review_policy: str = "none"
    is_code: bool = False
    deps: list[int] = Field(default_factory=list)


class Feedback(BaseModel):
    comment: str


class Note(BaseModel):
    note: str | None = None


class Assign(BaseModel):
    assignee: str


@router.post("/tasks")
def create_task(body: TaskIn):
    tid = _engine(store.create_task, body.project, body.title, body.description, body.definition_of_done,
                  assignee_profile=body.assignee or None, goal_id=body.goal_id,
                  review_policy=body.review_policy, is_code=body.is_code)
    for d in body.deps:
        _engine(store.add_task_dep, tid, d)
    return {"id": tid, "task": tq.task_detail(_db(), tid)}


def _after(tid):
    return {"ok": True, "task": tq.task_detail(_db(), tid)}


@router.post("/task/{tid}/mark-ready")
def mark_ready(tid: int):
    _engine(store.mark_ready, tid); return _after(tid)


@router.post("/task/{tid}/feedback")
def feedback(tid: int, body: Feedback):
    if not body.comment.strip():
        raise HTTPException(422, "comment is required")
    _engine(store.owner_feedback, tid, body.comment.strip()); return _after(tid)


@router.post("/task/{tid}/retry")
def retry(tid: int):
    _engine(store.retry_task, tid); return _after(tid)


@router.post("/task/{tid}/manual")
def manual(tid: int, body: Note | None = None):
    _engine(store.mark_manual, tid, note=(body.note if body else None)); return _after(tid)


@router.post("/task/{tid}/stop")
def stop(tid: int, keep_in_queue: int = 0):
    res = _engine(stopmod.stop_task, tid, keep_in_queue=bool(keep_in_queue))
    out = _after(tid); out["stop"] = res; return out


@router.post("/task/{tid}/assign")
def assign(tid: int, body: Assign):
    _engine(store.assign_task, tid, body.assignee); return _after(tid)


# ---- agents ------------------------------------------------------------
class InstallIn(BaseModel):
    template: str
    force: bool = False


class AskOrchIn(BaseModel):
    template: str
    project: str


@router.post("/agents/install")
def install_agent(body: InstallIn):
    return _engine(ag.install, body.template, force=body.force)


@router.post("/agents/ask-orchestrator")
def ask_orchestrator(body: AskOrchIn):
    return _engine(ag.ask_orchestrator, body.template, body.project)


class GatewayIn(BaseModel):
    enabled: bool


@router.post("/agent/{name}/gateway")
def agent_gateway(name: str, body: GatewayIn):
    return {"gateway": _engine(gw.set_enabled, name, body.enabled)}


# ---- chat --------------------------------------------------------------
class NewSession(BaseModel):
    title: str | None = None


class ChatIn(BaseModel):
    message: str


def _chat(fn, *a, **kw):
    try:
        return fn(*a, db_path=_db(), **kw)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except chat.GatewayError as e:
        import logging
        logging.getLogger("backend.chat").warning("gateway error: %s", e)
        raise HTTPException(502, str(e))


@router.post("/chat/{profile}/sessions")
def chat_new_session(profile: str, body: NewSession | None = None):
    return _chat(chat.create_session, profile, title=(body.title if body else None))


@router.post("/chat/{profile}/{session_id}/stop/{run_id}")
def chat_stop(profile: str, session_id: str, run_id: str):
    return _chat(chat.stop_turn, profile, run_id)


@router.post("/chat/{profile}/{session_id}")
def chat_send(profile: str, session_id: str, body: ChatIn):
    gen = _chat(chat.stream_turn, profile, session_id, body.message)
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- system ------------------------------------------------------------
@router.post("/system/pause")
def pause():
    store.set_paused(True, db_path=_db()); return {"paused": True}


@router.post("/system/resume")
def resume():
    store.set_paused(False, db_path=_db()); return {"paused": False}


@router.post("/system/dispatch")
def dispatch_now():
    return {"summary": wm_dispatch.run_dispatch(db_path=_db())}


@router.get("/system/roster")
def roster():
    return {"assignees": list(store.ASSIGNEE_PROFILES), "review_policies": list(store.REVIEW_POLICIES)}
