"""Stop a running run on purpose (owner action), safely against the dispatcher.

The dispatcher's liveness scan would also notice a dead wrapper — but only on
its next tick, and with a "pid is dead" reason that reads like a crash. This
module makes the stop explicit: it holds the same advisory dispatch lock the
tick uses (so no tick can claim/launch/liveness-scan mid-kill), kills the
wrapper's whole process group (wrapper + its `hermes chat` child, launched
with start_new_session=True), waits for the pid to go, and finalizes the run
and task itself with an honest note.

Outcome per `keep_in_queue`:
  False -> run failed("stopped by owner"), task stalled -> manual
  True  -> run failed("stopped by owner"), task stalled -> ready (fresh run next tick)
"""
import fcntl
import os
import signal
import time

from core import wm_dispatch, wm_store as store

STOP_NOTE = "stopped by owner"
GRACE_SECONDS = 5.0


def running_run_for_task(task_id, db_path=None):
    conn = store._connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM runs WHERE task_id=? AND status='running' "
            "ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
    finally:
        conn.close()


def _kill_group(pid, grace=GRACE_SECONDS):
    """SIGTERM the process group of `pid`, escalate to SIGKILL after `grace`.
    Returns a short description of what happened."""
    if not wm_dispatch._process_alive(pid):
        return "pid %s already gone" % pid
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return "pid %s already gone" % pid
    for sig, label in ((signal.SIGTERM, "SIGTERM"), (signal.SIGKILL, "SIGKILL")):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return "pgid %s gone before %s" % (pgid, label)
        deadline = time.time() + grace
        while time.time() < deadline:
            # The wrapper is our child when hermes-hq launched it; reap so the
            # pid does not linger as a zombie and read as "alive".
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            if not wm_dispatch._process_alive(pid):
                return "%s to pgid %s" % (label, pgid)
            time.sleep(0.1)
    return "pgid %s still alive after SIGKILL" % pgid


def stop_task(task_id, keep_in_queue=False, db_path=None):
    """Owner stop. Raises ValueError (-> 409 at the API) when nothing is running."""
    db_path = db_path or store.DEFAULT_DB_PATH
    t = store.get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    run = running_run_for_task(task_id, db_path=db_path)
    if run is None:
        raise ValueError("task %d has no running run (status=%s)" % (task_id, t["status"]))

    store.ensure_runs_dir()
    with open(wm_dispatch._dispatch_lock_path(), "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)  # blocking: wait out an in-flight tick
        try:
            how = _kill_group(run["pid"])
            detail = "%s (%s; keep_in_queue=%s)" % (STOP_NOTE, how, bool(keep_in_queue))
            store.log_activity(action="task_stopped", task_id=task_id, run_id=run["id"],
                               agent_profile=run["agent_profile"], detail=detail, db_path=db_path)
            # run -> failed, task running -> stalled (guarded; no double-finalize)
            store.mark_stalled(run["id"], task_id, STOP_NOTE, db_path=db_path, label="owner stop")
            if run["review_id"]:
                store.set_review_status(run["review_id"], "failed", db_path=db_path)
            if store.get_task(task_id, db_path=db_path)["status"] == "stalled":
                if keep_in_queue:
                    store.retry_task(task_id, db_path=db_path)
                else:
                    store.mark_manual(task_id, note=STOP_NOTE, db_path=db_path)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
    return {"run_id": run["id"], "pid": run["pid"], "kill": how,
            "task_status": store.get_task(task_id, db_path=db_path)["status"]}
