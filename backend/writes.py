"""Write API. Every route is a thin call into core.wm_store — the engine owns
all policy (release gate, rework path, refusals). ValueError from the engine
becomes a 409 with the engine's own message, never a fabricated success."""
import os
import subprocess
import urllib.error
import urllib.request

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend import auth as A
from backend import agents as ag, chat, gateways as gw, stop as stopmod, tasks as tq
from core import wm_dispatch, wm_store as store

router = APIRouter(prefix="/api")
KIS_REPO_URL = "https://github.com/smkamranqadri/kis-skill.git"
KIS_BOOTSTRAP_URL = "https://raw.githubusercontent.com/smkamranqadri/kis-skill/main/bootstrap.sh"


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
    initialize_kis: bool = False


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
    if body.initialize_kis:
        try:
            initialize_kis(path)
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            raise HTTPException(502, "KIS initialization failed: %s" % e)
    pid = _engine(store.create_project, body.slug, body.name, body.description, path)
    return {"id": pid, "slug": body.slug}


def initialize_kis(path: str) -> None:
    """Install KIS with its canonical bootstrap script into ``path``."""
    try:
        with urllib.request.urlopen(KIS_BOOTSTRAP_URL, timeout=30) as response:
            bootstrap = response.read()
    except (OSError, urllib.error.URLError) as e:
        raise ValueError("could not download KIS bootstrap: %s" % e) from e
    result = subprocess.run(
        ["bash", "-s", "--", "install", "--repo", KIS_REPO_URL, "--target", path],
        input=bootstrap,
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or b"KIS bootstrap exited unsuccessfully").decode(errors="replace").strip()
        raise ValueError("bootstrap exited with status %s: %s" % (result.returncode, detail[-500:]))


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
    owner_approval: bool = False
    phased: bool = False
    deps: list[int] = Field(default_factory=list)


class Answer(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class Feedback(BaseModel):
    comment: str


class Note(BaseModel):
    note: str | None = None


class Assign(BaseModel):
    assignee: str


@router.post("/tasks")
def create_task(body: TaskIn):
    if body.phased:
        plan_id, build_id = _engine(
            store.create_phased_tasks, body.project, body.title,
            body.description, body.definition_of_done,
            assignee_profile=body.assignee or None, goal_id=body.goal_id,
            owner_approval=body.owner_approval)
        return {"id": plan_id, "build_id": build_id,
                "task": tq.task_detail(_db(), plan_id),
                "build": tq.task_detail(_db(), build_id)}
    tid = _engine(store.create_task, body.project, body.title, body.description, body.definition_of_done,
                  assignee_profile=body.assignee or None, goal_id=body.goal_id,
                  review_policy=body.review_policy, is_code=body.is_code,
                  owner_approval=body.owner_approval)
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


@router.post("/run/{rid}/answer")
def run_answer(rid: int, body: Answer):
    """Deliver an answer to a live run, or answer-and-resume a blocked run."""
    run = store.get_run(rid, db_path=_db())
    if run is None:
        raise HTTPException(404, "no such run")
    if run["status"] == "blocked":
        try:
            resumed = store.resume_blocked_run(rid, body.message, db_path=_db())
        except ValueError as e:
            raise HTTPException(409, str(e))
        try:
            task = store.get_task(resumed["task_id"], db_path=_db())
            project = store.get_project(task["project_id"], db_path=_db()) if task else None
            cwd = (run["workdir"] or
                   (project["primary_path"] if project and project["primary_path"] else None) or
                   os.getcwd())
            brief = store.render_brief(rid, db_path=_db())
            cfg = wm_dispatch._resolve()
            launched = wm_dispatch._launch(
                rid, run["agent_profile"], brief, cwd, cfg, _db(),
                "run_resumed")
            if not launched:
                store.complete_run(resumed["task_id"], status="failed",
                                   error="wrapper spawn failed", run_id=rid,
                                   db_path=_db())
                raise HTTPException(502, "resumed run could not be launched")
            return {"ok": True, "resumed": True,
                    "task": tq.task_detail(_db(), resumed["task_id"])}
        except HTTPException:
            raise
        except Exception as e:
            store.fail_run(rid, run["task_id"],
                           "resumed run setup failed: %s" % e, db_path=_db())
            raise HTTPException(502, "resumed run setup failed")
    if run["status"] != "running":
        raise HTTPException(409, "run %s is not running (status %s) — use task feedback instead" % (rid, run["status"]))
    import time as _t
    line = "[owner %s] %s\n" % (_t.strftime("%Y-%m-%d %H:%M:%S"), body.message.strip())
    import os as _os
    _os.makedirs(_os.path.dirname(store.answer_path(rid)), exist_ok=True)
    with open(store.answer_path(rid), "a", encoding="utf-8") as f:
        f.write(line)
    return {"ok": True, "marked_read": store.mark_run_questions_read(rid, db_path=_db())}


@router.post("/task/{tid}/retry")
def retry(tid: int):
    _engine(store.retry_task, tid); return _after(tid)


@router.post("/task/{tid}/manual")
def manual(tid: int, body: Note | None = None):
    _engine(store.mark_manual, tid, note=(body.note if body else None)); return _after(tid)


@router.post("/task/{tid}/close-owner")
def close_owner(tid: int, body: Note | None = None):
    _engine(store.close_by_owner, tid, note=(body.note if body else None)); return _after(tid)


@router.post("/task/{tid}/approve-plan")
def approve_plan(tid: int, body: Note | None = None):
    """Approve a completed planning task's plan and release its goal."""
    promoted = _engine(store.approve_plan, tid, note=(body.note if body else None))
    out = _after(tid); out["promoted"] = promoted; return out


class TaskEdit(BaseModel):
    description: str | None = None
    definition_of_done: str | None = None
    owner_approval: bool | None = None


@router.post("/task/{tid}/edit")
def task_edit(tid: int, body: TaskEdit):
    _engine(store.edit_task, tid, description=body.description,
            definition_of_done=body.definition_of_done,
            owner_approval=body.owner_approval)
    return _after(tid)


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


class AgentModelIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    confirm: bool = False        # acknowledge Hermes' expensive-model warning


@router.post("/agent/{name}/model")
def agent_model_set(name: str, body: AgentModelIn):
    """Set the profile's DEFAULT model/provider/effort through Hermes' own
    assignment code (bridge model_set). Applies to NEW dispatched runs and new
    sessions; a running chat gateway keeps its loaded default until restart.
    A confirm_required response is NOT an error — the UI re-posts with
    confirm=true after the owner acknowledges the cost warning."""
    from backend import memory, skills
    from core import wm_store as store
    if name not in store.ASSIGNEE_PROFILES:
        raise HTTPException(404, "no such agent")
    if bool(body.model) != bool(body.provider):
        raise HTTPException(400, "provider and model go together")
    if not body.model and not body.effort:
        raise HTTPException(422, "nothing to change")
    if body.effort and body.effort not in chat.EFFORTS:
        raise HTTPException(400, "effort must be one of %s" % ", ".join(chat.EFFORTS))
    prof, home = memory.home_of(name)
    res = memory.bridge(home, "model_set",
                        {"provider": body.provider, "model": body.model,
                         "effort": body.effort, "confirm": body.confirm})
    if isinstance(res, dict) and res.get("confirm_required"):
        return res
    res = skills._ok(res)
    store.log_activity(action="agent_model_set", agent_profile=name,
                       detail="%s/%s effort=%s" % (res.get("provider") or "-",
                                                   res.get("model") or "-",
                                                   res.get("effort") or "-"),
                       db_path=store.DEFAULT_DB_PATH)
    return res


# ---- chat --------------------------------------------------------------
class NewSession(BaseModel):
    title: str | None = None


class ChatIn(BaseModel):
    message: str | list[dict]
    model: str | None = None
    provider: str | None = None
    effort: str | None = None
    fast: bool | None = None


class SteerIn(BaseModel):
    message: str


class SessionUpdate(BaseModel):
    title: str | None = None
    pinned: bool | None = None


class ChatStart(BaseModel):
    profile: str
    project_id: int | None = None
    task_id: int | None = None
    title: str | None = None


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


@router.post("/chat/start")
def chat_start(body: ChatStart):
    return _chat(chat.start_scoped, body.profile, project_id=body.project_id, task_id=body.task_id, title=body.title)


@router.post("/chat/{profile}/{session_id}/update")
def chat_update(profile: str, session_id: str, body: SessionUpdate):
    return _chat(chat.update_session, profile, session_id, title=body.title, pinned=body.pinned)


@router.post("/chat/{profile}/{session_id}/delete")
def chat_delete(profile: str, session_id: str):
    return _chat(chat.delete_session, profile, session_id)


@router.post("/chat/{profile}/{session_id}/stop/{run_id}")
def chat_stop(profile: str, session_id: str, run_id: str):
    return _chat(chat.stop_turn, profile, run_id)


@router.post("/chat/{profile}/{session_id}/steer/{run_id}")
def chat_steer(profile: str, session_id: str, run_id: str, body: SteerIn):
    return _chat(chat.steer_turn, profile, run_id, body.message)


@router.post("/chat/{profile}/{session_id}")
def chat_send(profile: str, session_id: str, body: ChatIn):
    gen = _chat(chat.stream_turn, profile, session_id, body.message, model=body.model, effort=body.effort, fast=body.fast, provider=body.provider)
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- notifications -----------------------------------------------------
class NotifRead(BaseModel):
    ids: list[int] | None = None      # None = all
    source_key: str | None = None     # e.g. chat:<session>:<run> — the device that watched the reply


class NotifIn(BaseModel):
    kind: str
    title: str
    body: str | None = None
    href: str | None = None
    source_key: str | None = None


@router.post("/notifications/read")
def notifications_read(body: NotifRead):
    return {"marked": store.mark_notifications_read(body.ids, db_path=_db(), source_key=body.source_key)}


@router.post("/notifications")
def notifications_add(body: NotifIn):
    """Client-originated events (a chat reply finished while you were elsewhere, an agent asked a question)."""
    if body.kind not in ("chat", "question"):
        raise HTTPException(409, "kind must be chat or question")
    nid = store.add_notification(body.kind, body.title[:160], (body.body or "")[:400] or None, body.href, source_key=body.source_key, db_path=_db())
    if nid:
        from backend import push
        push.push_notifications([nid], db_path=_db())
    return {"id": nid}


# ---- web push ----------------------------------------------------------
class PushSub(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    keys: dict


class PushEndpoint(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)


@router.post("/push/subscribe")
def push_subscribe(body: PushSub, request: Request):
    ok_scheme = body.endpoint.startswith("https://") or body.endpoint.startswith("http://127.0.0.1") or body.endpoint.startswith("http://localhost")
    if not ok_scheme or not isinstance(body.keys.get("p256dh"), str) or not isinstance(body.keys.get("auth"), str):
        raise HTTPException(409, "subscription must have an https endpoint and p256dh/auth keys")
    row = store.add_push_subscription(body.endpoint, {"p256dh": body.keys["p256dh"], "auth": body.keys["auth"]}, request.headers.get("user-agent"), db_path=_db())
    return {"id": row["id"], "subscriptions": len(store.list_push_subscriptions(db_path=_db()))}


@router.post("/push/unsubscribe")
def push_unsubscribe(body: PushEndpoint):
    return {"removed": store.remove_push_subscription(body.endpoint, db_path=_db())}


@router.post("/push/test")
def push_test():
    from backend import push
    return push.send_test(db_path=_db())


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
    return {"assignees": list(store.ASSIGNABLE), "review_policies": list(store.REVIEW_POLICIES)}
