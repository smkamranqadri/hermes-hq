#!/usr/bin/env python3
"""test_t6.py — LEAN T6 verification: parallelism + concurrency cap (fake agents).
python3 test_t6.py   # exit nonzero on any FAIL

Proves (per brief_t6 DoD, extending T3's marker-attribution harness):
  1. cap honored + configurable: concurrency_cap=2 via `wm config set` caps one
     dispatch to 2 running; the 3rd queues until a slot frees.
  2. three-way parallel: 3 independent ready tasks (analyst/writer/marketer),
     ONE dispatch, all 3 run concurrently (distinct launchers/run rows), all
     finish `done`, each with its OWN correct session_id (no cross-attribution).
  3. concurrent-finalize safety: 3 real wrappers finalize concurrently against
     the same DB, plus a threaded stress of simultaneous finalizers + an atomic
     claim race -> no lock/corruption, PRAGMA quick_check ok, atomic claim still
     yields exactly one winner.
  4. real-parallel SMELL: MANUAL note (full real path exercised in T7).

Stdlib only.
"""
import contextlib, io, json, os, sqlite3, subprocess, sys, tempfile, threading, time

BASE = os.path.dirname(os.path.abspath(__file__)); ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "core"); sys.path.insert(0, ENGINE)
import wm_store as store, wm_dispatch as dispatch, wm_cli  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wm_t6_")
DB, RUNS, PROF = (os.path.join(TMP, x) for x in ("wm.db", "runs", "profiles"))
os.makedirs(RUNS, exist_ok=True)
FAKE = os.path.join(TMP, "_fake_hermes.py")
open(FAKE, "w").write('''#!/usr/bin/env python3
import os,sys,json,time,sqlite3
_a=sys.argv[1:]
def val(k):
    for i,x in enumerate(_a[:-1]):
        if x==k: return _a[i+1]
marker=val("-c"); rid=str(marker).split("-")[-1]; profile=val("--profile")
runs=os.environ["WM_RUNS_DIR"]; prof=os.environ["WM_PROFILES_DIR"]
sdb=os.path.join(prof,profile,"state.db"); cpath=os.path.join(runs,rid+".completion.json")
os.makedirs(os.path.dirname(sdb),exist_ok=True)
con=sqlite3.connect(sdb); sid="SESS"+rid
con.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,started_at REAL,last_activity_at REAL,title TEXT)")
con.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?)",(sid,time.time(),time.time(),marker)); con.commit(); con.close()
sl=os.environ.get("WM_FAKE_SLEEP",""); time.sleep(float(sl) if sl else 0)
c={"completed":"done","summary":"ok","result_paths":[],"blocker":""}
if os.environ.get("WM_FAKE_REPORT")=="1": c["session_id"]=sid  # self-report via --pass-session-id
open(cpath,"w").write(json.dumps(c))
sys.exit(0)
''')
os.chmod(FAKE, 0o755)
store.DEFAULT_DB_PATH = DB
for k, v in [("WM_DB", DB), ("WM_RUNS_DIR", RUNS), ("WM_PROFILES_DIR", PROF),
             ("WM_PY", sys.executable), ("WM_HERMES", FAKE)]:
    os.environ[k] = v
os.environ["WM_FAKE_REPORT"] = "1"

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
def sess_row(sid, profile):
    c = sqlite3.connect(os.path.join(PROF, profile, "state.db")); c.row_factory = sqlite3.Row
    r = c.execute("SELECT id,title FROM sessions WHERE id=?", (sid,)).fetchone(); c.close(); return r
def quick_check(db_path=DB):
    c = _conn_ro(db_path)
    try:
        return c.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        c.close()
def _conn_ro(p):
    c = sqlite3.connect(p); c.row_factory = sqlite3.Row; return c
def stub(p, name):
    """Create independent ready task."""
    return store.create_task("t6", name, assignee_profile=p, db_path=DB)

AGENTS = [("analyst", "t6a_analyst"), ("writer", "t6b_writer"), ("marketer", "t6c_marketer")]

# --- baseline helpers sanity -------------------------------------------------
fresh()
check("fresh() seeds default concurrency_cap=3",
      store.get_meta("concurrency_cap", db_path=DB) == "3")

# ============================================================================
# 1. CAP honored + configurable. Set cap=2 (via the real `wm config set` CLI).
# ============================================================================
os.environ["WM_FAKE_SLEEP"] = "1.0"
fresh()
store.create_project("t6", "T6 project", primary_path=TMP, db_path=DB)
ta, tb, tc = (stub(p, n) for p, n in AGENTS)
for t in (ta, tb, tc): store.mark_ready(t, db_path=DB)

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = wm_cli.main(["config", "set", "concurrency_cap", "2"])
check("`wm config set concurrency_cap 2` persists via wm_meta",
      rc == 0 and store.get_meta("concurrency_cap", db_path=DB) == "2",
      (buf.getvalue().strip() if getattr(buf, "getvalue", None) else ""))
d1 = dispatch.run_dispatch(db_path=DB)
check("dispatch summary reflects configurable cap=2", d1["cap"] == 2, d1["cap"])
check("cap=2: exactly 2 ready tasks dispatched in ONE tick",
      len(d1["dispatched"]) == 2 and len(set(d1["dispatched"])) == 2, d1["dispatched"])
runs_running = store.running_runs(db_path=DB)
rd = store.get_task(ta, db_path=DB); rc_ = store.get_task(tc, db_path=DB)
check("cap=2: 2 runs concurrently `running` (never exceeds cap)",
      len(runs_running) == 2 and all(r["status"] == "running" for r in runs_running),
      [r["id"] for r in runs_running])
check("cap=2: third independent task QUEUES (still `ready`) while two run",
      rc_["status"] == "ready" and rd["status"] == "running", (rd["status"], rc_["status"]))
pids1 = {r["pid"] for r in runs_running}
check("cap=2: two runs have distinct live launchers (pids)",
      len(pids1) == 2 and all(p for p in pids1), sorted(pids1))
# free the 2 slots -> third dispatches
d1ra, d1rb = wait_final(d1["dispatched"][0]), wait_final(d1["dispatched"][1])
check("cap=2: first two runs complete `done`",
      d1ra and d1rb and d1ra["status"] == "done" and d1rb["status"] == "done",
      (d1ra["status"], d1rb["status"]))
d2 = dispatch.run_dispatch(db_path=DB)
check("cap=2: queued third dispatches once a slot frees", len(d2["dispatched"]) == 1, d2["dispatched"])
d2r = wait_final(d2["dispatched"][0])
check("queued third task completes `done` with its OWN session",
      d2r and d2r["status"] == "done" and d2r["session_id"] == "SESS%d" % d2r["id"],
      (d2r["status"], d2r["session_id"]))
check("quick_check ok after cap+queue lifecycle", quick_check() == "ok")

# ============================================================================
# 2. THREE-WAY parallel proof (cap=3, one dispatch, 3 distinct agents).
# ============================================================================
os.environ["WM_FAKE_SLEEP"] = "1.5"
fresh()
store.create_project("t6", "T6 project", primary_path=TMP, db_path=DB)
t3 = [store.mark_ready(stub(p, n), db_path=DB) for p, n in AGENTS]
d3 = dispatch.run_dispatch(db_path=DB)
check("cap=3: ONE dispatch launches all 3 concurrent runs",
      len(d3["dispatched"]) == 3 and len(set(d3["dispatched"])) == 3, d3["dispatched"])
r3 = [store.get_run(rid, db_path=DB) for rid in d3["dispatched"]]
running3 = store.running_runs(db_path=DB)
check("3 runs concurrently `running` with distinct launchers (true 3-way parallel)",
      len(running3) == 3 and all(r["status"] == "running" for r in running3)
      and len({r["pid"] for r in running3}) == 3,
      [r["pid"] for r in running3])
# wait all three, assert per-run OWN correct session + marker, no cross-attribution
fin = [wait_final(rid) for rid in d3["dispatched"]]
all_done = all(r and r["status"] == "done" for r in fin)
sids = [(r["id"], r["session_id"], r["agent_profile"]) for r in fin]
check("all 3 parallel runs complete `done`", all_done, sids)
ok_sess = True; seen = {}
for r in fin:
    exp = "SESS%d" % r["id"]
    if r["session_id"] != exp: ok_sess = False; break
    seen[r["session_id"]] = r["agent_profile"]
    row = sess_row(r["session_id"], r["agent_profile"])
    if not (row and row["title"] == "wm-run-%d" % r["id"]): ok_sess = False; break
check("each parallel run has its OWN correct session_id (no cross-attribution)",
      ok_sess and len(seen) == 3, seen)
check("quick_check ok after 3 concurrent finalizers", quick_check() == "ok")

# ============================================================================
# 3. concurrent-finalize safety (threaded stress + atomic-claim race).
# ============================================================================
os.environ["WM_FAKE_SLEEP"] = "0"
fresh()
store.create_project("t6", "T6 project", primary_path=TMP, db_path=DB)
runs, tasks = [], []
for i in range(3):
    tasks.append(stub(AGENTS[i][0], "cf_%d" % i))
    store.mark_ready(tasks[i], db_path=DB)
    runs.append(store.start_run(tasks[i], AGENTS[i][0], db_path=DB))
lk = []

def finalize(args):
    run_id, task_id, who = args
    try:
        store.record_completion(run_id, task_id, "done", summary="done-by-%s" % who,
                                result_paths=[], session_id="SX%d" % run_id, db_path=DB)
    except sqlite3.Error as e:
        lk.append(("FINALIZE", who, repr(e)))

ts = [threading.Thread(target=finalize, args=((runs[i], tasks[i], i),)) for i in range(3)]
for t in ts: t.start()
for t in ts: t.join()
check("3 concurrent finalizers: no sqlite3 lock/OperationalError",
      not lk, lk)
check("3 concurrent finalizers all recorded `done`",
      all(store.get_run(rid, db_path=DB)["status"] == "done" for rid in runs))
check("quick_check ok after threaded concurrent finalizers", quick_check() == "ok")

# atomic-claim race against the SAME ready task stays correct under load
z = store.create_task("t6", "race_target", assignee_profile="analyst", db_path=DB)
store.mark_ready(z, db_path=DB)
wins = []

def do_claim(tag):
    try:
        wins.append(store.claim_task(z, db_path=DB))
    except sqlite3.Error as e:
        lk.append(("CLAIM", tag, repr(e)))

ta = threading.Thread(target=do_claim, args=("t1",)); tb = threading.Thread(target=do_claim, args=("t2",))
ta.start(); tb.start(); ta.join(); tb.join()
check("atomic claim under load: exactly ONE of two racing claims wins",
      not lk and wins.count(True) == 1 and wins.count(False) == 1, wins)
check("atomic claim winner left task `running`, loser did not double-fire",
      store.get_task(z, db_path=DB)["status"] == "running")
check("quick_check ok after claim race", quick_check() == "ok")

# ============================================================================
# 4. real-parallel smoke (optional; budget > 0 => MANUAL note).
# ============================================================================
manual("real-parallel smoke", "skipped real spawns (lean; full real-parallel path "
                              "is the T7 Orchestrator exercise). Fake-agent path "
                              "here proves the dispatcher concurrency fully.")

print("\n=== PASS:%d FAIL:%d MANUAL:%d ===" % (len(PASS), len(FAIL), len(MANUAL)))
for n in FAIL: print("FAIL:", n)
for n in MANUAL: print("MANUAL:", n)
sys.exit(1 if FAIL else 0)