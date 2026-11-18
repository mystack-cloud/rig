"""Unit tests for coding-agent scaffolding."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from cli.agents import (
    OPENCODE_FREE_MODEL,
    apply_agent_selection,
    coerce_agent_types,
    ensure_agent_scaffold,
    normalize_agent_type,
    opencode_config,
    parse_agent_selection,
    prompt_coding_agent,
    prompt_coding_agents,
    prompt_default_agent,
    resolve_configured_agents,
    sync_opencode_config,
)


def test_normalize_agent_aliases():
    assert normalize_agent_type("Claude Code") == "claude"
    assert normalize_agent_type("opencode") == "opencode"
    assert normalize_agent_type("cursor-cli") == "cursor"


def test_opencode_config_defaults_to_free_without_api_key():
    free = opencode_config(has_api_key=False)
    assert free["model"] == OPENCODE_FREE_MODEL
    assert "provider" not in free

    keyed = opencode_config(has_api_key=True)
    assert keyed["model"] == "anthropic/claude-sonnet-4-5"
    assert "{env:CODING_AGENT_API_KEY}" in json.dumps(keyed)


def test_ensure_agent_scaffold_opencode(tmp_path):
    rig = tmp_path / ".rig"
    rig.mkdir()
    created = ensure_agent_scaffold(str(rig), agent_type="opencode")
    assert any(path.endswith("AGENTS.md") for path in created)
    config = json.loads((rig / ".opencode" / "opencode.json").read_text())
    assert config["model"] == OPENCODE_FREE_MODEL
    assert "CODING_AGENT_API_KEY" not in json.dumps(config)
    assert not (rig / ".opencode" / "mcp.json").exists()  # MCP servers live in .rig/mcp.json
    # second call does not overwrite
    (rig / "AGENTS.md").write_text("# custom\n")
    ensure_agent_scaffold(str(rig), agent_type="opencode")
    assert (rig / "AGENTS.md").read_text() == "# custom\n"


def test_sync_opencode_config_upgrades_when_api_key_set(tmp_path):
    rig = tmp_path / ".rig"
    rig.mkdir()
    ensure_agent_scaffold(str(rig), agent_type="opencode")
    sync_opencode_config(str(rig), has_api_key=True, force=True)
    config = json.loads((rig / ".opencode" / "opencode.json").read_text())
    assert "{env:CODING_AGENT_API_KEY}" in json.dumps(config)


def test_ensure_agent_scaffold_claude(tmp_path):
    rig = tmp_path / ".rig"
    rig.mkdir()
    ensure_agent_scaffold(str(rig), agent_type="claude")
    assert (rig / ".claude" / "settings.json").is_file()
    assert not (rig / ".claude" / "mcp.json").exists()
    assert "Workspace rules" in (rig / "AGENTS.md").read_text()


def test_ensure_agent_scaffold_cursor(tmp_path):
    rig = tmp_path / ".rig"
    rig.mkdir()
    ensure_agent_scaffold(str(rig), agent_type="cursor")
    assert (rig / ".cursor" / "cli-config.json").is_file()
    assert not (rig / ".cursor" / "mcp.json").exists()
    assert "Workspace rules" in (rig / "AGENTS.md").read_text()


def test_prompt_coding_agent_default():
    assert prompt_coding_agent(default="opencode", input_fn=lambda _p: "") == "opencode"
    assert prompt_coding_agent(default="opencode", input_fn=lambda _p: "claude") == "claude"
    assert prompt_coding_agent(default="opencode", input_fn=lambda _p: "cursor") == "cursor"


def test_prompt_coding_agents_multi_choice():
    assert prompt_coding_agents(default="opencode", input_fn=lambda _p: "") == ["opencode"]
    assert prompt_coding_agents(default="claude", input_fn=lambda _p: "1,3") == [
        "opencode",
        "cursor",
    ]
    assert prompt_coding_agents(default=["cursor"], input_fn=lambda _p: "all") == [
        "opencode",
        "claude",
        "cursor",
    ]
    assert prompt_coding_agents(default="opencode", input_fn=lambda _p: "claude, cursor") == ["claude", "cursor"]
    with pytest.raises(ValueError):
        prompt_coding_agents(default="opencode", input_fn=lambda _p: "nope")


def test_prompt_default_agent_among_selected():
    assert prompt_default_agent(["claude"], input_fn=lambda _p: "cursor") == "claude"
    assert prompt_default_agent(["opencode", "claude"], default="opencode", input_fn=lambda _p: "") == "opencode"
    assert prompt_default_agent(["opencode", "cursor"], default="opencode", input_fn=lambda _p: "3") == "cursor"
    assert prompt_default_agent(["opencode", "claude"], default="opencode", input_fn=lambda _p: "claude") == "claude"
    with pytest.raises(ValueError):
        prompt_default_agent(["opencode", "claude"], input_fn=lambda _p: "cursor")


class _Label:
    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text


def test_coerce_agent_types_edges():
    assert coerce_agent_types(None) == []
    assert coerce_agent_types("  ") == []
    assert coerce_agent_types(["opencode", None, "opencode", "claude"]) == [
        "opencode",
        "claude",
    ]
    assert coerce_agent_types(_Label("cursor")) == ["cursor"]
    assert coerce_agent_types(_Label("  ")) == []


def test_resolve_and_apply_ignore_unknown_names():
    installed, default = resolve_configured_agents(
        {"types": ["nope"], "type": "nope"},
        coding_agent_type="cursor",
    )
    assert installed == ["cursor"]
    assert default == "cursor"
    installed, default = resolve_configured_agents(
        {"types": ["nope"], "type": "nope"},
        coding_agent_type="also-nope",
    )
    assert installed == ["opencode"]
    assert default == "opencode"
    installed, default = resolve_configured_agents({"types": ["opencode", "claude"], "type": "nope"})
    assert default == "opencode"

    cfg = {}
    selected = apply_agent_selection(cfg, ["opencode", "claude"], default="nope")
    assert selected == ["opencode", "claude"]
    assert cfg["default"] == "opencode"
    assert cfg["enabled"] == ["opencode", "claude"]
    assert "type" not in cfg
    assert "types" not in cfg
    apply_agent_selection(cfg, ["opencode", "claude"], default="cursor")
    assert cfg["default"] == "opencode"


def test_parse_and_prompt_agent_selection_edges():
    assert parse_agent_selection("", default=["nope"]) == ["opencode"]
    assert parse_agent_selection(",,", default=["claude"]) == ["claude"]
    with pytest.raises(ValueError):
        parse_agent_selection("9", default=["opencode"])
    assert prompt_coding_agents(default="nope", input_fn=lambda _p: "") == ["opencode"]
    assert prompt_default_agent(["opencode", "claude"], default="nope", input_fn=lambda _p: "") == "opencode"
    with pytest.raises(ValueError):
        prompt_default_agent(["opencode", "claude"], input_fn=lambda _p: "3")


def test_resolve_configured_agents_prefers_types_and_default():
    installed, default = resolve_configured_agents({"type": "cursor", "types": ["opencode", "claude", "cursor"]})
    assert installed == ["opencode", "claude", "cursor"]
    assert default == "cursor"
    installed, default = resolve_configured_agents({"type": "claude"})
    assert installed == ["claude"]
    assert default == "claude"
    installed, default = resolve_configured_agents(
        {"default": "claude", "type": "cursor", "types": ["opencode", "claude", "cursor"]}
    )
    assert default == "claude"
    installed, default = resolve_configured_agents(
        {
            "default": "cursor",
            "enabled": ["opencode", "cursor"],
            "types": ["claude"],
        }
    )
    assert installed == ["opencode", "cursor"]
    assert default == "cursor"
