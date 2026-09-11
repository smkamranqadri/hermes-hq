"""Land on approval (intent/WorktreeStartPointPlan.md slices B+C): approved work
reaches `main` without a human moving git.

Nothing in this engine ever merged a `wm/*` branch back and the brief forbids
agents from doing it, so approved work sat on an orphan branch forever. On
2026-09-11 review 179 approved task #280 at 11:37 and a person fast-forwarded
`main` by hand at 13:17 — in between, an INTEGRATE task had nothing correct to
push and blocked.

ff-only always. The refusal paths matter more than the happy path: an automatic
merge or rebase would produce a tree nobody reviewed, which is the failure class
this plan exists to remove.
"""
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)

import pytest

from core import wm_store as store
from tests.backend.test_worktree_start_point import (  # noqa: F401
    _commit, _dispatch, _git, _repo, wm,
)


def _approved_build(wm, owner_approval=False, review_policy="required"):
    """A code task with a committed branch and an open review, ready to approve."""
    task = store.create_task("p", "BUILD", is_code=True,
                             review_policy=review_policy,
                             owner_approval=owner_approval, db_path=wm["db"])
    run = _dispatch(wm, task)
    sha = _commit(run["workdir"], "built.txt", "the build\n", "the build")
    store.complete_run(task, status="done", db_path=wm["db"],
                       run_id=run["run_id"])
    if store.get_open_review(task, db_path=wm["db"]) is None:
        store.create_review(task, review_policy=review_policy, db_path=wm["db"])
    return task, run, sha


def _head(repo):
    return _git(repo, "rev-parse", "HEAD")


def _dependent(wm, parent, title="INTEGRATE"):
    """A dependent that is actually eligible to promote.

    `_mark_ready_if_deps_done` promotes ONLY a `waiting_approval` task under a
    RELEASED goal. A freshly created task is `planned` with no goal, which the
    release gate never promotes by design — so a dependent left as created
    would make a promotion test pass for the wrong reason.
    """
    task = store.create_task("p", title, db_path=wm["db"])
    store.add_task_dep(task, parent, db_path=wm["db"])
    import sqlite3
    conn = sqlite3.connect(wm["db"])
    with conn:
        conn.execute("INSERT INTO goals(id, project_id, title, status) "
                     "VALUES(1, 1, 'g', 'released')")
        conn.execute("UPDATE tasks SET status='waiting_approval', goal_id=1 "
                     "WHERE id=?", (task,))
    conn.close()
    assert store.get_task(task, db_path=wm["db"])["status"] == "waiting_approval"
    return task


# 5. THE HAPPY PATH: approval moves main to the reviewed commit.
def test_approval_fast_forwards_main_to_the_reviewed_commit(wm):
    task, run, sha = _approved_build(wm)
    before = _head(wm["repo"])
    assert before != sha
    status, _, _ = store.review_verdict(task, "approved", db_path=wm["db"])
    assert status == "done"
    assert _head(wm["repo"]) == sha, "main did not reach the approved commit"
    assert os.path.exists(os.path.join(wm["repo"], "built.txt"))


def test_negative_control_without_the_land_main_never_moves(wm):
    """code_land off reproduces the pre-plan world — proving test 5 detects it."""
    store.append_meta("code_land", "0", db_path=wm["db"])
    task, run, sha = _approved_build(wm)
    before = _head(wm["repo"])
    store.review_verdict(task, "approved", db_path=wm["db"])
    assert _head(wm["repo"]) == before, (
        "with the land disabled main must NOT move — if it does, test 5 is "
        "not testing the land")
    assert _head(wm["repo"]) != sha


# 6. THE OWNER GATE: an owner_approval task lands on close, not on approval.
def test_owner_approval_task_does_not_land_until_the_owner_closes(wm):
    task, run, sha = _approved_build(wm, owner_approval=True)
    before = _head(wm["repo"])
    status, _, _ = store.review_verdict(task, "approved", db_path=wm["db"])
    assert status == "manual"
    assert _head(wm["repo"]) == before, "the owner gate was bypassed"
    store.close_by_owner(task, note="looks right", db_path=wm["db"])
    assert _head(wm["repo"]) == sha, "owner close did not land the work"


# 7. REFUSAL: never force, never merge, never rebase.
def test_diverged_main_refuses_and_blocks_instead_of_merging(wm):
    task, run, sha = _approved_build(wm)
    # main moves on its own, so the branch is no longer a fast-forward.
    _commit(wm["repo"], "elsewhere.txt", "divergent\n", "on main")
    before = _head(wm["repo"])
    status, _, promoted = store.review_verdict(task, "approved", db_path=wm["db"])
    assert _head(wm["repo"]) == before, "main moved despite not being a FF"
    assert status == "blocked"
    assert store.get_task(task, db_path=wm["db"])["status"] == "blocked"
    # no merge commit was created
    assert len(_git(wm["repo"], "rev-list", "--merges", "HEAD").split()) == 0
    # the work is not lost — it is still on its branch
    assert _git(wm["repo"], "rev-parse", run["branch"]) == sha


def test_dirty_primary_tree_refuses_rather_than_moving_files_under_the_owner(wm):
    task, run, sha = _approved_build(wm)
    open(os.path.join(wm["repo"], "base.txt"), "w").write("owner is editing\n")
    before = _head(wm["repo"])
    status, _, _ = store.review_verdict(task, "approved", db_path=wm["db"])
    assert _head(wm["repo"]) == before
    assert status == "blocked"
    assert open(os.path.join(wm["repo"], "base.txt")).read() == "owner is editing\n"


def test_refused_land_raises_a_notification_naming_the_branch(wm):
    task, run, _ = _approved_build(wm)
    _commit(wm["repo"], "elsewhere.txt", "divergent\n", "on main")
    store.review_verdict(task, "approved", db_path=wm["db"])
    rows, _unread = store.list_notifications(db_path=wm["db"])
    hit = [n for n in rows if n["task_id"] == task and "NOT landed" in n["title"]]
    assert hit, "a refused land must be visible, not silent"
    assert run["branch"] in (hit[0]["body"] or "")


def test_refused_land_does_not_promote_dependents(wm):
    """An INTEGRATE task must not be released against a main lacking the build."""
    task, run, _ = _approved_build(wm)
    integrate = _dependent(wm, task)
    _commit(wm["repo"], "elsewhere.txt", "divergent\n", "on main")
    _, _, promoted = store.review_verdict(task, "approved", db_path=wm["db"])
    assert integrate not in (promoted or [])
    assert store.get_task(integrate, db_path=wm["db"])["status"] != "ready"


def test_successful_land_does_promote_dependents(wm):
    task, run, sha = _approved_build(wm)
    integrate = _dependent(wm, task)
    _, _, promoted = store.review_verdict(task, "approved", db_path=wm["db"])
    assert _head(wm["repo"]) == sha
    assert integrate in (promoted or []), promoted


# 8 + 9. The gate is real, and nothing else changed.
def test_code_land_off_leaves_the_task_done_and_unblocked(wm):
    store.append_meta("code_land", "0", db_path=wm["db"])
    task, _, _ = _approved_build(wm)
    status, _, _ = store.review_verdict(task, "approved", db_path=wm["db"])
    assert status == "done"
    assert store.get_task(task, db_path=wm["db"])["status"] == "done"


def test_non_code_task_approval_is_untouched_by_the_land(wm):
    task = store.create_task("p", "think", is_code=False,
                             review_policy="required", db_path=wm["db"])
    run_id = store.start_run(task, "analyst", db_path=wm["db"])
    store.complete_run(task, status="done", db_path=wm["db"], run_id=run_id)
    if store.get_open_review(task, db_path=wm["db"]) is None:
        store.create_review(task, review_policy="required", db_path=wm["db"])
    before = _head(wm["repo"])
    status, _, _ = store.review_verdict(task, "approved", db_path=wm["db"])
    assert status == "done"
    assert _head(wm["repo"]) == before


def test_changes_requested_never_lands(wm):
    task, _, sha = _approved_build(wm)
    before = _head(wm["repo"])
    status, _, _ = store.review_verdict(task, "changes_requested",
                                        comment="no", db_path=wm["db"])
    assert status == "rework"
    assert _head(wm["repo"]) == before, "a rejected review must never land"
