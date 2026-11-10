"""Extra mocked coverage for cli.main and related CLI helpers."""

from __future__ import annotations

import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli import main as main_mod
from cli.main import main


@pytest.fixture
def tok_env():
    """Hetzner token set; restores env after."""
    saved = os.environ.get("HETZNER_CLOUD_API_TOKEN")
    os.environ["HETZNER_CLOUD_API_TOKEN"] = "tok"
    yield
    if saved is None:
        os.environ.pop("HETZNER_CLOUD_API_TOKEN", None)
    else:
        os.environ["HETZNER_CLOUD_API_TOKEN"] = saved


def test_load_credentials_warning_on_config_error():
    with patch.object(
        main_mod.RigConfig, "apply_to_environ", side_effect=OSError("nope")
    ):
        assert main_mod._load_credentials() is False


def test_main_version_exits(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert out.strip().startswith("rig ")


def test_main_unknown_command_prints_help(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["nope"])
    assert caught.value.code == 2
    assert "invalid choice" in capsys.readouterr().err.lower()


def test_main_vars_without_subcommand(capsys):
    assert main(["vars"]) == 1
    assert "vars" in capsys.readouterr().out.lower()


def test_main_cache_unknown_action(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["cache", "purge"])
    assert caught.value.code == 2
    assert "invalid choice" in capsys.readouterr().err.lower()


def _no_hetzner_token():
    """Avoid ~/.rig/config repopulating the token during tests."""
    return patch.dict(
        os.environ,
        {"HETZNER_CLOUD_API_TOKEN": "", "HZ_TOKEN": ""},
        clear=False,
    )


def test_create_errors_without_token(capsys):
    with patch("cli.main._load_credentials", return_value=True), _no_hetzner_token():
        assert main(["create", "--name", "x"]) == 1
    assert "credentials" in capsys.readouterr().err.lower()


def test_apply_errors_without_token(capsys):
    with patch("cli.main._load_credentials", return_value=True), _no_hetzner_token():
        assert main(["apply", "env1"]) == 1
    assert "credentials" in capsys.readouterr().err.lower()


def test_list_errors_without_token(capsys):
    with patch("cli.main._load_credentials", return_value=True), _no_hetzner_token():
        assert main(["list"]) == 1
    assert "credentials" in capsys.readouterr().err.lower()


def test_destroy_errors_without_token(capsys):
    with patch("cli.main._load_credentials", return_value=True), _no_hetzner_token():
        assert main(["delete", "a"]) == 1
    assert "credentials" in capsys.readouterr().err.lower()


def test_cache_errors_without_token(capsys):
    with patch("cli.main._load_credentials", return_value=True), _no_hetzner_token():
        assert main(["cache", "list"]) == 1
    assert "credentials" in capsys.readouterr().err.lower()


def test_list_empty_and_nonempty(tok_env, capsys):
    with patch("cli.main._load_credentials"):
        with patch("cli.main.RemoteDevEnvironment") as env_cls:
            inst = MagicMock()
            inst.list_environments.return_value = []
            env_cls.return_value = inst
            assert main(["list"]) == 0
            assert "No remote" in capsys.readouterr().err

            capsys.readouterr()
            inst.list_environments.return_value = [
                {"name": "a", "status": "running"}
            ]
            assert main(["list"]) == 0
            err = capsys.readouterr().err
            assert "Remote environments" in err
            assert "a" in err


def test_list_exception(tok_env, capsys):
    with patch("cli.main._load_credentials"):
        with patch("cli.main.RemoteDevEnvironment") as env_cls:
            env_cls.return_value.list_environments.side_effect = RuntimeError("boom")
            assert main(["list"]) == 1
    assert "Error listing" in capsys.readouterr().err


def test_apply_materialize_stack(tok_env):
    """--materialize adds authorize when not already selected."""
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.remember_last_env"), patch(
        "cli.main.os.path.exists", return_value=True
    ):
        inst = MagicMock()
        inst.resolve_resources.return_value = True
        inst.vm_ip = "10.0.0.1"
        inst.ssh_key_name = "k"
        inst.apply_playbooks.return_value = True
        env_cls.return_value = inst

        assert main(["apply", "--materialize"]) == 0
        pb = inst.apply_playbooks.call_args.kwargs["playbooks"]
        assert pb == ("authorize", "materialize")


def test_apply_authorize_only_branch(tok_env):
    """--authorize alone → authorize only."""
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.remember_last_env"), patch(
        "cli.main.os.path.exists", return_value=True
    ):
        inst = MagicMock()
        inst.resolve_resources.return_value = True
        inst.vm_ip = "10.0.0.1"
        inst.ssh_key_name = "k"
        inst.apply_playbooks.return_value = True
        env_cls.return_value = inst

        assert main(["apply", "--authorize"]) == 0
        assert inst.apply_playbooks.call_args.kwargs["playbooks"] == ("authorize",)


def test_apply_configure_only(tok_env):
    """--configure without --provision runs configure only."""
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.remember_last_env"), patch(
        "cli.main.os.path.exists", return_value=True
    ):
        inst = MagicMock()
        inst.resolve_resources.return_value = True
        inst.vm_ip = "10.0.0.1"
        inst.ssh_key_name = "k"
        inst.apply_playbooks.return_value = True
        env_cls.return_value = inst

        assert main(["apply", "--configure"]) == 0
        assert inst.apply_playbooks.call_args.kwargs["playbooks"] == ("configure",)


def test_apply_exception(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls:
        env_cls.return_value.resolve_resources.side_effect = RuntimeError("x")
        assert main(["apply"]) == 1
    assert "Error applying" in capsys.readouterr().err


def test_apply_no_vm_ip(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls:
        inst = MagicMock()
        inst.resolve_resources.return_value = True
        inst.vm_ip = ""
        env_cls.return_value = inst
        assert main(["apply"]) == 1
    assert "no VM IP" in capsys.readouterr().err


def test_apply_missing_ssh_key(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.os.path.exists", return_value=False):
        inst = MagicMock()
        inst.resolve_resources.return_value = True
        inst.vm_ip = "10.0.0.1"
        inst.ssh_key_name = "missing"
        env_cls.return_value = inst
        assert main(["apply"]) == 1
    assert "SSH private key" in capsys.readouterr().err


def test_create_success_and_no_attach(tok_env, tmp_path, capsys):
    (tmp_path / "rig.yml").write_text("name: p\n")
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.ProjectConfig") as pc_cls, patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.remember_last_env") as remember, patch(
        "cli.main.os.getcwd", return_value=str(tmp_path)
    ):
        pc = MagicMock()
        pc.config = {"vm": {}}
        pc.load.return_value = True
        pc_cls.return_value = pc

        inst = MagicMock()
        inst.name = "dev-c1"
        inst.setup.return_value = True
        inst.provision.return_value = True
        inst.attach_to_agent.return_value = 0
        env_cls.return_value = inst

        assert main(["create", "--name", "c1"]) == 0
        assert "Environment 'dev-c1' created" in capsys.readouterr().err
        inst.attach_to_agent.assert_called_once()
        inst.attach_to_agent.reset_mock()

        assert main(["create", "--name", "c2", "--no-attach"]) == 0
        inst.attach_to_agent.assert_not_called()
        remember.assert_called()


def test_create_setup_fails(tok_env, tmp_path, capsys):
    (tmp_path / "rig.yml").write_text("name: p\n")
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.ProjectConfig") as pc_cls, patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.os.getcwd", return_value=str(tmp_path)):
        pc = MagicMock()
        pc.config = {"vm": {}}
        pc.load.return_value = True
        pc_cls.return_value = pc
        env_cls.return_value.setup.return_value = False
        assert main(["create", "--name", "c"]) == 1


def test_create_provision_fails(tok_env, tmp_path):
    (tmp_path / "rig.yml").write_text("name: p\n")
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.ProjectConfig") as pc_cls, patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.os.getcwd", return_value=str(tmp_path)):
        pc = MagicMock()
        pc.config = {"vm": {}}
        pc.load.return_value = True
        pc_cls.return_value = pc
        inst = MagicMock()
        inst.setup.return_value = True
        inst.provision.return_value = False
        env_cls.return_value = inst
        assert main(["create", "--name", "c"]) == 1


def test_create_region_and_image_overrides(tok_env, tmp_path):
    (tmp_path / "rig.yml").write_text("name: p\n")
    captured = {}

    def make_env(*a, **k):
        captured["kwargs"] = k
        inst = MagicMock()
        inst.setup.return_value = True
        inst.provision.return_value = True
        inst.attach_to_agent.return_value = 0
        return inst

    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.ProjectConfig") as pc_cls, patch(
        "cli.main.RemoteDevEnvironment", side_effect=make_env
    ), patch("cli.main.os.getcwd", return_value=str(tmp_path)):
        pc = MagicMock()
        pc.config = {"vm": {"region": "old", "type": "small"}}
        pc.load.return_value = True
        pc_cls.return_value = pc

        assert (
            main(
                [
                    "create",
                    "--name",
                    "c",
                    "--type",
                    "large",
                    "--availability-zone",
                    "fsn1",
                    "--image",
                    "debian-12",
                ]
            )
            == 0
        )
        host = captured["kwargs"]["config"]["host"]
        assert host["provider"] == "hetzner"
        assert host["type"] == "vm"
        assert host["server_type"] == "large"
        assert host["availability_zone"] == "fsn1"
        assert "region" not in host
        assert host["image"] == "debian-12"


def test_create_exception(tok_env, tmp_path, capsys):
    (tmp_path / "rig.yml").write_text("name: p\n")
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.ProjectConfig") as pc_cls, patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.os.getcwd", return_value=str(tmp_path)):
        pc = MagicMock()
        pc.config = {}
        pc.load.return_value = True
        pc_cls.return_value = pc
        env_cls.return_value.setup.side_effect = RuntimeError("bad")
        assert main(["create", "--name", "c"]) == 1
    assert "Error creating" in capsys.readouterr().err


def test_dev_uses_project_editor_when_omitted(tok_env, tmp_path):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.remember_last_env"), patch(
        "cli.remote_dev.configured_editors", return_value=(["codium"], "codium")
    ) as pref:
        inst = MagicMock()
        inst.resolve_resources.return_value = True
        inst.vm_ip = "10.0.0.1"
        inst.open_vscode.return_value = 0
        env_cls.return_value = inst

        assert main(["dev"]) == 0
        pref.assert_called_once()
        assert inst.open_vscode.call_args.kwargs["editor"] == "codium"


def test_dev_exception(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls:
        env_cls.return_value.resolve_resources.side_effect = RuntimeError("x")
        assert main(["dev"]) == 1
    assert "VS Code" in capsys.readouterr().err


def test_destroy_multiple_names_and_failure(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.clear_last_env"
    ), patch("cli.main.clear_last_session"):
        first = MagicMock()
        second = MagicMock()
        second.cleanup.side_effect = RuntimeError("nope")
        env_cls.side_effect = [first, second]

        assert main(["delete", "ok-env", "bad-env"]) == 1
    err = capsys.readouterr().err
    assert "Failed to delete" in err


def test_destroy_uses_last_env(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="last-only"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.clear_last_env"), patch(
        "cli.main.clear_last_session"
    ):
        inst = MagicMock()
        env_cls.return_value = inst
        assert main(["delete"]) == 0
        inst.cleanup.assert_called_once()


def test_destroy_no_name_and_no_last(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value=None):
        assert main(["delete"]) == 1
    assert "required" in capsys.readouterr().err.lower()


def test_attach_flag_like_name_moves_to_remainder(tok_env):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.remember_last_env"), patch(
        "cli.main.read_last_session", return_value=None
    ), patch("cli.main.os.path.exists", return_value=True), patch(
        "cli.main.ProjectConfig"
    ) as pc_cls:
        pc = MagicMock()
        pc.config = {"agent": {"type": "opencode"}}
        pc.get_vars.return_value = None
        pc_cls.return_value = pc
        inst = MagicMock()
        inst.resolve_resources.return_value = True
        inst.vm_ip = "10.0.0.1"
        inst.ssh_key_name = "k"
        inst.attach_to_agent.return_value = 0
        env_cls.return_value = inst

        assert main(["attach", "--continue", "--", "--prompt", "hi"]) == 0
        args = inst.attach_to_agent.call_args.kwargs["agent_args"]
        assert "--continue" in args


def test_attach_resolve_fails(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls:
        env_cls.return_value.resolve_resources.return_value = False
        assert main(["attach", "dev"]) == 1
    assert "Could not resolve" in capsys.readouterr().err


def test_attach_exception(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ), patch("cli.main.resolve_env_name", return_value="dev"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls:
        env_cls.return_value.resolve_resources.side_effect = RuntimeError("x")
        assert main(["attach"]) == 1
    assert "Error attaching" in capsys.readouterr().err


def test_cache_list_unused_only_branch(tok_env, tmp_path, capsys):
    """list with no targets but keep nonempty → 'No unused snapshots'."""
    (tmp_path / "rig.yml").write_text("name: app\n")
    (tmp_path / ".rig").mkdir(exist_ok=True)
    keep = [{"description": "current", "id": "1"}]
    with patch("cli.main._load_credentials"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.os.getcwd", return_value=str(tmp_path)), patch(
        "cli.main._cache_select_snapshots", return_value=(keep, [], "unused x")
    ):
        inst = MagicMock()
        inst.manager.list_snapshots.return_value = []
        env_cls.return_value = inst
        assert main(["cache", "list"]) == 0
    assert "No unused" in capsys.readouterr().err


def test_cache_clear_eof_confirms(tok_env, tmp_path):
    (tmp_path / "rig.yml").write_text("name: app\n")
    (tmp_path / ".rig").mkdir(exist_ok=True)
    targets = [{"id": 9, "description": "stale"}]
    with patch("cli.main._load_credentials"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.os.getcwd", return_value=str(tmp_path)), patch(
        "cli.main._cache_select_snapshots", return_value=([], targets, "unused")
    ), patch("builtins.input", side_effect=EOFError()):
        inst = MagicMock()
        inst.manager.list_snapshots.return_value = []
        env_cls.return_value = inst
        assert main(["cache", "clear"]) == 0


def test_cache_delete_partial_failure(tok_env, tmp_path, capsys):
    (tmp_path / "rig.yml").write_text("name: app\n")
    (tmp_path / ".rig").mkdir(exist_ok=True)
    targets = [{"id": 9, "description": "stale"}]
    with patch("cli.main._load_credentials"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.os.getcwd", return_value=str(tmp_path)), patch(
        "cli.main._cache_select_snapshots", return_value=([], targets, "unused")
    ):
        inst = MagicMock()
        inst.manager.list_snapshots.return_value = []
        inst.manager.delete_image.return_value = False
        env_cls.return_value = inst
        assert main(["cache", "clear", "-y"]) == 1
    assert "Failed to delete" in capsys.readouterr().err


def test_cache_list_snapshots_fails(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls:
        env_cls.return_value.manager.list_snapshots.side_effect = RuntimeError("x")
        assert main(["cache", "list"]) == 1
    assert "Failed to list" in capsys.readouterr().err


def test_cache_select_runtime_error(tok_env, capsys):
    with patch("cli.main._load_credentials"), patch(
        "cli.main.RemoteDevEnvironment"
    ) as env_cls, patch("cli.main.os.getcwd", return_value="/no-rig-here"):
        inst = MagicMock()
        inst.manager.list_snapshots.return_value = []
        env_cls.return_value = inst
        assert main(["cache", "list"]) == 1
    assert "No rig.yml" in capsys.readouterr().err


def test_vars_encrypt_decrypt_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with patch(
        "cli.main.interactive_encrypt", return_value="cipher"
    ) as enc, patch("builtins.print") as pr:
        assert main(["vars", "encrypt"]) == 0
        enc.assert_called_once()
        pr.assert_called()

    with patch(
        "cli.main.interactive_decrypt", return_value="secret\n"
    ) as dec, patch("builtins.print") as pr:
        assert main(["vars", "decrypt", "x"]) == 0
        dec.assert_called_once()
        pr.assert_called()


def test_vars_list_and_export_key(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with patch(
        "cli.encrypt.list_all_vault_vars", return_value=[("a", "shared")]
    ), patch("builtins.print") as pr:
        assert main(["vars", "list"]) == 0
        pr.assert_called()

    with patch(
        "cli.encrypt.export_shared_data_key", return_value="shared-key"
    ) as ex, patch("builtins.print") as pr:
        assert main(["vars", "export-shared-key"]) == 0
        ex.assert_called_once()
        pr.assert_called()
