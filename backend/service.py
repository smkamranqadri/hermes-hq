"""Supervisor integration (Group 8-1): install/uninstall/status/restart hermes-hq as a service,
plus `service update` and the dispatcher-fired auto-update.

Detection first, never assumption (owner rule): systemd (PID1 `systemd` + systemctl) → a unit in
/etc/systemd/system; s6 (PID1 `s6-svscan` / s6-overlay) → a legacy service dir in /etc/services.d
(boot-persistent; the active `legacy-services` bundle copies it at boot) plus a live copy in the
running scan dir (`s6-svscanctl -a`); anything else → printed instructions. Paths are injectable for
tests and odd hosts via HERMES_HQ_* env vars.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

SERVICE = "hermes-hq"


# -- environment ---------------------------------------------------------------------------------
def repo_root() -> str:
    return os.environ.get("HERMES_HQ_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hq_bin() -> str:
    cand = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "hermes-hq")
    return cand if os.path.exists(cand) else shutil.which("hermes-hq") or cand


def systemd_dir() -> str:
    return os.environ.get("HERMES_HQ_SYSTEMD_DIR", "/etc/systemd/system")


def s6_etc_dir() -> str:
    return os.environ.get("HERMES_HQ_S6_ETC", "/etc/services.d")


def s6_scan_dir() -> str:
    for d in (os.environ.get("HERMES_HQ_S6_SCAN"), "/run/service", "/run/s6/legacy-services", "/var/run/s6/services"):
        if d and os.path.isdir(d):
            return d
    return "/run/service"


def s6_log_dir() -> str:
    return os.environ.get("HERMES_HQ_S6_LOG", "/var/log/hermes-hq")


def detect() -> str:
    """'systemd' | 's6' | 'none' — by PID 1 and available tooling, nothing hardcoded beforehand."""
    forced = os.environ.get("HERMES_HQ_SUPERVISOR")
    if forced in ("systemd", "s6", "none"):
        return forced
    try:
        with open("/proc/1/comm") as f:
            pid1 = f.read().strip()
    except OSError:
        pid1 = ""
    if pid1 == "systemd" and shutil.which("systemctl"):
        return "systemd"
    if pid1 in ("s6-svscan", "s6-linux-init-s", "init") and (shutil.which("s6-svc") or os.path.isdir("/run/s6")):
        return "s6"
    if shutil.which("systemctl") and os.path.isdir("/etc/systemd/system"):
        return "systemd"
    if shutil.which("s6-svc") and os.path.isdir("/run/s6"):
        return "s6"
    return "none"


def serve_cmd(flags: dict) -> str:
    parts = [hq_bin(), "serve", "--host", str(flags.get("host", "127.0.0.1")), "--port", str(flags.get("port", 9010)),
             "--interval", str(flags.get("interval", 30.0))]
    return " ".join(parts)


# -- templates ------------------------------------------------------------------------------------
def systemd_unit(flags: dict) -> str:
    env = "".join("Environment=%s=%s\n" % (k, v) for k, v in _kept_env().items())
    return ("[Unit]\nDescription=hermes-hq control plane\nAfter=network.target\n\n"
            "[Service]\nType=simple\nWorkingDirectory=%s\n%sExecStart=%s\nRestart=on-failure\nRestartSec=3\n\n"
            "[Install]\nWantedBy=multi-user.target\n" % (repo_root(), env, serve_cmd(flags)))


def s6_run_script(flags: dict) -> str:
    env = "".join("export %s='%s'\n" % (k, v) for k, v in _kept_env().items())
    return "#!/bin/sh\n# hermes-hq — written by `hermes-hq service install`\nset -e\ncd '%s'\n%sexec %s 2>&1\n" % (repo_root(), env, serve_cmd(flags))


def s6_log_script() -> str:
    return "#!/bin/sh\nmkdir -p '%s'\nexec s6-log -b n10 s5000000 T '%s'\n" % (s6_log_dir(), s6_log_dir())


def _kept_env() -> dict:
    keep = {}
    for k in ("HERMES_HQ_HOME", "HERMES_HOME", "WM_PROFILES_DIR", "WM_PROJECTS_ROOT", "HERMES_HQ_PASSWORD_FILE"):
        if os.environ.get(k):
            keep[k] = os.environ[k]
    return keep


# -- install / uninstall / status / restart -------------------------------------------------------
def _write_exec(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o755)


def _run(cmd: list[str], timeout=30) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def install(flags: dict, out=print) -> int:
    sup = detect()
    if sup == "systemd":
        unit = os.path.join(systemd_dir(), SERVICE + ".service")
        with open(unit, "w") as f:
            f.write(systemd_unit(flags))
        for cmd in (["systemctl", "daemon-reload"], ["systemctl", "enable", "--now", SERVICE]):
            code, msg = _run(cmd)
            if code != 0:
                out("systemd: `%s` failed: %s" % (" ".join(cmd), msg)); return 1
        out("installed systemd unit %s and started it (journalctl -u %s)" % (unit, SERVICE))
        return 0
    if sup == "s6":
        wrote_etc = True
        try:
            _write_service_dir(os.path.join(s6_etc_dir(), SERVICE), flags)
        except OSError as e:
            wrote_etc = False
            out("warning: could not write %s (%s) — the service will not survive a reboot" % (s6_etc_dir(), e))
        live = os.path.join(s6_scan_dir(), SERVICE)
        _write_service_dir(live, flags)
        code, msg = _run(["s6-svscanctl", "-a", s6_scan_dir()])
        if code != 0:
            out("s6-svscanctl failed: %s" % msg); return 1
        deadline = time.time() + 15
        while time.time() < deadline:
            up, detail = _s6_status(live)
            if up:
                out("installed s6 service %s (boot-persistent: %s) — logs in %s" % (live, wrote_etc, s6_log_dir()))
                return 0
            time.sleep(0.5)
        out("s6 service registered but not up yet: %s" % (_s6_status(live)[1],))
        return 1
    out("no supported supervisor detected (PID 1 is neither systemd nor s6).")
    out("Run it under your own supervisor with:\n  %s\nor in the background with:\n  nohup %s > %s.log 2>&1 &"
        % (serve_cmd(flags), serve_cmd(flags), SERVICE))
    return 2


def _write_service_dir(d: str, flags: dict):
    _write_exec(os.path.join(d, "run"), s6_run_script(flags))
    _write_exec(os.path.join(d, "log", "run"), s6_log_script())


def uninstall(out=print) -> int:
    sup = detect()
    if sup == "systemd":
        _run(["systemctl", "disable", "--now", SERVICE])
        unit = os.path.join(systemd_dir(), SERVICE + ".service")
        if os.path.exists(unit):
            os.unlink(unit)
        _run(["systemctl", "daemon-reload"])
        out("removed %s" % unit); return 0
    if sup == "s6":
        live = os.path.join(s6_scan_dir(), SERVICE)
        if os.path.isdir(live):
            _run(["s6-svc", "-d", live])
        for d in (os.path.join(s6_etc_dir(), SERVICE), live):
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        _run(["s6-svscanctl", "-a", s6_scan_dir()])
        out("removed the %s service dirs (ours only)" % SERVICE); return 0
    out("nothing installed (no supervisor)"); return 0


def _s6_status(live: str) -> tuple[bool, str]:
    code, msg = _run(["s6-svstat", live])
    return code == 0 and msg.startswith("up"), msg


def status(out=print) -> int:
    sup = detect()
    if sup == "systemd":
        code, msg = _run(["systemctl", "status", "--no-pager", "-n", "0", SERVICE])
        out(msg); return code
    if sup == "s6":
        up, msg = _s6_status(os.path.join(s6_scan_dir(), SERVICE))
        out("s6: %s" % msg); return 0 if up else 3
    code, msg = _run(["pgrep", "-af", "hermes-hq serve"])
    out(msg or "not running (no supervisor; would be a plain process)"); return 0 if code == 0 else 3


def restart(out=print) -> int:
    sup = detect()
    if sup == "systemd":
        code, msg = _run(["systemctl", "restart", SERVICE])
        out(msg or "restarted"); return code
    if sup == "s6":
        live = os.path.join(s6_scan_dir(), SERVICE)
        code, msg = _run(["s6-svc", "-r", live])
        out(msg or "restarted"); return code
    out("no supervisor — restart it yourself (pkill + serve)"); return 2


# -- update + auto-update -------------------------------------------------------------------------
UPDATER = os.path.join(repo_root(), "scripts", "hq_update.py")


def start_update_job(reason: str):
    """Run scripts/hq_update.py detached through the job runner; refuses a concurrent update."""
    from backend import jobs
    for j in jobs.JOBS.values():
        if j.kind == "hq-update" and j.status == "running":
            raise RuntimeError("an update job is already running (%s)" % j.id)
    return jobs.start("hq-update", "Update hermes-hq (%s)" % reason, [sys.executable, UPDATER],
                      env=jobs.child_env(HERMES_HQ_REPO=repo_root(), HERMES_HQ_PORT=os.environ.get("HERMES_HQ_PORT", "9010"),
                                         **{k: v for k, v in _kept_env().items()}),
                      cwd=repo_root(), timeout=1800, on_done=handle_update_result)


AUTO_KEY = "auto_update_cron"
NEXT_KEY = "auto_update_next"
DEFAULT_AUTO_CRON = "0 5 * * *"        # 05:00 Asia/Karachi


def auto_update_cron(db_path=None) -> str:
    from core import wm_store as store
    v = store.get_meta(AUTO_KEY, db_path=db_path)
    return DEFAULT_AUTO_CRON if v is None else v          # "" = off


def set_auto_update(cron: str | None, db_path=None) -> str:
    """cron expression, or '' / None to disable. Returns the stored value."""
    from core import schedule as sch
    from core import wm_store as store
    val = (cron or "").strip()
    if val:
        sch.validate(val, sch.DEFAULT_ZONE)
    store._set_meta(AUTO_KEY, val, db_path=db_path)
    store._set_meta(NEXT_KEY, str(sch.next_fires(val, sch.DEFAULT_ZONE, 1)[0]) if val else "", db_path=db_path)
    return val


def auto_update_pass(now=None, db_path=None):
    """Dispatcher hook: fire the updater when the auto-update window passes. Skips (and logs a reason)
    when disabled, not due, the tree is dirty, a WM run is running, or an update is already running."""
    from core import schedule as sch
    from core import wm_store as store
    now = now or time.time()
    cron = auto_update_cron(db_path=db_path)
    if not cron:
        return None
    nxt = store.get_meta(NEXT_KEY, db_path=db_path)
    if not nxt:
        store._set_meta(NEXT_KEY, str(sch.next_fires(cron, sch.DEFAULT_ZONE, 1, now)[0]), db_path=db_path)
        return None
    if now < float(nxt):
        return None
    advance = str(sch.next_fires(cron, sch.DEFAULT_ZONE, 1, now)[0])
    code, out = _run(["git", "-C", repo_root(), "status", "--porcelain"], timeout=20)
    running = store.count_running_runs(db_path=db_path)
    if code != 0 or out.strip():
        store._set_meta(NEXT_KEY, advance, db_path=db_path)
        return {"skipped": "dirty tree" if code == 0 else "git failed"}
    if running:
        # do NOT advance: retry next minute until the runs finish (the window stays open for the day)
        return {"skipped": "%d run(s) running" % running}
    try:
        job = start_update_job("scheduled")
    except RuntimeError as e:
        return {"skipped": str(e)}
    store._set_meta(NEXT_KEY, advance, db_path=db_path)
    return {"job": job.id}


def handle_update_result(job, db_path=None):
    """jobs.on_done for updates: Inbox rows. `info` on a real update, needs_you on failure."""
    from core import wm_store as store
    res = job.result if isinstance(job.result, dict) else {}
    if job.status == "done" and res.get("noop"):
        return
    if job.status == "done":
        store.add_notification("info", "hermes-hq updated to %s" % (res.get("sha", "?")[:9]),
                               body=", ".join(res.get("steps", [])), href="/",
                               source_key="hq-update:%s" % res.get("sha", job.id), db_path=db_path)
    else:
        store.add_notification("needs_you", "hermes-hq update failed",
                               body=(res.get("error") or "see the job log")[:300], href="/",
                               source_key="hq-update-fail:%s" % job.id, db_path=db_path)


def cli(argv, out=print) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="hermes-hq service")
    sub = p.add_subparsers(dest="op", required=True)
    ins = sub.add_parser("install")
    ins.add_argument("--host", default="127.0.0.1"); ins.add_argument("--port", type=int, default=9010)
    ins.add_argument("--interval", type=float, default=30.0)
    sub.add_parser("uninstall"); sub.add_parser("status"); sub.add_parser("restart")
    sub.add_parser("update")
    au = sub.add_parser("auto-update")
    g = au.add_mutually_exclusive_group()
    g.add_argument("--cron"); g.add_argument("--off", action="store_true"); g.add_argument("--show", action="store_true")
    a = p.parse_args(argv)
    if a.op == "install":
        return install({"host": a.host, "port": a.port, "interval": a.interval}, out=out)
    if a.op == "uninstall":
        return uninstall(out=out)
    if a.op == "status":
        return status(out=out)
    if a.op == "restart":
        return restart(out=out)
    if a.op == "update":
        r = subprocess.run([sys.executable, UPDATER], cwd=repo_root(),
                           env=dict(os.environ, HERMES_HQ_REPO=repo_root()))
        return r.returncode
    if a.op == "auto-update":
        if a.off:
            set_auto_update(""); out("auto-update off")
        elif a.cron:
            out("auto-update: %s (Asia/Karachi)" % set_auto_update(a.cron))
        else:
            cur = auto_update_cron()
            out("auto-update: %s" % (("%s (Asia/Karachi)" % cur) if cur else "off"))
        return 0
    return 2
