#!/usr/bin/env python3
# test_t5.py — LEAN T5 verification (fake reviewer; no LLM, no real wm.db).
# python3 test_t5.py  # exit nonzero on any FAIL
# DoD: required/optional -> auto-created review (never done directly); reviewer
# spawn (wrapper, fake agent); wm review approved -> done+promote /
# changes_requested -> rework+comments in re-run brief -> re-review -> done;
# `none` never routes; `optional` non-blocking (waive); reviews rows + status;
# NO second/separate review exists anywhere.
import contextlib, io, json, os, sqlite3, subprocess, sys, tempfile, time
BASE = os.path.dirname(os.path.abspath(__file__)); ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hermes_hq", "engine"); sys.path.insert(0, ENGINE)
import wm_store as store, wm_dispatch as dispatch, wm_cli  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wm_t5_")
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
open(cp,"w").write(json.dumps({"completed":"done","summary":"ok","result_paths":[],"blocker":""}))
sys.exit(0)
''')
os.chmod(FAKE, 0o755)
store.DEFAULT_DB_PATH = DB
for k, v in [("WM_DB", DB), ("WM_RUNS_DIR", RUNS), ("WM_PROFILES_DIR", PROF),
             ("WM_PY", sys.executable), ("WM_HERMES", FAKE)]:
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
def project(): store.create_project("t5", "T5", primary_path=TMP, db_path=DB)
def work_done(t, ses="S", paths=("/tmp/out.txt",)):
    r = store.start_run(t, "writer", db_path=DB)
    store.record_completion(r, t, "done", summary="built", result_paths=list(paths),
                            session_id=ses, db_path=DB)
def wait_final(rid, t=40):
    t0 = time.time()
    while time.time() - t0 < t:
        r = store.get_run(rid, db_path=DB)
        if r and r["status"] != "running": return r
        time.sleep(0.05)
    return store.get_run(rid, db_path=DB)
def verdict(tid, v, c): store.review_verdict(tid, v, comment=c, db_path=DB)
def nrev(t): return len(store.list_reviews(task_id=t, db_path=DB))
# Poll (bounded) until the auto-created review row for a task reaches a status.
# The wrapper finalizes the reviewer RUN (done) and only then sets the review row
# to 'reviewed' — so wait_final on the run can race ahead of the row. Determinize.
def wait_review_status(tid, want, t=2.0):
    t0 = time.time()
    while time.time() - t0 < t:
        rows = store.list_reviews(task_id=tid, db_path=DB)
        if rows and rows[0]["status"] == want: return rows[0]
        time.sleep(0.02)
    rows = store.list_reviews(task_id=tid, db_path=DB)
    return rows[0] if rows else {}

# (1) required completion -> needs_review (NOT done) + auto-created review.
fresh(); project()
req = store.create_task("t5", "req", definition_of_done="ship",
                        assignee_profile="writer", review_policy="required", db_path=DB)
store.mark_ready(req, db_path=DB); work_done(req)
rv = store.list_reviews(task_id=req, db_path=DB)[0]
check("required completion -> task needs_review (NOT done), result kept",
      store.get_task(req, db_path=DB)["status"] == "needs_review"
      and store.get_task(req, db_path=DB)["result_path"] == "/tmp/out.txt")
check("auto-created review pending, bound to origin, reviewer/review_policy set",
      nrev(req) == 1 and rv["task_id"] == req and rv["status"] == "pending"
      and rv["reviewer_profile"] == "reviewer" and rv["review_policy"] == "required")

# (2) none never routes; (3) optional routes but is non-blocking (waive).
not_ = store.create_task("t5", "none", assignee_profile="writer",
                         review_policy="none", db_path=DB)
store.mark_ready(not_, db_path=DB); work_done(not_, ses="SN")
check("none -> done directly, NO review", store.get_task(not_, db_path=DB)["status"] == "done" and nrev(not_) == 0)
opt = store.create_task("t5", "opt", assignee_profile="writer",
                        review_policy="optional", db_path=DB)
store.mark_ready(opt, db_path=DB); work_done(opt, ses="SO")
check("optional routes -> needs_review + review (non-blocking path exists)",
      store.get_task(opt, db_path=DB)["status"] == "needs_review" and nrev(opt) == 1)
store.waive_review(opt, comment="proceed", db_path=DB)
check("optional waive -> done + review waived (non-blocking)",
      store.get_task(opt, db_path=DB)["status"] == "done"
      and store.list_reviews(task_id=opt, db_path=DB)[0]["status"] == "waived")
w = False
try: store.waive_review(req, db_path=DB)
except ValueError: w = True
check("required policy cannot be waived", w and store.get_task(req, db_path=DB)["status"] == "needs_review")

# (4) wm review approved -> origin done + dependent promoted + no 2nd review.
# (Under the release gate a dependent only auto-promotes once its GOAL is
# released; a `planned` backlog dependent must stay parked.)
fresh(); project()
g4 = store.create_goal("t5", "plan", db_path=DB)
pa = store.create_task("t5", "parent", assignee_profile="writer", review_policy="required", goal_id=g4, db_path=DB)
ch = store.create_task("t5", "child", assignee_profile="writer", goal_id=g4, db_path=DB)
store.add_task_dep(ch, pa, db_path=DB)
store.release_goal(g4, db_path=DB)   # approve the plan
check("release gate: child under released goal becomes waiting_approval (deps pending)",
      store.get_task(ch, db_path=DB)["status"] == "waiting_approval")
store.mark_ready(pa, db_path=DB); work_done(pa, ses="SP")
verdict(pa, "approved", "nice work"); rv = store.list_reviews(task_id=pa, db_path=DB)[0]
check("approve -> origin done, review done+approved, comments stored",
      store.get_task(pa, db_path=DB)["status"] == "done"
      and rv["status"] == "done" and rv["verdict"] == "approved" and rv["comments"] == "nice work")
check("approve -> dependent auto-promoted to ready (released goal + deps done)",
      store.get_task(ch, db_path=DB)["status"] == "ready")
check("SINGLE review: 1 review for origin + exactly 2 task rows (no review task)",
      nrev(pa) == 1 and len(store.list_tasks(db_path=DB)) == 2, nrev(pa))

# (4b) BACKLOG gate: a `planned` dependent under an UNRELEASED goal must NOT
# auto-run even after its parent completes.
fresh(); project()
gu = store.create_goal("t5", "holding", db_path=DB)
ph = store.create_task("t5", "parentH", assignee_profile="writer", goal_id=gu, db_path=DB)
bu = store.create_task("t5", "childH", assignee_profile="writer", goal_id=gu, db_path=DB)
store.add_task_dep(bu, ph, db_path=DB)   # goal NOT released
store.mark_ready(ph, db_path=DB); work_done(ph, ses="SH")
check("planned dependent under UNRELEASED goal stays planned (no auto-run)",
      store.get_task(bu, db_path=DB)["status"] == "planned")

# (5) changes_requested -> rework; comments in re-run brief; re-review -> done.
fresh(); project()
t = store.create_task("t5", "draft", assignee_profile="writer", review_policy="required", db_path=DB)
store.mark_ready(t, db_path=DB); work_done(t, ses="SR")
verdict(t, "changes_requested", "please add tests")
check("changes_requested -> origin rework, review changes_requested + comments",
      store.get_task(t, db_path=DB)["status"] == "rework"
      and store.list_reviews(task_id=t, db_path=DB)[0]["comments"] == "please add tests")
check("rework task is claimable (re-dispatched)",
      store.claim_task(t, db_path=DB) is True and store.get_task(t, db_path=DB)["status"] == "running")
rr = store.start_run(t, "writer", db_path=DB)
check("re-run brief auto-injects Reviewer comments", "please add tests" in store.render_brief(rr, db_path=DB))
store.record_completion(rr, t, "done", summary="v2", result_paths=["/tmp/v2.txt"],
                        session_id="SR2", db_path=DB)
check("re-completion auto-creates re-review (2nd row); origin back to needs_review",
      nrev(t) == 2 and store.get_task(t, db_path=DB)["status"] == "needs_review")
verdict(t, "approved", "ok")
check("re-review approved -> origin done (comments end-to-end)",
      store.get_task(t, db_path=DB)["status"] == "done"
      and store.list_reviews(task_id=t, db_path=DB)[0]["status"] == "done")

# (6) DISPATCHER spawns a real (fake) Reviewer run from the auto-created review.
fresh(); project()
g6 = store.create_goal("t5", "pipelined", db_path=DB)
pt = store.create_task("t5", "art", assignee_profile="writer", review_policy="required", goal_id=g6, db_path=DB)
dp = store.create_task("t5", "consumer", assignee_profile="writer", goal_id=g6, db_path=DB)
store.add_task_dep(dp, pt, db_path=DB)
store.release_goal(g6, db_path=DB)
store.mark_ready(pt, db_path=DB)
w1 = dispatch.run_dispatch(db_path=DB)
check("dispatch spawns the WORK run of a required task", len(w1["dispatched"]) == 1)
wr = wait_final(w1["dispatched"][0])
check("work run done; task -> needs_review; review auto-created by wrapper",
      wr["status"] == "done" and store.get_task(pt, db_path=DB)["status"] == "needs_review" and nrev(pt) == 1)
w2 = dispatch.run_dispatch(db_path=DB)
check("next dispatch spawns the auto-created REVIEW (reviewer) run",
      len(w2["reviews_dispatched"]) == 1, w2["reviews_dispatched"])
rvr = wait_final(w2["reviews_dispatched"][0])
rrv = wait_review_status(pt, "reviewed")  # poll for row to be 'reviewed'
check("reviewer run done='reviewed'; origin STILL needs_review (not done)",
      rvr["status"] == "done" and rvr["agent_profile"] == "reviewer"
      and rrv["status"] == "reviewed" and store.get_task(pt, db_path=DB)["status"] == "needs_review")
verdict(pt, "approved", "approved")
check("approve after reviewer run -> origin done + dependent promoted",
      store.get_task(pt, db_path=DB)["status"] == "done" and store.get_task(dp, db_path=DB)["status"] == "ready")
check("review run excluded from origin handoff/session lookup",
      store.get_task_last_run(pt, db_path=DB)["id"] == wr["id"]
      and store.get_task_latest_run(pt, db_path=DB)["id"] == wr["id"])

# (7) status surfaces NEEDS REVIEW + REVIEWS; wm reviews lists the row.
fresh(); project()
s = store.create_task("t5", "st", assignee_profile="writer", review_policy="required", db_path=DB)
store.mark_ready(s, db_path=DB); work_done(s, ses="SS")
buf = io.StringIO()
with contextlib.redirect_stdout(buf): wm_cli.main(["status"])
out = buf.getvalue().upper()
check("status surfaces NEEDS REVIEW + REVIEWS block", "NEEDS REVIEW" in out and "REVIEWS" in out)
buf = io.StringIO()
with contextlib.redirect_stdout(buf): wm_cli.main(["reviews"])
check("wm reviews lists the auto-created review row",
      buf.getvalue().lower().find("review") >= 0 and "#1" in buf.getvalue())

print("\n=== PASS:%d FAIL:%d MANUAL:%d ===" % (len(PASS), len(FAIL), len(MANUAL)))
for n in FAIL: print("FAIL:", n)
for n in MANUAL: print("MANUAL:", n)
sys.exit(1 if FAIL else 0)