"""Group 4b-2: rename/pin/delete via the gateway, export, hq-side search."""
import os, sqlite3, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_chat import env, FakeGateway, login  # noqa: F401


def _seed_state_db(root, profile, sid, title, msgs):
    """Minimal Hermes state.db for a profile with one session + messages."""
    db = root / "profiles" / profile / "state.db"
    con = sqlite3.connect(db)
    con.executescript("""
      CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, title TEXT, title_source TEXT, started_at REAL, ended_at REAL,
        end_reason TEXT, message_count INTEGER, tool_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
        cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cwd TEXT,
        git_branch TEXT, last_activity_at REAL, profile_name TEXT, archived INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0, api_call_count INTEGER);
      CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL,
        token_count INTEGER, display_kind TEXT, active INTEGER, reasoning TEXT, reasoning_content TEXT);
      CREATE TABLE session_model_usage (session_id TEXT, model TEXT, task TEXT, api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER,
        cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT,
        first_seen REAL, last_seen REAL);
    """)
    now = time.time()
    con.execute("INSERT INTO sessions (id, source, model, title, started_at, last_activity_at, profile_name, api_call_count, input_tokens, output_tokens) VALUES (?,?,?,?,?,?,?,1,100,20)",
                (sid, "api_server", "gpt-5.6-luna", title, now - 60, now, profile))
    for i, (role, content, tool) in enumerate(msgs):
        con.execute("INSERT INTO messages (session_id, role, content, tool_name, timestamp, active, tool_calls) VALUES (?,?,?,?,?,1,?)",
                    (sid, role, content, tool, now - 50 + i, '[{"function":{"name":"ls","arguments":"{}"}}]' if role == "assistant" and i == 1 else None))
    con.commit(); con.close()


def test_rename_pin_delete_round_trip(env):
    c, store, gw, root = env
    h = login(c)
    store.create_project("demo", "Demo", "", "/tmp/demo", db_path=store.DEFAULT_DB_PATH)
    store.link_chat_session("orchestrator", "api_1_abc", project_id=1, title="Project: Demo", db_path=store.DEFAULT_DB_PATH)
    r = c.post("/api/chat/orchestrator/api_1_abc/update", json={"title": "  Brand plan  ", "pinned": True}, headers=h)
    assert r.status_code == 200 and r.json() == {"id": "api_1_abc", "title": "Brand plan", "pinned": True}
    assert FakeGateway.calls[-1] == ("PATCH /api/sessions/api_1_abc", {"title": "Brand plan", "pinned": True})
    assert store.chat_sessions_for_project(1, db_path=store.DEFAULT_DB_PATH)[0]["title"] == "Brand plan"
    assert c.post("/api/chat/orchestrator/api_1_abc/update", json={}, headers=h).status_code == 409
    assert c.post("/api/chat/orchestrator/api_1_abc/update", json={"title": "  "}, headers=h).status_code == 409
    r = c.post("/api/chat/orchestrator/api_1_abc/delete", headers=h)
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert FakeGateway.calls[-1][0] == "DELETE /api/sessions/api_1_abc"
    assert store.chat_sessions_for_project(1, db_path=store.DEFAULT_DB_PATH) == []
    # disabled specialist: 409, nothing sent
    n = len(FakeGateway.calls)
    assert c.post("/api/chat/coder/api_1_abc/delete", headers=h).status_code == 409 and len(FakeGateway.calls) == n


def test_export_and_search_from_state_db(env):
    c, store, gw, root = env
    h = login(c)
    _seed_state_db(root, "coder", "s1", "Refactor login", [("user", "Please refactor the login flow", None), ("assistant", "", None),
                                                          ("tool", "{\"files\": []}", "ls"), ("assistant", "Done — the login flow now uses PKCE.", None)])
    md = c.get("/api/session/coder/s1/export.md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "# Refactor login" in md.text and "## You" in md.text and "**tool → ls**" in md.text and "PKCE" in md.text
    assert c.get("/api/session/coder/nope/export.md").status_code == 404
    r = c.get("/api/chat/search?q=pkce")
    assert r.status_code == 200
    res = r.json()["results"]
    assert len(res) == 1 and res[0]["profile"] == "coder" and res[0]["id"] == "s1" and "PKCE" in res[0]["snippet"] and res[0]["hits"] == 1
    assert c.get("/api/chat/search?q=refactor").json()["results"][0]["title"] == "Refactor login"
    assert c.get("/api/chat/search?q=zzz-nothing").json()["results"] == []
    assert c.get("/api/chat/search?q=x").json()["results"] == []   # too short
    # pinned-first ordering in the list
    assert c.get("/api/agent/coder/sessions").json()["sessions"][0]["pinned"] == 0
