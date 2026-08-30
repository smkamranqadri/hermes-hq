#!/usr/bin/env python3
"""Detached updater for hermes-hq (`service update` and the scheduled auto-update).

Runs OUTSIDE the request path (jobs.py subprocess or the CLI) and OUTLIVES the server it
restarts (systemd KillMode=process; jobs.stop_all exempts hq-update), so it must do its own
reporting: terminal outcomes are written straight into hq.db as Inbox rows. A repo-local
flock makes the CLI and the scheduled job mutually exclusive. Order of operations: lock →
fetch → noop? → dirty? → tool pre-checks → pull --ff-only → selective deps/build → restart →
health poll. The last stdout line is the JSON result the job runner parses.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = os.environ.get("HERMES_HQ_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = os.environ.get("HERMES_HQ_PORT", "9010")


class R:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def sh(*cmd, timeout=600):
    print("$ " + " ".join(cmd), flush=True)
    try:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        print("command not found: %s" % cmd[0], file=sys.stderr, flush=True)
        return R(127, "", "command not found: %s" % cmd[0])
    except subprocess.TimeoutExpired:
        print("timed out after %ss: %s" % (timeout, cmd[0]), file=sys.stderr, flush=True)
        return R(124, "", "timed out")
    if r.stdout:
        print(r.stdout[-4000:], flush=True)
    if r.stderr:
        print(r.stderr[-4000:], file=sys.stderr, flush=True)
    return r


def notify(kind, title, body, source_key):
    """Write the Inbox row ourselves — the server process that could have done it may be the
    one we just restarted. Never fatal."""
    try:
        sys.path.insert(0, REPO)
        from core import wm_store as store
        store.init_db(db_path=store.DEFAULT_DB_PATH)          # idempotent; a fresh home has no tables yet
        store.add_notification(kind, title, body=body[:300], href="/", source_key=source_key)
        return True
    except Exception as e:   # noqa: BLE001
        print("notify failed: %s" % e, file=sys.stderr, flush=True)
        return False


def out(**kw):
    ok = bool(kw.get("ok"))
    if not kw.get("noop"):
        if ok:
            kw["notified"] = notify("info", "hermes-hq updated to %s" % kw.get("sha", "?")[:9],
                                    ", ".join(kw.get("steps", [])), "hq-update:%s" % kw.get("sha", "?"))
        else:
            kw["notified"] = notify("needs_you", "hermes-hq update failed", kw.get("error", ""),
                                    "hq-update-fail:%s:%s" % (kw.get("sha", "?"), int(time.time())))
    print("\n" + json.dumps(kw), flush=True)
    return 0 if ok else 1


def lock_path() -> str:
    """Inside the git dir, NOT the worktree — an untracked lock file would make the tree 'dirty'."""
    try:
        r = subprocess.run(["git", "-C", REPO, "rev-parse", "--git-path", "hq-update.lock"],
                           capture_output=True, text=True, timeout=10)
        p = r.stdout.strip()
        if r.returncode == 0 and p:
            return p if os.path.isabs(p) else os.path.join(REPO, p)
    except OSError:
        pass
    return os.path.join(REPO, ".git", "hq-update.lock")


def main():
    lock = open(lock_path(), "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(json.dumps({"ok": False, "error": "another update is already running", "notified": True}), flush=True)
        return 1
    if sh("git", "fetch", "--quiet", timeout=120).returncode != 0:
        return out(ok=False, error="git fetch failed")
    if sh("git", "status", "--porcelain").stdout.strip():
        return out(ok=False, error="working tree is dirty — refusing to update")
    local = sh("git", "rev-parse", "HEAD").stdout.strip()
    remote = sh("git", "rev-parse", "@{u}").stdout.strip()
    if not remote:
        return out(ok=False, error="no upstream configured")
    behind = sh("git", "rev-list", "--count", "HEAD..@{u}").stdout.strip()
    if behind in ("", "0"):          # nothing to pull — being AHEAD of origin is still a noop
        print("\n" + json.dumps({"ok": True, "noop": True, "sha": local}), flush=True)
        return 0
    changed = sh("git", "diff", "--name-only", "HEAD", remote).stdout.splitlines()
    need_deps = any(f in ("pyproject.toml", "uv.lock") for f in changed)
    need_build = any(f.startswith("frontend/") for f in changed)
    # tool pre-checks BEFORE the pull: never leave the checkout ahead of what we can build
    if need_deps and not shutil.which("uv"):
        return out(ok=False, error="uv not on PATH but dependencies changed — not pulling")
    if need_build and not shutil.which("npm"):
        return out(ok=False, error="npm not on PATH but frontend/ changed — not pulling")
    if sh("git", "pull", "--ff-only", "--quiet", timeout=300).returncode != 0:
        return out(ok=False, error="git pull --ff-only failed (diverged?)")
    steps = ["pull"]
    if need_deps:
        if sh("uv", "pip", "install", "--python", sys.executable, "-q", "-e", ".").returncode != 0:
            return out(ok=False, error="dependency install failed", steps=steps, sha=remote)
        steps.append("deps")
    if need_build:
        if sh("npm", "run", "build", "--prefix", os.path.join(REPO, "frontend"), timeout=900).returncode != 0:
            return out(ok=False, error="frontend build failed", steps=steps, sha=remote)
        steps.append("build")
    sys.path.insert(0, REPO)
    from backend import service
    if service.restart(out=lambda *a: print(*a, flush=True)) != 0:
        return out(ok=False, error="supervisor restart failed", steps=steps, sha=remote)
    steps.append("restart")
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%s/api/health" % PORT, timeout=3) as r:
                if r.status == 200:
                    return out(ok=True, sha=remote, steps=steps, health="ok")
        except OSError:
            pass
        time.sleep(2)
    return out(ok=False, error="health check failed after restart", sha=remote, steps=steps)


if __name__ == "__main__":
    sys.exit(main())
