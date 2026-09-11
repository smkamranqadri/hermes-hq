#!/usr/bin/env python3
"""wm_dispatch.py — the T2 dispatcher for the Hermes Work Manager.

Runs a single "tick" of the dispatch loop. It is invoked two ways
(identical code path — see run_dispatch()):
  1. manually, from the CLI  ->  `wm dispatch`  (wm_cli.cmd_dispatch)
  2. automatically, every 60s via a hermes cron no-agent script job.

Per tick it:
  1. skips entirely if the store is paused;
  2. promotes planned -> ready any task whose deps are all 'done';
  3. claims up to (concurrency_cap - runningCount) ready tasks (oldest first);
  4. for each claimed task inserts a runs row, renders + writes its brief,
     then launches the run wrapper DETACHED (outlives the tick);
  5. writes activity rows;
  6. liveness-scan: marks stalled any 'running' run whose wrapper process is
     dead but not finalized, or whose Launched session has been idle past
     stall_seconds (liveness is judged by process + session activity, NOT by
     the wrapper's own heartbeat).

 Stdlib only.
"""

import fcntl
import os
import subprocess
import sys
import time

try:
    from core import wm_store as store
except ImportError:  # run as a bare script from the engine dir
    import wm_store as store

DEFAULT_ASSIGNEE = "coder"


def _dispatch_lock_path():
    return os.path.join(store.resolve_runs_dir(), ".dispatch.lock")


def _resolve():
    """Env-overridable runtime constants, shared with wm_run_agent."""
    return {
        "py": store.resolve_py(),
        "hermes": store.resolve_hermes(),
        "runs_dir": store.resolve_runs_dir(),
        "profiles_dir": store.resolve_profiles_dir(),
        "run_agent": store.WM_RUN_AGENT,
    }


def resolve_start_point(task, run_id, db_path=None):
    """Which branch a new isolated run's worktree should be cut FROM.

    Returns (branch_or_None, reason). None means the primary repo's HEAD —
    the fallback, and what every run did before WorktreeStartPointPlan.

    The defect this replaces: `git worktree add -b <branch> <path>` with no
    start-point branches from the primary HEAD, so a BUILD run and the REVIEW
    run that is supposed to check it both started at `main` and landed as
    SIBLINGS. Measured on project 7 (2026-09-11): build `1d6642b` and review
    `dcd2d19` both parented on `d678fb7`, 9 files apart, and the reviewer
    approved a tree the coder never produced.

    Precedence, by RUN RECENCY rather than task status:

      1. both a parent build branch and this task's own earlier branch exist
         -> whichever run is NEWER wins.
      2. only a parent's        -> the parent's (a review finally sees its build)
      3. only this task's own   -> resume it (a rework amends its own work
                                   instead of restarting from main)
      4. neither                -> None (primary HEAD)

    Recency, not `task["status"] == 'rework'`, for two reasons. The status is
    already gone by the time this runs — `claim_task()` flips ready/rework to
    'running' BEFORE the dispatcher reaches here, so the row we hold carries a
    stale pre-claim value. And a plain "rework resumes its own branch" rule is
    wrong in a real case: when the parent BUILD rebuilds while a REVIEW is
    pending (exactly the #280/#281 shape), the review must re-base on the NEW
    build, not resume its own now-stale branch. Comparing run ids gets both.

    Multiple completed parents with branches REFUSE rather than guess: there is
    no defensible pick, so fall back to the primary HEAD and say so, loudly, in
    the brief — a reviewer is entitled to know it is not looking at a build.
    """
    own = store.get_task_last_branched_run(task["id"], exclude_run_id=run_id,
                                           db_path=db_path)
    parents = []
    for d in store.list_task_deps(task["id"], db_path=db_path):
        pt = store.get_task(d["depends_on_task_id"], db_path=db_path)
        if pt is None or pt["status"] != "done":
            continue
        pr = store.get_task_last_branched_run(pt["id"], db_path=db_path)
        if pr is not None:
            parents.append((pt, pr))
    if len(parents) > 1:
        names = ", ".join("#%s" % pt["id"] for pt, _ in parents)
        if own is not None:
            return own["branch"], (
                "resumed this task's own previous run %d (%s); %d completed "
                "parents (%s) carry branches and there is no defensible pick "
                "between them" % (own["id"], own["branch"], len(parents), names))
        return None, (
            "branched from the primary HEAD because %d completed parents (%s) "
            "carry branches and there is no defensible pick between them — "
            "THIS TREE IS NOT A PARENT'S BUILD, do not review it as one"
            % (len(parents), names))
    parent = parents[0] if parents else None
    if parent is not None and own is not None:
        pt, pr = parent
        if pr["id"] > own["id"]:
            return pr["branch"], (
                "branched from parent task #%s's run %d (%s) — newer than this "
                "task's own last run %d, so the parent produced fresh work to "
                "build on" % (pt["id"], pr["id"], pr["branch"], own["id"]))
        return own["branch"], (
            "resumed this task's own previous run %d (%s) — newer than parent "
            "task #%s's run %d, so your earlier work is the base to amend"
            % (own["id"], own["branch"], pt["id"], pr["id"]))
    if parent is not None:
        pt, pr = parent
        return pr["branch"], (
            "branched from parent task #%s's run %d (%s) — this tree CONTAINS "
            "that work" % (pt["id"], pr["id"], pr["branch"]))
    if own is not None:
        return own["branch"], (
            "resumed this task's own previous run %d (%s) — your earlier work "
            "is the base to amend, not a clean slate" % (own["id"], own["branch"]))
    return None, ("branched from the primary HEAD — this task has no completed "
                  "parent with a branch and no earlier run of its own")


def run_workdir(task, project, run_id, db_path=None):
    """Determine the run's EFFECTIVE working directory + (optional) branch.

    Artifact/run-safety (fix #6): for a CODE task in a git repo, each run gets
    its own git worktree on a dedicated branch `wm/run-<run_id>`, so a failed/
    retried run never writes over another run's (or main's) files. Non-code
    tasks keep working in the project's primary_path (worktrees are NOT forced
    on non-code work).

    Returns (workdir, branch, base_branch, base_reason). When isolated, the
    worktree is created now and the run row is tagged by the caller.
    """
    primary = (project["primary_path"] if project and project["primary_path"]
               else os.getcwd())
    use_worktree = (
        bool(task["is_code"])
        and (store.get_meta("code_worktree", db_path=db_path) or "1") == "1"
    )
    if not use_worktree:
        return primary, None, None, None
    # Only isolate when the project is actually a git working tree.
    try:
        subprocess.run(["git", "-C", primary, "rev-parse", "--is-inside-work-tree"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return primary, None, None, None
    wt_root = os.path.join(store.resolve_runs_dir(), "worktrees")
    os.makedirs(wt_root, exist_ok=True)
    wt = os.path.join(wt_root, "run-%d" % run_id)
    branch = "wm/run-%d" % run_id
    try:
        subprocess.run(["git", "-C", primary, "worktree", "prune"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    # WorktreeStartPointPlan slice A: cut the branch from the parent BUILD (or
    # from this task's own earlier work) instead of always from the primary
    # HEAD. Passing a start-point is the whole fix — without it git uses HEAD.
    base_branch, base_reason = resolve_start_point(task, run_id, db_path=db_path)
    add = ["git", "-C", primary, "worktree", "add", "-b", branch, wt]
    if base_branch:
        add.append(base_branch)
    r = subprocess.run(add, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, text=True)
    if r.returncode != 0 and base_branch:
        # The start-point is a best effort, never a reason to refuse the run:
        # a branch can have been pruned by worktree GC between runs. Fall back
        # to the primary HEAD and RECORD that we did, so a review is never
        # silently handed a tree it believes contains a build.
        r = subprocess.run(["git", "-C", primary, "worktree", "add", "-b",
                            branch, wt], stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True)
        if r.returncode == 0:
            base_reason = ("branched from the primary HEAD: intended "
                           "start-point %r is unusable (%s) — %s"
                           % (base_branch,
                              (r.stderr or "").strip().splitlines()[-1:] or "no detail",
                              base_reason))
            base_branch = None
    if r.returncode == 0:
        return wt, branch, base_branch, base_reason
    # Worktree/branch collision (e.g. branch already exists): use the branch by
    # checking it out into a fresh path; last resort is the primary path.
    r2 = subprocess.run(["git", "-C", primary, "worktree", "add", wt, branch],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r2.returncode == 0:
        return wt, branch, base_branch, base_reason
    return primary, None, None, None


def _process_alive(pid):
    """True if process `pid` exists (is alive)."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def check_stall(run, stall_seconds, now=None):
    """Judge whether a single 'running' run is stalled. Returns None if not
    stalled, else a human-readable reason string.

    A run is stalled when either:
      - its wrapper process is dead but the run was never finalized; or
      - its Launched agent session's last_activity_at is older than
        stall_seconds (and the session can be located).
    A run with a live process but no session yet recorded is treated as
    still-starting (not stalled) to avoid false positives on fresh launches.
    """
    now = now if now is not None else time.time()
    pid_alive = _process_alive(run["pid"])
    if not pid_alive:
        return ("process pid %s is dead but run %d was never finalized"
                % (run["pid"], run["id"]))
    # Process alive: judge by Hermes session last_activity_at. Resolve the
    # run's live session via its recorded session_id OR its planted marker
    # title (so activity liveness works mid-run, before the wrapper captures
    # the id).
    session = store.get_run_session_activity(
        run["agent_profile"], run["id"],
        session_id=run["session_id"] if run["session_id"] else None)
    if session is None or session["last_activity_at"] is None:
        return None  # no located session yet -> still starting
    idle = now - session["last_activity_at"]
    if idle > stall_seconds:
        return ("process alive but session %s idle for %.0fs > stall_seconds=%s"
                % (session["id"], idle, stall_seconds))
    return None


def _launch(run_id, agent, brief_text, cwd, cfg, db_path, log_action):
    """Write the run's brief, spawn its wrapper DETACHED, record pid + activity.
    Returns True on success; on failure finalizes the run+task/review as failed."""
    store.ensure_runs_dir()
    bpath = store.brief_path(run_id)
    with open(bpath, "w") as f:
        f.write(brief_text)
    store.set_run_brief(run_id, bpath, db_path=db_path)
    env = os.environ.copy()
    path = env.get("PATH", "")
    hbin = os.path.dirname(cfg["hermes"])
    resolve_hq_cli = getattr(store, "resolve_hq_cli", None)
    hq_cli = (resolve_hq_cli() if resolve_hq_cli else
              os.environ.get("HERMES_HQ_CLI") or
              os.path.join(os.environ.get("HERMES_HQ_REPO", os.getcwd()),
                           ".venv", "bin", "hermes-hq"))
    qbin = os.path.dirname(hq_cli)
    for bindir in (hbin, qbin):
        if bindir and bindir not in env.get("PATH", "").split(os.pathsep):
            env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    resolve_hq_repo = getattr(store, "resolve_hq_repo", None)
    env["HERMES_HQ_REPO"] = (resolve_hq_repo() if resolve_hq_repo else
                              os.environ.get("HERMES_HQ_REPO", os.getcwd()))
    env["WM_DB"] = db_path
    env["WM_RUNS_DIR"] = cfg["runs_dir"]
    env["WM_PROFILES_DIR"] = cfg["profiles_dir"]
    env["WM_HERMES"] = cfg["hermes"]
    env["WM_PY"] = cfg["py"]
    with open(store.run_log_path(run_id), "ab", buffering=0) as logfile:
        try:
            proc = subprocess.Popen(
                [cfg["py"], cfg["run_agent"], str(run_id), agent, bpath],
                start_new_session=True, cwd=cwd,
                stdout=logfile, stderr=logfile, env=env)
        except Exception as e:
            store.finish_run(run_id, status="failed",
                             error="failed to spawn wrapper: %s" % e,
                             db_path=db_path)
            store.log_activity(action="run_spawn_failed", run_id=run_id,
                               agent_profile=agent, detail=str(e),
                               db_path=db_path)
            return False
    store.set_run_pid(run_id, proc.pid, db_path=db_path)
    store.log_activity(action=log_action, run_id=run_id, agent_profile=agent,
                       detail="launched pid=%d" % proc.pid, db_path=db_path)
    return True


def run_dispatch(db_path=None):
    """Run one dispatch tick. Returns a summary dict for callers/CLI output.

    db_path defaults to store.DEFAULT_DB_PATH (which tests may redirect).
    The exact paths/hermes used here are passed into the spawned wrapper's
    environment so dispatch and the run wrapper always agree.

    SINGLE-FLIGHT (fix #3): the whole tick (promote + claim + launch + liveness)
    runs under a non-blocking advisory file lock. `wm dispatch` (manual) and the
    cron auto-tick share this exact path, and only ONE tick can hold the lock at
    a time — overlapping manual/cron runs serially skip each other instead of
    double-launching or overshooting the concurrency cap.
    """
    db_path = db_path or os.environ.get("WM_DB") or store.DEFAULT_DB_PATH
    cfg = _resolve()
    store.ensure_runs_dir()
    summary = {
        "paused": False, "skipped": False, "backup": None,
        "promoted": [], "dispatched": [], "stalled": [],
        "reviews_dispatched": [],
        "ready_candidates": 0, "running_count": 0, "cap": 0, "errors": [],
    }

    # 0. single-flight: if another tick holds the lock, skip without doing work.
    lock_path = _dispatch_lock_path()
    with open(lock_path, "w") as lock_fh:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            summary["skipped"] = True
            return summary

        try:
            # 1. paused -> exit silently.
            if store.get_meta("paused", db_path=db_path) == "1":
                summary["paused"] = True
                return summary

            # 2. promote released+eligible tasks: `waiting_approval` with deps done
            # -> `ready`. `planned` tasks are NEVER auto-promoted (release gate).
            summary["promoted"] = store.promote_waiting_approval_ready(db_path=db_path)

            # 3. capacity: running WORK runs + running REVIEW runs count together.
            try:
                cap = int(store.get_meta("concurrency_cap", db_path=db_path) or 3)
            except (TypeError, ValueError):
                cap = 3
            running_count = store.running_run_count(db_path=db_path)
            summary["running_count"] = running_count
            summary["cap"] = cap
            available = max(0, cap - running_count)
            ready = store.next_ready_tasks(available, db_path=db_path)
            summary["ready_candidates"] = len(ready)

            # 4. claim + launch WORK runs.
            for t in ready:
                if not store.claim_task(t["id"], db_path=db_path):
                    continue  # lost the race (another tick claimed it)
                agent = t["assignee_profile"] or DEFAULT_ASSIGNEE
                project = store.get_project(t["project_id"], db_path=db_path)
                primary_path = (project["primary_path"] if project else os.getcwd())
                if not primary_path or not os.path.isdir(primary_path):
                    store.complete_run(t["id"], status="failed",
                                       error="primary_path missing: %r" % primary_path,
                                       db_path=db_path)
                    summary["errors"].append(
                        "task %d: primary_path invalid; not dispatched" % t["id"])
                    continue
                run_id = store.start_run(t["id"], agent, db_path=db_path)
                # Per-run work-dir isolation for CODE tasks (fix #6): a worktree +
                # branch per run so retries never clobber another run's files.
                wd, branch, base_b, base_r = run_workdir(
                    t, project, run_id, db_path=db_path)
                store.set_run_workdir(run_id, wd, branch, db_path=db_path,
                                      base_branch=base_b, base_reason=base_r)
                brief_text = store.render_brief(run_id, db_path=db_path)
                if not _launch(run_id, agent, brief_text, wd, cfg, db_path,
                               "run_dispatched"):
                    store.complete_run(t["id"], status="failed",
                                       error="wrapper spawn failed", db_path=db_path)
                    summary["errors"].append("task %d spawn failed" % t["id"])
                    continue
                summary["dispatched"].append(run_id)

            # 4b. T5: dispatch pending reviews (SINGLE review model), atomically.
            # Each pending review is claimed ('pending'->'running') so overlapping
            # ticks can never spawn two Reviewer runs for the same review. Reviews
            # only launch within the capacity that WORK runs left free.
            remaining = max(0, cap - store.running_run_count(db_path=db_path))
            for rv in store.pending_reviews(db_path=db_path):
                if remaining <= 0:
                    break  # no slot: leave the review pending for a later tick
                if not store.claim_review(rv["id"], db_path=db_path):
                    continue  # another tick already claimed this review
                origin = store.get_task(rv["task_id"], db_path=db_path)
                if origin is None:
                    store.set_review_status(rv["id"], "failed", db_path=db_path)
                    continue
                project = store.get_project(origin["project_id"], db_path=db_path)
                primary_path = (project["primary_path"] if project else os.getcwd())
                if not primary_path or not os.path.isdir(primary_path):
                    store.set_review_status(rv["id"], "blocked", db_path=db_path)
                    summary["errors"].append(
                        "review %d: origin primary_path invalid" % rv["id"])
                    continue
                run_id = store.start_run(origin["id"], "reviewer", db_path=db_path)
                store.set_run_review(run_id, rv["id"], db_path=db_path)
                brief_text = store.render_brief(run_id, db_path=db_path)
                if not _launch(run_id, "reviewer", brief_text, primary_path, cfg,
                               db_path, "review_dispatched"):
                    store.set_review_status(rv["id"], "failed", db_path=db_path)
                    summary["errors"].append("review %d spawn failed" % rv["id"])
                    continue
                summary["reviews_dispatched"].append(run_id)
                remaining -= 1

            # 4c. operational: cheap periodic backup (guard; typically daily).
            try:
                summary["backup"] = store.maybe_auto_backup(db_path=db_path)
            except Exception as e:
                summary["errors"].append("auto-backup failed: %s" % e)

            # 5. liveness: flag stalled running runs.
            try:
                stall_seconds = int(
                    store.get_meta("stall_seconds", db_path=db_path) or 300)
            except (TypeError, ValueError):
                stall_seconds = 300
            for run in store.running_runs(db_path=db_path):
                reason = check_stall(run, stall_seconds)
                if reason:
                    store.mark_stalled(run["id"], run["task_id"], reason,
                                       db_path=db_path)
                    summary["stalled"].append(run["id"])
                    store.log_activity(action="run_stalled", run_id=run["id"],
                                       task_id=run["task_id"],
                                       agent_profile=run["agent_profile"],
                                       detail=reason, db_path=db_path)
                    if run["review_id"]:
                        # A stalled reviewer run updates the review row (the origin
                        # task itself stays 'needs_review' — mark_stalled only
                        # touches a 'running' task, so it is left untouched here).
                        store.set_review_status(run["review_id"], "failed",
                                                db_path=db_path)

            # 5. reap spent run worktrees (intent/WorktreeGCPlan.md slice B).
            # Hourly, not every 20-second tick — a sweep shells out to git once
            # per worktree. Never touches a dirty tree; that is `wm gc`'s job.
            # Failure here must never break a dispatch tick: the engine's job is
            # to run work, and garbage collection is strictly secondary.
            try:
                from core import worktree_gc as gc
                if gc.sweep_due(store, db_path=db_path):
                    res = gc.sweep(store, db_path=db_path, dry_run=False)
                    gc.mark_swept(store, db_path=db_path)
                    if res["removed"]:
                        summary["reaped"] = res["removed"]
                        store.log_activity(
                            action="worktrees_reaped",
                            detail="reaped %d spent worktree(s): %s%s"
                                   % (len(res["removed"]), ", ".join(res["removed"][:5]),
                                      " (+%d links repointed)" % res["links"]
                                      if res["links"] else ""),
                            db_path=db_path)
            except Exception as e:      # noqa: BLE001 - GC never breaks dispatch
                summary["errors"].append("worktree gc: %s" % e)

            return summary
        finally:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_UN)
            except Exception:
                pass

    return summary


def main(argv=None):
    """Script entry (used by the cron no-agent job and manual `python wm_dispatch.py`).

    Prints nothing when the tick was a quiet no-op (so a no-agent cron job
    stays silent), and a short line only when it actually did something.
    """
    summary = run_dispatch()
    lines = []
    if summary["paused"]:
        return 0  # silent when paused
    if summary["promoted"]:
        lines.append("promoted to ready: %s" % _ids(summary["promoted"]))
    if summary["dispatched"]:
        lines.append("dispatched runs: %s" % _ids(summary["dispatched"]))
    if summary["reviews_dispatched"]:
        lines.append("dispatched review runs: %s" % _ids(summary["reviews_dispatched"]))
    if summary["stalled"]:
        lines.append("stalled runs: %s" % _ids(summary["stalled"]))
    if summary["errors"]:
        lines.append("errors: %s" % "; ".join(summary["errors"]))
    for ln in lines:
        print("wm_dispatch: %s" % ln)
    return 0


def _ids(seq):
    return ", ".join(str(x) for x in seq) or "-"


if __name__ == "__main__":
    sys.exit(main())