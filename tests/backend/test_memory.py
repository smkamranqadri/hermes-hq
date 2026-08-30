"""Group 6-2 memory: file listing/limits, read/write with 409 + flock 423, reset, cross-profile search,
name validation, provider/graph routes through a mocked bridge, jobs polling with a real subprocess."""
import fcntl, json, os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"; profiles = hermes / "profiles"
    (hermes / "memories").mkdir(parents=True)
    (hermes / "memories" / "MEMORY.md").write_text("root fact one\n§\nroot fact two about kubernetes\n")
    (hermes / "memories" / "USER.md").write_text("Owner likes tea\n")
    (hermes / "memories" / "MEMORY.md.lock").write_text("")
    (hermes / "config.yaml").write_text("memory:\n  memory_char_limit: 3000\n  user_char_limit: 1000\n")
    (profiles / "coder" / "memories").mkdir(parents=True)
    (profiles / "coder" / "memories" / "MEMORY.md").write_text("coder remembers kubernetes too\n")
    (profiles / "coder" / "config.yaml").write_text("memory:\n  memory_char_limit: 500\n")
    (profiles / "writer").mkdir()
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    monkeypatch.setenv("WM_PROFILES_DIR", str(profiles))
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend import memory
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        r = c.post("/api/login", json={"password": "pw-test"})
        c.headers.update({"x-csrf": r.json()["csrf"]})
        yield c, memory, hermes


def test_profiles_files_limits(env):
    c, memory, hermes = env
    ps = c.get("/api/memory/profiles").json()["profiles"]
    assert [p["name"] for p in ps] == ["orchestrator", "coder", "writer"]
    r = c.get("/api/memory/files").json()
    assert r["profile"] == "orchestrator" and r["limits"] == {"memory": 3000, "user": 1000}
    m = r["files"][0]; u = r["files"][1]
    assert m["name"] == "MEMORY.md" and m["entries"] == 2 and m["limit"] == 3000 and m["kind"] == "memory"
    assert u["name"] == "USER.md" and u["limit"] == 1000
    assert not any(f["name"].endswith(".lock") for f in r["files"])
    r = c.get("/api/memory/files", params={"profile": "coder"}).json()
    assert r["limits"] == {"memory": 500, "user": 1000}          # profile overrides root, root fills the rest
    assert [f["name"] for f in r["files"]] == ["MEMORY.md", "USER.md"] and r["files"][1].get("missing")
    assert c.get("/api/memory/files", params={"profile": "nope"}).status_code == 404
    assert c.get("/api/memory/read", params={"name": "../config.yaml"}).status_code == 400
    assert c.get("/api/memory/read", params={"name": "MEMORY.md.lock"}).status_code == 400


def test_write_409_423_reset(env):
    c, memory, hermes = env
    r = c.get("/api/memory/read", params={"name": "MEMORY.md"}).json()
    w = c.post("/api/memory/write", json={"name": "MEMORY.md", "content": "edited\n", "mtime": r["mtime"]})
    assert w.status_code == 200 and (hermes / "memories" / "MEMORY.md").read_text() == "edited\n"
    # stale mtime → 409, force → 200
    assert c.post("/api/memory/write", json={"name": "MEMORY.md", "content": "x", "mtime": r["mtime"]}).status_code == 409
    assert c.post("/api/memory/write", json={"name": "MEMORY.md", "content": "forced\n", "mtime": r["mtime"], "force": True}).status_code == 200
    # agent holds the flock → 423
    lock = open(hermes / "memories" / "MEMORY.md.lock", "a+"); fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        assert c.post("/api/memory/write", json={"name": "MEMORY.md", "content": "y", "force": True}).status_code == 423
    finally:
        lock.close()
    # new file on a profile without one
    assert c.post("/api/memory/write", json={"profile": "writer", "name": "USER.md", "content": "new\n"}).status_code == 200
    assert (hermes / "profiles" / "writer" / "memories" / "USER.md").read_text() == "new\n"
    assert c.post("/api/memory/write", json={"name": "notes.txt", "content": ""}).status_code == 400
    # reset
    assert c.post("/api/memory/reset", json={"target": "user"}).json() == {"deleted": ["USER.md"]}
    assert not (hermes / "memories" / "USER.md").exists() and (hermes / "memories" / "MEMORY.md").exists()
    assert c.post("/api/memory/reset", json={"target": "bogus"}).status_code == 400


def test_search_across_profiles(env):
    c, memory, hermes = env
    h = c.get("/api/memory/search", params={"q": "KUBERNETES"}).json()["hits"]
    assert [(x["profile"], x["name"], x["line"]) for x in h] == [("orchestrator", "MEMORY.md", 3), ("coder", "MEMORY.md", 1)]
    assert c.get("/api/memory/search", params={"q": ""}).status_code == 422


def test_providers_graph_via_bridge(env, monkeypatch):
    c, memory, hermes = env
    calls = []
    def fake(home, op, body=None, timeout=60):
        calls.append((os.path.basename(home), op, body))
        if op == "providers":
            return {"active": "", "providers": [{"name": "mem0", "status": "needs_config", "fields": [{"key": "api_key", "kind": "secret", "value": "", "is_set": False}]}]}
        if op == "config":
            return {"ok": True}
        if op == "activate":
            return {"ok": False, "error": "Memory provider 'mem0' is not ready (needs config)."} if body["name"] else {"ok": True, "active": ""}
        if op == "graph":
            return {"nodes": [{"id": "s1"}], "edges": [], "clusters": [], "memory": [], "stats": {}}
        if op == "node":
            return {"ok": True, "id": body["id"], "content": "hello"} if body["id"] == "s1" else {"ok": False, "message": "no"}
    monkeypatch.setattr(memory, "bridge", fake)
    p = c.get("/api/memory/providers", params={"profile": "coder"}).json()
    assert p["providers"][0]["name"] == "mem0" and p["providers"][0]["fields"][0]["value"] == ""
    c.get("/api/memory/providers", params={"profile": "coder"})
    assert len([x for x in calls if x[1] == "providers"]) == 1        # cached
    assert c.post("/api/memory/providers/mem0/config", json={"profile": "coder", "values": {"api_key": "k"}}).status_code == 200
    assert calls[-1] == ("coder", "config", {"name": "mem0", "values": {"api_key": "k"}, "activate": False})
    r = c.post("/api/memory/provider", json={"profile": "coder", "name": "mem0"})
    assert r.status_code == 400 and "not ready" in r.json()["detail"]
    assert c.post("/api/memory/provider", json={"profile": "coder", "name": ""}).json() == {"ok": True, "active": ""}
    assert c.post("/api/memory/providers/bad..name/config", json={}).status_code == 404
    assert c.get("/api/memory/graph").json()["nodes"] == [{"id": "s1"}]
    assert c.get("/api/memory/graph/node", params={"id": "s1"}).json()["content"] == "hello"
    assert c.get("/api/memory/graph/node", params={"id": "zz"}).status_code == 404


def test_setup_job_polls_real_subprocess(env, monkeypatch):
    c, memory, hermes = env
    monkeypatch.setattr(memory, "bridge_argv", lambda op: [sys.executable, "-c", "import sys,json; body=json.loads(sys.stdin.read()); print('installing', body['name']); print(json.dumps({'ok': True, 'provider': body['name']}))"])
    monkeypatch.setattr(memory, "hermes_root", lambda: str(hermes))
    j = c.post("/api/memory/providers/mem0/setup", json={"profile": "coder", "values": {}}).json()["job"]
    assert j["status"] == "running" and j["kind"] == "memory-provider-setup"
    for _ in range(100):
        info = c.get(f"/api/jobs/{j['id']}").json()
        if info["status"] != "running":
            break
        time.sleep(0.05)
    assert info["status"] == "done" and info["result"] == {"ok": True, "provider": "mem0"} and "installing mem0" in info["log"]
    assert c.get("/api/jobs/nope").status_code == 404
    assert c.get("/api/jobs").json()["jobs"][0]["id"] == j["id"]


def test_job_timeout_stop_and_result_after_noise(env, monkeypatch):
    c, memory, hermes = env
    from backend import jobs
    monkeypatch.setattr(memory, "hermes_root", lambda: str(hermes))
    # result JSON followed by a stderr warning on the same fd is still found
    monkeypatch.setattr(memory, "bridge_argv", lambda op: [sys.executable, "-c", "import sys; print('{\"ok\": true, \"n\": 1}'); sys.stderr.write('warning after result')"])
    j = c.post("/api/memory/providers/mem0/setup", json={"profile": "coder"}).json()["job"]
    for _ in range(100):
        info = c.get(f"/api/jobs/{j['id']}").json()
        if info["status"] != "running": break
        time.sleep(0.05)
    assert info["status"] == "done" and info["result"] == {"ok": True, "n": 1}
    # timeout kills a hung job
    monkeypatch.setattr(memory, "bridge_argv", lambda op: [sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(jobs, "DEFAULT_TIMEOUT", 0.3)
    j = c.post("/api/memory/providers/mem0/setup", json={"profile": "coder"}).json()["job"]
    for _ in range(100):
        info = c.get(f"/api/jobs/{j['id']}").json()
        if info["status"] != "running": break
        time.sleep(0.05)
    assert info["status"] == "failed" and info["timed_out"] is True
    # stop route
    monkeypatch.setattr(jobs, "DEFAULT_TIMEOUT", 60)
    j = c.post("/api/memory/providers/mem0/setup", json={"profile": "coder"}).json()["job"]
    assert c.post(f"/api/jobs/{j['id']}/stop").json()["stopping"] is True
    for _ in range(100):
        info = c.get(f"/api/jobs/{j['id']}").json()
        if info["status"] != "running": break
        time.sleep(0.05)
    assert info["status"] == "failed" and info["stopped"] is True
    # missing binary → 503, not a traceback
    monkeypatch.setattr(memory, "bridge_argv", lambda op: ["/nonexistent/python", "x"])
    assert c.post("/api/memory/providers/mem0/setup", json={"profile": "coder"}).status_code == 503
    # cap
    monkeypatch.setattr(memory, "bridge_argv", lambda op: [sys.executable, "-c", "import time; time.sleep(5)"])
    monkeypatch.setattr(jobs, "MAX_RUNNING", 1)
    j = c.post("/api/memory/providers/mem0/setup", json={"profile": "coder"}).json()["job"]
    assert c.post("/api/memory/providers/mem0/setup", json={"profile": "coder"}).status_code == 429
    c.post(f"/api/jobs/{j['id']}/stop")


def test_cache_never_stores_a_value_computed_before_an_invalidate(env):
    c, memory, hermes = env
    home = str(hermes)
    calls = {"n": 0}
    def compute():
        calls["n"] += 1
        if calls["n"] == 1:
            memory.invalidate(home)          # a write landed while this read was being built
        return {"servers": [f"v{calls['n']}"]}
    assert memory._cached(("mcp", home), 60, compute) == {"servers": ["v1"]}
    assert memory._cached(("mcp", home), 60, compute) == {"servers": ["v2"]}      # v1 was not cached
    assert memory._cached(("mcp", home), 60, compute) == {"servers": ["v2"]}      # v2 is
