"""Group 4b-5.3: Web Push — VAPID keys per install, subscriptions, real encrypted pushes to a fake push service."""
import base64, json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from tests.backend.test_chat import env, login  # noqa: F401


class FakePushService(BaseHTTPRequestHandler):
    received = []
    status = 201

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        FakePushService.received.append({"path": self.path, "encoding": self.headers.get("Content-Encoding"), "auth": self.headers.get("Authorization", ""), "ttl": self.headers.get("TTL"), "len": len(body)})
        self.send_response(FakePushService.status); self.send_header("Content-Length", "0"); self.end_headers()

    def log_message(self, *a): pass


def _browser_keys():
    """What a browser hands out in PushSubscription.toJSON().keys."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    priv = ec.generate_private_key(ec.SECP256R1())
    raw = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    return {"p256dh": b64(raw), "auth": b64(os.urandom(16))}


def test_vapid_subscribe_push_and_prune(env):
    c, store, gw, root = env
    h = login(c)
    srv = HTTPServer(("127.0.0.1", 0), FakePushService); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakePushService.received.clear(); FakePushService.status = 201
    try:
        v = c.get("/api/push/vapid").json()
        assert len(v["publicKey"]) == 87 and v["subscriptions"] == 0            # 65-byte uncompressed P-256 point, base64url
        assert c.get("/api/push/vapid").json()["publicKey"] == v["publicKey"]     # stable per install
        assert os.path.exists(os.path.join(store.hq_home(), "vapid_private.pem"))
        # subscribe (loopback http is accepted for dev/test; real browsers only hand out https endpoints)
        ep = "http://127.0.0.1:%d/send/abc" % port
        r = c.post("/api/push/subscribe", json={"endpoint": ep, "keys": _browser_keys()}, headers=h)
        assert r.status_code == 200 and r.json()["subscriptions"] == 1
        assert c.post("/api/push/subscribe", json={"endpoint": "ftp://example.com/push", "keys": {"p256dh": "a", "auth": "b"}}, headers=h).status_code == 409
        # a new notification is pushed (synchronously here) as an aes128gcm-encrypted body with a VAPID header
        from backend import push
        nid = store.add_notification("needs_you", "Task #1 needs you", "blocked", "/tasks/1", source_key="t:1", db_path=store.DEFAULT_DB_PATH)
        assert push.push_notifications([nid], db_path=store.DEFAULT_DB_PATH, background=False) == 1
        assert len(FakePushService.received) == 1
        got = FakePushService.received[0]
        assert got["path"] == "/send/abc" and got["encoding"] == "aes128gcm" and got["auth"].startswith("vapid t=") and got["len"] > 100 and got["ttl"] == "3600"
        # test route
        assert c.post("/api/push/test", headers=h).json() == {"subscriptions": 1, "delivered": 1}
        # gone subscription (410) is pruned
        FakePushService.status = 410
        assert c.post("/api/push/test", headers=h).json() == {"subscriptions": 1, "delivered": 0}
        assert c.get("/api/push/vapid").json()["subscriptions"] == 0
        # unsubscribe of an unknown endpoint is a no-op
        assert c.post("/api/push/unsubscribe", json={"endpoint": ep}, headers=h).json()["removed"] == 0
    finally:
        srv.shutdown()


def test_sync_returns_new_ids_and_dispatcher_hook_is_idempotent(env):
    c, store, gw, root = env
    from backend import push
    db = store.DEFAULT_DB_PATH
    store.create_project("demo", "Demo", "", "/tmp/demo", db_path=db)
    t = store.create_task("demo", "Write docs", "", "", db_path=db); tid = t if isinstance(t, int) else t["id"]
    assert store.sync_notifications(db_path=db) == []                 # watermark only
    con = store._connect(db)
    with con:
        con.execute("INSERT INTO state_transitions(task_id, ts, from_status, to_status, detail) VALUES (?, 1, 'running', 'failed', 'boom')", (tid,))
    con.close()
    assert push.sync_and_push(db_path=db, background=False) == 0        # no subscriptions → nothing sent, but the row exists
    rows, unread = store.list_notifications(db_path=db)
    assert unread == 1 and rows[0]["kind"] == "needs_you"
    assert push.sync_and_push(db_path=db, background=False) == 0 and store.list_notifications(db_path=db)[1] == 1
