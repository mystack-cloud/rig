"""Tests for rig attach / last-env fallback."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.main import _merge_agent_args, main


def test_attach_help(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["attach", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "last" in help_text.lower()
    assert "--continue" in help_text


def test_merge_agent_args_continue_session_resume_and_remainder():
    assert _merge_agent_args(continue_=True) == ["--continue"]
    assert _merge_agent_args(session="ses_1") == ["--session", "ses_1"]
    assert _merge_agent_args(resume="") == ["--resume"]
    assert _merge_agent_args(resume="abc") == ["--resume", "abc"]
    assert _merge_agent_args(
        continue_=True,
        remainder=["--", "--model", "anthropic/claude"],
    ) == ["--continue", "--model", "anthropic/claude"]


def test_attach_requires_name_when_no_last_env(capsys):
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.resolve_env_name", return_value=None):
        assert main(["attach"]) == 1
    assert "Environment name is required" in capsys.readouterr().err


def test_attach_uses_positional_name_and_remembers():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.remember_last_env") as remember, patch(
        "cli.main.os.path.exists", return_value=True
    ):
        instance = MagicMock()
        instance.name = "dev-pos"
        instance.vm_ip = "1.2.3.4"
        instance.ssh_key_name = "dev-pos-key"
        instance.resolve_resources.return_value = True
        instance.attach_to_agent.return_value = 0
        env_cls.return_value = instance

        assert main(["attach", "dev-pos"]) == 0
        env_cls.assert_called_once()
        assert env_cls.call_args.kwargs.get("name") == "dev-pos"
        remember.assert_called_once_with("dev-pos")
        instance.attach_to_agent.assert_called_once()
        assert instance.attach_to_agent.call_args.kwargs.get("agent_args") == []


def test_attach_forwards_continue_flag():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-last"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ), patch("cli.main.os.path.exists", return_value=True):
        instance = MagicMock()
        instance.name = "dev-last"
        instance.vm_ip = "10.0.0.1"
        instance.ssh_key_name = "dev-last-key"
        instance.resolve_resources.return_value = True
        instance.attach_to_agent.return_value = 0
        env_cls.return_value = instance

        assert main(["attach", "-c"]) == 0
        assert instance.attach_to_agent.call_args.kwargs.get("agent_args") == [
            "--continue"
        ]


def test_attach_forwards_remainder_after_dashdash():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-last"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ), patch("cli.main.os.path.exists", return_value=True):
        instance = MagicMock()
        instance.name = "dev-last"
        instance.vm_ip = "10.0.0.1"
        instance.ssh_key_name = "dev-last-key"
        instance.resolve_resources.return_value = True
        instance.attach_to_agent.return_value = 0
        env_cls.return_value = instance

        assert main(["attach", "--", "--prompt", "hello world"]) == 0
        assert instance.attach_to_agent.call_args.kwargs.get("agent_args") == [
            "--prompt",
            "hello world",
        ]


def test_attach_falls_back_to_last_env():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-last"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ), patch("cli.main.os.path.exists", return_value=True):
        instance = MagicMock()
        instance.name = "dev-last"
        instance.vm_ip = "10.0.0.1"
        instance.ssh_key_name = "dev-last-key"
        instance.resolve_resources.return_value = True
        instance.attach_to_agent.return_value = 0
        env_cls.return_value = instance

        assert main(["attach"]) == 0
        assert env_cls.call_args.kwargs.get("name") == "dev-last"


def test_attach_fails_when_ssh_key_missing(capsys):
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.os.path.exists", return_value=False):
        instance = MagicMock()
        instance.name = "dev-nokey"
        instance.vm_ip = "1.2.3.4"
        instance.ssh_key_name = "dev-nokey-key"
        instance.resolve_resources.return_value = True
        env_cls.return_value = instance

        assert main(["attach", "dev-nokey"]) == 1
    assert "SSH private key not found" in capsys.readouterr().err


def test_destroy_clears_matching_last_env():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.clear_last_env") as clear:
        instance = MagicMock()
        env_cls.return_value = instance
        assert main(["destroy", "dev-gone"]) == 0
        clear.assert_called_once_with("dev-gone")
