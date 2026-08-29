"""hermes-hq HTTP service: REST + static UI, engine-backed, dispatcher in-process."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__
from backend import auth as A
from backend.api import router as api_router
from backend.writes import router as write_router, make_auth_routes
from backend import gateways
from backend.dispatcher import DispatcherLoop
from core import wm_store

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def create_app(dispatcher_enabled: bool = True, interval: float = 30.0, password: str | None = None) -> FastAPI:
    dispatcher = DispatcherLoop(interval=interval, enabled=dispatcher_enabled)
    sweeper = gateways.IdleSweeper(enabled=dispatcher_enabled)
    os.makedirs(wm_store.hq_home(), exist_ok=True)
    password = password or A.resolve_password()[0]
    sessions = A.Sessions()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        os.makedirs(wm_store.hq_home(), exist_ok=True)
        os.makedirs(wm_store.resolve_runs_dir(), exist_ok=True)
        wm_store.init_db(db_path=wm_store.DEFAULT_DB_PATH)
        dispatcher.start()
        sweeper.start()
        yield
        sweeper.stop()
        dispatcher.stop()
        try:
            gateways.stop_started()
        except Exception:  # never block shutdown
            pass

    app = FastAPI(title="hermes-hq", version=__version__, lifespan=lifespan)
    app.add_middleware(A.AuthMiddleware, sessions=sessions)
    app.include_router(make_auth_routes(sessions, password))
    app.include_router(api_router)
    app.include_router(write_router)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/system")
    def system():
        return {
            "version": __version__,
            "hermes_home": wm_store.hermes_home(),
            "hq_home": wm_store.hq_home(),
            "db_path": wm_store.DEFAULT_DB_PATH,
            "hermes": wm_store.resolve_hermes(),
            "profiles_dir": wm_store.resolve_profiles_dir(),
            "schema_version": wm_store.get_meta("schema_version", db_path=wm_store.DEFAULT_DB_PATH),
            "paused": wm_store.get_meta("paused", db_path=wm_store.DEFAULT_DB_PATH) == "1",
            "running": wm_store.running_run_count(db_path=wm_store.DEFAULT_DB_PATH),
            "cap": int(wm_store.get_meta("concurrency_cap", db_path=wm_store.DEFAULT_DB_PATH) or 3),
            "imported_from": wm_store.get_meta("imported_from", db_path=wm_store.DEFAULT_DB_PATH),
            "dispatcher": dispatcher.status(),
        }

    if os.path.isdir(STATIC_DIR):
        app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = os.path.join(STATIC_DIR, path)
            if path and os.path.isfile(candidate):
                return FileResponse(candidate)
            # the SPA shell must never be served stale: hashed assets change on every build
            return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        def no_ui():
            return JSONResponse({"error": "UI not built: run `npm run build` in frontend/"}, status_code=503)

    return app
