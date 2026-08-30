"""Group 4b-3: message parts (images), per-turn model options, steer."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_chat import env, FakeGateway, login, _events  # noqa: F401

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def test_parts_and_model_options_reach_the_gateway(env):
    c, store, gw, root = env
    h = login(c)
    r = c.post("/api/chat/orchestrator/api_1_abc", json={"message": [{"type": "text", "text": "what colour?"}, {"type": "image_url", "image_url": {"url": PNG}}],
                                                        "model": "gpt-5.6-luna", "provider": "openai", "effort": "high", "fast": True}, headers=h)
    assert r.status_code == 200
    ev = dict(_events(r.text))
    assert ev["assistant.completed"]["content"] == "Hello " + "what colour? [img]"[::-1]
    path, body = FakeGateway.calls[-1]
    assert path == "/api/sessions/api_1_abc/chat/stream"
    assert body["message"] == [{"type": "text", "text": "what colour?"}, {"type": "image_url", "image_url": {"url": PNG}}]
    assert body["model"] == "gpt-5.6-luna" and body["provider"] == "openai" and body["model_options"] == {"reasoning_effort": "high", "fast": True}
    # plain text still plain, no options keys when none given; the hq-options hint rides along as an ephemeral system message
    c.post("/api/chat/orchestrator/api_1_abc", json={"message": "hi"}, headers=h)
    body = FakeGateway.calls[-1][1]
    assert body["message"] == "hi" and set(body) == {"message", "system_message"} and "hq-options" in body["system_message"]
    # activity preview mentions the image
    con = store._connect(store.DEFAULT_DB_PATH)
    try:
        details = [r[0] for r in con.execute("SELECT detail FROM activity WHERE action='chat_message'")]
    finally:
        con.close()
    assert any("[1 image]" in (d or "") for d in details)


def test_bad_parts_and_effort_are_409_before_bytes(env):
    c, store, gw, root = env
    h = login(c)
    n = len(FakeGateway.calls)
    assert c.post("/api/chat/orchestrator/api_1_abc", json={"message": [{"type": "image_url", "image_url": {"url": "https://x/y.png"}}]}, headers=h).status_code == 409
    assert c.post("/api/chat/orchestrator/api_1_abc", json={"message": [{"type": "file", "data": "x"}]}, headers=h).status_code == 409
    assert c.post("/api/chat/orchestrator/api_1_abc", json={"message": [{"type": "text", "text": "  "}]}, headers=h).status_code == 409
    assert c.post("/api/chat/orchestrator/api_1_abc", json={"message": "hi", "effort": "turbo"}, headers=h).status_code == 409
    big = [{"type": "image_url", "image_url": {"url": PNG}}] * 5
    assert c.post("/api/chat/orchestrator/api_1_abc", json={"message": big}, headers=h).status_code == 409
    assert len(FakeGateway.calls) == n


def test_steer_passthrough_and_models_list(env):
    c, store, gw, root = env
    h = login(c)
    r = c.post("/api/chat/orchestrator/api_1_abc/steer/run_9", json={"message": "focus on tests"}, headers=h)
    assert r.status_code == 200 and r.json()["steer"] == "accepted"
    assert FakeGateway.calls[-1] == ("/v1/runs/run_9/steer", {"message": "focus on tests"})
    assert c.post("/api/chat/orchestrator/api_1_abc/steer/run_done", json={"message": "x"}, headers=h).status_code == 502
    assert c.post("/api/chat/orchestrator/api_1_abc/steer/run_9", json={"message": " "}, headers=h).status_code == 409
    import json as _j
    os.environ["HERMES_HOME"] = str(root)   # providers are read from <HERMES_HOME>/auth.json, not the live box
    m = c.get("/api/chat/models?q=luna").json()
    assert "high" in m["efforts"] and m["models"] == [] and m["providers"] == [] and m["provider"] is None
    # providers come from Hermes auth.json / config.yaml, per profile; models from Hermes' own caches
    (root / "auth.json").write_text(_j.dumps({"providers": {"openai-codex": {}}, "credential_pool": {"nous": {}}, "active_provider": "openai-codex"}))
    (root / "profiles" / "coder" / "auth.json").write_text(_j.dumps({"credential_pool": {"opencode-go": {}}}))
    (root / "profiles" / "coder" / "config.yaml").write_text("model:\n  default: x\n  provider: copilot\n")
    ids = [p["id"] for p in c.get("/api/chat/models?profile=orchestrator").json()["providers"]]
    assert ids == ["openai-codex", "nous"]
    ids = [p["id"] for p in c.get("/api/chat/models?profile=coder").json()["providers"]]
    assert ids == ["opencode-go", "copilot", "openai-codex", "nous"]
    (root / "provider_models_cache.json").write_text(_j.dumps({"openai-codex": {"models": ["gpt-5.6-luna", "gpt-5.5"]}}))
    (root / "cache").mkdir(exist_ok=True)
    (root / "cache" / "model_catalog.json").write_text(_j.dumps({"providers": {"openai-codex": {"models": [{"id": "gpt-5.5", "description": "older"}, {"id": "gpt-5.3-codex", "description": "code"}]}, "nous": {"models": [{"id": "hermes-4", "description": ""}]}}}))
    (root / "profiles" / "coder" / "provider_models_cache.json").write_text(_j.dumps({"opencode-go": {"models": ["kimi-k3"]}}))
    m = c.get("/api/chat/models?profile=orchestrator").json()          # no provider given → the active one
    assert m["provider"] == "openai-codex" and [x["id"] for x in m["models"]] == ["gpt-5.6-luna", "gpt-5.5", "gpt-5.3-codex"]
    assert m["models"][2]["description"] == "code"
    assert [x["id"] for x in c.get("/api/chat/models?profile=orchestrator&provider=nous").json()["models"]] == ["hermes-4"]
    assert [x["id"] for x in c.get("/api/chat/models?profile=coder&provider=opencode-go").json()["models"]] == ["kimi-k3"]
    assert [x["id"] for x in c.get("/api/chat/models?profile=orchestrator&provider=openai-codex&q=codex").json()["models"]] == ["gpt-5.3-codex"]
