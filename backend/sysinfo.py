"""Real host + Hermes + store stats for the System page. stdlib only; every
probe is best-effort and reports None rather than inventing a value."""
import json
import os
import shutil
import sqlite3
import time
import urllib.request

from core import wm_store as store


def _meminfo():
    try:
        kv = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                kv[k] = int(v.strip().split()[0]) * 1024
        return {"total": kv.get("MemTotal"), "available": kv.get("MemAvailable")}
    except OSError:
        return None


def _uptime():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except OSError:
        return None


def _disk(path):
    try:
        u = shutil.disk_usage(path)
        return {"path": path, "total": u.total, "free": u.free}
    except OSError:
        return None


def _dir_size(path, limit_files=20000):
    total = n = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            n += 1
            if n > limit_files:
                return total, True
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total, False


def _http_json(url, timeout=1.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # unreachable / non-JSON: report why
        return {"error": type(e).__name__}


def _agent_processes():
    """Count live `hermes --profile <x>` processes (real agent sessions) by /proc scan."""
    out = {}
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    args = f.read().split(b"\0")
            except OSError:
                continue
            if any(a.endswith(b"/hermes") or a == b"hermes" for a in args[:2]) and b"--profile" in args:
                prof = args[args.index(b"--profile") + 1].decode(errors="replace")
                out[prof] = out.get(prof, 0) + 1
    except OSError:
        return None
    return out


def _store_stats(db_path):
    if not os.path.exists(db_path):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        counts = {t: con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in ("projects", "goals", "tasks", "runs", "reviews", "activity")}
        by_status = {r[0]: r[1] for r in con.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status")}
        running = con.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
        last_act = con.execute("SELECT MAX(ts) FROM activity").fetchone()[0]
    finally:
        con.close()
    size = sum(os.path.getsize(p) for p in (db_path, db_path + "-wal") if os.path.exists(p))
    return {"counts": counts, "tasks_by_status": by_status, "runs_running": running,
            "last_activity": last_act, "db_bytes": size}


def collect():
    hq = store.hq_home()
    runs_dir = store.resolve_runs_dir()
    runs_bytes, truncated = _dir_size(runs_dir) if os.path.isdir(runs_dir) else (0, False)
    dash = _http_json("http://127.0.0.1:9119/api/status")
    return {
        "ts": time.time(),
        "host": {
            "load": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "cpus": os.cpu_count(),
            "mem": _meminfo(),
            "uptime": _uptime(),
            "disk_hq": _disk(hq),
        },
        "hermes": {
            "binary": store.resolve_hermes(),
            "gateway": _http_json("http://127.0.0.1:8642/health"),
            "dashboard": {k: dash.get(k) for k in ("version", "gateway_running", "gateway_state", "error")} if isinstance(dash, dict) else dash,
            "agent_processes": _agent_processes(),
            "profiles": sorted(d for d in os.listdir(store.resolve_profiles_dir())
                               if os.path.isdir(os.path.join(store.resolve_profiles_dir(), d))) if os.path.isdir(store.resolve_profiles_dir()) else [],
        },
        "store": _store_stats(store.DEFAULT_DB_PATH),
        "runs_dir": {"path": runs_dir, "bytes": runs_bytes, "truncated": truncated},
    }
