"""Neutral MCP config: validation, ${NAME} resolution and per-Agent rendering."""

import pytest

from cli.ansible_filters import rig_mcp as m

STDIO = {
    "command": "python3",
    "args": ["-u", "/opt/awx.py"],
    "env": {"AWX_MCP_API_KEY": "${AWX_MCP_API_KEY}", "URL": "${AWX_MCP_URL:-https://d/mcp}"},
}
REMOTE = {"url": "https://c.example/mcp", "headers": {"X-Key": "${CTX_KEY}"}}
CFG = {"mcpServers": {"awx": STDIO, "ctx": REMOTE}}


# ---- validation -----------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "  \n"])
def test_empty_config_has_no_servers(raw):
    assert m.parse_config(raw) == {"mcpServers": {}}


@pytest.mark.parametrize(
    "raw, message",
    [
        ("{not json", "not valid JSON"),
        ("[]", "must be a JSON object"),
        ({"mcpServers": []}, "'mcpServers' must be an object"),
        ({"mcpServers": {"a": []}}, "must be an object"),
        ({"mcpServers": {"a": {}}}, "exactly one of"),
        ({"mcpServers": {"a": {"command": "x", "url": "y"}}}, "exactly one of"),
        ({"mcpServers": {"a": {"command": ""}}}, "non-empty string"),
        ({"mcpServers": {"a": {"command": "x", "args": "no"}}}, "'args'"),
        ({"mcpServers": {"a": {"command": "x", "env": {"K": 1}}}}, "'env'"),
        ({"mcpServers": {"a": {"command": "x", "type": "http"}}}, "type 'stdio'"),
        ({"mcpServers": {"a": {"url": "u", "type": "stdio"}}}, "type 'http' or 'sse'"),
        ({"mcpServers": {"a": {"url": "u", "headers": {"H": 2}}}}, "'headers'"),
        ({"mcpServers": {"bad name": {"command": "x"}}}, "invalid name"),
    ],
)
def test_invalid_config_is_rejected(raw, message):
    with pytest.raises(m.McpConfigError, match=message):
        m.parse_config(raw)


def test_valid_config_round_trips_from_json_text():
    import json

    assert m.parse_config(json.dumps(CFG)) == CFG


# ---- variables ------------------------------------------------------------


def test_variables_in_marks_required_only_without_default():
    assert m.variables_in(STDIO) == {"AWX_MCP_API_KEY": True, "AWX_MCP_URL": False}
    assert m.required_variables(STDIO) == ["AWX_MCP_API_KEY"]


def test_a_variable_with_and_without_default_is_required():
    assert m.variables_in({"a": "${X:-1}", "b": "${X}"}) == {"X": True}


def test_lookup_ignores_case_and_treats_empty_as_missing():
    lk = m.VariableLookup({"awx_mcp_api_key": "s3", "empty": ""})
    assert lk.get("AWX_MCP_API_KEY") == "s3"
    assert lk.get("EMPTY") is None
    assert lk.get("nope") is None


def test_lookup_rejects_names_that_differ_only_by_case():
    lk = m.VariableLookup({"Foo": "1", "FOO": "2"})
    with pytest.raises(m.McpConfigError, match="differ only by case"):
        lk.get("foo")


def test_values_are_substituted_in_a_single_pass():
    lk = m.VariableLookup({"a": "${B}", "b": "x"})
    assert m.resolve_value("${A}-${B}", lk) == "${B}-x"  # no recursive expansion


def test_values_containing_json_metacharacters_survive():
    lk = m.VariableLookup({"k": 'a"b\\c'})
    assert m.resolve_value({"h": "${K}"}, lk) == {"h": 'a"b\\c'}


# ---- rendering ------------------------------------------------------------

VARS = {"awx_mcp_api_key": "S", "ctx_key": "C"}


def test_render_claude():
    out, skipped, warnings = m.render(CFG, ["claude"], VARS)
    assert skipped == {} and warnings == []
    assert out["claude"]["awx"] == {
        "type": "stdio",
        "command": "python3",
        "args": ["-u", "/opt/awx.py"],
        "env": {"AWX_MCP_API_KEY": "S", "URL": "https://d/mcp"},
    }
    assert out["claude"]["ctx"] == {"type": "http", "url": "https://c.example/mcp", "headers": {"X-Key": "C"}}


def test_render_cursor_has_no_type_field():
    out = m.render(CFG, ["cursor"], VARS)[0]["cursor"]
    assert "type" not in out["awx"] and "type" not in out["ctx"]
    assert out["awx"]["env"]["AWX_MCP_API_KEY"] == "S"


def test_render_opencode_uses_its_own_shape():
    out = m.render(CFG, ["opencode"], VARS)[0]["opencode"]
    assert out["awx"] == {
        "type": "local",
        "command": ["python3", "-u", "/opt/awx.py"],
        "environment": {"AWX_MCP_API_KEY": "S", "URL": "https://d/mcp"},
    }
    assert out["ctx"] == {
        "type": "remote",
        "url": "https://c.example/mcp",
        "headers": {"X-Key": "C"},
        "oauth": False,
    }


def test_sse_type_is_kept_for_claude_only():
    cfg = {"mcpServers": {"s": {"url": "https://x", "type": "sse"}}}
    out = m.render(cfg, list(m.AGENTS), {})[0]
    assert out["claude"]["s"]["type"] == "sse"
    assert "type" not in out["cursor"]["s"]
    assert out["opencode"]["s"] == {"type": "remote", "url": "https://x"}


def test_only_requested_known_agents_are_rendered():
    out = m.render(CFG, ["cursor", "bogus"], VARS)[0]
    assert list(out) == ["cursor"]


def test_server_with_a_missing_variable_is_skipped_everywhere_with_a_warning():
    out, skipped, warnings = m.render(CFG, ["claude", "opencode"], {"ctx_key": "C"})
    assert skipped == {"awx": ["AWX_MCP_API_KEY"]}
    assert "awx" not in out["claude"] and "awx" not in out["opencode"]
    assert "ctx" in out["claude"]
    assert warnings == ["MCP server 'awx' skipped: no value for AWX_MCP_API_KEY (store it with `rig vars encrypt`)"]
    assert "S" not in warnings[0]


def test_variable_with_default_does_not_skip_the_server():
    cfg = {"mcpServers": {"a": {"command": "x", "env": {"U": "${U:-d}"}}}}
    out, skipped, _ = m.render(cfg, ["claude"], {})
    assert skipped == {} and out["claude"]["a"]["env"] == {"U": "d"}


def test_unknown_fields_are_dropped_with_a_warning():
    cfg = {"mcpServers": {"a": {"command": "x", "cwd": "/tmp", "timeout": 5}}}
    out, _, warnings = m.render(cfg, ["claude"], {})
    assert out["claude"]["a"] == {"type": "stdio", "command": "x"}
    assert warnings == ["MCP server 'a': ignoring unsupported field(s) cwd, timeout"]


def test_invalid_config_fails_the_render():
    with pytest.raises(m.McpConfigError):
        m.render({"mcpServers": {"a": {}}}, ["claude"], {})


# ---- merge ----------------------------------------------------------------


def test_merge_replaces_same_name_whole_and_keeps_other_servers():
    existing = {
        "numStartups": 3,
        "mcpServers": {
            "awx": {"command": "old", "env": {"STALE": "1"}},
            "mine": {"command": "hand-added"},
        },
    }
    out = m.merge_servers(existing, "mcpServers", {"awx": {"command": "new"}})
    assert out["mcpServers"] == {"awx": {"command": "new"}, "mine": {"command": "hand-added"}}
    assert out["numStartups"] == 3
    assert existing["mcpServers"]["awx"]["command"] == "old"  # input untouched


@pytest.mark.parametrize("existing", [None, {}, {"mcpServers": None}, {"mcpServers": []}])
def test_merge_creates_the_key_when_missing_or_malformed(existing):
    assert m.merge_servers(existing, "mcp", {"a": {}})["mcp"] == {"a": {}}


# ---- filter wiring ----------------------------------------------------------


def test_filter_module_exposes_both_filters():
    filters = m.FilterModule().filters()
    assert set(filters) == {"rig_mcp_render", "rig_mcp_merge"}
    out = filters["rig_mcp_render"](CFG, ["claude"], VARS)
    assert out["claude"]["awx"]["env"]["AWX_MCP_API_KEY"] == "S"


def test_filter_turns_config_errors_into_filter_errors():
    with pytest.raises(m.AnsibleFilterError):
        m.rig_mcp_render("{bad", ["claude"], {})
