"""Group 6-1 terminal: WS auth/origin, uid drop, reattach replay, resize, close, limits."""
import json, os, shutil, sys, tempfile, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    # pytest's basetemp is 0700 for the running user; the dropped-privilege shell needs a traversable HOME
    home = tempfile.mkdtemp(prefix="hq-term-home-", dir="/tmp"); os.chmod(home, 0o777)
    monkeypatch.setenv("HERMES_HQ_TERMINAL_HOME", home)
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend import terminal
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        r = c.post("/api/login", json={"password": "pw-test"})
        c.headers.update({"x-csrf": r.json()["csrf"]})
        yield c, terminal, home
        terminal.REGISTRY.close_all()
    shutil.rmtree(home, ignore_errors=True)


def _read_until(ws, needle: bytes, timeout=8.0) -> bytes:
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        m = ws.receive()
        if m.get("type") == "websocket.close":
            from starlette.websockets import WebSocketDisconnect
            raise WebSocketDisconnect(m.get("code", 1000), m.get("reason"))
        if "bytes" in m and m["bytes"] is not None:
            buf += m["bytes"]
        elif "text" in m and m["text"]:
            buf += m["text"].encode()
        if needle in buf:
            return buf
    raise AssertionError(f"{needle!r} not seen; got {buf[-500:]!r}")


def _hello(ws):
    msg = ws.receive_text()
    return json.loads(msg)


def test_ws_requires_cookie_and_origin(env):
    c, terminal, _ = env
    from starlette.websockets import WebSocketDisconnect
    anon = c.__class__(c.app)   # fresh client: no cookie
    with pytest.raises(WebSocketDisconnect) as e:
        with anon.websocket_connect("/api/terminal/ws", headers={"origin": "http://testserver"}):
            pass
    assert e.value.code == terminal.CLOSE_NO_AUTH
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/api/terminal/ws", headers={"origin": "http://evil.example"}):
            pass
    assert e.value.code == terminal.CLOSE_BAD_ORIGIN
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/api/terminal/ws"):        # no Origin at all
            pass
    assert e.value.code == terminal.CLOSE_BAD_ORIGIN
    with c.websocket_connect("/api/terminal/ws?session=nope", headers={"origin": "http://testserver"}) as ws:
        err = json.loads(ws.receive_text())
        assert err == {"t": "err", "code": terminal.CLOSE_NOT_FOUND, "reason": "session gone"}
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
        assert e.value.code == terminal.CLOSE_NOT_FOUND


def test_shell_user_resize_reattach_close(env):
    c, terminal, home = env
    O = {"origin": "http://testserver"}
    with c.websocket_connect("/api/terminal/ws?cols=100&rows=30", headers=O) as ws:
        h = _hello(ws)
        assert h["t"] == "hello" and not h["reattach"] and h["exited"] is None
        sid = h["id"]
        ws.send_text(json.dumps({"t": "i", "d": "echo UID=$(id -u) HOME=$HOME; stty size\n"}))
        out = _read_until(ws, b"30 100")
        expect_uid = terminal.target_user()[0] if os.geteuid() == 0 else os.geteuid()
        assert f"UID={expect_uid}".encode() in out
        assert f"HOME={home}".encode() in out
        if os.geteuid() == 0:
            assert expect_uid != 0 and expect_uid == 10000
        ws.send_text(json.dumps({"t": "r", "cols": 120, "rows": 40}))
        ws.send_text(json.dumps({"t": "i", "d": "stty size; echo MARKER_ONE\n"}))
        _read_until(ws, b"40 120")
        _read_until(ws, b"MARKER_ONE")
        ws.send_text(json.dumps({"t": "p"}))
        _read_until(ws, b'"pong"')
    # detached, still alive
    info = c.get("/api/terminal/sessions").json()
    assert [s for s in info["sessions"] if s["id"] == sid][0]["attached"] is False
    assert terminal.REGISTRY.sessions[sid].exit_code is None
    # reattach: hello says reattach, ring replays what we typed earlier
    with c.websocket_connect(f"/api/terminal/ws?session={sid}", headers=O) as ws:
        h = _hello(ws)
        assert h["reattach"] is True
        out = _read_until(ws, b"MARKER_ONE")
        assert b"UID=" in out
        ws.send_text(json.dumps({"t": "i", "d": "echo MARKER_TWO\n"}))
        _read_until(ws, b"MARKER_TWO")
    # close kills the process
    pid = terminal.REGISTRY.sessions[sid].pid
    r = c.post(f"/api/terminal/{sid}/close")
    assert r.status_code == 200
    assert sid not in terminal.REGISTRY.sessions
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert c.post(f"/api/terminal/{sid}/close").status_code == 404


def test_exit_notifies_and_limit(env, monkeypatch):
    c, terminal, _ = env
    O = {"origin": "http://testserver"}
    with c.websocket_connect("/api/terminal/ws", headers=O) as ws:
        h = _hello(ws); sid = h["id"]
        ws.send_text(json.dumps({"t": "i", "d": "exit 7\n"}))
        out = _read_until(ws, b'"exit"')
        assert b'"code": 7' in out or b'"code":7' in out
    assert terminal.REGISTRY.sessions[sid].exit_code == 7
    monkeypatch.setattr(terminal, "MAX_SESSIONS", 1)
    with c.websocket_connect("/api/terminal/ws", headers=O) as ws:
        _hello(ws)
        assert c.post("/api/terminal/spawn").status_code == 429
        from starlette.websockets import WebSocketDisconnect
        with c.websocket_connect("/api/terminal/ws", headers=O) as ws2:
            assert json.loads(ws2.receive_text())["code"] == terminal.CLOSE_LIMIT
            with pytest.raises(WebSocketDisconnect) as e:
                ws2.receive_text()
            assert e.value.code == terminal.CLOSE_LIMIT


def test_close_no_csrf_is_403(env):
    c, terminal, _ = env
    s = c.post("/api/terminal/spawn").json()
    bare = c.__class__(c.app); bare.cookies = c.cookies
    assert bare.post(f"/api/terminal/{s['id']}/close").status_code == 403
    assert c.post(f"/api/terminal/{s['id']}/close").status_code == 200


def test_logout_stops_input(env):
    c, terminal, _ = env
    O = {"origin": "http://testserver"}
    from starlette.websockets import WebSocketDisconnect
    with c.websocket_connect("/api/terminal/ws", headers=O) as ws:
        _hello(ws)
        ws.send_text(json.dumps({"t": "i", "d": "echo BEFORE_LOGOUT\n"}))
        _read_until(ws, b"BEFORE_LOGOUT")
        assert c.post("/api/logout").status_code == 200
        ws.send_text(json.dumps({"t": "i", "d": "echo AFTER_LOGOUT\n"}))
        with pytest.raises(WebSocketDisconnect) as e:
            _read_until(ws, b"AFTER_LOGOUT", timeout=3)
        assert e.value.code == terminal.CLOSE_NO_AUTH
