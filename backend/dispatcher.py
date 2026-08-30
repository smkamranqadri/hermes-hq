"""In-process dispatcher loop: replaces the Hermes cron tick from the legacy
Work Manager so a single `hermes-hq serve` runs the whole control plane."""
import logging
import threading
import time

from core import wm_dispatch, wm_store

log = logging.getLogger("backend.dispatcher")


class DispatcherLoop:
    def __init__(self, interval: float = 30.0, enabled: bool = True):
        self.interval = interval
        self.enabled = enabled
        self.last_tick = None
        self.last_summary = None
        self.last_error = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not self.enabled:
            log.info("dispatcher disabled")
            return
        self._thread = threading.Thread(target=self._run, name="hq-dispatcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def tick_once(self):
        summary = wm_dispatch.run_dispatch(db_path=wm_store.DEFAULT_DB_PATH)
        self.last_tick = time.time()
        try:   # notifications + web push must not depend on a browser polling
            from backend import push
            push.sync_and_push()
        except Exception:
            log.exception("push sync failed")
        self.last_summary = summary
        self.last_error = None
        return summary

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as exc:  # keep the loop alive; surface via /api/system
                self.last_error = repr(exc)
                log.exception("dispatcher tick failed")
            self._stop.wait(self.interval)

    def status(self):
        return {
            "enabled": self.enabled,
            "interval": self.interval,
            "alive": bool(self._thread and self._thread.is_alive()),
            "last_tick": self.last_tick,
            "last_error": self.last_error,
        }
