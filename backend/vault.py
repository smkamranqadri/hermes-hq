"""The private vault (Second Brain Phase 4 slice B, `intent/Phase4Plan.md`).

Everything the vault knows how to do lives here: the crypto, the row access
and the one destruction path. `core/wm_store.py` owns the tables and nothing
else — it has no function that reads or writes a `vault_notes` row, which is
what makes the librarian's denial STRUCTURAL rather than prompted. Every
agent-facing surface (`list_notes`, `search_notes`, `lint_library`,
`related_notes`, the proposal path, the `wm note` CLI group, the export) reads
`notes`, and a row that is not in `notes` is a row none of them can reach.

WHAT THIS ACTUALLY PROTECTS — read this before trusting it
----------------------------------------------------------
`/opt/data` is a **virtiofs** mount from the owner's Mac, and ownership is
remapped per viewer: a file written by root with mode 0600 is seen by an agent
run (uid 10000) as its own `-rw-------` and read without obstacle. This was
settled empirically on 2026-09-10, not assumed. Consequences, stated plainly
because an implied property is worse than none:

  * **No file mode or ownership under `/opt/data` is a security boundary.**
    A server-held key file there could not be hidden from an agent, so no
    such file exists any more — see the recovery code below.
  * `hq.db` is on that same mount, so an agent run **can read the vault
    ciphertext and the wrapped keys**. It cannot read the plaintext.
  * Vault security therefore reduces to **passphrase strength against an
    offline attack**. That is why scrypt runs at n=2**15 and why a passphrase
    shorter than MIN_PASSPHRASE characters is refused outright.

What the vault does buy, precisely: the librarian cannot reach a vault row
through any tool it has (structural — the rows are not in `notes`), a live
session on an unlocked phone cannot open the vault, and a stolen `hq.db`
alone is useless without the passphrase.

Key handling, in the order it matters:

1. A random 32-byte DATA KEY seals every field (AES-GCM, `nonce || ct`).
2. It is wrapped TWICE, both times with scrypt, and BOTH wraps live in
   `vault_meta`. There is no key file anywhere.
   - `scrypt(passphrase)` is the READ PATH — the API, the web UI and
     `wm vault list` unwrap with the passphrase and nothing else.
   - `scrypt(recovery_code)` exists so a forgotten passphrase is not a dead
     end. The code is >= 160 bits, generated once, **printed once**, and
     never stored, never logged, never written to `activity`.
3. **No route ever ACCEPTS a recovery code, and exactly one route returns
   one: `POST /api/vault/init`, at creation, once.** Possession of the code
   opens the vault outright, so nothing that takes one may sit behind a
   session cookie — `unwrap_recovery` exists only for `wm vault recover`.
   Showing it at creation is different in kind: it appears in the same
   authenticated browser where the owner has just typed the passphrase, and
   withholding it there is what made a phone-created vault unrecoverable
   (owner's reversal, 2026-09-10). A test enumerates every route to hold
   exactly this line — init as the single, creation-only exception.

Because the recovery code is not stored, losing it is permanent — hence
`wm vault recovery-code --regenerate`, which mints a fresh one (and thereby
invalidates the old) for anyone who still knows the passphrase.

The unlocked data key NEVER enters `backend/auth.py`'s session dict:
`Sessions._save()` serialises that dict to `sessions.json` on disk, so a key
put there would be written out on the next save. Unlocked keys live in
`KEYS`, a process-memory map keyed by session token with an idle timeout,
cleared on logout and gone on every restart (including the 05:00 PKT
auto-update).
"""
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core import wm_store as store

# scrypt parameters. Stored in vault_meta at init so they can be raised later
# without stranding an existing vault. n=2**15/r=8/p=1 is 32 MiB of scratch,
# so maxmem has to be raised past OpenSSL's 32 MiB default or the KDF errors.
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 96 << 20
KEY_BYTES = 32
NONCE_BYTES = 12

# How long an unlocked session key survives without use.
IDLE_SECONDS = 30 * 60

# See the module docstring: the ciphertext is assumed readable by anything on
# this box, so the passphrase is the whole defence in an offline attack.
MIN_PASSPHRASE = 12

VAULT_KINDS = ("note", "secret")
ATTACHMENT_KINDS = ("photo", "voice", "file")


class VaultError(Exception):
    """Refusals the API turns into 4xx: wrong passphrase, locked, not set up."""


class VaultLocked(VaultError):
    pass


# ---------------------------------------------------------------------------
# recovery code — high entropy, printed ONCE, never stored
# ---------------------------------------------------------------------------
# base32 without the padding: unambiguous in print and safe to retype. 25
# bytes is 200 bits, comfortably past the 160-bit floor.
RECOVERY_BYTES = 25
RECOVERY_GROUP = 5


def new_recovery_code():
    """A fresh code in readable groups. The ONLY place one is minted."""
    import base64
    raw = base64.b32encode(secrets.token_bytes(RECOVERY_BYTES)).decode().rstrip("=")
    return "-".join(raw[i:i + RECOVERY_GROUP] for i in range(0, len(raw), RECOVERY_GROUP))


def normalize_recovery_code(code):
    """Accept it however the owner retypes it: spaces, dashes, any case."""
    return "".join(str(code or "").split()).replace("-", "").upper()


# ---------------------------------------------------------------------------
# sealing
# ---------------------------------------------------------------------------
def seal(key, plaintext, aad=b""):
    """nonce || AES-GCM(plaintext). `aad` binds a ciphertext to its column, so
    a body blob copied into the title column fails to open rather than
    silently rendering somewhere it was never meant to be."""
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    nonce = secrets.token_bytes(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def unseal(key, blob, aad=b""):
    if blob is None:
        return b""
    blob = bytes(blob)
    if len(blob) < NONCE_BYTES + 16:
        raise VaultError("sealed value is truncated")
    try:
        return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
    except InvalidTag:
        raise VaultError("sealed value did not open with this key")


def unseal_text(key, blob, aad=b""):
    return unseal(key, blob, aad).decode("utf-8", "replace")


AAD_TITLE = b"vault.note.title"
AAD_BODY = b"vault.note.body"
AAD_ENTRY = b"vault.entry.body"
AAD_ATT_BLOB = b"vault.attachment.blob"
AAD_ATT_NAME = b"vault.attachment.name"
AAD_ATT_MIME = b"vault.attachment.mime"
AAD_DATA_KEY = b"vault.datakey"


# ---------------------------------------------------------------------------
# vault_meta — the wrapped key
# ---------------------------------------------------------------------------
def _meta(conn):
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM vault_meta")}


def _set_meta(conn, **kw):
    for k, v in kw.items():
        conn.execute("INSERT INTO vault_meta(key, value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))


def _derive(passphrase, salt, params):
    if not isinstance(passphrase, str) or not passphrase:
        raise VaultError("a passphrase is required")
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt,
                          n=int(params["scrypt_n"]), r=int(params["scrypt_r"]),
                          p=int(params["scrypt_p"]), dklen=KEY_BYTES,
                          maxmem=SCRYPT_MAXMEM)


def is_initialized(db_path=None):
    conn = store._connect(db_path)
    try:
        return "pass_wrap" in _meta(conn)
    finally:
        conn.close()


def _check_passphrase(passphrase):
    """One rule, applied at init AND at every change.

    The floor is 12, not 8: `hq.db` sits on a virtiofs mount an agent run can
    read, so the ciphertext is assumed obtainable and the passphrase is all
    that stands between it and plaintext in an OFFLINE attack. An 8-character
    passphrase is not a credential under that threat model.
    """
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE:
        raise VaultError(
            "the vault passphrase must be at least %d characters — hq.db can be "
            "read by anything running on this box, so the passphrase is the only "
            "thing protecting the contents against an offline attack"
            % MIN_PASSPHRASE)


def init_vault(passphrase, db_path=None, with_recovery=True):
    """Mint the data key and its wraps. Returns the recovery code ONCE.

    A second call is refused: re-initialising would orphan every sealed row.

    The code is returned exactly ONCE, from this call, and never persisted.
    Both the CLI and the HTTP setup route show it to the owner at creation —
    the owner's 2026-09-10 reversal, and it is the right call: the code is
    displayed in the same authenticated browser where they just typed the
    passphrase, so showing it there exposes it to nothing the passphrase is
    not already exposed to, while withholding it left a phone-created vault
    permanently unrecoverable.

    `with_recovery=False` exists only for tests that need a vault with no
    recovery wrap (the pre-reversal shape, which older vaults still have).
    """
    _check_passphrase(passphrase)
    conn = store._connect(db_path)
    try:
        if "pass_wrap" in _meta(conn):
            raise VaultError("the vault is already set up")
        data_key = secrets.token_bytes(KEY_BYTES)
        salt = secrets.token_bytes(16)
        params = {"scrypt_n": SCRYPT_N, "scrypt_r": SCRYPT_R, "scrypt_p": SCRYPT_P}
        pass_key = _derive(passphrase, salt, params)
        meta = {"kdf": "scrypt", "salt": salt.hex(),
                "pass_wrap": seal(pass_key, data_key, AAD_DATA_KEY).hex(),
                "created_at": time.time()}
        meta.update(params)
        code = None
        if with_recovery:
            code, rmeta = _wrap_for_recovery(data_key)
            meta.update(rmeta)
        with conn:
            _set_meta(conn, **meta)
        # The code is RETURNED, never persisted and never logged. This return
        # value is the only time it exists outside the caller's terminal.
        return {"created": True, "recovery_code": code}
    finally:
        conn.close()


def _wrap_for_recovery(data_key):
    """(code, meta) — a fresh code and the vault_meta fields that wrap under it."""
    code = new_recovery_code()
    rsalt = secrets.token_bytes(16)
    params = {"scrypt_n": SCRYPT_N, "scrypt_r": SCRYPT_R, "scrypt_p": SCRYPT_P}
    rkey = _derive(normalize_recovery_code(code), rsalt, params)
    return code, {"recovery_salt": rsalt.hex(),
                  "recovery_wrap": seal(rkey, data_key, AAD_DATA_KEY).hex()}


def unlock(passphrase, db_path=None):
    """THE READ PATH. Returns the data key, or raises. The passphrase wrap is
    the only one it touches — the recovery wrap is not a fallback here."""
    conn = store._connect(db_path)
    try:
        m = _meta(conn)
    finally:
        conn.close()
    if "pass_wrap" not in m:
        raise VaultError("the vault is not set up yet")
    pass_key = _derive(passphrase, bytes.fromhex(m["salt"]), m)
    try:
        return unseal(pass_key, bytes.fromhex(m["pass_wrap"]), AAD_DATA_KEY)
    except VaultError:
        raise VaultError("wrong passphrase")


def unwrap_recovery(code, db_path=None):
    """Open the vault with the recovery code, the passphrase unknown.

    No gate and no uid check: possession of a 200-bit code IS the credential,
    and a `geteuid` test would buy nothing on a mount whose ownership is
    remapped per viewer anyway. What keeps this off the web is that no HTTP
    route calls it — enumerated by test.
    """
    conn = store._connect(db_path)
    try:
        m = _meta(conn)
    finally:
        conn.close()
    if "pass_wrap" not in m:
        raise VaultError("the vault is not set up yet")
    if "recovery_wrap" not in m:
        raise VaultError(
            "this vault has no recovery code (it was set up from the web). "
            "Run `wm vault recovery-code --regenerate` while you still know "
            "the passphrase.")
    rkey = _derive(normalize_recovery_code(code), bytes.fromhex(m["recovery_salt"]), m)
    try:
        return unseal(rkey, bytes.fromhex(m["recovery_wrap"]), AAD_DATA_KEY)
    except VaultError:
        raise VaultError("that recovery code does not open this vault")


def regenerate_recovery_code(data_key, db_path=None):
    """Mint a fresh code for an already-open data key, invalidating the old.

    The data key itself is untouched, so every sealed row stays readable and
    the passphrase wrap keeps working.
    """
    code, rmeta = _wrap_for_recovery(data_key)
    conn = store._connect(db_path)
    try:
        with conn:
            _set_meta(conn, **rmeta)
            # No code, no hash, no hint — just that it happened.
            _audit(conn, "rewrapped", detail="recovery code regenerated")
    finally:
        conn.close()
    return code


def set_passphrase(data_key, passphrase, db_path=None):
    """Re-wrap an already-open data key under a new passphrase. The recovery
    wrap is untouched: the data key itself never changes, so every sealed row
    stays readable and the recovery code keeps working."""
    _check_passphrase(passphrase)
    conn = store._connect(db_path)
    try:
        m = _meta(conn)
        if "pass_wrap" not in m:
            raise VaultError("the vault is not set up yet")
        salt = secrets.token_bytes(16)
        params = {"scrypt_n": SCRYPT_N, "scrypt_r": SCRYPT_R, "scrypt_p": SCRYPT_P}
        pass_key = _derive(passphrase, salt, params)
        with conn:
            _set_meta(conn, salt=salt.hex(),
                      pass_wrap=seal(pass_key, data_key, AAD_DATA_KEY).hex(), **params)
            _audit(conn, "rewrapped", detail="passphrase changed")
    finally:
        conn.close()
    return True


# ---------------------------------------------------------------------------
# unlocked keys — PROCESS MEMORY ONLY
# ---------------------------------------------------------------------------
class KeyStore:
    """Unlocked data keys, keyed by session token, never serialised.

    Deliberately NOT `request.state.session`: `auth.Sessions._save()` json-dumps
    that dict to `sessions.json` (0600, but on disk) on the next create/drop,
    so a key parked there would be written out. This map dies with the process
    — which is the intended behaviour, the vault re-seals on every restart.
    """

    def __init__(self, idle_seconds=IDLE_SECONDS):
        self.idle = idle_seconds
        self._keys = {}
        self._lock = threading.Lock()

    def put(self, token, key):
        if not token:
            raise VaultError("no session")
        now = time.time()
        with self._lock:
            self._keys[token] = [key, now, now]      # key, last_used, unlocked_at

    def get(self, token):
        """A real read: it refreshes the idle timer, because using the vault
        is what "still active" means."""
        with self._lock:
            row = self._keys.get(token or "")
            if row is None:
                return None
            if time.time() - row[1] > self.idle:
                self._keys.pop(token, None)
                return None
            row[1] = time.time()
            return row[0]

    def peek(self, token):
        """Is it unlocked, WITHOUT counting as use.

        `status` is polled by the open page every 60s. If that refreshed the
        timer, a vault left open on the phone would never idle-lock — the
        timeout would be defeated by the very UI that displays it.
        """
        with self._lock:
            row = self._keys.get(token or "")
            if row is None:
                return False
            if time.time() - row[1] > self.idle:
                self._keys.pop(token, None)
                return False
            return True

    def drop(self, token):
        with self._lock:
            self._keys.pop(token or "", None)

    def clear(self):
        with self._lock:
            self._keys.clear()

    def unlocked_at(self, token):
        with self._lock:
            row = self._keys.get(token or "")
            return row[2] if row else None

    def idle_since(self, token):
        with self._lock:
            row = self._keys.get(token or "")
            return row[1] if row else None


KEYS = KeyStore()


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
def _audit(conn, action, old_note_id=None, vault_note_id=None, title=None,
           counts=None, detail=None):
    """Record WHAT happened without preserving the secret: a title is stored
    as a sha256, which proves a specific note was destroyed to anyone who
    already knows the title and reveals nothing to anyone who does not."""
    counts = counts or {}
    cur = conn.execute(
        "INSERT INTO vault_audit(action, old_note_id, vault_note_id, title_sha256, "
        "revisions, entries, attachments, links, proposals, detail, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (action, old_note_id, vault_note_id,
         hashlib.sha256(title.encode("utf-8")).hexdigest() if title else None,
         counts.get("revisions", 0), counts.get("entries", 0),
         counts.get("attachments", 0), counts.get("links", 0),
         counts.get("proposals", 0), detail, time.time()))
    return cur.lastrowid


def list_audit(limit=100, db_path=None):
    conn = store._connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM vault_audit ORDER BY id DESC LIMIT ?", (int(limit),))]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# vault notes
# ---------------------------------------------------------------------------
def count_notes(db_path=None):
    conn = store._connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM vault_notes").fetchone()["n"]
    finally:
        conn.close()


def list_notes(key=None, db_path=None, limit=500):
    """Locked (`key=None`) returns id + dates ONLY — the lock-icon-and-a-date
    row. Unlocked decrypts titles, so an unlocked vault reads like an ordinary
    list instead of a wall of dates (owner decision: a vault that is annoying
    to read becomes write-only)."""
    conn = store._connect(db_path)
    try:
        rows = conn.execute(
            "SELECT n.id, n.kind, n.created_at, n.updated_at, n.title_enc,"
            " (SELECT COUNT(*) FROM vault_entries e WHERE e.note_id=n.id) AS entry_count,"
            " (SELECT COUNT(*) FROM vault_attachments a WHERE a.note_id=n.id) AS attachment_count"
            " FROM vault_notes n ORDER BY COALESCE(n.updated_at, n.created_at) DESC "
            "LIMIT ?", (int(limit),)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = {"id": r["id"], "kind": r["kind"], "created_at": r["created_at"],
             "updated_at": r["updated_at"], "entry_count": r["entry_count"],
             "attachment_count": r["attachment_count"], "locked": key is None,
             "title": None}
        if key is not None:
            d["title"] = unseal_text(key, r["title_enc"], AAD_TITLE)
        out.append(d)
    return out


def get_note(note_id, key, db_path=None):
    if key is None:
        raise VaultLocked("the vault is locked")
    conn = store._connect(db_path)
    try:
        r = conn.execute("SELECT * FROM vault_notes WHERE id=?", (note_id,)).fetchone()
        if r is None:
            return None
        entries = conn.execute(
            "SELECT id, body_enc, created_at FROM vault_entries WHERE note_id=? "
            "ORDER BY id DESC", (note_id,)).fetchall()
        atts = conn.execute(
            "SELECT id, kind, name_enc, mime_enc, bytes, created_at "
            "FROM vault_attachments WHERE note_id=? ORDER BY id", (note_id,)).fetchall()
    finally:
        conn.close()
    return {
        "id": r["id"], "kind": r["kind"], "created_at": r["created_at"],
        "updated_at": r["updated_at"], "locked": False,
        "title": unseal_text(key, r["title_enc"], AAD_TITLE),
        "body": unseal_text(key, r["body_enc"], AAD_BODY),
        "entries": [{"id": e["id"], "created_at": e["created_at"],
                     "body": unseal_text(key, e["body_enc"], AAD_ENTRY)}
                    for e in entries],
        "attachments": [{"id": a["id"], "kind": a["kind"], "bytes": a["bytes"],
                         "created_at": a["created_at"],
                         "name": unseal_text(key, a["name_enc"], AAD_ATT_NAME)
                         if a["name_enc"] else None,
                         "mime": unseal_text(key, a["mime_enc"], AAD_ATT_MIME)
                         if a["mime_enc"] else None}
                        for a in atts],
    }


def create_note(title, body, key, kind="note", db_path=None, created_at=None,
                _conn=None, _audit_action="created"):
    if key is None:
        raise VaultLocked("the vault is locked")
    if not isinstance(title, str) or not title.strip():
        raise VaultError("a title is required")
    if kind not in VAULT_KINDS:
        raise VaultError("invalid vault kind %r" % (kind,))
    now = time.time() if created_at is None else float(created_at)
    own = _conn is None
    conn = _conn or store._connect(db_path)
    try:
        ctx = conn if own else contextlib.nullcontext()
        with ctx:
            cur = conn.execute(
                "INSERT INTO vault_notes(title_enc, body_enc, kind, created_at, updated_at) "
                "VALUES(?,?,?,?,?)",
                (seal(key, title.strip(), AAD_TITLE), seal(key, body or "", AAD_BODY),
                 kind, now, now))
            nid = cur.lastrowid
            if _audit_action:
                _audit(conn, _audit_action, vault_note_id=nid, title=title.strip())
            return nid
    finally:
        if own:
            conn.close()


def update_note(note_id, key, title=None, body=None, db_path=None):
    """No revision snapshots. `note_revisions` is plaintext history by design,
    and the vault deliberately keeps none — an edited-away secret that lingers
    in a snapshot table is exactly what move-to-vault destroys elsewhere."""
    if key is None:
        raise VaultLocked("the vault is locked")
    if title is None and body is None:
        raise VaultError("nothing to update")
    conn = store._connect(db_path)
    try:
        with conn:
            if conn.execute("SELECT 1 FROM vault_notes WHERE id=?", (note_id,)).fetchone() is None:
                raise VaultError("no such vault note: %r" % note_id)
            sets, params = [], []
            if title is not None:
                if not isinstance(title, str) or not title.strip():
                    raise VaultError("a title is required")
                sets.append("title_enc=?"); params.append(seal(key, title.strip(), AAD_TITLE))
            if body is not None:
                sets.append("body_enc=?"); params.append(seal(key, body, AAD_BODY))
            sets.append("updated_at=?"); params.append(time.time())
            params.append(note_id)
            conn.execute("UPDATE vault_notes SET %s WHERE id=?" % ", ".join(sets), params)
    finally:
        conn.close()
    return get_note(note_id, key, db_path=db_path)


def add_entry(note_id, body, key, db_path=None, created_at=None, _conn=None):
    if key is None:
        raise VaultLocked("the vault is locked")
    if not isinstance(body, str) or not body.strip():
        raise VaultError("an entry body is required")
    now = time.time()
    own = _conn is None
    conn = _conn or store._connect(db_path)
    try:
        ctx = conn if own else contextlib.nullcontext()
        with ctx:
            if conn.execute("SELECT 1 FROM vault_notes WHERE id=?", (note_id,)).fetchone() is None:
                raise VaultError("no such vault note: %r" % note_id)
            cur = conn.execute(
                "INSERT INTO vault_entries(note_id, body_enc, created_at) VALUES(?,?,?)",
                (note_id, seal(key, body.strip(), AAD_ENTRY),
                 now if created_at is None else float(created_at)))
            conn.execute("UPDATE vault_notes SET updated_at=? WHERE id=?", (now, note_id))
            return cur.lastrowid
    finally:
        if own:
            conn.close()


def add_attachment(note_id, data, key, kind="photo", mime=None, name=None,
                   db_path=None, created_at=None, _conn=None):
    """Bytes are sealed INTO the row. A vault attachment written to
    <hq_home>/attachments would be a plaintext file the vault claims to have
    sealed."""
    if key is None:
        raise VaultLocked("the vault is locked")
    if not data:
        raise VaultError("empty attachment")
    if kind not in ATTACHMENT_KINDS:
        raise VaultError("kind must be one of %s" % (ATTACHMENT_KINDS,))
    blob = seal(key, data, AAD_ATT_BLOB)
    own = _conn is None
    conn = _conn or store._connect(db_path)
    try:
        ctx = conn if own else contextlib.nullcontext()
        with ctx:
            if conn.execute("SELECT 1 FROM vault_notes WHERE id=?", (note_id,)).fetchone() is None:
                raise VaultError("no such vault note: %r" % note_id)
            cur = conn.execute(
                "INSERT INTO vault_attachments(note_id, kind, name_enc, mime_enc, "
                "blob_enc, bytes, created_at) VALUES(?,?,?,?,?,?,?)",
                (note_id, kind,
                 seal(key, name, AAD_ATT_NAME) if name else None,
                 seal(key, mime, AAD_ATT_MIME) if mime else None,
                 blob, len(blob),
                 time.time() if created_at is None else float(created_at)))
            return cur.lastrowid
    finally:
        if own:
            conn.close()


def read_attachment(attachment_id, key, db_path=None):
    """Returns (bytes, mime, name) or None."""
    if key is None:
        raise VaultLocked("the vault is locked")
    conn = store._connect(db_path)
    try:
        r = conn.execute("SELECT * FROM vault_attachments WHERE id=?",
                         (attachment_id,)).fetchone()
    finally:
        conn.close()
    if r is None:
        return None
    return (unseal(key, r["blob_enc"], AAD_ATT_BLOB),
            unseal_text(key, r["mime_enc"], AAD_ATT_MIME) if r["mime_enc"] else None,
            unseal_text(key, r["name_enc"], AAD_ATT_NAME) if r["name_enc"] else None)


# ---------------------------------------------------------------------------
# move-to-vault — the ONE carve-out from archive-only
# ---------------------------------------------------------------------------
def _scrub(conn):
    """Make the destruction real on DISK, not just in the b-tree.

    A plain DELETE leaves the row's bytes on a freed page and the note's terms
    in old FTS5 segments, so a `grep hq.db` for the secret still hits. Three
    knobs, all cheap: `secure_delete` zeroes freed content as it goes, the FTS5
    `optimize` merges the segments the delete marked, and a TRUNCATE checkpoint
    empties the -wal file where the pre-delete pages also live.

    The checkpoint is the one that can genuinely fail: TRUNCATE needs every
    other connection out of the WAL, and one concurrent reader — the phone
    polling, an agent mid-query — is enough to block it. That is the ORDINARY
    case, not an exotic one, and swallowing it left the marker sitting in the
    -wal file while the move reported success (review finding 2026-09-10:
    5 hits vs 0 in the control). So: retry, and RETURN the outcome so the
    caller can put it in the audit row instead of hiding it.
    """
    out = {"fts_optimized": False, "checkpointed": False, "checkpoint_error": None}
    try:
        conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('optimize')")
        conn.commit()                      # the checkpoint below needs no open txn
        out["fts_optimized"] = True
    except Exception as e:                 # no FTS5 in this build: nothing to merge
        out["checkpoint_error"] = "fts optimize: %s" % e
        try:
            conn.rollback()
        except Exception:
            pass
    last = None
    for attempt in range(5):
        try:
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            # sqlite returns (busy, log_pages, checkpointed_pages); busy=0 means
            # it actually ran. A non-zero busy is a SILENT failure otherwise.
            if row is None or row[0] == 0:
                out["checkpointed"] = True
                out["checkpoint_error"] = None
                return out
            last = "wal_checkpoint busy (%s)" % (tuple(row),)
        except Exception as e:
            last = str(e)
        time.sleep(0.2 * (attempt + 1))
    out["checkpoint_error"] = last
    return out


def _refusal(tasks, scheds):
    """The sentence the owner reads, naming a remedy the app can actually do.

    A schedule can be renamed (`update_schedule` takes name/title, POST
    /api/schedules/{id}); a TASK cannot — nothing in hermes-hq edits a task
    title (`edit_task` takes description/DoD/approval only) — so for a task
    the honest instruction is "delete it", not "retitle it".
    """
    parts, fix = [], []
    if tasks:
        parts.append("task%s %s" % ("" if len(tasks) == 1 else "s",
                                    ", ".join("#%d" % t for t in tasks)))
        fix.append("delete the task%s (hermes-hq cannot edit a task's title)"
                   % ("" if len(tasks) == 1 else "s"))
    if scheds:
        parts.append("reminder%s %s" % ("" if len(scheds) == 1 else "s",
                                        ", ".join("#%d" % t for t in scheds)))
        fix.append("rename the reminder%s on the Schedules page"
                   % ("" if len(scheds) == 1 else "s"))
    return ("this note cannot be sealed while %s still carr%s its title — "
            "sealing it would erase the note and leave the title in plain "
            "sight%s. To go ahead, %s, then move the note."
            % (" and ".join(parts),
               "ies" if len(tasks) + len(scheds) == 1 else "y",
               ", and a reminder keeps minting new tasks carrying it" if scheds else "",
               " and ".join(fix)))


def move_impact(note_id, db_path=None):
    """What a move WOULD destroy, computed without writing a thing.

    The confirm modal shows this before the owner commits, because this is the
    only irreversible delete in hermes-hq and "N activity rows will be
    redacted" is exactly the number they need in order to say no.
    """
    conn = store._connect(db_path)
    # Belt AND braces. `dry_run=True` is the contract, but this is a preview of
    # an IRREVERSIBLE delete, so its safety must not rest on one boolean being
    # honoured: the whole call runs inside an explicit transaction that is
    # always rolled back. Without this the preview was safe only because
    # sqlite3 discards uncommitted work on close — accidental, not designed,
    # and a negative control proved it (breaking `dry_run` changed nothing).
    conn.isolation_level = None
    conn.execute("BEGIN")
    try:
        n = conn.execute("SELECT id, title FROM notes WHERE id=?", (note_id,)).fetchone()
        if n is None:
            raise VaultError("no such note: %r" % note_id)
        titles = store.note_title_history(conn, note_id)
        distinctive = [t for t in titles if store.is_distinctive_title(t)]
        tasks, scheds = store.titleholders(conn, note_id, titles=titles)
        counts = store._destroy_note_plaintext(conn, note_id, title=n["title"],
                                               dry_run=True)
        return {
            "note_id": note_id,
            "blocked": bool(tasks or scheds),
            "blocking_tasks": tasks,
            "blocking_schedules": scheds,
            "reason": _refusal(tasks, scheds) if (tasks or scheds) else None,
            "counts": counts,
            "titles_known": len(titles),
            # Told plainly rather than implied: a short or ordinary title is
            # NOT chased through activity/notifications, so occurrences of it
            # elsewhere survive. That is a deliberate trade (see wm_store), and
            # the owner is the one who should know about it.
            "title_reach": bool(distinctive),
            "title_reach_note": None if distinctive else
                ("The title is short or ordinary, so it is not hunted through "
                 "the activity log or notifications — chasing a title like this "
                 "would rewrite unrelated history. The note itself, its Thoughts, "
                 "its revisions and its search-index entry are still destroyed."),
        }
    finally:
        try:
            conn.rollback()          # a preview NEVER writes, whatever happened
        except Exception:
            pass
        conn.close()


class VaultRefused(VaultError):
    """The move cannot be made safe without the owner deciding something."""


def move_note_to_vault(note_id, key, db_path=None):
    """Seal a library note into the vault and destroy its plaintext.

    Order is the whole safety argument: the ciphertext is written, READ BACK
    and compared against the original inside the same transaction, and only
    then is the plaintext destroyed. Any failure — a bad key, a truncated
    blob, a disk error — raises before the DELETE and rolls the whole thing
    back, so the note is either fully in the vault or fully untouched.
    """
    if key is None:
        raise VaultLocked("the vault is locked")
    conn = store._connect(db_path)
    try:
        # secure_delete must be set BEFORE the transaction that frees the pages.
        conn.execute("PRAGMA secure_delete=ON")
        with conn:
            n = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if n is None:
                raise VaultError("no such note: %r" % note_id)
            title, body = n["title"], n["body"] or ""
            # BEFORE anything is written: does a linked task or schedule carry
            # this title? If so the move cannot erase the plaintext, so it is
            # refused with the ids rather than half-done.
            # Scans ALL tasks and schedules, both substring directions, over
            # every title this note has ever had — not just linked objects
            # and not just the current title (round-2 findings B, C and D).
            bad_tasks, bad_scheds = store.titleholders(conn, note_id)
            if bad_tasks or bad_scheds:
                raise VaultRefused(_refusal(bad_tasks, bad_scheds))
            entries = conn.execute(
                "SELECT body, created_at FROM note_entries WHERE note_id=? ORDER BY id",
                (note_id,)).fetchall()
            atts = conn.execute(
                "SELECT * FROM note_attachments WHERE note_id=? ORDER BY id",
                (note_id,)).fetchall()
            # Filing is NOT carried over: vault notes have no area, tags or
            # project by decision. What would be lost silently goes into the
            # body as a provenance line instead.
            trail = ["", "---",
                     "_moved into the vault from library note #%d_" % note_id]
            if n["tags"] and n["tags"] != "[]":
                try:
                    tags = ", ".join(json.loads(n["tags"]))
                except ValueError:
                    tags = ""
                if tags:
                    trail.append("_former tags: %s_" % tags)
            body_out = body + "\n".join(trail)

            vid = create_note(title, body_out, key, kind="note", _conn=conn,
                              created_at=n["created_at"], _audit_action=None)
            for e in entries:
                add_entry(vid, e["body"], key, _conn=conn, created_at=e["created_at"])
            moved_bytes = []                       # (path, plaintext bytes)
            for a in atts:
                try:
                    path = store.attachment_path(a["note_id"], a["stored_name"])
                    data = open(path, "rb").read()
                except (OSError, ValueError):
                    # The row outlived its bytes (already a 410 in the API).
                    # Nothing to seal and nothing to unlink; the count still
                    # tells the audit row an attachment row went away.
                    continue
                add_attachment(vid, data, key, kind=a["kind"], mime=a["mime"],
                               name=a["orig_name"] or a["stored_name"], _conn=conn,
                               created_at=a["created_at"])
                moved_bytes.append((path, data))

            # READ BACK before destroying anything. This is the line that makes
            # the delete safe: it proves the ciphertext in the DB opens with
            # this key and yields back EXACTLY what went in — every field, by
            # value, never by count.
            check = get_note_in_conn(conn, vid, key)
            if check is None:
                raise VaultError("vault read-back found no row — nothing was destroyed")
            if check["title"] != title:
                raise VaultError("vault read-back mismatch on the title — nothing was destroyed")
            if check["body"] != body_out:
                raise VaultError("vault read-back mismatch on the body — nothing was destroyed")
            if check["entries"] != [e["body"].strip() for e in entries]:
                raise VaultError("vault read-back mismatch on a Thought — nothing was destroyed")
            if check["attachments"] != [d for _p, d in moved_bytes]:
                raise VaultError("vault read-back mismatch on an attachment — nothing was "
                                 "destroyed (the sealed bytes did not open back to the "
                                 "file on disk)")

            counts = store._destroy_note_plaintext(conn, note_id, title=title)
            audit_id = _audit(
                conn, "moved_in", old_note_id=note_id, vault_note_id=vid,
                title=title, counts=counts,
                detail="plaintext destroyed in notes, notes_fts, note_revisions, "
                       "note_entries, note_links, note_attachments, proposals; "
                       "%d activity row(s) redacted, %d notification(s) removed"
                       % (counts.get("activity", 0), counts.get("notifications", 0)))
        scrub = _scrub(conn)
    finally:
        conn.close()
    # Files AFTER the commit: row before file on delete, so a stray file is
    # inert rather than a row pointing at nothing (the 2c rule).
    leftovers = []
    for path, _data in moved_bytes:
        try:
            os.remove(path)
        except OSError as e:
            # A file we could not unlink is a PLAINTEXT image that nothing
            # points at any more and that `_backup_attachments` will copy into
            # every future backup. Silence would hide it forever, so it is
            # named in the audit row the owner can read.
            leftovers.append("%s (%s)" % (path, e))
    try:
        os.rmdir(store.attachments_dir(note_id))
    except OSError:
        pass

    # Amend the audit row with what only the post-commit phase can know.
    tail = []
    if not scrub.get("checkpointed"):
        tail.append("WAL NOT truncated (%s) — plaintext pages may remain in "
                    "hq.db-wal until the next checkpoint"
                    % (scrub.get("checkpoint_error") or "unknown"))
    if leftovers:
        tail.append("PLAINTEXT FILE(S) LEFT ON DISK: %s" % "; ".join(leftovers))
    if tail:
        c2 = store._connect(db_path)
        try:
            with c2:
                c2.execute("UPDATE vault_audit SET detail = detail || ?  WHERE id=?",
                           (" | " + " | ".join(tail), audit_id))
        finally:
            c2.close()

    store.log_activity("note_vaulted",
                       detail="note #%s sealed into the vault as vault note #%s "
                              "(plaintext destroyed)" % (note_id, vid),
                       db_path=db_path)
    return {"vault_note_id": vid, "destroyed": counts,
            "wal_truncated": bool(scrub.get("checkpointed")),
            "leftover_files": leftovers}


def get_note_in_conn(conn, note_id, key):
    """Unseal EVERYTHING about a vault note against a live connection.

    This is the read-back, and it runs inside the destruction transaction. It
    must open every sealed field — including each attachment blob — because
    what it does not check, it silently certifies as safe to destroy.

    Review finding (2026-09-10): this used to compare title, body and the
    COUNT of Thoughts. A single corrupted byte in an attachment ciphertext
    therefore passed, the move returned ok, the plaintext image was deleted
    and the owner's photo became permanently unrecoverable. Counts are not
    verification.
    """
    r = conn.execute("SELECT * FROM vault_notes WHERE id=?", (note_id,)).fetchone()
    if r is None:
        return None
    entries = conn.execute("SELECT body_enc FROM vault_entries WHERE note_id=? ORDER BY id",
                           (note_id,)).fetchall()
    atts = conn.execute("SELECT id, blob_enc FROM vault_attachments WHERE note_id=? ORDER BY id",
                        (note_id,)).fetchall()
    return {"title": unseal_text(key, r["title_enc"], AAD_TITLE),
            "body": unseal_text(key, r["body_enc"], AAD_BODY),
            "entries": [unseal_text(key, e["body_enc"], AAD_ENTRY) for e in entries],
            # bytes, not a count: compared against what was read from disk
            "attachments": [unseal(key, a["blob_enc"], AAD_ATT_BLOB) for a in atts]}


# ---------------------------------------------------------------------------
# quarantine drain
# ---------------------------------------------------------------------------
def drain_quarantine(key, db_path=None, apply=False):
    """Move the import's held-back secrets into the vault.

    Phase 3 refused to store these units: no note row was created, and the
    bytes went into a 0600 file outside hq_home so they would never ride in a
    backup. The vault is where they were always headed. Each becomes a vault
    note, the quarantine row is STAMPED (never deleted — the ledger of what the
    import refused is worth keeping) and the 0600 file is removed only after
    the ciphertext has been read back.
    """
    if key is None:
        raise VaultLocked("the vault is locked")
    conn = store._connect(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM import_quarantine WHERE drained_at IS NULL ORDER BY id")]
    finally:
        conn.close()
    out = {"drained": [], "skipped": [], "would_drain": [], "errors": []}
    for r in rows:
        path = r.get("stored_path") or ""
        if not path or not os.path.isfile(path):
            out["skipped"].append({"id": r["id"], "why": "bytes are gone from %s" % (path or "(no path)")})
            continue
        if not apply:
            out["would_drain"].append({"id": r["id"], "source": r["source"], "ident": r["ident"]})
            continue
        try:
            data = open(path, "rb").read()
        except OSError as e:
            out["errors"].append("#%s: %s" % (r["id"], e))
            continue
        text = data.decode("utf-8", "replace")
        # The title is built from source+ident, which are ALREADY plaintext
        # columns in import_quarantine — no new secret enters the clear.
        title = "%s · %s" % (r["source"], os.path.basename(r["ident"]) or r["ident"])
        body = ("_quarantined by the %s import: %s_\n\n"
                "```\n%s\n```" % (r["source"], r["reason"], text))
        c = store._connect(db_path)
        try:
            c.execute("PRAGMA secure_delete=ON")
            with c:
                vid = create_note(title, body, key, kind="secret", _conn=c,
                                  created_at=r["created_at"], _audit_action=None)
                check = get_note_in_conn(c, vid, key)
                if check["body"] != body:
                    raise VaultError("vault read-back mismatch for quarantine #%s" % r["id"])
                c.execute("UPDATE import_quarantine SET drained_at=? WHERE id=?",
                          (time.time(), r["id"]))
                _audit(c, "drained", vault_note_id=vid, title=title,
                       detail="import_quarantine #%s (%s %s), sha256 %s"
                              % (r["id"], r["source"], r["ident"], r["sha256"][:16]))
        finally:
            c.close()
        try:
            os.remove(path)
        except OSError as e:
            out["errors"].append("#%s: sealed, but the 0600 file remains: %s" % (r["id"], e))
        out["drained"].append({"id": r["id"], "vault_note_id": vid,
                               "source": r["source"], "ident": r["ident"]})
    return out


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def status(token=None, db_path=None):
    conn = store._connect(db_path)
    try:
        m = _meta(conn)
    finally:
        conn.close()
    return {
        "initialized": "pass_wrap" in m,
        # peek, not get: polling status must not keep the vault awake.
        "unlocked": KEYS.peek(token),
        "notes": count_notes(db_path=db_path),
        "idle_seconds": KEYS.idle,
        "unlocked_at": KEYS.unlocked_at(token),
        # whether a recovery code EXISTS — never anything about what it is
        "has_recovery_code": "recovery_wrap" in m,
    }


def verify_title_sha(title, digest):
    """What a `vault_audit` row is FOR: prove a specific note was the one
    destroyed, for someone who already knows the title."""
    return hmac.compare_digest(hashlib.sha256(title.encode("utf-8")).hexdigest(),
                              digest or "")
