"""Tests for rig_* Ansible variables injected into playbooks."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.remote_dev import (
    AnsibleManager,
    ProjectConfig,
    RemoteDevEnvironment,
    build_rig_ansible_vars,
)


def test_build_rig_ansible_vars_includes_core_fields(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="estasify", agent_type="opencode")
    cfg.set("vm.type", "cax21")
    cfg.set("vm.availability_zone", "hel1")
    cfg.save(write_vars=False)
    cfg.load()

    env = RemoteDevEnvironment(
        name="dev-abc123",
        config=cfg.config,
    )
    env.vm_ip = "203.0.113.10"
    env.vm_id = 42
    env.boot_source = "system"

    vars_out = build_rig_ansible_vars(env, cfg, action="create")
    assert vars_out["rig_env_name"] == "dev-abc123"
    assert vars_out["rig_workspace_name"] == "estasify"
    assert vars_out["rig_vm_type"] == "cax21"
    assert vars_out["rig_availability_zone"] == "hel1"
    assert vars_out["rig_vm_ip"] == "203.0.113.10"
    assert vars_out["rig_vm_id"] == "42"
    assert vars_out["rig_agent_type"] == "opencode"
    assert vars_out["rig_workspace_root"] == "/workspace"
    assert vars_out["rig_action"] == "create"
    assert vars_out["rig_boot_source"] == "system"
    assert vars_out["rig_project_dir"] == str(tmp_path)
    assert all(key.startswith("rig_") for key in vars_out)


def test_run_one_playbook_injects_rig_vars_as_json(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    (tmp_path / "inventory.yml").write_text("all: {}\n")

    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    manager.inventory_path = str(tmp_path / "inventory.yml")
    manager.rig_vars = {
        "rig_env_name": "dev-x",
        "rig_workspace_name": "ws",
        "rig_repos": [{"name": "app", "url": "https://example.com/app.git"}],
    }

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="PLAY RECAP", stderr="")

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        assert manager._run_one_playbook(
            str(tmp_path / ".rig" / "workspace.yml"),
            stream=False,
        )

    assert "cmd" in captured
    # Find the JSON -e payload after @vars.yml
    e_payloads = []
    cmd = captured["cmd"]
    for index, part in enumerate(cmd):
        if part == "-e" and index + 1 < len(cmd):
            e_payloads.append(cmd[index + 1])
    json_payloads = [p for p in e_payloads if p.startswith("{")]
    assert json_payloads
    loaded = json.loads(json_payloads[-1])
    assert loaded["rig_env_name"] == "dev-x"
    assert loaded["rig_workspace_name"] == "ws"
    assert loaded["rig_repos"][0]["name"] == "app"


def test_apply_playbooks_sets_rig_vars(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="dev-apply")
    env.vm_ip = "203.0.113.20"
    env.ssh_key_name = "dev-apply-key"
    with open("/tmp/dev-apply-key", "w", encoding="utf-8") as handle:
        handle.write("key\n")

    seen = {}

    def fake_run(self, playbook_path, extra_vars=None, vault_password=None, stream=False):
        seen["rig_vars"] = dict(self.rig_vars or {})
        return True

    try:
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run
        ):
            assert env.apply_playbooks(
                project_dir=str(tmp_path),
                playbooks=("workspace",),
                action="apply",
                stream=False,
            )
    finally:
        try:
            os.remove("/tmp/dev-apply-key")
        except OSError:
            pass

    assert seen["rig_vars"]["rig_env_name"] == "dev-apply"
    assert seen["rig_vars"]["rig_action"] == "apply"
    assert seen["rig_vars"]["rig_workspace_name"] == "ws"
