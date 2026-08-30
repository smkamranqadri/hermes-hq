"""Group 6-3 skills: routes over a mocked bridge, name/identifier validation, CLI jobs built as argv."""
import json, os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"; profiles = hermes / "profiles"
    (profiles / "coder").mkdir(parents=True); (hermes / "memories").mkdir()
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq")); monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    monkeypatch.setenv("WM_PROFILES_DIR", str(profiles))
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True); store.init_db(db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend import skills, memory, jobs
    calls = []
    def fake(home, op, body=None, timeout=60):
        calls.append((os.path.basename(home), op, body))
        if op == "skills_list":
            return {"ok": True, "skills": [{"name": "claude-code", "category": "autonomous-ai-agents", "enabled": True, "usage": 3, "provenance": "bundled", "tags": ["x"]}]}
        if op == "skills_content":
            return {"name": body["name"], "content": "# hi", "path": "/x/SKILL.md"} if body["name"] == "claude-code" else {"ok": False, "status": 404, "error": "Skill 'zz' not found."}
        if op == "skills_create":
            return {"ok": False, "status": 400, "error": "Invalid frontmatter"} if "bad" in body["content"] else {"success": True, "path": "/x/new"}
        if op in ("skills_update", "skills_toggle"):
            return {"ok": True, **body}
        if op == "hub_sources":
            return {"sources": [{"id": "hermes-index", "available": True}], "index_available": True, "featured": [], "installed": {}}
        if op == "hub_search":
            return {"results": [{"identifier": "skills-sh/a/b/c", "name": "c"}], "source_counts": {"hermes-index": 1}, "timed_out": [], "installed": {}}
        if op == "hub_preview":
            return {"identifier": body["identifier"], "skill_md": "# c", "files": ["SKILL.md"]}
        if op == "hub_scan":
            return {"verdict": "safe", "policy": "allow", "findings": []}
    monkeypatch.setattr(memory, "bridge", fake)
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        r = c.post("/api/login", json={"password": "pw-test"}); c.headers.update({"x-csrf": r.json()["csrf"]})
        yield c, calls, skills, jobs, store


def test_list_read_write_create_toggle(env):
    c, calls, skills, jobs, store = env
    r = c.get("/api/skills", params={"profile": "coder"}).json()
    assert r["profile"] == "coder" and r["skills"][0]["name"] == "claude-code"
    c.get("/api/skills", params={"profile": "coder"}); assert len([x for x in calls if x[1] == "skills_list"]) == 1   # cached
    assert c.get("/api/skills/read", params={"name": "claude-code"}).json()["content"] == "# hi"
    assert c.get("/api/skills/read", params={"name": "zz"}).status_code == 404
    assert c.get("/api/skills/read", params={"name": "../etc"}).status_code == 400
    assert c.post("/api/skills/write", json={"profile": "coder", "name": "claude-code", "content": "# new"}).status_code == 200
    assert calls[-1] == ("coder", "skills_update", {"name": "claude-code", "content": "# new"})
    assert len([x for x in calls if x[1] == "skills_list"]) == 1
    c.get("/api/skills", params={"profile": "coder"}); assert len([x for x in calls if x[1] == "skills_list"]) == 2   # invalidated by the write
    r = c.post("/api/skills/create", json={"name": "my-skill", "category": "productivity", "content": "---\nname: my-skill\n---\nbody"})
    assert r.status_code == 200 and r.json()["success"]
    assert c.post("/api/skills/create", json={"name": "my-skill", "content": "bad"}).status_code == 400
    assert c.post("/api/skills/create", json={"name": "my-skill", "category": "Bad Cat", "content": "x"}).status_code == 400
    assert c.post("/api/skills/toggle", json={"name": "claude-code", "enabled": False}).json()["enabled"] is False
    assert calls[-1] == ("hermes", "skills_toggle", {"name": "claude-code", "enabled": False})     # orchestrator = root home


def test_hub_routes(env):
    c, calls, *_ = env
    assert c.get("/api/skills/hub/sources").json()["index_available"] is True
    assert c.get("/api/skills/hub/search", params={"q": "kube"}).json()["results"][0]["name"] == "c"
    assert calls[-1][2] == {"q": "kube", "source": "all", "limit": 20}
    assert c.get("/api/skills/hub/search", params={"q": "kube", "source": "bad source"}).status_code == 400
    assert c.get("/api/skills/hub/preview", params={"identifier": "skills-sh/a/b/c"}).json()["skill_md"] == "# c"
    assert c.get("/api/skills/hub/scan", params={"identifier": "skills-sh/a/b/c"}).json()["policy"] == "allow"
    assert c.get("/api/skills/hub/preview", params={"identifier": "../../x"}).status_code == 400
    assert c.get("/api/skills/hub/preview", params={"identifier": "-rf"}).status_code == 400


def test_cli_jobs_are_argv(env, monkeypatch):
    c, calls, skills, jobs, store = env
    started = []
    class J:
        def __init__(self): self.id = "j1"
        def info(self, tail_bytes=0): return {"id": "j1", "status": "running"}
    monkeypatch.setattr(jobs, "start", lambda kind, label, argv, **kw: started.append((kind, argv, kw)) or J())
    monkeypatch.setattr(store, "resolve_hermes", lambda: "/usr/bin/hermes")
    assert c.post("/api/skills/hub/install", json={"profile": "coder", "identifier": "skills-sh/a/b/c", "category": "devops"}).json()["job"]["id"] == "j1"
    assert started[-1][1] == ["/usr/bin/hermes", "--profile", "coder", "skills", "install", "skills-sh/a/b/c", "--yes", "--category", "devops"]
    assert started[-1][2]["env"]["HERMES_HOME"].endswith("profiles/coder")
    c.post("/api/skills/hub/install", json={"identifier": "skills-sh/a/b/c"})
    assert started[-1][1] == ["/usr/bin/hermes", "skills", "install", "skills-sh/a/b/c", "--yes"]      # orchestrator: no --profile
    c.post("/api/skills/hub/uninstall", json={"profile": "coder", "name": "c"}); assert started[-1][1][-3:] == ["uninstall", "c", "--yes"]
    c.post("/api/skills/hub/update", json={}); assert started[-1][1][-2:] == ["skills", "update"]
    c.post("/api/skills/hub/update", json={"name": "c"}); assert started[-1][1][-2:] == ["update", "c"]
    c.post("/api/skills/hub/check", json={}); assert started[-1][1][-1] == "check"
    c.post("/api/skills/audit", json={}); assert started[-1][1][-1] == "audit"
    assert c.post("/api/skills/hub/install", json={"identifier": "--force"}).status_code == 400
    assert c.post("/api/skills/hub/uninstall", json={"name": "a b"}).status_code == 400
    assert all("--force" not in a[1] for a in started)
