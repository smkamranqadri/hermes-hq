"""Group 7-2 agent jobs: routes over a mocked bridge, every→cron, legacy tagging + delete refusal,
minute pass (off-gateway tick argv, skip when healthy or already ticking, error → notification)."""
import os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"; profiles = hermes / "profiles"
    (profiles / "coder").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq")); monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test"); monkeypatch.setenv("WM_PROFILES_DIR", str(profiles))
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True); store.init_db(db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend import cron, memory, jobs, gateways
    calls = []
    state = {"jobs": [
        {"id": "dfe30ff9e8bf", "name": "wm-dispatch", "no_agent": True, "enabled": False, "state": "paused", "profile": "default", "last_status": "ok", "last_run_at": "t0", "last_error": None},
        {"id": "aaa111", "name": "news", "no_agent": False, "enabled": True, "state": "scheduled", "profile": "default", "last_status": "ok", "last_run_at": "t1", "last_error": None},
        {"id": "bbb222", "name": "coder-job", "no_agent": False, "enabled": True, "state": "scheduled", "profile": "coder", "last_status": "error", "last_run_at": "t2", "last_error": "boom"},
    ]}
    def fake(home, op, body=None, timeout=60):
        calls.append((op, body))
        if op == "cron_list":
            p = body["profile"]
            return {"jobs": [j for j in state["jobs"] if p == "all" or j["profile"] == p]}
        if op == "cron_get":
            return dict(state["jobs"][1])
        if op == "cron_runs":
            return {"runs": [{"status": "ok"}]} if isinstance(body, dict) else {}
        if op == "cron_create":
            return {"id": "new123", "name": body["name"], "schedule": {"expr": body["schedule"]}, "profile": body["profile"], "no_agent": False, "enabled": True}
        if op in ("cron_update", "cron_pause", "cron_resume", "cron_trigger", "cron_delete"):
            return {"id": body["id"], "ok": True, "profile": "default", "no_agent": False}
        if op == "cron_targets":
            return {"targets": [{"id": "local"}]}
    monkeypatch.setattr(memory, "bridge", fake)
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        r = c.post("/api/login", json={"password": "pw-test"}); c.headers.update({"x-csrf": r.json()["csrf"]})
        yield c, calls, cron, jobs, gateways, store, state


def test_list_create_update_actions(env):
    c, calls, cron, *_ = env
    r = c.get("/api/cron/jobs").json()["jobs"]
    assert r[0]["legacy_wm"] is True and r[0]["is_script"] is True and r[0]["profile"] == "orchestrator"
    assert r[2]["profile"] == "coder" and r[2]["legacy_wm"] is False
    c.get("/api/cron/jobs"); assert len([x for x in calls if x[0] == "cron_list"]) == 1        # cached
    assert c.get("/api/cron/jobs", params={"profile": "coder"}).json()["jobs"][0]["id"] == "bbb222"
    assert calls[-1][1] == {"profile": "coder"}
    j = c.post("/api/cron/jobs", json={"profile": "orchestrator", "name": "every5", "prompt": "hi", "every": {"n": 5, "unit": "minutes"}}).json()
    assert calls[-1][1]["schedule"] == "*/5 * * * *" and calls[-1][1]["profile"] == "default" and j["profile"] == "orchestrator"
    assert c.post("/api/cron/jobs", json={"name": "x", "prompt": "p", "every": {"n": 90, "unit": "minutes"}}).status_code == 400
    assert c.post("/api/cron/jobs", json={"name": "x", "prompt": "p"}).status_code == 400
    assert c.post("/api/cron/jobs", json={"profile": "all", "name": "x", "prompt": "p", "schedule": "* * * * *"}).status_code == 400
    assert c.post("/api/cron/jobs/aaa111/update", json={"updates": {"prompt": "new"}}).status_code == 200
    assert c.post("/api/cron/jobs/aaa111/update", json={"updates": {"script": "x.py"}}).status_code == 400
    for act in ("pause", "resume", "trigger"):
        assert c.post(f"/api/cron/jobs/aaa111/{act}", json={}).status_code == 200
    assert c.post("/api/cron/jobs/aaa111/delete", json={}).status_code == 200
    assert c.post("/api/cron/jobs/dfe30ff9e8bf/delete", json={}).status_code == 403
    assert c.get("/api/cron/jobs/bad%20id").status_code == 400
    assert c.get("/api/cron/targets").json()["targets"][0]["id"] == "local"


def test_minute_pass(env, monkeypatch):
    c, calls, cron, jobs, gateways, store, state = env
    started = []
    class J:
        def __init__(self): self.id = "tick1"; self.status = "running"
        def info(self, tail_bytes=0): return {"id": self.id}
    monkeypatch.setattr(jobs, "start", lambda kind, label, argv, **kw: started.append((kind, argv)) or J())
    monkeypatch.setattr(store, "resolve_hermes", lambda: "/usr/bin/hermes")
    monkeypatch.setattr(gateways, "healthy", lambda n: False)
    cron._last_pass = 0; cron._ticking.clear(); cron._notified.clear()
    out = cron.minute_pass(now=time.time())
    assert out["ticked"] == ["coder"] and started[-1] == ("cron-tick", ["/usr/bin/hermes", "--profile", "coder", "cron", "tick"])
    assert out["errors"] == 1
    notes = c.get("/api/notifications").json()["notifications"]
    assert any("coder-job" in n["title"] and n["kind"] == "needs_you" for n in notes)
    # within 60 s → no-op; after, healthy gateway or a still-running tick → no new tick; error not re-notified
    assert cron.minute_pass(now=time.time()) is None
    jobs.JOBS["tick1"] = J()
    out = cron.minute_pass(now=time.time() + 61)
    assert out["ticked"] == [] and out["errors"] == 0
    jobs.JOBS["tick1"].status = "done"
    monkeypatch.setattr(gateways, "healthy", lambda n: True)
    out = cron.minute_pass(now=time.time() + 122)
    assert out["ticked"] == []
    # orchestrator jobs never ticked by hq even when probe says off
    monkeypatch.setattr(gateways, "healthy", lambda n: False)
    state["jobs"] = [j for j in state["jobs"] if j["profile"] == "default"]
    cron._last_pass = 0
    assert cron.minute_pass(now=time.time() + 200)["ticked"] == []
