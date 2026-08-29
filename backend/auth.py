"""Single-owner auth: one password, cookie session, CSRF header on mutations.

Password: HERMES_HQ_PASSWORD, else $HERMES_HQ_HOME/password (generated once,
mode 0600, also printed by `serve`). Sessions persist in
$HERMES_HQ_HOME/sessions.json so a restart does not log the phone out.
"""
import hmac
import json
import os
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from core import wm_store

COOKIE = "hq_session"
CSRF_HEADER = "x-csrf"
SESSION_TTL = 30 * 24 * 3600
PUBLIC = ("/api/login", "/api/health")
MUTATING = ("POST", "PUT", "PATCH", "DELETE")


def password_path():
    return os.path.join(wm_store.hq_home(), "password")


def resolve_password(generate=True):
    """Return (password, source) where source is env|file|generated."""
    env = os.environ.get("HERMES_HQ_PASSWORD")
    if env:
        return env, "env"
    p = password_path()
    if os.path.isfile(p):
        return open(p).read().strip(), "file"
    if not generate:
        return None, None
    pw = secrets.token_urlsafe(12)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(pw + "\n")
    os.chmod(p, 0o600)
    return pw, "generated"


class Sessions:
    def __init__(self, path=None):
        self.path = path or os.path.join(wm_store.hq_home(), "sessions.json")
        self._s = {}
        try:
            with open(self.path) as f:
                self._s = json.load(f)
        except (OSError, ValueError):
            self._s = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._s, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def create(self):
        tok = secrets.token_urlsafe(32)
        self._s[tok] = {"csrf": secrets.token_urlsafe(24), "created": time.time(), "seen": time.time()}
        self._save()
        return tok, self._s[tok]["csrf"]

    def get(self, tok):
        s = self._s.get(tok) if tok else None
        if not s:
            return None
        if time.time() - s["created"] > SESSION_TTL:
            self._s.pop(tok, None); self._save()
            return None
        s["seen"] = time.time()
        return s

    def drop(self, tok):
        if self._s.pop(tok, None) is not None:
            self._save()


class AuthMiddleware(BaseHTTPMiddleware):
    """401 for unauthenticated /api/*, 403 for mutations without the CSRF header."""

    def __init__(self, app, sessions: Sessions):
        super().__init__(app)
        self.sessions = sessions

    async def dispatch(self, request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or path in PUBLIC:
            return await call_next(request)
        sess = self.sessions.get(request.cookies.get(COOKIE))
        if sess is None:
            return JSONResponse({"error": "login required"}, status_code=401)
        if request.method in MUTATING and not hmac.compare_digest(
                request.headers.get(CSRF_HEADER, ""), sess["csrf"]):
            return JSONResponse({"error": "missing or bad CSRF token"}, status_code=403)
        request.state.session = sess
        return await call_next(request)


def check_password(given, actual):
    return bool(given) and hmac.compare_digest(given.encode(), actual.encode())
