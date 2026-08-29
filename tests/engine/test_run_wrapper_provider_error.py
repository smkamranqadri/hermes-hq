#!/usr/bin/env python3
"""test_run_wrapper_provider_error.py — honest run errors when the agent dies
before it can write a completion file.

Observed failure: a run hit the provider's monthly cap (HTTP 429). The agent
process died mid-turn, so `<run_id>.completion.json` was never written. The
completion contract failed the run — correct — but the ONLY thing stored on
runs.error (the string the dashboard's run row renders) was

    missing completion file at .../17.completion.json

which names the symptom and hides the cause. This file pins the fix:

  1. the contract verdict is UNCHANGED (no completion == run+task failed);
  2. when the run log holds a real provider/agent failure, that line leads the
     stored error, quoted verbatim and labelled as coming from the log;
  3. the contract message is RETAINED after it — never replaced;
  4. with no useful underlying error, the stored error is exactly the old
     contract message (pure fallback, no invented cause);
  5. extraction is bounded (tail-only read, single line, hard length cap),
     safe (never raises, never mutates the verdict) and non-recursive (the
     wrapper's own log lines are skipped, so re-finalizing cannot nest).

Re-runnable, self-contained: python3 test_run_wrapper_provider_error.py
"""

import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hq", "engine"); sys.path.insert(0, ENGINE)

TMP = os.path.realpath(tempfile.mkdtemp(prefix="wm_provider_err_"))
RUNS = os.path.join(TMP, "runs")
PROF = os.path.join(TMP, "profiles")
DB = os.path.join(TMP, "wm.db")
os.makedirs(RUNS, exist_ok=True)
os.makedirs(os.path.join(PROF, "coder"), exist_ok=True)
os.environ["WM_RUNS_DIR"] = RUNS
os.environ["WM_PROFILES_DIR"] = PROF
os.environ["WM_DB"] = DB

import wm_store as store           # noqa: E402
import wm_run_agent as wra         # noqa: E402

store.DEFAULT_DB_PATH = DB
store.init_db(db_path=DB)
store.create_project("perr", "Provider errors", primary_path=TMP, db_path=DB)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append((name, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name
          + ((" — " + str(detail)) if detail else ""))


# The real thing Anthropic's CLI prints when the monthly cap is hit, with the
# ANSI colouring a TTY-ish log carries.
LIMIT_LINE = ('\x1b[31mAPI Error: 429 {"type":"error","error":{"type":'
              '"rate_limit_error","message":"You have reached your monthly '
              'usage limit."}}\x1b[0m')

# argv seeds wra._out()'s sink (real invocation is
# `wm_run_agent.py <run_id> <agent> <brief>`); without it every _out() call
# would write to the wrong file.
sys.argv = [sys.argv[0], "0", "coder", "brief"]


def new_run(agent="coder", log="", completion=None, review=False):
    """A claimed, running run with a seeded log (and optional completion)."""
    tid = store.create_task("perr", "t", assignee_profile=agent, db_path=DB)
    store.claim_task(tid, db_path=DB)
    if review:
        rid = store.create_review(tid, review_policy="required", db_path=DB)
        store.claim_review(rid, db_path=DB)
        run_id = store.start_run(tid, agent, db_path=DB)
        store.set_run_review(run_id, rid, db_path=DB)
    else:
        rid = None
        run_id = store.start_run(tid, agent, db_path=DB)
    if log:
        with open(store.run_log_path(run_id), "w") as f:
            f.write(log)
    if completion is not None:
        with open(store.completion_path(run_id), "w") as f:
            f.write(completion)
    sys.argv[1] = str(run_id)
    return tid, run_id, rid


def finalize(run_id, tid, review_id=None, exit_code=0, wrapper_err=None):
    return wra._finalize(run_id, tid, exit_code, DB, review_id=review_id,
                         wrapper_err=wrapper_err)


def run_error(run_id):
    r = store.get_run(run_id, db_path=DB)
    return (r["error"] or "") if r else ""


# ---------------------------------------------------------------------------
print("== 1. line cleaning is safe and bounded ==")
# ---------------------------------------------------------------------------
_c = wra._clean_log_line(LIMIT_LINE)
check("ANSI escapes are stripped", "\x1b" not in _c and "[31m" not in _c,
      repr(_c[:60]))
check("the provider's message survives cleaning",
      "429" in _c and "monthly usage limit" in _c, _c)
check("control characters are neutralised",
      "\x07" not in wra._clean_log_line("boom\x07\x00bang")
      and "\x00" not in wra._clean_log_line("boom\x07\x00bang"),
      repr(wra._clean_log_line("boom\x07\x00bang")))
check("newlines/tabs collapse to a single line",
      "\n" not in wra._clean_log_line("a\nb\tc")
      and wra._clean_log_line("a\nb\tc") == "a b c",
      repr(wra._clean_log_line("a\nb\tc")))
_long = wra._clean_log_line("x" * 5000)
check("cleaning enforces a hard length cap",
      len(_long) <= wra._ERR_MAX_CHARS,
      "len=%d cap=%d" % (len(_long), wra._ERR_MAX_CHARS))
check("a truncated line is marked as truncated", _long.endswith("..."))
check("cleaning an empty/None line yields empty (no crash)",
      wra._clean_log_line(None) == "" and wra._clean_log_line("") == "")

# ---------------------------------------------------------------------------
print("\n== 2. log reading is bounded and never raises ==")
# ---------------------------------------------------------------------------
check("a missing log reads as empty",
      wra._log_tail(os.path.join(RUNS, "nope.log")) == "")
check("a directory instead of a log reads as empty (no exception)",
      wra._log_tail(RUNS) == "")
_big = os.path.join(TMP, "big.log")
with open(_big, "w") as f:
    f.write("filler line %d\n" % 0)
    f.write(("A" * 200 + "\n") * 2000)          # ~400KB of noise
    f.write(LIMIT_LINE + "\n")
_tail = wra._log_tail(_big)
check("a large log is read tail-only, within the byte bound",
      0 < len(_tail.encode("utf-8")) <= wra._LOG_TAIL_BYTES,
      "bytes=%d bound=%d" % (len(_tail.encode("utf-8")), wra._LOG_TAIL_BYTES))
check("the partial first line of a mid-file seek is dropped",
      all(ln == "" or len(ln) == 200 or "API Error" in ln
          for ln in _tail.splitlines()),
      "first=%r" % _tail.splitlines()[0][:40])
check("the failure at the end of a large log is still found",
      "monthly usage limit" in (wra._scan_log_for_error(_big) or ""),
      wra._scan_log_for_error(_big))
_bin = os.path.join(TMP, "binary.log")
with open(_bin, "wb") as f:
    f.write(b"\xff\xfe\x00garbage\xc3\x28\n" + LIMIT_LINE.encode("utf-8"))
check("undecodable bytes do not raise and do not block extraction",
      "429" in (wra._scan_log_for_error(_bin) or ""),
      wra._scan_log_for_error(_bin))

# ---------------------------------------------------------------------------
print("\n== 3. what counts as an underlying failure ==")
# ---------------------------------------------------------------------------


def scan(text):
    p = os.path.join(TMP, "scan.log")
    with open(p, "w") as f:
        f.write(text)
    return wra._scan_log_for_error(p)


check("HTTP 429 monthly usage limit is extracted",
      "monthly usage limit" in (scan(LIMIT_LINE + "\n") or ""), scan(LIMIT_LINE))
_o = scan("thinking...\nAPI Error: 529 overloaded_error\n")
check("a 529 overloaded provider error is extracted",
      _o is not None and "overloaded" in _o, _o)
_a = scan("hello\nAuthentication error: invalid API key\n")
check("an auth failure is extracted", _a is not None and "invalid API key" in _a, _a)
_t = scan("connecting\nconnection error: read timed out after 60s\n")
check("a transport failure is extracted", _t is not None and "timed out" in _t, _t)
_g = scan("step 1\nTraceback (most recent call last):\n  File x\n"
          "ValueError: bad config\n")
check("a plain crash is extracted when there is no provider error",
      _g is not None and "ValueError: bad config" in _g, _g)
_pref = scan('API Error: 429 monthly usage limit\nTraceback (most recent '
             'call last):\nRuntimeError: cleanup failed\n')
check("a provider error OUTRANKS a later generic traceback",
      _pref is not None and "429" in _pref, _pref)
_last = scan("API Error: 500 server error\nAPI Error: 429 monthly usage limit\n")
check("the newest provider error wins within its tier",
      _last is not None and "429" in _last, _last)

# Honesty: benign output must not be dressed up as a failure.
check("a clean log yields no underlying error",
      scan("planning\nwrote file\ndone: 3 files changed\n") is None,
      scan("planning\nwrote file\ndone: 3 files changed\n"))
check("an empty log yields no underlying error", scan("") is None)
check("a bare number that looks like a status code is NOT a failure",
      scan("used 429 tokens\nport 5000 listening\n") is None,
      scan("used 429 tokens\nport 5000 listening\n"))
check("the word 'error' inside ordinary prose is not enough",
      scan("I will add error handling to the parser\n") is None,
      scan("I will add error handling to the parser\n"))

# Non-recursion: the wrapper's own CONTRACT line already embeds an extracted
# error. Re-scanning it would nest the message on every re-finalize.
_wrap = scan("[wm_run_agent 7] CONTRACT: agent log: API Error: 429 monthly "
             "usage limit; missing completion file at /x/7.completion.json -> "
             "run+task FAILED (process exited 1)\n")
check("the wrapper's own log lines are skipped (no nesting on re-finalize)",
      _wrap is None, _wrap)

# ---------------------------------------------------------------------------
print("\n== 4. _underlying_error precedence ==")
# ---------------------------------------------------------------------------
_tid, _rid, _ = new_run(log=LIMIT_LINE + "\n")
_u = wra._underlying_error(_rid)
check("an extracted cause is LABELLED as coming from the log",
      _u is not None and _u.startswith("agent log: "), _u)
check("the extracted cause quotes the provider verbatim",
      _u is not None and "monthly usage limit" in _u, _u)
_w = wra._underlying_error(_rid, wrapper_err="agent run timed out after 6h")
check("a first-hand wrapper error OUTRANKS the log scan",
      _w == "agent run timed out after 6h", _w)
_none_tid, _none_rid, _ = new_run(log="all good\n")
check("no underlying error when the log holds none",
      wra._underlying_error(_none_rid) is None,
      wra._underlying_error(_none_rid))
check("a run with no log at all degrades to None",
      wra._underlying_error(999999) is None,
      wra._underlying_error(999999))

# ---------------------------------------------------------------------------
print("\n== 5. finalize: the provider failure reaches the stored run error ==")
# ---------------------------------------------------------------------------
tid, rid, _ = new_run(log="working on it\n" + LIMIT_LINE + "\n")
status = finalize(rid, tid, exit_code=1)
err = run_error(rid)
check("no completion still FAILS the run (contract verdict unchanged)",
      status == "failed" and store.get_run(rid, db_path=DB)["status"] == "failed",
      "status=%s run=%s" % (status, store.get_run(rid, db_path=DB)["status"]))
check("the task is failed too",
      store.get_task(tid, db_path=DB)["status"] == "failed",
      store.get_task(tid, db_path=DB)["status"])
check("the stored run error names the REAL failure (429)",
      "429" in err and "monthly usage limit" in err, err)
check("the stored run error LEADS with the underlying cause",
      err.startswith("agent log: "), err)
check("the completion-contract message is RETAINED after it",
      "missing completion file" in err, err)
check("the stored run error stays bounded",
      len(err) <= wra._ERR_MAX_CHARS + 200, "len=%d" % len(err))
check("no ANSI escapes leak into the stored error", "\x1b" not in err)
check("the exit code is still recorded",
      store.get_run(rid, db_path=DB)["exit_code"] == 1,
      store.get_run(rid, db_path=DB)["exit_code"])
_conn = sqlite3.connect(DB)
try:
    _details = [r[0] or "" for r in _conn.execute(
        "SELECT detail FROM activity WHERE task_id=?", (tid,)).fetchall()]
finally:
    _conn.close()
check("the failure activity detail carries the real cause too",
      any("429" in d for d in _details), _details)

# ---------------------------------------------------------------------------
print("\n== 6. the same holds for an INVALID completion file ==")
# ---------------------------------------------------------------------------
tid2, rid2, _ = new_run(log=LIMIT_LINE + "\n", completion="{not json")
finalize(rid2, tid2, exit_code=0)
err2 = run_error(rid2)
check("invalid completion JSON also surfaces the provider failure",
      "429" in err2, err2)
check("invalid completion JSON keeps its contract message",
      "invalid completion JSON" in err2, err2)

tid3, rid3, _ = new_run(log=LIMIT_LINE + "\n",
                        completion=json.dumps({"completed": "maybe"}))
finalize(rid3, tid3, exit_code=0)
err3 = run_error(rid3)
check("an out-of-contract completed= value also surfaces the cause",
      "429" in err3 and "completed=" in err3, err3)

# ---------------------------------------------------------------------------
print("\n== 7. fallback: no useful cause means the old message, unchanged ==")
# ---------------------------------------------------------------------------
tid4, rid4, _ = new_run(log="planned the work\nwrote 2 files\n")
finalize(rid4, tid4, exit_code=0)
err4 = run_error(rid4)
check("with nothing to report the error is exactly the contract message",
      err4 == "missing completion file at %s" % store.completion_path(rid4),
      err4)
check("no cause is invented when the log is clean",
      "agent log:" not in err4, err4)

tid5, rid5, _ = new_run()          # no log file at all
finalize(rid5, tid5, exit_code=0)
check("a run with no log file falls back cleanly",
      run_error(rid5) == "missing completion file at %s"
      % store.completion_path(rid5), run_error(rid5))

# ---------------------------------------------------------------------------
print("\n== 8. a wrapper-observed failure is reported first-hand ==")
# ---------------------------------------------------------------------------
tid6, rid6, _ = new_run(log="starting\n")
finalize(rid6, tid6, exit_code=124, wrapper_err="agent run timed out after 6h")
err6 = run_error(rid6)
check("the timeout the wrapper saw leads the stored error",
      err6.startswith("agent run timed out after 6h"), err6)
check("the contract message is still retained",
      "missing completion file" in err6, err6)

tid7, rid7, _ = new_run(log="starting\n")
finalize(rid7, tid7, exit_code=None,
         wrapper_err="failed to launch agent: [Errno 2] No such file")
check("a launch failure is no longer dropped on the floor",
      "failed to launch agent" in run_error(rid7), run_error(rid7))

# ---------------------------------------------------------------------------
print("\n== 9. a SUCCESSFUL run is untouched by log scanning ==")
# ---------------------------------------------------------------------------
tid8, rid8, _ = new_run(
    log="retried after " + LIMIT_LINE + "\nrecovered\n",
    completion=json.dumps({"completed": "done", "summary": "shipped",
                           "result_paths": ["/tmp/x"], "blocker": ""}))
st8 = finalize(rid8, tid8, exit_code=0)
check("a valid done completion still completes the run",
      st8 == "done" and store.get_run(rid8, db_path=DB)["status"] == "done",
      "st=%s run=%s" % (st8, store.get_run(rid8, db_path=DB)["status"]))
check("a transient 429 EARLIER in the log never pollutes a successful run",
      not run_error(rid8), repr(run_error(rid8)))

tid9, rid9, _ = new_run(
    log=LIMIT_LINE + "\n",
    completion=json.dumps({"completed": "failed", "summary": "",
                           "result_paths": [], "blocker": "agent gave up"}))
finalize(rid9, tid9, exit_code=1)
check("an agent's OWN blocker is preserved verbatim (not overwritten)",
      run_error(rid9) == "agent gave up", run_error(rid9))

# ---------------------------------------------------------------------------
print("\n== 10. review runs get the same honesty ==")
# ---------------------------------------------------------------------------
tidr, ridr, review_id = new_run(agent="reviewer", log=LIMIT_LINE + "\n",
                                review=True)
finalize(ridr, tidr, review_id=review_id, exit_code=1)
errr = run_error(ridr)
check("a review run with no completion surfaces the provider failure",
      "429" in errr and "missing completion file" in errr, errr)
check("the review row is failed",
      store.get_review(review_id, db_path=DB)["status"] == "failed",
      store.get_review(review_id, db_path=DB)["status"])

# ---------------------------------------------------------------------------
print("\n== 11. re-finalizing cannot nest the message ==")
# ---------------------------------------------------------------------------
# _finalize writes its CONTRACT line (which embeds the extracted cause) into the
# very log it scans. A second pass over that log must extract the SAME cause,
# not a cause wrapped in a cause.
tidn, ridn, _ = new_run(log=LIMIT_LINE + "\n")
finalize(ridn, tidn, exit_code=1)
first = wra._underlying_error(ridn)
second = wra._underlying_error(ridn)
check("the wrapper's CONTRACT line is in the log (precondition)",
      "CONTRACT:" in open(store.run_log_path(ridn)).read())
check("re-extraction is stable — the cause does not nest",
      first == second and first.count("agent log:") == 1,
      "first=%r second=%r" % (first, second))

print()
print("passed=%d failed=%d total=%d"
      % (len(PASS), len(FAIL), len(PASS) + len(FAIL)))
sys.exit(1 if FAIL else 0)
