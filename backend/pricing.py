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


_providers = {}


def _build_index(data):
    """{model_id_lower: {"cost": {...}, "context": int|None}} across providers; bare ids win over provider/-prefixed duplicates.
    Also fills the provider catalogue used by the picker."""
    idx = {}
    _providers.clear()
    for pid, prov in (data or {}).items():
        _providers[pid] = {"id": pid, "name": prov.get("name") or pid, "models": sorted((prov.get("models") or {}).keys())}
        for mid, m in (prov.get("models") or {}).items():
            cost = m.get("cost") if isinstance(m, dict) else None
            if not isinstance(cost, dict) or "input" not in cost:
                continue
            key = mid.lower()
            if key not in idx or "/" in key:
                idx[key] = {"cost": cost, "context": ((m.get("limit") or {}).get("context") if isinstance(m.get("limit"), dict) else None)}
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
            return cand, idx[cand]["cost"]
    best = None
    for key in idx:
        if m.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return (best, idx[best]["cost"]) if best else (None, None)


def context_limit(model):
    """Context window (tokens) from models.dev for the matched model, else None."""
    matched, _ = lookup(model)
    return (_load() or {}).get(matched, {}).get("context") if matched else None


def context_estimate(model, transcript_chars, input_tokens, cache_read, cache_write, api_calls):
    """≈ tokens the next call will carry: transcript (chars/4) + system-prompt overhead inferred from Hermes'
    per-call prompt totals (prompt grows ~linearly, so avg prompt ≈ overhead + transcript/2). Honest label: estimate."""
    transcript = int((transcript_chars or 0) / 4)
    calls = api_calls or 0
    overhead = 0
    if calls > 0:
        avg_prompt = ((input_tokens or 0) + (cache_read or 0) + (cache_write or 0)) / calls
        overhead = max(0, int(avg_prompt - transcript / 2))
    used = transcript + overhead
    limit = context_limit(model)
    return {"used": used, "transcript": transcript, "overhead": overhead, "limit": limit,
            "pct": (round(100.0 * used / limit, 1) if limit else None), "source": "estimate; window from models.dev" if limit else "estimate"}


def estimate(model, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    """USD estimate from models.dev prices, or None when the model is unknown."""
    matched, cost = lookup(model)
    if not cost:
        return None
    usd = ((input_tokens or 0) * cost.get("input", 0) + (output_tokens or 0) * cost.get("output", 0)
           + (cache_read or 0) * cost.get("cache_read", 0) + (cache_write or 0) * cost.get("cache_write", 0)) / 1e6
    return {"usd": round(usd, 4), "model": matched, "source": "models.dev"}


def model_ids(prefix=None, limit=200, provider=None):
    """Known model ids from models.dev; optional provider (models.dev provider id) and substring filter."""
    idx = _load() or {}
    if provider and provider in _providers:
        ids = list(_providers[provider]["models"])
    else:
        ids = sorted(k for k in idx if "/" not in k)
    if prefix:
        p = prefix.lower()
        ids = [k for k in ids if p in k.lower()]
    return ids[:limit]


def providers():
    """[{id, name, models: n}] from models.dev, sorted by name."""
    _load()
    return sorted(({"id": p["id"], "name": p["name"], "models": len(p["models"])} for p in _providers.values()), key=lambda p: p["name"].lower())


# ---- Hermes-configured providers (owner: the picker lists what Hermes has, not the whole catalogue) ----
# Hermes provider id -> models.dev provider id for model suggestions (None = free text, all ids)
HERMES_TO_MODELS_DEV = {
    "openai-codex": "openai", "openai-api": "openai", "anthropic": "anthropic", "copilot": "github-copilot",
    "copilot-acp": "github-copilot", "gemini": "google", "xai-oauth": "xai", "xai": "xai", "openrouter": "openrouter",
    "opencode-go": "opencode-go", "opencode": "opencode", "qwen-oauth": "alibaba", "deepseek": "deepseek",
    "mistral": "mistral", "groq": "groq", "minimax-oauth": "minimax", "minimax": "minimax", "nous": None,
    "lmstudio": None, "ollama": None,
}
_REGISTRY_NAMES = {}


def _registry_names():
    """{id: display name} parsed from Hermes' provider registry when the source is readable; ids otherwise."""
    if _REGISTRY_NAMES:
        return _REGISTRY_NAMES
    import re
    for path in ("/opt/hermes/hermes_cli/auth.py", os.path.join(os.environ.get("HERMES_SRC", ""), "hermes_cli", "auth.py")):
        try:
            with open(path) as f:
                src = f.read()
        except OSError:
            continue
        for m in re.finditer(r'id="([a-z0-9-]+)",\s*name="([^"]+)"', src):
            _REGISTRY_NAMES[m.group(1)] = m.group(2)
        break
    return _REGISTRY_NAMES


def hermes_providers(profile=None):
    """Providers Hermes can route for this profile: auth.json `providers` + `credential_pool` (profile file first,
    then the root one) + config.yaml `model.provider`. [{id, name, active}]."""
    homes = []
    if profile and profile != store.ORCHESTRATOR_AGENT:
        homes.append(os.path.join(store.resolve_profiles_dir(), profile))
    homes.append(store.hermes_home())
    ids, active, cfg_active = [], None, None
    for home in homes:
        try:
            with open(os.path.join(home, "auth.json")) as f:
                a = json.load(f)
        except (OSError, ValueError):
            a = {}
        for k in list((a.get("providers") or {}).keys()) + list((a.get("credential_pool") or {}).keys()):
            if k not in ids:
                ids.append(k)
        active = active or a.get("active_provider")
        try:
            import yaml
            with open(os.path.join(home, "config.yaml")) as f:
                c = yaml.safe_load(f) or {}
            p = (c.get("model") or {}).get("provider") if isinstance(c.get("model"), dict) else None
            if p and p not in ids:
                ids.append(p)
            cfg_active = cfg_active or p
        except Exception:
            pass
    active = cfg_active or active   # config.yaml model.provider is what the agent actually runs with
    names = _registry_names()
    return [{"id": i, "name": names.get(i, i), "active": i == active} for i in ids]


def models_for_hermes_provider(hermes_id, prefix=None, limit=200):
    _load()
    md = HERMES_TO_MODELS_DEV.get(hermes_id, hermes_id)
    return model_ids(prefix, limit=limit, provider=md if md in _providers else None)
