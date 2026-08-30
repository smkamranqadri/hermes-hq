"""Web Push (4b-5.3): generic for any install.

- VAPID key pair generated once per install into `<hq home>/vapid_private.pem` (never leaves the server);
  the public key is served to browsers by `GET /api/push/vapid`.
- Subscriptions (one per browser/device) live in hq.db `push_subscriptions`.
- Every new notification row is pushed to every subscription; 404/410 from the push service removes the
  subscription; other failures are counted and the subscription is dropped after 8 in a row.
- `sync_and_push()` runs on each dispatcher tick and after client-side inserts, so pushes go out even when no
  browser is polling (the phone app is frozen in the background — the whole point of push).
Contact for the VAPID `sub` claim: `HERMES_HQ_PUSH_CONTACT` (mailto:/https:), default `mailto:admin@localhost`.
"""
import json, logging, os, threading, time

from core import wm_store as store

log = logging.getLogger("backend.push")
_lock = threading.Lock()
_vapid = {"pem": None, "public": None}
MAX_FAILURES = 8


def _pem_path():
    return os.path.join(store.hq_home(), "vapid_private.pem")


def vapid():
    """(private_pem_path, public_key_b64url). Generates the pair on first use."""
    with _lock:
        if _vapid["public"]:
            return _vapid["pem"], _vapid["public"]
        from py_vapid import Vapid, b64urlencode
        path = _pem_path()
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            v = Vapid(); v.generate_keys(); v.save_key(path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        v = Vapid.from_file(path)
        from cryptography.hazmat.primitives import serialization
        raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        _vapid.update(pem=path, public=b64urlencode(raw))
        return _vapid["pem"], _vapid["public"]


def contact():
    c = os.environ.get("HERMES_HQ_PUSH_CONTACT", "").strip() or "mailto:admin@localhost"
    return c if c.startswith(("mailto:", "https://")) else "mailto:" + c


def send(sub, payload, db_path=None):
    """Push one payload to one subscription row. Returns True on success; prunes dead subscriptions."""
    from pywebpush import webpush, WebPushException
    pem, _ = vapid()
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": json.loads(sub["keys_json"])},
                data=json.dumps(payload), vapid_private_key=pem, vapid_claims={"sub": contact()}, ttl=3600, timeout=10)
        store.push_subscription_ok(sub["id"], db_path=db_path)
        return True
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            store.remove_push_subscription(endpoint=sub["endpoint"], db_path=db_path)
            log.info("push subscription gone (%s): removed", status)
        else:
            n = store.push_subscription_failed(sub["id"], db_path=db_path)
            log.warning("push failed (%s): %s [failures=%s]", status, str(e)[:200], n)
            if n >= MAX_FAILURES:
                store.remove_push_subscription(endpoint=sub["endpoint"], db_path=db_path)
        return False
    except Exception as e:   # DNS down, etc. — count it, never raise into the caller
        n = store.push_subscription_failed(sub["id"], db_path=db_path)
        log.warning("push error: %s [failures=%s]", str(e)[:200], n)
        if n >= MAX_FAILURES:
            store.remove_push_subscription(endpoint=sub["endpoint"], db_path=db_path)
        return False


def payload_for(n):
    return {"id": n["id"], "kind": n["kind"], "title": n["title"], "body": n.get("body"), "href": n.get("href") or "/inbox", "tag": "hq-%s" % n["id"]}


def push_notifications(ids, db_path=None, background=True):
    """Push the given notification ids to every subscription (in a thread by default)."""
    ids = [int(i) for i in ids or [] if i]
    if not ids:
        return 0
    subs = store.list_push_subscriptions(db_path=db_path)
    if not subs:
        return 0
    rows = store.get_notifications(ids, db_path=db_path)

    def work():
        for n in rows:
            p = payload_for(n)
            for s in subs:
                send(s, p, db_path=db_path)
    if background:
        threading.Thread(target=work, name="hq-push", daemon=True).start()
    else:
        work()
    return len(rows) * len(subs)


def sync_and_push(db_path=None, background=True):
    """Turn new state transitions into notifications and push them. Safe to call often (idempotent)."""
    try:
        new_ids = store.sync_notifications(db_path=db_path)
    except Exception:
        log.exception("notification sync failed")
        return 0
    return push_notifications(new_ids, db_path=db_path, background=background)


def send_test(db_path=None):
    subs = store.list_push_subscriptions(db_path=db_path)
    ok = 0
    for s in subs:
        ok += 1 if send(s, {"id": 0, "kind": "info", "title": "hermes-hq push works", "body": "You will get alerts here even when the app is closed.", "href": "/inbox", "tag": "hq-test"}, db_path=db_path) else 0
    return {"subscriptions": len(subs), "delivered": ok}
