"""Model pricing from models.dev (owner choice, 2026-08-30) for cost estimates
when Hermes reports no cost (cost_status "included"/None).

The catalogue (https://models.dev/api.json, ~1 MB) is fetched at most once a
day into `<hq home>/models_dev.json`; a failed fetch is retried after 10 min
and the last good file keeps serving. Prices are USD per million tokens.
"""
import json, logging, os, threading, time, urllib.request

from core import wm_store as store

URL = "https://models.dev/api.json"
TTL = 24 * 3600
RETRY = 600
_lock = threading.Lock()
_state = {"loaded_at": 0.0, "failed_at": 0.0, "index": None}
log = logging.getLogger("backend.pricing")


def _cache_path():
    return os.path.join(store.hq_home(), "models_dev.json")


def _build_index(data):
    """{model_id_lower: cost dict} across providers; bare ids win over provider/-prefixed duplicates."""
    idx = {}
    for prov in (data or {}).values():
        for mid, m in (prov.get("models") or {}).items():
            cost = m.get("cost") if isinstance(m, dict) else None
            if not isinstance(cost, dict) or "input" not in cost:
                continue
            key = mid.lower()
            if key not in idx or "/" in key:
                idx[key] = cost
    return idx


def _load(force=False):
    now = time.time()
    with _lock:
        fresh = _state["index"] is not None and now - _state["loaded_at"] < TTL
        if fresh and not force:
            return _state["index"]
        path = _cache_path()
        stale_ok = None
        if os.path.exists(path):
            try:
                with open(path) as f:
                    stale_ok = _build_index(json.load(f))
                if now - os.path.getmtime(path) < TTL and not force:
                    _state.update(index=stale_ok, loaded_at=now)
                    return stale_ok
            except (OSError, ValueError):
                stale_ok = None
        if now - _state["failed_at"] < RETRY and stale_ok is not None:
            return stale_ok
        try:
            with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "hermes-hq"}), timeout=8) as r:
                raw = r.read()
            data = json.loads(raw)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
            _state.update(index=_build_index(data), loaded_at=now)
            return _state["index"]
        except (OSError, ValueError) as e:
            log.warning("models.dev fetch failed: %s", e)
            _state["failed_at"] = now
            if stale_ok is not None:
                _state.update(index=stale_ok, loaded_at=now)
            return stale_ok


def lookup(model):
    """Cost dict for a model id, matching exact, then provider-stripped, then the longest prefix
    (Hermes ids carry suffixes like `-900k`). Returns (matched_id, cost) or (None, None)."""
    if not model:
        return None, None
    idx = _load()
    if not idx:
        return None, None
    m = model.lower()
    for cand in (m, m.split("/", 1)[-1]):
        if cand in idx:
            return cand, idx[cand]
    best = None
    for key in idx:
        if m.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return (best, idx[best]) if best else (None, None)


def estimate(model, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    """USD estimate from models.dev prices, or None when the model is unknown."""
    matched, cost = lookup(model)
    if not cost:
        return None
    usd = ((input_tokens or 0) * cost.get("input", 0) + (output_tokens or 0) * cost.get("output", 0)
           + (cache_read or 0) * cost.get("cache_read", 0) + (cache_write or 0) * cost.get("cache_write", 0)) / 1e6
    return {"usd": round(usd, 4), "model": matched, "source": "models.dev"}
