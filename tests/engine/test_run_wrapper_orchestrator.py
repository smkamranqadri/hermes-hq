#!/usr/bin/env python3
"""test_run_wrapper_orchestrator.py — the orchestrator launch invariant.

The original failed-run root cause: an `orchestrator`-assigned task (the
internal `Plan goal #N` task) was launched with `hermes --profile orchestrator`
— no such profile exists — so the run died at launch.

The FIRST fix (6.5.2) dropped `--profile` entirely for the orchestrator. That
made the command launchable but not correct: `hermes` without `--profile` does
NOT mean "the default profile". `hermes_cli.main._apply_profile_override()`
trusts an already-set profile-shaped `HERMES_HOME` (one whose parent directory
is named `profiles`), and otherwise follows the sticky `<root>/active_profile`.
A dispatch launched from inside a specialist's session therefore inherited e.g.
`HERMES_HOME=/opt/data/profiles/coder` and the orchestrator run silently
executed AS THE CODER — writing its session into a state.db that the capture
and liveness probes (which read the ROOT store) never look at.

So the invariant this file pins is not "no --profile". It is:

  1. Every run names its profile EXPLICITLY. The orchestrator's profile is
     Hermes' root profile, spelled `default` — never the nonexistent
     `orchestrator`. Specialists are unchanged: `--profile <agent>`.
  2. An orchestrator run's HERMES_HOME is pinned to the ROOT Hermes home, from
     the SAME canonical mapping that decides where its sessions are read from
     (store.profile_state_db) — so launch and capture cannot disagree. A
     specialist's environment is left untouched.
  3. That mapping is symlink-stable and consistent between the orchestrator's
     home and its state.db.

Portability (L2): every assertion derives its expected path from a temp
profiles dir, EXCEPT one production-mapping check that pins the real
/opt/data/profiles -> /opt/data/state.db contract using a controlled temp tree
shaped like production rather than a hard-coded literal.

Re-runnable: python3 test_run_wrapper_orchestrator.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hermes_hq", "engine"); sys.path.insert(0, ENGINE)
import wm_store as store            # noqa: E402
import wm_run_agent as wra          # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append((name, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" — " + detail) if detail else ""))


TMP = os.path.realpath(tempfile.mkdtemp(prefix="wm_run_orb_"))
RUNDIR = os.path.join(TMP, "runs")
os.makedirs(RUNDIR, exist_ok=True)
# A production-SHAPED tree: <root>/profiles/<specialist> beside <root>/state.db.
ROOT = os.path.join(TMP, "root")
PRO = os.path.join(ROOT, "profiles")
os.makedirs(os.path.join(PRO, "coder"), exist_ok=True)
os.environ["WM_RUNS_DIR"] = RUNDIR
os.environ["WM_PROFILES_DIR"] = PRO

ORCH = store.ORCHESTRATOR_AGENT
check("store exposes the reserved ORCHESTRATOR_AGENT constant",
      ORCH == "orchestrator", "got=%r" % ORCH)
check("store exposes Hermes' name for the root profile",
      store.DEFAULT_PROFILE == "default", "got=%r" % store.DEFAULT_PROFILE)

# ---- 1. profile argument ------------------------------------------------------
print("\n== 1. the orchestrator's Hermes profile is `default`, not `orchestrator` ==")
check("hermes_profile_arg('orchestrator') is the DEFAULT profile",
      store.hermes_profile_arg(ORCH) == store.DEFAULT_PROFILE,
      "got=%r" % store.hermes_profile_arg(ORCH))
check("hermes_profile_arg never yields the nonexistent 'orchestrator' profile",
      store.hermes_profile_arg(ORCH) != ORCH)
check("hermes_profile_arg leaves a specialist as its own profile",
      store.hermes_profile_arg("coder") == "coder",
      "got=%r" % store.hermes_profile_arg("coder"))

# ---- 2. home + session-store mapping -----------------------------------------
print("\n== 2. orchestrator home/sessions resolve to the ROOT, from ONE mapping ==")
check("orchestrator HERMES_HOME is the ROOT home (parent of the profiles dir)",
      store.agent_hermes_home(ORCH) == ROOT,
      "got=%s want=%s" % (store.agent_hermes_home(ORCH), ROOT))
check("hermes_root_home() agrees with agent_hermes_home('orchestrator')",
      store.hermes_root_home() == store.agent_hermes_home(ORCH),
      "root=%s agent=%s" % (store.hermes_root_home(),
                            store.agent_hermes_home(ORCH)))
check("orchestrator sessions live in <root>/state.db",
      store.agent_session_db_path(ORCH) == os.path.join(ROOT, "state.db"),
      "got=%s" % store.agent_session_db_path(ORCH))
check("orchestrator session store is NOT profiles/orchestrator/state.db",
      ORCH not in store.agent_session_db_path(ORCH).split(os.sep),
      "got=%s" % store.agent_session_db_path(ORCH))
check("session db is exactly <hermes home>/state.db for the orchestrator",
      store.agent_session_db_path(ORCH)
      == os.path.join(store.agent_hermes_home(ORCH), "state.db"))
check("specialist HERMES_HOME is its own profile dir",
      store.agent_hermes_home("coder") == os.path.join(PRO, "coder"),
      "got=%s" % store.agent_hermes_home("coder"))
check("specialist sessions stay in profiles/<agent>/state.db",
      store.agent_session_db_path("coder")
      == os.path.join(PRO, "coder", "state.db"),
      "got=%s" % store.agent_session_db_path("coder"))

# The mapping must be symlink-stable: a profiles dir reached through a symlink
# must resolve to the SAME files as the real path, or the launcher and the
# dashboard's liveness probe end up reading different databases (M2).
_link = os.path.join(TMP, "profiles-link")
if not os.path.islink(_link):
    os.symlink(PRO, _link)
check("profile_state_db is symlink-stable for the orchestrator",
      store.profile_state_db(_link, ORCH) == store.profile_state_db(PRO, ORCH),
      "link=%s real=%s" % (store.profile_state_db(_link, ORCH),
                           store.profile_state_db(PRO, ORCH)))
check("profile_state_db is symlink-stable for a specialist",
      store.profile_state_db(_link, "coder")
      == store.profile_state_db(PRO, "coder"))

# Production mapping, pinned WITHOUT hard-coding /opt/data: build a controlled
# temp tree with the same shape as the deployment (<root>/profiles/<agent>) and
# assert the contract "profiles dir's parent holds the default state.db".
print("\n== 2b. production-shaped mapping (controlled temp tree) ==")
_prod_root = os.path.join(TMP, "opt-data")
_prod_pro = os.path.join(_prod_root, "profiles")
os.makedirs(os.path.join(_prod_pro, "reviewer"), exist_ok=True)
check("<root>/profiles -> orchestrator store is <root>/state.db",
      store.profile_state_db(_prod_pro, ORCH)
      == os.path.join(_prod_root, "state.db"),
      "got=%s" % store.profile_state_db(_prod_pro, ORCH))
check("<root>/profiles -> specialist store is <root>/profiles/<a>/state.db",
      store.profile_state_db(_prod_pro, "reviewer")
      == os.path.join(_prod_pro, "reviewer", "state.db"))

# ---- 3. command building ------------------------------------------------------
print("\n== 3. _run_agent command building ==")
_hermes = "/fake/hermes"
run_id = 42
_captured = {}


class _FakePopen:
    def __init__(self, cmd, **kw):
        _captured["cmd"] = list(cmd)
        _captured["cwd"] = kw.get("cwd")
        _captured["env"] = kw.get("env")

    def communicate(self, input=None, timeout=None):
        return (b"", b"")

    @property
    def returncode(self):
        return 0


_real_popen = subprocess.Popen
# A caller environment that has ALREADY been captured by a specialist profile —
# exactly the state that made a bare `hermes chat` run as the coder.
_env = {"PATH": "/bin", "HERMES_HOME": os.path.join(PRO, "coder")}
# The wrapper's `_out` sink reads the run id from argv[1] (real invocation:
# `wm_run_agent.py <run_id> <agent> <brief>`), so seed argv like a real run.
_saved_argv = list(sys.argv)
sys.argv = [sys.argv[0], str(run_id), ORCH, "brief_path"]
try:
    subprocess.Popen = _FakePopen
    wra._run_agent(_hermes, ORCH, "brief", run_id, cwd="/tmp", env=_env)
    _ocmd = _captured["cmd"]
    _oenv = _captured["env"]
    check("orchestrator command names its profile EXPLICITLY",
          "--profile" in _ocmd, "cmd=%s" % _ocmd)
    check("orchestrator command uses --profile default",
          _ocmd[_ocmd.index("--profile") + 1] == store.DEFAULT_PROFILE,
          "cmd=%s" % _ocmd)
    check("orchestrator command never passes the nonexistent 'orchestrator' profile",
          ORCH not in _ocmd, "cmd=%s" % _ocmd)
    check("orchestrator command is `hermes chat` reading the brief on stdin",
          _ocmd[0] == _hermes and "chat" in _ocmd
          and "-Q" in _ocmd and "--query-file" in _ocmd, "cmd=%s" % _ocmd)
    check("orchestrator command carries the marker + session flags",
          "-c" in _ocmd and "wm-run-42" in _ocmd
          and "--create-if-missing" in _ocmd and "--pass-session-id" in _ocmd,
          "cmd=%s" % _ocmd)

    print("\n== 4. environment pinning ==")
    check("orchestrator run OVERRIDES an inherited specialist HERMES_HOME",
          _oenv.get("HERMES_HOME") == ROOT,
          "got=%r want=%r" % (_oenv.get("HERMES_HOME"), ROOT))
    check("pinned HERMES_HOME comes from the same mapping as the session store",
          os.path.join(_oenv.get("HERMES_HOME", ""), "state.db")
          == store.agent_session_db_path(ORCH),
          "home=%r sessions=%s" % (_oenv.get("HERMES_HOME"),
                                   store.agent_session_db_path(ORCH)))
    check("pinning does not mutate the caller's env dict",
          _env["HERMES_HOME"] == os.path.join(PRO, "coder"),
          "caller env=%r" % _env["HERMES_HOME"])
    check("orchestrator run keeps the rest of the environment (PATH)",
          _oenv.get("PATH") == "/bin", "PATH=%r" % _oenv.get("PATH"))

    print("\n== 5. specialist launch is UNCHANGED ==")
    _captured.clear()
    wra._run_agent(_hermes, "coder", "brief", run_id, cwd="/tmp", env=_env)
    _ccmd = _captured["cmd"]
    _cenv = _captured["env"]
    check("specialist agent command KEEPS --profile <agent>",
          "--profile" in _ccmd
          and _ccmd[_ccmd.index("--profile") + 1] == "coder", "cmd=%s" % _ccmd)
    check("specialist agent command still passes stdin + marker + session flags",
          "chat" in _ccmd and "--query-file" in _ccmd and "wm-run-42" in _ccmd
          and "--pass-session-id" in _ccmd, "cmd=%s" % _ccmd)
    check("specialist environment is NOT re-pinned (--profile decides its home)",
          _cenv.get("HERMES_HOME") == _env["HERMES_HOME"],
          "got=%r" % _cenv.get("HERMES_HOME"))
finally:
    subprocess.Popen = _real_popen
    sys.argv = _saved_argv

# ---- 6. resume command --------------------------------------------------------
print("\n== 6. resume command targets a profile that actually exists ==")
_r = store.get_resume_command(ORCH, "sess-orch-1")
check("orchestrator resume uses --profile default",
      _r == "hermes --profile default --resume sess-orch-1", "got=%r" % _r)
check("orchestrator resume never emits --profile orchestrator",
      "--profile orchestrator" not in (_r or ""), "got=%r" % _r)
_rc = store.get_resume_command("coder", "sess-coder-1")
check("specialist resume is unchanged",
      _rc == "hermes --profile coder --resume sess-coder-1", "got=%r" % _rc)
check("resume is None without a session id",
      store.get_resume_command(ORCH, None) is None)
check("resume is None without an agent (no `None` token in a pasteable command)",
      store.get_resume_command(None, "sess-x") is None,
      "got=%r" % store.get_resume_command(None, "sess-x"))

# ---- 7. the installed Hermes CLI actually accepts this form -------------------
# Confirms the invariant against the REAL launcher when one is installed: an
# explicit `--profile default` must resolve to the root home EVEN WHEN the
# environment already points at a specialist profile. Skipped (not failed) when
# no hermes binary is present, so the suite stays runnable off-box.
print("\n== 7. installed Hermes CLI accepts --profile default ==")
_hbin = store.resolve_hermes()
if not os.path.exists(_hbin):
    print("[SKIP] no hermes binary at %s — CLI confirmation skipped" % _hbin)
else:
    # Drop the temp override so this probe uses the REAL deployment paths.
    _saved = os.environ.pop("WM_PROFILES_DIR", None)
    try:
        _probe_env = dict(os.environ)
        # Poison the environment the way a specialist dispatch would.
        _probe_env["HERMES_HOME"] = store.agent_hermes_home("coder")
        try:
            _p = subprocess.run(
                [_hbin, "--profile", store.DEFAULT_PROFILE, "profile", "list"],
                env=_probe_env, capture_output=True, timeout=120)
            _ok = _p.returncode == 0
            check("`hermes --profile default profile list` exits 0 with a "
                  "specialist HERMES_HOME set", _ok,
                  "rc=%s err=%s" % (_p.returncode,
                                    _p.stderr.decode("utf-8", "replace")[:200]))
            check("the installed CLI does NOT know a profile named 'orchestrator'",
                  _ok and ("\n  orchestrator" not in
                           _p.stdout.decode("utf-8", "replace")),
                  "stdout had an 'orchestrator' profile row")
        except (OSError, subprocess.TimeoutExpired) as e:
            print("[SKIP] could not execute %s (%s)" % (_hbin, e))
    finally:
        if _saved is not None:
            os.environ["WM_PROFILES_DIR"] = _saved

print()
print("passed=%d failed=%d total=%d" % (len(PASS), len(FAIL), len(PASS) + len(FAIL)))
sys.exit(1 if FAIL else 0)
