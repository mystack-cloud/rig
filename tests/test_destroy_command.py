"""Tests for rig delete / resource resolution."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.main import main
from cli.remote_dev import RemoteDevEnvironment


def test_destroy_cli_does_not_pass_hetzner_token():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls:
        instance = MagicMock()
        env_cls.return_value = instance
        assert main(["delete", "--name", "dev-37ffecd3"]) == 0
        env_cls.assert_called_once()
        kwargs = env_cls.call_args.kwargs
        assert kwargs.get("name") == "dev-37ffecd3"
        assert "hetzner_token" not in kwargs
        instance.cleanup.assert_called_once()
        assert instance.cleanup.call_args.kwargs["ansible_tags"] is None

        assert main(["delete", "--name", "dev-37ffecd3", "--tags", "cleanup"]) == 0
        assert instance.cleanup.call_args.kwargs["ansible_tags"] == ["cleanup"]


def test_destroy_accepts_positional_name():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls:
        instance = MagicMock()
        env_cls.return_value = instance
        assert main(["delete", "dev-pos1"]) == 0
        assert env_cls.call_args.kwargs.get("name") == "dev-pos1"
        instance.cleanup.assert_called_once()


def test_destroy_accepts_multiple_names():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls:
        instance = MagicMock()
        env_cls.return_value = instance
        assert main(["delete", "dev-a", "dev-b", "--name", "dev-c"]) == 0
        names = [call.kwargs.get("name") for call in env_cls.call_args_list]
        assert names == ["dev-a", "dev-b", "dev-c"]
        assert instance.cleanup.call_count == 3


def test_destroy_requires_at_least_one_name(capsys):
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.resolve_env_name", return_value=None):
        assert main(["delete"]) == 1
    assert "at least one environment name" in capsys.readouterr().err.lower()


def test_destroy_falls_back_to_last_env():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-last"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.clear_last_env"
    ) as clear:
        instance = MagicMock()
        env_cls.return_value = instance
        assert main(["delete"]) == 0
        assert env_cls.call_args.kwargs.get("name") == "dev-last"
        instance.cleanup.assert_called_once()
        clear.assert_called_once_with("dev-last")


def test_resolve_resources_from_state():
    env = RemoteDevEnvironment(name="dev-abc123")
    env.manager = MagicMock()
    env.manager._load_state.return_value = {
        "environments": [
            {
                "name": "dev-abc123",
                "id": 42,
                "ip": "1.2.3.4",
                "ssh_key_id": 7,
                "firewall_id": 9,
            }
        ]
    }
    assert env.resolve_resources() is True
    assert env.vm_id == 42
    assert env.vm_ip == "1.2.3.4"
    assert env.ssh_key_id == 7
    assert env.firewall_id == 9


def test_cleanup_resolves_then_deletes():
    env = RemoteDevEnvironment(name="dev-xyz")
    env.manager = MagicMock()
    env.manager._load_state.return_value = {
        "environments": [
            {"name": "dev-xyz", "id": 11, "ssh_key_id": 22, "firewall_id": 33}
        ]
    }
    env.manager.storage_manager = None
    env.manager.delete_vm.return_value = True
    env.manager.delete_firewall.return_value = True
    env.manager.delete_ssh_key.return_value = True

    with patch.object(env, "run_destroy_playbooks", return_value=True) as finalizers_pb:
        env.cleanup(project_dir="/tmp/project")

    finalizers_pb.assert_called_once_with(
        project_dir="/tmp/project",
        stream=True,
        ansible_tags=None,
    )
    env.manager.delete_vm.assert_called_once_with(11)
    env.manager.delete_firewall.assert_called_once_with(33)
    env.manager.delete_ssh_key.assert_called_once_with(22)


def test_cleanup_runs_finalizers_playbook_before_vm_delete(tmp_path):
    env = RemoteDevEnvironment(name="dev-order")
    env.vm_ip = "203.0.113.50"
    env.vm_id = 99
    env.manager = MagicMock()
    env.manager._load_state.return_value = {"environments": []}
    env.manager.storage_manager = None
    env.manager.delete_vm.return_value = True

    order = []

    def fake_finalizers_pb(**_kwargs):
        order.append("finalizers_playbook")
        return True

    def fake_delete_vm(_vm_id):
        order.append("delete_vm")
        return True

    env.manager.delete_vm.side_effect = fake_delete_vm
    with patch.object(env, "run_destroy_playbooks", side_effect=fake_finalizers_pb), patch.object(
        env, "resolve_resources", return_value=True
    ):
        env.cleanup(project_dir=str(tmp_path))

    assert order == ["finalizers_playbook", "delete_vm"]  # finalize→destroy then VM


def test_run_destroy_playbooks_invokes_ansible(tmp_path):
    from cli.remote_dev import ProjectConfig

    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    assert (tmp_path / ".rig" / "finalize.yml").is_file()

    with open("/tmp/dev-pb-key", "w", encoding="utf-8") as handle:
        handle.write("fake-key\n")
    env = RemoteDevEnvironment(name="dev-pb")
    env.vm_ip = "203.0.113.60"
    env.ssh_key_name = "dev-pb-key"
    try:
        with patch("cli.remote_dev.AnsibleManager") as ansible_cls:
            instance = MagicMock()
            instance.configure.return_value = True
            instance.setup_bootstrap.return_value = True
            instance._run_one_playbook.return_value = True
            instance.finalize_playbook_path = str(tmp_path / ".rig" / "finalize.yml")
            instance.destroy_playbook_path = str(tmp_path / ".rig" / "destroy.yml")
            ansible_cls.return_value = instance
            assert env.run_destroy_playbooks(project_dir=str(tmp_path)) is True
            instance.setup_bootstrap.assert_called_once()
            assert instance._run_one_playbook.call_count == 2
            paths = [c.args[0] for c in instance._run_one_playbook.call_args_list]
            assert paths[0].endswith("finalize.yml")
            assert paths[1].endswith("destroy.yml")
            assert env.run_destroy_playbooks(
                project_dir=str(tmp_path),
                ansible_tags=["cleanup"],
            ) is True
            tagged = instance._run_one_playbook.call_args_list[-2:]
            assert tagged[0].kwargs["ansible_args"] == ["--tags", "cleanup"]
            assert tagged[1].kwargs["ansible_args"] == ["--tags", "cleanup"]
    finally:
        try:
            os.remove("/tmp/dev-pb-key")
        except OSError:
            pass
