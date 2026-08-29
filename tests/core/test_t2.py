#!/usr/bin/env python3
"""test_t2.py — LEAN T2 verification (fake agent; no LLM, no real wm.db).
python3 test_t2.py   # exit nonzero on any FAIL
Covers T2 DoD: loop+sessionid+dependent promote (2), completion contract
both ways (3), liveness one case (4), `wm dispatch` manual path + status (5).
"""
import contextlib, io, os, sqlite3, sys, tempfile, time
BASE = os.path.dirname(os.path.abspath(__file__)); ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend", "core"); sys.path.insert(0, ENGINE)
import wm_store as store, wm_dispatch as dispatch, wm_cli      # noqa: E402

TMP = tempfile.mkdtemp(prefix="wm_t2_")
DB, RUNS, PROF = (os.path.join(TMP, x) for x in ("wm.db", "runs", "profiles"))
os.makedirs(RUNS, exist_ok=True)
FAKE = os.path.join(TMP, "_fake_hermes.py")
open(FAKE, "w").write('''#!/usr/bin/env python3
import os,sys,json,time,sqlite3
_a=sys.argv[1:]
def val(k):
    for i,x in enumerate(_a[:-1]):
        if x==k: return _a[i+1]
    return None
marker=val("-c"); profile=val("--profile"); rid=marker.split("-")[-1]
runs=os.environ["WM_RUNS_DIR"]; prof=os.environ["WM_PROFILES_DIR"]
sdb=os.path.join(prof,profile,"state.db"); cpath=os.path.join(runs,rid+".completion.json")
os.makedirs(os.path.dirname(sdb),exist_ok=True)
con=sqlite3.connect(sdb); sid="SESS"+rid
con.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,started_at REAL,last_activity_at REAL,title TEXT)")
con.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?)",(sid,time.time(),time.time(),marker)); con.commit(); con.close()
m=os.environ.get("WM_FAKE_MODE","done")
if m=="done": open(cpath,"w").write(json.dumps({"completed":"done","summary":"ok","result_paths":[],"blocker":""}))
elif m=="invalid": open(cpath,"w").write("{not valid")
sys.exit(0)
''')
os.chmod(FAKE, 0o755)
store.DEFAULT_DB_PATH = DB
for k, v in [("WM_DB", DB), ("WM_RUNS_DIR", RUNS), ("WM_PROFILES_DIR", PROF),
             ("WM_PY", sys.executable), ("WM_HERMES", FAKE)]:
    os.environ[k] = v

PASS, FAIL, MANUAL = [], [], []
def check(name, ok, d=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, " — " + str(d) if d else ""))
    (PASS if ok else FAIL).append(name)
def manual(name, d): MANUAL.append(name); print("[MANUAL] %s — %s" % (name, d))
def fresh():
    for p in (DB, DB + "-wal", DB + "-shm"):
        if os.path.exists(p): os.remove(p)
    store.init_db(db_path=DB)
def wait_final(run_id, t=40):
    start = time.time()
    while time.time() - start < t:
        r = store.get_run(run_id, db_path=DB)
        if r and r["status"] != "running": return r
        time.sleep(0.05)
    return store.get_run(run_id, db_path=DB)
def row(q):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    r = c.execute(q).fetchone(); c.close(); return r

# (2) ONE end-to-end manual dispatch: real wrapper, session capture, promotion.
fresh(); store.create_project("t2", "T2", primary_path=TMP, db_path=DB)
g2 = store.create_goal("t2", "T2 plan", db_path=DB)
a = store.create_task("t2", "alpha", assignee_profile="writer", goal_id=g2, db_path=DB)
b = store.create_task("t2", "beta-dependent", assignee_profile="writer", goal_id=g2, db_path=DB)
store.add_task_dep(b, a, db_path=DB)
store.release_goal(g2, db_path=DB)      # approve the plan -> b is waiting_approval
store.mark_ready(a, db_path=DB)
os.environ["WM_FAKE_MODE"] = "done"
check("wm dispatch exits 0 (manual path = run_dispatch)", wm_cli.main(["dispatch"]) == 0)
run = row("SELECT * FROM runs WHERE task_id=%d" % a)
check("dispatch created a run row", run and run["task_id"] == a)
fin = wait_final(run["id"])
check("run done with a real captured session_id",
      fin and fin["status"] == "done" and fin["session_id"] == "SESS%d" % run["id"],
      fin and (fin["status"], fin["session_id"]))
res = dispatch.run_dispatch(db_path=DB)  # next tick: auto-promote dependent and dispatch it
b_done = wait_final(res["dispatched"][0]) if res["dispatched"] else None
check("task -> done; dependent auto-promoted by next tick",
      store.get_task(a, db_path=DB)["status"] == "done"
      and b_done and b_done["status"] == "done"
      and store.get_task(b, db_path=DB)["status"] == "done",
      (store.get_task(a, db_path=DB)["status"], res["dispatched"],
       store.get_task(b, db_path=DB)["status"]))

# (3) Completion contract both ways (fake agent, real dispatch + wrapper).
for mode, want in [("done", "done"), ("invalid", "failed")]:
    fresh(); store.create_project("t2", "T2", primary_path=TMP, db_path=DB)
    t = store.create_task("t2", "c-" + mode, assignee_profile="writer", db_path=DB)
    store.mark_ready(t, db_path=DB); os.environ["WM_FAKE_MODE"] = mode
    fin = wait_final(dispatch.run_dispatch(db_path=DB)["dispatched"][0])
    check("contract %s (exit-0 valid JSON) -> %s" % (mode, want),
          fin and fin["status"] == want and store.get_task(t, db_path=DB)["status"] == want,
          fin and (fin["status"], store.get_task(t, db_path=DB)["status"]))

# (4) Liveness: running run, process dead + never finalized -> stalled.
fresh(); store.create_project("t2", "T2", primary_path=TMP, db_path=DB)
t = store.create_task("t2", "liveness", assignee_profile="writer", db_path=DB)
store.mark_ready(t, db_path=DB); store.claim_task(t, db_path=DB)
rid = store.start_run(t, "writer", db_path=DB)
store.set_run_pid(rid, 99999999, db_path=DB)      # nonexistent pid -> dead process
store.append_meta("stall_seconds", "5", db_path=DB)
res = dispatch.run_dispatch(db_path=DB)
check("liveness: dead-process run -> stalled (run failed, task stalled)",
      rid in res["stalled"] and store.get_run(rid, db_path=DB)["status"] == "failed"
      and store.get_task(t, db_path=DB)["status"] == "stalled", res["stalled"])

# (5) `wm status` reflects states.
fresh(); store.create_project("t2", "T2", primary_path=TMP, db_path=DB)
for title, st in [("S-running", "running"), ("S-failed", "failed"), ("S-stalled", "stalled"),
                  ("S-blocked", "blocked"), ("S-ready", "ready"), ("S-done", "done")]:
    t = store.create_task("t2", title, db_path=DB)
    store.mark_ready(t, db_path=DB)
    if st == "running": store.claim_task(t, db_path=DB)
    elif st == "done": store.complete_run(t, "done", db_path=DB)
    elif st != "ready": store.complete_run(t, st, error="x", db_path=DB)
buf = io.StringIO()
with contextlib.redirect_stdout(buf): wm_cli.main(["status"])
out = buf.getvalue().upper()
check("wm status groups show RUNNING/FAILED/STALLED/BLOCKED/READY/DONE",
      all(x in out for x in ("RUNNING", "FAILED", "STALLED", "BLOCKED", "READY", "DONE")))
manual("dispatcher cron job listed in `hermes cron list`",
       "registered via hermes cron, verified directly during the build")

print("\n=== PASS:%d FAIL:%d MANUAL:%d ===" % (len(PASS), len(FAIL), len(MANUAL)))
for n in FAIL: print("FAIL:", n)
for n in MANUAL: print("MANUAL:", n)
sys.exit(1 if FAIL else 0)