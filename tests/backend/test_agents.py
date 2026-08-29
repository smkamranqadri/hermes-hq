"""Agents API: templates, installed state, install via (fake) hermes CLI, overlay, fallback."""
import os, stat, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest
from tests.backend.test_writes import login  # noqa: F401

FAKE_HERMES = """#!/bin/sh
# mimics `hermes profile create NAME --no-alias --description D` under $HERMES_HOME
[ "$1" = profile ] && [ "$2" = create ] || { echo "unsupported: $*" >&2; exit 2; }
name=$3; shift 3
desc=""; while [ $# -gt 0 ]; do case "$1" in --description) desc="$2"; shift;; esac; shift; done
d="$HERMES_HOME/profiles/$name"; mkdir -p "$d/skills/bundled" "$d/sessions"
printf 'description: %s\\ndescription_auto: false\\n' "$desc" > "$d/profile.yaml"
echo "You are Hermes Agent (stock)." > "$d/SOUL.md"
echo "# k" > "$d/.env"
echo "created $d"
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "hermes"; (root / "profiles").mkdir(parents=True)
    (root / "SOUL.md").write_text("You are Hermes Agent (stock root).\n")
    shim = tmp_path / "hermes-shim"; shim.write_text(FAKE_HERMES); shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    monkeypatch.setenv("WM_PROFILES_DIR", str(root / "profiles"))
    monkeypatch.setenv("WM_HERMES", str(shim))
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    os.makedirs(tmp_path / "alpha")
    store.create_project("alpha", "Alpha", "", str(tmp_path / "alpha"), db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        yield c, store, root


def test_list_and_templates(env):
    c, store, root = env
    h = login(c)
    d = c.get("/api/agents").json()
    names = {a["name"]: a for a in d["agents"]}
    assert set(names) == set(store.ASSIGNEE_PROFILES)
    assert names["orchestrator"]["installed"] is True and names["orchestrator"]["overlay_applied"] is False
    assert names["coder"]["installed"] is False and names["coder"]["has_template"] is True
    g = names["coder"]["gateway"]; assert (g["configured"], g["port"], g["enabled"], g["running"]) == (False, None, False, False)
    t = {x["name"]: x for x in d["templates"]}
    assert t["orchestrator"]["overlay"] is True and t["coder"]["skills"] == ["coder-specialist"]
    assert c.get("/api/agent/nobody").status_code == 404
    assert c.get("/api/agent/coder").json()["runs"] == []


def test_install_specialist_then_refuse(env):
    c, store, root = env
    h = login(c)
    r = c.post("/api/agents/install", json={"template": "coder"}, headers=h)
    assert r.status_code == 200, r.text
    home = root / "profiles" / "coder"
    assert (home / "profile.yaml").read_text().startswith("description: Software development")
    assert (home / "SOUL.md").read_text().startswith("# Coder")           # template layered over stock
    assert (home / "skills" / "coder-specialist" / "SKILL.md").is_file()
    assert (home / "skills" / "bundled").is_dir()                          # CLI's own skills kept
    assert c.get("/api/agent/coder").json()["installed"] is True
    r = c.post("/api/agents/install", json={"template": "coder"}, headers=h)
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    assert c.post("/api/agents/install", json={"template": "ghost"}, headers=h).status_code == 409
    assert c.post("/api/agents/install", json={"template": "../etc"}, headers=h).status_code == 409


def test_install_orchestrator_overlay_with_backup(env):
    c, store, root = env
    h = login(c)
    r = c.post("/api/agents/install", json={"template": "orchestrator"}, headers=h)
    assert r.status_code == 200, r.text
    assert (root / "SOUL.md").read_text().startswith("# Orchestrator")
    bak = r.json()["backup"]; assert bak and open(bak).read().startswith("You are Hermes Agent (stock root)")
    assert c.get("/api/agents").json()["agents"][0]["overlay_applied"] is True
    assert c.post("/api/agents/install", json={"template": "orchestrator"}, headers=h).status_code == 409
    assert c.post("/api/agents/install", json={"template": "orchestrator", "force": True}, headers=h).status_code == 200


def test_ask_orchestrator_files_task(env):
    c, store, root = env
    h = login(c)
    r = c.post("/api/agents/ask-orchestrator", json={"template": "writer", "project": "alpha"}, headers=h)
    assert r.status_code == 200, r.text
    t = c.get("/api/task/%d" % r.json()["task_id"]).json()
    assert t["assignee_profile"] == "orchestrator" and "writer" in t["title"] and "hermes profile create writer" in t["description"]


def test_cli_failure_is_a_refusal(env, monkeypatch):
    c, store, root = env
    h = login(c)
    monkeypatch.setenv("WM_HERMES", "/bin/false")
    r = c.post("/api/agents/install", json={"template": "uiux"}, headers=h)
    assert r.status_code == 409 and "failed" in r.json()["detail"]
    assert not (root / "profiles" / "uiux").exists()
