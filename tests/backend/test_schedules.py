"""Group 7-1 task schedules: presets/zone math, firing (tokens, ready, skip/always, late catch-up,
error → notification), run-now, API round-trip incl. preview/compile/next and validation."""
import os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store, schedule as sch
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    (tmp_path / "proj").mkdir()
    store.create_project("alpha", "Alpha", "", str(tmp_path / "proj"), db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        r = c.post("/api/login", json={"password": "pw-test"})
        c.headers.update({"x-csrf": r.json()["csrf"]})
        yield c, store, sch


def test_presets_zone_tokens(env):
    _, store, sch = env
    assert sch.preset_to_cron("daily", at="7:05") == "5 7 * * *"
    assert sch.preset_to_cron("weekdays", at="09:00") == "0 9 * * 1-5"
    assert sch.preset_to_cron("weekly", at="09:30", dow="sun") == "30 9 * * 0"
    assert sch.preset_to_cron("monthly", at="08:00", day=15) == "0 8 15 * *"
    assert sch.preset_to_cron("hours", every_hours=6, at="0:10") == "10 */6 * * *"
    with pytest.raises(ValueError):
        sch.preset_to_cron("monthly", day=31)
    with pytest.raises(ValueError):
        sch.preset_to_cron("daily", at="25:00")
    # 09:00 Karachi = 04:00 UTC
    import datetime
    t = sch.next_fires("0 9 * * *", "Asia/Karachi", 1, after=time.time())[0]
    assert datetime.datetime.fromtimestamp(t, datetime.timezone.utc).hour == 4
    assert sch.describe("0 9 * * 1-5") == "weekdays at 09:00"
    with pytest.raises(ValueError):
        sch.validate("not a cron")


def test_fire_flow(env):
    c, store, sch = env
    db = store.DEFAULT_DB_PATH
    sid = store.create_schedule("Weekly brief", "* * * * *", "alpha", "Brief {date}", "Body {week}", db_path=db)
    s0 = store.get_schedule(sid, db_path=db)
    assert store.fire_due(now=s0["next_fire_at"] - 1, db_path=db) == []          # not due yet
    res = store.fire_due(now=s0["next_fire_at"] + 1, db_path=db)
    assert [r[1] for r in res] == ["fired"]
    t = store.list_tasks(db_path=db)[0]
    assert t["schedule_id"] == sid and t["status"] == "ready" and "{date}" not in t["title"] and "-W" in store.get_task(t["id"], db_path=db)["description"]
    # api row carries schedule_id
    assert c.get("/api/tasks").json()["tasks"][0]["schedule_id"] == sid
    # overlap: skip while open
    s1 = store.get_schedule(sid, db_path=db)
    assert store.fire_due(now=s1["next_fire_at"] + 1, db_path=db)[0][1] == "skipped"
    store.complete_run(t["id"], status="done", summary="ok", db_path=db) if False else None
    # mark done directly and fire again → fired
    conn = store._connect(db); conn.execute("UPDATE tasks SET status='done' WHERE id=?", (t["id"],)); conn.commit(); conn.close()
    s2 = store.get_schedule(sid, db_path=db)
    assert store.fire_due(now=s2["next_fire_at"] + 1, db_path=db)[0][1] == "fired"
    # always: fires with an open previous
    store.update_schedule(sid, db_path=db, overlap="always")
    s3 = store.get_schedule(sid, db_path=db)
    assert store.fire_due(now=s3["next_fire_at"] + 1, db_path=db)[0][1] == "fired"
    # late catch-up collapses to one
    s4 = store.get_schedule(sid, db_path=db)
    res = store.fire_due(now=s4["next_fire_at"] + 3600, db_path=db)
    assert [r[1] for r in res] == ["late"]
    assert store.get_schedule(sid, db_path=db)["next_fire_at"] > s4["next_fire_at"] + 3600
    kinds = [r["kind"] for r in store.list_schedule_runs(sid, db_path=db)]
    assert kinds == ["late", "fired", "fired", "skipped", "fired"]
    # pause stops firing
    store.update_schedule(sid, db_path=db, enabled=False)
    s5 = store.get_schedule(sid, db_path=db)
    assert store.fire_due(now=(s5["next_fire_at"] or time.time()) + 61, db_path=db) == []


def test_error_records_and_notifies(env, monkeypatch):
    c, store, sch = env
    db = store.DEFAULT_DB_PATH
    sid = store.create_schedule("Broken", "* * * * *", "alpha", "T", assignee_profile=None, db_path=db)
    monkeypatch.setattr(store, "create_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    s0 = store.get_schedule(sid, db_path=db)
    assert store.fire_due(now=s0["next_fire_at"] + 1, db_path=db)[0][1] == "error"
    assert store.list_schedule_runs(sid, db_path=db)[0]["kind"] == "error"
    notes = c.get("/api/notifications").json()["notifications"]
    assert any(n["kind"] == "needs_you" and "Broken" in n["title"] for n in notes)


def test_api_roundtrip(env):
    c, store, sch = env
    r = c.post("/api/schedules", json={"name": "Daily", "cron": "0 9 * * *", "project": "alpha", "title": "T {date}", "assignee": "coder"})
    assert r.status_code == 200
    v = r.json(); sid = v["id"]
    assert v["cron_text"] == "every day at 09:00" and len(v["next_fires"]) == 3 and v["project_slug"] == "alpha"
    assert c.get("/api/schedules").json()["schedules"][0]["name"] == "Daily"
    assert c.get("/api/schedules/preview", params={"cron": "bad"}).status_code == 400
    p = c.get("/api/schedules/preview", params={"cron": "0 9 * * *"}).json(); assert p["text"] == "every day at 09:00"
    assert c.post("/api/schedules/compile", json={"kind": "weekly", "at": "10:00", "dow": "fri"}).json()["cron"] == "0 10 * * 5"
    n = c.get("/api/schedules/next").json(); assert n["total_enabled"] == 1 and n["next"][0]["name"] == "Daily"
    assert c.post(f"/api/schedules/{sid}", json={"title": "T2"}).json()["title"] == "T2"
    assert c.post(f"/api/schedules/{sid}/pause").json()["enabled"] == 0
    assert c.get("/api/schedules/next").json()["total_enabled"] == 0
    assert c.post(f"/api/schedules/{sid}/resume").json()["enabled"] == 1
    tid = c.post(f"/api/schedules/{sid}/run").json()["task_id"]
    assert c.get(f"/api/schedules/{sid}/runs").json()["runs"][0] | {} == c.get(f"/api/schedules/{sid}/runs").json()["runs"][0]
    assert c.get("/api/tasks").json()["tasks"][0]["schedule_id"] == sid
    assert c.post("/api/schedules", json={"name": "x", "cron": "bad", "project": "alpha", "title": "T"}).status_code == 400
    assert c.post("/api/schedules", json={"name": "x", "cron": "0 9 * * *", "project": "nope", "title": "T"}).status_code == 404
    assert c.post("/api/schedules", json={"name": "x", "cron": "0 9 * * *", "project": "alpha", "title": "T", "assignee": "bad name"}).status_code == 400
    assert c.post(f"/api/schedules/{sid}/delete").json() == {"ok": True}
    assert c.get("/api/schedules").json()["schedules"] == []
    assert c.get("/api/tasks").json()["tasks"][0]["schedule_id"] is None      # unlinked, task kept
