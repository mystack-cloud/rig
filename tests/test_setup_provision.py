"""Tests for rig create provisioning + agent attach wiring."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.main import main
from cli.remote_dev import AnsibleManager, ProjectConfig, RemoteDevEnvironment


def test_agent_remote_command_opencode_and_claude():
    opencode = RemoteDevEnvironment.agent_remote_command("opencode")
    assert "opencode" in opencode
    assert "credentials.env" in opencode
    assert "mkdir -p /workspace" in opencode
    assert "cd /workspace" in opencode

    claude = RemoteDevEnvironment.agent_remote_command("claude")
    assert "claude" in claude
    assert ".local/bin" in claude
    assert "mkdir -p /workspace" in claude


def test_provision_requires_rig_yml(tmp_path):
    env = RemoteDevEnvironment(name="demo")
    env.vm_ip = "1.2.3.4"
    env.ssh_key_name = "demo-key"
    assert env.provision(project_dir=str(tmp_path), stream=False) is False


def test_provision_runs_playbooks_in_order(tmp_path):
    project_dir = tmp_path / "ws"
    project_dir.mkdir()
    cfg = ProjectConfig(str(project_dir))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="ws")
    env.vm_ip = "10.0.0.8"
    env.ssh_key_name = "ws-key"
    real_key = "/tmp/ws-key"
    with open(real_key, "w", encoding="utf-8") as handle:
        handle.write("fake\n")

    calls = []

    def fake_run_one(self, playbook_path, extra_vars=None, vault_password=None, stream=False):
        calls.append({"path": playbook_path, "stream": stream})
        return True

    try:
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run_one
        ):
            assert env.provision(project_dir=str(project_dir), stream=True) is True
    finally:
        try:
            os.remove(real_key)
        except OSError:
            pass

    assert len(calls) == 2
    assert calls[0]["stream"] is True
    assert calls[0]["path"].endswith("system.yml")
    assert calls[1]["path"].endswith("workspace.yml")


def test_apply_playbooks_workspace_only_by_default(tmp_path):
    project_dir = tmp_path / "ws"
    project_dir.mkdir()
    cfg = ProjectConfig(str(project_dir))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="ws-apply")
    env.vm_ip = "10.0.0.9"
    env.ssh_key_name = "ws-apply-key"
    real_key = "/tmp/ws-apply-key"
    with open(real_key, "w", encoding="utf-8") as handle:
        handle.write("fake\n")

    calls = []

    def fake_run_one(self, playbook_path, extra_vars=None, vault_password=None, stream=False):
        calls.append(playbook_path)
        return True

    try:
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run_one
        ):
            assert env.apply_playbooks(
                project_dir=str(project_dir),
                stream=False,
                playbooks=("workspace",),
            ) is True
            assert env.apply_playbooks(
                project_dir=str(project_dir),
                stream=False,
                playbooks=("system",),
            ) is True
    finally:
        try:
            os.remove(real_key)
        except OSError:
            pass

    assert len(calls) == 2
    assert calls[0].endswith("workspace.yml")
    assert calls[1].endswith("system.yml")


def test_apply_help_mentions_system(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["apply", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--system" in help_text
    assert "workspace" in help_text.lower()


def test_apply_command_defaults_to_workspace():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-apply"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ), patch("cli.main.os.path.exists", return_value=True):
        instance = MagicMock()
        instance.name = "dev-apply"
        instance.vm_ip = "10.0.0.2"
        instance.ssh_key_name = "dev-apply-key"
        instance.resolve_resources.return_value = True
        instance.apply_playbooks.return_value = True
        env_cls.return_value = instance

        assert main(["apply"]) == 0
        kwargs = instance.apply_playbooks.call_args.kwargs
        assert kwargs["playbooks"] == ("workspace",)

        assert main(["apply", "--system"]) == 0
        kwargs = instance.apply_playbooks.call_args.kwargs
        assert kwargs["playbooks"] == ("system",)


def test_attach_to_agent_builds_ssh_tty_command(tmp_path):
    env = RemoteDevEnvironment(name="attach-demo")
    env.vm_ip = "203.0.113.10"
    env.ssh_key_name = "attach-demo"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0)

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        code = env.attach_to_agent(agent_type="claude")

    assert code == 0
    assert captured["cmd"][0] == "ssh"
    assert "-tt" in captured["cmd"]
    assert "root@203.0.113.10" in captured["cmd"]
    assert any("exec claude" in part for part in captured["cmd"])


def test_attach_to_agent_forwards_extra_args():
    env = RemoteDevEnvironment(name="attach-args")
    env.vm_ip = "203.0.113.11"
    env.ssh_key_name = "attach-args"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0)

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        code = env.attach_to_agent(
            agent_type="opencode",
            agent_args=["--continue", "--prompt", "pick up where we left off"],
        )

    assert code == 0
    remote = captured["cmd"][-1]
    assert "exec opencode" in remote
    assert "--continue" in remote
    assert "pick" in remote


def test_agent_remote_command_quotes_args():
    cmd = RemoteDevEnvironment.agent_remote_command(
        "claude",
        extra_args=["--resume", "sess id"],
    )
    assert "exec claude" in cmd
    assert "--resume" in cmd
    assert "'sess id'" in cmd or '"sess id"' in cmd


def test_create_help_mentions_attach(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["create", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--no-attach" in help_text
    assert "attach" in help_text.lower()
    assert "--availability-zone" in help_text


def test_env_reads_availability_zone_from_config():
    env = RemoteDevEnvironment(
        name="az-demo",
        config={"vm": {"type": "cax21", "availability_zone": "hel1"}},
    )
    assert env.vm_type == "cax21"
    assert env.region == "hel1"


def test_env_falls_back_to_legacy_region_key():
    env = RemoteDevEnvironment(
        name="legacy-region",
        config={"vm": {"type": "cax11", "region": "nbg1"}},
    )
    assert env.region == "nbg1"


def test_create_uses_vm_from_rig_yml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    cfg.set("vm.type", "cax31")
    cfg.set("vm.availability_zone", "hel1")
    cfg.save(write_vars=False)

    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "token")
    captured = {}

    class FakeEnv:
        def __init__(self, name=None, config=None, storage_manager=None):
            captured["config"] = config
            self.name = name or "dev-x"
            self.config = config or {}

        def setup(self, project_dir=None):
            return False

    with patch("cli.main.RemoteDevEnvironment", FakeEnv), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ):
        assert main(["create", "--name", "from-yml"]) == 1

    assert captured["config"]["vm"]["type"] == "cax31"
    assert captured["config"]["vm"]["availability_zone"] == "hel1"


def test_run_all_playbooks_logs_command(tmp_path, capsys):
    from cli import log as rig_log

    cfg = ProjectConfig(str(tmp_path))
    cfg.init(name="play")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    manager.inventory_path = str(tmp_path / "inventory.yml")
    (tmp_path / "inventory.yml").write_text("all: {}\n")

    rig_log.configure_logging(verbose=True)
    with patch("cli.remote_dev.subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
        assert manager.run_all_playbooks(stream=False) is True

    err = capsys.readouterr().err
    assert "ansible-playbook" in err
    assert "system.yml" in err
    assert "@" in err  # -e @vars.yml


def test_setup_inventory_excludes_vault_secrets(tmp_path):
    from cli.remote_dev import VaultEncrypted

    cfg = ProjectConfig(str(tmp_path))
    cfg.init(name="inv")
    cfg.vars_config["github_pat"] = VaultEncrypted("$ANSIBLE_VAULT;1.1;AES256\nabc")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    assert manager.setup_inventory("1.2.3.4", "/tmp/key")
    text = (tmp_path / ".rig" / "inventory.yml").read_text()
    assert "remote_vm" in text
    assert "1.2.3.4" in text
    assert "github_pat" not in text
    assert "VaultEncrypted" not in text


def test_playbook_fails_when_no_hosts_matched(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    cfg.init(name="play")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    manager.inventory_path = str(tmp_path / ".rig" / "inventory.yml")
    manager.setup_inventory("1.2.3.4", "/tmp/key")

    fake = MagicMock(
        returncode=0,
        stdout="skipping: no hosts matched\nPLAY RECAP\n",
        stderr="",
    )
    with patch("cli.remote_dev.subprocess.run", return_value=fake):
        assert manager.run_playbook(stream=False) is False
