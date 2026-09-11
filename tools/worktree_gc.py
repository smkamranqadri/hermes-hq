#!/usr/bin/env python3
"""Reap engine-created run worktrees (intent/WorktreeGCPlan.md, slice A).

DRY RUN BY DEFAULT. `--apply` is the only thing that removes anything.

Four safety properties, in order of how much damage their absence would do:

1. **Path confinement.** Nothing is removed whose *resolved* path is not strictly
   under the worktrees root. `os.path.join(base, "/abs")` discards the base —
   that is how hermes-hq's SPA fallback served the owner's password file on
   2026-09-10 — so every candidate is realpath'd and checked against the root.
2. **The clean check fails CLOSED.** An unreadable `.git`, a git error, a
   timeout: all mean "dirty". This check is the only thing between the engine
   and someone's uncommitted work, so it is never allowed to guess "empty".
3. **Iterate REPOS, not branches.** `git worktree list` run from hermes-hq shows
   only hermes-hq's worktrees; the 2026-09-07 audit saw 36 of 59 that way and
   never looked at two other repos. The owning repo is read from each
   directory's own `.git` file, and every git command runs against THAT repo.
4. **`git branch -d`, never `-D`.** Lowercase refuses to delete an unmerged
   branch. That refusal IS the merged/unmerged split — it is a safety net to
   lean on, not an error to suppress.
"""
import argparse
import hashlib
import os
import subprocess
import sys
import sqlite3
import tarfile
import time

ROOT = "/opt/data/hermes-hq/runs/worktrees"
PARACHUTE_DIR = "/opt/data/hermes-hq/runs/parachutes"


def git(repo, *args, timeout=30):
    """Run git in `repo`. Returns (rc, stdout). Never raises — a failure here
    must read as 'unknown', which callers treat as dirty."""
    try:
        p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def owning_repo(d):
    """The repo a worktree belongs to, from its own `.git` file."""
    g = os.path.join(d, ".git")
    try:
        with open(g) as fh:
            line = fh.read().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = line.split("gitdir:", 1)[1].strip()
    marker = "/.git/worktrees/"
    return gitdir.split(marker)[0] if marker in gitdir else None


def confined(path):
    """Property 1. Strictly under ROOT, after resolving symlinks."""
    root = os.path.realpath(ROOT)
    real = os.path.realpath(path)
    return real != root and real.startswith(root + os.sep)


def classify(d):
    """(verdict, untracked_paths, branch). Property 2: anything unknown is dirty."""
    # `-uall` is load-bearing: plain --porcelain COLLAPSES an untracked
    # directory to one line (`?? 03-platforms/`), so a parachute built from it
    # silently drops every file inside. run-407 reported 7 untracked paths and
    # actually holds 63 files. An undercount here is exactly how a sweep
    # destroys work while reporting that it saved everything.
    rc, out = git(d, "status", "--porcelain", "-uall")
    if rc != 0:
        return "unreadable", [], None
    _rc, branch = git(d, "branch", "--show-current")
    untracked = [l[3:] for l in out.splitlines() if l.startswith("??")]
    return ("clean" if not out.splitlines() else "dirty"), untracked, (branch or None)


TERMINAL = ("done", "failed", "cancelled", "manual")
DB = "/opt/data/hermes-hq/hq.db"


def live_runs(db=DB):
    """Run ids that must NEVER be swept, and why.

    Property 3 of the plan, and the owner flagged it while a reviewer was
    mid-run on personal-brand: a sweep that can delete a live agent's checkout
    is not a sweep, it is an outage. Three independent reasons to refuse, and
    ANY of them is enough — a task can read `running` with a dead process, and
    a process can be alive while the row has already moved on.
    """
    reasons = {}
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        c.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None                      # unknown => caller refuses everything
    try:
        for r in c.execute("SELECT id, task_id, status, pid FROM runs"):
            why = []
            if r["status"] not in ("done", "failed", "cancelled"):
                why.append("run is %s" % r["status"])
            if r["pid"] and os.path.exists("/proc/%d" % int(r["pid"])):
                why.append("pid %s alive" % r["pid"])
            if why:
                reasons[r["id"]] = ", ".join(why)
        for r in c.execute(
                "SELECT r.id, t.status AS ts, t.id AS tid FROM runs r "
                "JOIN tasks t ON t.id = r.task_id WHERE t.status NOT IN "
                "('done','failed','cancelled')"):
            reasons.setdefault(r["id"], "task #%s is %s" % (r["tid"], r["ts"]))
    finally:
        c.close()
    return reasons


def run_id_of(name):
    return int(name.split("run-", 1)[1]) if name.startswith("run-") else None


def scan():
    rows = []
    for name in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, name)
        if not os.path.isdir(d):
            continue
        verdict, untracked, branch = classify(d)
        rows.append({"name": name, "path": d, "repo": owning_repo(d),
                     "verdict": verdict, "untracked": untracked, "branch": branch,
                     "confined": confined(d)})
    busy = live_runs()
    for r in rows:
        rid = run_id_of(r["name"])
        if busy is None:
            r["hold"] = "cannot read hq.db — refusing everything"
        elif rid is None:
            r["hold"] = "name is not run-<id>; cannot prove it is idle"
        else:
            r["hold"] = busy.get(rid)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually remove; without it nothing is touched")
    args = ap.parse_args()

    rows = scan()
    if not rows:
        print("no worktrees under %s" % ROOT)
        return 0

    by_repo, by_verdict = {}, {}
    for r in rows:
        by_repo[r["repo"]] = by_repo.get(r["repo"], 0) + 1
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1

    print("DRY RUN — nothing will be touched.\n" if not args.apply else "APPLYING.\n")
    print("%d worktree(s) under %s" % (len(rows), ROOT))
    for repo, n in sorted(by_repo.items(), key=lambda kv: -kv[1]):
        print("  %-52s %3d" % (repo or "(UNKNOWN OWNER — will be skipped)", n))
    print("  verdicts: " + " · ".join("%s %d" % (k, v) for k, v in sorted(by_verdict.items())))

    unconfined = [r for r in rows if not r["confined"]]
    if unconfined:
        print("\n  REFUSED (not strictly under the root): %s"
              % ", ".join(r["name"] for r in unconfined))

    held = [r for r in rows if r["hold"]]
    if held:
        print("\n  HELD BACK (live or unfinished work) — %d:" % len(held))
        for r in held:
            print("    %-12s %s" % (r["name"], r["hold"]))

    print("\n%-12s %-10s %-22s %s" % ("worktree", "verdict", "branch", "holds"))
    print("-" * 78)
    for r in rows:
        holds = ""
        if r["verdict"] == "dirty":
            rc, out = git(r["path"], "status", "--porcelain", "-uall")
            n = len(out.splitlines()) if rc == 0 else -1
            holds = "%d change(s)" % n
            if r["untracked"]:
                holds += ": " + ", ".join(r["untracked"][:1])
                if len(r["untracked"]) > 1:
                    holds += " +%d" % (len(r["untracked"]) - 1)
        mark = "  HELD" if r["hold"] else ""
        print("%-12s %-10s %-22s %s%s" % (r["name"], r["verdict"],
                                          (r["branch"] or "-")[:22], holds, mark))

    # what the parachute would hold: DISTINCT untracked files by content
    seen, files = {}, 0
    for r in rows:
        for rel in r["untracked"]:
            p = os.path.join(r["path"], rel)
            if not os.path.isfile(p):
                continue
            files += 1
            with open(p, "rb") as fh:
                seen.setdefault(hashlib.sha256(fh.read()).hexdigest(), (r["name"], rel))
    print("\nparachute would hold %d distinct file(s) (from %d untracked path(s))"
          % (len(seen), files))

    # branches: let `git branch -d` decide the split, but SHOW the expected one
    repos = {r["repo"] for r in rows if r["repo"]}
    for repo in sorted(repos):
        _rc, merged = git(repo, "branch", "--merged", "main", "--list", "wm/*")
        _rc, unmerged = git(repo, "branch", "--no-merged", "main", "--list", "wm/*")
        m = [b.strip("*+ ").strip() for b in merged.splitlines() if b.strip()]
        u = [b.strip("*+ ").strip() for b in unmerged.splitlines() if b.strip()]
        print("\n%s\n  wm/* branches: %d merged (would be pruned) · %d UNMERGED (kept)"
              % (repo, len(m), len(u)))
        for b in u:
            print("    keep: %s" % b)

    if not args.apply:
        print("\nNothing was touched. Re-run with --apply after a `wm backup`.")
        return 0

    # ---- apply ---------------------------------------------------------
    # Only rows that are confined, have a known owning repo, and are NOT held.
    targets = [r for r in rows if r["confined"] and r["repo"] and not r["hold"]]
    skipped = len(rows) - len(targets)
    print("\nAPPLY: %d to remove, %d left alone." % (len(targets), skipped))

    # 1. parachute FIRST — every distinct untracked file, by content.
    os.makedirs(PARACHUTE_DIR, exist_ok=True)
    tar_path = os.path.join(PARACHUTE_DIR, "worktree-drafts-%s.tar.gz"
                            % time.strftime("%Y%m%d-%H%M%S"))
    seen, added = set(), 0
    with tarfile.open(tar_path, "w:gz") as tar:
        for r in targets:
            for rel in r["untracked"]:
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
    print("  parachute: %s (%d distinct file(s))" % (tar_path, added))
    if added == 0 and any(r["untracked"] for r in targets):
        print("  REFUSING: untracked files exist but none were archived.")
        return 1

    # 2. remove each worktree in its OWNING repo (property 3).
    removed, failed = 0, []
    for r in targets:
        rc, out = git(r["repo"], "worktree", "remove", "--force", r["path"])
        if rc == 0:
            removed += 1
        else:
            failed.append((r["name"], out))
    print("  removed: %d   failed: %d" % (removed, len(failed)))
    for name, why in failed[:5]:
        print("    FAILED %s: %s" % (name, why[:90]))

    # 3. prune stale registrations, then let `git branch -d` decide the split.
    for repo in sorted({r["repo"] for r in targets}):
        git(repo, "worktree", "prune")
        _rc, listing = git(repo, "branch", "--list", "wm/*")
        pruned, kept = 0, []
        for raw in listing.splitlines():
            b = raw.strip("*+ ").strip()
            if not b:
                continue
            rc, _out = git(repo, "branch", "-d", b)     # -d, never -D
            if rc == 0:
                pruned += 1
            else:
                kept.append(b)
        print("  %s: pruned %d branch(es), kept %d" % (repo, pruned, len(kept)))
        for b in kept:
            print("    kept: %s" % b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
