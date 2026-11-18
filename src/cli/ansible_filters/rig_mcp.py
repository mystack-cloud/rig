"""MCP server declaration, resolution and rendering for rig.

This file is both an Ansible filter plugin (loaded by path, so it must stay
standard-library only and import nothing from ``cli``) and a normal module used
by the ``rig mcp`` commands.

A Workspace declares MCP servers once in ``.rig/mcp.json`` using Claude's
``.mcp.json`` schema. Values may reference MCP variables as ``${NAME}`` or
``${NAME:-default}``. At the authorize Stage each Agent's config is rendered
from that file, resolving each variable against the play variables (vaulted
values included) by name, ignoring case.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional

try:  # pragma: no cover - exercised only under Ansible
    from ansible.errors import AnsibleFilterError
except Exception:  # pragma: no cover

    class AnsibleFilterError(Exception):  # type: ignore[no-redef]
        pass


AGENTS = ("claude", "cursor", "opencode")
KNOWN_KEYS = ("type", "command", "args", "env", "url", "headers")
REMOTE_TYPES = ("http", "sse")
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


class McpConfigError(ValueError):
    """The neutral MCP config is invalid."""


def _warn(message: str) -> None:
    try:  # pragma: no cover - exercised only under Ansible
        from ansible.utils.display import Display

        Display().warning(message)
    except Exception:
        pass


# ---------------------------------------------------------------- parsing


def parse_config(raw: Any) -> dict[str, Any]:
    """Return a validated ``{"mcpServers": {...}}`` from text, a dict or nothing."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {"mcpServers": {}}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise McpConfigError(f"mcp.json is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise McpConfigError("mcp.json must be a JSON object")
    servers = raw.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise McpConfigError("mcp.json: 'mcpServers' must be an object")
    for name, server in servers.items():
        validate_server(name, server)
    return {"mcpServers": servers}


def validate_server(name: str, server: Any) -> None:
    where = f"mcp.json: server '{name}'"
    if not SERVER_NAME_RE.match(str(name)):
        raise McpConfigError(f"{where}: invalid name (use letters, digits, _ . -)")
    if not isinstance(server, dict):
        raise McpConfigError(f"{where} must be an object")
    has_command = "command" in server
    has_url = "url" in server
    if has_command == has_url:
        raise McpConfigError(f"{where} needs exactly one of 'command' or 'url'")
    if has_command:
        if not isinstance(server["command"], str) or not server["command"]:
            raise McpConfigError(f"{where}: 'command' must be a non-empty string")
        args = server.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise McpConfigError(f"{where}: 'args' must be a list of strings")
        _check_str_map(where, "env", server.get("env", {}))
        if server.get("type", "stdio") != "stdio":
            raise McpConfigError(f"{where}: a 'command' server must have type 'stdio'")
    else:
        if not isinstance(server["url"], str) or not server["url"]:
            raise McpConfigError(f"{where}: 'url' must be a non-empty string")
        _check_str_map(where, "headers", server.get("headers", {}))
        if server.get("type", "http") not in REMOTE_TYPES:
            raise McpConfigError(f"{where}: a 'url' server must have type 'http' or 'sse'")


def _check_str_map(where: str, key: str, value: Any) -> None:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise McpConfigError(f"{where}: '{key}' must be an object of strings")


def unknown_keys(server: Mapping[str, Any]) -> list[str]:
    return sorted(k for k in server if k not in KNOWN_KEYS)


# -------------------------------------------------------------- variables


def variables_in(obj: Any) -> dict[str, bool]:
    """Map each referenced variable name to whether it is required (no default)."""
    found: dict[str, bool] = {}

    def walk(x: Any) -> None:
        if isinstance(x, str):
            for m in VAR_RE.finditer(x):
                required = m.group(2) is None
                found[m.group(1)] = found.get(m.group(1), False) or required
        elif isinstance(x, list):
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)

    walk(obj)
    return found


def required_variables(obj: Any) -> list[str]:
    return sorted(n for n, req in variables_in(obj).items() if req)


class VariableLookup:
    """Case-insensitive lookup of variables by name."""

    def __init__(self, variables: Mapping[str, Any] | None = None, names: Iterable[str] | None = None) -> None:
        self._variables = variables
        self._by_lower: dict[str, list[str]] = {}
        keys = names if names is not None else (variables or {}).keys()
        for key in keys:
            self._by_lower.setdefault(str(key).lower(), []).append(str(key))

    def has(self, name: str) -> bool:
        return name.lower() in self._by_lower

    def get(self, name: str) -> str | None:
        """Value as text, or None when absent or empty."""
        keys = self._by_lower.get(name.lower(), [])
        if len(keys) > 1:
            raise McpConfigError(
                f"MCP variable {name} is ambiguous: variables {', '.join(sorted(keys))} differ only by case"
            )
        if not keys or self._variables is None:
            return None
        value = self._variables[keys[0]]
        if value is None:
            return None
        text = str(value)
        return text if text != "" else None


def missing_variables(server: Any, lookup: VariableLookup) -> list[str]:
    """Required variables of one server with no (non-empty) value."""
    out = []
    for name in required_variables(server):
        if lookup._variables is None:
            ok = lookup.has(name)
            if ok and len(lookup._by_lower[name.lower()]) > 1:
                lookup.get(name)  # raises: ambiguous
        else:
            ok = lookup.get(name) is not None
        if not ok:
            out.append(name)
    return out


def resolve_value(obj: Any, lookup: VariableLookup) -> Any:
    """Substitute ``${NAME}`` / ``${NAME:-default}`` in every string, in one pass."""

    def sub(m: re.Match[str]) -> str:
        value = lookup.get(m.group(1))
        if value is not None:
            return value
        if m.group(2) is not None:
            return m.group(2)
        raise McpConfigError(f"MCP variable {m.group(1)} has no value")

    if isinstance(obj, str):
        return VAR_RE.sub(sub, obj)
    if isinstance(obj, list):
        return [resolve_value(i, lookup) for i in obj]
    if isinstance(obj, dict):
        return {k: resolve_value(v, lookup) for k, v in obj.items()}
    return obj


# -------------------------------------------------------------- rendering


def render_server(agent: str, server: Mapping[str, Any]) -> dict[str, Any]:
    """Translate one resolved neutral server into an Agent's native shape."""
    if agent not in AGENTS:
        raise McpConfigError(f"unknown agent '{agent}'")
    if "command" in server:
        args = list(server.get("args") or [])
        env = dict(server.get("env") or {})
        if agent == "opencode":
            out: dict[str, Any] = {"type": "local", "command": [server["command"], *args]}
            if env:
                out["environment"] = env
            return out
        out = {}
        if agent == "claude":
            out["type"] = "stdio"
        out["command"] = server["command"]
        if args:
            out["args"] = args
        if env:
            out["env"] = env
        return out
    headers = dict(server.get("headers") or {})
    kind = server.get("type", "http")
    if agent == "opencode":
        out = {"type": "remote", "url": server["url"]}
        if headers:
            out["headers"] = headers
            out["oauth"] = False  # header credentials; don't start an OAuth flow
        return out
    out = {}
    if agent == "claude":
        out["type"] = kind
    out["url"] = server["url"]
    if headers:
        out["headers"] = headers
    return out


def render(
    config: Any, agents: Iterable[str], variables: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], list[str]]:
    """Render ``config`` for each agent.

    Returns ``(servers_by_agent, skipped, warnings)``. A server with an
    unresolved required variable is skipped (everywhere) and listed in
    ``skipped``; it never fails the render.
    """
    servers = parse_config(config)["mcpServers"]
    lookup = VariableLookup(variables)
    warnings: list[str] = []
    skipped: dict[str, list[str]] = {}
    resolved: dict[str, dict[str, Any]] = {}
    for name, server in servers.items():
        extra = unknown_keys(server)
        if extra:
            warnings.append(f"MCP server '{name}': ignoring unsupported field(s) {', '.join(extra)}")
        missing = missing_variables(server, lookup)
        if missing:
            skipped[name] = missing
            warnings.append(
                f"MCP server '{name}' skipped: no value for {', '.join(missing)} (store it with `rig vars encrypt`)"
            )
            continue
        resolved[name] = resolve_value({k: v for k, v in server.items() if k in KNOWN_KEYS}, lookup)
    by_agent = {
        agent: {name: render_server(agent, s) for name, s in resolved.items()} for agent in agents if agent in AGENTS
    }
    return by_agent, skipped, warnings


def merge_servers(existing: Any, key: str, servers: Mapping[str, Any]) -> dict[str, Any]:
    """Overlay rig-managed servers onto ``existing[key]`` by name.

    A same-name entry is replaced whole, not deep-merged; every other server
    already in the file (for example one added with ``claude mcp add``) is kept.
    """
    out = dict(existing or {})
    current = out.get(key)
    merged = dict(current) if isinstance(current, dict) else {}
    merged.update(servers)
    out[key] = merged
    return out


# ------------------------------------------------------- Ansible filters


def rig_mcp_render(raw_config: Any, agents: Any, variables: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``{{ raw | rig_mcp_render(agents, vars) }}`` -> ``{agent: {server: cfg}}``."""
    try:
        by_agent, _skipped, warnings = render(raw_config, list(agents or []), variables)
    except McpConfigError as exc:
        raise AnsibleFilterError(str(exc)) from exc
    for message in warnings:
        _warn(message)
    return by_agent


def rig_mcp_merge(existing: Any, key: str, servers: Mapping[str, Any]) -> dict[str, Any]:
    """``{{ existing | rig_mcp_merge('mcpServers', servers) }}``."""
    return merge_servers(existing, key, servers)


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {"rig_mcp_render": rig_mcp_render, "rig_mcp_merge": rig_mcp_merge}
