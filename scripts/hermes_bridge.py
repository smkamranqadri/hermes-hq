#!/usr/bin/env python3
"""Run Hermes' own memory-provider / learning-graph code for one profile.

Executed by hermes-hq with Hermes' venv python, cwd = the Hermes checkout and
HERMES_HOME = the profile's home, so every rule (schemas, secret storage, config
writes, readiness gate) is Hermes' own — nothing is re-implemented here.

    hermes_bridge.py <op>   with a JSON object on stdin; one JSON object on stdout.
ops: providers | config | activate | setup | graph | node | limits
     skills_list | skills_content | skills_create | skills_update | skills_toggle
     hub_sources | hub_search | hub_preview | hub_scan
     mcp_list | mcp_add | mcp_remove | mcp_test | mcp_enabled | mcp_catalog | mcp_catalog_install
"""
import json
import sys

_real_stdout = sys.stdout
sys.stdout = sys.stderr          # anything Hermes prints during import/work goes to stderr


def main():
    op = sys.argv[1] if len(sys.argv) > 1 else ""
    body = json.loads(sys.stdin.read() or "{}")
    out = OPS[op](body)
    # Own line, both sides: Hermes child processes (pip during a provider setup) share fd 1 with us and
    # may leave the cursor mid-line; the job runner parses the last line that is JSON.
    _real_stdout.write("\n" + json.dumps(out, default=str) + "\n")
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


def _guard(fn):
    """Hermes raises HTTPException for bad names/state; report it instead of a traceback."""
    def inner(body):
        from fastapi import HTTPException
        try:
            return fn(body)
        except HTTPException as e:
            return {"ok": False, "status": e.status_code, "error": str(e.detail)}
    return inner


@_guard
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


@_guard
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


# -- skills (Hermes dashboard router handlers, called in-process under this HERMES_HOME) ------------
def _run_async(coro):
    import asyncio
    return asyncio.run(coro)


def _router_call(fn, *a, **kw):
    """Call a Hermes router handler; its HTTPException becomes {"ok": False, "status", "error"}."""
    _ws()                                  # binds the late state the routers resolve against
    from fastapi import HTTPException
    try:
        res = _run_async(fn(*a, **kw))
    except HTTPException as e:
        return {"ok": False, "status": e.status_code, "error": str(e.detail)}
    return res


def op_skills_list(body):
    from hermes_cli.web_routers.skills import get_skills
    from tools.skill_manager_tool import _find_skill
    from tools.skills_tool import _parse_frontmatter
    import os
    rows = _router_call(get_skills, None)
    if isinstance(rows, dict) and rows.get("ok") is False:
        return rows
    for s in rows:
        found = _find_skill(s["name"])
        s["path"] = str(found["path"]) if found else ""
        s.setdefault("tags", []); s.setdefault("version", ""); s.setdefault("author", ""); s.setdefault("homepage", ""); s["mtime"] = None
        md = os.path.join(s["path"], "SKILL.md") if s["path"] else ""
        if md and os.path.isfile(md):
            try:
                fm, _ = _parse_frontmatter(open(md, encoding="utf-8", errors="replace").read())
                hermes = (fm.get("metadata") or {}).get("hermes") or {} if isinstance(fm.get("metadata"), dict) else {}
                s["tags"] = [str(t) for t in (hermes.get("tags") or fm.get("tags") or [])][:12]
                s["version"] = str(fm.get("version") or ""); s["author"] = str(fm.get("author") or "")
                s["homepage"] = str(hermes.get("homepage") or fm.get("homepage") or "")
                s["mtime"] = os.stat(md).st_mtime
            except Exception:
                pass
    return {"ok": True, "skills": rows}


def op_skills_content(body):
    from hermes_cli.web_routers.skills import get_skill_content
    return _router_call(get_skill_content, str(body.get("name") or ""), None)


def op_skills_create(body):
    from hermes_cli.web_routers.skills import create_skill
    from hermes_cli.web_models import SkillCreate
    return _router_call(create_skill, SkillCreate(name=str(body.get("name") or ""), content=str(body.get("content") or ""), category=body.get("category") or None))


def op_skills_update(body):
    from hermes_cli.web_routers.skills import update_skill_content
    from hermes_cli.web_models import SkillContentUpdate
    return _router_call(update_skill_content, SkillContentUpdate(name=str(body.get("name") or ""), content=str(body.get("content") or "")))


def op_skills_toggle(body):
    from hermes_cli.web_routers.skills import toggle_skill
    from hermes_cli.web_models import SkillToggle
    return _router_call(toggle_skill, SkillToggle(name=str(body.get("name") or ""), enabled=bool(body.get("enabled"))), None)


def op_hub_sources(body):
    from hermes_cli.web_routers.skills import list_skills_hub_sources
    return _router_call(list_skills_hub_sources, None)


def op_hub_search(body):
    from hermes_cli.web_routers.skills import search_skills_hub
    return _router_call(search_skills_hub, q=str(body.get("q") or ""), source=str(body.get("source") or "all"), limit=int(body.get("limit") or 20), profile=None)


def op_hub_preview(body):
    from hermes_cli.web_routers.skills import preview_skill_hub
    return _router_call(preview_skill_hub, identifier=str(body.get("identifier") or ""), profile=None)


def op_hub_scan(body):
    from hermes_cli.web_routers.skills import scan_skill_hub
    return _router_call(scan_skill_hub, identifier=str(body.get("identifier") or ""), profile=None)


OPS.update({"skills_list": op_skills_list, "skills_content": op_skills_content, "skills_create": op_skills_create,
            "skills_update": op_skills_update, "skills_toggle": op_skills_toggle, "hub_sources": op_hub_sources,
            "hub_search": op_hub_search, "hub_preview": op_hub_preview, "hub_scan": op_hub_scan})


# -- MCP (Hermes dashboard router handlers) --------------------------------------------------------
def op_mcp_list(body):
    from hermes_cli.web_routers.mcp import list_mcp_servers
    return _router_call(list_mcp_servers, None)


def op_mcp_add(body):
    from hermes_cli.web_routers.mcp import add_mcp_server
    from hermes_cli.web_models import MCPServerCreate
    from pydantic import SecretStr
    tok = body.get("bearer_token")
    m = MCPServerCreate(name=str(body.get("name") or ""), url=body.get("url") or None, command=body.get("command") or None,
                        args=[str(a) for a in (body.get("args") or [])], env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
                        auth=body.get("auth") or None, bearer_token=SecretStr(tok) if tok else None)
    return _router_call(add_mcp_server, m, None)


def op_mcp_remove(body):
    from hermes_cli.web_routers.mcp import remove_mcp_server
    return _router_call(remove_mcp_server, str(body.get("name") or ""), None)


def op_mcp_test(body):
    from hermes_cli.web_routers.mcp import test_mcp_server
    return _router_call(test_mcp_server, str(body.get("name") or ""), None)


def op_mcp_enabled(body):
    from hermes_cli.web_routers.mcp import set_mcp_server_enabled
    from hermes_cli.web_models import MCPEnabledToggle
    return _router_call(set_mcp_server_enabled, str(body.get("name") or ""), MCPEnabledToggle(enabled=bool(body.get("enabled"))), None)


def op_mcp_catalog(body):
    from hermes_cli.web_routers.mcp import list_mcp_catalog
    return _router_call(list_mcp_catalog, None)


def op_mcp_catalog_install(body):
    """Sync catalog installs only; entries that need a git bootstrap are reported back so hq runs
    `hermes mcp install <name>` as a job (the CLI path Hermes uses for the slow clone)."""
    from hermes_cli import mcp_catalog
    from hermes_cli.web_routers.mcp import install_mcp_catalog_entry
    from hermes_cli.web_models import MCPCatalogInstall
    name = str(body.get("name") or "")
    entry = mcp_catalog.get_entry(name)
    if entry is None:
        return {"ok": False, "status": 404, "error": f"No catalog entry '{name}'"}
    if entry.install is not None:
        # write the env values the same way Hermes does, then let hq run the CLI install job
        from hermes_cli.config import save_env_value
        for k, v in (body.get("env") or {}).items():
            if v:
                save_env_value(str(k), str(v))
        return {"ok": True, "name": name, "needs_cli_install": True}
    return _router_call(install_mcp_catalog_entry, MCPCatalogInstall(name=name, env={str(k): str(v) for k, v in (body.get("env") or {}).items()}, enable=bool(body.get("enable", True))), None)


OPS.update({"mcp_list": op_mcp_list, "mcp_add": op_mcp_add, "mcp_remove": op_mcp_remove, "mcp_test": op_mcp_test,
            "mcp_enabled": op_mcp_enabled, "mcp_catalog": op_mcp_catalog, "mcp_catalog_install": op_mcp_catalog_install})

if __name__ == "__main__":
    main()
