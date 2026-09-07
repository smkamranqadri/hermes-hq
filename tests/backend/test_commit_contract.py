"""Commit contract (intent/CommitContractPlan.md): a code run that leaves work
uncommitted has not finished.

Agents get an isolated branch per code run and were never told to commit — 13
of 31 worktrees held work from runs marked `done`, and #191's whole migration
was approved off an uncommitted tree and never reached main. The engine now
checks, because a rule that lives only in a prompt gets skipped.
"""
import os, subprocess, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest

from tests.backend.test_chat import env, login  # noqa: F401
from tests.backend.test_questions import _mk_run


def _repo(path, dirty=False, untracked=False):
    """A throwaway git repo with one commit, optionally left dirty."""
    os.makedirs(path, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=path, capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    open(os.path.join(path, "a.txt"), "w").write("one\n")
    run("git", "add", "a.txt")
    run("git", "commit", "-qm", "init")
    if dirty:
        open(os.path.join(path, "a.txt"), "w").write("changed\n")
    if untracked:
        open(os.path.join(path, "new_source.py"), "w").write("# real work\n")
    return path


def _finish(store, tid, rid, workdir, db):
    con = store._connect(db)
    with con:
        con.execute("UPDATE runs SET workdir=?, branch='wm/run-x' WHERE id=?", (workdir, rid))
    con.close()
    return store.record_completion(rid, tid, "done", summary="did the thing", db_path=db)


# ---- the gate -----------------------------------------------------------

def test_dirty_worktree_refuses_done_and_names_the_paths(env, tmp_path):
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    tid, rid = _mk_run(store, "coder", "cc-dirty")
    wd = _repo(str(tmp_path / "dirty"), dirty=True)
    task_status, run_status = _finish(store, tid, rid, wd, db)
    assert (task_status, run_status) == ("rework", "failed")
    err = store.get_run(rid, db_path=db)["error"]
    assert "uncommitted" in err and "a.txt" in err and "wm/run-x" in err
    assert "Do not merge to main" in err


def test_untracked_source_counts_as_uncommitted(env, tmp_path):
    """The new test file #191 wrote was untracked — that is real work too."""
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    tid, rid = _mk_run(store, "coder", "cc-untracked")
    wd = _repo(str(tmp_path / "untracked"), untracked=True)
    assert _finish(store, tid, rid, wd, db)[0] == "rework"
    assert "new_source.py" in store.get_run(rid, db_path=db)["error"]


def test_clean_worktree_with_no_commits_still_passes(env, tmp_path):
    """A clean tree that produced no commit is a legitimate investigate/verify
    run (#183, #192) — the gate is on DIRT, never on 'has commits'."""
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    tid, rid = _mk_run(store, "coder", "cc-clean")
    wd = _repo(str(tmp_path / "clean"))
    assert _finish(store, tid, rid, wd, db) == ("done", "done")


# ---- fail open: a broken check must never strand a finished task --------

@pytest.mark.parametrize("kind", ["missing_dir", "not_a_repo", "no_workdir"])
def test_gate_fails_open(env, tmp_path, kind):
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    tid, rid = _mk_run(store, "coder", "cc-open-" + kind.replace("_", "-"))
    if kind == "missing_dir":
        wd = str(tmp_path / "gone")
    elif kind == "not_a_repo":
        wd = str(tmp_path / "plain"); os.makedirs(wd, exist_ok=True)
    else:
        wd = None
    assert _finish(store, tid, rid, wd, db) == ("done", "done")


def test_uncommitted_paths_helper_is_total(tmp_path):
    from core import wm_store as store
    assert store.uncommitted_paths(None) == []
    assert store.uncommitted_paths("") == []
    assert store.uncommitted_paths(str(tmp_path / "nope")) == []
    assert store.uncommitted_paths(_repo(str(tmp_path / "ok"))) == []
    assert store.uncommitted_paths(_repo(str(tmp_path / "bad"), dirty=True)) == ["a.txt"]


# ---- the contract is in the briefs agents actually read -----------------

def test_briefs_carry_the_commit_contract(env):
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    tid, rid = _mk_run(store, "coder", "cc-brief")
    con = store._connect(db)
    with con:
        con.execute("UPDATE runs SET branch='wm/run-b', workdir='/tmp' WHERE id=?", (rid,))
    con.close()
    brief = store.render_brief(rid, db_path=db)
    assert "COMMIT CONTRACT" in brief
    assert "git status --porcelain" in brief


def test_repeated_refusals_stop_re_dispatching(env, tmp_path):
    """The refusal returns the task to `rework`, which the dispatcher re-claims —
    and the review cap never fires for a review_policy='none' task. Seen live on
    #202: three runs in six minutes. The gate bounds itself with the same cap."""
    c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    tid, rid = _mk_run(store, "coder", "cc-loop")
    wd = _repo(str(tmp_path / "loop"), dirty=True)
    seen = []
    for _ in range(store.MAX_REWORK_CYCLES):
        rid = store.start_run(tid, "coder", db_path=db)
        seen.append(_finish(store, tid, rid, wd, db)[0])
    assert seen[:-1] == ["rework"] * (store.MAX_REWORK_CYCLES - 1)
    assert seen[-1] == "manual"                      # owner's desk, not re-dispatched
    assert "Stopped re-dispatching" in store.get_run(rid, db_path=db)["error"]
    assert tid not in [t["id"] for t in store.next_ready_tasks(50, db_path=db)]
