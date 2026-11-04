"""Tests for snapshot naming, hashing, and cache clear."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.main import main
from cli.remote_dev import RemoteDevEnvironment
from cli.snapshots import (
    filter_snapshots,
    resolve_boot_snapshot,
    system_snapshot_name,
    workspace_content_hash,
    workspace_snapshot_name,
)


def test_system_snapshot_name_uses_version():
    assert system_snapshot_name("1.2.3") == "rig-1.2.3"
    assert system_snapshot_name("1.2.3+dirty") == "rig-1.2.3-dirty"


def test_workspace_snapshot_name_and_hash(tmp_path):
    (tmp_path / "rig.yml").write_text("name: demo\n")
    rig = tmp_path / ".rig"
    rig.mkdir()
    (rig / "vars.yml").write_text("x: 1\n")
    (rig / "workspace.yml").write_text("---\n")
    digest = workspace_content_hash(str(tmp_path))
    assert len(digest) == 12
    assert workspace_snapshot_name("demo", digest) == f"workspace-demo-{digest}"

    # Changing content changes the hash.
    (rig / "vars.yml").write_text("x: 2\n")
    assert workspace_content_hash(str(tmp_path)) != digest


def test_workspace_hash_ignores_inventory_system_and_roles(tmp_path):
    (tmp_path / "rig.yml").write_text("name: demo\n")
    rig = tmp_path / ".rig"
    rig.mkdir()
    (rig / "vars.yml").write_text("x: 1\n")
    (rig / "workspace.yml").write_text("---\n")
    baseline = workspace_content_hash(str(tmp_path))

    # Ephemeral / generated paths must not change the digest.
    (rig / "inventory.yml").write_text("all:\n  hosts:\n    remote_vm:\n      ansible_host: 1.2.3.4\n")
    (rig / "system.yml").write_text("# generated\n")
    roles = rig / "roles" / "geerlingguy.docker"
    roles.mkdir(parents=True)
    (roles / "meta" ).mkdir(parents=True, exist_ok=True)
    (roles / "meta" / ".galaxy_install_info").write_text("version: 1.0.0\n")
    assert workspace_content_hash(str(tmp_path)) == baseline

    # Durable workspace inputs still affect the digest.
    (rig / "AGENTS.md").write_text("# rules\n")
    assert workspace_content_hash(str(tmp_path)) != baseline


def test_resolve_boot_snapshot_prefers_workspace_then_system():
    images = [
        {
            "id": 1,
            "type": "snapshot",
            "status": "available",
            "description": "rig-1.0.0",
            "labels": {"rig": "system"},
        },
        {
            "id": 2,
            "type": "snapshot",
            "status": "available",
            "description": "workspace-demo-abcd1234ef00",
            "labels": {"rig": "workspace", "rig_workspace": "demo"},
        },
    ]
    workspace = resolve_boot_snapshot(
        images,
        workspace_name="demo",
        content_hash="abcd1234ef00",
        version="1.0.0",
    )
    assert workspace["source"] == "workspace"
    assert workspace["skip_playbooks"] == ("system", "workspace")
    assert workspace["image"]["id"] == 2

    system_only = resolve_boot_snapshot(
        images,
        workspace_name="demo",
        content_hash="differenthash1",
        version="1.0.0",
    )
    assert system_only["source"] == "system"
    assert system_only["skip_playbooks"] == ("system",)
    assert system_only["image"]["id"] == 1

    base = resolve_boot_snapshot(
        images,
        workspace_name="demo",
        content_hash="differenthash1",
        version="9.9.9",
    )
    assert base["source"] == "base"
    assert base["skip_playbooks"] == ()
    assert base["image"] is None


def test_filter_snapshots_system_and_workspace():
    images = [
        {"id": 1, "type": "snapshot", "description": "rig-0.1.0", "labels": {"rig": "system"}},
        {
            "id": 2,
            "type": "snapshot",
            "description": "workspace-app-abc123",
            "labels": {"rig": "workspace", "rig_workspace": "app"},
        },
        {
            "id": 3,
            "type": "snapshot",
            "description": "workspace-other-zzz",
            "labels": {"rig": "workspace", "rig_workspace": "other"},
        },
        {"id": 4, "type": "system", "description": "ubuntu"},
    ]
    system = filter_snapshots(images, system=True)
    assert [img["id"] for img in system] == [1]
    workspace = filter_snapshots(images, workspace_name="app")
    assert [img["id"] for img in workspace] == [2]


def test_create_snapshot_skips_when_description_exists():
    mgr = RemoteDevEnvironment(name="dev-snap").manager
    mgr._request = MagicMock()
    mgr.list_snapshots = MagicMock(
        return_value=[{"id": 9, "description": "rig-1.0.0", "type": "snapshot"}]
    )
    image = mgr.create_snapshot("42", "rig-1.0.0", labels={"rig": "system"})
    assert image["id"] == 9
    mgr._request.assert_not_called()


def test_create_snapshot_posts_and_waits():
    mgr = RemoteDevEnvironment(name="dev-snap2").manager
    mgr.find_snapshot_by_description = MagicMock(return_value=None)
    mgr.wait_for_action = MagicMock(return_value=True)
    mgr._request = MagicMock(
        return_value={
            "image": {"id": 77, "description": "workspace-demo-deadbeef"},
            "action": {"id": 5, "status": "running"},
        }
    )
    image = mgr.create_snapshot(
        "11",
        "workspace-demo-deadbeef",
        labels={"rig": "workspace"},
        wait=True,
    )
    assert image["id"] == 77
    mgr._request.assert_called_once()
    kwargs = mgr._request.call_args
    assert kwargs[0][0] == "POST"
    assert "/servers/11/actions/create_image" in kwargs[0][1]
    mgr.wait_for_action.assert_called_once_with("5")


def test_snapshot_after_playbook_system_and_workspace(tmp_path):
    (tmp_path / "rig.yml").write_text("name: demo\nkind: workspace\n")
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "system.yml").write_text("---\n")
    (tmp_path / ".rig" / "workspace.yml").write_text("---\n")

    from cli.remote_dev import ProjectConfig

    project = ProjectConfig(str(tmp_path))
    assert project.load()
    env = RemoteDevEnvironment(name="dev-x")
    env.vm_id = 99
    env.manager.create_snapshot = MagicMock(return_value={"id": 1})

    assert env._snapshot_after_playbook("system", project) is True
    assert env.manager.create_snapshot.call_args[0][1].startswith("rig-")

    env.manager.create_snapshot.reset_mock()
    assert env._snapshot_after_playbook("workspace", project) is True
    desc = env.manager.create_snapshot.call_args[0][1]
    assert desc.startswith("workspace-demo-")


def test_cache_clear_workspace_and_system(tmp_path):
    (tmp_path / "rig.yml").write_text("name: app\n")
    (tmp_path / ".rig").mkdir()

    images = [
        {
            "id": 10,
            "type": "snapshot",
            "description": "workspace-app-aaa",
            "labels": {"rig": "workspace", "rig_workspace": "app"},
        },
        {
            "id": 11,
            "type": "snapshot",
            "description": "rig-0.9.0",
            "labels": {"rig": "system", "rig_version": "0.9.0"},
        },
    ]

    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.os.getcwd", return_value=str(tmp_path)
    ):
        instance = MagicMock()
        instance.manager.list_snapshots.return_value = images
        instance.manager.delete_image.return_value = True
        env_cls.return_value = instance

        assert main(["cache", "clear"]) == 0
        instance.manager.delete_image.assert_called_once_with("10")

        instance.manager.delete_image.reset_mock()
        assert main(["cache", "--system", "clear"]) == 0
        instance.manager.delete_image.assert_called_once_with("11")


def test_provision_skips_playbooks_from_boot_snapshot(tmp_path):
    project_dir = tmp_path / "ws"
    project_dir.mkdir()
    from cli.remote_dev import AnsibleManager, ProjectConfig

    cfg = ProjectConfig(str(project_dir))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="ws")
    env.vm_ip = "10.0.0.3"
    env.vm_id = 56
    env.ssh_key_name = "ws-skip-key"
    env.boot_source = "workspace"
    env.boot_snapshot_description = "workspace-ws-abc"
    env.skip_playbooks = ("system", "workspace")
    key = "/tmp/ws-skip-key"
    with open(key, "w", encoding="utf-8") as handle:
        handle.write("k\n")

    calls = []

    def fake_run(self, playbook_path, extra_vars=None, vault_password=None, stream=False):
        calls.append(playbook_path)
        return True

    try:
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run
        ):
            assert env.provision(project_dir=str(project_dir), stream=False) is True
            assert calls == []

            assert (
                env.provision(
                    project_dir=str(project_dir),
                    stream=False,
                    force_system=True,
                    force_workspace=True,
                )
                is True
            )
            assert len(calls) == 2
    finally:
        try:
            os.remove(key)
        except OSError:
            pass


def test_setup_uses_workspace_snapshot_image(tmp_path):
    (tmp_path / "rig.yml").write_text("name: demo\n")
    (tmp_path / ".rig").mkdir()
    digest = workspace_content_hash(str(tmp_path))
    desc = workspace_snapshot_name("demo", digest)

    env = RemoteDevEnvironment(name="dev-boot")
    env.manager.list_snapshots = MagicMock(
        return_value=[
            {
                "id": 4242,
                "type": "snapshot",
                "status": "available",
                "description": desc,
            }
        ]
    )
    decision = env.resolve_boot_image(project_dir=str(tmp_path))
    assert decision["source"] == "workspace"
    assert env.image == 4242
    assert env.skip_playbooks == ("system", "workspace")


def test_apply_playbooks_snapshots_after_success(tmp_path):
    project_dir = tmp_path / "ws"
    project_dir.mkdir()
    from cli.remote_dev import AnsibleManager, ProjectConfig

    cfg = ProjectConfig(str(project_dir))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="ws")
    env.vm_ip = "10.0.0.2"
    env.vm_id = 55
    env.ssh_key_name = "ws-key"
    key = "/tmp/ws-key"
    with open(key, "w", encoding="utf-8") as handle:
        handle.write("k\n")

    snaps = []

    def fake_snap(kind, project):
        snaps.append(kind)
        return True

    try:
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", return_value=True
        ), patch.object(RemoteDevEnvironment, "_snapshot_after_playbook", side_effect=fake_snap):
            assert env.apply_playbooks(
                project_dir=str(project_dir),
                stream=False,
                playbooks=("system", "workspace"),
            )
    finally:
        try:
            os.remove(key)
        except OSError:
            pass

    assert snaps == ["system", "workspace"]
