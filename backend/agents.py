"""Agents: installed Hermes profiles, repo templates, and installing one.

Templates live in <repo>/agents/<name>/ (see scripts/extract_agent_templates.py).
A specialist template is installed with the REAL `hermes profile create` (so the
profile gets Hermes' own layout, .env and bundled skills) and then layered with
the template's SOUL.md + specialist skill. The `orchestrator` template is an
OVERLAY: Hermes' default profile already exists, so "installing" it means
writing the template SOUL.md over <root>/SOUL.md (previous file backed up).
"""
import os
import re
import shutil
import subprocess
import time

import yaml

from backend import readers
from core import wm_store as store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY_MARKER = "# Orchestrator"
ENV_MARK = "# hermes-hq"


def templates_dir():
    return os.environ.get("HERMES_HQ_TEMPLATES") or os.path.join(REPO, "agents")


def _template_path(name):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name or ""):
        raise ValueError("invalid template name %r" % name)
    p = os.path.join(templates_dir(), name)
    if not os.path.isfile(os.path.join(p, "agent.yaml")):
        raise ValueError("no template %r" % name)
    return p


def load_template(name):
    p = _template_path(name)
    with open(os.path.join(p, "agent.yaml")) as f:
        meta = yaml.safe_load(f) or {}
    meta.setdefault("skills", []); meta.setdefault("overlay", False); meta.setdefault("soul", "SOUL.md")
    meta["path"] = p
    with open(os.path.join(p, meta["soul"])) as f:
        meta["soul_text"] = f.read()
    return meta


def list_templates():
    out = []
    d = templates_dir()
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        try:
            m = load_template(name)
        except ValueError:
            continue
        out.append({"name": name, "description": m.get("description", ""), "overlay": bool(m["overlay"]),
                    "skills": list(m["skills"]), "installed": is_installed(name)})
    return out


# ---- installed state ----------------------------------------------------
def profile_home(name):
    return store.profile_hermes_home(store.resolve_profiles_dir(), name)


def is_installed(name):
    home = profile_home(name)
    if name == store.ORCHESTRATOR_AGENT:
        return os.path.isdir(home)
    return os.path.isfile(os.path.join(home, "profile.yaml"))


def overlay_applied(name=store.ORCHESTRATOR_AGENT):
    p = os.path.join(profile_home(name), "SOUL.md")
    try:
        with open(p) as f:
            return f.read(4096).lstrip().startswith(OVERLAY_MARKER)
    except OSError:
        return False


def read_env(home):
    """Parse KEY=VALUE lines of a profile .env (values unquoted, comments kept out)."""
    env = {}
    try:
        with open(os.path.join(home, ".env")) as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def gateway_state(name):
    """What the profile's .env says about its HTTP API gateway. `running` is
    filled by the supervisor (backend.gateways) once it exists; until then None."""
    env = read_env(profile_home(name))
    port = env.get("API_SERVER_PORT")
    return {"configured": bool(port and env.get("API_SERVER_KEY")), "port": int(port) if port and port.isdigit() else None,
            "running": None}


def list_agents(db_path=None):
    db_path = db_path or store.DEFAULT_DB_PATH
    profiles_dir = store.resolve_profiles_dir()
    base = {a["name"]: a for a in readers.list_agents(db_path, profiles_dir)}
    out = []
    for name in store.ASSIGNEE_PROFILES:
        a = dict(base.get(name, {"name": name}))
        home = profile_home(name)
        a.update({"installed": is_installed(name), "home": home,
                  "description": _profile_description(home, name),
                  "has_template": os.path.isfile(os.path.join(templates_dir(), name, "agent.yaml")),
                  "gateway": gateway_state(name)})
        if name == store.ORCHESTRATOR_AGENT:
            a["overlay_applied"] = overlay_applied(name)
        out.append(a)
    return out


def _profile_description(home, name):
    try:
        with open(os.path.join(home, "profile.yaml")) as f:
            return (yaml.safe_load(f) or {}).get("description", "") or ""
    except OSError:
        if name == store.ORCHESTRATOR_AGENT:
            return "Hermes default profile"
        return ""


def agent_detail(name, db_path=None):
    if name not in store.ASSIGNEE_PROFILES:
        raise ValueError("unknown agent %r" % name)
    a = next(x for x in list_agents(db_path) if x["name"] == name)
    conn = store._connect(db_path or store.DEFAULT_DB_PATH)
    try:
        a["runs"] = [dict(r) for r in conn.execute(
            "SELECT r.id, r.task_id, r.status, r.started_at, r.finished_at, r.error, r.session_id, t.title AS task_title "
            "FROM runs r LEFT JOIN tasks t ON t.id=r.task_id WHERE r.agent_profile=? ORDER BY r.id DESC LIMIT 50", (name,))]
    finally:
        conn.close()
    try:
        a["sessions"] = readers.agent_sessions(store.resolve_profiles_dir(), name, limit=50)
    except (ValueError, FileNotFoundError):
        a["sessions"] = []
    return a


# ---- install ------------------------------------------------------------
def _copy_template_into(meta, home):
    with open(os.path.join(home, "SOUL.md"), "w") as f:
        f.write(meta["soul_text"])
    for sk in meta["skills"]:
        src = os.path.join(meta["path"], "skills", sk)
        dst = os.path.join(home, "skills", sk)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def install(template, hermes=None, force=False, db_path=None, timeout=120):
    """Install a template. Returns a dict describing what happened.
    Raises ValueError on refusal (already installed, bad template, CLI failure)."""
    meta = load_template(template)
    name = meta["name"]
    home = profile_home(name)
    if meta["overlay"]:
        if name != store.ORCHESTRATOR_AGENT:
            raise ValueError("only the orchestrator template is an overlay")
        if not os.path.isdir(home):
            raise ValueError("default profile home %s does not exist" % home)
        if overlay_applied(name) and not force:
            raise ValueError("orchestrator overlay already applied to %s/SOUL.md" % home)
        soul = os.path.join(home, "SOUL.md")
        backup = None
        if os.path.isfile(soul):
            backup = soul + ".bak-%d" % int(time.time())
            shutil.copyfile(soul, backup)
        _copy_template_into(meta, home)
        store.log_activity(action="agent_overlay", agent_profile=name,
                           detail="orchestrator SOUL applied to %s (backup %s)" % (home, backup), db_path=db_path)
        return {"name": name, "overlay": True, "home": home, "backup": backup}

    if name not in store.SPECIALIST_PROFILES:
        raise ValueError("template %r is not a known specialist profile" % name)
    if is_installed(name) and not force:
        raise ValueError("profile %r already exists at %s" % (name, home))
    hermes = hermes or store.resolve_hermes()
    env = dict(os.environ, HERMES_HOME=store.hermes_root_home())
    cmd = [hermes, "profile", "create", name, "--no-alias", "--description", meta.get("description", "") or name]
    if not is_installed(name):
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ValueError("hermes profile create failed to run: %s" % e)
        if r.returncode != 0 or not is_installed(name):
            raise ValueError("hermes profile create failed (rc=%s): %s" % (
                r.returncode, (r.stderr or r.stdout or "").strip()[-800:]))
    _copy_template_into(meta, home)
    ok = os.path.isfile(os.path.join(home, "SOUL.md")) and all(
        os.path.isfile(os.path.join(home, "skills", sk, "SKILL.md")) for sk in meta["skills"])
    if not ok:
        raise ValueError("profile created but template files missing under %s" % home)
    store.log_activity(action="agent_install", agent_profile=name,
                       detail="installed from template into %s" % home, db_path=db_path)
    return {"name": name, "overlay": False, "home": home, "skills": list(meta["skills"]), "cmd": " ".join(cmd)}


def ask_orchestrator(template, project, db_path=None):
    """Fallback when the CLI path is unavailable: file a task for the Orchestrator
    to install the profile by hand from the template directory."""
    meta = load_template(template)
    brief = ("Install the Hermes profile '%s' from the hermes-hq template at %s.\n"
             "Run `hermes profile create %s --no-alias --description %r`, then copy SOUL.md and "
             "skills/* from the template into the new profile. Report the resulting path."
             % (meta["name"], meta["path"], meta["name"], meta.get("description", "")))
    tid = store.create_task(project, "Install agent profile: %s" % meta["name"], brief,
                            "Profile exists under profiles/%s with template SOUL.md and skills." % meta["name"],
                            assignee_profile=store.ORCHESTRATOR_AGENT, db_path=db_path)
    return {"task_id": tid}
