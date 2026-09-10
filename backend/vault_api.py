"""HTTP surface for the private vault (Phase 4 slice B).

Owner-only like every `/api/*` route (cookie session + CSRF), and locked on
top of that: the session cookie gets you a list of dates, nothing more. The
passphrase is what turns a row into words, and it is held only in
`vault.KEYS`, a process-memory map — never in `request.state.session`, which
`auth.Sessions._save()` writes to `sessions.json` on disk.

Deliberately absent from this file: the RECOVERY CODE, in every direction.
No route accepts one, none returns one, and setup here mints no recovery wrap
at all — a code returned in a response body would exist in a browser and in
any proxy log, and possession of the code opens the vault outright. Recovery
is `wm vault recover`, at a shell. A test enumerates every route to keep this
true.
"""
import os

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend import vault as V
from backend import auth as A
from backend.files import UPLOAD_MAX_BYTES
from core import wm_store as store

router = APIRouter(prefix="/api/vault")

LOCKED = 423                 # RFC 4918: the resource is there, it is locked


def _db():
    return store.DEFAULT_DB_PATH


def _token(request: Request):
    return request.cookies.get(A.COOKIE)


def _key(request: Request):
    """The unlocked data key for THIS session, or 423. Idle timeout and
    logout both make this return None again without any state on disk."""
    key = V.KEYS.get(_token(request))
    if key is None:
        raise HTTPException(LOCKED, "the vault is locked")
    return key


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except V.VaultLocked as e:
        raise HTTPException(LOCKED, str(e))
    except V.VaultRefused as e:
        # 409 with the store's own sentence: it names the task/reminder ids the
        # owner has to deal with, so the UI must show it verbatim.
        raise HTTPException(409, str(e))
    except V.VaultError as e:
        raise HTTPException(409, str(e))


class Passphrase(BaseModel):
    passphrase: str = Field(min_length=1, max_length=400)


class NewPassphrase(BaseModel):
    passphrase: str = Field(min_length=1, max_length=400)
    new_passphrase: str = Field(min_length=8, max_length=400)


class VaultNoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = ""


class VaultNoteEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = None


class EntryIn(BaseModel):
    body: str = Field(min_length=1)


@router.get("/status")
def vault_status(request: Request):
    return V.status(token=_token(request), db_path=_db())


@router.post("/init")
def vault_init(request: Request, body: Passphrase):
    """Set the vault up from the web. NO recovery code is minted here.

    `with_recovery=False` is the point: this route cannot show the owner a
    code, and a wrap whose code nobody ever saw is dead weight pretending to
    be a safety net. The response says so, and the UI repeats it — the owner
    runs `wm vault recovery-code --regenerate` on the box to gain one.
    """
    res = _guard(V.init_vault, body.passphrase, db_path=_db(), with_recovery=False)
    assert res["recovery_code"] is None      # belt and braces: never in a response
    # Setting it up unlocks it — otherwise the owner types the passphrase
    # twice in a row to reach an empty vault.
    V.KEYS.put(_token(request), _guard(V.unlock, body.passphrase, db_path=_db()))
    return {"ok": True,
            "recovery_hint": "No recovery code exists for this vault. Run "
                             "`wm vault recovery-code --regenerate` on the server "
                             "to mint one — without it, forgetting the passphrase "
                             "is permanent.",
            **V.status(token=_token(request), db_path=_db())}


@router.post("/unlock")
def vault_unlock(request: Request, body: Passphrase):
    """THE READ PATH. `V.unlock` derives scrypt(passphrase) and unwraps the
    passphrase wrap alone — a wrong passphrase fails the AES-GCM tag and
    there is no second wrap here to fall back on."""
    try:
        key = V.unlock(body.passphrase, db_path=_db())
    except V.VaultError as e:
        raise HTTPException(401, str(e))
    V.KEYS.put(_token(request), key)
    return V.status(token=_token(request), db_path=_db())


@router.post("/lock")
def vault_lock(request: Request):
    V.KEYS.drop(_token(request))
    return V.status(token=_token(request), db_path=_db())


@router.post("/passphrase")
def vault_passphrase(request: Request, body: NewPassphrase):
    """Change the passphrase. The DATA key is unchanged, so every sealed row
    stays readable and the recovery code keeps working untouched."""
    try:
        key = V.unlock(body.passphrase, db_path=_db())
    except V.VaultError as e:
        raise HTTPException(401, str(e))
    _guard(V.set_passphrase, key, body.new_passphrase, db_path=_db())
    V.KEYS.put(_token(request), key)
    return {"ok": True}


@router.get("/notes")
def vault_notes(request: Request):
    """Locked: id + dates, no title — the lock-icon-and-a-date row. Unlocked:
    titles decrypted for this session."""
    key = V.KEYS.get(_token(request))
    return {"locked": key is None,
            "notes": _guard(V.list_notes, key, db_path=_db())}


@router.post("/notes")
def vault_create(request: Request, body: VaultNoteIn):
    key = _key(request)
    nid = _guard(V.create_note, body.title, body.body, key, db_path=_db())
    return {"id": nid, "note": _guard(V.get_note, nid, key, db_path=_db())}


@router.get("/note/{note_id}")
def vault_note(request: Request, note_id: int):
    n = _guard(V.get_note, note_id, _key(request), db_path=_db())
    if n is None:
        raise HTTPException(404, "no such vault note")
    return n


@router.post("/note/{note_id}/edit")
def vault_edit(request: Request, note_id: int, body: VaultNoteEdit):
    key = _key(request)
    return {"note": _guard(V.update_note, note_id, key, title=body.title,
                           body=body.body, db_path=_db())}


@router.post("/note/{note_id}/entries")
def vault_entry(request: Request, note_id: int, body: EntryIn):
    key = _key(request)
    _guard(V.add_entry, note_id, body.body, key, db_path=_db())
    return {"note": _guard(V.get_note, note_id, key, db_path=_db())}


@router.post("/note/{note_id}/attachments")
async def vault_attach(request: Request, note_id: int, kind: str = Form("photo"),
                       file: UploadFile = File(...)):
    key = _key(request)
    data, size = [], 0
    while True:
        chunk = await file.read(1 << 20)
        if not chunk:
            break
        size += len(chunk)
        if size > UPLOAD_MAX_BYTES:
            raise HTTPException(413, "attachments up to %d MB"
                                % (UPLOAD_MAX_BYTES // 1_000_000))
        data.append(chunk)
    if not size:
        raise HTTPException(400, "empty file")
    _guard(V.add_attachment, note_id, b"".join(data), key, kind=kind,
           mime=file.content_type, name=file.filename, db_path=_db())
    return {"note": _guard(V.get_note, note_id, key, db_path=_db())}


@router.get("/attachment/{attachment_id}")
def vault_attachment(request: Request, attachment_id: int):
    """Decrypted in memory and handed straight back — a vault attachment is
    never written to disk in the clear, not even to a temp file."""
    got = _guard(V.read_attachment, attachment_id, _key(request), db_path=_db())
    if got is None:
        raise HTTPException(404, "no such vault attachment")
    data, mime, name = got
    headers = {"Cache-Control": "no-store"}
    if name:
        headers["Content-Disposition"] = 'inline; filename="%s"' % os.path.basename(
            name).replace('"', "")
    return Response(content=data, media_type=mime or "application/octet-stream",
                    headers=headers)


class MoveIn(BaseModel):
    note_id: int


@router.post("/move")
def vault_move(request: Request, body: MoveIn):
    """Move-to-vault: the ONE carve-out from archive-only. Irreversible — the
    note's plaintext leaves `notes`, `notes_fts` and every `note_revisions`
    snapshot in a single transaction, after the ciphertext has been read back.
    """
    key = _key(request)
    res = _guard(V.move_note_to_vault, body.note_id, key, db_path=_db())
    return {"ok": True, **res,
            "note": _guard(V.get_note, res["vault_note_id"], key, db_path=_db())}


@router.get("/audit")
def vault_audit(request: Request):
    """Readable while LOCKED on purpose: it holds no secret — a title is a
    sha256 — and it is the owner's proof that a destruction happened."""
    return {"audit": V.list_audit(db_path=_db())}
