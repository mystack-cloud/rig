"""Tests for snapshot naming, hashing, and cache clear."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.main import main
from cli.remote_dev import RemoteDevEnvironment
from cli.remote_dev import ProjectConfig
from cli.snapshots import (
    filter_snapshots,
    find_unused_snapshots,
    resolve_boot_snapshot,
    system_content_hash,
    system_hash_inputs_from_project,
    system_snapshot_name,
    workspace_content_hash,
    workspace_snapshot_name,
)


def test_system_snapshot_name_uses_version_and_hash():
    digest = system_content_hash(
        var_map={"coding_agent_type": "opencode"},
        playbook_body="playbook-a",
    )
    assert system_snapshot_name("1.2.3", content_hash=digest) == f"rig-1.2.3-{digest}"
    assert system_snapshot_name("1.2.3+dirty", content_hash="abcd1234ef00") == (
        "rig-1.2.3-dirty-abcd1234ef00"
    )


def test_system_content_hash_changes_when_agent_set_changes():
    one = system_content_hash(
        var_map={
            "coding_agent_type": "opencode",
            "coding_agent_types": ["opencode"],
        },
        playbook_body="playbook-a",
    )
    several = system_content_hash(
        var_map={
            "coding_agent_type": "opencode",
            "coding_agent_types": ["claude", "opencode"],
        },
        playbook_body="playbook-a",
    )
    assert one != several


def test_system_hash_inputs_label_joins_multiple_agents(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(
        name="ws",
        agent_type="claude",
        agent_types=["opencode", "claude"],
    )
    inputs = system_hash_inputs_from_project(cfg)
    assert inputs["agent_type"] == "claude+opencode"


def test_system_content_hash_uses_var_map_not_editor():
    base = system_content_hash(
        var_map={
            "coding_agent_type": "opencode",
            "git_providers": ["github"],
        },
        playbook_body="playbook-a",
    )
    assert len(base) == 12
    assert system_content_hash(
        var_map={
            "coding_agent_type": "claude",
            "git_providers": ["github"],
        },
        playbook_body="playbook-a",
    ) != base
    # Editor is not part of the system map — ignored even if passed legacy-style.
    assert system_content_hash(
        var_map={
            "coding_agent_type": "opencode",
            "git_providers": ["github"],
        },
        playbook_body="playbook-a",
        editor="cursor",
    ) == base
    assert system_content_hash(
        var_map={
            "coding_agent_type": "opencode",
            "git_providers": ["github", "gitlab"],
        },
        playbook_body="playbook-a",
    ) != base
    # Sanity: playbook body changes the hash.
    assert system_content_hash(
        var_map={"coding_agent_type": "opencode"},
        playbook_body="playbook-a",
    ) != system_content_hash(
        var_map={"coding_agent_type": "opencode"},
        playbook_body="playbook-b",
    )


def test_workspace_snapshot_name_and_hash(tmp_path):
    (tmp_path / "rig.yml").write_text("name: demo\n")
    rig = tmp_path / ".rig"
    rig.mkdir()
    (rig / "vars.yml").write_text("x: 1\n")
    (rig / "materialize.yml").write_text("---\n")
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
    (rig / "materialize.yml").write_text("---\n")
    baseline = workspace_content_hash(str(tmp_path))

    # Ephemeral / generated paths must not change the digest.
    (rig / "inventory.yml").write_text("all:\n  hosts:\n    remote_vm:\n      ansible_host: 1.2.3.4\n")
    (rig / "provision.yml").write_text("# generated\n")
    roles = rig / "roles" / "geerlingguy.docker"
    roles.mkdir(parents=True)
    (roles / "meta").mkdir(parents=True, exist_ok=True)
    (roles / "meta" / ".galaxy_install_info").write_text("version: 1.0.0\n")
    assert workspace_content_hash(str(tmp_path)) == baseline

    system_before = system_content_hash(project_dir=str(tmp_path), playbook_body="prov")
    local = rig / "local_roles" / "app"
    local.mkdir(parents=True)
    (local / ".gitkeep").write_text("")
    assert system_content_hash(project_dir=str(tmp_path), playbook_body="prov") == system_before
    (local / "tasks.yml").write_text("- debug:\n")
    assert system_content_hash(project_dir=str(tmp_path), playbook_body="prov") != system_before

    # Agent scaffolds / authnz / AGENTS.md do not affect workspace hash.
    (rig / "AGENTS.md").write_text("# rules\n")
    (rig / "authorize.yml").write_text("---\n")
    assert workspace_content_hash(str(tmp_path)) == baseline

    # materialize.yml changes still affect the digest.
    (rig / "materialize.yml").write_text("---\n- name: changed\n")
    assert workspace_content_hash(str(tmp_path)) != baseline


def test_resolve_boot_snapshot_prefers_workspace_then_system():
    open_hash = system_content_hash(
        var_map={"coding_agent_type": "opencode"},
        playbook_body="x",
    )
    claude_hash = system_content_hash(
        var_map={"coding_agent_type": "claude"},
        playbook_body="x",
    )
    images = [
        {
            "id": 1,
            "type": "snapshot",
            "status": "available",
            "description": f"rig-1.0.0-{open_hash}",
            "labels": {
                "rig": "system",
                "rig_version": "1.0.0",
                "rig_hash": open_hash,
            },
        },
        {
            "id": 2,
            "type": "snapshot",
            "status": "available",
            "description": "workspace-demo-abcd1234ef00",
            "labels": {"rig": "workspace", "rig_workspace": "demo"},
        },
        {
            "id": 3,
            "type": "snapshot",
            "status": "available",
            "description": f"rig-1.0.0-{claude_hash}",
            "labels": {
                "rig": "system",
                "rig_version": "1.0.0",
                "rig_hash": claude_hash,
            },
        },
    ]
    # Legacy workspace images are ignored; matching system hash wins.
    system_only = resolve_boot_snapshot(
        images,
        workspace_name="demo",
        content_hash="abcd1234ef00",
        version="1.0.0",
        system_hash=open_hash,
    )
    assert system_only["source"] == "system"
    assert system_only["skip_playbooks"] == ("provision", "configure")
    assert system_only["image"]["id"] == 1

    claude_system = resolve_boot_snapshot(
        images,
        workspace_name="demo",
        content_hash="differenthash1",
        version="1.0.0",
        system_hash=claude_hash,
    )
    assert claude_system["source"] == "system"
    assert claude_system["image"]["id"] == 3

    base = resolve_boot_snapshot(
        images,
        workspace_name="demo",
        content_hash="differenthash1",
        version="9.9.9",
        system_hash="deadbeefdead",
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
        {"id": 4, "type": "provision", "description": "ubuntu"},
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


def test_snapshot_after_playbook_only_after_configure(tmp_path):
    (tmp_path / "rig.yml").write_text("name: demo\nkind: workspace\nagent:\n  type: opencode\n")
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "provision.yml").write_text("---\n")
    (tmp_path / ".rig" / "configure.yml").write_text("---\n")
    (tmp_path / ".rig" / "materialize.yml").write_text("---\n")

    from cli.remote_dev import ProjectConfig

    project = ProjectConfig(str(tmp_path))
    assert project.load()
    env = RemoteDevEnvironment(name="dev-x")
    env.vm_id = 99
    env.manager.create_snapshot = MagicMock(return_value={"id": 1})

    # provision alone does not snapshot.
    assert env._snapshot_after_playbook("provision", project) is True
    env.manager.create_snapshot.assert_not_called()

    assert env._snapshot_after_playbook("configure", project) is True
    system_desc = env.manager.create_snapshot.call_args[0][1]
    assert system_desc.startswith("rig-")
    labels = env.manager.create_snapshot.call_args.kwargs.get("labels") or {}
    assert labels.get("rig") == "system"
    assert labels.get("rig_hash")
    assert system_desc.endswith(f"-{labels['rig_hash']}")
    assert labels.get("rig_agent") == "opencode"

    env.manager.create_snapshot.reset_mock()
    # materialize never snapshots in the new model.
    assert env._snapshot_after_playbook("materialize", project) is True
    env.manager.create_snapshot.assert_not_called()


def test_find_unused_snapshots_keeps_current_deletes_stale():
    images = [
        {
            "id": 1,
            "type": "snapshot",
            "description": "rig-1.0.0-aaaa1111bbbb",
            "labels": {
                "rig": "system",
                "rig_version": "1.0.0",
                "rig_hash": "aaaa1111bbbb",
            },
        },
        {
            "id": 2,
            "type": "snapshot",
            "description": "rig-0.9.0-oldoldold001",
            "labels": {
                "rig": "system",
                "rig_version": "0.9.0",
                "rig_hash": "oldoldold001",
            },
        },
        {
            "id": 3,
            "type": "snapshot",
            "description": "rig-1.0.0-cccc2222dddd",
            "labels": {
                "rig": "system",
                "rig_version": "1.0.0",
                "rig_hash": "cccc2222dddd",
            },
        },
        {
            "id": 4,
            "type": "snapshot",
            "description": "workspace-app-currhash0001",
            "labels": {
                "rig": "workspace",
                "rig_workspace": "app",
                "rig_hash": "currhash0001",
            },
        },
        {
            "id": 5,
            "type": "snapshot",
            "description": "workspace-app-stalehash002",
            "labels": {
                "rig": "workspace",
                "rig_workspace": "app",
                "rig_hash": "stalehash002",
            },
        },
        {
            "id": 6,
            "type": "snapshot",
            "description": "workspace-other-zzzzzzzzzzzz",
            "labels": {
                "rig": "workspace",
                "rig_workspace": "other",
                "rig_hash": "zzzzzzzzzzzz",
            },
        },
    ]
    split = find_unused_snapshots(
        images,
        current_version="1.0.0",
        current_system_hash="aaaa1111bbbb",
        current_workspace_name="app",
        current_workspace_hash="currhash0001",
    )
    keep_ids = {m["id"] for m in split["keep"]}
    unused_ids = {m["id"] for m in split["unused"]}
    assert keep_ids == {1, 4}
    assert unused_ids == {2, 3, 5}
    # Other workspaces are not touched.
    assert 6 not in unused_ids and 6 not in keep_ids


def test_cache_clear_lists_and_confirms(tmp_path):
    (tmp_path / "rig.yml").write_text(
        "name: app\nagent:\n  type: opencode\neditor: code\n"
    )
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "vars.yml").write_text("x: 1\n")

    ws_hash = workspace_content_hash(str(tmp_path))
    sys_hash = system_content_hash(
        var_map={"coding_agent_type": "opencode", "git_providers": []},
        playbook_body="x",
    )
    images = [
        {
            "id": 10,
            "type": "snapshot",
            "description": f"workspace-app-{ws_hash}",
            "labels": {
                "rig": "workspace",
                "rig_workspace": "app",
                "rig_hash": ws_hash,
            },
        },
        {
            "id": 11,
            "type": "snapshot",
            "description": "workspace-app-oldhash00001",
            "labels": {
                "rig": "workspace",
                "rig_workspace": "app",
                "rig_hash": "oldhash00001",
            },
        },
        {
            "id": 12,
            "type": "snapshot",
            "description": "rig-0.1.0-legacyhash01",
            "labels": {
                "rig": "system",
                "rig_version": "0.1.0",
                "rig_hash": "legacyhash01",
            },
        },
    ]

    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.os.getcwd", return_value=str(tmp_path)
    ), patch(
        "cli.snapshots.package_version", return_value="9.9.9"
    ), patch("builtins.input", return_value="n"):
        instance = MagicMock()
        instance.manager.list_snapshots.return_value = images
        instance.manager.delete_image.return_value = True
        env_cls.return_value = instance

        assert main(["cache", "clear"]) == 0
        instance.manager.delete_image.assert_not_called()

    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.os.getcwd", return_value=str(tmp_path)
    ), patch("cli.snapshots.package_version", return_value="9.9.9"):
        instance = MagicMock()
        instance.manager.list_snapshots.return_value = images
        instance.manager.delete_image.return_value = True
        env_cls.return_value = instance

        assert main(["cache", "clear", "-y"]) == 0
        deleted = sorted(
            call.args[0] for call in instance.manager.delete_image.call_args_list
        )
        assert deleted == ["11", "12"]


def test_cache_clear_all_and_scoped(tmp_path):
    (tmp_path / "rig.yml").write_text("name: app\n")
    (tmp_path / ".rig").mkdir()

    images = [
        {
            "id": 1,
            "type": "snapshot",
            "description": "workspace-app-currhash0001",
            "labels": {
                "rig": "workspace",
                "rig_workspace": "app",
                "rig_hash": "currhash0001",
            },
        },
        {
            "id": 2,
            "type": "snapshot",
            "description": "rig-1.0.0-aaaa1111bbbb",
            "labels": {
                "rig": "system",
                "rig_version": "1.0.0",
                "rig_hash": "aaaa1111bbbb",
            },
        },
        {
            "id": 3,
            "type": "snapshot",
            "description": "ubuntu-base",
            "labels": {},
        },
        {
            "id": 4,
            "type": "snapshot",
            "description": "workspace-other-zzzzzzzzzzzz",
            "labels": {
                "rig": "workspace",
                "rig_workspace": "other",
                "rig_hash": "zzzzzzzzzzzz",
            },
        },
    ]

    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "builtins.input", return_value="n"
    ):
        instance = MagicMock()
        instance.manager.list_snapshots.return_value = images
        instance.manager.delete_image.return_value = True
        env_cls.return_value = instance

        assert main(["cache", "clear", "--all"]) == 0
        instance.manager.delete_image.assert_not_called()

    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.RemoteDevEnvironment") as env_cls:
        instance = MagicMock()
        instance.manager.list_snapshots.return_value = images
        instance.manager.delete_image.return_value = True
        env_cls.return_value = instance

        assert main(["cache", "clear", "--all", "-y"]) == 0
        deleted = sorted(
            call.args[0] for call in instance.manager.delete_image.call_args_list
        )
        assert deleted == ["1", "2", "4"]

    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.os.getcwd", return_value=str(tmp_path)
    ):
        instance = MagicMock()
        instance.manager.list_snapshots.return_value = images
        instance.manager.delete_image.return_value = True
        env_cls.return_value = instance

        assert main(["cache", "clear", "--all", "--workspace", "-y"]) == 0
        deleted = sorted(
            call.args[0] for call in instance.manager.delete_image.call_args_list
        )
        assert deleted == ["1"]

    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.RemoteDevEnvironment") as env_cls:
        instance = MagicMock()
        instance.manager.list_snapshots.return_value = images
        instance.manager.delete_image.return_value = True
        env_cls.return_value = instance

        assert main(["cache", "list", "--system", "--all"]) == 0
        instance.manager.delete_image.assert_not_called()


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
    env.boot_source = "system"
    env.boot_snapshot_description = "rig-1.0.0-abc"
    env.skip_playbooks = ("provision", "configure")
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
            # authorize + materialize always run when provision/configure skipped.
            assert len(calls) == 2
            assert calls[0].endswith("authorize.yml")
            assert calls[1].endswith("materialize.yml")

            calls.clear()
            assert (
                env.provision(
                    project_dir=str(project_dir),
                    stream=False,
                    force_system=True,
                    force_workspace=True,
                )
                is True
            )
            assert len(calls) == 4
            assert calls[0].endswith("provision.yml")
            assert calls[1].endswith("configure.yml")
            assert calls[2].endswith("authorize.yml")
            assert calls[3].endswith("materialize.yml")
    finally:
        try:
            os.remove(key)
        except OSError:
            pass


def test_setup_uses_system_snapshot_image(tmp_path):
    from cli.remote_dev import ProjectConfig
    from cli.snapshots import (
        package_version,
        system_content_hash,
        system_hash_inputs_from_project,
        system_snapshot_name,
    )

    project = ProjectConfig(str(tmp_path))
    assert project.init(name="demo", agent_type="opencode")
    inputs = system_hash_inputs_from_project(project)
    digest = system_content_hash(
        project=project,
        var_map=inputs.get("var_map"),
        project_dir=str(tmp_path),
    )
    desc = system_snapshot_name(package_version(), content_hash=digest)

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
    assert decision["source"] == "system"
    assert env.image == 4242
    assert env.skip_playbooks == ("provision", "configure")


def test_apply_playbooks_snapshots_after_configure(tmp_path):
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
                playbooks=("provision", "configure", "authorize", "materialize"),
            )
    finally:
        try:
            os.remove(key)
        except OSError:
            pass

    assert snaps == ["configure"]


def test_system_hash_ignores_bytecode_and_survives_binary_files(tmp_path):
    """A stray __pycache__/*.pyc under local_roles must not crash or change the hash."""
    files = tmp_path / ".rig" / "local_roles" / "awx_mcp" / "files"
    files.mkdir(parents=True)
    (files / "proxy.py").write_text("print('hi')\n")
    before = system_content_hash(project_dir=str(tmp_path), playbook_body="prov")

    cache = files / "__pycache__"
    cache.mkdir()
    (cache / "proxy.cpython-312.pyc").write_bytes(b"\xcb\x0d\x0d\x0a\x00\xff")
    (files / "stray.pyc").write_bytes(b"\xcb\xff")
    assert system_content_hash(project_dir=str(tmp_path), playbook_body="prov") == before

    # Any other non-UTF-8 file is tolerated instead of raising UnicodeDecodeError.
    (files / "blob.bin").write_bytes(b"\xcb\xff\xfe")
    system_content_hash(project_dir=str(tmp_path), playbook_body="prov")
