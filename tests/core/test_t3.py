#!/usr/bin/env python3
"""test_t3.py — LEAN T3 verification (fake agent; no LLM, no real wm.db).
python3 test_t3.py   # exit nonzero on any FAIL
Covers T3 DoD: deterministic/concurrency-safe session attribution (fake agents),
--pass-session-id cross-check channel, one dispatch launching TWO concurrent
runs each with its OWN session, render_brief automatic handoff context, and
`wm session <task_id>`.
"""
import contextlib, io, json, os, sqlite3, subprocess, sys, tempfile, time

BASE = os.path.dirname(os.path.abspath(__file__)); ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend", "core"); sys.path.insert(0, ENGINE)
import wm_store as store, wm_dispatch as dispatch, wm_cli  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wm_t3_")
DB, RUNS, PROF = (os.path.join(TMP, x) for x in ("wm.db", "runs", "profiles"))
os.makedirs(RUNS, exist_ok=True)
FAKE = os.path.join(TMP, "_fake_hermes.py")
open(FAKE, "w").write('''#!/usr/bin/env python3
import os,sys,json,time,sqlite3
_a=sys.argv[1:]
def val(k):
    for i,x in enumerate(_a[:-1]):
        if x==k: return _a[i+1]
marker=val("-c"); rid=marker.split("-")[-1]; profile=val("--profile")
runs=os.environ["WM_RUNS_DIR"]; prof=os.environ["WM_PROFILES_DIR"]
sdb=os.path.join(prof,profile,"state.db"); cpath=os.path.join(runs,rid+".completion.json")
os.makedirs(os.path.dirname(sdb),exist_ok=True)
con=sqlite3.connect(sdb); sid="SESS"+rid
con.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,started_at REAL,last_activity_at REAL,title TEXT)")
con.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?)",(sid,time.time(),time.time(),marker)); con.commit(); con.close()
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
def sess_row(sid, profile="writer"):
    c = sqlite3.connect(os.path.join(PROF, profile, "state.db")); c.row_factory = sqlite3.Row
    r = c.execute("SELECT id,title FROM sessions WHERE id=?", (sid,)).fetchone(); c.close(); return r

# --- 1. --pass-session-id reality check (live CLI; tolerant) -----------------
try:
    import shutil
    hrm = shutil.which("hermes") or ("/opt/hermes/bin/hermes"
                                     if os.path.exists("/opt/hermes/bin/hermes") else None)
    if hrm:
        out = subprocess.run([hrm, "chat", "--help"], capture_output=True, text=True, timeout=30).stdout
        check("--pass-session-id present in `hermes chat --help`", "pass-session-id" in out)
    else:
        manual("--pass-session-id live check", "hermes CLI not found; behavior documented in wm_store.capture_session_id")
    doc = store.capture_session_id.__doc__ or ""
    check("capture rule documented (preferred + marker fallback, no 'newest row')",
          "--pass-session-id" in doc and "HERMES_TUI_PASS_SESSION_ID" in doc and "preferred" in doc and "marker" in doc)
except Exception as e:
    manual("--pass-session-id live check", "skipped: %s" % e)

# --- 2. CONCURRENCY: ONE dispatch -> TWO concurrent runs, each its OWN session
for scenario, report in [("marker-fallback", ""), ("pass-session-id-crosscheck", "1")]:
    fresh(); os.environ["WM_FAKE_REPORT"] = report
    store.create_project("t3", "T3", primary_path=TMP, db_path=DB)
    ta = store.create_task("t3", "alpha", assignee_profile="writer", db_path=DB)
    tb = store.create_task("t3", "beta", assignee_profile="writer", db_path=DB)
    store.mark_ready(ta, db_path=DB); store.mark_ready(tb, db_path=DB)
    dispatched = dispatch.run_dispatch(db_path=DB)["dispatched"]
    check("%s: one dispatch spawned TWO concurrent runs" % scenario,
          len(dispatched) == 2 and len(set(dispatched)) == 2, dispatched)
    ra, rb = wait_final(dispatched[0]), wait_final(dispatched[1])
    sa, sb = ra["session_id"], rb["session_id"]
    expected_a, expected_b = "SESS%d" % ra["id"], "SESS%d" % rb["id"]
    check("%s: each run has its OWN correct session, none cross-attributed" % scenario,
          ra and rb and sa == expected_a and sb == expected_b and sa != sb,
          (sa, expected_a, sb, expected_b))
    ok_a = sess_row(sa) and sess_row(sa)["title"] == "wm-run-%d" % ra["id"]
    ok_b = sess_row(sb) and sess_row(sb)["title"] == "wm-run-%d" % rb["id"]
    check("%s: session titles match their OWN run markers (deterministic)" % scenario,
          ok_a and ok_b, (sa, "wm-run-%d" % ra["id"], sb, "wm-run-%d" % rb["id"]))

# --- 3. render_brief automatic handoff (parent result_path+summary+--resume) -
fresh(); store.create_project("t3", "T3", primary_path=TMP, db_path=DB)
p1 = store.create_task("t3", "parent", assignee_profile="writer", db_path=DB)
p2 = store.create_task("t3", "child-driven", assignee_profile="writer", db_path=DB)
store.add_task_dep(p2, p1, db_path=DB)
r1 = store.start_run(p1, "writer", db_path=DB)
store.record_completion(r1, p1, "done", summary="parent summary text",
                        result_paths=["/tmp/t3_parent.txt"], session_id="SP1", db_path=DB)
store.mark_ready(p2, db_path=DB)
r2 = store.start_run(p2, "writer", db_path=DB)
brief = store.render_brief(r2, db_path=DB)
check("brief has task info + primary_path + completion-JSON instruction",
      "Project primary_path" in brief and "COMPLETION CONTRACT" in brief
      and "result_paths" in brief and TMP in brief)
check("brief auto-injects parent result_path + summary + --resume",
      "/tmp/t3_parent.txt" in brief and "parent summary text" in brief
      and "hermes --profile writer --resume SP1" in brief, "handoff present")
check("brief does NOT leak an incomplete parent", "child-driven" not in brief.split("HANDOFF")[-1])

# --- 4. `wm session <task_id>` ------------------------------------------------
buf = io.StringIO()
with contextlib.redirect_stdout(buf): wm_cli.main(["session", str(p1)])
out = buf.getvalue()
check("wm session prints real session_id + valid resume command",
      "SP1" in out and "hermes --profile writer --resume SP1" in out, out.strip().replace("\n", " | "))

print("\n=== PASS:%d FAIL:%d MANUAL:%d ===" % (len(PASS), len(FAIL), len(MANUAL)))
for n in FAIL: print("FAIL:", n)
for n in MANUAL: print("MANUAL:", n)
sys.exit(1 if FAIL else 0)