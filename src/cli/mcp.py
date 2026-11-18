"""``rig mcp``: declare MCP servers once in ``.rig/mcp.json`` for every Agent.

The file uses Claude's ``.mcp.json`` schema with ``${NAME}`` MCP variables.
Values live in the vault, matched to a variable name ignoring case; the
authorize Stage renders each enabled Agent's own config from this file
(see ``cli.ansible_filters.rig_mcp``).
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from typing import Any, Optional

from cli.ansible_filters import rig_mcp as core
from cli.ansible_filters.rig_mcp import McpConfigError

MCP_FILENAME = "mcp.json"
# Per-agent MCP files from earlier rig versions; no longer deployed.
LEGACY_AGENT_MCP_FILES = (
    (".claude", "mcp.json"),
    (".cursor", "mcp.json"),
    (".opencode", "mcp.json"),
)
_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):", re.MULTILINE)
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*)=(.*)$", re.DOTALL)
_BARE_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _resolve_root(project_dir: str | None) -> str:
    from cli.encrypt import resolve_project_dir

    return resolve_project_dir(project_dir)


def mcp_path(project_dir: str | None = None) -> str:
    return os.path.join(_resolve_root(project_dir), ".rig", MCP_FILENAME)


def load_config(project_dir: str | None = None) -> dict[str, Any]:
    """Validated config; an absent file is an empty config."""
    path = mcp_path(project_dir)
    if not os.path.exists(path):
        return {"mcpServers": {}}
    with open(path, encoding="utf-8") as handle:
        return core.parse_config(handle.read())


def save_config(config: dict[str, Any], project_dir: str | None = None) -> str:
    core.parse_config(config)
    path = mcp_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    return path


def known_variable_names(project_dir: str | None = None) -> list[str]:
    """Names defined in the shared and identity vars files (vaulted or plain)."""
    from cli.encrypt import vars_path_for_project
    from cli.vault_keys import WRAP_FIELD_NAMES

    root = _resolve_root(project_dir)
    names: list[str] = []
    for personal in (False, True):
        path = vars_path_for_project(root, personal=personal)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            for name in _TOP_LEVEL_KEY_RE.findall(handle.read()):
                if name not in WRAP_FIELD_NAMES:
                    names.append(name)
    return names


def legacy_files_with_servers(project_dir: str | None = None) -> list[str]:
    """Old per-agent MCP files that still declare servers (now ignored)."""
    rig_dir = os.path.join(_resolve_root(project_dir), ".rig")
    found = []
    for folder, filename in LEGACY_AGENT_MCP_FILES:
        path = os.path.join(rig_dir, folder, filename)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        servers = data.get("mcpServers") or data.get("mcp") if isinstance(data, dict) else None
        if isinstance(servers, dict) and servers:
            found.append(os.path.join(".rig", folder, filename))
    return found


def legacy_warning(files: Sequence[str]) -> str:
    return f"Ignoring MCP servers in {', '.join(files)}: move them to .rig/{MCP_FILENAME} (see `rig mcp add`)"


# ------------------------------------------------------------ building


def parse_assignments(items: Sequence[str] | None, *, allow_bare: bool) -> dict[str, str]:
    """``K=V`` -> {K: V}; bare ``K`` (env only) -> {K: '${K}'}."""
    out: dict[str, str] = {}
    for item in items or []:
        match = _ASSIGN_RE.match(item)
        if match:
            out[match.group(1)] = match.group(2)
        elif allow_bare and _BARE_ENV_RE.match(item):
            out[item] = f"${{{item}}}"
        else:
            hint = "KEY or KEY=VALUE" if allow_bare else "KEY=VALUE"
            raise McpConfigError(f"expected {hint}, got {item!r}")
    return out


def build_server(
    *, url: str | None, command: Sequence[str], env: Sequence[str], headers: Sequence[str], kind: str | None
) -> dict[str, Any]:
    command = list(command)
    if command and command[0] == "--":
        command = command[1:]
    if bool(url) == bool(command):
        raise McpConfigError("give either --url URL or a command after `--`")
    if url:
        if env:
            raise McpConfigError("--env applies to command servers; use --header for --url")
        server: dict[str, Any] = {"url": url}
        if kind and kind != "http":
            server["type"] = kind
        hdrs = parse_assignments(headers, allow_bare=False)
        if hdrs:
            server["headers"] = hdrs
        return server
    if headers or kind:
        raise McpConfigError("--header and --type apply to --url servers")
    server = {"command": command[0]}
    if command[1:]:
        server["args"] = command[1:]
    envs = parse_assignments(env, allow_bare=True)
    if envs:
        server["env"] = envs
    return server


def unresolved_variables(server: Any, names: Sequence[str]) -> list[str]:
    """Required variables of ``server`` with no matching variable name."""
    lookup = core.VariableLookup(names=names)
    return core.missing_variables(server, lookup)


# ------------------------------------------------------------ commands


def _secret_prompt(label: str, env_name: str) -> str:
    from cli.encrypt import _prompt_secret_with_env_fallback

    return _prompt_secret_with_env_fallback(label, env_name)


def cmd_add(
    name: str,
    *,
    url: str | None = None,
    command: Sequence[str] = (),
    env: Sequence[str] = (),
    headers: Sequence[str] = (),
    kind: str | None = None,
    personal: bool = False,
    force: bool = False,
    project_dir: str | None = None,
    interactive: bool | None = None,
    prompt: Callable[[str, str], str] = _secret_prompt,
) -> int:
    from cli.encrypt import store_encrypted_var

    try:
        server = build_server(url=url, command=command, env=env, headers=headers, kind=kind)
        config = load_config(project_dir)
        core.validate_server(name, server)
        missing = unresolved_variables(server, known_variable_names(project_dir))
    except McpConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if name in config["mcpServers"] and not force:
        print(f"Error: MCP server '{name}' already exists (use --force to replace)", file=sys.stderr)
        return 1
    config["mcpServers"][name] = server
    path = save_config(config, project_dir)
    print(f"Added MCP server '{name}' to {os.path.relpath(path)}")

    interactive = sys.stdin.isatty() if interactive is None else interactive
    for var in missing:
        var_name = var.lower()
        if not interactive:
            print(f"  {var}: no value yet. Store it with: rig vars encrypt --stdin-name {var_name}")
            continue
        value = prompt(f"Value for MCP variable {var}", var)
        if not value:
            print(
                f"  {var}: skipped. The server stays disabled until a value is stored "
                f"as '{var_name}' (rig vars encrypt)."
            )
            continue
        store_encrypted_var(var_name, value, project_dir, personal=personal)
    print("Run `rig apply --authorize` to push this to a running Environment.")
    return 0


def cmd_remove(name: str, *, project_dir: str | None = None) -> int:
    try:
        config = load_config(project_dir)
    except McpConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if name not in config["mcpServers"]:
        print(f"Error: no MCP server named '{name}'", file=sys.stderr)
        return 1
    del config["mcpServers"][name]
    save_config(config, project_dir)
    print(f"Removed MCP server '{name}' from .rig/{MCP_FILENAME}")
    print("Running Environments keep it until recreated.")
    return 0


def _describe(server: dict[str, Any]) -> str:
    if "command" in server:
        return " ".join([server["command"], *server.get("args", [])])
    return server["url"]


def report(project_dir: str | None = None) -> tuple[int, list[str]]:
    """Per-server status lines and an exit code (1 on invalid config/missing vars)."""
    lines: list[str] = []
    try:
        config = load_config(project_dir)
    except McpConfigError as exc:
        return 1, [f"Error: {exc}"]
    names = known_variable_names(project_dir)
    lookup = core.VariableLookup(names=names)
    code = 0
    for name, server in config["mcpServers"].items():
        try:
            missing = core.missing_variables(server, lookup)
        except McpConfigError as exc:
            lines.append(f"{name}: Error: {exc}")
            code = 1
            continue
        kind = "stdio" if "command" in server else server.get("type", "http")
        status = "ok" if not missing else f"missing {', '.join(missing)}"
        lines.append(f"{name} [{kind}] {_describe(server)}: {status}")
        for extra in core.unknown_keys(server):
            lines.append(f"  warning: field '{extra}' is not supported and will be ignored")
        if missing:
            code = 1
    if not config["mcpServers"]:
        lines.append(f"No MCP servers in .rig/{MCP_FILENAME}")
    legacy = legacy_files_with_servers(project_dir)
    if legacy:
        lines.append("warning: " + legacy_warning(legacy))
    return code, lines


def cmd_list(*, project_dir: str | None = None) -> int:
    _code, lines = report(project_dir)
    print("\n".join(lines))
    return 1 if lines and lines[0].startswith("Error:") else 0


def cmd_check(*, project_dir: str | None = None) -> int:
    code, lines = report(project_dir)
    print("\n".join(lines))
    return code
