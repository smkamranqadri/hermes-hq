"""Agent jobs (Group 7-2): Hermes cron per profile, through the bridge → Hermes' own cron dashboard
handlers (create/update validation, per-profile job stores, durable run history). The bridge runs under
the ROOT home — these handlers resolve profiles by name (`all` spans every profile; hq's `orchestrator`
is Hermes' `default`). Hermes cron fires from the gateway ticker, so the dispatcher's minute pass runs
`hermes --profile <p> cron tick` for profiles with active jobs whose gateway is off, and turns a job's
error status into an Inbox needs-you row. The three paused legacy WM crons are tagged and cannot be
deleted from here (they are the rollback path)."""
from __future__ import annotations

import re
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend import gateways, jobs, memory, skills
from core import wm_store as store

router = APIRouter(prefix="/api/cron")

LEGACY_WM_IDS = {"dfe30ff9e8bf": "wm-dispatch", "040334fe79ae": "wm completion watchdog", "b84db989076d": "wm-planning-pickup"}
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
EVERY_UNITS = {"minutes": "*/{n} * * * *", "hours": "0 */{n} * * *"}


def _root_home():
    return store.hermes_root_home()


def _hermes_profile(profile: str | None) -> str:
    p = (profile or "all").strip() or "all"
    if p in ("orchestrator", "default"):
        return "default"
    if p == "all":
        return "all"
    if not ID_RE.fullmatch(p):
        raise HTTPException(400, "bad profile")
    return p


def _hq_profile(name: str) -> str:
    return store.ORCHESTRATOR_AGENT if name == "default" else name


def _jid(i: str) -> str:
    if not ID_RE.fullmatch(i or ""):
        raise HTTPException(400, "bad job id")
    return i


def _bridge(op, body, timeout=90):
    return skills._ok(memory.bridge(_root_home(), op, body, timeout=timeout))


def _tag(job: dict) -> dict:
    job["legacy_wm"] = job.get("id") in LEGACY_WM_IDS
    job["is_script"] = bool(job.get("no_agent"))
    job["profile"] = _hq_profile(str(job.get("profile") or "default"))
    return job


@router.get("/jobs")
def list_jobs(profile: str | None = None, fresh: int = 0):
    if fresh:
        memory.invalidate(_root_home())
    res = memory._cached(("cron", _root_home(), _hermes_profile(profile)), 30,
                         lambda: memory.bridge(_root_home(), "cron_list", {"profile": _hermes_profile(profile)}))
    return {"jobs": [_tag(dict(j)) for j in skills._ok(res)["jobs"]]}


@router.get("/jobs/{jid}")
def get_job(jid: str, profile: str | None = None):
    return _tag(_bridge("cron_get", {"id": _jid(jid), "profile": None if not profile or profile == "all" else _hermes_profile(profile)}))


@router.get("/jobs/{jid}/runs")
def job_runs(jid: str, profile: str | None = None, limit: int = 20):
    return _bridge("cron_runs", {"id": _jid(jid), "profile": None if not profile or profile == "all" else _hermes_profile(profile), "limit": max(1, min(100, limit))})


@router.get("/targets")
def targets():
    return skills._ok(memory._cached(("cron-targets", _root_home()), 300, lambda: memory.bridge(_root_home(), "cron_targets", {})))


class Every(BaseModel):
    n: int
    unit: str  # minutes | hours


class JobIn(BaseModel):
    profile: str | None = None
    name: str = ""
    prompt: str = ""
    schedule: str | None = None
    every: Every | None = None
    deliver: str = "local"
    skills: list[str] | None = None
    model: str | None = None
    provider: str | None = None
    workdir: str | None = None
    enabled_toolsets: list[str] | None = None


def _schedule_of(b: JobIn) -> str:
    if b.schedule:
        return b.schedule
    if b.every:
        if b.every.unit not in EVERY_UNITS or not 1 <= b.every.n <= (59 if b.every.unit == "minutes" else 23):
            raise HTTPException(400, "every must be 1-59 minutes or 1-23 hours")
        return EVERY_UNITS[b.every.unit].format(n=b.every.n)
    raise HTTPException(400, "schedule or every is required")


@router.post("/jobs")
def create_job(b: JobIn):
    p = _hermes_profile(b.profile or "orchestrator")
    if p == "all":
        raise HTTPException(400, "pick a profile")
    body = {"profile": p, "schedule": _schedule_of(b), "name": b.name, "prompt": b.prompt, "deliver": b.deliver,
            "skills": b.skills, "model": b.model, "provider": b.provider, "workdir": b.workdir,
            "enabled_toolsets": b.enabled_toolsets}
    res = _bridge("cron_create", body)
    memory.invalidate(_root_home())
    return _tag(dict(res))


class JobUpdate(BaseModel):
    profile: str | None = None
    updates: dict


@router.post("/jobs/{jid}/update")
def update_job(jid: str, b: JobUpdate):
    allowed = {"name", "prompt", "schedule", "deliver", "skills", "model", "provider", "workdir", "enabled_toolsets"}
    bad = set(b.updates) - allowed
    if bad:
        raise HTTPException(400, "cannot update %s" % ", ".join(sorted(bad)))
    res = _bridge("cron_update", {"id": _jid(jid), "profile": None if not b.profile or b.profile == "all" else _hermes_profile(b.profile), "updates": b.updates})
    memory.invalidate(_root_home())
    return _tag(dict(res))


class ProfileBody(BaseModel):
    profile: str | None = None


def _simple(op):
    def run(jid: str, b: ProfileBody):
        if op == "cron_delete" and jid in LEGACY_WM_IDS:
            raise HTTPException(403, "'%s' is the legacy Work Manager rollback path — it stays paused, delete it from the Hermes CLI if you really mean it" % LEGACY_WM_IDS[jid])
        res = _bridge(op, {"id": _jid(jid), "profile": None if not b.profile or b.profile == "all" else _hermes_profile(b.profile)})
        memory.invalidate(_root_home())
        return res if isinstance(res, dict) else {"ok": True}
    return run


router.post("/jobs/{jid}/pause")(_simple("cron_pause"))
router.post("/jobs/{jid}/resume")(_simple("cron_resume"))
router.post("/jobs/{jid}/trigger")(_simple("cron_trigger"))
router.post("/jobs/{jid}/delete")(_simple("cron_delete"))


# -- dispatcher minute pass -------------------------------------------------------------------
_last_pass = 0.0
_ticking: dict[str, str] = {}          # profile → running tick job id
_notified: set[tuple] = set()


def minute_pass(now=None, db_path=None):
    """Once a minute from the dispatcher: tick cron for off-gateway profiles with active jobs,
    and surface error statuses as Inbox rows. Never raises."""
    global _last_pass
    now = now or time.time()
    if now - _last_pass < 60:
        return None
    _last_pass = now
    try:
        rows = memory.bridge(_root_home(), "cron_list", {"profile": "all"}, timeout=60)
        if isinstance(rows, dict) and rows.get("ok") is False:
            return None
        out = {"ticked": [], "errors": 0}
        by_profile: dict[str, list] = {}
        for j in rows["jobs"]:
            by_profile.setdefault(str(j.get("profile") or "default"), []).append(j)
        for pname, pjobs in by_profile.items():
            hq = _hq_profile(pname)
            active = [j for j in pjobs if j.get("enabled") and j.get("state") != "paused"]
            # error statuses → needs_you (idempotent per run timestamp)
            for j in pjobs:
                if j.get("last_status") == "error" and j.get("last_run_at"):
                    key = (j["id"], j["last_run_at"])
                    if key not in _notified:
                        _notified.add(key)
                        out["errors"] += 1
                        store.add_notification("needs_you", "Agent job '%s' failed" % (j.get("name") or j["id"]),
                                               body=(j.get("last_error") or "")[:300], href="/schedules?tab=agents",
                                               source_key="cron:%s:%s:%s" % (pname, j["id"], j["last_run_at"]), db_path=db_path)
            if not active or hq == store.ORCHESTRATOR_AGENT:
                continue          # the root gateway is never idle-stopped by hq
            if gateways.healthy(hq):
                continue
            tick_id = _ticking.get(hq)
            if tick_id and jobs.JOBS.get(tick_id) and jobs.JOBS[tick_id].status == "running":
                continue
            job = jobs.start("cron-tick", "Cron tick for %s (gateway off)" % hq,
                             skills.hermes_argv(hq, "cron", "tick"),
                             env=jobs.child_env(HERMES_HOME=str(memory.home_of(hq)[1])), cwd=memory.hermes_root(),
                             timeout=600)
            _ticking[hq] = job.id
            out["ticked"].append(hq)
        return out
    except Exception:
        import logging
        logging.getLogger("hermes-hq.cron").exception("cron minute pass failed")
        return None
