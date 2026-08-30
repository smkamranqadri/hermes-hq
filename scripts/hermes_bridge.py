#!/usr/bin/env python3
"""Run Hermes' own memory-provider / learning-graph code for one profile.

Executed by hermes-hq with Hermes' venv python, cwd = the Hermes checkout and
HERMES_HOME = the profile's home, so every rule (schemas, secret storage, config
writes, readiness gate) is Hermes' own — nothing is re-implemented here.

    hermes_bridge.py <op>   with a JSON object on stdin; one JSON object on stdout.
ops: providers | config | activate | setup | graph | node | limits
"""
import json
import sys

_real_stdout = sys.stdout
sys.stdout = sys.stderr          # anything Hermes prints during import/work goes to stderr


def main():
    op = sys.argv[1] if len(sys.argv) > 1 else ""
    body = json.loads(sys.stdin.read() or "{}")
    out = OPS[op](body)
    _real_stdout.write(json.dumps(out, default=str))
    _real_stdout.flush()


def _ws():
    import hermes_cli.web_server as w
    return w


def _active(w):
    from hermes_cli.config import load_config
    mem = load_config().get("memory")
    return w._normalize_memory_provider_name(mem.get("provider")) if isinstance(mem, dict) else ""


def op_providers(body):
    w = _ws()
    rows = w._discover_memory_provider_statuses()
    for row in rows:
        if row.get("status") == "missing":
            row["label"], row["fields"] = row["name"], []
            continue
        provider = w._load_memory_provider(row["name"])
        payload = w._memory_provider_payload(row["name"], provider) if provider is not None else {"label": row["name"], "fields": []}
        row["label"] = payload.get("label") or row["name"]
        row["fields"] = payload.get("fields") or []
    return {"active": _active(w), "providers": rows}


def op_config(body):
    w = _ws()
    name = str(body.get("name") or "")
    w._require_valid_memory_provider_name(name)
    provider = w._load_memory_provider(name)
    if provider is None:
        return {"ok": False, "error": f"Unknown memory provider: {name}"}
    try:
        w._write_memory_provider_config_values(name, provider, body.get("values") or {})
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    res = {"ok": True}
    if body.get("activate"):
        res.update(op_activate({"name": name}))
    return res


def op_activate(body):
    w = _ws()
    from hermes_cli.config import load_config, save_config
    name = w._normalize_memory_provider_name(body.get("name") or "")
    try:
        w._require_memory_provider_ready(name)
    except Exception as e:           # HTTPException from Hermes carries .detail
        return {"ok": False, "error": getattr(e, "detail", str(e))}
    with w._CONFIG_MUTATION_LOCK:
        cfg = load_config()
        if not isinstance(cfg.get("memory"), dict):
            cfg["memory"] = {}
        cfg["memory"]["provider"] = name
        save_config(cfg)
    return {"ok": True, "active": name}


def op_setup(body):
    w = _ws()
    name = str(body.get("name") or "")
    w._require_valid_memory_provider_name(name)
    provider = w._load_memory_provider(name)
    if provider is None and not w._memory_provider_manifest(name):
        return {"ok": False, "error": f"Unknown memory provider: {name}"}
    if provider is not None and body.get("values"):
        try:
            w._write_memory_provider_config_values(name, provider, body["values"])
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    return w._install_memory_provider_setup(name)


def op_graph(body):
    from agent.learning_graph import build_learning_graph
    return build_learning_graph()


def op_node(body):
    from agent.learning_mutations import node_detail
    return node_detail(str(body.get("id") or ""))


def op_limits(body):
    from hermes_cli.config import load_config
    mem = load_config().get("memory") or {}
    return {"memory": int(mem.get("memory_char_limit") or 2200), "user": int(mem.get("user_char_limit") or 1375),
            "enabled": bool(mem.get("memory_enabled", True)), "provider": str(mem.get("provider") or "")}


OPS = {"providers": op_providers, "config": op_config, "activate": op_activate, "setup": op_setup,
       "graph": op_graph, "node": op_node, "limits": op_limits}

if __name__ == "__main__":
    main()
