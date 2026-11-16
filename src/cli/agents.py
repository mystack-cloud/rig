"""
Coding agent definitions and local .rig scaffolding.

Supported agents: opencode, claude (Claude Code), cursor (Cursor CLI).

Layout under the project (committed, user-editable):

  .rig/AGENTS.md                 # shared workspace rules
  .rig/.opencode/opencode.json   # OpenCode config (API key via env)
  .rig/.opencode/mcp.json        # OpenCode MCP servers
  .rig/.claude/settings.json     # Claude Code settings
  .rig/.claude/mcp.json          # Claude Code MCP servers (.mcp.json on remote)
  .rig/.cursor/cli-config.json   # Cursor CLI config
  .rig/.cursor/mcp.json          # Cursor CLI MCP servers
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

SUPPORTED_AGENTS = ("opencode", "claude", "cursor")

AGENT_LABELS = {
    "opencode": "OpenCode",
    "claude": "Claude Code",
    "cursor": "Cursor CLI",
}

# Per-agent vault var. A single selected agent still uses the legacy shared name
# so existing ``coding_agent_api_key`` values keep working.
AGENT_API_KEY_VARS = {
    "opencode": "opencode_api_key",
    "claude": "claude_api_key",
    "cursor": "cursor_api_key",
}

# Distinct env fallbacks so several agents are not given the same key.
AGENT_API_KEY_ENVS = {
    "opencode": "RIG_OPENCODE_API_KEY",
    "claude": "RIG_ANTHROPIC_API_KEY",
    "cursor": "RIG_CURSOR_API_KEY",
}

LEGACY_CODING_AGENT_API_KEY = "coding_agent_api_key"

# Env var name referenced from generated configs; set by authorize.yml on the VM.
CODING_AGENT_API_KEY_ENV = "CODING_AGENT_API_KEY"

DEFAULT_AGENTS_MD = """# Workspace rules

These rules apply to coding agents provisioned by rig for this workspace.

## Goals
- Prefer small, focused changes.
- Match existing project conventions before introducing new patterns.
- Do not commit secrets; use vaulted `.rig/vars.yml` values via the environment.

## Remote environment
- This workspace runs on a rig-managed remote VM.
- Project files live under `/workspace` (not `/root`); home is for tooling/dotfiles only.
- Git remotes use HTTPS with provider PATs from the environment / Ansible vars.
- Prefer `gh` (GitHub) or `glab` (GitLab) for MRs, reviews, and issue workflows.

## Safety
- Do not force-push to main/master unless explicitly requested.
- Ask before destructive operations (reset --hard, drop database, etc.).
"""

# Built-in OpenCode Zen free model (no API key). Prefer a zero-retention free id.
OPENCODE_FREE_MODEL = "opencode/deepseek-v4.1-flash-free"

OPENCODE_CONFIG_FREE = {
    "$schema": "https://opencode.ai/config.json",
    "model": OPENCODE_FREE_MODEL,
    "instructions": ["AGENTS.md"],
    "mcp": {},
}

OPENCODE_CONFIG_WITH_API_KEY = {
    "$schema": "https://opencode.ai/config.json",
    "model": "anthropic/claude-sonnet-4-5",
    "provider": {
        "anthropic": {
            "options": {
                "apiKey": "{env:CODING_AGENT_API_KEY}",
            }
        }
    },
    "instructions": ["AGENTS.md"],
    "mcp": {},
}

# Default scaffold when no coding_agent_api_key / OPENCODE_API_KEY is configured.
OPENCODE_CONFIG = OPENCODE_CONFIG_FREE


def opencode_config(has_api_key: bool = False) -> Dict[str, Any]:
    """Return OpenCode config for free models or a keyed Anthropic provider."""
    import copy
    return copy.deepcopy(
        OPENCODE_CONFIG_WITH_API_KEY if has_api_key else OPENCODE_CONFIG_FREE
    )


def sync_opencode_config(
    rig_dir: str,
    has_api_key: bool = False,
    force: bool = False,
) -> Optional[str]:
    """
    Write ``.rig/.opencode/opencode.json`` for free or keyed usage.

    When ``force`` is False, an existing file is left unchanged.
    Returns the relative path created/updated, or None if skipped.
    """
    cfg_dir = os.path.join(os.path.abspath(rig_dir), ".opencode")
    config_path = os.path.join(cfg_dir, "opencode.json")
    if not force and os.path.exists(config_path):
        return None
    _write_json(config_path, opencode_config(has_api_key=has_api_key))
    return ".rig/.opencode/opencode.json"

OPENCODE_MCP: Dict[str, Any] = {
    "mcp": {
        # Example (disabled). Uncomment / extend as needed:
        # "context7": {
        #     "type": "remote",
        #     "url": "https://mcp.context7.com/mcp",
        #     "enabled": false,
        #     "headers": {"CONTEXT7_API_KEY": "{env:CONTEXT7_API_KEY}"},
        # },
    }
}

CLAUDE_SETTINGS = {
    # Bare tool names allow every path; "Tool(*)" warns on recent Claude Code.
    "permissions": {
        "allow": [
            "Bash",
            "Edit",
            "Read",
            "Write",
            "Glob",
            "Grep",
        ]
    },
    # Skips the first-run theme picker.
    "theme": "dark",
    # API key is injected via /etc/profile.d/rig-coding-agent.sh and
    # ~/.claude/credentials.env (CODING_AGENT_API_KEY / ANTHROPIC_API_KEY).
}

CLAUDE_MCP: Dict[str, Any] = {
    "mcpServers": {
        # Example (disabled). Add servers as needed for Claude Code:
        # "filesystem": {
        #     "command": "npx",
        #     "args": ["-y", "@modelcontextprotocol/server-filesystem", "/root"],
        # },
    }
}

# Cursor CLI (~/.cursor/cli-config.json). API key via CURSOR_API_KEY on the VM.
CURSOR_CLI_CONFIG: Dict[str, Any] = {
    "permissions": {
        "allow": [
            "Shell(*)",
            "Read(*)",
            "Write(*)",
        ],
    },
}

CURSOR_MCP: Dict[str, Any] = {
    "mcpServers": {},
}


def normalize_agent_type(value: Optional[str]) -> str:
    raw = (value or "").strip().lower().replace(" ", "-").replace("_", "-")
    aliases = {
        "opencode": "opencode",
        "open-code": "opencode",
        "claude": "claude",
        "claude-code": "claude",
        "anthropic": "claude",
        "cursor": "cursor",
        "cursor-cli": "cursor",
        "cursor-agent": "cursor",
    }
    agent = aliases.get(raw, raw)
    if agent not in SUPPORTED_AGENTS:
        raise ValueError(
            f"Unsupported coding agent {value!r}. "
            f"Choose one of: {', '.join(SUPPORTED_AGENTS)}"
        )
    return agent


def agent_config_dir_name(agent_type: str) -> str:
    agent = normalize_agent_type(agent_type)
    if agent == "opencode":
        return ".opencode"
    if agent == "cursor":
        return ".cursor"
    return ".claude"


def agent_remote_paths(agent_type: str) -> Dict[str, str]:
    """Where scaffolded files land on the remote VM (as root)."""
    agent = normalize_agent_type(agent_type)
    if agent == "opencode":
        return {
            "config_dir": "/root/.config/opencode",
            "config_file": "/root/.config/opencode/opencode.json",
            "mcp_file": "/root/.config/opencode/mcp.json",
            "rules_file": "/root/.config/opencode/AGENTS.md",
            "env_file": "/root/.config/opencode/credentials.env",
        }
    if agent == "cursor":
        return {
            "config_dir": "/root/.cursor",
            "config_file": "/root/.cursor/cli-config.json",
            "mcp_file": "/root/.cursor/mcp.json",
            "rules_file": "/workspace/AGENTS.md",
            "env_file": "/root/.cursor/credentials.env",
        }
    return {
        "config_dir": "/root/.claude",
        "config_file": "/root/.claude/settings.json",
        "mcp_file": "/root/.mcp.json",
        "rules_file": "/root/.claude/CLAUDE.md",
        "env_file": "/root/.claude/credentials.env",
    }


def _write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def _write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content if content.endswith("\n") else content + "\n")


def ensure_agent_scaffold(
    rig_dir: str,
    agent_type: str = "opencode",
    force: bool = False,
    has_api_key: bool = False,
) -> List[str]:
    """
    Create missing agent config / MCP / AGENTS.md under ``rig_dir``.

    For OpenCode, defaults to free Zen models when ``has_api_key`` is False.
    Existing files are left untouched unless ``force`` is True.
    Returns the list of paths that were created (relative to rig_dir parent).
    """
    agent = normalize_agent_type(agent_type)
    created: List[str] = []
    rig_dir = os.path.abspath(rig_dir)

    agents_md = os.path.join(rig_dir, "AGENTS.md")
    if force or not os.path.exists(agents_md):
        _write_text(agents_md, DEFAULT_AGENTS_MD)
        created.append(os.path.join(".rig", "AGENTS.md"))

    if agent == "opencode":
        written = sync_opencode_config(
            rig_dir, has_api_key=has_api_key, force=force
        )
        if written:
            created.append(written)
        cfg_dir = os.path.join(rig_dir, ".opencode")
        mcp_path = os.path.join(cfg_dir, "mcp.json")
        if force or not os.path.exists(mcp_path):
            _write_json(mcp_path, OPENCODE_MCP)
            created.append(".rig/.opencode/mcp.json")
    elif agent == "cursor":
        cfg_dir = os.path.join(rig_dir, ".cursor")
        config_path = os.path.join(cfg_dir, "cli-config.json")
        mcp_path = os.path.join(cfg_dir, "mcp.json")
        if force or not os.path.exists(config_path):
            _write_json(config_path, CURSOR_CLI_CONFIG)
            created.append(".rig/.cursor/cli-config.json")
        if force or not os.path.exists(mcp_path):
            _write_json(mcp_path, CURSOR_MCP)
            created.append(".rig/.cursor/mcp.json")
    else:
        cfg_dir = os.path.join(rig_dir, ".claude")
        config_path = os.path.join(cfg_dir, "settings.json")
        mcp_path = os.path.join(cfg_dir, "mcp.json")
        if force or not os.path.exists(config_path):
            _write_json(config_path, CLAUDE_SETTINGS)
            created.append(".rig/.claude/settings.json")
        if force or not os.path.exists(mcp_path):
            _write_json(mcp_path, CLAUDE_MCP)
            created.append(".rig/.claude/mcp.json")

    return created


def prompt_coding_agent(
    default: str = "opencode",
    input_fn: Optional[Callable[..., str]] = None,
) -> str:
    """Ask which coding agent to use (single choice)."""
    ask = input_fn or input
    try:
        default = normalize_agent_type(default)
    except ValueError:
        default = "opencode"
    choices = ", ".join(f"{a} ({AGENT_LABELS[a]})" for a in SUPPORTED_AGENTS)
    answer = ask(f"Coding agent [{choices}] [{default}]: ").strip()
    if not answer:
        return default
    return normalize_agent_type(answer)


def coerce_agent_types(value: Any) -> List[str]:
    """
    Normalize a string, comma-separated string, or list into agent ids.

    Order is preserved and duplicates are dropped. Empty input returns [].
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parts = [part.strip() for part in text.replace(" ", ",").split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                parts.append(text)
    else:
        text = str(value).strip()
        parts = [text] if text else []

    selected: List[str] = []
    seen = set()
    for part in parts:
        agent = normalize_agent_type(part)
        if agent not in seen:
            selected.append(agent)
            seen.add(agent)
    return selected


def agent_default_field(agent_cfg: Optional[Dict[str, Any]] = None) -> Any:
    """``agent.default``, or the legacy ``agent.type`` string."""
    agent_cfg = agent_cfg or {}
    raw = agent_cfg.get("default")
    if raw not in (None, ""):
        return raw
    return agent_cfg.get("type")


def agent_enabled_field(agent_cfg: Optional[Dict[str, Any]] = None) -> Any:
    """``agent.enabled``, or the legacy ``agent.types`` list."""
    agent_cfg = agent_cfg or {}
    if agent_cfg.get("enabled"):
        return agent_cfg.get("enabled")
    return agent_cfg.get("types")


def resolve_configured_agents(
    agent_cfg: Optional[Dict[str, Any]] = None,
    *,
    coding_agent_type: Any = None,
    coding_agent_types: Any = None,
) -> Tuple[List[str], str]:
    """
    Return ``(installed_agents, default_attach_agent)``.

    ``agent.enabled`` / ``coding_agent_types`` is the install set.
    ``agent.default`` is the attach default when it is a member of that set.
    Legacy ``agent.types`` and ``agent.type`` are still accepted.
    """
    agent_cfg = agent_cfg or {}
    types: List[str] = []
    for candidate in (agent_enabled_field(agent_cfg), coding_agent_types):
        if not candidate:
            continue
        try:
            types = coerce_agent_types(candidate)
        except ValueError:
            types = []
        if types:
            break
    if not types:
        raw_type = agent_default_field(agent_cfg)
        if raw_type:
            try:
                types = coerce_agent_types(raw_type)
            except ValueError:
                types = []
        if not types and coding_agent_type:
            try:
                types = coerce_agent_types(coding_agent_type)
            except ValueError:
                types = []
    if not types:
        types = ["opencode"]

    primary: Optional[str] = None
    raw_primary = agent_default_field(agent_cfg)
    if isinstance(raw_primary, str) and raw_primary.strip():
        try:
            primary = normalize_agent_type(raw_primary)
        except ValueError:
            primary = None
    if primary not in types:
        primary = types[0]
    return types, primary


def apply_agent_selection(
    agent_cfg: Dict[str, Any],
    agent_types: Sequence[str],
    default: Any = None,
) -> List[str]:
    """
    Write ``default``, ``enabled``, and credential var names onto an agent config dict.

    ``default`` is the attach default. The requested name is used when that
    agent was selected, otherwise the first selected agent.
    A single agent keeps ``credentials_var: coding_agent_api_key``.
    Several agents each get their own vault var under ``credentials``.
    """
    selected = coerce_agent_types(agent_types) or ["opencode"]
    primary: Optional[str] = None
    if isinstance(default, str) and default.strip():
        try:
            primary = normalize_agent_type(default)
        except ValueError:
            primary = None
    if primary not in selected:
        primary = selected[0]
    agent_cfg["default"] = primary
    agent_cfg.pop("type", None)
    agent_cfg["enabled"] = list(selected)
    agent_cfg.pop("types", None)
    if len(selected) == 1:
        agent_cfg["credentials_var"] = LEGACY_CODING_AGENT_API_KEY
        agent_cfg.pop("credentials", None)
    else:
        agent_cfg["credentials"] = {
            agent: AGENT_API_KEY_VARS[agent] for agent in selected
        }
        agent_cfg["credentials_var"] = AGENT_API_KEY_VARS[primary]
    return selected


def parse_agent_selection(
    answer: str,
    *,
    default: Sequence[str],
) -> List[str]:
    """Parse 'all', indexes, or names the same way git project selection works."""
    try:
        defaults = coerce_agent_types(list(default)) or ["opencode"]
    except ValueError:
        defaults = ["opencode"]
    raw = (answer or "").strip()
    if not raw:
        return defaults
    if raw.lower() in ("all", "*"):
        return list(SUPPORTED_AGENTS)

    selected: List[str] = []
    seen = set()
    tokens = [token.strip() for token in raw.replace(" ", ",").split(",") if token.strip()]
    if not tokens:
        return defaults
    for token in tokens:
        if token.isdigit():
            index = int(token) - 1
            if not 0 <= index < len(SUPPORTED_AGENTS):
                raise ValueError(
                    f"Coding agent number {token} is out of range. "
                    f"Choose 1-{len(SUPPORTED_AGENTS)}, names, or all."
                )
            agent = SUPPORTED_AGENTS[index]
        else:
            agent = normalize_agent_type(token)
        if agent not in seen:
            selected.append(agent)
            seen.add(agent)
    return selected


def prompt_coding_agents(
    default: Any = "opencode",
    input_fn: Optional[Callable[..., str]] = None,
) -> List[str]:
    """
    Ask which coding agents to install.

    Accepts numbers, names, or ``all``, like the git project picker.
    Enter keeps ``default``.
    """
    ask = input_fn or input
    try:
        defaults = coerce_agent_types(default) or ["opencode"]
    except ValueError:
        defaults = ["opencode"]
    print("Coding agents:")
    for index, agent in enumerate(SUPPORTED_AGENTS, start=1):
        print(f"  {index}. {agent} ({AGENT_LABELS[agent]})")
    default_label = ",".join(defaults)
    answer = ask(
        "Coding agents to include (numbers, names, or all) "
        f"[{default_label}]: "
    )
    return parse_agent_selection(answer, default=defaults)


def prompt_default_agent(
    selected: Sequence[str],
    default: Any = None,
    input_fn: Optional[Callable[..., str]] = None,
) -> str:
    """
    Ask which selected agent ``rig attach`` should open.

    A single selected agent is returned without a prompt. Numbers match the
    catalog shown by ``prompt_coding_agents`` (not a renumbered sub-list).
    """
    agents = coerce_agent_types(list(selected)) or ["opencode"]
    if len(agents) == 1:
        return agents[0]
    preferred = agents[0]
    if isinstance(default, str) and default.strip():
        try:
            candidate = normalize_agent_type(default)
        except ValueError:
            candidate = None
        if candidate in agents:
            preferred = candidate
    by_number = {
        str(index): agent
        for index, agent in enumerate(SUPPORTED_AGENTS, start=1)
        if agent in agents
    }
    print("Default agent for rig attach:")
    for number, agent in by_number.items():
        print(f"  {number}. {agent} ({AGENT_LABELS[agent]})")
    ask = input_fn or input
    answer = ask(f"Default agent [{preferred}]: ").strip()
    if not answer:
        return preferred
    if answer.isdigit():
        numbered = by_number.get(answer)
        if not numbered:
            raise ValueError(
                f"Default agent number {answer} is not one of the selected agents "
                f"({', '.join(agents)})"
            )
        return numbered
    chosen = normalize_agent_type(answer)
    if chosen not in agents:
        raise ValueError(
            f"Default agent {chosen!r} was not selected. "
            f"Choose one of: {', '.join(agents)}"
        )
    return chosen
