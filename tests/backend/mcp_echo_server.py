#!/usr/bin/env python3
"""Minimal MCP stdio server (JSON-RPC 2.0, newline-delimited) with two tools — used by the 6-4 tests
and the live proof. No dependencies, so any python can run it."""
import json
import sys

TOOLS = [
    {"name": "echo", "description": "Echo the text back", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "add", "description": "Add two numbers", "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}},
]


def reply(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n"); sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    m, rid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if m == "initialize":
        reply(rid, {"protocolVersion": p.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}, "prompts": {}, "resources": {}}, "serverInfo": {"name": "hq-echo", "version": "0.1"}})
    elif m == "tools/list":
        reply(rid, {"tools": TOOLS})
    elif m == "tools/call":
        a = p.get("arguments") or {}
        out = a.get("text", "") if p.get("name") == "echo" else str(a.get("a", 0) + a.get("b", 0))
        reply(rid, {"content": [{"type": "text", "text": out}]})
    elif m in ("prompts/list", "resources/list"):
        reply(rid, {"prompts": []} if m == "prompts/list" else {"resources": []})
    elif m == "ping":
        reply(rid, {})
    elif rid is not None:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {m}"}}) + "\n"); sys.stdout.flush()
