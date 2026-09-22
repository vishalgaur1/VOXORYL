from __future__ import annotations

"""
Minimal MCP bridge — load servers from setup/mcp.servers.json and call tools.
Inspired by MCP server ecosystem + LocalClaw MCP client pattern.
Supports streamable HTTP MCP where available; stdio listed for documentation.
"""

import json
from pathlib import Path
from typing import Any

import httpx

from voxoryl.config import settings

ROOT = Path(__file__).resolve().parent.parent


def mcp_config_path() -> Path:
    return ROOT / "setup" / "mcp.servers.json"


def load_mcp_config() -> dict[str, Any]:
    path = mcp_config_path()
    if not path.exists():
        example = ROOT / "setup" / "mcp.servers.example.json"
        if example.exists() and not path.exists():
            return json.loads(example.read_text(encoding="utf-8"))
        return {"servers": []}
    return json.loads(path.read_text(encoding="utf-8"))


async def list_mcp_tools() -> dict[str, Any]:
    cfg = load_mcp_config()
    servers = cfg.get("servers") or []
    tools: list[dict[str, Any]] = []
    for srv in servers:
        if not srv.get("enabled", True):
            continue
        name = srv.get("name")
        kind = srv.get("transport")
        if kind == "http" and srv.get("url"):
            # Best-effort OpenAPI-ish or MCP HTTP probe
            tools.append(
                {
                    "server": name,
                    "transport": "http",
                    "url": srv.get("url"),
                    "tools": srv.get("tools") or ["(configure tools in mcp.servers.json)"],
                }
            )
        elif kind == "stdio":
            tools.append(
                {
                    "server": name,
                    "transport": "stdio",
                    "command": srv.get("command"),
                    "args": srv.get("args") or [],
                    "tools": srv.get("tools") or [],
                    "note": "Stdio MCP servers are declared here; wire with Cursor/Claude Desktop or enable HTTP bridge.",
                }
            )
    return {"ok": True, "path": str(mcp_config_path()), "servers": tools}


async def call_mcp_http(server: str, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = load_mcp_config()
    match = next((s for s in (cfg.get("servers") or []) if s.get("name") == server), None)
    if not match:
        return {"ok": False, "error": f"Unknown MCP server: {server}"}
    if match.get("transport") != "http" or not match.get("url"):
        return {"ok": False, "error": "Server is not HTTP transport", "hint": match}
    url = str(match["url"]).rstrip("/") + "/tools/call"
    payload = {"name": tool, "arguments": arguments or {}}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=payload)
            if r.status_code >= 400:
                # fallback generic
                r2 = await client.post(str(match["url"]), json={"tool": tool, "arguments": arguments or {}})
                if r2.status_code >= 400:
                    return {"ok": False, "error": r.text}
                return {"ok": True, "result": r2.json()}
            return {"ok": True, "result": r.json()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def tool_mcp(action: str = "list", server: str = "", tool: str = "", args: dict[str, Any] | None = None) -> dict[str, Any]:
    if action == "list":
        return await list_mcp_tools()
    if action == "call":
        tname = (tool or "").lower()
        if any(k in tname for k in ("delete", "unlink", "remove_file", "rmdir", "remove")):
            from voxoryl.safety import may_delete_path, refuse

            payload = args or {}
            path = payload.get("path") or payload.get("file") or payload.get("filepath") or payload.get("uri") or ""
            if not path:
                return refuse("file", detail="MCP delete without path refused.")
            gate = may_delete_path(str(path).replace("file://", ""))
            if not gate.get("ok"):
                return gate
        return await call_mcp_http(server, tool, args)
    return {"ok": False, "error": f"unknown mcp action {action}"}
