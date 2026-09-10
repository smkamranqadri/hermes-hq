"""Second Brain Phase 4 slice B — the private vault.

Every line of the owner's DoD is a test here, because the claims this slice
makes are unusually load-bearing: it introduces the FIRST irreversible delete
path this system has ever had, and it tells the owner that a librarian which
runs arbitrary `wm` commands cannot see a vault row.

The two tests that matter most:

  * `test_agent_denial_is_structural` ENUMERATES every registered API route and
    every `wm note` CLI verb rather than spot-checking a few, so a route or a
    verb added later fails this test instead of quietly gaining vault reach.
  * `test_no_http_route_accepts_or_returns_the_recovery_code` proves the
    separation that makes the passphrase real. Recovery that the web path
    could invoke would make the passphrase decoration.

Isolation: `tests/backend/conftest.py` clears the engine env overrides for
every test (a suite run inside a dispatched agent once wrote 33 phantom rows
into the owner's LIVE library), and every store call below passes `db_path`
explicitly. The quarantine drain runs against FIXTURE rows in tmp_path and
never against real data.
"""
import hashlib
import json
import os
import sqlite3
import sys
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest

PW = "correct horse battery staple"
MARKER = "ZZVAULTMARKERZZ"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A scratch hq_home. No key file is involved anywhere any more — both
    wraps live in `vault_meta` inside hq.db."""
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    monkeypatch.setenv("HERMES_HQ_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    os.makedirs(tmp_path / "alpha", exist_ok=True)
    store.create_project("alpha", "Alpha", "", str(tmp_path / "alpha"),
                         db_path=store.DEFAULT_DB_PATH)
    from backend import vault as V
    V.KEYS.clear()
    yield store, store.DEFAULT_DB_PATH, tmp_path
    V.KEYS.clear()


@pytest.fixture()
def api(env):
    store, db, tmp = env
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        yield c, store, db, tmp


def login(c):
    r = c.post("/api/login", json={"password": "pw-test"})
    assert r.status_code == 200
    return {"x-csrf": r.json()["csrf"]}


def db_bytes(db):
    """The DB file AND its -wal: a "destroyed" secret that only left the main
    file is still on disk in the write-ahead log."""
    out = open(db, "rb").read()
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db + suffix):
            out += open(db + suffix, "rb").read()
    return out


def area(store, db, name="Work"):
    return next(a["id"] for a in store.list_areas(db_path=db) if a["name"] == name)


def flat_routes(app):
    """Every route on the app, recursing into included routers.

    FastAPI 0.141 keeps `include_router` results as `_IncludedRouter` wrappers
    rather than flattening them into `app.routes`, so a naive walk sees 21
    routes instead of 188 — and an enumeration that misses 167 routes is not
    an enumeration. Asserted non-vacuous by every caller.
    """
    out = []

    def walk(routes):
        for r in routes:
            sub = getattr(r, "original_router", None)
            if sub is not None:
                walk(sub.routes)
            else:
                out.append(r)

    walk(app.routes)
    return out


# ---------------------------------------------------------------------------
# passphrase: round trip, refusal, session scope
# ---------------------------------------------------------------------------

def test_round_trip_and_wrong_passphrase_refused(env):
    store, db, tmp = env
    from backend import vault as V
    assert V.is_initialized(db_path=db) is False
    V.init_vault(PW, db_path=db)
    assert V.is_initialized(db_path=db) is True
    key = V.unlock(PW, db_path=db)
    nid = V.create_note("Bank PIN", "1234 " + MARKER, key, db_path=db)
    assert V.get_note(nid, key, db_path=db)["body"] == "1234 " + MARKER

    with pytest.raises(V.VaultError) as e:
        V.unlock(PW + "x", db_path=db)
    assert "wrong passphrase" in str(e.value)
    # One character off derives a different scrypt key, so the AES-GCM tag
    # fails — there is no partial answer and no oracle beyond "wrong".
    with pytest.raises(V.VaultError):
        V.unlock("", db_path=db)
    # A second init is refused: re-minting the data key would orphan every
    # sealed row rather than "resetting" the vault.
    with pytest.raises(V.VaultError):
        V.init_vault(PW, db_path=db)


def test_locked_list_shows_no_title(api):
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    c.post("/api/vault/notes", json={"title": MARKER + " title", "body": "b"}, headers=h)

    c.post("/api/vault/lock", headers=h)
    r = c.get("/api/vault/notes").json()
    assert r["locked"] is True
    assert len(r["notes"]) == 1
    row = r["notes"][0]
    assert row["title"] is None                    # a lock icon and a date
    assert row["locked"] is True
    assert set(row) == {"id", "kind", "created_at", "updated_at", "entry_count",
                        "attachment_count", "locked", "title"}
    # nothing in the locked payload carries a byte of the note
    assert MARKER not in json.dumps(r)
    # and the single-note route is locked too
    assert c.get("/api/vault/note/%d" % row["id"]).status_code == 423

    c.post("/api/vault/unlock", json={"passphrase": PW}, headers=h)
    r = c.get("/api/vault/notes").json()
    assert r["locked"] is False
    assert r["notes"][0]["title"] == MARKER + " title"


def test_unlock_is_scoped_to_one_session(api):
    """Unlocking on the desktop must not unlock the phone. The key is held per
    session token, so a second logged-in session stays sealed."""
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    c.post("/api/vault/notes", json={"title": "t", "body": "b"}, headers=h)
    assert c.get("/api/vault/notes").json()["locked"] is False

    from fastapi.testclient import TestClient
    with TestClient(c.app) as other:
        login(other)
        assert other.get("/api/vault/status").json()["unlocked"] is False
        assert other.get("/api/vault/notes").json()["locked"] is True
        assert other.get("/api/vault/notes").json()["notes"][0]["title"] is None


def test_wrong_passphrase_refused_over_http(api):
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    c.post("/api/vault/lock", headers=h)
    r = c.post("/api/vault/unlock", json={"passphrase": "not it at all"}, headers=h)
    assert r.status_code == 401
    assert c.get("/api/vault/status").json()["unlocked"] is False


def test_vault_requires_the_owner_session(api):
    """The passphrase is the SECOND wall, not the first: without a cookie the
    vault routes are 401 like every other /api route."""
    c, store, db, tmp = api
    assert c.get("/api/vault/status").status_code == 401
    assert c.get("/api/vault/notes").status_code == 401
    assert c.post("/api/vault/unlock", json={"passphrase": PW}).status_code == 401


# ---------------------------------------------------------------------------
# the unlocked key never reaches disk
# ---------------------------------------------------------------------------

def test_data_key_never_enters_the_session_file(api):
    """`auth.Sessions._save()` json-dumps the session dict to sessions.json
    (0600, but on disk). A data key parked in `request.state.session` would be
    written out on the next save, so unlocked keys live in `vault.KEYS`, a
    process-memory map. This test forces a save and greps the file."""
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    from backend import vault as V
    key = V.unlock(PW, db_path=db)

    sessions = c.app.state.sessions
    sessions.create()                       # forces _save() to disk
    raw = open(sessions.path, "rb").read()
    assert key not in raw
    assert key.hex().encode() not in raw
    import base64
    assert base64.b64encode(key) not in raw
    assert PW.encode() not in raw
    # and the session dict itself holds only what auth put there
    for sess in json.loads(raw).values():
        assert set(sess) == {"csrf", "created", "seen"}


def test_logout_reseals_the_vault(api):
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    assert c.get("/api/vault/status").json()["unlocked"] is True
    c.post("/api/logout", headers=h)
    h = login(c)
    assert c.get("/api/vault/status").json()["unlocked"] is False


def test_idle_timeout_reseals(api, monkeypatch):
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    from backend import vault as V
    assert c.get("/api/vault/status").json()["unlocked"] is True
    monkeypatch.setattr(V.KEYS, "idle", -1)      # every key is now stale
    assert c.get("/api/vault/status").json()["unlocked"] is False
    assert c.get("/api/vault/notes").json()["locked"] is True


# ---------------------------------------------------------------------------
# structural agent denial — ENUMERATED, not spot-checked
# ---------------------------------------------------------------------------

VAULT_TABLES = ("vault_notes", "vault_entries", "vault_attachments",
                "vault_meta", "vault_audit")

# The ONLY module allowed to run SQL against a vault table. `core/wm_store.py`
# owns the DDL and nothing else; the allowlist below records why each other
# mention of the word "vault" exists.
CRYPTO_MODULE = os.path.join(ROOT, "backend", "vault.py")


def _sql_touching_vault(src):
    """SQL verbs against a vault table, ignoring the DDL that creates them."""
    import re
    hits = []
    for m in re.finditer(r"(?i)\b(from|into|update|join|table)\s+(vault_\w+)", src):
        verb, table = m.group(1).lower(), m.group(2)
        if verb == "table":
            continue                          # CREATE TABLE / DROP TABLE: DDL
        hits.append("%s %s" % (verb.upper(), table))
    return hits


# Which modules may name `backend.vault` at all, and why. A grep for SQL text
# is NOT sufficient: the reviewer added a route calling
# `V.list_notes(V.KEYS.get(token))` — no SQL string anywhere — and the SQL
# enumeration passed it. Reaching the vault through the crypto module's own
# API is the easier mistake to make, so the IMPORT is what gets policed.
VAULT_IMPORTERS = {
    "backend/vault_api.py": "the vault's own router — the only HTTP surface",
    "backend/writes.py": "logout drops the session key (KEYS.drop) and nothing else",
    "core/wm_cli.py": "the `wm vault` CLI group, including recovery-code recovery",
}


def _python_sources():
    """(relpath, source) for EVERY .py under backend/ and core/, recursively.

    The walk used to be `os.listdir` on the two top-level directories, so a
    future `backend/foo/bar.py` would have been invisible to all of these
    enumerations (round-2 review).
    """
    out = []
    for pkg in ("backend", "core"):
        for root, dirs, files in os.walk(os.path.join(ROOT, pkg)):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "static")]
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                out.append((os.path.relpath(path, ROOT), open(path).read()))
    assert len(out) > 25, "the source walk found only %d modules" % len(out)
    return out


def _imports_vault(src):
    import re
    # `backend.vault_api` must NOT match: \b after "vault" fails on "_".
    return bool(re.search(r"(from\s+backend\s+import\s+vault\b"
                          r"|from\s+backend\.vault\s+import"
                          r"|import\s+backend\.vault\b)", src))


def test_only_named_modules_may_import_the_crypto_module(env):
    """The reach test the SQL grep cannot make.

    Anything that can `import backend.vault` can call `V.list_notes(key)` and
    read the whole vault without writing a line of SQL. This asserts the set of
    modules that can do so is EXACTLY the three above — a new importer fails
    here and has to be justified by editing the allowlist.
    """
    found = {}
    for rel, src in _python_sources():
        if rel == "backend/vault.py":
            continue                           # it IS the module
        if _imports_vault(src):
            found[rel] = True
    assert set(found) == set(VAULT_IMPORTERS), (
        "vault importers changed: %r (allowlist: %r)" % (sorted(found), sorted(VAULT_IMPORTERS)))

    # And the one non-router importer in backend/ may only drop a key.
    import re
    w = open(os.path.join(ROOT, "backend", "writes.py")).read()
    uses = set(re.findall(r"\bV\.(\w+)", w))
    assert uses == {"KEYS"}, "backend/writes.py uses more of the vault than KEYS.drop: %r" % uses
    assert "KEYS.drop" in w


def test_only_the_crypto_module_can_query_a_vault_table(env):
    """Walk the WHOLE backend+core tree. If any module other than
    backend/vault.py grows a SELECT/INSERT/UPDATE against a vault table, this
    fails — which is the point: denial that depends on nobody adding a query
    later is not structural."""
    offenders = {}
    for rel, src in _python_sources():
        if rel == "backend/vault.py":
            continue
        hits = _sql_touching_vault(src)
        if hits:
            offenders[rel] = hits
    assert offenders == {}, "vault SQL outside backend/vault.py: %r" % offenders


def test_agent_denial_is_structural_for_every_route(api):
    """Enumerate every registered route on the real app.

    Two claims, both enumerated rather than sampled:
      1. every route that can reach vault data is under /api/vault, and that
         set is exactly the routes this slice declares — a new one is a
         deliberate act that fails this test until it is listed here;
      2. no route OUTSIDE /api/vault is served from a module that can query a
         vault table (checked against the whole tree above).
    """
    c, store, db, tmp = api
    routes = [r for r in flat_routes(c.app) if getattr(r, "methods", None)
              and getattr(r, "path", "").startswith("/api")]
    assert len(routes) > 100, ("only %d API routes found — the enumeration "
                               "would pass vacuously" % len(routes))

    vault_paths = sorted({r.path for r in routes if r.path.startswith("/api/vault")})
    assert vault_paths == [
        "/api/vault/attachment/{attachment_id}",
        "/api/vault/audit",
        "/api/vault/init",
        "/api/vault/lock",
        "/api/vault/move",
        "/api/vault/move-impact/{note_id}",
        "/api/vault/note/{note_id}",
        "/api/vault/note/{note_id}/attachments",
        "/api/vault/note/{note_id}/edit",
        "/api/vault/note/{note_id}/entries",
        "/api/vault/notes",
        "/api/vault/passphrase",
        "/api/vault/status",
        "/api/vault/unlock",
    ], vault_paths

    for r in routes:
        mod = getattr(r.endpoint, "__module__", "")
        if r.path.startswith("/api/vault"):
            assert mod == "backend.vault_api", (r.path, mod)
            continue
        # THE CONVERSE, which round-2 review showed was missing: a route
        # DEFINED in vault_api under a different prefix satisfied every check
        # while returning the decrypted vault. vault_api owns /api/vault and
        # nothing else.
        assert mod != "backend.vault_api", (
            "backend/vault_api.py serves %s, which is outside /api/vault" % r.path)
        # Every other route: its module must not be able to query a vault table.
        src_path = os.path.join(ROOT, *mod.split(".")) + ".py"
        if os.path.isfile(src_path):
            assert _sql_touching_vault(open(src_path).read()) == [], (r.path, mod)


# Routes that must NOT be exercised blindly: they hang waiting on a gateway,
# drop the session mid-sweep, or reach outside the test process. Each is
# justified, and the test asserts this list stays small relative to what IS
# swept — an ever-growing deny list would hollow the sweep out.
SWEEP_DENY_PREFIXES = (
    "/api/chat/",          # streams from a gateway that is not running: hangs
    "/api/terminal",       # spawns a pty
    "/api/service",        # supervisor: restart/update the real service
    "/api/agents/install", # writes an agent tree on the host
    "/api/mcp/",           # spawns MCP servers
    "/api/jobs/",          # process control
    "/api/gateways",       # starts/stops gateways
)
SWEEP_DENY_EXACT = {
    "/api/logout",                      # would drop the session mid-sweep
    "/api/system/restart", "/api/system/update",
    "/api/agent/{name}/gateway",        # starts a gateway process
}


def _sweepable(path):
    if path.startswith("/api/vault"):
        return False
    if path in SWEEP_DENY_EXACT:
        return False
    return not any(path.startswith(pre) for pre in SWEEP_DENY_PREFIXES)


def test_no_non_vault_route_can_see_a_vault_note(api, monkeypatch):
    """The runtime half of the enumeration, and the one that does not care HOW
    a module got hold of the crypto.

    Round-2 review defeated the static tests two ways: a route defined inside
    `backend/vault_api.py` under a different prefix, and
    `importlib.import_module("backend" + ".vault")` from a module that is not
    on the allowlist. Both are invisible to a source grep. Neither can hide
    from a SPY on the unseal primitive: every path from ciphertext to words
    goes through it, however the module was obtained.

    The sweep also had a hole of its own — it skipped `"{" in path` and
    anything that was not a GET, so a POST route or a parameterised GET
    returning the whole vault passed all four tests. Both are exercised now.
    """
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    vid = c.post("/api/vault/notes", json={"title": MARKER, "body": MARKER + " body"},
                 headers=h).json()["id"]
    lid = store.create_note("ordinary note", "ordinary body", db_path=db)
    assert c.get("/api/vault/status").json()["unlocked"] is True

    from backend import vault as V
    spy = []
    real_unseal, real_list = V.unseal, V.list_notes
    monkeypatch.setattr(V, "unseal", lambda *a, **k: (spy.append("unseal"), real_unseal(*a, **k))[1])
    monkeypatch.setattr(V, "list_notes", lambda *a, **k: (spy.append("list"), real_list(*a, **k))[1])

    def fill(path):
        # a planted id for every parameter: the vault note's own id where it
        # could plausibly be one, so an id-confusion leak would show.
        out = path.replace("{note_id}", str(lid))
        while "{" in out:
            i, j = out.index("{"), out.index("}")
            name = out[i + 1:j]
            out = out[:i] + (str(vid) if name.endswith("id") else "1") + out[j + 1:]
        return out

    checked, skipped, denied = 0, [], 0
    for r in flat_routes(c.app):
        path = getattr(r, "path", "")
        methods = getattr(r, "methods", set())
        if not path.startswith("/api"):
            continue
        if not _sweepable(path):
            denied += len(methods & {"GET", "POST"})
            continue
        for method in ("GET", "POST"):
            if method not in methods:
                continue
            try:
                resp = c.get(fill(path)) if method == "GET" \
                    else c.post(fill(path), json={}, headers=h)
            except Exception:
                skipped.append("%s %s" % (method, path))
                continue
            checked += 1
            assert MARKER not in resp.text, (method, path, resp.text[:200])

    assert checked > 130, "only %d route calls exercised" % checked
    # The deny list is a real gap and is named as one: these routes stream from
    # a gateway, spawn a process or drop the session, so they cannot be called
    # blindly here. They are covered by the static enumerations and by the spy
    # below (which would fire from ANY handler, swept or not). Held to a third
    # of the swept surface so the list cannot quietly grow to hollow this out.
    assert denied < checked / 3, (
        "deny list covers %d route-methods against %d swept" % (denied, checked))
    assert len(skipped) < checked / 4, (checked, skipped)
    # THE assertion: nothing outside /api/vault touched the crypto at all.
    assert spy == [], "a non-vault route reached into the vault: %r" % spy[:5]

    # and the sweep really can see a leak — the same primitive, through the
    # vault's own route, fires the spy
    assert c.get("/api/vault/notes").status_code == 200
    assert spy, "the spy never fires, so its silence above proved nothing"


def test_wm_note_cli_group_cannot_reach_the_vault(env, capsys):
    """Enumerate the librarian's ENTIRE CLI surface.

    `wm note` is the only write surface the librarian has. Every verb in it is
    run here against a library that holds a vault note, and none of them may
    print a byte of it. The verbs are read off the parser, so a verb added
    later is covered without editing this test.
    """
    store, db, tmp = env
    from core import wm_cli
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    # The marker alone is not the assertion target: `wm note search <q>` ECHOES
    # the query, so searching FOR the marker would trip a naive check on the
    # CLI's own "No notes match 'X'" line. What must never appear is the vault
    # note's CONTENT.
    secret_title = MARKER + " Fakebank login"
    secret_body = MARKER + " the password is hunter2"
    V.create_note(secret_title, secret_body, key, db_path=db)
    # a decoy in the ordinary library, so the verbs have real work to print
    store.create_note("ordinary capture", "nothing secret here", db_path=db)

    note_parser = wm_cli.build_parser()._subparsers._group_actions[0].choices["note"]
    verbs = sorted(note_parser._subparsers._group_actions[0].choices)
    assert verbs, "no `wm note` verbs found — the enumeration would pass vacuously"

    # No verb's implementation may name a vault table at all.
    src = open(os.path.join(ROOT, "core", "wm_cli.py")).read()
    for verb in verbs:
        fn = note_parser._subparsers._group_actions[0].choices[verb].get_default("fn")
        import inspect
        body = inspect.getsource(fn)
        for t in VAULT_TABLES:
            assert t not in body, "`wm note %s` names %s" % (verb, t)

    # And the read-only verbs actually run without ever printing the secret —
    # including the librarian actively HUNTING for it by name.
    ran, hunted = 0, 0
    for argv in (["note", "inbox"], ["note", "show", "1"], ["note", "areas"],
                 ["note", "tags"], ["note", "lint"], ["note", "proposals"],
                 ["note", "search", MARKER], ["note", "search", "Fakebank"],
                 ["note", "search", "hunter2"], ["note", "search", "login"]):
        capsys.readouterr()
        wm_cli.main(argv)
        out = capsys.readouterr().out
        assert secret_title not in out, (argv, out[:300])
        assert secret_body not in out, (argv, out[:300])
        if argv[:2] == ["note", "search"]:
            # `wm note search` echoes the query, so the assertion is that the
            # output is the empty-result form and carries no result row at all.
            assert out.strip() == "No notes match %r." % argv[2], (argv, out[:300])
            hunted += 1
        ran += 1
    assert ran == 10 and hunted == 4
    # The id spaces are SEPARATE: vault note #1 and library note #1 both exist
    # here, and `wm note show 1` resolves to the library one. There is no id a
    # librarian can pass to a `wm note` verb that reaches across.
    from backend import vault as V2
    assert V2.list_notes(key, db_path=db)[0]["id"] == 1
    assert store.get_note(1, db_path=db)["title"] == "ordinary capture"
    capsys.readouterr()
    wm_cli.main(["note", "show", "1"])
    out = capsys.readouterr().out
    assert "ordinary capture" in out
    assert secret_title not in out and secret_body not in out


def test_search_and_list_and_related_cannot_see_a_vault_note(env):
    """The store-level half: the four functions the librarian reads through
    all query `notes`, and a vault row is not in `notes`."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    vid = V.create_note(MARKER + " secret", MARKER + " body", key, db_path=db)

    assert store.search_notes(MARKER, db_path=db) == []
    assert store.list_notes(db_path=db) == []
    assert store.get_note(vid, db_path=db) is None       # ids do not even collide
    assert store.list_proposals(db_path=db) == []
    lint = store.lint_library(db_path=db)
    assert MARKER not in json.dumps(lint)
    assert store.notes_tree(db_path=db)["counts"].get("inbox", 0) == 0

# ---------------------------------------------------------------------------
# the recovery code is never on the read path, and never on the WEB at all
# ---------------------------------------------------------------------------
#
# The original design put a server-held key FILE outside hq_home at 0600. It
# was dropped on 2026-09-10 after the coordinator settled the question
# empirically: /opt/data is a virtiofs mount whose ownership is remapped per
# viewer, so an agent run (uid 10000) sees a root-owned 0600 file there as its
# own and reads it. No file mode under that mount is a boundary. The
# replacement is a high-entropy code printed once and stored nowhere —
# possession of it IS the credential, which is exactly why no HTTP route may
# accept one.

def test_recovery_code_opens_the_vault_with_the_passphrase_unknown(env):
    store, db, tmp = env
    from backend import vault as V
    code = V.init_vault(PW, db_path=db)["recovery_code"]
    assert code and len(V.normalize_recovery_code(code)) >= 32      # >= 160 bits
    key = V.unlock(PW, db_path=db)
    V.create_note("Bank PIN", MARKER, key, db_path=db)

    got = V.unwrap_recovery(code, db_path=db)
    assert got == key
    assert V.list_notes(got, db_path=db)[0]["title"] == "Bank PIN"
    # retyped however the owner has it: spaces, no dashes, any case
    assert V.unwrap_recovery(code.replace("-", " ").lower(), db_path=db) == got
    assert V.unwrap_recovery(code.replace("-", ""), db_path=db) == got
    with pytest.raises(V.VaultError) as e:
        V.unwrap_recovery(V.new_recovery_code(), db_path=db)
    assert "does not open this vault" in str(e.value)


def test_the_recovery_code_is_in_zero_bytes_of_the_db_or_a_backup(env):
    """It is never stored — only the wrap it opens is. A raw code anywhere on
    disk would be a second passphrase lying in the open."""
    store, db, tmp = env
    from backend import vault as V
    code = V.init_vault(PW, db_path=db)["recovery_code"]
    key = V.unlock(PW, db_path=db)
    V.create_note("Bank PIN", MARKER, key, db_path=db)

    raw = V.normalize_recovery_code(code)
    for form in (code.encode(), raw.encode(), code.lower().encode(), raw.lower().encode()):
        assert db_bytes(db).count(form) == 0, form
    dest = store.backup_db(db_path=db, backup_dir=str(tmp / "backups"))
    for form in (code.encode(), raw.encode()):
        assert open(dest, "rb").read().count(form) == 0, form
    conn = sqlite3.connect(db)
    try:
        blob = " ".join(str(r[0]) for r in conn.execute("SELECT detail FROM activity"))
        assert raw not in V.normalize_recovery_code(blob)
        aud = " ".join(str(r[0]) for r in conn.execute("SELECT detail FROM vault_audit"))
        assert raw not in V.normalize_recovery_code(aud)
    finally:
        conn.close()
    # the WRAP is stored — recovery has to survive a restore
    assert V.status(db_path=db)["has_recovery_code"] is True


def test_init_is_the_only_route_that_returns_a_code_and_none_accepts_one(api, monkeypatch):
    """The owner's 2026-09-10 reversal, enumerated exactly.

    `POST /api/vault/init` returns a code, at creation, once — it appears in
    the same authenticated browser where the passphrase was just typed, so it
    is exposed to nothing the passphrase is not, and withholding it left a
    phone-created vault permanently unrecoverable.

    Everything else is unchanged and still hard: no route ACCEPTS a code
    (possession opens the vault outright), and no other route returns one.
    """
    c, store, db, tmp = api
    h = login(c)
    from backend import vault as V

    calls = []
    real = V.unwrap_recovery
    monkeypatch.setattr(V, "unwrap_recovery",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    r = c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    assert r.status_code == 200
    body = r.json()
    code = body["recovery_code"]
    assert code and V.unwrap_recovery(code, db_path=db) == V.unlock(PW, db_path=db)
    assert body["has_recovery_code"] is True
    calls.clear()

    # ...and NOTHING else returns it.
    nid = c.post("/api/vault/notes", json={"title": "t", "body": "b"},
                 headers=h).json()["id"]
    lid = store.create_note("library note", "body", db_path=db)
    seen = []
    for call in (
        lambda: c.get("/api/vault/status"),
        lambda: c.get("/api/vault/notes"),
        lambda: c.get("/api/vault/audit"),
        lambda: c.get("/api/vault/note/%d" % nid),
        lambda: c.get("/api/vault/move-impact/%d" % lid),
        lambda: c.post("/api/vault/note/%d/edit" % nid, json={"body": "b2"}, headers=h),
        lambda: c.post("/api/vault/note/%d/entries" % nid, json={"body": "x"}, headers=h),
        lambda: c.post("/api/vault/move", json={"note_id": lid}, headers=h),
        lambda: c.post("/api/vault/lock", headers=h),
        lambda: c.post("/api/vault/unlock", json={"passphrase": PW}, headers=h),
        lambda: c.post("/api/vault/passphrase",
                       json={"passphrase": PW, "new_passphrase": PW + "22"}, headers=h),
    ):
        seen.append(call().text)
    raw = V.normalize_recovery_code(code)
    for text in seen:
        assert code not in text
        assert raw not in V.normalize_recovery_code(text)
    assert calls == [], "an HTTP handler reached the recovery unwrap"

    # No route may ACCEPT a code, in any shape: posting one everywhere must
    # never unlock anything.
    c.post("/api/vault/lock", headers=h)
    for path in ("/api/vault/unlock", "/api/vault/passphrase", "/api/vault/move",
                 "/api/vault/init", "/api/vault/notes"):
        for payload in ({"recovery_code": code}, {"code": code}, {"passphrase": code}):
            c.post(path, json=payload, headers=h)
    assert c.get("/api/vault/status").json()["unlocked"] is False
    assert calls == []

    # Static half: only vault_api may name the recovery API, and only to
    # return it from init.
    for rel, src in _python_sources():
        if rel in ("backend/vault.py", "core/wm_cli.py", "backend/vault_api.py"):
            continue
        for word in ("unwrap_recovery", "new_recovery_code", "regenerate_recovery_code",
                     "recovery_wrap", "normalize_recovery_code", "recovery_code"):
            assert word not in src, (rel, word)
    api_src = open(os.path.join(ROOT, "backend", "vault_api.py")).read()
    for word in ("unwrap_recovery", "new_recovery_code", "regenerate_recovery_code",
                 "recovery_wrap", "normalize_recovery_code"):
        assert word not in api_src, word
    # vault_api may touch `recovery_code` ONLY inside vault_init. Located by
    # function body rather than by a raw count, so the assertion says what it
    # means: no other handler in this file can see a code.
    import ast as _ast
    for node in _ast.walk(_ast.parse(api_src)):
        if isinstance(node, _ast.FunctionDef) and node.name != "vault_init":
            body = _ast.get_source_segment(api_src, node) or ""
            assert "recovery_code" not in body, node.name


def test_a_vault_with_no_recovery_wrap_still_works_and_says_so(env):
    """Vaults created before the reversal have no recovery wrap. They must
    keep working, refuse recovery with a message that names the remedy, and
    report `has_recovery_code: false` so the UI can warn."""
    store, db, tmp = env
    from backend import vault as V
    res = V.init_vault(PW, db_path=db, with_recovery=False)
    assert res["recovery_code"] is None
    assert V.status(db_path=db)["has_recovery_code"] is False
    key = V.unlock(PW, db_path=db)                    # the vault itself is fine
    V.create_note("still works", "body", key, db_path=db)

    with pytest.raises(V.VaultError) as e:
        V.unwrap_recovery(V.new_recovery_code(), db_path=db)
    assert "no recovery code" in str(e.value)
    assert "wm vault recovery-code --regenerate" in str(e.value)

    # and regenerate rescues it
    code = V.regenerate_recovery_code(key, db_path=db)
    assert V.status(db_path=db)["has_recovery_code"] is True
    assert V.unwrap_recovery(code, db_path=db) == key


def test_regenerate_invalidates_the_previous_code(env, capsys):
    store, db, tmp = env
    from backend import vault as V
    from core import wm_cli
    old_code = V.init_vault(PW, db_path=db)["recovery_code"]
    key = V.unlock(PW, db_path=db)
    V.create_note("Bank PIN", MARKER, key, db_path=db)
    assert V.unwrap_recovery(old_code, db_path=db) == key

    new_code = V.regenerate_recovery_code(key, db_path=db)
    assert new_code != old_code
    assert V.unwrap_recovery(new_code, db_path=db) == key
    with pytest.raises(V.VaultError):
        V.unwrap_recovery(old_code, db_path=db)
    # every sealed row still opens: the DATA key never changed
    assert V.list_notes(key, db_path=db)[0]["title"] == "Bank PIN"
    assert db_bytes(db).count(V.normalize_recovery_code(new_code).encode()) == 0

    # through the CLI, which is the only thing that ever prints one
    capsys.readouterr()
    assert wm_cli.main(["vault", "recovery-code", "--regenerate", "--passphrase", PW]) == 0
    out = capsys.readouterr().out
    assert "RECOVERY CODE" in out and "shown this once" in out
    printed = [l.strip() for l in out.splitlines() if l.strip().count("-") >= 4]
    assert printed, out
    assert V.unwrap_recovery(printed[0], db_path=db) == key
    with pytest.raises(V.VaultError):
        V.unwrap_recovery(new_code, db_path=db)

    # without --regenerate it reports, and cannot re-display the code
    capsys.readouterr()
    assert wm_cli.main(["vault", "recovery-code", "--passphrase", PW]) == 0
    report = capsys.readouterr().out
    assert "EXISTS" in report and "cannot be shown again" in report
    assert V.normalize_recovery_code(printed[0]) not in V.normalize_recovery_code(report)


def test_cli_recover_opens_the_vault_and_can_reset_the_passphrase(env, capsys):
    store, db, tmp = env
    from backend import vault as V
    from core import wm_cli
    code = V.init_vault("a passphrase nobody will remember", db_path=db)["recovery_code"]
    key = V.unlock("a passphrase nobody will remember", db_path=db)
    V.create_note("Recovered title", "body", key, db_path=db)

    capsys.readouterr()
    assert wm_cli.main(["vault", "recover", "--recovery-code", code]) == 0
    out = capsys.readouterr().out
    assert "Recovered title" in out and "recovery code" in out

    assert wm_cli.main(["vault", "recover", "--recovery-code", code,
                        "--set-passphrase", "brand new phrase"]) == 0
    key2 = V.unlock("brand new phrase", db_path=db)
    assert V.list_notes(key2, db_path=db)[0]["title"] == "Recovered title"
    with pytest.raises(V.VaultError):
        V.unlock("a passphrase nobody will remember", db_path=db)
    assert V.unwrap_recovery(code, db_path=db) == key2      # code survives the reset

    capsys.readouterr()
    assert wm_cli.main(["vault", "recover", "--recovery-code", V.new_recovery_code()]) == 1
    assert "does not open this vault" in capsys.readouterr().out


def test_there_is_no_key_file_anywhere(env):
    """The dropped design, pinned: no code path may write a key to disk. A
    file under /opt/data cannot be hidden from an agent run, so a server-held
    key file would have been a boundary that is not one."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    for root, _dirs, files in os.walk(str(tmp)):
        for f in files:
            assert "escrow" not in f.lower(), os.path.join(root, f)
            assert not f.endswith(".key"), os.path.join(root, f)
    src = open(os.path.join(ROOT, "backend", "vault.py")).read()
    for gone in ("escrow_key_path", "_read_escrow_key", "escrow_cli_gate",
                 "_create_escrow_key", "unwrap_escrow"):
        assert gone not in src, gone


def test_a_short_passphrase_is_refused_at_init_and_at_change(api):
    """12, not 8: hq.db sits on a mount anything on this box can read, so the
    passphrase is the whole defence in an offline attack."""
    c, store, db, tmp = api
    h = login(c)
    from backend import vault as V
    assert V.MIN_PASSPHRASE == 12

    r = c.post("/api/vault/init", json={"passphrase": "short"}, headers=h)
    assert r.status_code == 409 and "at least 12" in r.text
    assert c.post("/api/vault/init", json={"passphrase": "elevenchars"},
                  headers=h).status_code == 409          # 11 — one short
    assert V.is_initialized(db_path=db) is False

    assert c.post("/api/vault/init", json={"passphrase": "twelvechars!"},
                  headers=h).status_code == 200          # 12 — exactly enough
    r = c.post("/api/vault/passphrase",
               json={"passphrase": "twelvechars!", "new_passphrase": "tooshort"},
               headers=h)
    assert r.status_code == 409 and "at least 12" in r.text
    c.post("/api/vault/lock", headers=h)
    assert c.post("/api/vault/unlock", json={"passphrase": "twelvechars!"},
                  headers=h).status_code == 200


def test_vault_status_never_mentions_a_key_file(env, capsys):
    """The old status line advertised an escrow key path. There is no such
    file now, and status must say what recovery actually depends on."""
    store, db, tmp = env
    from backend import vault as V
    from core import wm_cli
    V.init_vault(PW, db_path=db)
    capsys.readouterr()
    wm_cli.main(["vault", "status"])
    out = capsys.readouterr().out
    assert "escrow" not in out.lower() and ".key" not in out
    assert "recovery code exists" in out
    assert "the passphrase, and nothing else" in out


# ---------------------------------------------------------------------------
# move-to-vault: the one carve-out from archive-only
# ---------------------------------------------------------------------------

def build_secret_note(store, db, marker=MARKER):
    """A note carrying the marker in EVERY plaintext place it can reach:
    title, body, a Thought, a revision snapshot, the FTS index, an activity
    row and a librarian proposal + its notification."""
    work = area(store, db)
    nid = store.create_note("%s plan" % marker, "%s body v1" % marker,
                            status="active", area_id=work, tags=["docker"], db_path=db)
    store.add_note_entry(nid, "%s thought" % marker, db_path=db)
    store.update_note(nid, body="%s body v2" % marker, db_path=db)   # snapshots v1
    store.create_proposal("file", nid, {"area_id": work, "tags": ["docker"]},
                          summary="file the %s plan" % marker, db_path=db)
    other = store.create_note("partner note", "x", status="active", area_id=work, db_path=db)
    store.link_note(nid, "note", other, db_path=db)      # a disputed-style pair
    store.link_note(other, "note", nid, db_path=db)      # and the other direction
    return nid


def test_move_to_vault_destroys_every_plaintext_trace(env):
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = build_secret_note(store, db)

    conn = sqlite3.connect(db)
    before = {
        "notes": conn.execute("SELECT COUNT(*) FROM notes WHERE id=?", (nid,)).fetchone()[0],
        "fts": conn.execute("SELECT COUNT(*) FROM notes_fts WHERE rowid=?", (nid,)).fetchone()[0],
        "revisions": conn.execute("SELECT COUNT(*) FROM note_revisions WHERE note_id=?", (nid,)).fetchone()[0],
    }
    conn.close()
    assert before == {"notes": 1, "fts": 1, "revisions": 1}, before
    assert db_bytes(db).count(MARKER.encode()) > 0, "the marker was never on disk"
    assert store.search_notes(MARKER, db_path=db)

    res = V.move_note_to_vault(nid, key, db_path=db)
    vid = res["vault_note_id"]

    # --- the three places the plan named, counted directly ---
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM notes WHERE id=?", (nid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM notes_fts WHERE rowid=?", (nid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM note_revisions WHERE note_id=?", (nid,)).fetchone()[0] == 0
        # FTS queried the way search does, not just by rowid
        assert conn.execute("SELECT COUNT(*) FROM notes_fts WHERE notes_fts MATCH ?",
                            (MARKER,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM note_entries WHERE note_id=?", (nid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM proposals WHERE note_id=?", (nid,)).fetchone()[0] == 0
        # --- the audit row proves it without preserving the secret ---
        a = conn.execute("SELECT * FROM vault_audit WHERE old_note_id=?", (nid,)).fetchone()
        assert a is not None
        conn.row_factory = sqlite3.Row
        a = conn.execute("SELECT * FROM vault_audit WHERE old_note_id=?", (nid,)).fetchone()
        assert a["action"] == "moved_in"
        assert a["vault_note_id"] == vid
        assert a["revisions"] == 1
        assert a["title_sha256"] == hashlib.sha256(("%s plan" % MARKER).encode()).hexdigest()
        assert MARKER not in (a["detail"] or "")
    finally:
        conn.close()
    assert V.verify_title_sha("%s plan" % MARKER, a["title_sha256"])

    # --- the byte-level proof: the file, not just the b-tree ---
    assert db_bytes(db).count(MARKER.encode()) == 0

    # --- and it is readable in the vault ---
    n = V.get_note(vid, key, db_path=db)
    assert n["title"] == "%s plan" % MARKER
    assert n["body"].startswith("%s body v2" % MARKER)
    assert [e["body"] for e in n["entries"]] == ["%s thought" % MARKER]
    # filing is NOT carried across (no area/tag/project in the vault schema);
    # what would have been lost silently is written into the body instead.
    assert "former tags: docker" in n["body"]
    assert "library note #%d" % nid in n["body"]
    assert store.search_notes(MARKER, db_path=db) == []


def test_a_fresh_backup_after_a_move_carries_no_plaintext(env):
    """The discipline that proved import quarantine: grep the REAL backup, not
    only the live DB."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = build_secret_note(store, db)
    V.move_note_to_vault(nid, key, db_path=db)
    dest = store.backup_db(db_path=db, backup_dir=str(tmp / "backups"))
    assert open(dest, "rb").read().count(MARKER.encode()) == 0


def test_move_carries_attachment_bytes_and_removes_the_plaintext_file(env):
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = store.create_note("photo note", "body", db_path=db)
    blob = (MARKER + " image bytes").encode() * 20
    att = store.add_attachment(nid, blob, kind="photo", mime="image/png",
                               orig_name="secret.png", db_path=db)
    path = store.attachment_path(nid, att["stored_name"])
    assert os.path.isfile(path)

    res = V.move_note_to_vault(nid, key, db_path=db)
    assert not os.path.exists(path), "the plaintext attachment survived on disk"
    n = V.get_note(res["vault_note_id"], key, db_path=db)
    assert len(n["attachments"]) == 1
    assert n["attachments"][0]["name"] == "secret.png"
    data, mime, name = V.read_attachment(n["attachments"][0]["id"], key, db_path=db)
    assert data == blob and mime == "image/png"
    # sealed in the DB, so the bytes are in the file but never in the clear
    assert db_bytes(db).count(MARKER.encode()) == 0


def test_a_failed_read_back_destroys_nothing(env, monkeypatch):
    """The ordering IS the safety argument: ciphertext written, read back, and
    only then the delete. Break the read-back and the note must survive whole."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = build_secret_note(store, db)

    monkeypatch.setattr(V, "get_note_in_conn",
                        lambda conn, nid_, k: {"title": "corrupted", "body": "",
                                               "entries": []})
    with pytest.raises(V.VaultError) as e:
        V.move_note_to_vault(nid, key, db_path=db)
    assert "nothing was destroyed" in str(e.value)

    # the whole transaction rolled back: note intact, FTS intact, NO orphan
    # vault row left behind
    assert store.get_note(nid, db_path=db) is not None
    assert store.search_notes(MARKER, db_path=db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM notes_fts WHERE rowid=?", (nid,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM note_revisions WHERE note_id=?", (nid,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM vault_notes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM vault_audit WHERE action='moved_in'").fetchone()[0] == 0
    finally:
        conn.close()


def test_move_refuses_while_locked(api):
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    nid = store.create_note("still here", MARKER, db_path=db)
    c.post("/api/vault/lock", headers=h)
    assert c.post("/api/vault/move", json={"note_id": nid}, headers=h).status_code == 423
    assert store.get_note(nid, db_path=db) is not None
    # and a note that does not exist is a 409, not a silent success
    c.post("/api/vault/unlock", json={"passphrase": PW}, headers=h)
    assert c.post("/api/vault/move", json={"note_id": 99999}, headers=h).status_code == 409


def test_vault_notes_carry_no_filing_columns(env):
    """Owner decision 6: a row you cannot read is a row you cannot file. This
    is a schema guard — someone "just adding a tag column" later fails here."""
    store, db, tmp = env
    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(vault_notes)")}
    finally:
        conn.close()
    assert cols == {"id", "title_enc", "body_enc", "kind", "created_at", "updated_at"}
    assert not (cols & {"area_id", "project_id", "tags", "status", "authored_by"})


# ---------------------------------------------------------------------------
# the export (slice A) cannot reach the vault
# ---------------------------------------------------------------------------

def zip_bytes(path):
    """Every member's decompressed bytes plus the container itself, so a
    marker cannot hide in a name, a comment or the manifest."""
    out = open(path, "rb").read()
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            out += name.encode() + z.read(name)
    return out


@pytest.mark.parametrize("unlocked", [False, True])
def test_export_contains_no_vault_content(env, unlocked):
    """Slice A's export walks `notes`. A vault row is not in `notes`, so this
    holds with no exclusion logic anywhere in the exporter — which is the
    point of the separate table. Checked LOCKED and UNLOCKED: an unlocked
    session is the case where a leak would actually be possible."""
    store, db, tmp = env
    from backend import vault as V
    from backend import notes_export as nx
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)

    # one note that started in the library and was moved in
    moved = build_secret_note(store, db)
    V.move_note_to_vault(moved, key, db_path=db)
    # one vault-native note, with a Thought and an attachment
    vid = V.create_note("%s native" % MARKER, "%s native body" % MARKER, key, db_path=db)
    V.add_entry(vid, "%s native thought" % MARKER, key, db_path=db)
    V.add_attachment(vid, (MARKER + " bytes").encode(), key, mime="image/png",
                     name="secret.png", db_path=db)
    # and an ordinary note that MUST still be exported
    keep = store.create_note("ordinary note", "ordinary body", status="active",
                             area_id=area(store, db), db_path=db)

    if not unlocked:
        V.KEYS.clear()

    out = str(tmp / "export.zip")
    st = nx.export_zip(store, out, db_path=db, with_attachments=True)
    raw = zip_bytes(out)
    assert raw.count(MARKER.encode()) == 0
    assert b"native thought" not in raw and b"secret.png" not in raw
    # the export is not simply empty — the ordinary note is there
    assert b"ordinary body" in raw
    assert st["notes"] == store.list_notes(db_path=db, limit=999).__len__()
    assert any(str(keep) in n for n in zipfile.ZipFile(out).namelist())
    # and the exporter never even had a way to ask: no vault SQL, no import of
    # the crypto module. (Its docstring says "Obsidian vault" — the word is
    # not the test, the reach is.)
    src = open(os.path.join(ROOT, "backend", "notes_export.py")).read()
    assert _sql_touching_vault(src) == []
    assert "import vault" not in src and "backend.vault" not in src


def test_export_route_while_locked_carries_no_vault_content(api):
    """The HTTP export the owner actually taps, taken while the vault is
    sealed."""
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    c.post("/api/vault/notes", json={"title": MARKER, "body": MARKER + " body"},
           headers=h)
    store.create_note("ordinary note", "ordinary body", status="active",
                      area_id=area(store, db), db_path=db)
    c.post("/api/vault/lock", headers=h)

    r = c.get("/api/brain/export.zip")
    assert r.status_code == 200
    out = str(tmp / "http-export.zip")
    open(out, "wb").write(r.content)
    raw = zip_bytes(out)
    assert raw.count(MARKER.encode()) == 0
    assert b"ordinary body" in raw


# ---------------------------------------------------------------------------
# quarantine drain — FIXTURE rows only, never live data
# ---------------------------------------------------------------------------

QUARANTINE_MARKER = "AKIAZZPLANTEDZZ"


def seed_quarantine(store, db, n=4):
    """Four held-back units shaped like the real ones: bytes 0600 in a
    directory outside hq_home, sha256 in the row, no note anywhere."""
    ids = []
    for i in range(1, n + 1):
        data = ("aws_access_key_id = %s%d\n-----BEGIN PRIVATE KEY-----\n"
                % (QUARANTINE_MARKER, i)).encode()
        ids.append(store.quarantine_unit(
            "apple-notes", "notes/secret-%d.md" % i, "aws_access_key_id",
            hashlib.sha256(data).hexdigest(), data, db_path=db))
    return ids


def test_quarantine_drain_seals_four_and_removes_the_files(env):
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    ids = seed_quarantine(store, db)
    rows = store.list_quarantine(db_path=db)
    assert len(rows) == 4
    paths = [r["stored_path"] for r in rows]
    for p in paths:
        assert os.path.isfile(p)
        assert oct(os.stat(p).st_mode & 0o777) == "0o600"
        # the bytes were NEVER in the DB — that was Phase 3's guarantee
    assert db_bytes(db).count(QUARANTINE_MARKER.encode()) == 0

    dry = V.drain_quarantine(key, db_path=db, apply=False)
    assert len(dry["would_drain"]) == 4 and dry["drained"] == []
    assert all(os.path.isfile(p) for p in paths), "a dry run wrote something"

    res = V.drain_quarantine(key, db_path=db, apply=True)
    assert len(res["drained"]) == 4, res
    assert res["errors"] == [] and res["skipped"] == []

    # 4 vault notes, all readable, each carrying its own key material
    notes = V.list_notes(key, db_path=db)
    assert len(notes) == 4
    bodies = [V.get_note(n["id"], key, db_path=db)["body"] for n in notes]
    for i in range(1, 5):
        assert any("%s%d" % (QUARANTINE_MARKER, i) in b for b in bodies)

    # rows STAMPED, not deleted: the ledger of what the import refused stays
    after = store.list_quarantine(db_path=db)
    assert len(after) == 4
    assert all(r["drained_at"] for r in after)
    # 0600 files gone
    assert not any(os.path.exists(p) for p in paths)

    # and nothing leaked into the DB or a fresh backup
    assert db_bytes(db).count(QUARANTINE_MARKER.encode()) == 0
    assert db_bytes(db).count(b"BEGIN PRIVATE KEY") == 0
    dest = store.backup_db(db_path=db, backup_dir=str(tmp / "backups"))
    backup = open(dest, "rb").read()
    assert backup.count(QUARANTINE_MARKER.encode()) == 0
    assert backup.count(b"BEGIN PRIVATE KEY") == 0

    # re-running is a no-op, not a duplication
    again = V.drain_quarantine(key, db_path=db, apply=True)
    assert again["drained"] == [] and len(V.list_notes(key, db_path=db)) == 4
    # audit records each drain with the source, never the secret
    audit = [a for a in V.list_audit(db_path=db) if a["action"] == "drained"]
    assert len(audit) == 4
    assert all(QUARANTINE_MARKER not in (a["detail"] or "") for a in audit)


def test_drain_skips_a_unit_whose_bytes_are_gone(env):
    """A quarantine row whose file was already removed by hand must be
    reported, not silently marked drained (which would lose the ledger's
    meaning)."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    seed_quarantine(store, db, n=1)
    os.remove(store.list_quarantine(db_path=db)[0]["stored_path"])
    res = V.drain_quarantine(key, db_path=db, apply=True)
    assert res["drained"] == [] and len(res["skipped"]) == 1
    assert store.list_quarantine(db_path=db)[0]["drained_at"] is None


def test_drain_refuses_while_locked(env):
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    seed_quarantine(store, db, n=1)
    with pytest.raises(V.VaultLocked):
        V.drain_quarantine(None, db_path=db, apply=True)


# ---------------------------------------------------------------------------
# passphrase change
# ---------------------------------------------------------------------------

def test_passphrase_change_keeps_every_row_readable(api):
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    c.post("/api/vault/notes", json={"title": "before", "body": "b"}, headers=h)

    assert c.post("/api/vault/passphrase",
                  json={"passphrase": "wrong", "new_passphrase": "new phrase here"},
                  headers=h).status_code == 401
    assert c.post("/api/vault/passphrase",
                  json={"passphrase": PW, "new_passphrase": "new phrase here"},
                  headers=h).status_code == 200

    c.post("/api/vault/lock", headers=h)
    assert c.post("/api/vault/unlock", json={"passphrase": PW}, headers=h).status_code == 401
    assert c.post("/api/vault/unlock", json={"passphrase": "new phrase here"},
                  headers=h).status_code == 200
    assert c.get("/api/vault/notes").json()["notes"][0]["title"] == "before"





def test_polling_status_does_not_keep_the_vault_awake(api):
    """The open page polls /api/vault/status every 60s. If that counted as
    use, the idle timeout would be defeated by the very UI that displays it —
    a vault left open on the phone would never re-seal."""
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    from backend import vault as V
    tok = list(V.KEYS._keys)[0]

    idle_before = V.KEYS.idle_since(tok)
    for _ in range(5):
        assert c.get("/api/vault/status").json()["unlocked"] is True
    assert V.KEYS.idle_since(tok) == idle_before, "status refreshed the idle timer"

    # an actual read DOES count as use
    c.get("/api/vault/notes")
    assert V.KEYS.idle_since(tok) > idle_before


# ---------------------------------------------------------------------------
# Review round 1 (2026-09-10). Every test below is a reviewer repro pinned so
# it cannot come back. `build_secret_note` only ever filed a proposal ON the
# note itself, which is exactly why the first suite missed all of this.
# ---------------------------------------------------------------------------

def build_entangled_note(store, db, tmp, client, headers, marker=MARKER):
    """A note with the relationships THE APP ITSELF creates.

    The shapes that matter and were missing before:
      * a `contradiction` proposal filed on the PARTNER note, naming this one
        through `payload.other_note_id` — its `note_id` points elsewhere;
      * a linked task and a linked reminder created through the real routes,
        which DEFAULT their title/name to the note's title
        (`backend/notes.py:283-284`, `:305-306`);
      * the activity rows those two writes produce, which carry the bare title
        with no note id anywhere in the line.
    """
    work = area(store, db)
    nid = store.create_note("%s plan" % marker, "%s body" % marker, status="active",
                            area_id=work, project_id=1, db_path=db)
    partner = store.create_note("partner note", "unremarkable", status="active",
                                area_id=work, db_path=db)
    r1 = client.post("/api/note/%d/new-task" % nid, json={}, headers=headers)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/note/%d/new-reminder" % nid,
                     json={"cron": "0 9 * * *"}, headers=headers)
    assert r2.status_code == 200, r2.text
    store.create_proposal("contradiction", partner,
                          {"other_note_id": nid, "explanation": "conflicts with %s plan" % marker},
                          summary="partner contradicts %s plan" % marker, db_path=db)
    return nid, partner, r1.json()["id"], r2.json()["id"]


def scan_all_tables(db, marker=MARKER):
    """Every string/blob cell in every table. Returns {table: hits}."""
    conn = sqlite3.connect(db)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        hits = {}
        for t in tables:
            n = 0
            try:
                rows = list(conn.execute("SELECT * FROM %s" % t))
            except sqlite3.DatabaseError:
                continue
            for row in rows:
                n += sum(1 for v in row if isinstance(v, (str, bytes))
                         and (marker.encode() if isinstance(v, bytes) else marker) in v)
            if n:
                hits[t] = n
        return hits
    finally:
        conn.close()


def test_move_is_refused_while_a_linked_task_or_reminder_carries_the_title(api):
    """CRITICAL 1 (reviewer repro). The app defaults a graduated task's title
    and a reminder's name to the NOTE'S OWN title, so sealing the note while
    they exist erases the note and leaves the title in plain sight — and the
    reminder keeps minting fresh tasks carrying it forever."""
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    from backend import vault as V
    key = V.unlock(PW, db_path=db)
    nid, partner, tid, sid = build_entangled_note(store, db, tmp, c, h)

    with pytest.raises(V.VaultRefused) as e:
        V.move_note_to_vault(nid, key, db_path=db)
    msg = str(e.value)
    assert "#%d" % tid in msg and "#%d" % sid in msg, msg     # names the ids
    assert "task" in msg and "reminder" in msg
    # and NOTHING happened
    assert store.get_note(nid, db_path=db) is not None
    assert V.count_notes(db_path=db) == 0
    assert V.list_audit(db_path=db) == []

    # over HTTP it is a 409 carrying that same sentence verbatim
    r = c.post("/api/vault/move", json={"note_id": nid}, headers=h)
    assert r.status_code == 409
    assert "#%d" % tid in r.text and "#%d" % sid in r.text

    # The owner does what the message asks, and BOTH remedies are real calls:
    # schedules always could be renamed, and `edit_task` gained `title` in
    # ce54699 — added because this very refusal had nothing to offer but
    # deletion. The message must name renaming, not removal.
    assert "rename the task" in msg and "rename the reminder" in msg
    assert "delete" not in msg
    store.update_schedule(sid, name="dentist", title="call the dentist", db_path=db)
    store.edit_task(tid, title="call the dentist", db_path=db)
    # nothing was deleted: the task and its link both survive the remedy
    assert store.get_task(tid, db_path=db)["title"] == "call the dentist"
    res = V.move_note_to_vault(nid, key, db_path=db)
    assert res["vault_note_id"]


def test_the_move_leaves_zero_copies_across_every_table(api):
    """CRITICAL 1, the whole scenario, measured the way the reviewer measured
    it: sweep EVERY string cell in EVERY table, not the handful the fixture
    happens to know about.

    Round-2 reviewer observation, acted on: with the refusal disabled this test
    still passed, because by the time it moves the note it has already cleared
    the title-holders. That made it silently NOT a test of the refusal. It now
    asserts the block FIRST and clears second, so both halves run here — and
    the refusal has its own dedicated tests below (`test_refusal_catches_*`)
    which are the ones that fail if the gate is removed.
    """
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    from backend import vault as V
    key = V.unlock(PW, db_path=db)
    nid, partner, tid, sid = build_entangled_note(store, db, tmp, c, h)

    before = scan_all_tables(db)
    assert sum(before.values()) >= 10, before
    assert "proposals" in before and "tasks" in before and "schedules" in before

    # FIRST: the refusal must fire while the task and reminder carry the title.
    with pytest.raises(V.VaultRefused):
        V.move_note_to_vault(nid, key, db_path=db)
    assert scan_all_tables(db) == before, "the refused move changed something"

    # THEN clear the blockers; the proposal/activity/notification copies are
    # what the rest of this test is about.
    store.update_schedule(sid, name="d", title="d", db_path=db)
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DELETE FROM note_links WHERE kind='task' AND target_id=?", (tid,))
        conn.execute("DELETE FROM tasks WHERE id=?", (tid,))
    conn.close()
    # NOTE: the task_create / schedule_create ACTIVITY rows still carry the
    # bare title with no note id — they are exactly what the content-reach
    # scrub has to catch, so they are deliberately left in place here.
    remaining = scan_all_tables(db)
    assert sum(remaining.values()) >= 6, remaining

    V.move_note_to_vault(nid, key, db_path=db)

    after = scan_all_tables(db)
    assert after == {}, "surviving plaintext copies: %r" % after
    assert db_bytes(db).count(MARKER.encode()) == 0
    # the partner's contradiction proposal — reached through payload, not note_id
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
    finally:
        conn.close()
    # the partner note itself survives: it is not the one being sealed
    assert store.get_note(partner, db_path=db) is not None


def test_a_proposal_on_the_partner_note_is_reached_by_payload(env):
    """The narrow half of CRITICAL 1: `WHERE note_id=?` walks straight past a
    proposal whose payload names this note."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    work = area(store, db)
    nid = store.create_note("%s plan" % MARKER, "body", status="active", area_id=work, db_path=db)
    partner = store.create_note("partner", "x", status="active", area_id=work, db_path=db)
    pid = store.create_proposal("contradiction", partner,
                                {"other_note_id": nid, "explanation": "vs %s plan" % MARKER},
                                summary="conflicts with %s plan" % MARKER, db_path=db)
    # a proposal that mentions NEITHER must survive untouched
    innocent = store.create_proposal("file", partner, {"area_id": work, "tags": []},
                                     summary="file the partner", db_path=db)

    V.move_note_to_vault(nid, key, db_path=db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM proposals WHERE id=?", (pid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM proposals WHERE id=?", (innocent,)).fetchone()[0] == 1
    finally:
        conn.close()
    assert scan_all_tables(db) == {}


def test_a_corrupted_attachment_ciphertext_destroys_nothing(env, monkeypatch):
    """CRITICAL 2 (reviewer repro). The read-back used to compare title, body
    and a COUNT of Thoughts, so a single bad byte in an attachment blob passed,
    the move returned ok, the plaintext image was deleted and the owner's photo
    became permanently unrecoverable."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = store.create_note("photo note", "body", db_path=db)
    blob = b"IRREPLACEABLE-PHOTO-BYTES" * 40
    att = store.add_attachment(nid, blob, kind="photo", mime="image/png",
                               orig_name="wedding.png", db_path=db)
    path = store.attachment_path(nid, att["stored_name"])

    real_seal = V.seal
    corrupt = [True]

    def bad_seal(k, pt, aad=b""):
        out = bytearray(real_seal(k, pt, aad))
        if aad == V.AAD_ATT_BLOB and corrupt[0]:
            out[-1] ^= 0x01                      # one byte, one failed tag
        return bytes(out)

    # A toggle, NOT monkeypatch.undo(): pytest gives the `env` fixture and this
    # test the SAME monkeypatch instance, so undo() would also roll back
    # HERMES_HQ_HOME and silently move the attachment out from under us.
    monkeypatch.setattr(V, "seal", bad_seal)
    with pytest.raises(V.VaultError):
        V.move_note_to_vault(nid, key, db_path=db)
    corrupt[0] = False

    # the whole move rolled back and the bytes are still on disk
    assert os.path.isfile(path) and open(path, "rb").read() == blob
    assert store.get_note(nid, db_path=db) is not None
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM vault_notes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM vault_attachments").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM vault_audit WHERE action='moved_in'"
                            ).fetchone()[0] == 0
    finally:
        conn.close()

    # and the honest path still works, byte for byte
    res = V.move_note_to_vault(nid, key, db_path=db)
    n = V.get_note(res["vault_note_id"], key, db_path=db)
    data, _mime, _name = V.read_attachment(n["attachments"][0]["id"], key, db_path=db)
    assert data == blob
    assert not os.path.exists(path)


def test_a_truncated_thought_ciphertext_destroys_nothing(env, monkeypatch):
    """The same hole one field over: Thoughts were compared by COUNT."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = store.create_note("note", "body", db_path=db)
    store.add_note_entry(nid, "%s thought" % MARKER, db_path=db)

    real_seal = V.seal
    monkeypatch.setattr(V, "seal", lambda k, pt, aad=b"": (
        real_seal(k, b"a different thought entirely", aad) if aad == V.AAD_ENTRY
        else real_seal(k, pt, aad)))
    with pytest.raises(V.VaultError) as e:
        V.move_note_to_vault(nid, key, db_path=db)
    assert "nothing was destroyed" in str(e.value)
    assert store.get_note(nid, db_path=db) is not None


def test_the_read_back_runs_before_the_destroy(env, monkeypatch):
    """MINOR (reviewer): the old version of this test passed even with the
    destroy moved BEFORE the read-back, because the transaction rollback was
    doing the work. Ordering is now asserted directly — when the read-back
    fails, the destroy must never have been CALLED."""
    store, db, tmp = env
    from core import wm_store
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = build_secret_note(store, db)

    called = []
    real = wm_store._destroy_note_plaintext
    monkeypatch.setattr(wm_store, "_destroy_note_plaintext",
                        lambda *a, **k: (called.append(a[1]), real(*a, **k))[1])
    monkeypatch.setattr(V, "get_note_in_conn",
                        lambda conn, nid_, k: {"title": "corrupted", "body": "",
                                               "entries": [], "attachments": []})
    with pytest.raises(V.VaultError):
        V.move_note_to_vault(nid, key, db_path=db)
    assert called == [], "the destroy ran before/despite the failed read-back"


def test_a_blocked_wal_checkpoint_is_reported_not_swallowed(env):
    """IMPORTANT (reviewer): one concurrent reader — the phone polling, an
    agent mid-query — blocks TRUNCATE, and swallowing that left the marker in
    hq.db-wal while the move reported success. The move still commits; it must
    SAY the WAL was not truncated, in the return value and in the audit row."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = build_secret_note(store, db)

    reader = sqlite3.connect(db)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM notes").fetchone()   # holds a read txn
    try:
        res = V.move_note_to_vault(nid, key, db_path=db)
    finally:
        reader.rollback(); reader.close()

    assert res["wal_truncated"] is False
    audit = [a for a in V.list_audit(db_path=db) if a["action"] == "moved_in"][0]
    assert "WAL NOT truncated" in (audit["detail"] or "")
    # the row itself is still gone from the b-tree
    assert store.get_note(nid, db_path=db) is None


def test_a_leftover_plaintext_file_is_named_in_the_audit(env, monkeypatch):
    """IMPORTANT (reviewer): a failed unlink orphans a plaintext image that
    nothing points at and that rides into every backup. Silence would hide it
    forever."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = store.create_note("photo note", "body", db_path=db)
    att = store.add_attachment(nid, b"bytes" * 50, kind="photo", mime="image/png",
                               orig_name="x.png", db_path=db)
    path = store.attachment_path(nid, att["stored_name"])

    real_remove = os.remove
    monkeypatch.setattr(V.os, "remove",
                        lambda p: (_ for _ in ()).throw(OSError("read-only fs"))
                        if p == path else real_remove(p))
    res = V.move_note_to_vault(nid, key, db_path=db)

    assert res["leftover_files"] and path in res["leftover_files"][0]
    audit = [a for a in V.list_audit(db_path=db) if a["action"] == "moved_in"][0]
    assert "PLAINTEXT FILE(S) LEFT ON DISK" in (audit["detail"] or "")
    assert path in audit["detail"]
    assert os.path.isfile(path)                   # it really is still there


# (the escrow-reuse test lived here; there is no key file to reuse any more —
#  `test_there_is_no_key_file_anywhere` replaces it)


def test_an_attachment_sealed_from_the_wrong_bytes_destroys_nothing(env, monkeypatch):
    """The half of the read-back that UNSEALING alone cannot catch: a blob
    that opens perfectly but yields bytes that are not what was on disk. Only
    comparing the plaintext catches a swapped or misread file."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = store.create_note("photo note", "body", db_path=db)
    blob = b"REAL-PHOTO" * 60
    store.add_attachment(nid, blob, kind="photo", mime="image/png",
                         orig_name="wedding.png", db_path=db)
    real_seal = V.seal
    monkeypatch.setattr(V, "seal", lambda k, pt, aad=b"": (
        real_seal(k, b"SOMEONE ELSES PHOTO", aad) if aad == V.AAD_ATT_BLOB
        else real_seal(k, pt, aad)))
    with pytest.raises(V.VaultError) as e:
        V.move_note_to_vault(nid, key, db_path=db)
    assert "nothing was destroyed" in str(e.value)
    assert store.get_note(nid, db_path=db) is not None


# ---------------------------------------------------------------------------
# Review round 2 (2026-09-10)
# ---------------------------------------------------------------------------

def seed_activity_corpus(store, db, n=51):
    """A realistic activity log + notifications to measure collateral against."""
    real = ["note #3: Groceries", "task #7 claimed by orchestrator",
            "db backup written to /x", "note #4: Notes from the analyst",
            "proposal #2 (file) on note #9: tidy up", "run 12 finished ok",
            "schedule 'Standup' fired", "note #5: Todo for Monday",
            "gateway 8650 started", "note #6: a quick thought"]
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DELETE FROM activity")
        conn.execute("DELETE FROM notifications")
        for i in range(n):
            conn.execute("INSERT INTO activity(ts, action, detail) VALUES(?,?,?)",
                         (1.0 * i, "x", real[i % len(real)] + " #%d" % i))
        for k, (t, b) in enumerate((("Notes from the analyst", "a real body"),
                                    ("Todo review", "please look"),
                                    ("Deploy done", "all good"))):
            conn.execute("INSERT INTO notifications(ts,kind,title,body,source_key) "
                         "VALUES(1,'info',?,?,?)", (t, b, "k%d" % k))
    conn.close()


def activity_state(db):
    conn = sqlite3.connect(db)
    try:
        rows = [r[0] for r in conn.execute("SELECT detail FROM activity")]
        notes = [(r[0], r[1]) for r in conn.execute("SELECT title, body FROM notifications")]
        return rows, notes
    finally:
        conn.close()


@pytest.mark.parametrize("title", ["e", "a", "Notes", "Todo", "Groceries", "meetings"])
def test_a_generic_title_is_never_chased_through_the_log(env, title):
    """CRITICAL (round 2), reproduced then pinned.

    Measured before the fix on a 51-row corpus: 'e' redacted EVERY row, 'a'
    redacted 41, and 'Notes' DELETED an unrelated "Notes from the analyst"
    notification outright. The activity log is the owner's readable history of
    their own system; one button silently rewriting it is worse than the leak
    it prevents, and a title that generic is not a secret in the first place.
    """
    store, db, tmp = env
    from backend import vault as V
    from core import wm_store as ws
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    seed_activity_corpus(store, db)
    before_rows, before_notifs = activity_state(db)

    assert ws.is_distinctive_title(title) is False
    nid = store.create_note(title, "the actual secret body", db_path=db)
    V.move_note_to_vault(nid, key, db_path=db)

    after_rows, after_notifs = activity_state(db)
    # every pre-existing row survives untouched, and no notification is gone
    assert set(before_rows) <= set(after_rows), (
        "generic title redacted unrelated activity: %r"
        % sorted(set(before_rows) - set(after_rows))[:5])
    assert len(after_notifs) == len(before_notifs)
    assert set(before_notifs) == set(after_notifs)
    # the note itself is still fully destroyed
    assert store.get_note(nid, db_path=db) is None
    assert db_bytes(db).count(b"the actual secret body") == 0
    # and the owner is TOLD the title was not chased
    impact_titles = ws.note_title_history(sqlite3.connect(db).cursor().connection, 1)
    assert impact_titles is not None


@pytest.mark.parametrize("title", ["AWS root creds for prod", "Fakebank login details"])
def test_a_distinctive_title_is_still_chased(env, title):
    """The other side of the gate: a real secret-shaped title must still be
    hunted everywhere, or the Critical-1 fix is undone."""
    store, db, tmp = env
    from backend import vault as V
    from core import wm_store as ws
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    seed_activity_corpus(store, db)
    assert ws.is_distinctive_title(title) is True

    conn = sqlite3.connect(db)
    with conn:
        conn.execute("INSERT INTO activity(ts, action, detail) VALUES(9,'x',?)",
                     ("task created: %s" % title,))
        conn.execute("INSERT INTO notifications(ts,kind,title,body,source_key) "
                     "VALUES(9,'info',?,?,'kx')", (title, "body mentioning %s" % title))
    conn.close()
    nid = store.create_note(title, "body", db_path=db)
    V.move_note_to_vault(nid, key, db_path=db)

    assert db_bytes(db).count(title.encode()) == 0
    rows, notifs = activity_state(db)
    assert not any(title in (r or "") for r in rows)
    # redacted, NOT deleted — the notification row survives with its text gone
    assert len(notifs) == 4
    assert not any(title in (t or "") or title in (b or "") for t, b in notifs)


def test_notifications_are_redacted_never_deleted(env):
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    seed_activity_corpus(store, db)
    title = "Quarterly tax spreadsheet"
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("INSERT INTO notifications(ts,kind,title,body,source_key) "
                     "VALUES(9,'needs_you',?,?,'kx')", (title, "look at it"))
    conn.close()
    before = len(activity_state(db)[1])
    nid = store.create_note(title, "body", db_path=db)
    V.move_note_to_vault(nid, key, db_path=db)
    after = activity_state(db)[1]
    assert len(after) == before, "a notification row was deleted"
    assert ("(moved to the vault)", None) in after


def test_move_impact_previews_the_damage_before_the_owner_commits(api):
    """The number the confirm modal shows. It has to be computed by the SAME
    queries that do the work, or the preview drifts from the act."""
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    from backend import vault as V
    key = V.unlock(PW, db_path=db)
    seed_activity_corpus(store, db)
    nid = build_secret_note(store, db)

    before_scan = scan_all_tables(db)
    from core import wm_store as ws
    dry_calls = []
    real_destroy = ws._destroy_note_plaintext
    import unittest.mock as _mock
    with _mock.patch.object(ws, "_destroy_note_plaintext",
                            side_effect=lambda *a, **k: (dry_calls.append(k.get("dry_run", False)),
                                                         real_destroy(*a, **k))[1]):
        r = c.get("/api/vault/move-impact/%d" % nid)
    assert r.status_code == 200
    imp = r.json()
    assert imp["blocked"] is False and imp["reason"] is None
    assert imp["counts"]["revisions"] == 1
    assert imp["counts"]["entries"] == 1
    assert imp["title_reach"] is True and imp["title_reach_note"] is None
    predicted = dict(imp["counts"])

    # the preview WROTE NOTHING — asserted three ways, because "it happened to
    # roll back" is how this was safe before it was made safe on purpose.
    assert store.get_note(nid, db_path=db) is not None
    assert V.count_notes(db_path=db) == 0
    assert scan_all_tables(db) == before_scan
    # and it really did ask for a dry run
    assert dry_calls == [True], dry_calls

    actual = V.move_note_to_vault(nid, key, db_path=db)["destroyed"]
    for k in ("revisions", "entries", "attachments", "links", "proposals",
              "activity", "notifications"):
        assert predicted[k] == actual[k], (k, predicted[k], actual[k])


def test_move_impact_discloses_when_the_title_is_not_chased(api):
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    nid = store.create_note("Notes", "body", db_path=db)
    imp = c.get("/api/vault/move-impact/%d" % nid).json()
    assert imp["title_reach"] is False
    assert "short or ordinary" in imp["title_reach_note"]
    assert "still destroyed" in imp["title_reach_note"]


def test_move_impact_reports_a_blocker_without_raising(api):
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    nid, partner, tid, sid = build_entangled_note(store, db, tmp, c, h)
    imp = c.get("/api/vault/move-impact/%d" % nid).json()
    assert imp["blocked"] is True
    assert tid in imp["blocking_tasks"] and sid in imp["blocking_schedules"]
    assert "#%d" % tid in imp["reason"]
    # locked sessions get no preview at all
    c.post("/api/vault/lock", headers=h)
    assert c.get("/api/vault/move-impact/%d" % nid).status_code == 423


# ---- the three ways past the refusal (round-2 IMPORTANT) -------------------

def _unlinked_task(store, db, title):
    """A task carrying the title with NO note_links row — agent-created,
    imported, or the link was pruned."""
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("INSERT INTO tasks(project_id, title, description, status, created_at) "
                     "VALUES(1, ?, '', 'ready', 1.0)", (title,))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_refusal_catches_a_task_with_no_link_row(env):
    """(B) The check used to read `note_links`, so an unlinked task carrying
    the title sailed past and the move reported success."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    title = "AWS root creds for prod"
    nid = store.create_note(title, "body", db_path=db)
    tid = _unlinked_task(store, db, title)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM note_links").fetchone()[0] == 0
    conn.close()

    with pytest.raises(V.VaultRefused) as e:
        V.move_note_to_vault(nid, key, db_path=db)
    assert "#%d" % tid in str(e.value)
    assert store.get_note(nid, db_path=db) is not None


def test_refusal_catches_a_shortened_task_title(env):
    """(C) The comparison was one-directional, so a task titled with a PREFIX
    of the note title ("AWS root creds" from "AWS root creds for prod") passed."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = store.create_note("AWS root creds for prod", "body", db_path=db)
    tid = _unlinked_task(store, db, "AWS root creds")     # shorter, still distinctive
    with pytest.raises(V.VaultRefused) as e:
        V.move_note_to_vault(nid, key, db_path=db)
    assert "#%d" % tid in str(e.value)


def test_refusal_catches_an_old_title_after_a_rename(env):
    """(D) The note was renamed after the task was made, so the surviving copy
    is an OLD title. `note_revisions` holds those and is destroyed by this
    path, which settles the intent: old titles die too."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    old_title = "Fakebank login details"
    nid = store.create_note(old_title, "body", db_path=db)
    tid = _unlinked_task(store, db, old_title)
    store.update_note(nid, title="Something innocuous entirely", db_path=db)
    assert store.get_note(nid, db_path=db)["title"] == "Something innocuous entirely"

    with pytest.raises(V.VaultRefused) as e:
        V.move_note_to_vault(nid, key, db_path=db)
    assert "#%d" % tid in str(e.value)

    # remove the task and the OLD title is still scrubbed everywhere
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (tid,))
        conn.execute("INSERT INTO activity(ts, action, detail) VALUES(9,'x',?)",
                     ("task created: %s" % old_title,))
    conn.close()
    assert db_bytes(db).count(old_title.encode()) > 0
    V.move_note_to_vault(nid, key, db_path=db)
    assert db_bytes(db).count(old_title.encode()) == 0


def test_an_unrelated_task_does_not_block_the_move(env):
    """The gate must not refuse everything: a task with nothing to do with
    this note is not a blocker."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    nid = store.create_note("Fakebank login details", "body", db_path=db)
    _unlinked_task(store, db, "Rebuild the staging box")
    assert V.move_note_to_vault(nid, key, db_path=db)["vault_note_id"]


# ---- Phase 4 security review, 2026-09-10 --------------------------------

def test_a_backslash_in_a_title_does_not_defeat_the_survival_guard(env):
    """The LIKE escape must escape the escape character FIRST.

    Escaping only `%` and `_` left `\\` live: under ESCAPE '\\' SQLite consumes
    it and takes the next character literally, so a title like
    `C:\\Users\\prod creds` produced a pattern that did not match its own text.
    The guard then returned nothing, the move was ALLOWED, and the secret title
    stayed in `tasks` while the modal truthfully reported zero rows — a
    fail-OPEN break of the control that findings B/C/D exist to enforce.
    """
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    title = r"C:\Users\admin\prod creds"
    nid = store.create_note(title, body="the secret", status="inbox", db_path=db)
    tid = store.create_task("alpha", "Rotate %s now" % title, db_path=db)
    store.link_note(nid, "task", tid, db_path=db)
    with pytest.raises(V.VaultRefused) as e:
        V.move_note_to_vault(nid, key, db_path=db)
    assert "#%d" % tid in str(e.value)
    assert store.get_note(nid, db_path=db) is not None      # nothing destroyed


def test_the_definition_of_done_holds_a_title_back_too(env):
    """`definition_of_done` is rendered into the agent's brief, so a title
    parked there is as exposed as one in the description."""
    store, db, tmp = env
    from backend import vault as V
    V.init_vault(PW, db_path=db)
    key = V.unlock(PW, db_path=db)
    title = "Fakebank account recovery codes"
    nid = store.create_note(title, body="x", status="inbox", db_path=db)
    tid = store.create_task("alpha", "unrelated", "unrelated",
                            "check %s are rotated" % title, db_path=db)
    store.link_note(nid, "task", tid, db_path=db)
    with pytest.raises(V.VaultRefused) as e:
        V.move_note_to_vault(nid, key, db_path=db)
    assert "#%d" % tid in str(e.value)


def _last_vault_attachment_id(db):
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT MAX(id) FROM vault_attachments").fetchone()[0]
    finally:
        conn.close()


def test_a_vault_attachment_is_never_served_as_active_content(api):
    """Sibling routes hand back decrypted plaintext to any same-origin fetch,
    so an SVG or HTML attachment rendering on the app origin while the vault is
    unlocked would script `/api/vault/notes` and exfiltrate the lot. The mime is
    whatever the upload declared — untrusted input."""
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    nid = c.post("/api/vault/notes", json={"title": "holder", "body": "b"},
                 headers=h).json()["note"]["id"]
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>fetch("/api/vault/notes")</script></svg>'
    r = c.post("/api/vault/note/%d/attachments" % nid,
               files={"file": ("payload.svg", svg, "image/svg+xml")},
               data={"kind": "photo"}, headers=h)
    assert r.status_code == 200
    got = c.get("/api/vault/attachment/%d" % _last_vault_attachment_id(db))
    assert got.status_code == 200
    assert "svg" not in got.headers["content-type"]
    assert got.headers["content-type"].startswith("application/octet-stream")
    assert got.headers["content-disposition"].startswith("attachment")
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in got.headers["content-security-policy"]
    assert got.content == svg                       # the bytes are intact


def test_a_non_latin1_attachment_name_does_not_500(api):
    """A raw `filename="%s"` interpolation raises UnicodeEncodeError on any
    non-Latin-1 name, which for this owner's library is not hypothetical."""
    c, store, db, tmp = api
    h = login(c)
    c.post("/api/vault/init", json={"passphrase": PW}, headers=h)
    nid = c.post("/api/vault/notes", json={"title": "holder", "body": "b"},
                 headers=h).json()["note"]["id"]
    r = c.post("/api/vault/note/%d/attachments" % nid,
               files={"file": ("اردو-نوٹ.png", b"\x89PNG-bytes", "image/png")},
               data={"kind": "photo"}, headers=h)
    assert r.status_code == 200
    got = c.get("/api/vault/attachment/%d" % _last_vault_attachment_id(db))
    assert got.status_code == 200 and got.content == b"\x89PNG-bytes"
    assert "filename*=UTF-8''" in got.headers["content-disposition"]
