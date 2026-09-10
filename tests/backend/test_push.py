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


def test_payload_carries_unread_for_badge(env, monkeypatch):
    """9-2: pushes carry the live unread count so sw.js can badge the app icon while the app is closed."""
    c, store, gw, root = env
    from backend import push
    db = store.DEFAULT_DB_PATH
    h = login(c)
    assert c.post("/api/push/subscribe", json={"endpoint": "http://127.0.0.1:9/dead", "keys": _browser_keys()}, headers=h).status_code == 200
    sent = []
    monkeypatch.setattr(push, "send", lambda s, p, db_path=None: sent.append(p) or True)
    n1 = store.add_notification("info", "one", "b", "/tasks/1", source_key="u:1", db_path=db)
    n2 = store.add_notification("needs_you", "two", "b", "/tasks/2", source_key="u:2", db_path=db)
    assert push.push_notifications([n1, n2], db_path=db, background=False) == 2
    expected = store.list_notifications(limit=1, db_path=db)[1]
    assert expected >= 2 and [p["unread"] for p in sent] == [expected, expected]
    # without a count the key is absent (send_test passes None only if the store read fails)
    assert "unread" not in push.payload_for({"id": 1, "kind": "info", "title": "t"})


# ---- per-cycle notification spam (2026-09-10) ---------------------------
#
# Task #176 produced 59 notifications for ONE task — a needs_you plus an info
# per build↔review cycle. The bell was always fine (`sync_notifications` marks
# a task's earlier unread attention rows read when it transitions again), but
# once the push watermark started delivering every row, a repeat would have
# been 59 buzzes on the owner's phone.

def test_a_cycling_task_delivers_one_real_push_not_sixty(env):
    """Drives the REAL push path and counts HTTP deliveries to a fake push
    service — not `collapse_for_push` in isolation. The first version of this
    test called the helper directly and stayed green with the collapse removed
    from `push_notifications` entirely, which is a test that proves nothing."""
    c, store, gw, root = env
    h = login(c)
    db = store.DEFAULT_DB_PATH
    srv = HTTPServer(("127.0.0.1", 0), FakePushService); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakePushService.received.clear(); FakePushService.status = 201
    try:
        from backend import push
        c.post("/api/push/subscribe",
               json={"endpoint": "http://127.0.0.1:%d/send/abc" % port,
                     "keys": _browser_keys()}, headers=h)
        ids = []
        for cycle in range(30):
            ids.append(store.add_notification("needs_you", "Task #176 needs you — needs review",
                                              task_id=176, source_key="c%d:a" % cycle, db_path=db))
            ids.append(store.add_notification("info", "Task #176 sent back for rework",
                                              task_id=176, source_key="c%d:b" % cycle, db_path=db))
        assert len(ids) == 60
        delivered = push.push_notifications(ids, db_path=db, background=False)
        assert delivered == 1, "60 notifications for one task must be ONE buzz"
        assert len(FakePushService.received) == 1
    finally:
        srv.shutdown()


def test_a_cycling_task_produces_one_push_not_fifty_nine(env):
    _c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    from backend import push
    ids = []
    for cycle in range(30):
        a = store.add_notification("needs_you", "Task #176 needs you — needs review",
                                   task_id=176, source_key="t:%d:a" % cycle, db_path=db)
        b = store.add_notification("info", "Task #176 sent back for rework",
                                   task_id=176, source_key="t:%d:b" % cycle, db_path=db)
        ids += [a, b]
    assert len(ids) == 60
    rows = store.get_notifications(ids, db_path=db)
    collapsed = push.collapse_for_push(rows)
    assert len(collapsed) == 1
    assert collapsed[0]["id"] == ids[-1]        # the newest state, not the first


def test_a_superseded_row_is_never_pushed(env):
    """A later transition marks the earlier attention rows read. Buzzing about a
    state the task has already left is worse than silence."""
    _c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    from backend import push
    import sqlite3, time as _t
    old = store.add_notification("needs_you", "Task #9 needs you — blocked",
                                 task_id=9, source_key="old", db_path=db)
    new = store.add_notification("done", "Task #9 is done", task_id=9,
                                 source_key="new", db_path=db)
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("UPDATE notifications SET read_at=? WHERE id=?", (_t.time(), old))
    conn.close()
    collapsed = push.collapse_for_push(store.get_notifications([old, new], db_path=db))
    assert [n["id"] for n in collapsed] == [new]


def test_different_tasks_each_get_their_own_push(env):
    _c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    from backend import push
    a = store.add_notification("needs_you", "Task #1 needs you", task_id=1,
                               source_key="a", db_path=db)
    b = store.add_notification("needs_you", "Task #2 needs you", task_id=2,
                               source_key="b", db_path=db)
    collapsed = push.collapse_for_push(store.get_notifications([a, b], db_path=db))
    assert sorted(n["id"] for n in collapsed) == sorted([a, b])


def test_taskless_notifications_are_never_collapsed_together(env):
    """A due reminder, a librarian proposal and a schedule error are distinct
    events that merely share a batch."""
    _c, store, _gw, _root = env
    db = store.DEFAULT_DB_PATH
    from backend import push
    ids = [store.add_notification("needs_you", "Reminder: dentist", source_key="r1", db_path=db),
           store.add_notification("needs_you", "Librarian: file proposal on 'x'", source_key="p1", db_path=db),
           store.add_notification("needs_you", "Schedule 'lint' failed", source_key="e1", db_path=db)]
    collapsed = push.collapse_for_push(store.get_notifications(ids, db_path=db))
    assert len(collapsed) == 3
