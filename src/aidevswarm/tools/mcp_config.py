"""Load ``.mcp.json`` and convert it to a dict the Claude Agent SDK accepts.

Phase 2 only uses stdio MCP servers; HTTP/SDK transports can be added
to ``_parse`` later without changing callers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk.types import McpStdioServerConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MCP_JSON = REPO_ROOT / ".mcp.json"


def load_mcp_servers(path: Path | None = None) -> dict[str, McpStdioServerConfig]:
    """Return a SDK-ready mapping ``name -> McpStdioServerConfig``.

    Missing or empty ``.mcp.json`` resolves to an empty dict so the SDK
    runs without MCP rather than crashing. Non-stdio entries are
    skipped (warning logged in the future Phase that adds them).
    """
    target = path or DEFAULT_MCP_JSON
    if not target.is_file():
        return {}

    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    servers = raw.get("mcpServers") or raw.get("mcp_servers") or {}
    if not isinstance(servers, Mapping):
        return {}

    out: dict[str, McpStdioServerConfig] = {}
    for name, entry in servers.items():
        cfg = _parse(entry)
        if cfg is not None:
            out[str(name)] = cfg
    return out


def _parse(entry: object) -> McpStdioServerConfig | None:
    """Build a :class:`McpStdioServerConfig` from a raw mapping."""
    if not isinstance(entry, Mapping):
        return None
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        return None
    args = entry.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return None
    env = entry.get("env") or {}
    if not isinstance(env, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        env = {}
    config: dict[str, Any] = {
        "type": "stdio",
        "command": command,
        "args": list(args),
    }
    if env:
        config["env"] = dict(env)
    return cast(McpStdioServerConfig, config)
