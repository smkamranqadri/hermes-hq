#!/usr/bin/env python3
"""Detached updater for hermes-hq (`service update` and the scheduled auto-update).

Runs OUTSIDE the request path (jobs.py subprocess or the CLI): fetch → noop when already
up to date → refuse a dirty tree → pull --ff-only → reinstall deps only when the Python
project changed → rebuild the UI only when frontend/ changed → supervisor restart → poll
/api/health for 60 s. Last stdout line is the JSON result the job runner parses.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

REPO = os.environ.get("HERMES_HQ_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = os.environ.get("HERMES_HQ_PORT", "9010")


def sh(*cmd, timeout=600):
    print("$ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
    if r.stdout:
        print(r.stdout[-4000:], flush=True)
    if r.stderr:
        print(r.stderr[-4000:], file=sys.stderr, flush=True)
    return r


def out(**kw):
    print("\n" + json.dumps(kw), flush=True)
    return 0 if kw.get("ok") else 1


def main():
    if sh("git", "fetch", "--quiet", timeout=120).returncode != 0:
        return out(ok=False, error="git fetch failed")
    if sh("git", "status", "--porcelain").stdout.strip():
        return out(ok=False, error="working tree is dirty — refusing to update")
    local = sh("git", "rev-parse", "HEAD").stdout.strip()
    remote = sh("git", "rev-parse", "@{u}").stdout.strip()
    if not remote:
        return out(ok=False, error="no upstream configured")
    if local == remote:
        return out(ok=True, noop=True, sha=local)
    changed = sh("git", "diff", "--name-only", "HEAD", remote).stdout.splitlines()
    if sh("git", "pull", "--ff-only", "--quiet", timeout=300).returncode != 0:
        return out(ok=False, error="git pull --ff-only failed (diverged?)")
    steps = ["pull"]
    if any(f in ("pyproject.toml", "uv.lock") for f in changed):
        py = sys.executable
        if sh("uv", "pip", "install", "--python", py, "-q", "-e", ".").returncode != 0:
            return out(ok=False, error="dependency install failed", steps=steps)
        steps.append("deps")
    if any(f.startswith("frontend/") for f in changed):
        if sh("npm", "run", "build", "--prefix", os.path.join(REPO, "frontend"), timeout=900).returncode != 0:
            return out(ok=False, error="frontend build failed", steps=steps)
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
