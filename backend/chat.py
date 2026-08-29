"""Chat proxy: hermes-hq <-> a profile's Hermes gateway session API.

Thin by design. The browser never sees a gateway key; hermes-hq injects it.
Streaming turns are forwarded byte-for-byte as SSE with the gateway's own
event names (`assistant.delta`, `tool.started`, ..., `done`), so the UI renders
what the gateway says, not a second schema. History is read from the profile's
state.db (backend.readers), which works with the gateway off and sees sessions
created by dispatched runs (same SessionDB).
"""
import http.client
import json
import logging
import re
import socket
import time

from backend import gateways, readers
from core import wm_store as store

log = logging.getLogger("backend.chat")

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
CONNECT_TIMEOUT = 10.0
TURN_TIMEOUT = 60 * 60.0   # a coder turn can run long; the socket read timeout


class GatewayError(Exception):
    """The gateway answered with an error (or not at all) — surfaces as 502."""


def _check_profile(profile):
    if profile not in store.ASSIGNEE_PROFILES:
        raise ValueError("unknown profile %r" % profile)


def _check_session(session_id):
    if not SESSION_ID_RE.match(session_id or ""):
        raise ValueError("invalid session id")


def _conn(port):
    return http.client.HTTPConnection("127.0.0.1", port, timeout=CONNECT_TIMEOUT)


def _gateway_json(port, key, method, path, body=None, retry_for=0.0):
    """One JSON call to the gateway. With retry_for>0, connection errors and
    503 (gateway still warming up right after start) are retried for that long."""
    deadline = time.time() + retry_for
    while True:
        try:
            return _gateway_json_once(port, key, method, path, body)
        except GatewayError as e:
            if time.time() >= deadline or not getattr(e, "transient", False):
                raise
            time.sleep(0.5)


def _gateway_json_once(port, key, method, path, body=None):
    c = _conn(port)
    try:
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        r = c.getresponse()
        raw = r.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as e:
        err = GatewayError("gateway unreachable: %s" % e); err.transient = True
        raise err
    finally:
        c.close()
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {"raw": raw[:500]}
    if r.status >= 400:
        msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
        err = GatewayError("gateway %s %s -> %d: %s" % (method, path, r.status, msg or raw[:300]))
        err.transient = r.status in (502, 503, 504)
        raise err
    return data


# ---- history (state.db, read-only) ----------------------------------------
def sessions(profile, limit=100):
    _check_profile(profile)
    try:
        return readers.agent_sessions(store.resolve_profiles_dir(), profile, limit=limit)
    except (ValueError, FileNotFoundError):
        return []


def transcript(profile, session_id, limit=400):
    _check_profile(profile); _check_session(session_id)
    try:
        return readers.session_detail(store.resolve_profiles_dir(), profile, session_id, transcript=True, limit=limit)
    except (ValueError, FileNotFoundError):
        return None


# ---- gateway-backed ---------------------------------------------------------
def create_session(profile, title=None, db_path=None):
    _check_profile(profile)
    port, key = gateways.ensure_running(profile, db_path)
    body = {"title": title} if title else {}
    data = _gateway_json(port, key, "POST", "/api/sessions", body, retry_for=8.0)
    sess = data.get("session") if isinstance(data.get("session"), dict) else data
    sid = sess.get("id") or sess.get("session_id")
    if not sid:
        raise GatewayError("gateway did not return a session id: %s" % json.dumps(data)[:300])
    store.log_activity(action="chat_session", agent_profile=profile, detail="session %s" % sid, db_path=db_path)
    return {"id": sid, "profile": profile, "title": sess.get("title") or title}


def stop_turn(profile, run_id, db_path=None):
    _check_profile(profile); _check_session(run_id)
    port, key = gateways.credentials(profile)
    if not (port and key) or not gateways.healthy(profile):
        raise ValueError("gateway for %s is not running" % profile)
    return _gateway_json(port, key, "POST", "/v1/runs/%s/stop" % run_id, {})


def stream_turn(profile, session_id, message, db_path=None):
    """Generator of SSE bytes from the gateway's /chat/stream, pass-through.

    Raises ValueError (chat disabled / bad input) or GatewayError BEFORE the
    first byte, so the route can still answer 409/502; after that, failures are
    reported in-band as an `event: error` + `event: done` pair.
    """
    _check_profile(profile); _check_session(session_id)
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message is required")
    port, key = gateways.ensure_running(profile, db_path)
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=CONNECT_TIMEOUT)
    try:
        c.connect()
        c.sock.settimeout(TURN_TIMEOUT)   # connect fast, then allow a long-running turn
        c.request("POST", "/api/sessions/%s/chat/stream" % session_id, body=json.dumps({"message": message}),
                  headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "text/event-stream"})
        r = c.getresponse()
    except (OSError, http.client.HTTPException) as e:
        c.close()
        raise GatewayError("gateway unreachable: %s" % e)
    if r.status >= 400:
        raw = r.read().decode("utf-8", "replace"); c.close()
        try:
            msg = (json.loads(raw).get("error") or {}).get("message") or raw[:300]
        except (ValueError, AttributeError):
            msg = raw[:300]
        raise GatewayError("gateway chat -> %d: %s" % (r.status, msg))
    store.log_activity(action="chat_message", agent_profile=profile, detail="session %s: %s" % (session_id, message.strip()[:120]), db_path=db_path)

    def gen():
        last_touch = 0.0
        try:
            while True:
                chunk = r.readline()
                if not chunk:
                    break
                yield chunk
                now = time.time()
                if now - last_touch > 30:   # keep the idle sweeper off a live turn
                    gateways.touch(profile, db_path); last_touch = now
        except (OSError, http.client.HTTPException, socket.timeout) as e:
            yield ("event: error\ndata: %s\n\nevent: done\ndata: {}\n\n" % json.dumps({"message": "stream broke: %s" % e})).encode()
        finally:
            c.close()
            gateways.touch(profile, db_path)
    return gen()
