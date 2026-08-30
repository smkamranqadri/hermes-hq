"""Background jobs for slow Hermes CLI/bridge work (Group 6): a subprocess whose combined output
goes to <hq home>/jobs/<id>.log; the browser polls GET /api/jobs/{id}. If the last stdout line is
JSON it is exposed as `result`. Never a shell string — argv lists only."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
import time

from fastapi import APIRouter, HTTPException

from core import wm_store as store

router = APIRouter(prefix="/api/jobs")
JOBS: dict[str, "Job"] = {}
KEEP = 200            # finished jobs remembered in memory


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

    def info(self, tail_bytes: int = 4000):
        tail = ""
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, 2); size = f.tell(); f.seek(max(0, size - tail_bytes))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            pass
        return {"id": self.id, "kind": self.kind, "label": self.label, "status": self.status, "exit_code": self.exit_code,
                "started": self.started, "finished": self.finished, "result": self.result, "log": tail}


def jobs_dir():
    d = os.path.join(store.hq_home(), "jobs")
    os.makedirs(d, exist_ok=True)
    return d


def start(kind: str, label: str, argv: list[str], *, env: dict | None = None, cwd: str | None = None, stdin: str | None = None) -> Job:
    job = Job(kind, label)
    log = open(job.log_path, "wb")
    job.proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                env=env, cwd=cwd, start_new_session=True)
    if stdin is not None:
        job.proc.stdin.write(stdin.encode()); job.proc.stdin.close()
    JOBS[job.id] = job

    def wait():
        code = job.proc.wait()
        log.close()
        job.exit_code, job.finished = code, time.time()
        try:
            with open(job.log_path, "rb") as f:
                lines = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
            if lines and lines[-1].startswith("{"):
                job.result = json.loads(lines[-1])
        except (OSError, ValueError):
            pass
        ok = code == 0 and not (isinstance(job.result, dict) and job.result.get("ok") is False)
        job.status = "done" if ok else "failed"
        _trim()

    threading.Thread(target=wait, daemon=True).start()
    return job


def _trim():
    done = [j for j in JOBS.values() if j.status != "running"]
    for j in sorted(done, key=lambda j: j.finished or 0)[:-KEEP]:
        JOBS.pop(j.id, None)
        try:
            os.unlink(j.log_path)
        except OSError:
            pass


@router.get("")
def list_jobs():
    return {"jobs": [j.info(tail_bytes=0) for j in sorted(JOBS.values(), key=lambda j: -j.started)][:50]}


@router.get("/{jid}")
def get_job(jid: str):
    j = JOBS.get(jid)
    if j is None:
        raise HTTPException(404, "no such job")
    return j.info()
