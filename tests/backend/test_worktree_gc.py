"""Worktree GC — the standing policy (intent/WorktreeGCPlan.md, slice B).

Every safety property here has a negative control in the same file: break the
guard, watch the test fail, restore. Three times on 2026-09-10 a control passed
against deliberately broken code because the test called a helper directly
instead of the entry point, so these drive `sweep()` and `wm gc`.

The fixture builds a REAL git repo with REAL worktrees in tmp_path — a mocked
`git status` would prove nothing about the one check that stands between the
engine and someone's uncommitted work.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


def sh(*args, cwd=None):
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    (runs / "worktrees").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    monkeypatch.setenv("WM_RUNS_DIR", str(runs))
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store, worktree_gc as gc
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    db = store.DEFAULT_DB_PATH

    repo = tmp_path / "repo"
    repo.mkdir()
    sh("git", "init", "-q", "-b", "main", str(repo))
    sh("git", "config", "user.email", "t@t", cwd=repo)
    sh("git", "config", "user.name", "t", cwd=repo)
    (repo / "kept.md").write_text("committed content\n")
    sh("git", "add", "-A", cwd=repo)
    sh("git", "commit", "-qm", "base", cwd=repo)
    os.makedirs(tmp_path / "proj", exist_ok=True)
    store.create_project("alpha", "Alpha", "", str(repo), db_path=db)
    yield store, gc, db, str(runs / "worktrees"), repo, tmp_path


def make_worktree(repo, wt_root, run_id):
    path = os.path.join(wt_root, "run-%d" % run_id)
    sh("git", "worktree", "add", "-q", "-b", "wm/run-%d" % run_id, path, cwd=repo)
    return path


def finished_task(store, db, run_id, status="done", ago=48 * 3600):
    """A task+run in a terminal state, its transition `ago` seconds in the past."""
    tid = store.create_task("alpha", "task for run %d" % run_id, db_path=db)
    import sqlite3
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
        conn.execute("INSERT INTO runs(id, task_id, status, started_at, agent_profile) "
                     "VALUES(?,?,?,?,?)", (run_id, tid, status, time.time() - ago, "coder"))
        conn.execute("INSERT INTO state_transitions(task_id, from_status, to_status, ts) "
                     "VALUES(?,?,?,?)", (tid, "running", status, time.time() - ago))
    conn.close()
    return tid


# ---- the happy path ----------------------------------------------------

def test_a_spent_clean_worktree_is_reaped(env):
    store, gc, db, wt_root, repo, tmp = env
    p = make_worktree(repo, wt_root, 101)
    finished_task(store, db, 101)
    res = gc.sweep(store, db_path=db, dry_run=False)
    assert res["removed"] == ["run-101"]
    assert not os.path.exists(p)


def test_a_dry_run_removes_nothing(env, monkeypatch):
    """Proven by spying on the remove call — not by counting directories after,
    which would also pass if the removal simply failed."""
    store, gc, db, wt_root, repo, tmp = env
    make_worktree(repo, wt_root, 102)
    finished_task(store, db, 102)
    calls = []
    real = gc.git
    monkeypatch.setattr(gc, "git", lambda repo_, *a, **kw: (calls.append(a), real(repo_, *a, **kw))[1])
    res = gc.sweep(store, db_path=db, dry_run=True)
    assert [r["name"] for r in res["reap"]] == ["run-102"]
    assert res["removed"] == []
    assert not any(a and a[0] == "worktree" and a[1] == "remove" for a in calls)


# ---- property 3: a live run is untouchable -----------------------------

def test_the_grace_window_holds_a_just_finished_worktree(env):
    store, gc, db, wt_root, repo, tmp = env
    make_worktree(repo, wt_root, 103)
    finished_task(store, db, 103, ago=60)           # one minute ago
    res = gc.sweep(store, db_path=db, dry_run=False)
    assert res["removed"] == []
    assert "grace" in [r["why"] for r in res["keep"] if r["name"] == "run-103"][0]


def test_an_unfinished_task_is_never_reaped(env):
    store, gc, db, wt_root, repo, tmp = env
    make_worktree(repo, wt_root, 104)
    tid = finished_task(store, db, 104)
    import sqlite3
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("UPDATE tasks SET status='needs_review' WHERE id=?", (tid,))
    conn.close()
    res = gc.sweep(store, db_path=db, dry_run=False)
    assert res["removed"] == []


def test_a_live_pid_is_never_reaped(env):
    """A task can read terminal while its process is still alive. This uses THIS
    interpreter's pid, which is definitionally running."""
    store, gc, db, wt_root, repo, tmp = env
    make_worktree(repo, wt_root, 105)
    finished_task(store, db, 105)
    import sqlite3
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("UPDATE runs SET pid=? WHERE id=?", (os.getpid(), 105))
    conn.close()
    res = gc.sweep(store, db_path=db, dry_run=False)
    assert res["removed"] == []
    assert "alive" in [r["why"] for r in res["keep"] if r["name"] == "run-105"][0]


# ---- property 2: the clean check fails closed --------------------------

def test_a_dirty_worktree_is_never_reaped_automatically(env):
    store, gc, db, wt_root, repo, tmp = env
    p = make_worktree(repo, wt_root, 106)
    finished_task(store, db, 106)
    open(os.path.join(p, "agent-notes.md"), "w").write("the only copy\n")
    res = gc.sweep(store, db_path=db, dry_run=False)
    assert res["removed"] == []
    assert os.path.exists(os.path.join(p, "agent-notes.md"))


def test_an_untracked_DIRECTORY_makes_it_dirty_and_is_fully_parachuted(env):
    """`--porcelain` collapses an untracked directory to ONE line. A worktree
    once reported 7 untracked paths while holding 63 files; a parachute built
    from the collapsed output would have dropped 60 of them."""
    store, gc, db, wt_root, repo, tmp = env
    p = make_worktree(repo, wt_root, 107)
    finished_task(store, db, 107)
    os.makedirs(os.path.join(p, "drafts", "deep"))
    for i in range(5):
        open(os.path.join(p, "drafts", "deep", "d%d.md" % i), "w").write("draft %d\n" % i)
    verdict, untracked = gc.classify(p)
    assert verdict == "dirty"
    assert len(untracked) == 5, "an untracked directory must expand to its files"
    res = gc.sweep(store, db_path=db, dry_run=False, include_dirty=True,
                   parachute_dir=str(tmp / "chutes"))
    assert res["parachute"]["files"] == 5
    import tarfile
    with tarfile.open(res["parachute"]["path"]) as t:
        assert len(t.getnames()) == 5


def test_an_unreadable_git_file_counts_as_dirty(env):
    store, gc, db, wt_root, repo, tmp = env
    p = make_worktree(repo, wt_root, 108)
    finished_task(store, db, 108)
    open(os.path.join(p, ".git"), "w").write("not a gitdir pointer")
    verdict, _ = gc.classify(p)
    assert verdict in ("unreadable", "dirty")
    res = gc.sweep(store, db_path=db, dry_run=False)
    assert res["removed"] == []


# ---- property 1: path confinement --------------------------------------

def test_a_path_outside_the_root_is_refused(env):
    store, gc, db, wt_root, repo, tmp = env
    assert gc.confined(wt_root, os.path.join(wt_root, "run-1")) is True
    assert gc.confined(wt_root, "/etc") is False
    assert gc.confined(wt_root, wt_root) is False          # the root itself
    assert gc.confined(wt_root, os.path.join(wt_root, "..", "..", "etc")) is False


# ---- decision 2: the link rescue, carried INTO the reap ----------------

def test_reaping_repoints_links_and_leaves_the_uncommittable_broken(env):
    store, gc, db, wt_root, repo, tmp = env
    p = make_worktree(repo, wt_root, 109)
    tid = finished_task(store, db, 109)
    import sqlite3
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("UPDATE tasks SET result_paths=? WHERE id=?",
                     (json.dumps([os.path.join(p, "kept.md"),
                                  os.path.join(p, "never-committed.md")]), tid))
    conn.close()
    res = gc.sweep(store, db_path=db, dry_run=False)
    assert res["removed"] == ["run-109"] and res["links"] == 1
    conn = sqlite3.connect(db)
    paths = json.loads(conn.execute("SELECT result_paths FROM tasks WHERE id=?",
                                    (tid,)).fetchone()[0])
    conn.close()
    assert paths[0] == os.path.join(str(repo), "kept.md")   # repointed, and it exists
    assert os.path.exists(paths[0])
    assert "run-109" in paths[1]                            # no counterpart: left honest


# ---- the tick only sweeps hourly ---------------------------------------

def test_the_tick_sweeps_hourly_not_every_twenty_seconds(env):
    store, gc, db, wt_root, repo, tmp = env
    assert gc.sweep_due(store, db_path=db) is True          # never swept
    gc.mark_swept(store, db_path=db)
    assert gc.sweep_due(store, db_path=db) is False
    assert gc.sweep_due(store, db_path=db, now=time.time() + 3601) is True
