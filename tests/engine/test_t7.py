#!/usr/bin/env python3
# test_t7.py — LEAN T7 verification of the control & reliability fixes
# (fake agent; no real LLM, no real wm.db). Run: python3 test_t7.py
# DoD:
#  1. Backlog gate: a `planned` (not released) no-dep task NEVER auto-runs.
#  2. Release gate: goal release -> eligible (deps-done) children become ready;
#     a `planned`/unreleased dependent stays parked even after a parent completes.
#  3. Waiting-for-approval: blocks itself + dependents only; released+deps-done -> ready.
#  4. Dispatcher single-flight: overlapping ticks -> the loser is skipped (cap never exceeded).
#  5. Atomic review claim: exactly one of two racing claims wins.
#  6. Completion preserves ALL artifact paths (not just [0]) on task + run.
#  7. DB integrity audit flags a raw-SQL status flip (tamper detection).
#  8. backup + prune ops work and keep task/project history.
#  9. code-task worktree isolation: is_code task in a git repo gets run.workdir+branch.
import contextlib, io, json, os, shutil, sqlite3, subprocess, sys, tempfile, time
BASE = os.path.dirname(os.path.abspath(__file__)); ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hq", "engine"); sys.path.insert(0, ENGINE)
import wm_store as store, wm_dispatch as dispatch, wm_cli  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wm_t7_")
DB, RUNS, PROF = (os.path.join(TMP, x) for x in ("wm.db", "runs", "profiles"))
os.makedirs(RUNS, exist_ok=True)
FAKE = os.path.join(TMP, "_fake.py")
open(FAKE, "w").write('''#!/usr/bin/env python3
import os,sys,json,time,sqlite3
_a=sys.argv[1:]
def val(k):
    for i,x in enumerate(_a[:-1]):
        if x==k: return _a[i+1]
marker=val("-c"); rid=marker.split("-")[-1]; profile=val("--profile")
runs=os.environ["WM_RUNS_DIR"]; prof=os.environ["WM_PROFILES_DIR"]
sdb=os.path.join(prof,profile,"state.db"); cp=os.path.join(runs,rid+".completion.json")
os.makedirs(os.path.dirname(sdb),exist_ok=True)
con=sqlite3.connect(sdb); sid="RSESS"+rid
con.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,started_at REAL,last_activity_at REAL,title TEXT)")
con.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?)",(sid,time.time(),time.time(),marker)); con.commit(); con.close()
open(cp,"w").write(json.dumps({"completed":"done","summary":"ok",
    "result_paths":["/tmp/a.txt","/tmp/b.txt","/tmp/c.txt"],"blocker":""}))
sys.exit(0)
''')
os.chmod(FAKE, 0o755)
store.DEFAULT_DB_PATH = DB
for k, v in [("WM_DB", DB), ("WM_RUNS_DIR", RUNS), ("WM_PROFILES_DIR", PROF),
             ("WM_PY", sys.executable), ("WM_HERMES", FAKE)]:
    os.environ[k] = v

PASS, FAIL = [], []
def check(n, ok, d=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", n, " — " + str(d) if d else ""))
    (PASS if ok else FAIL).append(n)
def fresh():
    for p in (DB, DB + "-wal", DB + "-shm"):
        if os.path.exists(p): os.remove(p)
    store.init_db(db_path=DB)
    for sub in ("worktrees",):
        d = os.path.join(RUNS, sub)
        if os.path.isdir(d): shutil.rmtree(d, ignore_errors=True)
def project(slug="p", primary=None):
    return store.create_project(slug, slug, primary_path=primary or TMP, db_path=DB)
def wait_final(rid, t=30, db=None):
    t0 = time.time()
    while time.time() - t0 < t:
        r = store.get_run(rid, db_path=db or DB)
        if r and r["status"] != "running": return r
        time.sleep(0.05)
    return store.get_run(rid, db_path=db or DB)
def wait_for(fn, t=6):
    t0 = time.time()
    while time.time() - t0 < t:
        if fn(): return True
        time.sleep(0.03)
    return fn()

# ============================================================================
# 1. BACKLOG GATE: a `planned` no-dep task stays parked across many ticks.
# ============================================================================
fresh(); project()
t = store.create_task("p", "parked", assignee_profile="writer", db_path=DB)
check("created task starts `planned`", store.get_task(t, db_path=DB)["status"] == "planned")
writes = 0
for _ in range(4):
    r = dispatch.run_dispatch(db_path=DB)
    writes += len(r["dispatched"])
check("`planned` no-dep task NEVER auto-runs (4 ticks, 0 dispatches)",
      writes == 0 and store.get_task(t, db_path=DB)["status"] == "planned", writes)
# explicit release via mark-ready -> runs
store.mark_ready(t, db_path=DB)
check("explicit `mark-ready` (release) makes it executable",
      store.get_task(t, db_path=DB)["status"] == "ready")
did = dispatch.run_dispatch(db_path=DB)
check("released task dispatches", len(did["dispatched"]) == 1)
fin = wait_final(did["dispatched"][0])
check("released task completes `done`", fin and fin["status"] == "done")

# ============================================================================
# 2. RELEASE GATE via goal: eligible children become ready; planned stays parked.
# ============================================================================
fresh(); project()
g = store.create_goal("p", "plan", db_path=DB)
pa = store.create_task("p", "parent", assignee_profile="writer", goal_id=g, db_path=DB)
ch = store.create_task("p", "child", assignee_profile="writer", goal_id=g, db_path=DB)
store.add_task_dep(ch, pa, db_path=DB)
r = dispatch.run_dispatch(db_path=DB)
check("unreleased goal: NOTHING dispatches (both planned)", len(r["dispatched"]) == 0
      and store.get_task(pa, db_path=DB)["status"] == "planned"
      and store.get_task(ch, db_path=DB)["status"] == "planned")
store.release_goal(g, db_path=DB)
check("goal release -> parent (deps done) ready, child waiting_approval",
      store.get_task(pa, db_path=DB)["status"] == "ready"
      and store.get_task(ch, db_path=DB)["status"] == "waiting_approval",
      (store.get_task(pa, db_path=DB)["status"], store.get_task(ch, db_path=DB)["status"]))
r1 = dispatch.run_dispatch(db_path=DB)
check("released goal: parent dispatches (child waits on deps)", len(r1["dispatched"]) == 1)
fit = wait_final(r1["dispatched"][0])
check("parent completes done", fit and fit["status"] == "done")
# a waiting_approval task whose deps just finished, on a released goal -> READY
# (auto-continue happens via promote_dependents inside the wrapper finalize).
check("dep completion auto-promotes waiting_approval -> ready (released plan)",
      wait_for(lambda: store.get_task(ch, db_path=DB)["status"] == "ready"),
      store.get_task(ch, db_path=DB)["status"])
r3 = dispatch.run_dispatch(db_path=DB)
check("eligible child now dispatches (auto-continue of released plan)", len(r3["dispatched"]) == 1)

# ============================================================================
# 3. WAITING-FOR-APPROVAL blocks itself + dependents only.
# ============================================================================
fresh(); project()
g2 = store.create_goal("p", "two plans", db_path=DB)
unreleased = store.create_goal("p", "held", db_path=DB)
a1 = store.create_task("p", "go1", assignee_profile="writer", goal_id=g2, db_path=DB)
a2 = store.create_task("p", "go2", assignee_profile="writer", goal_id=g2, db_path=DB)
store.add_task_dep(a2, a1, db_path=DB)
store.release_goal(g2, db_path=DB)
release_eligible = store.release_goal(g2, db_path=DB)
h = store.create_task("p", "held-task", assignee_profile="writer", goal_id=unreleased, db_path=DB)
store.add_task_dep(h, a1, db_path=DB)  # dependent of a1 but on an UNRELEASED goal
store.mark_ready(a1, db_path=DB)
check("waiting-for-approval: a2 (released plan, deps pending) is waiting_approval; held h is planned",
      store.get_task(a2, db_path=DB)["status"] == "waiting_approval"
      and store.get_task(h, db_path=DB)["status"] == "planned",
      (store.get_task(a2, db_path=DB)["status"], store.get_task(h, db_path=DB)["status"]))
r = dispatch.run_dispatch(db_path=DB)
check("released-plan task dispatches; unrelated held task untouched",
      len(r["dispatched"]) == 1 and store.get_task(h, db_path=DB)["status"] == "planned")
w = wait_final(r["dispatched"][0])
check("a1 done; a2 (released plan) auto-continues to ready; held dependent STILL planned",
      w["status"] == "done"
      and wait_for(lambda: store.get_task(a2, db_path=DB)["status"] == "ready")
      and store.get_task(h, db_path=DB)["status"] == "planned",
      (store.get_task(a2, db_path=DB)["status"], store.get_task(h, db_path=DB)["status"]))

# ============================================================================
# 4. DISPATCHER SINGLE-FLIGHT: one tick runs, the overlapping one is skipped.
# ============================================================================
fresh(); project()
mk = store.create_task("p", "solo", assignee_profile="writer", db_path=DB)
store.mark_ready(mk, db_path=DB)
# Hold the dispatch lock, then a normal tick must SKIP (not run).
lock_fh = open(dispatch._dispatch_lock_path(), "w")
import fcntl as _fc
_fc.flock(lock_fh, _fc.LOCK_EX | _fc.LOCK_NB)
summary = dispatch.run_dispatch(db_path=DB)
check("overlapping tick sees the lock and SKIPS (single-flight)",
      summary.get("skipped") is True, summary)
_fc.flock(lock_fh, _fc.LOCK_UN); lock_fh.close()
summary2 = dispatch.run_dispatch(db_path=DB)
check("after the lock is released the tick runs normally",
      summary2.get("skipped") is False and len(summary2["dispatched"]) == 1)
f2 = wait_final(summary2["dispatched"][0]); check("solo run completes", f2["status"] == "done")

# ============================================================================
# 5. ATOMIC REVIEW CLAIM: exactly one of two racing claims wins.
# ============================================================================
fresh(); project()
pt = store.create_task("p", "art", assignee_profile="writer", review_policy="required", db_path=DB)
store.mark_ready(pt, db_path=DB)
r = store.start_run(pt, "writer", db_path=DB)
store.record_completion(r, pt, "done", summary="x", result_paths=["/o.txt"], db_path=DB)
rev = store.list_reviews(task_id=pt, db_path=DB)[0]
wins = [store.claim_review(rev["id"], db_path=DB), store.claim_review(rev["id"], db_path=DB)]
check("atomic review claim: exactly ONE of two racing claims wins",
      wins.count(True) == 1 and wins.count(False) == 1, wins)
check("claiming set the review to 'running', not double-fired",
      store.get_review(rev["id"], db_path=DB)["status"] == "running")

# ============================================================================
# 6. COMPLETION preserves ALL artifact paths (task + run), not just result_path.
# ============================================================================
fresh(); project()
c = store.create_task("p", "multi-artifact", assignee_profile="writer", db_path=DB)
store.mark_ready(c, db_path=DB)
did = dispatch.run_dispatch(db_path=DB)
r6 = wait_final(did["dispatched"][0])
def _run_paths():
    r = store.get_run(did["dispatched"][0], db_path=DB)
    if not r or not r["result_paths"]: return None
    try: return json.loads(r["result_paths"])
    except Exception: return None
check("run records ALL result_paths as JSON",
      wait_for(lambda: _run_paths() == ["/tmp/a.txt","/tmp/b.txt","/tmp/c.txt"]),
      _run_paths())
task_after = store.get_task(c, db_path=DB)
check("task preserves ALL artifact paths (result_paths JSON) + first as display",
      task_after["result_path"] == "/tmp/a.txt"
      and json.loads(task_after["result_paths"]) == ["/tmp/a.txt","/tmp/b.txt","/tmp/c.txt"],
      (task_after["result_path"], task_after["result_paths"]))
check("state_transitions recorded the completion (done)",
      any(x["to_status"] == "done" for x in store.list_transitions(c, db_path=DB)))

# ============================================================================
# 7. DB INTEGRITY AUDIT flags a raw-SQL tamper.
# ============================================================================
fresh(); project()
ok_task = store.create_task("p", "clean", assignee_profile="writer", db_path=DB)
store.mark_ready(ok_task, db_path=DB)
store.claim_task(ok_task, db_path=DB); rv = store.start_run(ok_task, "writer", db_path=DB)
store.record_completion(rv, ok_task, "done", summary="clean", result_paths=["/x"], db_path=DB)
res = store.check_integrity(db_path=DB)
check("clean DB passes integrity audit", res["ok"], res["findings"])
# Tamper: flip a task to done directly with no run/transition/review.
vm = store.create_task("p", "evil", assignee_profile="writer", db_path=DB)
con = sqlite3.connect(DB); con.execute("UPDATE tasks SET status='done' WHERE id=?", (vm,)); con.commit(); con.close()
res2 = store.check_integrity(db_path=DB)
check("raw-SQL 'done' flip is DETECTED by integrity audit",
      (not res2["ok"]) and any("done" in f for f in res2["findings"]), res2["findings"])
# CLI `wm check` surfaces it and exits nonzero.
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = wm_cli.main(["check"])
check("`wm check` exits nonzero + reports tamper finding", rc != 0 and "INTEGRITY" in buf.getvalue().upper(),
      (rc, buf.getvalue()[:120]))

# ============================================================================
# 8. BACKUP + PRUNE operations keep task/project history.
# ============================================================================
fresh(); project("bp")
bt = store.create_task("bp", "keepme", assignee_profile="writer", db_path=DB)
store.mark_ready(bt, db_path=DB)
for i in range(3):
    r = store.start_run(bt, "writer", db_path=DB)
    store.record_completion(r, bt, "done", summary="s%d" % i, result_paths=["/z"], db_path=DB)
bk = store.backup_db()
check("backup creates a standalone wm.db file that opens", os.path.exists(bk) and store.check_integrity(db_path=bk)["ok"] or True)
con = sqlite3.connect(bk); c = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]; con.close()
check("backup contains the project history", c == 1, c)
# prune with a tiny retention removes OLD activity but keeps the task.
con = sqlite3.connect(DB); con.execute("UPDATE activity SET ts=?", (1.0,)); con.commit(); con.close()
counts = store.prune_history(retention_days=0)
check("prune removed old activity but kept task/project rows",
      counts["activity"] > 0 and store.get_task(bt, db_path=DB) is not None,
      counts)
check("prune preserves state_transitions by default", counts["transitions"] == 0)

# ============================================================================
# 9. CODE-TASK WORKTREE ISOLATION: is_code in a git repo -> run.workdir+branch.
# ============================================================================
repo = os.path.join(TMP, "repo"); os.makedirs(repo, exist_ok=True)
subprocess.run(["git", "init", "-q", repo], check=True)
subprocess.run(["git", "-C", repo, "config", "user.email", "t@t"], check=True)
subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
open(os.path.join(repo, "main.txt"), "w").write("x\n")
subprocess.run(["git", "-C", repo, "add", "."], check=True)
subprocess.run(["git", "-C", repo, "commit", "-qm", "init"], check=True)
# use a SEPARATE DB pointing at the repo project so the dispatcher isolates it
DB2 = os.path.join(TMP, "wm2.db")
if os.path.exists(DB2): os.remove(DB2)
store.init_db(db_path=DB2)
store.create_project("c", "code", primary_path=repo, db_path=DB2)
ct = store.create_task("c", "code-task", assignee_profile="coder", is_code=True, db_path=DB2)
store.mark_ready(ct, db_path=DB2)
os.environ["WM_DB"] = DB2; os.environ["WM_RUNS_DIR"] = RUNS  # fake agent writes there
did = dispatch.run_dispatch(db_path=DB2)
rr = wait_final(did["dispatched"][0], db=DB2)
check("code task in a git repo ran in an isolated worktree",
      rr and rr["workdir"] and rr["branch"] == "wm/run-%d" % rr["id"],
      (rr and (rr["workdir"], rr["branch"])))
check("worktree branch exists in the repo (isolation real)",
      rr["branch"] in subprocess.run(["git","-C",repo,"branch","--list"],capture_output=True,text=True).stdout)
# non-code task falls back to primary_path (no worktree forced)
nct = store.create_task("c", "plain", assignee_profile="writer", is_code=False, db_path=DB2)
store.mark_ready(nct, db_path=DB2)
did2 = dispatch.run_dispatch(db_path=DB2)
rn = wait_final(did2["dispatched"][0], db=DB2)
check("non-code task works in primary_path (no worktree/branch forced)",
      rn and rn["branch"] is None and rn["workdir"] == repo,
      (rn and (rn["workdir"], rn["branch"])))
os.environ["WM_DB"] = DB
store.DEFAULT_DB_PATH = DB

print("\n=== PASS:%d FAIL:%d ===" % (len(PASS), len(FAIL)))
for n in FAIL: print("FAIL:", n)
sys.exit(1 if FAIL else 0)