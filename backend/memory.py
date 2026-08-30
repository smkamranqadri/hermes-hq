"""Memory browser (Group 6-2): the built-in memory files of every profile, Hermes memory providers
and the learning graph.

Files live in <profile home>/memories/: MEMORY.md, USER.md and any other top-level *.md — names are
allow-listed, never paths. Hermes takes an flock on <file>.lock while it writes (the .lock files exist
permanently), so a write here takes the same lock non-blocking and answers 423 while the agent holds
it; mtime mismatch answers 409 unless `force`. Providers / graph / limits go through
scripts/hermes_bridge.py under Hermes' own venv with HERMES_HOME = the profile, so Hermes' rules
apply verbatim (secrets to the profile .env, readiness gate, config writer).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend import jobs
from core import wm_store as store

router = APIRouter(prefix="/api/memory")

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.md")
PROVIDER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
SEARCH_CAP = 200
MAX_BYTES = 1_000_000
ORCH = store.ORCHESTRATOR_AGENT
ENTRY_SEP = "\n§\n"


# -- profiles ---------------------------------------------------------------------
def profiles():
    """[(name, home)] — orchestrator first, then the specialist profiles that exist on disk."""
    out = [(ORCH, store.hermes_root_home())]
    pdir = store.resolve_profiles_dir()
    if pdir and os.path.isdir(pdir):
        for n in sorted(os.listdir(pdir)):
            if PROVIDER_RE.fullmatch(n) and os.path.isdir(os.path.join(pdir, n)) and n != ORCH:
                out.append((n, store.profile_hermes_home(pdir, n)))
    return out


def home_of(profile: str | None) -> tuple[str, str]:
    p = profile or ORCH
    for n, h in profiles():
        if n == p:
            if not os.path.isdir(h):
                raise HTTPException(404, f"profile {p} has no home directory")
            return n, h
    raise HTTPException(404, f"unknown profile {p}")


def mem_dir(home: str) -> str:
    return os.path.join(home, "memories")


def file_path(home: str, name: str) -> str:
    if not NAME_RE.fullmatch(name or "") or "/" in name:
        raise HTTPException(400, "bad memory file name")
    return os.path.join(mem_dir(home), name)


# -- limits (profile config.yaml, root fallback, Hermes defaults) ---------------------
def limits(home: str) -> dict:
    lim = {"memory": 2200, "user": 1375}
    for cfg in (os.path.join(store.hermes_root_home(), "config.yaml"), os.path.join(home, "config.yaml")):
        try:
            import yaml
            with open(cfg, encoding="utf-8") as f:
                mem = (yaml.safe_load(f) or {}).get("memory") or {}
            if mem.get("memory_char_limit"):
                lim["memory"] = int(mem["memory_char_limit"])
            if mem.get("user_char_limit"):
                lim["user"] = int(mem["user_char_limit"])
        except Exception:
            continue
    return lim


# -- files --------------------------------------------------------------------------
def _entry(name: str, path: str, lim: dict):
    st = os.stat(path)
    chars = entries = None
    if st.st_size <= MAX_BYTES:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                txt = f.read()
            chars = len(txt)
            entries = len([c for c in txt.split(ENTRY_SEP) if c.strip()]) if txt.strip() else 0
        except OSError:
            pass
    key = "memory" if name == "MEMORY.md" else "user" if name == "USER.md" else None
    return {"name": name, "size": st.st_size, "mtime": st.st_mtime, "chars": chars, "entries": entries,
            "limit": lim.get(key) if key else None, "kind": key or "other"}


@router.get("/profiles")
def list_profiles():
    return {"profiles": [{"name": n, "home": h, "exists": os.path.isdir(h)} for n, h in profiles()]}


@router.get("/files")
def list_files(profile: str | None = None):
    name, home = home_of(profile)
    d = mem_dir(home)
    lim = limits(home)
    out = []
    if os.path.isdir(d):
        names = [n for n in os.listdir(d) if NAME_RE.fullmatch(n) and os.path.isfile(os.path.join(d, n))]
        order = {"MEMORY.md": 0, "USER.md": 1}
        for n in sorted(names, key=lambda n: (order.get(n, 2), n)):
            out.append(_entry(n, os.path.join(d, n), lim))
    for n in ("MEMORY.md", "USER.md"):          # always listed so an empty file can be created
        if not any(e["name"] == n for e in out):
            out.append({"name": n, "size": 0, "mtime": None, "chars": 0, "entries": 0, "missing": True,
                        "limit": lim["memory" if n == "MEMORY.md" else "user"], "kind": "memory" if n == "MEMORY.md" else "user"})
    out.sort(key=lambda e: ({"MEMORY.md": 0, "USER.md": 1}.get(e["name"], 2), e["name"]))
    return {"profile": name, "dir": d, "files": out, "limits": lim}


@router.get("/read")
def read_file(profile: str | None = None, name: str = Query(...)):
    _, home = home_of(profile)
    p = file_path(home, name)
    if not os.path.isfile(p):
        return {"name": name, "content": "", "mtime": None, "size": 0, "missing": True}
    st = os.stat(p)
    if st.st_size > MAX_BYTES:
        raise HTTPException(413, "memory file larger than 1 MB")
    with open(p, encoding="utf-8", errors="replace") as f:
        return {"name": name, "content": f.read(), "mtime": st.st_mtime, "size": st.st_size}


class WriteBody(BaseModel):
    profile: str | None = None
    name: str
    content: str
    mtime: float | None = None
    force: bool = False


def _atomic_write(dst: str, data: bytes):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst), prefix=".hq-tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if os.path.exists(dst):
            os.chmod(tmp, os.stat(dst).st_mode & 0o777)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@router.post("/write")
def write_file(b: WriteBody):
    _, home = home_of(b.profile)
    p = file_path(home, b.name)
    if len(b.content.encode()) > MAX_BYTES:
        raise HTTPException(413, "content larger than 1 MB")
    os.makedirs(mem_dir(home), exist_ok=True)
    lock_path = p + ".lock"
    lock = open(lock_path, "a+")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise HTTPException(423, "the agent is writing this file right now — try again in a moment")
        cur = os.stat(p).st_mtime if os.path.exists(p) else None
        if not b.force and (cur or 0) != (b.mtime or 0) and not (cur is None and b.mtime is None):
            raise HTTPException(409, "file changed on disk since you opened it")
        _atomic_write(p, b.content.encode())
        return {"name": b.name, "mtime": os.stat(p).st_mtime, "size": os.stat(p).st_size}
    finally:
        lock.close()


class ResetBody(BaseModel):
    profile: str | None = None
    target: str = "all"


@router.post("/reset")
def reset(b: ResetBody):
    if b.target not in ("all", "memory", "user"):
        raise HTTPException(400, "target must be all, memory or user")
    _, home = home_of(b.profile)
    deleted = []
    for n in (["MEMORY.md"] if b.target in ("all", "memory") else []) + (["USER.md"] if b.target in ("all", "user") else []):
        p = os.path.join(mem_dir(home), n)
        if os.path.exists(p):
            os.unlink(p); deleted.append(n)
    return {"deleted": deleted}


@router.get("/search")
def search(q: str = Query(..., min_length=1)):
    needle = q.lower()
    hits = []
    for prof, home in profiles():
        d = mem_dir(home)
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            p = os.path.join(d, n)
            if not NAME_RE.fullmatch(n) or not os.path.isfile(p) or os.path.getsize(p) > MAX_BYTES:
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if needle in line.lower():
                        hits.append({"profile": prof, "name": n, "line": i, "text": line.rstrip("\n")[:300]})
                        if len(hits) >= SEARCH_CAP:
                            return {"hits": hits, "truncated": True}
    return {"hits": hits, "truncated": False}


# -- Hermes bridge (providers, graph) ---------------------------------------------------
def hermes_root() -> str:
    """The Hermes checkout: explicit override, else derived from the resolved `hermes` binary
    (<root>/bin/hermes), else /opt/hermes when that is a checkout."""
    if os.environ.get("HERMES_HQ_HERMES_ROOT"):
        return os.environ["HERMES_HQ_HERMES_ROOT"]
    cand = os.path.dirname(os.path.dirname(os.path.realpath(store.resolve_hermes())))
    for c in (cand, "/opt/hermes"):
        if os.path.isfile(os.path.join(c, "hermes_cli", "web_server.py")):
            return c
    return cand


def hermes_python() -> str:
    return os.environ.get("HERMES_HQ_HERMES_PY") or os.path.join(hermes_root(), ".venv", "bin", "python")


BRIDGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "hermes_bridge.py")


def bridge_argv(op: str):
    return [hermes_python(), BRIDGE, op]


def bridge_env(home: str):
    return jobs.child_env(HERMES_HOME=home, PYTHONUNBUFFERED="1")


def bridge(home: str, op: str, body: dict | None = None, timeout: float = 60):
    py = hermes_python()
    if not os.path.exists(py):
        raise HTTPException(503, f"Hermes python not found at {py}")
    try:
        r = subprocess.run(bridge_argv(op), input=json.dumps(body or {}), capture_output=True, text=True,
                           env=bridge_env(home), cwd=hermes_root(), timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"Hermes {op} timed out")
    if r.returncode != 0:
        raise HTTPException(502, (r.stderr.strip().splitlines() or [f"Hermes {op} failed"])[-1][:500])
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise HTTPException(502, f"Hermes {op} returned no JSON")


_cache: dict[tuple, tuple[float, dict]] = {}
_gen: dict[str, int] = {}          # per-home invalidation generation


def _cached(key: tuple, ttl: float, fn):
    """Cache per (kind, home). A value computed before an invalidate() for that home is never stored:
    a read that raced a write (install finishing while a list was being built) must not outlive it."""
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    gen = _gen.get(key[1], 0)
    val = fn()
    if _gen.get(key[1], 0) == gen and not (isinstance(val, dict) and val.get("ok") is False):     # never cache stale or error envelopes
        _cache[key] = (now, val)
    return val


def invalidate(home: str | None = None):
    for k in list(_cache):
        if home is None or k[1] == home:
            _cache.pop(k, None)
    for h in ([home] if home else list(_gen)):
        _gen[h] = _gen.get(h, 0) + 1


@router.get("/providers")
def list_providers(profile: str | None = None, fresh: int = 0):
    _, home = home_of(profile)
    if fresh:
        invalidate(home)
    return _cached(("providers", home), 120, lambda: bridge(home, "providers"))


class ProviderConfigBody(BaseModel):
    profile: str | None = None
    values: dict = {}
    activate: bool = False


@router.post("/providers/{name}/config")
def provider_config(name: str, b: ProviderConfigBody):
    if not PROVIDER_RE.fullmatch(name):
        raise HTTPException(404, "unknown provider")
    _, home = home_of(b.profile)
    res = bridge(home, "config", {"name": name, "values": b.values, "activate": b.activate})
    invalidate(home)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "provider config rejected")
    return res


class ProviderSetupBody(BaseModel):
    profile: str | None = None
    values: dict = {}


@router.post("/providers/{name}/setup")
def provider_setup(name: str, b: ProviderSetupBody):
    if not PROVIDER_RE.fullmatch(name):
        raise HTTPException(404, "unknown provider")
    prof, home = home_of(b.profile)
    invalidate(home)
    job = jobs.start("memory-provider-setup", f"Install {name} for {prof}", bridge_argv("setup"),
                     env=bridge_env(home), cwd=hermes_root(), stdin=json.dumps({"name": name, "values": b.values}),
                     on_done=lambda j: invalidate(home))
    return {"job": job.info(tail_bytes=0)}


class ProviderSelectBody(BaseModel):
    profile: str | None = None
    name: str = ""


@router.post("/provider")
def select_provider(b: ProviderSelectBody):
    if b.name and not PROVIDER_RE.fullmatch(b.name):
        raise HTTPException(404, "unknown provider")
    _, home = home_of(b.profile)
    res = bridge(home, "activate", {"name": b.name})
    invalidate(home)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "cannot activate provider")
    return res


@router.get("/graph")
def graph(profile: str | None = None):
    _, home = home_of(profile)
    return _cached(("graph", home), 30, lambda: bridge(home, "graph"))


@router.get("/graph/node")
def graph_node(id: str = Query(...), profile: str | None = None):
    _, home = home_of(profile)
    res = bridge(home, "node", {"id": id})
    if not res.get("ok"):
        raise HTTPException(404, res.get("message") or "node not found")
    return res
