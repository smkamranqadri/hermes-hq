"""The SPA fallback must not serve anything outside the build directory.

Found live by the Phase 4 security review (2026-09-10): `AuthMiddleware` only
guards paths starting with `/api/`, the catch-all `{path:path}` binds a leading
slash from a request line like `//etc/passwd`, and `os.path.join(base, "/abs")`
discards the base. The running server returned 200 for
`//opt/data/hermes-hq/password` — the owner's login password — to an
UNAUTHENTICATED request, and `sessions.json` (every live session token and its
CSRF token) the same way.

These tests exist so that can never come back quietly.
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    # a real secret outside the build dir, of the shape the live box has
    secret = tmp_path / "hq" / "password"
    secret.write_text("s3cr3t-live-password\n")
    import backend.app as appmod
    # STATIC_DIR is derived from backend/app.py's OWN path, NOT from
    # HERMES_HQ_HOME — so writing a fixture index.html into it CLOBBERS THE
    # PRODUCTION BUILD. This test did exactly that on 2026-09-10: it replaced
    # the owner's live 1053-byte index.html with a 32-byte stub carrying no
    # script tag, and the running app served a white page until the frontend
    # was rebuilt. Point it at a temp dir instead, and never write to the real
    # one from a test.
    static = tmp_path / "static"
    os.makedirs(static / "assets", exist_ok=True)
    (static / "index.html").write_text("<!doctype html><title>hq</title>")
    monkeypatch.setattr(appmod, "STATIC_DIR", str(static))
    from fastapi.testclient import TestClient
    with TestClient(appmod.create_app(dispatcher_enabled=False)) as c:
        yield c, str(secret)


BASE = "http://testserver"


def test_double_slash_absolute_path_is_not_served(client):
    c, secret = client
    for probe in ("/" + secret.lstrip("/"), "//" + secret.lstrip("/"),
                  "/etc/hostname", "//etc/hostname"):
        r = c.get(BASE + probe)
        # either the SPA shell or a 404 — never the file
        assert "s3cr3t-live-password" not in r.text, probe
        assert "root:" not in r.text, probe


def test_dot_dot_cannot_climb_out_of_the_build_dir(client):
    c, secret = client
    for probe in ("/../../../etc/hostname", "/assets/../../app.py",
                  "/%2e%2e/%2e%2e/etc/hostname"):
        r = c.get(BASE + probe)
        assert "root:" not in r.text and "def create_app" not in r.text, probe


def test_the_spa_shell_and_real_assets_still_serve(client):
    c, _ = client
    assert "<title>hq</title>" in c.get("/brain/library").text   # SPA route
    assert "<title>hq</title>" in c.get("/").text


def test_the_auth_gate_cannot_be_sidestepped_by_spelling(client):
    """`//api/system` and `/api/./system` reach the same route; the gate has to
    normalise before it decides, or one spelling is guarded and the other is
    not."""
    c, _ = client
    assert c.get("/api/system").status_code == 401
    for probe in ("//api/system", "/api/./system", "/api/foo/../system"):
        assert c.get(BASE + probe).status_code in (401, 404), probe


def test_the_probes_actually_send_the_path_under_test():
    """httpx reads a leading `//` as a protocol-relative URL (host = the first
    segment), so a relative probe silently tests nothing. This pins the shape
    the other tests rely on."""
    import httpx
    assert httpx.URL(BASE + "//etc/hostname").path == "//etc/hostname"
    assert httpx.URL(BASE + "//api/system").path == "//api/system"


def test_normalise_path_collapses_slashes_and_dots():
    from backend.auth import _normalise_path
    assert _normalise_path("//api/system") == "/api/system"
    assert _normalise_path("/api/./system") == "/api/system"
    assert _normalise_path("/api/foo/../system") == "/api/system"
    assert _normalise_path("/../../etc/passwd") == "/etc/passwd"
    assert _normalise_path("/") == "/"
