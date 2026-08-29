"""Gateway control against a fake `hermes` whose `gateway start` really serves
/v1/models on the profile's API_SERVER_PORT (and `stop` kills it)."""
import os, stat, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest
from tests.backend.test_writes import login  # noqa: F401

FAKE_HERMES = r'''#!/bin/sh
# usage: hermes [--profile NAME] gateway start|stop
prof=default; [ "$1" = --profile ] && { prof=$2; shift 2; }
[ "$1" = gateway ] || { echo "unsupported: $*" >&2; exit 2; }
home="$HERMES_HOME/profiles/$prof"; pidf="$home/.fake-gw.pid"
echo "$prof $2" >> "$HERMES_HOME/cli.log"
case "$2" in
  start)
    port=$(grep '^API_SERVER_PORT=' "$home/.env" | head -1 | cut -d= -f2 | awk '{print $1}')
    key=$(grep '^API_SERVER_KEY=' "$home/.env" | head -1 | cut -d= -f2 | awk '{print $1}')
    [ -n "$port" ] || { echo "no port" >&2; exit 3; }
    python3 - "$port" "$key" > /dev/null 2>&1 <<'PY' &
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
port, key = int(sys.argv[1]), sys.argv[2]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/v1/models" and self.headers.get("Authorization") == "Bearer " + key
        self.send_response(200 if ok else 401); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "hermes-agent"}]} if ok else {"error": "auth"}).encode())
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", port), H).serve_forever()
PY
    echo $! > "$pidf";;
  stop) [ -f "$pidf" ] && kill "$(cat "$pidf")" 2>/dev/null; rm -f "$pidf";;
  *) echo "unsupported: $*" >&2; exit 2;;
esac
'''


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "hermes"; (root / "profiles" / "coder").mkdir(parents=True)
    (root / "profiles" / "coder" / ".env").write_text("# owner line\nOPENAI_KEY=x\n")
    (root / ".env").write_text("API_SERVER_KEY=rootkey\n")
    shim = tmp_path / "hermes-shim"; shim.write_text(FAKE_HERMES); shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    monkeypatch.setenv("WM_PROFILES_DIR", str(root / "profiles"))
    monkeypatch.setenv("WM_HERMES", str(shim))
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    from backend import gateways as gw
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    # test ports well away from anything live
    monkeypatch.setattr(gw, "PORTS", {n: 18650 + i for i, n in enumerate(store.SPECIALIST_PROFILES)})
    monkeypatch.setattr(gw, "START_TIMEOUT", 10.0)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        yield c, store, gw, root
    gw.stop_started()
    for n in store.SPECIALIST_PROFILES:
        if gw.healthy(n):
            gw.stop(n)


def test_enable_writes_env_starts_and_is_healthy(env):
    c, store, gw, root = env
    h = login(c)
    r = c.post("/api/agent/coder/gateway", json={"enabled": True}, headers=h)
    assert r.status_code == 200, r.text
    g = r.json()["gateway"]
    assert g == {**g, "configured": True, "port": 18653, "enabled": True, "running": True}
    envtxt = (root / "profiles" / "coder" / ".env").read_text()
    assert envtxt.startswith("# owner line\nOPENAI_KEY=x\n")                       # owner lines untouched
    assert "API_SERVER_PORT=18653  # hermes-hq" in envtxt and "API_SERVER_KEY=" in envtxt
    assert gw.healthy("coder") and gw.base_url("coder") == "http://127.0.0.1:18653"
    assert c.get("/api/agents").json()["agents"][4]["gateway"]["running"] is True   # coder is 5th
    # enabling again is idempotent: no second start, env unchanged
    c.post("/api/agent/coder/gateway", json={"enabled": True}, headers=h)
    assert (root / "profiles" / "coder" / ".env").read_text() == envtxt
    assert (root / "cli.log").read_text().count("coder start") == 1
    r = c.post("/api/agent/coder/gateway", json={"enabled": False}, headers=h)
    assert r.json()["gateway"]["running"] is False and r.json()["gateway"]["enabled"] is False
    assert "coder stop" in (root / "cli.log").read_text()
    acts = [a["action"] for a in store.recent_activity(db_path=store.DEFAULT_DB_PATH)] if hasattr(store, "recent_activity") else None
    if acts is None:
        con = store._connect(store.DEFAULT_DB_PATH); acts = [x[0] for x in con.execute("SELECT action FROM activity")]; con.close()
    assert "gateway_start" in acts and "gateway_stop" in acts


def test_idle_sweep_and_exit_stop(env):
    c, store, gw, root = env
    db = store.DEFAULT_DB_PATH
    gw.set_enabled("coder", True, db_path=db)
    assert gw.idle_sweep(db_path=db) == []                                # just used
    gw._set_meta("last_used", "coder", int(time.time()) - 16 * 60, db)
    assert gw.idle_sweep(db_path=db) == ["coder"] and not gw.healthy("coder")
    # ensure_running brings it back for chat; exit stop takes it down
    port, key = gw.ensure_running("coder", db_path=db)
    assert port == 18653 and gw.healthy("coder")
    assert gw.stop_started(db_path=db) == ["coder"] and not gw.healthy("coder")


def test_refusals(env):
    c, store, gw, root = env
    h = login(c)
    assert c.post("/api/agent/orchestrator/gateway", json={"enabled": False}, headers=h).status_code == 409
    assert c.post("/api/agent/ghost/gateway", json={"enabled": True}, headers=h).status_code == 409
    r = c.post("/api/agent/writer/gateway", json={"enabled": True}, headers=h)   # not installed
    assert r.status_code == 409 and "not installed" in r.json()["detail"]
    with pytest.raises(ValueError, match="not enabled"):
        gw.ensure_running("coder", db_path=store.DEFAULT_DB_PATH)
    assert gw.credentials("orchestrator") == (8642, "rootkey")               # default: read, never written
