"""Group 6-4 MCP: routes over a mocked bridge, validation, toolsets gateway-off state, catalog install job path,
and a real probe of the stdio echo server through the same JSON-RPC the bridge would use."""
import json, os, subprocess, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"; profiles = hermes / "profiles"
    (profiles / "coder").mkdir(parents=True); (hermes / "memories").mkdir()
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq")); monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test"); monkeypatch.setenv("WM_PROFILES_DIR", str(profiles))
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True); store.init_db(db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend import mcp, memory, jobs, gateways
    calls = []
    def fake(home, op, body=None, timeout=60):
        calls.append((os.path.basename(home), op, body))
        if op == "mcp_list":
            return {"servers": [{"name": "srv", "transport": "http", "url": "https://x", "auth": "oauth", "enabled": True, "tools": None, "env": {}}, {"name": "tok", "transport": "http", "url": "https://y", "auth": "header", "enabled": False, "tools": ["a"], "env": {}}]}
        if op == "mcp_add":
            return {"ok": False, "status": 409, "error": "exists"} if body["name"] == "dup" else {"name": body["name"], "transport": "stdio" if body.get("command") else "http", "auth": body.get("auth")}
        if op in ("mcp_remove", "mcp_enabled"):
            return {"ok": False, "status": 404, "error": "not found"} if body["name"] == "ghost" else {"ok": True, **body}
        if op == "mcp_test":
            return {"ok": True, "tools": [{"name": "echo", "description": "d"}], "prompts": 1, "resources": 2}
        if op == "mcp_catalog":
            return {"entries": [{"name": "git-one", "needs_install": True}, {"name": "plain", "needs_install": False}], "diagnostics": []}
        if op == "mcp_catalog_install":
            return {"ok": True, "name": body["name"], "needs_cli_install": True} if body["name"] == "git-one" else {"ok": True, "name": body["name"], "background": False}
    monkeypatch.setattr(memory, "bridge", fake)
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        r = c.post("/api/login", json={"password": "pw-test"}); c.headers.update({"x-csrf": r.json()["csrf"]})
        yield c, calls, mcp, jobs, gateways, store


def test_servers_add_toggle_test_remove(env):
    c, calls, mcp, *_ = env
    r = c.get("/api/mcp", params={"profile": "coder"}).json()
    assert r["servers"][0]["login_command"] == "hermes --profile coder mcp login srv" and r["servers"][0]["last_test"] is None
    assert r["servers"][1]["has_token"] is True and r["servers"][1]["login_command"] is None
    assert c.get("/api/mcp").json()["servers"][0]["login_command"] == "hermes --profile default mcp login srv"
    a = c.post("/api/mcp/add", json={"profile": "coder", "name": "s1", "transport": "stdio", "command": "python3", "args": ["x.py"], "env": {"API_KEY": "v"}, "auth": "none"})
    assert a.status_code == 200 and calls[-1][2] == {"name": "s1", "auth": "none", "bearer_token": None, "command": "python3", "args": ["x.py"], "env": {"API_KEY": "v"}}
    b = c.post("/api/mcp/add", json={"profile": "coder", "name": "s2", "transport": "http", "url": "https://h", "auth": "bearer", "bearer_token": "tok"})
    assert b.status_code == 200 and calls[-1][2] == {"name": "s2", "auth": "header", "bearer_token": "tok", "url": "https://h"}
    assert c.post("/api/mcp/add", json={"name": "s3", "transport": "http", "url": "ftp://h"}).status_code == 400
    assert c.post("/api/mcp/add", json={"name": "s3", "transport": "stdio"}).status_code == 400
    assert c.post("/api/mcp/add", json={"name": "s3", "transport": "stdio", "command": "x", "env": {"bad key": "v"}}).status_code == 400
    assert c.post("/api/mcp/add", json={"name": "bad name", "transport": "http", "url": "https://h"}).status_code == 400
    assert c.post("/api/mcp/add", json={"name": "dup", "transport": "http", "url": "https://h"}).status_code == 409
    assert c.post("/api/mcp/toggle", json={"profile": "coder", "name": "srv", "enabled": False}).json()["enabled"] is False
    assert c.post("/api/mcp/toggle", json={"name": "ghost", "enabled": True}).status_code == 404
    t = c.post("/api/mcp/test", json={"profile": "coder", "name": "srv"}).json()
    assert t["ok"] and t["tools"][0]["name"] == "echo" and t["prompts"] == 1
    assert c.get("/api/mcp", params={"profile": "coder", "fresh": 1}).json()["servers"][0]["last_test"]["tools"][0]["name"] == "echo"
    assert c.post("/api/mcp/remove", json={"profile": "coder", "name": "srv"}).status_code == 200
    assert c.post("/api/mcp/remove", json={"name": "ghost"}).status_code == 404


def test_toolsets_gateway_off_and_on(env, monkeypatch):
    c, calls, mcp, jobs, gateways, store = env
    monkeypatch.setattr(gateways, "credentials", lambda n: (None, None))
    assert c.get("/api/mcp/toolsets", params={"profile": "coder"}).json() == {"profile": "coder", "gateway": "off", "toolsets": []}
    monkeypatch.setattr(gateways, "credentials", lambda n: (8999, "k"))
    monkeypatch.setattr(gateways, "healthy", lambda n: True)
    class R:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps(self.data).encode()
    seen = {}
    def fake_open(req, timeout=5):
        seen["url"] = req.full_url; seen["auth"] = req.headers.get("Authorization")
        return R({"object": "list", "platform": "api_server", "data": [{"name": "web", "enabled": True, "configured": True, "tools": ["web_search"]}]})
    monkeypatch.setattr(mcp.urllib.request, "urlopen", fake_open)
    r = c.get("/api/mcp/toolsets", params={"profile": "coder"}).json()
    assert r["gateway"] == "on" and r["toolsets"][0]["name"] == "web" and seen["url"].endswith(":8999/v1/toolsets") and seen["auth"] == "Bearer k"


def test_catalog_and_install_job(env, monkeypatch):
    c, calls, mcp, jobs, gateways, store = env
    assert [e["name"] for e in c.get("/api/mcp/catalog").json()["entries"]] == ["git-one", "plain"]
    started = []
    class J:
        def info(self, tail_bytes=0): return {"id": "j9", "status": "running"}
    monkeypatch.setattr(jobs, "start", lambda kind, label, argv, **kw: started.append((kind, argv)) or J())
    monkeypatch.setattr(store, "resolve_hermes", lambda: "/usr/bin/hermes")
    r = c.post("/api/mcp/catalog/install", json={"profile": "coder", "name": "git-one", "env": {"TOKEN": "t"}}).json()
    assert r["background"] is True and r["job"]["id"] == "j9" and started[-1][1] == ["/usr/bin/hermes", "--profile", "coder", "mcp", "install", "git-one"]
    assert calls[-1][2] == {"name": "git-one", "env": {"TOKEN": "t"}, "enable": True}
    r = c.post("/api/mcp/catalog/install", json={"name": "plain"}).json()
    assert r["background"] is False and len(started) == 1
    assert c.post("/api/mcp/catalog/install", json={"name": "x", "env": {"lower": "v"}}).status_code == 400


def test_echo_server_speaks_mcp():
    """The fixture server used by the live proof answers initialize + tools/list over stdio."""
    p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "mcp_echo_server.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    out, _ = p.communicate(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}) + "\n" + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n", timeout=10)
    lines = [json.loads(l) for l in out.strip().splitlines()]
    assert lines[0]["result"]["serverInfo"]["name"] == "hq-echo" and [t["name"] for t in lines[1]["result"]["tools"]] == ["echo", "add"]
