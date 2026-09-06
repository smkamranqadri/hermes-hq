#!/usr/bin/env python3
"""wm_run_agent.py — the run wrapper backing each Work Manager dispatch.

Usage: wm_run_agent.py <run_id> <agent> <brief_file>

Launched DETACHED by wm_dispatch for a claimed task. It:
  1. loads the run + task + project from the store;
  2. runs the real agent in a persisted session:
       hermes --profile <agent> chat -q <brief> -c 'wm-run-<run_id>'
              --create-if-missing --pass-session-id
     (the -c marker plants a distinctive session title used for the
      deterministic session-id capture in step 3.1);
     EXCEPTION (6.5.2): an `orchestrator`-assigned run (the internal `Plan goal
     #N` task) is the reserved DEFAULT-profile identity — there is no
     `profiles/orchestrator` — so it launches as
       hermes --profile default chat ...   with HERMES_HOME pinned to the ROOT
     Hermes home, and its sessions map to /opt/data/state.db. Both halves
     matter: `--profile orchestrator` aborts at launch, and a bare `hermes
     chat` would inherit an active specialist profile instead.
  3. after the process exits enforces the COMPLETION CONTRACT:
       - reads <runs_dir>/<run_id>.completion.json written by the agent as
         its LAST action;
       - valid completed=="done"  -> run done,  task done;
       - completed=="blocked"     -> run blocked, task blocked (blocker kept);
       - completed=="failed"      -> run failed, task failed;
       - missing/invalid JSON / completed not 'done' -> run+task FAILED even
         if the process exited 0. Process exit is NOT completion.
     Records session_id, completion JSON, exit_code, finished_at on the run;
     writes activity; promotes any tasks whose deps just became all 'done'.
     When there is NO usable completion the stored error leads with the
     UNDERLYING failure — the launch/timeout error the wrapper saw, else a
     verbatim provider/agent error line lifted from the bounded tail of the run
     log (e.g. an HTTP 429 monthly usage limit) — and keeps the completion
     contract message after it. "missing completion file" alone is the
     fallback, not the headline.
  4. The wrapper does NOT pump a fake heartbeat — liveness is judged by the
     dispatcher from process state + the Launched session's last_activity_at.

Stdlib only. Env-overridable paths (WM_DB/WM_RUNS_DIR/WM_PROFILES_DIR/
WM_HERMES/WM_PY) let tests point everything at scratch locations and a fake
'hermes' stub.
"""

import json
import os
import re
import subprocess
import sys
import time

try:
    from core import wm_store as store
except ImportError:  # run as a bare script from the engine dir
    import wm_store as store


def _out(msg):
    try:
        p = store.run_log_path(int(sys.argv[1]))
    except Exception:
        p = os.devnull
    with open(p, "a") as f:
        f.write("[wm_run_agent %s] %s\n" % (sys.argv[1], msg))


def _agent_env(agent, env):
    """The environment an agent's Hermes process runs under.

    Specialists are untouched: `--profile <agent>` fully determines their home.

    The Orchestrator is pinned. A bare `hermes chat` does NOT reliably mean
    "the default profile": hermes_cli.main._apply_profile_override() trusts an
    already-set profile-shaped HERMES_HOME (parent dir named `profiles`), and
    otherwise follows the sticky `<root>/active_profile`. A dispatch launched
    from inside a specialist's session therefore inherits e.g.
    HERMES_HOME=/opt/data/profiles/coder and the orchestrator run silently
    executes as the coder — writing its session into the WRONG state.db, where
    the capture/liveness probes (which read the root store, per
    store.agent_session_db_path) would never find it. So we set HERMES_HOME to
    the ROOT home explicitly, from the same canonical mapping.
    """
    env = dict(env or {})
    if agent == store.ORCHESTRATOR_AGENT:
        env["HERMES_HOME"] = store.hermes_root_home()
    return env


def _run_agent(hermes, agent, brief_text, run_id, cwd, env):
    marker = "wm-run-%s" % run_id
    # F-23: brief passes on stdin (--query-file -), never in argv (avoids
    # E2BIG on long rework chains and leaking the brief in `ps`).
    #
    # EVERY run names its profile explicitly. The reserved `orchestrator`
    # identity maps to Hermes' root profile, spelled `default` — there is no
    # `profiles/orchestrator`, so `--profile orchestrator` aborts at launch,
    # and omitting `--profile` entirely lets an inherited HERMES_HOME or a
    # sticky active_profile capture the run (see _agent_env). `--profile
    # default` resolves to the root home in both cases.
    profile = store.hermes_profile_arg(agent)
    cmd = [hermes, "--profile", profile, "chat", "-Q", "--query-file", "-",
           "-c", marker, "--create-if-missing", "--pass-session-id"]
    env = _agent_env(agent, env)
    _out("running: %s" % " ".join(cmd[:6] + ["-c", marker]))
    log_path = store.run_log_path(run_id)
    try:
        with open(log_path, "ab") as logf:
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=env, stdin=subprocess.PIPE,
                stdout=logf, stderr=logf)
            try:
                proc.communicate(input=brief_text.encode("utf-8"),
                                 timeout=6 * 3600)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                return 124, "agent run timed out after 6h"
    except OSError as e:
        _out("failed to launch agent: %s" % e)
        return None, "failed to launch agent: %s" % e
    return proc.returncode, None


# ---------------------------------------------------------------------------
# Underlying-failure extraction
# ---------------------------------------------------------------------------
# When a run dies on its provider (the observed case: Anthropic answering HTTP
# 429 "monthly usage limit"), the agent never gets to write its completion
# file. The contract still fails the run — correctly — but storing only
# "missing completion file at <path>" hides the one fact an operator needs.
# So the finalizer first tries to name the REAL failure, from the run log the
# agent's own stdout/stderr was teed into.
#
# The scan is deliberately conservative: bounded read, bounded output, verbatim
# quoting, and a labelled prefix. It is a heuristic over unstructured CLI
# output, so it never re-words a line into a claim the log does not make, and
# it never replaces the contract message — it precedes it.

# Only the tail of the log is read: a long run's log is unbounded, and the
# failure that killed the process is at the end.
_LOG_TAIL_BYTES = 64 * 1024
_LOG_TAIL_LINES = 400
# Longest fragment stored on the run/task (the dashboard renders runs.error
# inline in the run row, so a whole stack trace would be unreadable).
_ERR_MAX_CHARS = 320

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# The wrapper's own lines share the run log. They are skipped on purpose: one
# of them is the PREVIOUS finalize's "CONTRACT: ..." line, which already
# embeds an extracted error — re-extracting it would nest the message deeper
# on every rework of the same run log.
_WRAPPER_RE = re.compile(r"^\[wm_run_agent \d+\]")

# Tier 1 — lines that are unambiguously a provider/transport failure.
_PROVIDER_RES = [
    re.compile(r"(?i)usage limit"),
    re.compile(r"(?i)rate[ _-]?limit"),
    re.compile(r"(?i)too many requests"),
    re.compile(r"(?i)\bquota\b|\bcredit balance\b"
               r"|\binsufficient (?:credit|quota|funds)"),
    re.compile(r"(?i)\boverloaded\b"),
    re.compile(r"(?i)\b(?:api|provider|http|server|connection|network)"
               r"[ _-]?error\b"),
    re.compile(r"(?i)\b(?:invalid|missing|expired)[ _-]?api[ _-]?key\b"),
    re.compile(r"(?i)\bauthentication (?:error|failed)\b"
               r"|\bunauthorized\b|\bforbidden\b"),
    # A bare status code counts only next to error-ish context, so a token
    # count, a port or a timestamp can never be read as a failure.
    re.compile(r"(?i)\b(?:status|code|http)\W{0,3}(?:4\d\d|5\d\d)\b"),
    re.compile(r"(?i)\b(?:4\d\d|5\d\d)\b[^\n]{0,24}"
               r"\b(?:error|limit|exceeded|denied|unavailable|timed? ?out)\b"),
]

# Tier 2 — a generic crash/abort. Real, but less specific: used only when no
# tier-1 line exists, so a provider error always wins over the traceback it
# may have produced downstream.
_GENERIC_RES = [
    re.compile(r"(?i)^(?:fatal|error|exception|panic)\b\W"),
    re.compile(r"\b[A-Za-z_.]*(?:Error|Exception)\b\s*:"),
    re.compile(r"(?i)^traceback \(most recent call last\)"),
    re.compile(r"(?i)\b(?:command not found|no such file or directory"
               r"|permission denied|out of memory|killed)\b"),
    re.compile(r"(?i)\btimed out\b"),
]


def _clean_log_line(line):
    """One log line, safe to store and render: no ANSI, no control chars, no
    newlines, collapsed whitespace, hard length cap."""
    line = _ANSI_RE.sub("", line or "")
    line = "".join(
        ch if (ch >= " " and ch != "\x7f") else " " for ch in line)
    line = " ".join(line.split())
    if len(line) > _ERR_MAX_CHARS:
        line = line[:_ERR_MAX_CHARS - 3] + "..."
    return line


def _log_tail(path, max_bytes=_LOG_TAIL_BYTES):
    """The last <=max_bytes of a log as text. Bounded, never raises."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            raw = f.read(max_bytes)
    except OSError:
        return ""
    text = raw.decode("utf-8", "replace")
    if size > max_bytes:
        # The first line of a mid-file seek is a fragment — drop it rather
        # than quote half a sentence as if it were the whole error.
        nl = text.find("\n")
        text = text[nl + 1:] if nl >= 0 else ""
    return text


def _scan_log_for_error(log_path):
    """The most specific failure line in the log's tail, or None.

    Newest-first within a tier: the last provider error beats an earlier one,
    and any provider error beats a generic crash line.
    """
    text = _log_tail(log_path)
    if not text:
        return None
    lines = []
    for raw in text.splitlines()[-_LOG_TAIL_LINES:]:
        if _WRAPPER_RE.match(raw.lstrip()):
            continue
        cleaned = _clean_log_line(raw)
        if cleaned:
            lines.append(cleaned)
    for tier in (_PROVIDER_RES, _GENERIC_RES):
        for line in reversed(lines):
            if any(rx.search(line) for rx in tier):
                return line
    return None


def _underlying_error(run_id, wrapper_err=None):
    """One honest line naming why the agent actually failed, or None.

    `wrapper_err` (a launch failure or the 6h timeout) is authoritative — the
    wrapper observed it directly — so it wins over anything read out of the
    log. Otherwise the log tail is scanned. Diagnostics must never break
    finalization, so every failure here degrades to None (contract message
    only) rather than raising.
    """
    if wrapper_err:
        return _clean_log_line(wrapper_err) or None
    try:
        line = _scan_log_for_error(store.run_log_path(run_id))
    except Exception as e:
        _out("could not scan run log for an underlying error: %s" % e)
        return None
    return ("agent log: %s" % line) if line else None


def _read_completion(cpath):
    if not os.path.exists(cpath):
        return None, "missing completion file at %s" % cpath
    try:
        with open(cpath) as f:
            data = json.load(f)
    except Exception as e:
        return None, "invalid completion JSON at %s: %s" % (cpath, e)
    if not isinstance(data, dict):
        return None, "completion JSON is not an object"
    completed = data.get("completed")
    if completed not in ("done", "blocked", "failed", "manual"):
        return None, ("completion completed=%r invalid (must be "
                      "done|blocked|failed|manual)" % (completed,))
    return data, None


def _finalize(run_id, task_id, proc_exit, db_path, review_id=None,
              wrapper_err=None):
    """Apply the Completion contract after the agent process exits.

    `wrapper_err` is the error the wrapper itself observed while running the
    agent (launch failure, 6h timeout) — it is the truest cause when present.
    """
    cpath = store.completion_path(run_id)
    data, err = _read_completion(cpath)
    if err is None and review_id is not None \
            and data.get("completed") == "manual":
        # A review run never hands over — its whole job is the verdict.
        data, err = None, ("completion completed='manual' invalid for a "
                           "REVIEW run (must be done|blocked|failed)")
    # Deterministic session capture: prefer the session_id the agent
    # self-reported (via the --pass-session-id 'Session ID:' system-prompt
    # line it was asked to copy into the completion JSON), cross-checked
    # against the profile state.db; reliable fallback is the unique per-run
    # marker title lookup. Both channels are concurrency-safe (see
    # wm_store.capture_session_id).
    agent = store.get_run(run_id, db_path=db_path)["agent_profile"]
    reported = (data or {}).get("session_id") if data else None
    session_id = store.capture_session_id(
        run_id, agent,
        preferred=reported if isinstance(reported, str) else None,
        db_path=db_path)
    if err:
        # The contract verdict is unchanged (no completion == failed). What
        # changes is WHAT gets stored: the real failure first, the contract
        # message kept after it, so the dashboard shows "429 monthly usage
        # limit" instead of only "missing completion file".
        cause = _underlying_error(run_id, wrapper_err=wrapper_err)
        blocker = ("%s; %s" % (cause, err)) if cause else err
        _out("CONTRACT: %s -> run+task FAILED (process exited %s)"
             % (blocker, proc_exit))
        if review_id is not None:
            store.record_review_completion(
                run_id, review_id, completed="failed", summary="",
                result_paths=[], blocker=blocker, session_id=session_id,
                exit_code=proc_exit, db_path=db_path)
        else:
            store.record_completion(run_id, task_id, completed="failed",
                                    summary="", result_paths=[],
                                    blocker=blocker,
                                    session_id=session_id, exit_code=proc_exit,
                                    db_path=db_path)
        return "failed"
    completed = data.get("completed")
    summary = data.get("summary") or ""
    result_paths = data.get("result_paths") or []
    blocker = data.get("blocker") or ""
    _out("CONTRACT: completed=%r -> run %s (exit %s)"
         % (completed, completed, proc_exit))
    if review_id is not None:
        # T5: a review run's completion finalizes ONLY the review run + review
        # row. It never advances the origin task — the verdict does that via
        # `wm review`. No dependents are promoted here.
        store.record_review_completion(
            run_id, review_id, completed=completed, summary=summary,
            result_paths=result_paths, blocker=blocker,
            session_id=session_id, exit_code=proc_exit, db_path=db_path)
    else:
        store.record_completion(run_id, task_id, completed=completed,
                                summary=summary, result_paths=result_paths,
                                blocker=blocker, session_id=session_id,
                                exit_code=proc_exit, db_path=db_path)
    return completed


def main(argv=None):
    if len(sys.argv) < 4:
        sys.stderr.write("usage: wm_run_agent.py <run_id> <agent> <brief_file>\n")
        return 2
    run_id = int(sys.argv[1])
    agent = sys.argv[2]
    brief_file = sys.argv[3]

    db_path = os.environ.get("WM_DB") or store.DEFAULT_DB_PATH
    cfg = {
        "hermes": store.resolve_hermes(),
        "runs_dir": store.resolve_runs_dir(),
    }

    run = store.get_run(run_id, db_path=db_path)
    if run is None:
        sys.stderr.write("wm_run_agent: no run %d\n" % run_id)
        return 1
    task_id = run["task_id"]
    review_id = run["review_id"]
    task = store.get_task(task_id, db_path=db_path)
    project = store.get_project(task["project_id"], db_path=db_path) \
        if task else None
    cwd = (project["primary_path"] if project and project["primary_path"]
           else os.getcwd())
    # Fix #6: an isolated code run executes in its own git worktree (recorded on
    # the run by the dispatcher) so it never writes into another run's tree.
    if run["workdir"]:
        cwd = run["workdir"]

    try:
        with open(brief_file) as f:
            brief_text = f.read()
    except Exception as e:
        _out("failed to read brief %s: %s" % (brief_file, e))
        store.fail_run(run_id, task_id,
                       "wrapper could not read brief: %s" % e,
                       exit_code=None, db_path=db_path)
        return 1

    env = os.environ.copy()
    hbin = os.path.dirname(cfg["hermes"])
    if hbin and hbin not in env.get("PATH", ""):
        env["PATH"] = hbin + os.pathsep + env.get("PATH", "")

    # launch_err is the wrapper's own first-hand account of the failure (could
    # not exec hermes / killed at the 6h timeout). It used to be dropped on the
    # floor; it is now the preferred cause when no completion file exists.
    proc_exit, launch_err = _run_agent(
        cfg["hermes"], agent, brief_text, run_id, cwd, env)

    status = _finalize(run_id, task_id, proc_exit, db_path,
                       review_id=review_id, wrapper_err=launch_err)

    # Promote any dependents whose deps are now all done. A REVIEW run never
    # promotes — only an `wm review ... approved` verdict advances dependents.
    if review_id is None:
        promoted = store.promote_dependents(task_id, db_path=db_path)
        if promoted:
            _out("promoted dependents: %s" % ", ".join(str(p) for p in promoted))

    return 0


if __name__ == "__main__":
    sys.exit(main())