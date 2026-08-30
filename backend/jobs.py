"""Background jobs for slow Hermes CLI/bridge work (Group 6): a subprocess whose combined output
goes to <hq home>/jobs/<id>.log; the browser polls GET /api/jobs/{id}. If the last stdout line is
JSON it is exposed as `result`. Never a shell string — argv lists only."""
from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import threading
import time
from typing import Callable

from fastapi import APIRouter, HTTPException

from core import wm_store as store

router = APIRouter(prefix="/api/jobs")
JOBS: dict[str, "Job"] = {}
LOCK = threading.Lock()
KEEP = 200            # finished jobs remembered in memory
MAX_RUNNING = 4       # concurrent jobs; more → 429
DEFAULT_TIMEOUT = 30 * 60   # seconds before a job is killed (network installs that hang)
ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "USER", "LOGNAME",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


def child_env(**extra: str) -> dict[str, str]:
    """Allow-listed environment for Hermes children: never the server's own secrets/tokens (provider
    setup manifests and hub install hooks run third-party commands)."""
    env = {k: v for k, v in os.environ.items() if k in ENV_KEEP}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.update({k: v for k, v in extra.items() if v is not None})
    return env


class Job:
    def __init__(self, kind: str, label: str):
        self.id = secrets.token_hex(6)
        self.kind, self.label = kind, label
        self.status = "running"
        self.exit_code: int | None = None
        self.started = time.time()
        self.finished: float | None = None
        self.result = None
        self.log_path = os.path.join(jobs_dir(), f"{self.id}.log")
        self.proc: subprocess.Popen | None = None
        self.timed_out = False
        self.stopped = False

    def info(self, tail_bytes: int = 4000):
        tail = ""
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, 2); size = f.tell(); f.seek(max(0, size - tail_bytes))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            pass
        return {"id": self.id, "kind": self.kind, "label": self.label, "status": self.status, "exit_code": self.exit_code,
                "started": self.started, "finished": self.finished, "result": self.result, "log": tail,
                "timed_out": self.timed_out, "stopped": self.stopped}

    def stop(self):
        if self.proc is None or self.status != "running":
            return
        self.stopped = True
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


def jobs_dir():
    d = os.path.join(store.hq_home(), "jobs")
    os.makedirs(d, exist_ok=True)
    return d


def start(kind: str, label: str, argv: list[str], *, env: dict | None = None, cwd: str | None = None, stdin: str | None = None,
          timeout: float | None = None, on_done: Callable[["Job"], None] | None = None) -> Job:
    timeout = timeout or DEFAULT_TIMEOUT
    with LOCK:
        if sum(1 for j in JOBS.values() if j.status == "running") >= MAX_RUNNING:
            raise HTTPException(429, f"at most {MAX_RUNNING} jobs run at once — wait for one to finish")
    if not os.path.exists(argv[0]):
        raise HTTPException(503, f"command not found: {argv[0]}")
    job = Job(kind, label)
    log = open(job.log_path, "wb")
    try:
        job.proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    env=env if env is not None else child_env(), cwd=cwd, start_new_session=True)
    except OSError as e:
        log.close()
        try:
            os.unlink(job.log_path)
        except OSError:
            pass
        raise HTTPException(503, f"could not start {argv[0]}: {e}")
    if stdin is not None:
        try:
            job.proc.stdin.write(stdin.encode()); job.proc.stdin.close()
        except OSError:
            pass
    with LOCK:
        JOBS[job.id] = job

    def wait():
        try:
            code = job.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            job.timed_out = True
            try:
                os.killpg(job.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            code = job.proc.wait()
        log.close()
        job.exit_code, job.finished = code, time.time()
        job.result = _last_json(job.log_path)
        ok = code == 0 and not job.timed_out and not job.stopped and not (isinstance(job.result, dict) and job.result.get("ok") is False)
        job.status = "done" if ok else "failed"
        if on_done:
            try:
                on_done(job)
            except Exception:
                pass
        _trim()

    threading.Thread(target=wait, daemon=True).start()
    return job


def _last_json(path: str):
    """The last line of the log that parses as a JSON object (Hermes may print warnings after the result)."""
    try:
        with open(path, "rb") as f:
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def _trim():
    with LOCK:
        done = [j for j in JOBS.values() if j.status != "running"]
        old = sorted(done, key=lambda j: j.finished or 0)[:-KEEP]
        for j in old:
            JOBS.pop(j.id, None)
    for j in old:
        try:
            os.unlink(j.log_path)
        except OSError:
            pass


def stop_all():
    with LOCK:
        running = [j for j in JOBS.values() if j.status == "running"]
    for j in running:
        j.stop()


@router.get("")
def list_jobs():
    with LOCK:
        jobs = list(JOBS.values())
    return {"jobs": [j.info(tail_bytes=0) for j in sorted(jobs, key=lambda j: -j.started)][:50]}


@router.post("/{jid}/stop")
def stop_job(jid: str):
    j = JOBS.get(jid)
    if j is None:
        raise HTTPException(404, "no such job")
    j.stop()
    return {"id": j.id, "status": j.status, "stopping": j.status == "running"}


@router.get("/{jid}")
def get_job(jid: str):
    j = JOBS.get(jid)
    if j is None:
        raise HTTPException(404, "no such job")
    return j.info()
