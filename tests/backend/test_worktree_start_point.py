"""Worktree start-point (intent/WorktreeStartPointPlan.md slice A): a REVIEW run
must be able to SEE the BUILD it is reviewing.

`git worktree add -b <branch> <path>` with no start-point branches from the
primary repo's HEAD, so a BUILD run and the REVIEW run meant to check it both
started at `main` and landed as SIBLINGS. Measured on project 7 (2026-09-11):
build `1d6642b` and review `dcd2d19` both parented on `d678fb7`, 9 files apart
with opposite renames — and the reviewer approved a tree the coder never
produced, which would have published content missing the owner's rework.

Each guard here carries a NEGATIVE CONTROL that is proven to fail when the
guard is removed, because the notification-dedupe work shipped three tests that
all passed against deliberately broken code.
"""
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)

import pytest

from core import wm_dispatch as disp
from core import wm_store as store


def _git(path, *args):
    return subprocess.run(["git", "-C", path] + list(args),
                          capture_output=True, text=True).stdout.strip()


def _repo(path):
    """A throwaway git repo on `main` with one commit."""
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "-C", path, "init", "-q", "-b", "main"], check=True)
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    open(os.path.join(path, "base.txt"), "w").write("base\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "base")
    return path


@pytest.fixture()
def wm(tmp_path, monkeypatch):
    """An isolated store + repo, with runs/worktrees under tmp_path.

    WM_RUNS_DIR is mandatory here, not belt-and-braces. `resolve_runs_dir()`
    falls back to the module-level RUNS_DIR, which is computed from
    HERMES_HQ_HOME at IMPORT time — so setting HERMES_HQ_HOME in a fixture
    moves the database and nothing else, and `git worktree add` lands in the
    OWNER'S LIVE /opt/data/hermes-hq/runs/worktrees. That is not theoretical:
    the first run of this file created run-1..run-4 there, pointing at pytest
    tmp repos. conftest.py documents the same hazard for WM_DB.
    """
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path))
    monkeypatch.setenv("WM_RUNS_DIR", str(tmp_path / "runs"))
    db = str(tmp_path / "hq.db")
    store.init_db(db_path=db)
    repo = _repo(str(tmp_path / "repo"))
    store.create_project("p", "p", primary_path=repo, db_path=db)
    return {"db": db, "repo": repo}


def _dispatch(wm, task_id, profile="coder"):
    """One isolated run for `task_id`, wired exactly as the dispatcher wires it."""
    run_id = store.start_run(task_id, profile, db_path=wm["db"])
    task = store.get_task(task_id, db_path=wm["db"])
    project = store.get_project(task["project_id"], db_path=wm["db"])
    wd, branch, base_b, base_r = disp.run_workdir(task, project, run_id,
                                                  db_path=wm["db"])
    store.set_run_workdir(run_id, wd, branch, db_path=wm["db"],
                          base_branch=base_b, base_reason=base_r)
    return {"run_id": run_id, "workdir": wd, "branch": branch,
            "base_branch": base_b, "base_reason": base_r}


def _commit(workdir, filename, text, message):
    open(os.path.join(workdir, filename), "w").write(text)
    _git(workdir, "add", "-A")
    _git(workdir, "commit", "-qm", message)
    return _git(workdir, "rev-parse", "HEAD")


def _is_ancestor(workdir, sha):
    return subprocess.run(["git", "-C", workdir, "merge-base",
                           "--is-ancestor", sha, "HEAD"]).returncode == 0


def _build_then_review(wm):
    """The shape that broke: BUILD commits, task goes done, REVIEW dispatches."""
    build = store.create_task("p", "BUILD", is_code=True, db_path=wm["db"])
    review = store.create_task("p", "REVIEW", is_code=True, db_path=wm["db"])
    store.add_task_dep(review, build, db_path=wm["db"])
    b = _dispatch(wm, build)
    sha = _commit(b["workdir"], "built.txt", "the build\n", "the build")
    store.complete_run(build, status="done", db_path=wm["db"], run_id=b["run_id"])
    return build, review, b, sha


# 1. THE FIX: a review's HEAD contains the build commit, not a sibling of it.
def test_review_worktree_contains_the_build_commit(wm):
    _, review, _, sha = _build_then_review(wm)
    r = _dispatch(wm, review, "reviewer")
    assert _is_ancestor(r["workdir"], sha), (
        "review branched from %s; build %s is not in its ancestry"
        % (r["base_branch"], sha[:7]))
    assert os.path.exists(os.path.join(r["workdir"], "built.txt"))


def test_negative_control_no_start_point_reproduces_the_sibling_bug(wm):
    """Remove the guard and the bug comes back — proving test 1 can detect it."""
    _, review, b, sha = _build_then_review(wm)
    # Exactly the pre-plan call: `worktree add -b <branch> <path>`, no start-point.
    wt = os.path.join(os.path.dirname(wm["repo"]), "sibling-wt")
    subprocess.run(["git", "-C", wm["repo"], "worktree", "add", "-b",
                    "wm/sibling", wt], capture_output=True, check=True)
    assert not _is_ancestor(wt, sha), (
        "without a start-point the review should NOT contain the build — if "
        "this passes, test 1 proves nothing")
    assert _git(wt, "rev-parse", "HEAD^{tree}") != _git(b["workdir"],
                                                        "rev-parse", "HEAD^{tree}")


# 2. THE TRAP: the parent's LATEST run is often a review run with branch=NULL.
def test_parent_whose_latest_run_has_no_branch_still_resolves_to_the_build(wm):
    build, review, b, sha = _build_then_review(wm)
    # A review run on the BUILD task itself: latest run, no branch — task #280's
    # run 567 on 2026-09-11, which is what made the naive lookup silently fail.
    later = store.start_run(build, "reviewer", db_path=wm["db"])
    store.set_run_workdir(later, wm["repo"], None, db_path=wm["db"])
    branched = store.get_task_last_branched_run(build, db_path=wm["db"])
    assert branched["id"] == b["run_id"], "must skip the branchless later run"
    r = _dispatch(wm, review, "reviewer")
    assert r["base_branch"] == b["branch"]
    assert _is_ancestor(r["workdir"], sha)


def test_negative_control_a_naive_latest_run_lookup_finds_no_branch(wm):
    """The naive lookup must actually be wrong here, or test 2 proves nothing.

    Written as the rejected implementation — "newest row for this task" — and
    asserted to come back branchless, which is what silently sends the run back
    to the primary HEAD.
    """
    build, _, b, _ = _build_then_review(wm)
    later = store.start_run(build, "reviewer", db_path=wm["db"])
    store.set_run_workdir(later, wm["repo"], None, db_path=wm["db"])
    assert later > b["run_id"], "the branchless run must be the newest"

    import sqlite3
    conn = sqlite3.connect(wm["db"])
    conn.row_factory = sqlite3.Row
    naive = conn.execute("SELECT * FROM runs WHERE task_id=? "
                         "ORDER BY id DESC LIMIT 1", (build,)).fetchone()
    conn.close()
    assert naive["id"] == later
    assert naive["branch"] is None, (
        "the naive lookup must find no branch — that is the trap")
    assert store.get_task_last_branched_run(
        build, db_path=wm["db"])["id"] == b["run_id"]


# 3. REWORK: a re-run resumes its own work instead of restarting from main.
def test_rework_rerun_resumes_its_own_previous_branch(wm):
    task = store.create_task("p", "BUILD", is_code=True, db_path=wm["db"])
    first = _dispatch(wm, task)
    sha = _commit(first["workdir"], "wip.txt", "half done\n", "wip")
    store.complete_run(task, status="done", db_path=wm["db"],
                       run_id=first["run_id"])
    second = _dispatch(wm, task)
    assert second["base_branch"] == first["branch"], second["base_reason"]
    assert _is_ancestor(second["workdir"], sha), (
        "a rework re-run must amend its own work, not start from main")
    assert os.path.exists(os.path.join(second["workdir"], "wip.txt"))


def test_parent_rebuild_wins_over_this_tasks_own_stale_branch(wm):
    """The #280/#281 shape: the BUILD rebuilds while the REVIEW is pending, so
    the review must re-base on the NEW build rather than resume its own."""
    build, review, b1, _ = _build_then_review(wm)
    r1 = _dispatch(wm, review, "reviewer")
    store.complete_run(review, status="done", db_path=wm["db"],
                       run_id=r1["run_id"])
    b2 = _dispatch(wm, build)
    sha2 = _commit(b2["workdir"], "built.txt", "REBUILT\n", "rebuild")
    store.complete_run(build, status="done", db_path=wm["db"],
                       run_id=b2["run_id"])
    r2 = _dispatch(wm, review, "reviewer")
    assert r2["base_branch"] == b2["branch"], r2["base_reason"]
    assert _is_ancestor(r2["workdir"], sha2)
    assert open(os.path.join(r2["workdir"], "built.txt")).read() == "REBUILT\n"


# 4. MULTIPLE PARENTS: refuse and say so, never guess.
def test_two_branched_parents_fall_back_and_the_brief_says_so(wm):
    p1 = store.create_task("p", "BUILD ONE", is_code=True, db_path=wm["db"])
    p2 = store.create_task("p", "BUILD TWO", is_code=True, db_path=wm["db"])
    child = store.create_task("p", "REVIEW", is_code=True, db_path=wm["db"])
    for parent in (p1, p2):
        store.add_task_dep(child, parent, db_path=wm["db"])
        run = _dispatch(wm, parent)
        _commit(run["workdir"], "x-%d.txt" % parent, "w\n", "work %d" % parent)
        store.complete_run(parent, status="done", db_path=wm["db"],
                           run_id=run["run_id"])
    r = _dispatch(wm, child, "reviewer")
    assert r["base_branch"] is None, "must not guess between two parents"
    assert "no defensible pick" in r["base_reason"]
    brief = store.render_brief(r["run_id"], db_path=wm["db"])
    assert "THIS TREE IS NOT A PARENT'S BUILD" in brief


def test_brief_names_the_build_and_demands_an_ancestry_check(wm):
    _, review, b, _ = _build_then_review(wm)
    r = _dispatch(wm, review, "reviewer")
    brief = store.render_brief(r["run_id"], db_path=wm["db"])
    assert "Branched FROM: %s" % b["branch"] in brief
    assert "merge-base --is-ancestor" in brief


# 9. UNCHANGED: non-code tasks and non-git projects keep today's behaviour.
def test_non_code_task_gets_no_worktree_and_no_start_point(wm):
    task = store.create_task("p", "think", is_code=False, db_path=wm["db"])
    r = _dispatch(wm, task, "analyst")
    assert r["branch"] is None
    assert r["base_branch"] is None and r["base_reason"] is None
    assert r["workdir"] == wm["repo"]


def test_first_run_of_a_parentless_task_branches_from_head(wm):
    task = store.create_task("p", "BUILD", is_code=True, db_path=wm["db"])
    r = _dispatch(wm, task)
    assert r["base_branch"] is None
    assert "no completed parent" in r["base_reason"]
    assert r["branch"] == "wm/run-%d" % r["run_id"]
