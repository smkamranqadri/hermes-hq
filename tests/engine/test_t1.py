#!/usr/bin/env python3
"""test_t1.py — automated verification of the T1 Definition of Done.

Runs against a throwaway database (WORK_MANAGER_TEST_DB or a temp file) so
it never touches the real /opt/data/work-manager/wm.db. Re-runnable:
    python3 test_t1.py

Covers the 6 Definition-of-Done items from the T1 brief.
"""

import os
import io
import argparse
import contextlib
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hq", "engine"))
import wm_store as store  # noqa: E402

DB = os.environ.get("WORK_MANAGER_TEST_DB") or os.path.join(
    tempfile.gettempdir(), "wm_test_t1.db")


def _wipe():
    if os.path.exists(DB):
        os.remove(DB)
    for suffix in ("-wal", "-shm"):
        p = DB + suffix
        if os.path.exists(p):
            os.remove(p)


PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append((name, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" — " + detail) if detail else ""))


def _conn():
    return store._connect(DB)


# ---------------------------------------------------------------------------
print("== T1 DoD 1: init creates full schema (8 tables + meta) ==")
_wipe()
store.init_db(db_path=DB)
conn = _conn()
try:
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"projects", "goals", "tasks", "task_deps", "runs", "reviews",
                "activity", "wm_meta"}
    check("8 tables present", expected.issubset(tables),
          "missing=" + str(sorted(expected - tables)))
    meta = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key,value FROM wm_meta")}
    check("wm_meta seeded schema_version=2",
          meta.get("schema_version") == "2", str(meta))
    check("wm_meta seeded concurrency_cap=3, stall_seconds=300, paused=0",
          meta.get("concurrency_cap") == "3"
          and meta.get("stall_seconds") == "300"
          and meta.get("paused") == "0", str(meta))
    # idempotent
    store.init_db(db_path=DB)
    check("init_db idempotent (no dup meta keys / no error)",
          conn.execute("SELECT COUNT(*) FROM wm_meta").fetchone()[0] == 8)
finally:
    conn.close()

# ---------------------------------------------------------------------------
print("\n== T1 DoD 2: create/persist/read-back project+goal+task+assign ==")
# fresh DB to isolate meta counters
_wipe()
store.init_db(db_path=DB)
proj = store.create_project("demo", "Demo Project", "a test", "/tmp/demo",
                            db_path=DB)
check("project create returns id", isinstance(proj, int) and proj > 0)
g = store.create_goal("demo", "Ship the thing", "desc", "criteria", db_path=DB)
check("goal create in project", g > 0)
t = store.create_task("demo", "Build widget", "do it", "works when qa passes",
                      assignee_profile="coder", goal_id=g, db_path=DB)
check("task create with assignee+goal", t > 0)
store.assign_task(t, "reviewer", db_path=DB)
row = store.get_task(t, db_path=DB)
check("task show reads back fields",
      row["title"] == "Build widget"
      and row["project_slug"] == "demo"
      and row["goal_id"] == g
      and row["assignee_profile"] == "reviewer"
      and row["review_policy"] == "none"
      and row["status"] == "planned",
      str(dict(row)))
check("project show reads back", store.get_project(slug="demo", db_path=DB)["name"] == "Demo Project")
check("project list returns 1", len(store.list_projects(db_path=DB)) == 1)
check("task list filters by project/status",
      len(store.list_tasks(project_slug="demo", status="planned", db_path=DB)) == 1)

# ---------------------------------------------------------------------------
print("\n== T1 DoD 3: dependency rule (ready-capable only when deps done) ==")
tA = store.create_task("demo", "A", db_path=DB)          # no deps
tB = store.create_task("demo", "B", db_path=DB)
store.add_task_dep(tB, tA, db_path=DB)                    # B depends on A
store.mark_ready(tA, db_path=DB)                          # A has no deps -> ready
a_ok = store.get_task(tA, db_path=DB)["status"] == "ready"
check("no-dep task A mark-ready -> ready", a_ok)
# B depends on A; A is ready (not done) -> B must be refused
before = store.get_task(tB, db_path=DB)["status"]
try:
    store.mark_ready(tB, db_path=DB)
    b_refused = False
    b_refuse_msg = ""
except ValueError as e:
    b_refused = True
    b_refuse_msg = str(e)
check("task B refused while dep A not done", b_refused and before == "planned",
      b_refuse_msg)
check("mark-ready refusal message names pending deps",
      "dependencies are done" in b_refuse_msg or "dep" in b_refuse_msg.lower(),
      b_refuse_msg)
# complete A -> B becomes ready-capable
store.complete_run(tA, status="done", db_path=DB)
check("deps_done(B)=True after A done", store.deps_done(tB, db_path=DB) is True)
store.mark_ready(tB, db_path=DB)
check("mark-ready B works once A done",
      store.get_task(tB, db_path=DB)["status"] == "ready")

# ---------------------------------------------------------------------------
print("\n== T1 DoD 4: atomic claim — two separate connections ==")
_wipe(); store.init_db(db_path=DB)
store.create_project("demo", "Demo", primary_path="/tmp/demo", db_path=DB)
t1 = store.create_task("demo", "claim me", db_path=DB)
# no deps -> ready via direct status set through a helper (mark_ready requires planned set already; it's planned with no deps)
store.mark_ready(t1, db_path=DB)
# claim from connection 1
first = store.claim_task(t1, db_path=DB)
# claim from connection 2 (fresh transaction/connection)
second = store.claim_task(t1, db_path=DB)
check("exactly one of two concurrent claims succeeds",
      first is True and second is False,
      "first=%s second=%s" % (first, second))
check("claimed task is now running with claimed_at set",
      store.get_task(t1, db_path=DB)["status"] == "running"
      and store.get_task(t1, db_path=DB)["claimed_at"] is not None)
# second claim must not flip it again
check("rowcount guard: third claim also False",
      store.claim_task(t1, db_path=DB) is False)

# ---------------------------------------------------------------------------
print("\n== T1 DoD 5: next_ready_tasks cap + ordering ==")
_wipe(); store.init_db(db_path=DB)
store.create_project("demo", "Demo", primary_path="/tmp/demo", db_path=DB)
ids = []
for i in range(4):
    tid = store.create_task("demo", "r%d" % i, db_path=DB)
    store.mark_ready(tid, db_path=DB)
    ids.append(tid)
nxt2 = [r["id"] for r in store.next_ready_tasks(2, db_path=DB)]
nxt9 = store.next_ready_tasks(9, db_path=DB)
check("next_ready_tasks returns at most cap, oldest first",
      len(nxt2) == 2 and nxt2[:2] == [ids[0], ids[1]], "nxt2=%s" % nxt2)
check("next_ready_tasks returns all ready up to cap", len(nxt9) == 4)

# ---------------------------------------------------------------------------
print("\n== T1 DoD 6: CLI surface (init already run; exercise the rest) ==")
import wm_cli

# pause/resume via store
store.set_paused(True, db_path=DB)
check("pause sets paused=1 meta", store.get_meta("paused", db_path=DB) == "1")
store.set_paused(False, db_path=DB)
check("resume sets paused=0 meta", store.get_meta("paused", db_path=DB) == "0")

# Exercise the argparse CLI end-to-end via main() against a scratch DB.
before_default = store.DEFAULT_DB_PATH
if os.path.exists(DB):
    os.remove(DB)
for s in ("-wal", "-shm"):
    if os.path.exists(DB + s):
        os.remove(DB + s)
store.DEFAULT_DB_PATH = DB
rc = wm_cli.main(["init"]); check("CLI init rc=0", rc == 0)
rc = wm_cli.main(["project", "create", "cli-proj", "--name", "CLI Proj",
                  "--description", "d", "--path", "/tmp/cli"])
check("CLI project create rc=0", rc == 0)
rc = wm_cli.main(["goal", "create", "cli-proj", "CLI Goal", "gd"])
check("CLI goal create rc=0", rc == 0)
rc = wm_cli.main(["task", "create", "cli-proj", "CLI Task", "td", "dod",
                  "--assignee", "writer", "--review-policy", "required"])
check("CLI task create rc=0", rc == 0)
task_id = store.list_tasks(project_slug="cli-proj", db_path=DB)[0]["id"]
rc = wm_cli.main(["task", "assign", str(task_id), "coder"])
check("CLI task assign rc=0", rc == 0)
rc = wm_cli.main(["task", "show", str(task_id)])
check("CLI task show rc=0", rc == 0)
rc = wm_cli.main(["task", "mark-ready", str(task_id)])
check("CLI mark-ready rc=0 (no deps)", rc == 0)
rc = wm_cli.main(["status"])
check("CLI status rc=0", rc == 0)
rc = wm_cli.main(["pause"]); check("CLI pause rc=0", rc == 0)
rc = wm_cli.main(["resume"]); check("CLI resume rc=0", rc == 0)
store.DEFAULT_DB_PATH = before_default

# ---------------------------------------------------------------------------
print("\n== T1-REV A: project rule explicit + enforced ==")
_wipe(); store.init_db(db_path=DB)
# A.1 project create refuses a missing/empty --path / primary_path (store)
proj_refused = False
try:
    store.create_project("nopath", "No Path", db_path=DB)
except ValueError as e:
    proj_refused = "primary_path" in str(e) or "path" in str(e).lower()
check("project create refuses missing/empty primary_path", proj_refused)
# A.2 a task cannot be created without a project (goal stays optional)
orphan_refused = [False, False]
for i, slug in enumerate(("no-such-project", "")):
    try:
        store.create_task(slug, "orphan task %d" % i, db_path=DB)
    except ValueError:
        orphan_refused[i] = True
check("task create refused without a valid project (unknown slug / empty)",
      all(orphan_refused), str(orphan_refused))
# A.3 tasks.project_id is NOT NULL in the schema
t_cols = {r["name"]: r["notnull"]
          for r in _conn().execute("PRAGMA table_info(tasks)")}
check("tasks.project_id NOT NULL", t_cols.get("project_id") == 1, str(t_cols))
# A.4 CLI surface: wm project create without --path -> rc 1; task w/o project -> rc 1
store.DEFAULT_DB_PATH = DB
rc_np = wm_cli.main(["project", "create", "cli-nopath", "--name", "NP"])
check("CLI project create without --path rc=1", rc_np == 1)
rc_orphan = wm_cli.main(["task", "create", "no-such-project", "orphan"])
check("CLI task create with invalid project rc=1", rc_orphan == 1)

# ---------------------------------------------------------------------------
print("\n== T1-REV B: runs.completion column + idempotent migration ==")
r_cols = {r["name"] for r in _conn().execute("PRAGMA table_info(runs)")}
check("runs.completion column exists", "completion" in r_cols, str(r_cols))
# idempotent re-run of init_db (fresh schema already has the column) must not error
store.init_db(db_path=DB)
check("init_db idempotent with completion column present", True)
# migration path: build a legacy runs table WITHOUT completion, then init_db
mig_db = os.path.join(tempfile.gettempdir(), "wm_test_mig.db")
for p in [mig_db, mig_db + "-wal", mig_db + "-shm"]:
    if os.path.exists(p):
        os.remove(p)
mc = sqlite3.connect(mig_db)
mc.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, task_id INTEGER)")
mc.commit(); mc.close()
store.init_db(db_path=mig_db)  # should ALTER ADD COLUMN completion
mig_cols = {r["name"] for r
            in store._connect(mig_db).execute("PRAGMA table_info(runs)")}
check("migration adds runs.completion to legacy table", "completion" in mig_cols,
      str(mig_cols))
store.init_db(db_path=mig_db)  # re-run must NOT error (no duplicate column)
check("migration idempotent (second init_db no error)", True)
for p in [mig_db, mig_db + "-wal", mig_db + "-shm"]:
    if os.path.exists(p):
        os.remove(p)

# ---------------------------------------------------------------------------
print("\n== T1-REV C: wm dispatch subcommand ==")
dispatch_choices = [
    c for a in wm_cli.build_parser()._actions
    if isinstance(a, argparse._SubParsersAction) for c in a.choices]
check("'dispatch' is a recognized subcommand", "dispatch" in dispatch_choices,
      str(dispatch_choices))
ph = io.StringIO()
with contextlib.redirect_stdout(ph):
    wm_cli.build_parser().print_help()
check("wm --help lists dispatch", "dispatch" in ph.getvalue())
po = io.StringIO()
with contextlib.redirect_stdout(po):
    rc = wm_cli.main(["dispatch"])
check("wm dispatch rc=0", rc == 0)
msg = po.getvalue()
# T2 wired cmd_dispatch to the real dispatcher; on an empty DB it must
# report no ready tasks rather than the old "not implemented" stub.
check("wm dispatch runs the real dispatcher (empty DB, no ready tasks)",
      "not yet implemented" not in msg
      and ("No ready tasks" in msg or "no ready" in msg.lower()),
      repr(msg))
store.DEFAULT_DB_PATH = before_default

# ---------------------------------------------------------------------------
print("\n===============================")
print("PASS: %d   FAIL: %d" % (len(PASS), len(FAIL)))
for name, detail in FAIL:
    print("  FAILED: %s  %s" % (name, detail))
sys.exit(1 if FAIL else 0)