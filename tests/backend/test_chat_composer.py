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
    # plain text still plain, no options keys when none given
    c.post("/api/chat/orchestrator/api_1_abc", json={"message": "hi"}, headers=h)
    assert FakeGateway.calls[-1][1] == {"message": "hi"}
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
    m = c.get("/api/chat/models?q=luna").json()
    assert "high" in m["efforts"] and isinstance(m["models"], list) and isinstance(m["providers"], list)
