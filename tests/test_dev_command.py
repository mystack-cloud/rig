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
        with patch.object(
            RemoteDevEnvironment, "resolve_editor_command", return_value="/usr/bin/code"
        ), patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
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
        with patch.object(
            RemoteDevEnvironment, "resolve_editor_command", return_value=None
        ), patch("cli.remote_dev.webbrowser.open") as opened:
            code = env.open_vscode(browser=False)
        err = capsys.readouterr().err
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass
    assert code == 1
    opened.assert_not_called()
    assert "No local VS Code/Cursor CLI" in err


def test_dev_cli_uses_last_env_and_opens_vscode():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-last"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ) as remember:
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


def test_dev_requires_name_when_no_last_env(capsys):
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.resolve_env_name", return_value=None):
        assert main(["dev"]) == 1
    assert "Environment name is required" in capsys.readouterr().err
