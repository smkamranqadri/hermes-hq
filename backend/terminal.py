"""Browser terminal (Group 6-1): a real PTY per session, relayed over a WebSocket.

Rules (owner decisions 2026-08-30, plan `kis/intent/Group6Plan.md`):
- The shell runs as the `hermes` user (uid/gid TERMINAL_UID), never as root. When
  hermes-hq itself runs as root the child drops privileges before exec; when it
  runs unprivileged the shell is simply that user. A failed drop aborts the spawn.
- The auth middleware never sees WebSocket handshakes, so the WS route checks the
  session cookie and a same-origin `Origin` header itself (4401 / 4403 close codes).
- Sessions survive disconnects: output goes into a 1 MiB ring buffer that is replayed
  on reattach; a detached session is reaped after DETACH_TTL; at most MAX_SESSIONS.

Wire protocol (client -> server, JSON text frames):
  {"t":"i","d":"<text>"}          keyboard input
  {"t":"r","cols":N,"rows":N}     resize (TIOCSWINSZ + SIGWINCH)
  {"t":"p"}                       ping
Server -> client: binary frames = raw PTY bytes; JSON text frames
  {"t":"hello","id":...,"reattach":bool,"exited":code|null}
  {"t":"exit","code":N}   {"t":"pong"}
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import pwd
import secrets
import signal
import struct
import termios
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend import auth as A
from core import wm_store as store

router = APIRouter(prefix="/api/terminal")

TERMINAL_USER = os.environ.get("HERMES_HQ_TERMINAL_USER", "hermes")
MAX_SESSIONS = 8
RING_BYTES = 1 << 20            # 1 MiB scrollback replayed on reattach
DETACH_TTL = 30 * 60            # seconds a session lives with no client attached
READ_CHUNK = 65536
CLOSE_NO_AUTH = 4401
CLOSE_BAD_ORIGIN = 4403
CLOSE_NOT_FOUND = 4404
CLOSE_LIMIT = 4429
INPUT_MAX_PENDING = 1 << 16    # unread keyboard input we hold for a busy tty
QUEUE_MAX = 512                # output chunks per attached client; oldest dropped when it cannot keep up


def _offer(q: asyncio.Queue, item):
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(item)


def _terminal_home() -> str:
    """HOME for the shell: the Hermes user's own home (where its CLI and dotfiles live)."""
    return os.environ.get("HERMES_HQ_TERMINAL_HOME") or os.path.join(store.hermes_root_home(), "home")


def target_user():
    """(uid, gid, home) the shell must run as. None when we already are an unprivileged user."""
    if os.geteuid() != 0:
        return None
    try:
        pw = pwd.getpwnam(TERMINAL_USER)
    except KeyError:
        raise RuntimeError(f"terminal user {TERMINAL_USER!r} does not exist; refusing to spawn a root shell")
    home = _terminal_home()
    if not (home and os.path.isdir(home)):
        home = pw.pw_dir
    return pw.pw_uid, pw.pw_gid, home


class Session:
    def __init__(self, sid: str, pid: int, fd: int, cols: int, rows: int):
        self.id = sid
        self.pid = pid
        self.fd = fd
        self.cols, self.rows = cols, rows
        self.created = time.time()
        self.last_io = self.created
        self.detached_at: float | None = self.created
        self.ring = bytearray()
        self.listeners: set[asyncio.Queue] = set()
        self.exit_code: int | None = None
        self.reader_installed = False
        self.pending = bytearray()      # input not yet accepted by the (non-blocking) master fd
        self.writer_installed = False

    @property
    def attached(self):
        return bool(self.listeners)

    def push(self, data: bytes):
        self.ring += data
        if len(self.ring) > RING_BYTES:
            del self.ring[: len(self.ring) - RING_BYTES]
        for q in list(self.listeners):
            _offer(q, data)

    def notify(self, msg: dict):
        for q in list(self.listeners):
            _offer(q, msg)

    def resize(self, cols: int, rows: int):
        try:
            cols = max(20, min(500, int(cols))); rows = max(5, min(300, int(rows)))
        except (TypeError, ValueError):
            return
        self.cols, self.rows = cols, rows
        if self.exit_code is not None:
            return
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            os.kill(self.pid, signal.SIGWINCH)
        except OSError:
            pass

    def write(self, data: bytes):
        """Never blocks the loop: what the tty will not take now waits in `pending` for add_writer."""
        if self.exit_code is not None:
            return
        self.last_io = time.time()
        self.pending += data
        if len(self.pending) > INPUT_MAX_PENDING:
            del self.pending[: len(self.pending) - INPUT_MAX_PENDING]
        self._flush()

    def _flush(self):
        loop = asyncio.get_running_loop()
        while self.pending:
            try:
                n = os.write(self.fd, bytes(self.pending[:4096]))
            except BlockingIOError:
                if not self.writer_installed:
                    loop.add_writer(self.fd, self._flush); self.writer_installed = True
                return
            except OSError:
                self.pending.clear(); break
            del self.pending[:n]
        if self.writer_installed:
            loop.remove_writer(self.fd); self.writer_installed = False

    def info(self):
        return {"id": self.id, "pid": self.pid, "created": self.created, "last_io": self.last_io,
                "attached": self.attached, "exit_code": self.exit_code, "cols": self.cols, "rows": self.rows}


class Registry:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self._reaper: asyncio.Task | None = None

    # -- lifecycle -----------------------------------------------------------
    def spawn(self, cols=80, rows=24) -> Session:
        live = [s for s in self.sessions.values() if s.exit_code is None]
        if len(live) >= MAX_SESSIONS:
            raise HTTPException(429, f"at most {MAX_SESSIONS} terminal sessions")
        tu = target_user()      # raises before fork when root would leak through
        home = tu[2] if tu else (os.environ.get("HOME") or os.getcwd())
        sid = secrets.token_hex(8)
        pid, fd = pty.fork()
        if pid == 0:            # child: drop privileges, then exec the shell
            try:
                if tu:
                    uid, gid, _ = tu
                    os.setgroups([gid])
                    os.setgid(gid)
                    os.setuid(uid)
                    if os.getuid() != uid or os.geteuid() != uid:
                        raise OSError("uid drop failed")
                env = {"TERM": "xterm-256color", "COLORTERM": "truecolor", "HOME": home,
                       "USER": TERMINAL_USER if tu else pwd.getpwuid(os.getuid()).pw_name,
                       "LANG": os.environ.get("LANG", "C.UTF-8"), "SHELL": "/bin/bash",
                       "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                       "HERMES_HOME": store.hermes_root_home()}
                try:
                    os.chdir(home)
                except OSError:          # HOME exists but is not usable by the dropped uid
                    home = tu_home = (pwd.getpwuid(os.getuid()).pw_dir if tu else "/")
                    env["HOME"] = tu_home
                    try:
                        os.chdir(home)
                    except OSError:
                        os.chdir("/")
                os.execve("/bin/bash", ["bash", "-i"], env)
            except BaseException as e:  # noqa: BLE001 - must never return into the parent's code
                os.write(1, f"\r\nhermes-hq: cannot start shell: {e}\r\n".encode())
            finally:
                os._exit(127)
        os.set_blocking(fd, False)
        s = Session(sid, pid, fd, cols, rows)
        s.resize(cols, rows)
        self.sessions[sid] = s
        self._install_reader(s)
        self._ensure_reaper()
        return s

    def _install_reader(self, s: Session):
        loop = asyncio.get_running_loop()

        def on_readable():
            try:
                data = os.read(s.fd, READ_CHUNK)
            except BlockingIOError:
                return
            except OSError:
                data = b""
            if data:
                s.last_io = time.time()
                s.push(data)
                return
            loop.remove_reader(s.fd)
            self._finish(s)

        loop.add_reader(s.fd, on_readable)
        s.reader_installed = True

    def _finish(self, s: Session, attempt: int = 0):
        """PTY EOF: reap without blocking. A shell that closed its tty but lingers gets SIGHUP, then SIGKILL."""
        if s.exit_code is not None:
            return
        try:
            pid, status = os.waitpid(s.pid, os.WNOHANG)
        except ChildProcessError:
            pid, status = s.pid, 0
        if pid == 0:
            try:
                os.kill(s.pid, signal.SIGHUP if attempt < 4 else signal.SIGKILL)
            except ProcessLookupError:
                pass
            asyncio.get_running_loop().call_later(0.5, self._finish, s, attempt + 1)
            return
        self._mark_exited(s, os.waitstatus_to_exitcode(status) if status else 0)

    def _mark_exited(self, s: Session, code: int):
        if s.exit_code is not None:
            return
        s.exit_code = code
        try:
            loop = asyncio.get_running_loop()
            if s.reader_installed:
                loop.remove_reader(s.fd); s.reader_installed = False
            if s.writer_installed:
                loop.remove_writer(s.fd); s.writer_installed = False
        except RuntimeError:
            pass
        try:
            os.close(s.fd)
        except OSError:
            pass
        s.notify({"t": "exit", "code": code})

    async def close(self, sid: str, wait=2.0):
        """SIGHUP (what a closed terminal sends), SIGKILL after `wait`; polls with await, never blocks the loop."""
        s = self.sessions.pop(sid, None)
        if s is None:
            raise HTTPException(404, "no such terminal session")
        if s.exit_code is None:
            self._mark_exited(s, -1)
            await self._reap(s, wait)
        return s

    async def _reap(self, s: Session, wait: float):
        try:
            os.kill(s.pid, signal.SIGHUP)
        except ProcessLookupError:
            return
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                if os.waitpid(s.pid, os.WNOHANG)[0]:
                    return
            except ChildProcessError:
                return
            await asyncio.sleep(0.05)
        try:
            os.kill(s.pid, signal.SIGKILL)
            os.waitpid(s.pid, 0)      # immediate after SIGKILL
        except (ProcessLookupError, ChildProcessError):
            pass

    def close_all(self):
        """Server shutdown: SIGKILL everything still alive, synchronously."""
        for sid, s in list(self.sessions.items()):
            self.sessions.pop(sid, None)
            if s.exit_code is None:
                try:
                    os.kill(s.pid, signal.SIGKILL); os.waitpid(s.pid, 0)
                except (ProcessLookupError, ChildProcessError):
                    pass
                try:
                    os.close(s.fd)
                except OSError:
                    pass

    def _ensure_reaper(self):
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap_loop())

    async def _reap_loop(self):
        while self.sessions:
            await asyncio.sleep(15)
            now = time.time()
            for sid, s in list(self.sessions.items()):
                dead = s.exit_code is not None and not s.attached
                stale = s.detached_at is not None and not s.attached and now - s.detached_at > DETACH_TTL
                if dead or stale:
                    try:
                        await self.close(sid, wait=0.5)
                    except Exception:
                        pass

    # -- attach --------------------------------------------------------------
    def attach(self, s: Session) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        s.listeners.add(q)
        s.detached_at = None
        return q

    def detach(self, s: Session, q: asyncio.Queue):
        s.listeners.discard(q)
        if not s.listeners:
            s.detached_at = time.time()


REGISTRY = Registry()


# -- HTTP ------------------------------------------------------------------------
@router.get("/sessions")
async def list_sessions():
    return {"sessions": [s.info() for s in REGISTRY.sessions.values()], "max": MAX_SESSIONS,
            "user": TERMINAL_USER if os.geteuid() == 0 else pwd.getpwuid(os.geteuid()).pw_name}


class SpawnBody(BaseModel):
    cols: int = 80
    rows: int = 24


@router.post("/spawn")
async def spawn(body: SpawnBody | None = None):
    body = body or SpawnBody()
    try:
        s = REGISTRY.spawn(body.cols, body.rows)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return s.info()


@router.post("/{sid}/close")
async def close(sid: str):
    s = await REGISTRY.close(sid)
    return {"id": s.id, "exit_code": s.exit_code}


# -- WebSocket -----------------------------------------------------------------
def _same_origin(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return False          # browsers always send Origin on WS; scripts without it are refused
    host = ws.headers.get("host", "")
    return urlsplit(origin).netloc.lower() == host.lower()


@router.websocket("/ws")
async def terminal_ws(ws: WebSocket):
    sessions: A.Sessions = ws.app.state.sessions
    if sessions.get(ws.cookies.get(A.COOKIE)) is None:
        await ws.close(code=CLOSE_NO_AUTH); return
    if not _same_origin(ws):
        await ws.close(code=CLOSE_BAD_ORIGIN); return
    sid = ws.query_params.get("session") or ""
    try:
        cols = int(ws.query_params.get("cols") or 80); rows = int(ws.query_params.get("rows") or 24)
    except ValueError:
        cols, rows = 80, 24
    s = REGISTRY.sessions.get(sid)
    reattach = s is not None
    # Auth/origin failures above are refused at the handshake (HTTP 403; browsers see 1006). Everything
    # below is accepted first so the browser receives the real close code and reason.
    await ws.accept()

    async def refuse(code: int, reason: str):
        await ws.send_text(json.dumps({"t": "err", "code": code, "reason": reason}))
        await ws.close(code=code, reason=reason[:120])

    if s is None:
        if sid:
            await refuse(CLOSE_NOT_FOUND, "session gone"); return
        try:
            s = REGISTRY.spawn(cols, rows)
        except HTTPException as e:
            await refuse(CLOSE_LIMIT if e.status_code == 429 else 1011, str(e.detail)); return
        except RuntimeError as e:
            await refuse(1011, str(e)); return
    q = REGISTRY.attach(s)
    await ws.send_text(json.dumps({"t": "hello", "id": s.id, "reattach": reattach, "exited": s.exit_code}))
    if reattach and s.ring:
        await ws.send_bytes(bytes(s.ring))
    if reattach and s.exit_code is None:
        s.resize(cols, rows)

    async def pump():
        while True:
            item = await q.get()
            if isinstance(item, (bytes, bytearray)):
                buf = bytearray(item)
                while not q.empty():           # coalesce bursts into one frame
                    nxt = q.get_nowait()
                    if isinstance(nxt, (bytes, bytearray)):
                        buf += nxt
                    else:
                        await ws.send_bytes(bytes(buf)); buf = bytearray()
                        await ws.send_text(json.dumps(nxt))
                if buf:
                    await ws.send_bytes(bytes(buf))
            else:
                await ws.send_text(json.dumps(item))

    task = asyncio.get_running_loop().create_task(pump())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            t = msg.get("t")
            if t == "i":
                if sessions.get(ws.cookies.get(A.COOKIE)) is None:      # logged out meanwhile: stop executing input
                    await ws.close(code=CLOSE_NO_AUTH); break
                s.write(str(msg.get("d", "")).encode())
            elif t == "r":
                s.resize(msg.get("cols", s.cols), msg.get("rows", s.rows))
            elif t == "p":
                await ws.send_text('{"t":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        task.cancel()
        REGISTRY.detach(s, q)
