"""Tests for rig dev / open VS Code on remote env."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.main import main
from cli.remote_dev import (
    REMOTE_WORKSPACE_ROOT,
    RemoteDevEnvironment,
    remote_workspace_path,
)


def test_remote_workspace_path_helpers():
    assert REMOTE_WORKSPACE_ROOT == "/workspace"
    assert remote_workspace_path() == "/workspace"
    assert remote_workspace_path("app") == "/workspace/app"
    assert remote_workspace_path("/app/") == "/workspace/app"


def test_dev_help(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["dev", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--browser" in help_text
    assert "--path" in help_text
    assert "Remote-SSH" in help_text or "code-server" in help_text


def test_ensure_ssh_config_host_upserts_block(tmp_path):
    env = RemoteDevEnvironment(name="dev-ssh1")
    env.vm_ip = "203.0.113.50"
    env.ssh_key_name = "dev-ssh1-key"
    config_path = str(tmp_path / "config")

    alias = env.ensure_ssh_config_host(ssh_config_path=config_path)
    assert alias == "rig-dev-ssh1"
    text = open(config_path, encoding="utf-8").read()
    assert "Host rig-dev-ssh1" in text
    assert "HostName 203.0.113.50" in text
    assert f"IdentityFile /tmp/{env.ssh_key_name}" in text

    env.vm_ip = "203.0.113.51"
    env.ensure_ssh_config_host(ssh_config_path=config_path)
    text = open(config_path, encoding="utf-8").read()
    assert text.count("Host rig-dev-ssh1") == 1
    assert "HostName 203.0.113.51" in text

    env.remove_ssh_config_host(ssh_config_path=config_path)
    text = open(config_path, encoding="utf-8").read()
    assert "rig-dev-ssh1" not in text


def test_open_vscode_launches_code_with_folder_uri(tmp_path):
    env = RemoteDevEnvironment(name="dev-ide")
    env.vm_ip = "198.51.100.9"
    env.ssh_key_name = "dev-ide-key"
    key_path = f"/tmp/{env.ssh_key_name}"
    config_path = str(tmp_path / "ssh_config")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0)

    with open(key_path, "w", encoding="utf-8") as handle:
        handle.write("fake-key\n")
    try:
        with (
            patch.object(RemoteDevEnvironment, "resolve_editor_command", return_value="/usr/bin/code"),
            patch("cli.remote_dev.subprocess.run", side_effect=fake_run),
        ):
            code = env.open_vscode(
                remote_path="/workspace/app",
                ssh_config_path=config_path,
            )
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass

    assert code == 0
    assert captured["cmd"][0] == "/usr/bin/code"
    assert "--folder-uri" in captured["cmd"]
    assert captured["cmd"][-1] == "vscode-remote://ssh-remote+rig-dev-ide/workspace/app"
    assert "HostName 198.51.100.9" in open(config_path, encoding="utf-8").read()


def test_open_vscode_browser_only_when_requested(tmp_path):
    env = RemoteDevEnvironment(name="dev-web")
    env.vm_ip = "198.51.100.10"
    env.ssh_key_name = "dev-web-key"
    key_path = f"/tmp/{env.ssh_key_name}"
    with open(key_path, "w", encoding="utf-8") as handle:
        handle.write("fake\n")
    try:
        with patch("cli.remote_dev.webbrowser.open", return_value=True) as opened:
            code = env.open_vscode(browser=True)
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass
    assert code == 0
    opened.assert_called_once_with("http://198.51.100.10:8080/?folder=/workspace")


def test_open_vscode_errors_when_no_editor_without_browser(tmp_path, capsys):
    from cli import log as rig_log

    env = RemoteDevEnvironment(name="dev-noed")
    env.vm_ip = "198.51.100.11"
    env.ssh_key_name = "dev-noed-key"
    key_path = f"/tmp/{env.ssh_key_name}"
    with open(key_path, "w", encoding="utf-8") as handle:
        handle.write("fake\n")
    try:
        rig_log.configure_logging()
        with (
            patch.object(RemoteDevEnvironment, "resolve_editor_command", return_value=None),
            patch("cli.remote_dev.webbrowser.open") as opened,
        ):
            code = env.open_vscode(browser=False)
        err = capsys.readouterr().err
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass
    assert code == 1
    opened.assert_not_called()
    assert "No local editor CLI found" in err


def test_dev_cli_uses_last_env_and_opens_vscode():
    with (
        patch("cli.main._load_credentials"),
        patch.dict(os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False),
        patch("cli.main.StorageManager", side_effect=ValueError("no storage")),
        patch("cli.main.resolve_env_name", return_value="dev-last"),
        patch("cli.main.RemoteDevEnvironment") as env_cls,
        patch("cli.main.remember_last_env") as remember,
    ):
        instance = MagicMock()
        instance.name = "dev-last"
        instance.vm_ip = "10.0.0.8"
        instance.resolve_resources.return_value = True
        instance.open_vscode.return_value = 0
        env_cls.return_value = instance

        assert main(["dev", "--browser"]) == 0
        remember.assert_called_once_with("dev-last")
        instance.open_vscode.assert_called_once()
        kwargs = instance.open_vscode.call_args.kwargs
        assert kwargs.get("browser") is True
        assert kwargs.get("remote_path") == "/workspace"


def test_normalize_and_project_editor_preference(tmp_path):
    from cli.remote_dev import (
        ProjectConfig,
        configured_editors,
        normalize_editor_type,
        project_editor_preference,
    )

    assert normalize_editor_type("Cursor") == "cursor"
    assert normalize_editor_type("vscode") == "code"
    with pytest.raises(ValueError):
        normalize_editor_type("notepad")

    assert project_editor_preference(str(tmp_path)) is None
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    assert project_editor_preference(str(tmp_path)) == "code"
    cfg.load()
    cfg.config["editor"] = {"default": "cursor", "enabled": ["code", "cursor"]}
    cfg.save(write_vars=False)
    assert project_editor_preference(str(tmp_path)) == "cursor"
    assert configured_editors(cfg) == (["code", "cursor"], "cursor")


def test_reinit_renames_legacy_editor_and_agent_type(tmp_path):
    from cli.remote_dev import ProjectConfig

    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    cfg.load()
    cfg.config.pop("editor", None)
    cfg.config["default_editor"] = "cursor"
    cfg.config["editors"] = ["code", "cursor"]
    cfg.config["agent"] = {
        "type": "claude",
        "types": ["opencode", "claude"],
    }
    cfg.save(write_vars=False)

    again = ProjectConfig(str(tmp_path))
    assert again.init(name="ws")
    assert again.config["editor"]["default"] == "cursor"
    assert again.config["editor"]["enabled"] == ["code", "cursor"]
    assert "default_editor" not in again.config
    assert "editors" not in again.config
    assert again.config["agent"]["default"] == "claude"
    assert again.config["agent"]["enabled"] == ["opencode", "claude"]
    assert "type" not in again.config["agent"]
    assert "types" not in again.config["agent"]

    plain = ProjectConfig(str(tmp_path / "plain"))
    plain.config = {
        "agent": {
            "default": "opencode",
            "type": "claude",
            "enabled": ["opencode"],
            "types": ["claude"],
        },
        "editor": "cursor",
    }
    plain._migrate_default_names()
    assert plain.config["agent"]["default"] == "opencode"
    assert plain.config["agent"]["enabled"] == ["opencode"]
    assert "type" not in plain.config["agent"]
    assert "types" not in plain.config["agent"]
    assert plain.config["editor"]["default"] == "cursor"
    plain.config = "nope"
    plain._migrate_default_names()
    assert plain.config == "nope"


def test_dev_cli_prefers_flag_over_rig_yml(tmp_path):
    with (
        patch("cli.main._load_credentials"),
        patch.dict(os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False),
        patch("cli.main.StorageManager", side_effect=ValueError("no storage")),
        patch("cli.main.resolve_env_name", return_value="dev-ed"),
        patch("cli.main.RemoteDevEnvironment") as env_cls,
        patch("cli.main.remember_last_env"),
        patch(
            "cli.remote_dev.configured_editors",
            return_value=(["cursor", "codium"], "cursor"),
        ) as pref,
    ):
        instance = MagicMock()
        instance.name = "dev-ed"
        instance.vm_ip = "10.0.0.9"
        instance.resolve_resources.return_value = True
        instance.open_vscode.return_value = 0
        env_cls.return_value = instance

        assert main(["dev", "--editor", "codium"]) == 0
        pref.assert_called()
        assert instance.open_vscode.call_args.kwargs.get("editor") == "codium"
        assert instance.open_vscode.call_args.kwargs.get("editor_fallbacks") == []

        instance.open_vscode.reset_mock()
        assert main(["dev"]) == 0
        assert instance.open_vscode.call_args.kwargs.get("editor") == "cursor"
        assert instance.open_vscode.call_args.kwargs.get("editor_fallbacks") == [
            "cursor",
            "codium",
        ]


def test_editor_selection_edges():
    from cli.remote_dev import (
        apply_editor_selection,
        coerce_editor_types,
        parse_editor_selection,
        prompt_default_editor,
        prompt_editors,
        resolve_configured_editors,
        configured_editors,
    )

    class _Label:
        def __init__(self, text):
            self.text = text

        def __str__(self):
            return self.text

    assert coerce_editor_types(None) == []
    assert coerce_editor_types("  ") == []
    assert coerce_editor_types(["code", None, "vscode", "cursor"]) == ["code", "cursor"]
    assert coerce_editor_types(_Label("codium")) == ["codium"]
    assert coerce_editor_types(_Label("  ")) == []

    assert resolve_configured_editors({}) == ([], None)
    enabled, default = resolve_configured_editors({"editor": {"enabled": ["nope"], "default": "cursor"}})
    assert enabled == ["cursor"]
    assert default == "cursor"
    assert resolve_configured_editors({"editor": "cursor"}) == (["cursor"], "cursor")
    assert resolve_configured_editors({"editors": ["code", "cursor"], "default_editor": "cursor"}) == (
        ["code", "cursor"],
        "cursor",
    )
    assert resolve_configured_editors({"editor": "also-nope"}) == ([], None)
    enabled, default = resolve_configured_editors({"editor": {"enabled": ["code", "cursor"], "default": "codium"}})
    assert enabled == ["code", "cursor"]
    assert default == "code"
    enabled, default = resolve_configured_editors(
        {"editor": {"enabled": ["code", "cursor"], "default": "not-an-editor"}}
    )
    assert enabled == ["code", "cursor"]
    assert default == "code"
    enabled, default = resolve_configured_editors(
        {
            "editor": {"enabled": ["code", "cursor"], "default": "code"},
            "editors": ["codium"],
            "default_editor": "codium",
        }
    )
    assert default == "code"
    assert enabled == ["code", "cursor"]

    cfg = {}
    selected = apply_editor_selection(cfg, ["code", "cursor"], default="nope")
    assert selected == ["code", "cursor"]
    assert cfg["editor"]["default"] == "code"
    assert cfg["editor"]["enabled"] == ["code", "cursor"]
    assert "default_editor" not in cfg
    assert "editors" not in cfg
    apply_editor_selection(cfg, "not-an-editor", default="cursor")
    assert cfg["editor"]["enabled"] == ["code"]
    assert cfg["editor"]["default"] == "code"

    assert parse_editor_selection("", default=["cursor"]) == ["cursor"]
    assert parse_editor_selection("all", default=["code"]) == [
        "code",
        "code-insiders",
        "cursor",
        "codium",
    ]
    assert parse_editor_selection(",,", default=["code"]) == ["code"]
    assert parse_editor_selection("vscode,cursor", default=["code"]) == ["code", "cursor"]
    with pytest.raises(ValueError):
        parse_editor_selection("9", default=["code"])
    with pytest.raises(ValueError):
        parse_editor_selection("emacs", default=["code"])

    assert parse_editor_selection("", default=["not-an-editor"]) == ["code"]
    answers = iter(["", "1,3", "2"])
    assert prompt_editors(default="not-an-editor", input_fn=lambda _: next(answers)) == ["code"]
    selected = prompt_editors(default=["code"], input_fn=lambda _: next(answers))
    assert selected == ["code", "cursor"]
    with pytest.raises(ValueError):
        prompt_default_editor(selected, default="code", input_fn=lambda _: next(answers))
    assert prompt_default_editor(["nope"], input_fn=lambda _: "unused") == "code"
    assert prompt_default_editor(["cursor"], input_fn=lambda _: "unused") == "cursor"
    assert prompt_default_editor(["code", "cursor"], default="cursor", input_fn=lambda _: "") == "cursor"
    assert prompt_default_editor(["code", "cursor"], default="not-an-editor", input_fn=lambda _: "") == "code"
    with pytest.raises(ValueError):
        prompt_default_editor(["code", "cursor"], input_fn=lambda _: "codium")
    assert prompt_default_editor(["code", "cursor"], input_fn=lambda _: "3") == "cursor"
    assert prompt_default_editor(["code", "cursor"], input_fn=lambda _: "cursor") == "cursor"
    assert configured_editors(None) == ([], None)


def test_resolve_editor_command_can_require_one_binary(monkeypatch):
    seen = []

    def fake_which(name):
        seen.append(name)
        if name == "cursor":
            return "/usr/bin/cursor"
        return None

    monkeypatch.setattr("cli.remote_dev.shutil.which", fake_which)
    assert RemoteDevEnvironment.resolve_editor_command(prefer="code", fallbacks=[]) is None
    assert seen == ["code"]
    assert RemoteDevEnvironment.resolve_editor_command(prefer="code", fallbacks=["cursor"]) == "/usr/bin/cursor"


def test_open_vscode_names_a_missing_strict_editor(capsys):
    from cli import log as rig_log

    env = RemoteDevEnvironment(name="dev-strict")
    env.vm_ip = "198.51.100.12"
    env.ssh_key_name = "dev-strict-key"
    key_path = f"/tmp/{env.ssh_key_name}"
    with open(key_path, "w", encoding="utf-8") as handle:
        handle.write("fake\n")
    try:
        rig_log.configure_logging()
        with patch.object(RemoteDevEnvironment, "resolve_editor_command", return_value=None):
            code = env.open_vscode(editor="cursor", editor_fallbacks=[])
        err = capsys.readouterr().err
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass
    assert code == 1
    assert "Editor 'cursor' not found" in err


def test_dev_rejects_editor_outside_the_configured_set(capsys):
    with (
        patch("cli.main._load_credentials"),
        patch.dict(os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False),
        patch("cli.main.StorageManager", side_effect=ValueError("no storage")),
        patch("cli.main.resolve_env_name", return_value="dev-ed"),
        patch("cli.main.RemoteDevEnvironment") as env_cls,
        patch("cli.main.remember_last_env"),
        patch(
            "cli.remote_dev.configured_editors",
            return_value=(["code", "cursor"], "code"),
        ),
    ):
        instance = MagicMock()
        instance.name = "dev-ed"
        instance.vm_ip = "10.0.0.9"
        instance.resolve_resources.return_value = True
        env_cls.return_value = instance
        assert main(["dev", "--editor", "codium"]) == 1
        instance.open_vscode.assert_not_called()
    assert "not enabled" in capsys.readouterr().err


def test_dev_rejects_unknown_editor_name(capsys):
    with (
        patch("cli.main._load_credentials"),
        patch.dict(os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False),
        patch("cli.main.StorageManager", side_effect=ValueError("no storage")),
        patch("cli.main.resolve_env_name", return_value="dev-ed"),
        patch("cli.main.RemoteDevEnvironment") as env_cls,
        patch("cli.main.remember_last_env"),
    ):
        instance = MagicMock()
        instance.resolve_resources.return_value = True
        instance.vm_ip = "10.0.0.9"
        env_cls.return_value = instance
        assert main(["dev", "--editor", "notepad"]) == 1
    assert "Unsupported editor" in capsys.readouterr().err


def test_dev_requires_name_when_no_last_env(capsys):
    with (
        patch("cli.main._load_credentials"),
        patch.dict(os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False),
        patch("cli.main.resolve_env_name", return_value=None),
    ):
        assert main(["dev"]) == 1
    assert "Environment name is required" in capsys.readouterr().err
