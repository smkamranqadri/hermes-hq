"""Chat proxy against an in-process fake gateway (session API + SSE stream)."""
import json, os, socket, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest
from tests.backend.test_writes import login  # noqa: F401

KEY = "k-test"


class FakeGateway(BaseHTTPRequestHandler):
    calls = []
    slow = False

    def _auth(self):
        if self.headers.get("Authorization") != "Bearer " + KEY:
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":{"message":"bad key"}}'); return False
        return True

    def _json(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if not self._auth(): return
        if self.path == "/v1/models": return self._json(200, {"data": [{"id": "hermes-agent"}]})
        self._json(404, {"error": {"message": "nope"}})

    def do_POST(self):
        if not self._auth(): return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        FakeGateway.calls.append((self.path, body))
        if self.path == "/api/sessions":
            return self._json(201, {"object": "hermes.session", "session": {"id": body.get("id") or "api_1_abc", "title": body.get("title")}})
        if self.path.startswith("/v1/runs/") and self.path.endswith("/steer"):
            rid = self.path.split("/")[3]
            if rid == "run_done": return self._json(409, {"error": {"message": "Run is not currently accepting steer input", "code": "run_not_accepting_steer"}})
            return self._json(200, {"run_id": rid, "steer": "accepted"})
        if self.path.startswith("/v1/runs/") and self.path.endswith("/stop"):
            return self._json(200, {"run_id": self.path.split("/")[3], "status": "stopping"})
        if self.path.endswith("/chat/stream"):
            sid = self.path.split("/")[3]
            if sid == "missing": return self._json(404, {"error": {"message": "Session not found: missing"}})
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            def ev(name, payload):
                payload.update({"session_id": sid, "run_id": "run_9"})
                self.wfile.write(("event: %s\ndata: %s\n\n" % (name, json.dumps(payload))).encode()); self.wfile.flush()
            ev("run.started", {}); ev("message.started", {"message": {"id": "m1", "role": "assistant"}})
            ev("tool.started", {"tool_name": "terminal", "preview": "ls"})
            if FakeGateway.slow: time.sleep(0.4)
            ev("tool.completed", {"tool_name": "terminal", "preview": "ok"})
            msg = body["message"]
            text = msg if isinstance(msg, str) else " ".join(p.get("text", "[img]" if p.get("type") == "image_url" else "?") for p in msg)
            for d in ("Hel", "lo ", text[::-1]): ev("assistant.delta", {"delta": d})
            ev("assistant.completed", {"content": "Hello " + text[::-1]}); ev("run.completed", {}); ev("done", {})
            return
        self._json(404, {"error": {"message": "nope"}})

    def do_PATCH(self):
        if not self._auth(): return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        FakeGateway.calls.append(("PATCH " + self.path, body))
        sid = self.path.split("/")[3]
        return self._json(200, {"object": "hermes.session", "session": {"id": sid, "title": body.get("title", "old"), "pinned": body.get("pinned", False)}})

    def do_DELETE(self):
        if not self._auth(): return
        FakeGateway.calls.append(("DELETE " + self.path, None))
        return self._json(200, {"object": "hermes.session.deleted", "id": self.path.split("/")[3], "deleted": True})

    def log_message(self, *a): pass


@pytest.fixture()
def env(tmp_path, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), FakeGateway); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    root = tmp_path / "hermes"; (root / "profiles" / "coder").mkdir(parents=True)
    (root / "profiles" / "coder" / ".env").write_text("API_SERVER_PORT=%d\nAPI_SERVER_KEY=%s\n" % (port, KEY))
    (root / ".env").write_text("API_SERVER_PORT=%d\nAPI_SERVER_KEY=%s\n" % (port, KEY))   # orchestrator shares the fake
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq")); monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    monkeypatch.setenv("WM_PROFILES_DIR", str(root / "profiles")); monkeypatch.setenv("WM_HERMES", "/bin/false")
    for m in list(sys.modules):
        if m.startswith(("core", "backend")): del sys.modules[m]
    from core import wm_store as store
    from backend import gateways as gw
    os.makedirs(store.hq_home(), exist_ok=True); store.init_db(db_path=store.DEFAULT_DB_PATH)
    FakeGateway.calls.clear(); FakeGateway.slow = False
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        yield c, store, gw, root
    srv.shutdown()


def _events(text):
    out = []
    for block in text.strip().split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "): name = line[7:]
            elif line.startswith("data: "): data = json.loads(line[6:])
        out.append((name, data))
    return out


def test_disabled_agent_is_409_before_any_bytes(env):
    c, store, gw, root = env
    h = login(c)
    r = c.post("/api/chat/coder/sessions", json={}, headers=h)
    assert r.status_code == 409 and "not enabled" in r.json()["detail"]
    assert FakeGateway.calls == []


def test_session_then_streaming_turn_passthrough(env):
    from backend import chat as chat_mod
    c, store, gw, root = env
    h = login(c); db = store.DEFAULT_DB_PATH
    gw._set_meta("enabled", "coder", "1", db)          # owner enabled chat; fake gateway already "running"
    r = c.post("/api/chat/coder/sessions", json={"title": "hq test"}, headers=h)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]; assert sid == "api_1_abc" and r.json()["title"] == "hq test"
    with c.stream("POST", "/api/chat/coder/%s" % sid, json={"message": "ping"}, headers=h) as s:
        assert s.status_code == 200 and s.headers["content-type"].startswith("text/event-stream")
        body = b"".join(s.iter_raw()).decode()
    ev = _events(body)
    names = [n for n, _ in ev]
    assert names == ["run.started", "message.started", "tool.started", "tool.completed", "assistant.delta", "assistant.delta", "assistant.delta", "assistant.completed", "run.completed", "done"]
    assert "".join(d["delta"] for n, d in ev if n == "assistant.delta") == "Hello gnip"
    assert ev[2][1]["tool_name"] == "terminal" and ev[0][1]["run_id"] == "run_9"
    assert KEY not in body                                                  # key never forwarded
    assert FakeGateway.calls[-1] == ("/api/sessions/api_1_abc/chat/stream", {"message": "ping", "system_message": chat_mod.HQ_OPTIONS_HINT})
    assert gw._meta("last_used", "coder", db) is not None                  # touched for the idle sweeper
    r = c.post("/api/chat/coder/%s/stop/run_9" % sid, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "stopping"
    con = store._connect(db); acts = [x[0] for x in con.execute("SELECT action FROM activity")]; con.close()
    assert "chat_session" in acts and "chat_message" in acts


def test_gateway_errors_and_bad_input(env):
    c, store, gw, root = env
    h = login(c); db = store.DEFAULT_DB_PATH
    gw._set_meta("enabled", "coder", "1", db)
    r = c.post("/api/chat/coder/missing", json={"message": "x"}, headers=h)
    assert r.status_code == 502 and "Session not found" in r.json()["detail"]
    assert c.post("/api/chat/coder/api_1_abc", json={"message": "   "}, headers=h).status_code == 409
    assert c.post("/api/chat/coder/../etc", json={"message": "x"}, headers=h).status_code in (404, 405, 409)   # never reaches the handler
    assert c.post("/api/chat/coder/bad%20id!", json={"message": "x"}, headers=h).status_code == 409           # invalid id refused
    assert c.post("/api/chat/ghost/sessions", json={}, headers=h).status_code == 409
    # orchestrator: always enabled, uses the root .env credentials
    r = c.post("/api/chat/orchestrator/sessions", json={}, headers=h)
    assert r.status_code == 200


def test_history_from_state_db_without_gateway(env):
    c, store, gw, root = env
    h = login(c)
    assert c.get("/api/agent/coder/sessions").json() == {"sessions": []}     # no state.db yet -> empty, not 500
    assert c.get("/api/session/coder/nope").status_code == 404
    assert c.get("/api/agent/ghost/sessions").status_code == 404
