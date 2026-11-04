"""
Coding agent definitions and local .rig scaffolding.

Supported agents (initially): opencode, claude (Claude Code).

Layout under the project (committed, user-editable):

  .rig/AGENTS.md                 # shared workspace rules
  .rig/.opencode/opencode.json   # OpenCode config (API key via env)
  .rig/.opencode/mcp.json        # OpenCode MCP servers
  .rig/.claude/settings.json     # Claude Code settings
  .rig/.claude/mcp.json          # Claude Code MCP servers (.mcp.json on remote)
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

SUPPORTED_AGENTS = ("opencode", "claude")

AGENT_LABELS = {
    "opencode": "OpenCode",
    "claude": "Claude Code",
}

# Env var name referenced from generated configs; set by system.yml on the VM.
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

OPENCODE_MCP = {
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
    "permissions": {
        "allow": [
            "Bash(*)",
            "Edit(*)",
            "Read(*)",
            "Write(*)",
            "Glob(*)",
            "Grep(*)",
        ]
    },
    # API key is injected via /etc/profile.d/rig-coding-agent.sh and
    # ~/.claude/credentials.env (CODING_AGENT_API_KEY / ANTHROPIC_API_KEY).
}

CLAUDE_MCP = {
    "mcpServers": {
        # Example (disabled). Add servers as needed for Claude Code:
        # "filesystem": {
        #     "command": "npx",
        #     "args": ["-y", "@modelcontextprotocol/server-filesystem", "/root"],
        # },
    }
}


def normalize_agent_type(value: Optional[str]) -> str:
    raw = (value or "").strip().lower().replace(" ", "-").replace("_", "-")
    aliases = {
        "opencode": "opencode",
        "open-code": "opencode",
        "claude": "claude",
        "claude-code": "claude",
        "anthropic": "claude",
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
    return ".opencode" if agent == "opencode" else ".claude"


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
    """Ask which coding agent to use."""
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
