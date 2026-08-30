"""MCP servers, toolsets and the Nous MCP catalog (Group 6-4).

Servers/catalog go through scripts/hermes_bridge.py → Hermes' own MCP router handlers under the
profile's HERMES_HOME (validation, bearer tokens to the profile .env as an env reference, config.yaml
writer, the probe that lists tools). Toolsets come from the profile's running gateway (`/v1/toolsets`);
when the gateway is off the response says so instead of guessing from config. Git-bootstrap catalog
entries install through a `hermes --profile <p> mcp install <name>` job. OAuth: no popup flow here —
the UI shows the `hermes mcp login <name>` command to run on the host. Enable/disable and new servers
take effect on the next session / gateway restart (Hermes reads mcp_servers at startup).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend import gateways, jobs, memory, skills
from core import wm_store as store

router = APIRouter(prefix="/api/mcp")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
ENV_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
TEST_CACHE: dict[tuple, dict] = {}       # (home, name) → last probe result + ts


def _name(n: str) -> str:
    if not NAME_RE.fullmatch(n or ""):
        raise HTTPException(400, "bad server name")
    return n


def _env_map(env: dict) -> dict[str, str]:
    out = {}
    for k, v in (env or {}).items():
        if not ENV_KEY_RE.fullmatch(str(k)):
            raise HTTPException(400, f"bad env key {k!r}")
        out[str(k)] = str(v)
    return out


# -- toolsets (running gateway) ------------------------------------------------------------------
@router.get("/toolsets")
def toolsets(profile: str | None = None):
    prof, _ = memory.home_of(profile)
    port, key = gateways.credentials(prof)
    if not port or not key or not gateways.healthy(prof):
        return {"profile": prof, "gateway": "off", "toolsets": []}
    req = urllib.request.Request("http://127.0.0.1:%d/v1/toolsets" % port, headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise HTTPException(502, f"gateway /v1/toolsets failed: {e}")
    return {"profile": prof, "gateway": "on", "platform": data.get("platform"), "toolsets": data.get("data") or []}


# -- servers ----------------------------------------------------------------------------------------
@router.get("")
def list_servers(profile: str | None = None, fresh: int = 0):
    prof, home = memory.home_of(profile)
    if fresh:
        memory.invalidate(home)
    res = skills._ok(memory._cached(("mcp", home), 60, lambda: memory.bridge(home, "mcp_list")))
    servers = res.get("servers") or []
    for s in servers:
        t = TEST_CACHE.get((home, s["name"]))
        s["last_test"] = t
        s["has_token"] = s.get("auth") == "header"
        s["login_command"] = f"hermes --profile {store.hermes_profile_arg(prof)} mcp login {s['name']}" if s.get("auth") == "oauth" else None
    return {"profile": prof, "servers": servers}


class AddBody(BaseModel):
    profile: str | None = None
    name: str
    transport: str = "http"          # http | stdio
    url: str | None = None
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    auth: str = "none"               # none | bearer | oauth
    bearer_token: str | None = None


@router.post("/add")
def add_server(b: AddBody):
    _, home = memory.home_of(b.profile)
    if b.transport not in ("http", "stdio"):
        raise HTTPException(400, "transport must be http or stdio")
    if b.auth not in ("none", "bearer", "oauth"):
        raise HTTPException(400, "auth must be none, bearer or oauth")
    body = {"name": _name(b.name), "auth": {"bearer": "header"}.get(b.auth, b.auth), "bearer_token": b.bearer_token or None}
    if b.transport == "http":
        if not (b.url or "").startswith(("http://", "https://")):
            raise HTTPException(400, "http servers need an http(s) URL")
        body["url"] = b.url
    else:
        if not b.command:
            raise HTTPException(400, "stdio servers need a command")
        body.update(command=b.command, args=[str(a) for a in b.args], env=_env_map(b.env))
    res = skills._ok(memory.bridge(home, "mcp_add", body))
    memory.invalidate(home)
    return res


class NameBody(BaseModel):
    profile: str | None = None
    name: str


@router.post("/remove")
def remove_server(b: NameBody):
    _, home = memory.home_of(b.profile)
    res = skills._ok(memory.bridge(home, "mcp_remove", {"name": _name(b.name)}), 404)
    memory.invalidate(home); TEST_CACHE.pop((home, b.name), None)
    return res


class ToggleBody(BaseModel):
    profile: str | None = None
    name: str
    enabled: bool


@router.post("/toggle")
def toggle_server(b: ToggleBody):
    _, home = memory.home_of(b.profile)
    res = skills._ok(memory.bridge(home, "mcp_enabled", {"name": _name(b.name), "enabled": b.enabled}), 404)
    memory.invalidate(home)
    return res


@router.post("/test")
def test_server(b: NameBody):
    _, home = memory.home_of(b.profile)
    res = memory.bridge(home, "mcp_test", {"name": _name(b.name)}, timeout=90)
    if isinstance(res, dict) and res.get("status") == 404:
        raise HTTPException(404, res.get("error"))
    out = {"ok": bool(res.get("ok")), "error": res.get("error"), "tools": res.get("tools") or [], "prompts": res.get("prompts", 0),
           "resources": res.get("resources", 0), "ts": time.time()}
    TEST_CACHE[(home, b.name)] = out
    return out


# -- catalog -------------------------------------------------------------------------------------------
@router.get("/catalog")
def catalog(profile: str | None = None, fresh: int = 0):
    _, home = memory.home_of(profile)
    if fresh:
        memory.invalidate(home)
    return skills._ok(memory._cached(("mcp-catalog", home), 120, lambda: memory.bridge(home, "mcp_catalog", timeout=90)))


class CatalogInstallBody(BaseModel):
    profile: str | None = None
    name: str
    env: dict[str, str] = {}
    enable: bool = True


@router.post("/catalog/install")
def catalog_install(b: CatalogInstallBody):
    prof, home = memory.home_of(b.profile)
    res = skills._ok(memory.bridge(home, "mcp_catalog_install", {"name": _name(b.name), "env": _env_map(b.env), "enable": b.enable}, timeout=120), 404)
    memory.invalidate(home)
    if res.get("needs_cli_install"):
        job = jobs.start("mcp-install", f"Install MCP {b.name} for {prof}", skills.hermes_argv(prof, "mcp", "install", b.name),
                         env=memory.bridge_env(home), cwd=memory.hermes_root(), on_done=lambda j: memory.invalidate(home))
        return {"ok": True, "name": b.name, "background": True, "job": job.info(tail_bytes=0)}
    return res
