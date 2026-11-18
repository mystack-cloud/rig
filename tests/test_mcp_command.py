"""`rig mcp`: add / list / remove / check against .rig/mcp.json."""

import json

import pytest

from cli import mcp
from cli.ansible_filters.rig_mcp import McpConfigError
from cli.main import main


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "rig.yml").write_text("name: t\n")
    (tmp_path / ".rig").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_vars(project, *names):
    lines = [f"{n}: !vault |\n  $ANSIBLE_VAULT;1.2;AES256;shared\n  3634\n" for n in names]
    (project / ".rig" / "vars.yml").write_text("".join(lines))


def read_cfg(project):
    return json.loads((project / ".rig" / "mcp.json").read_text())


# ---- building ---------------------------------------------------------------


def test_parse_assignments_supports_bare_env_shorthand():
    assert mcp.parse_assignments(["KEY", "URL=https://x?a=b"], allow_bare=True) == {
        "KEY": "${KEY}",
        "URL": "https://x?a=b",
    }
    assert mcp.parse_assignments(["A="], allow_bare=True) == {"A": ""}


@pytest.mark.parametrize("items, bare", [(["bad key"], True), (["KEY"], False), (["1X=1"], True)])
def test_parse_assignments_rejects_malformed_items(items, bare):
    with pytest.raises(McpConfigError):
        mcp.parse_assignments(items, allow_bare=bare)


def test_build_server_stdio_strips_the_separator():
    server = mcp.build_server(url=None, command=["--", "python3", "-u", "x.py"], env=["K"], headers=[], kind=None)
    assert server == {"command": "python3", "args": ["-u", "x.py"], "env": {"K": "${K}"}}


def test_build_server_remote_only_records_non_default_type():
    http = mcp.build_server(url="https://x", command=[], env=[], headers=["H=1"], kind="http")
    sse = mcp.build_server(url="https://x", command=[], env=[], headers=[], kind="sse")
    assert http == {"url": "https://x", "headers": {"H": "1"}}
    assert sse == {"url": "https://x", "type": "sse"}


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(url=None, command=[], env=[], headers=[], kind=None),
        dict(url="u", command=["c"], env=[], headers=[], kind=None),
        dict(url="u", command=[], env=["A"], headers=[], kind=None),
        dict(url=None, command=["c"], env=[], headers=["H=1"], kind=None),
        dict(url=None, command=["c"], env=[], headers=[], kind="sse"),
    ],
)
def test_build_server_rejects_inconsistent_arguments(kwargs):
    with pytest.raises(McpConfigError):
        mcp.build_server(**kwargs)


# ---- add ------------------------------------------------------------------


def test_add_writes_neutral_config_and_hints_at_apply(project, capsys):
    write_vars(project, "awx_mcp_api_key")
    rc = mcp.cmd_add("awx", command=["python3", "x.py"], env=["AWX_MCP_API_KEY"], interactive=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert read_cfg(project) == {
        "mcpServers": {
            "awx": {
                "command": "python3",
                "args": ["x.py"],
                "env": {"AWX_MCP_API_KEY": "${AWX_MCP_API_KEY}"},
            }
        }
    }
    assert "rig apply --authorize" in out
    assert "no value yet" not in out  # matched awx_mcp_api_key ignoring case


def test_add_prompts_for_and_vaults_each_unresolved_variable(project, monkeypatch):
    stored = []
    monkeypatch.setattr(
        "cli.encrypt.store_encrypted_var",
        lambda name, value, project_dir=None, personal=None, **k: stored.append((name, value, personal)),
    )
    answers = iter(["s3cret"])
    rc = mcp.cmd_add(
        "ctx",
        url="https://x",
        headers=["K=${CTX_KEY}", "D=${OTHER:-dflt}"],
        interactive=True,
        prompt=lambda label, env: next(answers),
        personal=True,
    )
    assert rc == 0
    assert stored == [("ctx_key", "s3cret", True)]  # lower-case name; default var not prompted


def test_add_skips_blank_answers_and_still_saves_the_server(project, monkeypatch, capsys):
    monkeypatch.setattr("cli.encrypt.store_encrypted_var", lambda *a, **k: pytest.fail("stored"))
    rc = mcp.cmd_add("a", command=["c"], env=["TOKEN"], interactive=True, prompt=lambda label, env: "")
    assert rc == 0 and "a" in read_cfg(project)["mcpServers"]
    assert "skipped" in capsys.readouterr().out


def test_add_non_interactive_prints_how_to_store_the_value(project, capsys):
    mcp.cmd_add("a", command=["c"], env=["MY_TOKEN"], interactive=False)
    assert "rig vars encrypt --stdin-name my_token" in capsys.readouterr().out


def test_add_refuses_to_replace_without_force(project, capsys):
    mcp.cmd_add("a", command=["one"], interactive=False)
    assert mcp.cmd_add("a", command=["two"], interactive=False) == 1
    assert "--force" in capsys.readouterr().err
    assert read_cfg(project)["mcpServers"]["a"]["command"] == "one"
    assert mcp.cmd_add("a", command=["two"], force=True, interactive=False) == 0
    assert read_cfg(project)["mcpServers"]["a"]["command"] == "two"


def test_add_reports_bad_arguments(project, capsys):
    assert mcp.cmd_add("a", interactive=False) == 1
    assert mcp.cmd_add("bad name", command=["c"], interactive=False) == 1
    assert "Error:" in capsys.readouterr().err
    assert not (project / ".rig" / "mcp.json").exists()


def test_add_refuses_an_invalid_existing_file(project, capsys):
    (project / ".rig" / "mcp.json").write_text("{nope")
    assert mcp.cmd_add("a", command=["c"], interactive=False) == 1
    assert "not valid JSON" in capsys.readouterr().err


# ---- remove / list / check -----------------------------------------------------


def test_remove_deletes_only_the_named_server(project, capsys):
    mcp.cmd_add("a", command=["c"], interactive=False)
    mcp.cmd_add("b", command=["c"], interactive=False)
    assert mcp.cmd_remove("a") == 0
    assert list(read_cfg(project)["mcpServers"]) == ["b"]
    assert "until recreated" in capsys.readouterr().out
    assert mcp.cmd_remove("a") == 1


def test_check_flags_missing_variables_and_list_does_not_fail(project, capsys):
    write_vars(project, "have_it")
    mcp.cmd_add("ok", command=["c"], env=["HAVE_IT"], interactive=False)
    mcp.cmd_add("bad", url="https://x", headers=["K=${MISSING}"], interactive=False)
    capsys.readouterr()
    assert mcp.cmd_check() == 1
    out = capsys.readouterr().out
    assert "ok [stdio] c: ok" in out and "bad [http] https://x: missing MISSING" in out
    assert mcp.cmd_list() == 0


def test_check_passes_when_everything_resolves_and_when_empty(project, capsys):
    assert mcp.cmd_check() == 0
    assert "No MCP servers" in capsys.readouterr().out
    write_vars(project, "k")
    mcp.cmd_add("a", command=["c"], env=["K"], interactive=False)
    assert mcp.cmd_check() == 0


def test_check_fails_on_invalid_config(project, capsys):
    (project / ".rig" / "mcp.json").write_text('{"mcpServers": {"a": {}}}')
    assert mcp.cmd_check() == 1
    assert "exactly one of" in capsys.readouterr().out
    assert mcp.cmd_list() == 1


def test_check_reports_names_that_differ_only_by_case(project, capsys):
    write_vars(project, "Foo", "FOO")
    # `add` refuses up front and saves nothing...
    assert mcp.cmd_add("a", command=["c"], env=["foo"], interactive=False) == 1
    assert "differ only by case" in capsys.readouterr().err
    assert not (project / ".rig" / "mcp.json").exists()
    # ...and `check` reports it for a hand-written file.
    (project / ".rig" / "mcp.json").write_text('{"mcpServers": {"a": {"command": "c", "env": {"K": "${foo}"}}}}')
    assert mcp.cmd_check() == 1
    assert "differ only by case" in capsys.readouterr().out


def test_check_warns_about_unsupported_fields(project, capsys):
    (project / ".rig" / "mcp.json").write_text('{"mcpServers": {"a": {"command": "c", "timeout": 3}}}')
    assert mcp.cmd_check() == 0
    assert "field 'timeout' is not supported" in capsys.readouterr().out


# ---- legacy per-agent files ---------------------------------------------------


def test_legacy_per_agent_files_with_servers_are_reported(project, capsys):
    (project / ".rig" / ".claude").mkdir()
    (project / ".rig" / ".claude" / "mcp.json").write_text('{"mcpServers": {"x": {"command": "c"}}}')
    (project / ".rig" / ".cursor").mkdir()
    (project / ".rig" / ".cursor" / "mcp.json").write_text('{"mcpServers": {}}')  # empty: fine
    (project / ".rig" / ".opencode").mkdir()
    (project / ".rig" / ".opencode" / "mcp.json").write_text("{broken")  # unreadable: ignored
    assert mcp.legacy_files_with_servers() == [".rig/.claude/mcp.json"]
    mcp.cmd_check()
    assert "move them to .rig/mcp.json" in capsys.readouterr().out


# ---- the real CLI entrypoint -----------------------------------------------------


def test_main_splits_the_command_after_double_dash(project, capsys):
    rc = main(["mcp", "add", "awx", "--env", "K=v", "--", "python3", "-u", "x.py"])
    assert rc == 0
    assert read_cfg(project)["mcpServers"]["awx"] == {
        "command": "python3",
        "args": ["-u", "x.py"],
        "env": {"K": "v"},
    }


def test_main_mcp_without_action_prints_help(project, capsys):
    assert main(["mcp"]) == 1
    assert "add" in capsys.readouterr().out


def test_main_dispatches_list_check_remove(project, capsys):
    main(["mcp", "add", "a", "--", "c"])
    assert main(["mcp", "list"]) == 0
    assert main(["mcp", "check"]) == 0
    assert main(["mcp", "remove", "a"]) == 0


# ---- ansible-playbook invocation ---------------------------------------------------


def _manager(tmp_path):
    from unittest.mock import MagicMock  # noqa: F401

    from cli.remote_dev import AnsibleManager, ProjectConfig

    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    (tmp_path / "inventory.yml").write_text("all: {}\n")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    manager.inventory_path = str(tmp_path / "inventory.yml")
    return manager


def _run(manager, playbook):
    from unittest.mock import MagicMock, patch

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env") or {}
        return MagicMock(returncode=0, stdout="PLAY RECAP", stderr="")

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        assert manager._run_one_playbook(playbook, stream=False)
    return seen["env"]


def test_playbooks_run_with_rigs_filter_plugins_on_the_path(tmp_path):
    import os

    manager = _manager(tmp_path)
    env = _run(manager, str(tmp_path / ".rig" / "authorize.yml"))
    first = env["ANSIBLE_FILTER_PLUGINS"].split(os.pathsep)[0]
    assert os.path.isfile(os.path.join(first, "rig_mcp.py"))


def test_authorize_warns_once_about_ignored_legacy_mcp_files(tmp_path):
    from unittest.mock import patch

    manager = _manager(tmp_path)
    legacy = tmp_path / ".rig" / ".cursor"
    legacy.mkdir(exist_ok=True)
    (legacy / "mcp.json").write_text('{"mcpServers": {"x": {"command": "c"}}}')

    with patch("cli.remote_dev.log.warning") as warn:
        _run(manager, str(tmp_path / ".rig" / "authorize.yml"))
        _run(manager, str(tmp_path / ".rig" / "materialize.yml"))  # other Stages stay quiet
    messages = [call.args[-1] for call in warn.call_args_list]
    assert len(messages) == 1
    assert ".rig/.cursor/mcp.json" in messages[0] and ".rig/mcp.json" in messages[0]


def test_authorize_is_quiet_without_legacy_mcp_files(tmp_path):
    from unittest.mock import patch

    manager = _manager(tmp_path)
    with patch("cli.remote_dev.log.warning") as warn:
        _run(manager, str(tmp_path / ".rig" / "authorize.yml"))
    warn.assert_not_called()
