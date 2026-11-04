"""Unit tests for coding-agent scaffolding."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.agents import (
    OPENCODE_FREE_MODEL,
    ensure_agent_scaffold,
    normalize_agent_type,
    opencode_config,
    prompt_coding_agent,
    sync_opencode_config,
)


def test_normalize_agent_aliases():
    assert normalize_agent_type("Claude Code") == "claude"
    assert normalize_agent_type("opencode") == "opencode"


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
    assert (rig / ".opencode" / "mcp.json").is_file()
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
    assert (rig / ".claude" / "mcp.json").is_file()
    assert "Workspace rules" in (rig / "AGENTS.md").read_text()


def test_prompt_coding_agent_default():
    assert prompt_coding_agent(default="opencode", input_fn=lambda _p: "") == "opencode"
    assert prompt_coding_agent(default="opencode", input_fn=lambda _p: "claude") == "claude"
