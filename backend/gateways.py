"""Per-profile Hermes gateway control (the HTTP API each chat talks to).

hermes-hq lives inside the Hermes s6 container: every profile already has a
supervised `gateway-<profile>` slot, so this module never spawns gateway
processes itself. It
  * makes sure the profile `.env` has API_SERVER_PORT / API_SERVER_KEY
    (appended, marked `# hermes-hq`, never rewriting the owner's lines),
  * drives s6 through the CLI: `hermes --profile X gateway start|stop`,
  * judges "running" only by a real health probe: GET /v1/models with the key,
  * idle-stops specialist gateways 15 min after their last chat use,
  * stops on serve exit whatever it started (default profile excluded).
The default profile's gateway (:8642) is owned by the container; it is read
(port/key) but never written, started or stopped.
"""
import json
import logging
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request

from core import wm_store as store

log = logging.getLogger("backend.gateways")

ENV_MARK = "# hermes-hq"
BASE_PORT = 8650
PORTS = {name: BASE_PORT + i for i, name in enumerate(store.SPECIALIST_PROFILES)}
DEFAULT_PORT = 8642
IDLE_SECONDS = 15 * 60
START_TIMEOUT = 45.0
STOP_TIMEOUT = 20.0


def _home(name):
    return store.profile_hermes_home(store.resolve_profiles_dir(), name)


def read_env(home):
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


def credentials(name):
    """(port, key) for a profile as its .env says now; (None, None) parts if unset."""
    env = read_env(_home(name))
    port = env.get("API_SERVER_PORT")
    port = int(port) if port and port.isdigit() else (DEFAULT_PORT if name == store.ORCHESTRATOR_AGENT else None)
    return port, env.get("API_SERVER_KEY") or None


def ensure_env(name):
    """Append PORT/KEY to a specialist's .env when missing. Returns (port, key)."""
    if name == store.ORCHESTRATOR_AGENT:
        return credentials(name)
    if name not in PORTS:
        raise ValueError("unknown profile %r" % name)
    home = _home(name)
    if not os.path.isdir(home):
        raise ValueError("profile %r is not installed (%s)" % (name, home))
    env = read_env(home)
    lines = []
    if not env.get("API_SERVER_PORT"):
        lines.append("API_SERVER_PORT=%d  %s" % (PORTS[name], ENV_MARK))
    if not env.get("API_SERVER_KEY"):
        lines.append("API_SERVER_KEY=%s  %s" % (secrets.token_urlsafe(32), ENV_MARK))
    if lines:
        path = os.path.join(home, ".env")
        with open(path, "a") as f:
            if os.path.getsize(path) and not open(path, "rb").read()[-1:] == b"\n":
                f.write("\n")
            f.write("\n".join(lines) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return credentials(name)


def base_url(name):
    port, _ = credentials(name)
    return "http://127.0.0.1:%d" % port if port else None


def healthy(name, timeout=2.0):
    port, key = credentials(name)
    if not port or not key:
        return False
    req = urllib.request.Request("http://127.0.0.1:%d/v1/models" % port,
                                 headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200 and "data" in json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _cli(name, *args, timeout=60):
    hermes = store.resolve_hermes()
    cmd = [hermes]
    if name != store.ORCHESTRATOR_AGENT:
        cmd += ["--profile", name]
    cmd += ["gateway", *args]
    env = dict(os.environ, HERMES_HOME=store.hermes_root_home())
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ValueError("hermes gateway %s for %s failed to run: %s" % (args[0], name, e))
    if r.returncode != 0:
        raise ValueError("hermes gateway %s for %s failed (rc=%s): %s" % (
            args[0], name, r.returncode, (r.stderr or r.stdout or "").strip()[-600:]))
    return (r.stdout or "").strip()


def _wait(name, want, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if healthy(name) is want:
            return True
        time.sleep(0.5)
    return healthy(name) is want


# ---- persisted desired state (wm_meta) -----------------------------------
def _meta(key, name, db_path=None):
    return store.get_meta("gateway_%s:%s" % (key, name), db_path=db_path)


def _set_meta(key, name, value, db_path=None):
    store.append_meta("gateway_%s:%s" % (key, name), str(value), db_path=db_path)


def is_enabled(name, db_path=None):
    if name == store.ORCHESTRATOR_AGENT:
        return True
    return _meta("enabled", name, db_path) == "1"


def touch(name, db_path=None):
    """Record chat use so the idle sweeper leaves the gateway alone for a while."""
    _set_meta("last_used", name, int(time.time()), db_path)


def start(name, db_path=None):
    if name == store.ORCHESTRATOR_AGENT:
        if not healthy(name):
            raise ValueError("default gateway on :%d is not healthy; it is managed by the container, not hermes-hq" % DEFAULT_PORT)
        return credentials(name)
    port, key = ensure_env(name)
    if healthy(name):
        return port, key
    _cli(name, "start")
    if not _wait(name, True, START_TIMEOUT):
        raise ValueError("gateway for %s started but /v1/models on :%d is not healthy after %.0fs" % (name, port, START_TIMEOUT))
    _set_meta("started_by_hq", name, 1, db_path)
    touch(name, db_path)
    store.log_activity(action="gateway_start", agent_profile=name, detail="port %d" % port, db_path=db_path)
    return port, key


def stop(name, reason="owner", db_path=None):
    if name == store.ORCHESTRATOR_AGENT:
        raise ValueError("the default gateway is not stopped by hermes-hq")
    was = healthy(name)
    _cli(name, "stop")
    _wait(name, False, STOP_TIMEOUT)
    _set_meta("started_by_hq", name, 0, db_path)
    if was:
        store.log_activity(action="gateway_stop", agent_profile=name, detail=reason, db_path=db_path)
    return not healthy(name)


def ensure_running(name, db_path=None):
    """For chat: bring the gateway up if the owner enabled it. Returns (port, key)."""
    if name != store.ORCHESTRATOR_AGENT and not is_enabled(name, db_path):
        raise ValueError("chat is not enabled for %s (enable its gateway on the Agents page)" % name)
    port, key = start(name, db_path)
    touch(name, db_path)
    return port, key


def set_enabled(name, enabled, db_path=None):
    if name == store.ORCHESTRATOR_AGENT:
        raise ValueError("the default profile's gateway is always on")
    if name not in PORTS:
        raise ValueError("unknown profile %r" % name)
    _set_meta("enabled", name, "1" if enabled else "0", db_path)
    if enabled:
        start(name, db_path)
    elif healthy(name):
        stop(name, reason="disabled by owner", db_path=db_path)
    return state(name, db_path)


def state(name, db_path=None):
    port, key = credentials(name)
    return {"configured": bool(port and key), "port": port, "enabled": is_enabled(name, db_path),
            "running": healthy(name), "last_used": _meta("last_used", name, db_path)}


# ---- idle sweeper + exit -------------------------------------------------
def idle_sweep(now=None, idle=IDLE_SECONDS, db_path=None):
    """Stop enabled specialist gateways unused for `idle` seconds. Returns names stopped."""
    now = now or time.time()
    stopped = []
    for name in store.SPECIALIST_PROFILES:
        if not is_enabled(name, db_path) or not healthy(name):
            continue
        last = _meta("last_used", name, db_path)
        last = float(last) if last else 0.0
        if now - last >= idle:
            try:
                stop(name, reason="idle %.0f min" % ((now - last) / 60), db_path=db_path)
                stopped.append(name)
            except ValueError as e:
                log.warning("idle stop %s: %s", name, e)
    return stopped


def stop_started(db_path=None):
    """Serve exit: stop the specialist gateways hermes-hq itself brought up."""
    out = []
    for name in store.SPECIALIST_PROFILES:
        if _meta("started_by_hq", name, db_path) == "1" and healthy(name):
            try:
                stop(name, reason="hermes-hq exit", db_path=db_path); out.append(name)
            except ValueError as e:
                log.warning("exit stop %s: %s", name, e)
    return out


class IdleSweeper:
    def __init__(self, interval=60.0, enabled=True):
        self.interval, self.enabled = interval, enabled
        self._stop = threading.Event(); self._thread = None

    def start(self):
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, name="hq-gateway-idle", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                idle_sweep()
            except Exception:
                log.exception("gateway idle sweep failed")
