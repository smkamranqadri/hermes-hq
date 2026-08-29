#!/usr/bin/env python3
"""test_t4.py — LEAN T4 verification (no LLM, no real wm.db, no agent spawn).
python3 test_t4.py   # exit nonzero on any FAIL
Covers T4 DoD: idle-hang stall detection (alive+stale -> stalled; alive+recent
-> NOT stalled), wm retry / resume / mark manual, no silent auto-retry, and
wm config set stall_seconds honored + wm status surfaces stalled/manual.
"""
import contextlib, io, os, sqlite3, sys, tempfile, time
BASE = os.path.dirname(os.path.abspath(__file__)); ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hermes_hq", "engine"); sys.path.insert(0, ENGINE)
import wm_store as store, wm_dispatch as dispatch, wm_cli  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wm_t4_")
DB, RUNS, PROF = (os.path.join(TMP, x) for x in ("wm.db", "runs", "profiles"))
os.makedirs(RUNS, exist_ok=True)
store.DEFAULT_DB_PATH = DB
for k, v in [("WM_DB", DB), ("WM_RUNS_DIR", RUNS), ("WM_PROFILES_DIR", PROF)]:
    os.environ[k] = v

PASS, FAIL, MANUAL = [], [], []
def check(n, ok, d=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", n, " — " + str(d) if d else ""))
    (PASS if ok else FAIL).append(n)
def manual(n, d): MANUAL.append(n); print("[MANUAL] %s — %s" % (n, d))
def fresh():
    for p in (DB, DB + "-wal", DB + "-shm"):
        if os.path.exists(p): os.remove(p)
    store.init_db(db_path=DB)
def plant(agent, marker, laa):
    sdb = os.path.join(PROF, agent, "state.db")
    os.makedirs(os.path.dirname(sdb), exist_ok=True)
    c = sqlite3.connect(sdb)
    c.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,"
              "started_at REAL,last_activity_at REAL,title TEXT)")
    c.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?)",
              ("SID-" + marker, time.time() - 500, laa, marker))
    c.commit(); c.close()
def make_running(agent="writer"):
    """A 'running' run whose wrapper process is ALIVE (this test process)."""
    store.create_project("t4", "T4", primary_path=TMP, db_path=DB)
    t = store.create_task("t4", "hang", assignee_profile=agent, db_path=DB)
    store.mark_ready(t, db_path=DB); store.claim_task(t, db_path=DB)
    rid = store.start_run(t, agent, db_path=DB)
    store.set_run_pid(rid, os.getpid(), db_path=DB)  # alive pid
    return t, rid

# (1) idle-hang: process alive + stale session -> STALLED (no session_id yet).
fresh(); store.append_meta("stall_seconds", "300", db_path=DB)
t, rid = make_running()
plant("writer", "wm-run-%d" % rid, time.time() - 3600)   # idle past 300s
res = dispatch.run_dispatch(db_path=DB)
check("idle-hang: alive proc + stale session -> run failed + task stalled",
      rid in res["stalled"] and store.get_run(rid, db_path=DB)["status"] == "failed"
      and store.get_task(t, db_path=DB)["status"] == "stalled",
      (store.get_run(rid, db_path=DB)["status"], res["stalled"]))

# (2) inverse: process alive + recent session activity -> NOT stalled.
fresh(); store.append_meta("stall_seconds", "300", db_path=DB)
t, rid = make_running()
plant("writer", "wm-run-%d" % rid, time.time() - 5)      # recent activity
res = dispatch.run_dispatch(db_path=DB)
check("inverse: alive proc + recent activity -> NOT stalled (still running)",
      rid not in res["stalled"] and store.get_run(rid, db_path=DB)["status"] == "running",
      (store.get_run(rid, db_path=DB)["status"], res["stalled"]))

# (3) wm retry: failed -> ready, old run kept; refuses while running.
fresh(); store.create_project("t4", "T4", primary_path=TMP, db_path=DB)
tf = store.create_task("t4", "failme", assignee_profile="writer", db_path=DB)
store.mark_ready(tf, db_path=DB); store.claim_task(tf, db_path=DB)
rf = store.start_run(tf, "writer", db_path=DB)
store.finish_run(rf, status="failed", session_id="SID-f", error="boom", db_path=DB)
store.complete_run(tf, "failed", db_path=DB)
store.retry_task(tf, db_path=DB)
check("retry: failed task -> ready; old run row + history preserved",
      store.get_task(tf, db_path=DB)["status"] == "ready"
      and store.get_run(rf, db_path=DB)["status"] == "failed"
      and store.get_run(rf, db_path=DB)["session_id"] == "SID-f")
fresh(); store.create_project("t4", "T4", primary_path=TMP, db_path=DB)
tr = store.create_task("t4", "running", assignee_profile="writer", db_path=DB)
store.mark_ready(tr, db_path=DB); store.claim_task(tr, db_path=DB)  # running
refused = False
try:
    store.retry_task(tr, db_path=DB)
except ValueError:
    refused = True
buf = io.StringIO()
with contextlib.redirect_stdout(buf): rc = wm_cli.main(["retry", str(tr)])
check("retry refuses while running (store ValueError + CLI nonzero)",
      refused and rc != 0 and "running" in buf.getvalue(), (refused, rc, buf.getvalue()))

# (4) wm resume: prints a valid --resume for a stalled run (session via marker).
fresh(); store.append_meta("stall_seconds", "300", db_path=DB)
t, rid = make_running()
plant("writer", "wm-run-%d" % rid, time.time() - 3600)
dispatch.run_dispatch(db_path=DB)                      # -> stalled (sid NULL)
want = "hermes --profile writer --resume " + ("SID-wm-run-%d" % rid)
buf = io.StringIO()
with contextlib.redirect_stdout(buf): rc = wm_cli.main(["resume", str(t)])
check("resume: valid --resume printed for the stalled run",
      rc == 0 and want in buf.getvalue(), buf.getvalue().strip().replace("\n", " | "))

# (5) wm mark manual: stalled task -> manual, taking it out of the queue.
fresh(); store.create_project("t4", "T4", primary_path=TMP, db_path=DB)
tm = store.create_task("t4", "stuck", assignee_profile="writer", db_path=DB)
store.mark_ready(tm, db_path=DB); store.complete_run(tm, "stalled", db_path=DB)
store.mark_manual(tm, note="human took over", db_path=DB)
check("mark manual: stalled task -> manual (out of queue)",
      store.get_task(tm, db_path=DB)["status"] == "manual")

# (6) no silent auto-retry: dispatch does NOT respawn a failed task on its own.
fresh(); store.create_project("t4", "T4", primary_path=TMP, db_path=DB)
n = store.create_task("t4", "failedtask", assignee_profile="writer", db_path=DB)
store.mark_ready(n, db_path=DB); store.complete_run(n, "failed", db_path=DB)
res = dispatch.run_dispatch(db_path=DB)
check("no auto-retry: failed task NOT respawned by dispatch",
      res["dispatched"] == [] and res["stalled"] == []
      and store.get_task(n, db_path=DB)["status"] == "failed",
      (res["dispatched"], res["stalled"], store.get_task(n, db_path=DB)["status"]))

# (7) wm config set stall_seconds N — persists and is honored by the dispatcher.
fresh(); wm_cli.main(["config", "set", "stall_seconds", "1"])
t, rid = make_running("writer")
plant("writer", "wm-run-%d" % rid, time.time() - 60)   # idle vs 1s threshold
res = dispatch.run_dispatch(db_path=DB)
check("config set stall_seconds honored (tiny threshold flags idle run)",
      store.get_meta("stall_seconds", db_path=DB) == "1"
      and rid in res["stalled"], (store.get_meta("stall_seconds", db_path=DB), res["stalled"]))

# (8) wm status surfaces stalled AND manual distinctly.
fresh(); store.create_project("t4", "T4", primary_path=TMP, db_path=DB)
for title, st in [("st1", "stalled"), ("m1", "manual"), ("r1", "running"),
                  ("f1", "failed"), ("bl1", "blocked"), ("dn1", "done")]:
    tt = store.create_task("t4", title, db_path=DB); store.mark_ready(tt, db_path=DB)
    if st == "running":
        store.claim_task(tt, db_path=DB)
    else:
        store.complete_run(tt, st, error="x", db_path=DB)
buf = io.StringIO()
with contextlib.redirect_stdout(buf): wm_cli.main(["status"])
out = buf.getvalue().upper()
check("wm status separates RUNNING/FAILED/STALLED/BLOCKED/MANUAL/DONE",
      all(x in out for x in ("RUNNING", "FAILED", "STALLED", "BLOCKED", "MANUAL", "DONE")))

# wm resume without an id must still unpause dispatch (no regression).
store.set_paused(True)
buf = io.StringIO()
with contextlib.redirect_stdout(buf): rc = wm_cli.main(["resume"])
check("wm resume (no id) still unpauses dispatch",
      rc == 0 and store.get_meta("paused", db_path=DB) == "0")

print("\n=== PASS:%d FAIL:%d MANUAL:%d ===" % (len(PASS), len(FAIL), len(MANUAL)))
for n in FAIL: print("FAIL:", n)
for n in MANUAL: print("MANUAL:", n)
sys.exit(1 if FAIL else 0)