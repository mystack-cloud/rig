"""Unit tests for Ansible Galaxy requirements/roles install helpers."""

import os
import sys
from unittest.mock import MagicMock, patch

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.remote_dev import AnsibleManager, ProjectConfig


def test_setup_requirements_writes_collections_and_roles(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    req_path = os.path.join(cfg.rig_dir, "requirements.yml")
    os.remove(req_path)

    mgr = AnsibleManager(cfg)
    mgr.configure(str(tmp_path))
    assert mgr.setup_requirements() is True
    payload = yaml.safe_load(open(req_path, encoding="utf-8"))
    names = {item["name"] for item in payload.get("collections", [])}
    role_names = {item["name"] for item in payload.get("roles", [])}
    assert "community.general" in names
    assert "ansible.posix" in names
    assert "geerlingguy.docker" in role_names
    # Second call is a no-op when file exists.
    assert mgr.setup_requirements() is True


def test_setup_roles_creates_local_and_roles_dirs(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    mgr = AnsibleManager(cfg)
    mgr.configure(str(tmp_path))
    assert mgr.setup_roles() is True
    assert (tmp_path / ".rig" / "local_roles" / ".gitkeep").is_file()
    assert (tmp_path / ".rig" / "roles").is_dir()
    assert not (tmp_path / ".rig" / "galaxy_roles").exists()
    assert not (tmp_path / ".rig" / "roles" / "default_role").exists()


def test_galaxy_roles_move_into_roles_and_local_roles_come_first(tmp_path):
    from cli.remote_dev import migrate_galaxy_roles_dir, roles_search_path

    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    legacy = tmp_path / ".rig" / "galaxy_roles" / "geerlingguy.docker" / "meta"
    legacy.mkdir(parents=True)
    (legacy / ".galaxy_install_info").write_text("version: 1\n")
    handmade = tmp_path / ".rig" / "roles" / "kept"
    handmade.mkdir(parents=True)
    (handmade / "tasks").mkdir()

    moved = migrate_galaxy_roles_dir(cfg.rig_dir)
    assert moved == ["geerlingguy.docker"]
    assert (tmp_path / ".rig" / "roles" / "geerlingguy.docker" / "meta" / ".galaxy_install_info").is_file()
    assert handmade.is_dir()
    assert migrate_galaxy_roles_dir(cfg.rig_dir) == []
    assert migrate_galaxy_roles_dir(str(tmp_path / "missing")) == []

    # Committed roles are found through the roles path; rig no longer symlinks them.
    local = tmp_path / ".rig" / "local_roles" / "app" / "tasks"
    local.mkdir(parents=True)
    (local / "main.yml").write_text("- ping:\n")
    path = roles_search_path(cfg.rig_dir).split(os.pathsep)
    assert path[0].endswith(os.path.join(".rig", "local_roles"))
    assert path[1].endswith(os.path.join(".rig", "roles"))
    assert len(path) == 2
    assert not (tmp_path / ".rig" / "roles" / "app").exists()


def test_rig_does_not_link_local_roles_into_roles():
    import cli.remote_dev as rd

    for name in ("link_local_roles", "unlink_local_role_shadows"):
        assert not hasattr(rd, name)


def test_install_roles_invokes_ansible_galaxy(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    mgr = AnsibleManager(cfg)
    mgr.configure(str(tmp_path))

    app = tmp_path / ".rig" / "local_roles" / "app" / "tasks"
    app.mkdir(parents=True)
    (app / "main.yml").write_text("- ping:\n")
    ok = MagicMock(returncode=0, stdout="ok", stderr="")

    def fake_run(cmd, **kwargs):
        return ok

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run) as run:
        assert mgr.install_roles() is True
    assert not (tmp_path / ".rig" / "roles" / "app").exists()  # no symlink is created
    commands = [" ".join(call.args[0]) for call in run.call_args_list]
    assert any("ansible-galaxy collection install" in cmd for cmd in commands)
    assert any("ansible-galaxy role install" in cmd for cmd in commands)
    role_cmd = next(cmd for cmd in commands if "ansible-galaxy role install" in cmd)
    assert f"-p {tmp_path / '.rig' / 'roles'}" in role_cmd
    search = mgr.ansible_env["ANSIBLE_ROLES_PATH"].split(os.pathsep)
    assert search[0].endswith(os.path.join(".rig", "local_roles"))
    assert search[1].endswith(os.path.join(".rig", "roles"))
    assert (tmp_path / ".rig" / "collections").is_dir()
    assert (tmp_path / ".rig" / "roles").is_dir()
    assert not (tmp_path / ".rig" / "galaxy_roles").exists()


def test_setup_bootstrap_wires_components(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    mgr = AnsibleManager(cfg)
    with patch.object(mgr, "install_roles", return_value=True) as install:
        assert mgr.setup_bootstrap("10.0.0.1", "/tmp/key") is True
        install.assert_called_once()
    assert (tmp_path / ".rig" / "inventory.yml").is_file()
