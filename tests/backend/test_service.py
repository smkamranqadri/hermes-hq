"""Group 8-1: supervisor detection, unit/run-script templates, s6/systemd install-uninstall on temp
roots, the updater against a scratch git repo, and the auto-update due/skip matrix."""
import json, os, stat, subprocess, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    from backend import service, jobs
    return service, jobs, store, tmp_path


def test_detect_and_templates(env, monkeypatch):
    service, *_ = env
    monkeypatch.setenv("HERMES_HQ_SUPERVISOR", "systemd")
    assert service.detect() == "systemd"
    unit = service.systemd_unit({"host": "0.0.0.0", "port": 9010, "interval": 20})
    assert "ExecStart=" in unit and "serve --host 0.0.0.0 --port 9010 --interval 20" in unit and "Restart=on-failure" in unit
    run = service.s6_run_script({"host": "0.0.0.0", "port": 9010, "interval": 20})
    assert run.startswith("#!/bin/sh") and "exec " in run and "--port 9010" in run and service.repo_root() in run
    assert "s6-log" in service.s6_log_script()
    monkeypatch.setenv("HERMES_HQ_SUPERVISOR", "none")
    assert service.detect() == "none"
    monkeypatch.delenv("HERMES_HQ_SUPERVISOR")
    assert service.detect() in ("systemd", "s6", "none")      # real box: whatever it is, no crash


def test_s6_install_uninstall_tmp_roots(env, monkeypatch, tmp_path):
    service, *_ = env
    etc = tmp_path / "etc-sd"; scan = tmp_path / "scan"; scan.mkdir()
    monkeypatch.setenv("HERMES_HQ_SUPERVISOR", "s6")
    monkeypatch.setenv("HERMES_HQ_S6_ETC", str(etc)); monkeypatch.setenv("HERMES_HQ_S6_SCAN", str(scan))
    calls = []
    monkeypatch.setattr(service, "_run", lambda cmd, timeout=30: calls.append(cmd) or (0, "up (pid 1) 1 seconds"))
    msgs = []
    assert service.install({"host": "0.0.0.0", "port": 9011, "interval": 20}, out=msgs.append) == 0
    assert (etc / "hermes-hq" / "run").exists() and (scan / "hermes-hq" / "log" / "run").exists()
    assert os.stat(etc / "hermes-hq" / "run").st_mode & stat.S_IXUSR
    assert ["s6-svscanctl", "-a", str(scan)] in calls
    assert service.status(out=msgs.append) == 0
    assert service.restart(out=msgs.append) == 0 and calls[-1][:2] == ["s6-svc", "-r"]
    assert service.uninstall(out=msgs.append) == 0
    assert not (etc / "hermes-hq").exists() and not (scan / "hermes-hq").exists()


def test_systemd_install_writes_unit(env, monkeypatch, tmp_path):
    service, *_ = env
    sd = tmp_path / "systemd"; sd.mkdir()
    monkeypatch.setenv("HERMES_HQ_SUPERVISOR", "systemd"); monkeypatch.setenv("HERMES_HQ_SYSTEMD_DIR", str(sd))
    calls = []
    monkeypatch.setattr(service, "_run", lambda cmd, timeout=30: calls.append(cmd) or (0, ""))
    assert service.install({"host": "127.0.0.1", "port": 9010, "interval": 30}, out=lambda *a: None) == 0
    text = (sd / "hermes-hq.service").read_text()
    assert "WantedBy=multi-user.target" in text
    assert ["systemctl", "enable", "--now", "hermes-hq"] in calls
    service.uninstall(out=lambda *a: None)
    assert not (sd / "hermes-hq.service").exists()


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


@pytest.fixture()
def gitrepo(tmp_path):
    origin = tmp_path / "origin"; origin.mkdir(); _git(origin, "init", "-q", "-b", "main")
    (origin / "a.txt").write_text("1"); _git(origin, "add", "."); _git(origin, "commit", "-qm", "c1")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    return origin, clone


def _run_updater(clone, extra_env=None):
    env = dict(os.environ, HERMES_HQ_REPO=str(clone), **(extra_env or {}))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "hq_update.py")], capture_output=True, text=True, env=env, timeout=120)
    last = [l for l in r.stdout.strip().splitlines() if l.startswith("{")][-1]
    res = json.loads(last); res["_stderr"] = r.stderr[-600:]
    return r.returncode, res


def test_updater_noop_dirty_and_update(gitrepo, tmp_path):
    origin, clone = gitrepo
    code, res = _run_updater(clone)
    assert code == 0 and res["ok"] and res["noop"]
    (clone / "a.txt").write_text("dirty")
    code, res = _run_updater(clone)
    assert code == 1 and "dirty" in res["error"]
    _git(clone, "checkout", "--", "a.txt")
    # local AHEAD of origin (unpushed commits) → still a noop, no pointless restart
    (clone / "local.txt").write_text("l"); _git(clone, "add", "."); _git(clone, "commit", "-qm", "local-only")
    code, res = _run_updater(clone)
    assert code == 0 and res["noop"]
    (origin / "b.txt").write_text("2"); _git(origin, "add", "."); _git(origin, "commit", "-qm", "c2")
    # no pyproject/frontend changes → steps = pull + restart; force the 'none' supervisor answer
    hq_home = tmp_path / "upd-hq"; hq_home.mkdir()
    # diverged (local ahead + origin ahead) → ff-only pull refuses, clearly
    code, res = _run_updater(clone)
    assert code == 1 and "diverged" in res["error"]
    _git(clone, "reset", "--hard", "@{u}")
    _git(origin, "commit", "-qm", "c3", "--allow-empty")
    code, res = _run_updater(clone, {"HERMES_HQ_SUPERVISOR": "none", "HERMES_HQ_HOME": str(hq_home)})
    # supervisor 'none' → restart returns 2 → updater reports restart failure but HAS pulled
    assert code == 1 and res["error"] == "supervisor restart failed" and "pull" in res["steps"]
    assert (clone / "b.txt").exists()
    # …and it wrote its own needs_you Inbox row (it outlives the server, on_done can't do this)
    assert res["notified"] is True, res["_stderr"]
    import sqlite3
    con = sqlite3.connect(hq_home / "hq.db"); row = con.execute("SELECT kind, title FROM notifications").fetchone(); con.close()
    assert row == ("needs_you", "hermes-hq update failed")


def test_updater_lock_and_tool_precheck(gitrepo, tmp_path):
    origin, clone = gitrepo
    # concurrent update refused via the repo flock
    import fcntl
    lock = open(clone / ".git" / "hq-update.lock", "a+"); fcntl.flock(lock, fcntl.LOCK_EX)
    code, res = _run_updater(clone)
    assert code == 1 and "already running" in res["error"]
    lock.close()
    # deps changed upstream but uv unavailable → refuses BEFORE pulling
    (origin / "pyproject.toml").write_text("x = 1"); _git(origin, "add", "."); _git(origin, "commit", "-qm", "deps")
    hq_home = tmp_path / "lock-hq"; hq_home.mkdir()
    code, res = _run_updater(clone, {"PATH": "/usr/bin:/bin", "HERMES_HQ_HOME": str(hq_home)})
    assert code == 1 and "uv not on PATH" in res["error"]
    assert not (clone / "pyproject.toml").exists()          # the pull did not happen


def test_auto_update_pass_matrix(env, monkeypatch):
    service, jobs, store, tmp_path = env
    db = store.DEFAULT_DB_PATH
    started = []
    class J:
        id = "u1"; kind = "hq-update"; status = "running"
    monkeypatch.setattr(service, "start_update_job", lambda reason: started.append(reason) or J())
    monkeypatch.setattr(service, "_run", lambda cmd, timeout=30: (0, ""))          # upstream ok + clean tree
    monkeypatch.setenv("HERMES_HQ_SUPERVISOR", "s6")
    monkeypatch.setattr(store, "running_run_count", lambda db_path=None: 0)
    service._last_check = 0.0
    assert service.set_auto_update("0 5 * * *", db_path=db) == "0 5 * * *"
    assert service.auto_update_pass(now=time.time(), db_path=db) is None            # not due yet
    due = float(store.get_meta(service.NEXT_KEY, db_path=db)) + 1
    assert service.auto_update_pass(now=due, db_path=db) == {"job": "u1"} and started == ["scheduled"]
    assert float(store.get_meta(service.NEXT_KEY, db_path=db)) > due                # advanced
    # no upstream → clean skip and advance
    monkeypatch.setattr(service, "_run", lambda cmd, timeout=30: (1, "fatal: no upstream") if "@{u}" in " ".join(cmd) else (0, ""))
    dueU = float(store.get_meta(service.NEXT_KEY, db_path=db)) + 61
    assert service.auto_update_pass(now=dueU, db_path=db) == {"skipped": "no git upstream configured"}
    # dirty tree → skip and advance
    monkeypatch.setattr(service, "_run", lambda cmd, timeout=30: (0, " M x.py") if "status" in cmd else (0, ""))
    due2 = float(store.get_meta(service.NEXT_KEY, db_path=db)) + 1
    assert service.auto_update_pass(now=due2, db_path=db) == {"skipped": "dirty tree"}
    # runs running → skip WITHOUT advancing (retries)
    monkeypatch.setattr(service, "_run", lambda cmd, timeout=30: (0, ""))
    monkeypatch.setattr(store, "running_run_count", lambda db_path=None: 2)
    due3 = float(store.get_meta(service.NEXT_KEY, db_path=db)) + 1
    assert service.auto_update_pass(now=due3, db_path=db) == {"skipped": "2 run(s) running"}
    assert float(store.get_meta(service.NEXT_KEY, db_path=db)) < due3
    # jobs.start raising HTTPException (cap) → clean skip, no advance
    from fastapi import HTTPException
    monkeypatch.setattr(store, "running_run_count", lambda db_path=None: 0)
    def boom(reason):
        raise HTTPException(429, "at most 4 jobs run at once — wait for one to finish")
    monkeypatch.setattr(service, "start_update_job", boom)
    due4 = float(store.get_meta(service.NEXT_KEY, db_path=db)) + 61
    assert "at most 4 jobs" in service.auto_update_pass(now=due4, db_path=db)["skipped"]
    assert float(store.get_meta(service.NEXT_KEY, db_path=db)) < due4                # not advanced
    # runs still going past the retry window → give up until tomorrow
    monkeypatch.setattr(store, "running_run_count", lambda db_path=None: 1)
    due5 = float(store.get_meta(service.NEXT_KEY, db_path=db)) + service.RETRY_WINDOW + 61
    assert "window expired" in service.auto_update_pass(now=due5, db_path=db)["skipped"]
    assert float(store.get_meta(service.NEXT_KEY, db_path=db)) > due5
    # no supervisor → skip and advance (a pull we cannot restart after just skews code vs process)
    monkeypatch.setenv("HERMES_HQ_SUPERVISOR", "none")
    monkeypatch.setattr(store, "running_run_count", lambda db_path=None: 0)
    due6 = float(store.get_meta(service.NEXT_KEY, db_path=db)) + 61
    assert service.auto_update_pass(now=due6, db_path=db) == {"skipped": "no supervisor to restart under"}
    monkeypatch.setenv("HERMES_HQ_SUPERVISOR", "s6")
    # off → None
    service.set_auto_update("", db_path=db)
    assert service.auto_update_pass(now=time.time() + 999999, db_path=db) is None
    # bad cron refused
    with pytest.raises(ValueError):
        service.set_auto_update("nope", db_path=db)


def test_update_result_notifications(env):
    service, jobs, store, tmp_path = env
    db = store.DEFAULT_DB_PATH
    class J:
        def __init__(self, status, result): self.status, self.result, self.id = status, result, "j1"
    service.handle_update_result(J("done", {"ok": True, "noop": True}), db_path=db)              # updater reported: no row
    service.handle_update_result(J("failed", {"ok": False, "notified": True}), db_path=db)      # updater notified itself: no row
    service.handle_update_result(J("failed", None), db_path=db)                                 # died without a result: row
    conn = store._connect(db)
    rows = conn.execute("SELECT kind, title FROM notifications ORDER BY id").fetchall(); conn.close()
    assert [(r["kind"], r["title"]) for r in rows] == [("needs_you", "hermes-hq update failed")]


def test_jobs_stop_all_spares_the_updater(env):
    service, jobs, store, tmp_path = env
    class P:
        pid = 999999
    class J:
        def __init__(self, kind): self.kind, self.status, self.proc, self.stopped = kind, "running", P(), False
        def stop(self): self.stopped = True
    a, b = J("hq-update"), J("skill-install")
    jobs.JOBS.clear(); jobs.JOBS["a"] = a; jobs.JOBS["b"] = b
    jobs.stop_all()
    assert a.stopped is False and b.stopped is True
    jobs.JOBS.clear()


def test_cli(env, monkeypatch, capsys):
    service, *_ = env
    monkeypatch.setattr(service, "install", lambda flags, out=print: out(str(flags)) or 0)
    assert service.cli(["install", "--host", "0.0.0.0", "--port", "9010", "--interval", "20"]) == 0
    assert "'host': '0.0.0.0'" in capsys.readouterr().out
    assert service.cli(["auto-update", "--show"]) == 0
    assert "auto-update:" in capsys.readouterr().out
