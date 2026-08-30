"""Skills browser (Group 6-3). Listing, SKILL.md read/edit/create, enable/disable and the hub
(sources, search, preview, security scan) run through scripts/hermes_bridge.py — i.e. Hermes' own
dashboard router handlers under the profile's HERMES_HOME — so validation, the security scanner and
the `skills.disabled` writer are Hermes' code. Installs, uninstalls, updates, checks and audits are
`hermes [--profile X] skills …` CLI runs as background jobs (argv lists, `--yes`, never `--force`).
The running gateway re-reads `skills.disabled` on its next skill scan (≤30 s cache) — no restart.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend import jobs, memory
from core import wm_store as store

router = APIRouter(prefix="/api/skills")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@#+=-]{0,255}$")
CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _name(n: str) -> str:
    if not NAME_RE.match(n or ""):
        raise HTTPException(400, "bad skill name")
    return n


def _ident(i: str) -> str:
    if not IDENT_RE.match(i or "") or ".." in i:
        raise HTTPException(400, "bad skill identifier")
    return i


def _ok(res: dict, default=400):
    if isinstance(res, dict) and res.get("ok") is False:
        raise HTTPException(int(res.get("status") or default), res.get("error") or "Hermes refused")
    return res


def hermes_argv(profile: str, *args: str) -> list[str]:
    cmd = [store.resolve_hermes()]
    if profile != store.ORCHESTRATOR_AGENT:
        cmd += ["--profile", profile]
    return cmd + list(args)


def _job(profile: str, kind: str, label: str, *args: str):
    prof, home = memory.home_of(profile)
    memory.invalidate(home)
    return {"job": jobs.start(kind, label, hermes_argv(prof, "skills", *args), env=memory.bridge_env(home), cwd=memory.hermes_root()).info(tail_bytes=0)}


# -- installed --------------------------------------------------------------------------
@router.get("")
def list_skills(profile: str | None = None, fresh: int = 0):
    prof, home = memory.home_of(profile)
    if fresh:
        memory.invalidate(home)
    res = memory._cached(("skills", home), 60, lambda: memory.bridge(home, "skills_list"))
    return {"profile": prof, "skills": _ok(res)["skills"]}


@router.get("/read")
def read_skill(profile: str | None = None, name: str = Query(...)):
    _, home = memory.home_of(profile)
    return _ok(memory.bridge(home, "skills_content", {"name": _name(name)}), 404)


class WriteBody(BaseModel):
    profile: str | None = None
    name: str
    content: str


@router.post("/write")
def write_skill(b: WriteBody):
    _, home = memory.home_of(b.profile)
    res = _ok(memory.bridge(home, "skills_update", {"name": _name(b.name), "content": b.content}))
    memory.invalidate(home)
    return res


class CreateBody(BaseModel):
    profile: str | None = None
    name: str
    category: str | None = None
    content: str


@router.post("/create")
def create_skill(b: CreateBody):
    _, home = memory.home_of(b.profile)
    if b.category and not CATEGORY_RE.match(b.category):
        raise HTTPException(400, "bad category")
    res = _ok(memory.bridge(home, "skills_create", {"name": _name(b.name), "category": b.category, "content": b.content}))
    memory.invalidate(home)
    return res


class ToggleBody(BaseModel):
    profile: str | None = None
    name: str
    enabled: bool


@router.post("/toggle")
def toggle_skill(b: ToggleBody):
    _, home = memory.home_of(b.profile)
    res = _ok(memory.bridge(home, "skills_toggle", {"name": _name(b.name), "enabled": b.enabled}))
    memory.invalidate(home)
    return res


# -- hub -------------------------------------------------------------------------------------
@router.get("/hub/sources")
def hub_sources(profile: str | None = None):
    _, home = memory.home_of(profile)
    return _ok(memory._cached(("hub-sources", home), 300, lambda: memory.bridge(home, "hub_sources", timeout=90)), 502)


@router.get("/hub/search")
def hub_search(q: str = Query(..., min_length=1), source: str = "all", limit: int = 20, profile: str | None = None):
    _, home = memory.home_of(profile)
    if not re.match(r"^[A-Za-z0-9_-]{1,32}$", source):
        raise HTTPException(400, "bad source")
    return _ok(memory.bridge(home, "hub_search", {"q": q[:200], "source": source, "limit": max(1, min(50, limit))}, timeout=120), 502)


@router.get("/hub/preview")
def hub_preview(identifier: str = Query(...), profile: str | None = None):
    _, home = memory.home_of(profile)
    return _ok(memory.bridge(home, "hub_preview", {"identifier": _ident(identifier)}, timeout=120), 502)


@router.get("/hub/scan")
def hub_scan(identifier: str = Query(...), profile: str | None = None):
    _, home = memory.home_of(profile)
    return _ok(memory.bridge(home, "hub_scan", {"identifier": _ident(identifier)}, timeout=180), 502)


class InstallBody(BaseModel):
    profile: str | None = None
    identifier: str
    category: str | None = None


@router.post("/hub/install")
def hub_install(b: InstallBody):
    args = ["install", _ident(b.identifier), "--yes"]
    if b.category:
        if not CATEGORY_RE.match(b.category):
            raise HTTPException(400, "bad category")
        args += ["--category", b.category]
    return _job(b.profile or store.ORCHESTRATOR_AGENT, "skill-install", f"Install {b.identifier}", *args)


class NameBody(BaseModel):
    profile: str | None = None
    name: str


@router.post("/hub/uninstall")
def hub_uninstall(b: NameBody):
    return _job(b.profile or store.ORCHESTRATOR_AGENT, "skill-uninstall", f"Uninstall {b.name}", "uninstall", _name(b.name), "--yes")


class UpdateBody(BaseModel):
    profile: str | None = None
    name: str | None = None


@router.post("/hub/update")
def hub_update(b: UpdateBody):
    args = ["update"] + ([_name(b.name)] if b.name else [])
    return _job(b.profile or store.ORCHESTRATOR_AGENT, "skill-update", f"Update {b.name or 'all skills'}", *args)


@router.post("/hub/check")
def hub_check(b: UpdateBody):
    return _job(b.profile or store.ORCHESTRATOR_AGENT, "skill-check", "Check for skill updates", "check", *([_name(b.name)] if b.name else []))


@router.post("/audit")
def audit(b: UpdateBody):
    return _job(b.profile or store.ORCHESTRATOR_AGENT, "skill-audit", "Audit installed hub skills", "audit")
