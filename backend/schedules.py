"""Task schedules API (Group 7-1): recurring WM tasks. Storage + firing live in core
(`wm_store` schedules tables, `core/schedule.py` recurrence); the dispatcher fires due
rows every tick. Times are entered/shown in the schedule's IANA zone (default Asia/Karachi)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core import schedule as sch
from core import wm_store as store

router = APIRouter(prefix="/api/schedules")


def _db():
    return store.DEFAULT_DB_PATH


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(404 if "no schedule" in str(e) or "no project" in str(e) else 400, str(e))


def _view(row: dict) -> dict:
    out = dict(row)
    try:
        out["next_fires"] = sch.next_fires(row["cron"], row["zone"], 3)
    except ValueError:
        out["next_fires"] = []
    out["cron_text"] = sch.describe(row["cron"])
    out["open_task"] = row.get("last_task_status") in store.OPEN_TASK_STATUSES if row.get("last_task_id") else False
    return out


@router.get("")
def list_schedules():
    return {"schedules": [_view(r) for r in store.list_schedules(db_path=_db())], "zone": sch.DEFAULT_ZONE}


@router.get("/preview")
def preview(cron: str = Query(...), zone: str = sch.DEFAULT_ZONE):
    try:
        sch.validate(cron, zone)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return {"cron": cron, "zone": zone, "text": sch.describe(cron), "next_fires": sch.next_fires(cron, zone, 3)}


@router.get("/presets")
def presets():
    return {"presets": ["daily", "weekdays", "weekly", "monthly", "hours"], "dow": list(sch.DOW), "zone": sch.DEFAULT_ZONE}


class PresetBody(BaseModel):
    kind: str
    at: str = "09:00"
    dow: str = "mon"
    day: int = 1
    every_hours: int = 6


@router.post("/compile")
def compile_preset(b: PresetBody):
    try:
        cron = sch.preset_to_cron(b.kind, at=b.at, dow=b.dow, day=b.day, every_hours=b.every_hours)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"cron": cron, "text": sch.describe(cron), "next_fires": sch.next_fires(cron)}


@router.get("/next")
def next_up(n: int = 3):
    """The soonest upcoming fires across enabled schedules — the Overview line."""
    rows = [r for r in store.list_schedules(db_path=_db()) if r["enabled"] and r["next_fire_at"]]
    rows.sort(key=lambda r: r["next_fire_at"])
    return {"next": [{"id": r["id"], "name": r["name"], "at": r["next_fire_at"], "project_slug": r["project_slug"]} for r in rows[:n]],
            "total_enabled": len(rows)}


class ScheduleIn(BaseModel):
    name: str
    cron: str
    zone: str = sch.DEFAULT_ZONE
    project: str
    title: str
    description: str = ""
    definition_of_done: str = ""
    assignee: str | None = None
    goal_id: int | None = None
    review_policy: str = "none"
    is_code: bool = False
    overlap: str = "skip"
    enabled: bool = True


@router.post("")
def create(b: ScheduleIn):
    sid = _wrap(store.create_schedule, b.name, b.cron, b.project, b.title, b.description,
                b.definition_of_done, assignee_profile=b.assignee, goal_id=b.goal_id,
                review_policy=b.review_policy, is_code=b.is_code, zone=b.zone,
                overlap=b.overlap, enabled=b.enabled, db_path=_db())
    return _view(store.get_schedule(sid, db_path=_db()))


class ScheduleUpdate(BaseModel):
    name: str | None = None
    cron: str | None = None
    zone: str | None = None
    title: str | None = None
    description: str | None = None
    definition_of_done: str | None = None
    assignee: str | None = None
    clear_assignee: bool = False
    goal_id: int | None = None
    review_policy: str | None = None
    is_code: bool | None = None
    overlap: str | None = None


@router.post("/{sid}")
def update(sid: int, b: ScheduleUpdate):
    fields = {k: v for k, v in b.model_dump().items() if v is not None and k not in ("assignee", "clear_assignee")}
    if b.assignee is not None or b.clear_assignee:
        fields["assignee_profile"] = None if b.clear_assignee else b.assignee
    return _view(_wrap(store.update_schedule, sid, db_path=_db(), **fields))


@router.post("/{sid}/pause")
def pause(sid: int):
    return _view(_wrap(store.update_schedule, sid, db_path=_db(), enabled=False))


@router.post("/{sid}/resume")
def resume(sid: int):
    return _view(_wrap(store.update_schedule, sid, db_path=_db(), enabled=True))


@router.post("/{sid}/run")
def run_now(sid: int):
    tid = _wrap(store.run_schedule_now, sid, db_path=_db())
    return {"task_id": tid}


@router.post("/{sid}/delete")
def delete(sid: int):
    _wrap(store.delete_schedule, sid, db_path=_db())
    return {"ok": True}


@router.get("/{sid}/runs")
def runs(sid: int, limit: int = 50):
    return {"runs": _wrap(store.list_schedule_runs, sid, limit=limit, db_path=_db())}
