"""Reaping engine-created run worktrees (intent/WorktreeGCPlan.md, slice B).

ONE implementation behind both callers — the dispatcher tick and `wm gc` — so
the automatic path and the manual one can never disagree about what is safe.

The owner's locked decisions (2026-09-11):

* the engine removes a worktree ONLY when there is provably nothing to lose:
  the task is terminal, no run of it is alive, the tree is CLEAN, and it
  terminated more than `GRACE` ago. Anything dirty waits for `wm gc`, which
  parachutes the content before removing it;
* on reap, a `result_paths` entry pointing into the worktree is repointed at
  the main checkout when the file exists there; otherwise it is left broken.
  Slice A applied this retroactively after the fact — carrying it INTO the reap
  is the whole point of doing it here;
* 24-hour grace: a clean worktree holds nothing to debug by definition, but a
  just-failed run is exactly what the owner would open.

Four safety properties, each with a negative control in the test suite. They
are listed in order of how much damage their absence does:

1. **Path confinement.** `os.path.join(base, "/abs")` discards the base — that
   is how the SPA fallback served the owner's password file on 2026-09-10 — so
   every candidate is realpath'd and required to sit strictly under the root.
2. **The clean check fails CLOSED.** Unreadable `.git`, git error, timeout: all
   mean dirty. This is the only thing between the engine and someone's work.
   `-uall` is mandatory: plain `--porcelain` collapses an untracked directory
   to one line, and a worktree reporting 7 untracked paths really held 63 files.
3. **A live run is untouchable.** The run is terminal AND its pid is dead AND
   the task is terminal. Any one failing is enough to hold: a task can read
   `running` with a dead process, and the reverse inside a tick.
4. **Iterate REPOS, not branches.** `git worktree list` from hermes-hq shows
   only hermes-hq's; the 2026-09-07 audit saw 36 of 59 that way. The owning repo
   comes from each directory's own `.git` file and every git command runs there.
"""
import hashlib
import json
import os
import re
import subprocess
import tarfile
import time

GRACE = 24 * 3600
SWEEP_INTERVAL = 3600          # the tick fires every 20s; sweeping is hourly
_SWEEP_WATERMARK = "worktree_last_sweep"
TERMINAL_TASK = ("done", "failed", "cancelled")
TERMINAL_RUN = ("done", "failed", "cancelled")
_WT_PATH = re.compile(r"^(?P<root>.*/runs/worktrees)/(?P<name>run-\d+)/(?P<rel>.*)$")


def worktrees_root(store):
    return os.path.join(store.resolve_runs_dir(), "worktrees") \
        if hasattr(store, "resolve_runs_dir") else \
        os.path.join(os.path.dirname(store.DEFAULT_DB_PATH), "runs", "worktrees")


def git(repo, *args, timeout=30):
    """Never raises: a failure reads as 'unknown', which every caller treats as
    a reason to keep the worktree."""
    try:
        p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def owning_repo(d):
    try:
        with open(os.path.join(d, ".git")) as fh:
            line = fh.read().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = line.split("gitdir:", 1)[1].strip()
    marker = "/.git/worktrees/"
    return gitdir.split(marker)[0] if marker in gitdir else None


def confined(root, path):
    """Property 1."""
    r, p = os.path.realpath(root), os.path.realpath(path)
    return p != r and p.startswith(r + os.sep)


def classify(d):
    """Property 2. (verdict, untracked). Anything unknown is 'dirty'."""
    rc, out = git(d, "status", "--porcelain", "-uall")
    if rc != 0:
        return "unreadable", []
    lines = out.splitlines()
    return ("clean" if not lines else "dirty"), [l[3:] for l in lines if l.startswith("??")]


def run_id_of(name):
    return int(name.split("run-", 1)[1]) if name.startswith("run-") else None


def holds(conn, now=None):
    """Property 3. run_id -> why it must not be touched. None means 'refuse all'."""
    now = now or time.time()
    reasons = {}
    try:
        for r in conn.execute("SELECT id, task_id, status, pid FROM runs"):
            why = []
            if r["status"] not in TERMINAL_RUN:
                why.append("run is %s" % r["status"])
            if r["pid"] and os.path.exists("/proc/%d" % int(r["pid"])):
                why.append("pid %s alive" % r["pid"])
            if why:
                reasons[r["id"]] = ", ".join(why)
        for r in conn.execute(
                "SELECT r.id AS rid, t.id AS tid, t.status AS ts FROM runs r "
                "JOIN tasks t ON t.id = r.task_id "
                "WHERE t.status NOT IN ('done','failed','cancelled')"):
            reasons.setdefault(r["rid"], "task #%s is %s" % (r["tid"], r["ts"]))
        # the grace window, measured from the task's terminal transition
        for r in conn.execute(
                "SELECT r.id AS rid, MAX(s.ts) AS ts FROM runs r "
                "JOIN state_transitions s ON s.task_id = r.task_id "
                "WHERE s.to_status IN ('done','failed','cancelled') GROUP BY r.id"):
            if r["ts"] and (now - float(r["ts"])) < GRACE and r["rid"] not in reasons:
                reasons[r["rid"]] = "terminal %.1fh ago (grace %dh)" % (
                    (now - float(r["ts"])) / 3600.0, GRACE // 3600)
    except Exception:
        return None
    return reasons


def plan_sweep(store, db_path=None, now=None):
    """Read-only. What a sweep WOULD do, and why each survivor survives."""
    root = worktrees_root(store)
    out = {"root": root, "reap": [], "keep": [], "root_missing": False}
    if not os.path.isdir(root):
        out["root_missing"] = True
        return out
    conn = store._connect(db_path)
    try:
        busy = holds(conn, now=now)
    finally:
        conn.close()
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        row = {"name": name, "path": d, "repo": owning_repo(d)}
        verdict, untracked = classify(d)
        row["verdict"], row["untracked"] = verdict, untracked
        rid = run_id_of(name)
        if not confined(root, d):
            row["why"] = "not confined to the worktrees root"
        elif busy is None:
            row["why"] = "cannot read the database — refusing everything"
        elif rid is None:
            row["why"] = "name is not run-<id>; cannot prove it is idle"
        elif rid in busy:
            row["why"] = busy[rid]
        elif verdict != "clean":
            row["why"] = "%s — dirty worktrees wait for `wm gc`" % verdict
        elif not row["repo"]:
            row["why"] = "no owning repo could be read from .git"
        else:
            row["why"] = None
        (out["reap"] if row["why"] is None else out["keep"]).append(row)
    return out


def rescue_links(store, rows, db_path=None, repos=()):
    """Decision 2, carried INTO the reap. Returns (links, tasks)."""
    names = {r["name"] for r in rows}
    conn = store._connect(db_path)
    links = tasks = 0
    try:
        todo = conn.execute("SELECT id, result_paths FROM tasks "
                            "WHERE result_paths LIKE '%runs/worktrees%'").fetchall()
        for t in todo:
            try:
                paths = json.loads(t["result_paths"])
            except Exception:
                continue
            if not isinstance(paths, list):
                continue
            new, moved = [], []
            for p in paths:
                m = _WT_PATH.match(p) if isinstance(p, str) else None
                if not m or m.group("name") not in names:
                    new.append(p)
                    continue
                rel = m.group("rel")
                hit = next((os.path.join(rp, rel) for rp in repos
                            if os.path.exists(os.path.join(rp, rel))), None)
                new.append(hit or p)
                if hit:
                    moved.append(rel)
            if moved:
                with conn:
                    conn.execute("UPDATE tasks SET result_paths=? WHERE id=?",
                                 (json.dumps(new), t["id"]))
                links += len(moved)
                tasks += 1
                store.log_activity(
                    action="result_paths_repointed", task_id=t["id"],
                    detail="worktree reap: %d link(s) repointed (%s)"
                           % (len(moved), ", ".join(moved[:3])[:160]),
                    db_path=db_path)
    finally:
        conn.close()
    return links, tasks


def parachute(rows, dest_dir):
    """Archive every DISTINCT untracked file, by content. Returns (path, count)."""
    files = [(r, rel) for r in rows for rel in r.get("untracked", [])]
    if not files:
        return None, 0
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, "worktree-gc-%s.tar.gz" % time.strftime("%Y%m%d-%H%M%S"))
    seen, added = set(), 0
    with tarfile.open(path, "w:gz") as tar:
        for r, rel in files:
            src = os.path.join(r["path"], rel)
            if not os.path.isfile(src):
                continue
            with open(src, "rb") as fh:
                h = hashlib.sha256(fh.read()).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            tar.add(src, arcname=os.path.join(r["name"], rel))
            added += 1
    return path, added


def sweep(store, db_path=None, dry_run=True, now=None, include_dirty=False,
          parachute_dir=None):
    """Reap what is provably safe. `include_dirty` is the `wm gc` path and
    parachutes first; the dispatcher never sets it."""
    plan = plan_sweep(store, db_path=db_path, now=now)
    rows = list(plan["reap"])
    if include_dirty:
        rows += [r for r in plan["keep"]
                 if r["why"] and r["why"].endswith("wait for `wm gc`")]
    plan["targets"] = rows
    plan["removed"], plan["failed"], plan["parachute"] = [], [], None
    plan["links"], plan["link_tasks"] = 0, 0
    if dry_run or not rows:
        return plan

    if include_dirty:
        dest = parachute_dir or os.path.join(os.path.dirname(plan["root"]), "parachutes")
        p, n = parachute(rows, dest)
        plan["parachute"] = {"path": p, "files": n}
        if n == 0 and any(r.get("untracked") for r in rows):
            plan["failed"].append(("parachute", "nothing archived but untracked files exist"))
            return plan

    repos = sorted({r["repo"] for r in rows if r["repo"]})
    plan["links"], plan["link_tasks"] = rescue_links(store, rows, db_path=db_path,
                                                     repos=repos)
    for r in rows:
        rc, out = git(r["repo"], "worktree", "remove", "--force", r["path"])
        (plan["removed"] if rc == 0 else plan["failed"]).append(
            r["name"] if rc == 0 else (r["name"], out[:120]))
    for repo in repos:
        git(repo, "worktree", "prune")
    return plan


def sweep_due(store, db_path=None, now=None):
    """Hourly, not every 20-second tick: a sweep shells out to git per worktree."""
    now = now or time.time()
    last = store.get_meta(_SWEEP_WATERMARK, db_path=db_path)
    return last is None or (now - float(last)) >= SWEEP_INTERVAL


def mark_swept(store, db_path=None, now=None):
    store._set_meta(_SWEEP_WATERMARK, str(now or time.time()), db_path=db_path)
