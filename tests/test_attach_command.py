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
    assert "--agent" in help_text


def test_merge_agent_args_continue_session_resume_and_remainder():
    assert _merge_agent_args(continue_=True) == ["--continue"]
    assert _merge_agent_args(session="ses_1") == ["--session", "ses_1"]
    assert _merge_agent_args(resume="") == ["--resume"]
    assert _merge_agent_args(resume="abc") == ["--resume", "abc"]
    assert _merge_agent_args(
        continue_=True,
        remainder=["--", "--model", "anthropic/claude"],
    ) == ["--continue", "--model", "anthropic/claude"]


def test_ssh_uses_last_env_and_forwards_a_command():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-ssh"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ) as remember, patch("cli.main.os.path.exists", return_value=True):
        instance = MagicMock()
        instance.name = "dev-ssh"
        instance.vm_ip = "203.0.113.9"
        instance.ssh_key_name = "dev-ssh-key"
        instance.resolve_resources.return_value = True
        instance.open_ssh.return_value = 0
        env_cls.return_value = instance

        assert main(["ssh", "--", "uptime", "-p"]) == 0
        instance.ensure_firewall_access.assert_called_once()
        remember.assert_called_once_with("dev-ssh")
        assert instance.open_ssh.call_args.kwargs["remote_command"] == "uptime -p"


def test_ssh_uses_named_env_and_a_command():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.remember_last_env"), patch(
        "cli.main.os.path.exists", return_value=True
    ):
        instance = MagicMock()
        instance.name = "dev-named"
        instance.vm_ip = "203.0.113.8"
        instance.ssh_key_name = "dev-named-key"
        instance.resolve_resources.return_value = True
        instance.open_ssh.return_value = 0
        env_cls.return_value = instance

        assert main(["ssh", "dev-named", "--", "echo", "hi"]) == 0
        assert env_cls.call_args.kwargs["name"] == "dev-named"
        assert instance.open_ssh.call_args.kwargs["remote_command"] == "echo hi"


def test_ssh_fails_without_token(capsys):
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "", "HZ_TOKEN": ""}, clear=False
    ):
        assert main(["ssh"]) == 1
    assert "credentials" in capsys.readouterr().err.lower()


def test_ssh_fails_when_vm_or_key_missing(capsys):
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.resolve_env_name", return_value="dev-ssh"):
        missing = MagicMock()
        missing.resolve_resources.return_value = False
        missing.vm_ip = None
        env_cls.return_value = missing
        assert main(["ssh"]) == 1

        missing.resolve_resources.return_value = True
        missing.vm_ip = "1.2.3.4"
        missing.ssh_key_name = "missing-key"
        with patch("cli.main.os.path.exists", return_value=False):
            assert main(["ssh"]) == 1
    err = capsys.readouterr().err
    assert "no VM IP found" in err
    assert "SSH private key not found" in err


def test_ssh_reports_connection_errors(capsys):
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.RemoteDevEnvironment", side_effect=RuntimeError("boom")
    ), patch("cli.main.resolve_env_name", return_value="dev-ssh"):
        assert main(["ssh"]) == 1
    assert "Error connecting to environment: boom" in capsys.readouterr().err


def test_ssh_requires_name_when_no_last_env(capsys):
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.resolve_env_name", return_value=None):
        assert main(["ssh"]) == 1
    assert "Environment name is required" in capsys.readouterr().err


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
        "cli.main.read_last_session", return_value=None
    ), patch("cli.main.os.path.exists", return_value=True), patch(
        "cli.main.ProjectConfig"
    ) as project_cls:
        project = MagicMock()
        project.config = {"agent": {"type": "opencode"}}
        project.get_vars.return_value = None
        project_cls.return_value = project
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


def test_attach_auto_resumes_remembered_session():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-last"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ), patch(
        "cli.main.read_last_session",
        return_value=("7b0ada27-f52d-4e43-b746-2ebf2074a462", "claude"),
    ), patch("cli.main.os.path.exists", return_value=True), patch(
        "cli.main.ProjectConfig"
    ) as project_cls:
        project = MagicMock()
        project.config = {"agent": {"type": "claude"}}
        project.get_vars.return_value = None
        project_cls.return_value = project
        instance = MagicMock()
        instance.name = "dev-last"
        instance.vm_ip = "10.0.0.1"
        instance.ssh_key_name = "dev-last-key"
        instance.resolve_resources.return_value = True
        instance.attach_to_agent.return_value = 0
        env_cls.return_value = instance

        assert main(["attach"]) == 0
        assert instance.attach_to_agent.call_args.kwargs.get("agent_args") == [
            "--resume",
            "7b0ada27-f52d-4e43-b746-2ebf2074a462",
        ]


def test_attach_explicit_continue_skips_auto_resume():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-last"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ), patch(
        "cli.main.read_last_session",
        return_value=("ses_old", "opencode"),
    ) as read_session, patch("cli.main.os.path.exists", return_value=True), patch(
        "cli.main.ProjectConfig"
    ) as project_cls:
        project = MagicMock()
        project.config = {"agent": {"type": "opencode"}}
        project.get_vars.return_value = None
        project_cls.return_value = project
        instance = MagicMock()
        instance.name = "dev-last"
        instance.vm_ip = "10.0.0.1"
        instance.ssh_key_name = "dev-last-key"
        instance.resolve_resources.return_value = True
        instance.attach_to_agent.return_value = 0
        env_cls.return_value = instance

        assert main(["attach", "-c"]) == 0
        read_session.assert_not_called()
        assert instance.attach_to_agent.call_args.kwargs.get("agent_args") == [
            "--continue"
        ]


def test_attach_forwards_continue_flag():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-last"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ), patch("cli.main.os.path.exists", return_value=True), patch(
        "cli.main.ProjectConfig"
    ) as project_cls:
        project = MagicMock()
        project.config = {"agent": {"type": "opencode"}}
        project.get_vars.return_value = None
        project_cls.return_value = project
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
    ), patch("cli.main.read_last_session", return_value=None), patch(
        "cli.main.os.path.exists", return_value=True
    ), patch("cli.main.ProjectConfig") as project_cls:
        project = MagicMock()
        project.config = {"agent": {"type": "opencode"}}
        project.get_vars.return_value = None
        project_cls.return_value = project
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
    ), patch("cli.main.read_last_session", return_value=None), patch(
        "cli.main.os.path.exists", return_value=True
    ), patch("cli.main.ProjectConfig") as project_cls:
        project = MagicMock()
        project.config = {"agent": {"type": "opencode"}}
        project.get_vars.return_value = None
        project_cls.return_value = project
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


def _run_attach(argv, agent, *, session=None):
    """Run ``rig attach`` with a fake VM and the given workspace agent config."""
    project = MagicMock()
    project.config = {"agent": agent}
    project.get_vars.return_value = None
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-agents"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ), patch("cli.main.read_last_session", return_value=session), patch(
        "cli.main.os.path.exists", return_value=True
    ), patch("cli.main.ProjectConfig") as project_cls:
        project_cls.return_value = project
        instance = MagicMock()
        instance.name = "dev-agents"
        instance.vm_ip = "10.1.2.3"
        instance.ssh_key_name = "dev-agents-key"
        instance.resolve_resources.return_value = True
        instance.attach_to_agent.return_value = 0
        env_cls.return_value = instance
        code = main(argv)
    return code, instance


_MULTI = {
    "type": "opencode",
    "types": ["opencode", "claude"],
}


def test_attach_agent_flag_opens_enabled_agent():
    code, instance = _run_attach(["attach", "--agent", "claude-code"], _MULTI)
    assert code == 0
    kwargs = instance.attach_to_agent.call_args.kwargs
    assert kwargs["agent_type"] == "claude"
    assert kwargs["agent_args"] == []


def test_attach_agent_flag_rejects_agent_that_is_not_enabled(capsys):
    code, instance = _run_attach(["attach", "--agent", "cursor"], _MULTI)
    assert code == 1
    instance.attach_to_agent.assert_not_called()
    err = capsys.readouterr().err
    assert "not enabled" in err
    assert "opencode" in err
    assert "claude" in err


def test_attach_agent_flag_rejects_unknown_name(capsys):
    code, instance = _run_attach(["attach", "--agent", "nope"], _MULTI)
    assert code == 1
    instance.attach_to_agent.assert_not_called()
    assert "Unsupported coding agent" in capsys.readouterr().err


def test_attach_uses_configured_default_when_it_is_not_first_selected():
    code, instance = _run_attach(
        ["attach"],
        {"type": "claude", "types": ["opencode", "claude"]},
    )
    assert code == 0
    assert instance.attach_to_agent.call_args.kwargs["agent_type"] == "claude"
    assert instance.attach_to_agent.call_args.kwargs["agent_args"] == []


def test_attach_resumes_session_owned_by_another_enabled_agent():
    session_id = "7b0ada27-f52d-4e43-b746-2ebf2074a462"
    code, instance = _run_attach(
        ["attach"],
        _MULTI,
        session=(session_id, "claude"),
    )
    assert code == 0
    kwargs = instance.attach_to_agent.call_args.kwargs
    assert kwargs["agent_type"] == "claude"
    assert kwargs["agent_args"] == ["--resume", session_id]


def test_attach_agent_flag_does_not_resume_another_agents_session():
    code, instance = _run_attach(
        ["attach", "--agent", "opencode"],
        _MULTI,
        session=("ses_claude", "claude"),
    )
    assert code == 0
    kwargs = instance.attach_to_agent.call_args.kwargs
    assert kwargs["agent_type"] == "opencode"
    assert kwargs["agent_args"] == []


def test_attach_agent_flag_resumes_when_it_matches_remembered_session():
    code, instance = _run_attach(
        ["attach", "--agent", "claude"],
        _MULTI,
        session=("ses_claude", "claude"),
    )
    assert code == 0
    kwargs = instance.attach_to_agent.call_args.kwargs
    assert kwargs["agent_type"] == "claude"
    assert kwargs["agent_args"] == ["--resume", "ses_claude"]


def test_attach_ignores_remembered_session_for_an_agent_that_is_not_enabled():
    code, instance = _run_attach(
        ["attach"],
        _MULTI,
        session=("ses_cursor", "cursor"),
    )
    assert code == 0
    kwargs = instance.attach_to_agent.call_args.kwargs
    assert kwargs["agent_type"] == "opencode"
    assert kwargs["agent_args"] == []


def test_destroy_clears_matching_last_env():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.clear_last_env") as clear, patch(
        "cli.main.clear_last_session"
    ) as clear_session:
        instance = MagicMock()
        env_cls.return_value = instance
        assert main(["delete", "dev-gone"]) == 0
        clear.assert_called_once_with("dev-gone")
        clear_session.assert_called_once_with("dev-gone")
