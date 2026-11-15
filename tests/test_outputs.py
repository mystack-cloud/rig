"""Tests for Environment Outputs: masking, merging, storage and capture."""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli import outputs as out  # noqa: E402


def test_is_sensitive_key_patterns():
    for key in ("password", "DB_PASSWORD", "api_token", "client_secret", "ssh_key_path"):
        assert out.is_sensitive_key(key), key
    for key in ("url", "port", "vscode_url"):
        assert not out.is_sensitive_key(key), key


def test_mask_by_pattern_and_explicit_list():
    data = {"url": "http://x", "password": "p", "custom": "c", "nested": {"token": "t", "ok": 1}}
    masked = out.mask_outputs(data, sensitive=["custom"])
    assert masked == {
        "url": "http://x",
        "password": out.MASK,
        "custom": out.MASK,
        "nested": {"token": out.MASK, "ok": 1},
    }
    # input is never mutated
    assert data["password"] == "p"


def test_mask_show_secrets_returns_values():
    data = {"password": "p"}
    assert out.mask_outputs(data, sensitive=[], show_secrets=True) == {"password": "p"}


def test_merge_shallow_later_wins_and_unions_sensitive():
    stored = {"rig_outputs": {"a": 1, "b": 2}, "rig_outputs_sensitive": ["a"]}
    new = {"rig_outputs": {"b": 3, "c": 4}, "rig_outputs_sensitive": ["c", "a"]}
    merged = out.merge_outputs(stored, new)
    assert merged["rig_outputs"] == {"a": 1, "b": 3, "c": 4}
    assert merged["rig_outputs_sensitive"] == ["a", "c"]


def test_merge_into_nothing():
    merged = out.merge_outputs(None, {"rig_outputs": {"x": 1}})
    assert merged["rig_outputs"] == {"x": 1}
    assert merged["rig_outputs_sensitive"] == []


def test_parse_outputs_file(tmp_path):
    p = tmp_path / "o.yml"
    p.write_text(yaml.safe_dump({"rig_outputs": {"url": "u"}, "rig_outputs_sensitive": ["url"]}))
    assert out.read_outputs_file(str(p)) == {"rig_outputs": {"url": "u"}, "rig_outputs_sensitive": ["url"]}


@pytest.mark.parametrize("content", ["", "not: [valid", "- a\n- b\n", "other: 1\n"])
def test_parse_outputs_file_rejects_garbage(tmp_path, content):
    p = tmp_path / "o.yml"
    p.write_text(content)
    assert out.read_outputs_file(str(p)) is None


def test_parse_outputs_file_missing(tmp_path):
    assert out.read_outputs_file(str(tmp_path / "nope.yml")) is None


# ---- capture from ansible runs -------------------------------------------

from unittest.mock import MagicMock, patch  # noqa: E402

from cli.remote_dev import AnsibleManager, ProjectConfig, RemoteDevEnvironment  # noqa: E402


def _ansible(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    (tmp_path / "inventory.yml").write_text("all: {}\n")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    manager.inventory_path = str(tmp_path / "inventory.yml")
    return manager


def _extra_var(cmd, name):
    for i, part in enumerate(cmd):
        if part == "-e" and i + 1 < len(cmd) and cmd[i + 1].startswith(f"{name}="):
            return cmd[i + 1].split("=", 1)[1]
    return None


def test_run_one_playbook_passes_outputs_file_and_captures(tmp_path):
    manager = _ansible(tmp_path)
    seen = {}

    def fake_run(cmd, **kwargs):
        path = _extra_var(cmd, "rig_outputs_file")
        seen["path"] = path
        with open(path, "w") as f:
            yaml.safe_dump({"rig_outputs": {"url": "http://vm"}}, f)
        return MagicMock(returncode=0, stdout="PLAY RECAP", stderr="")

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        assert manager._run_one_playbook(str(tmp_path / ".rig" / "materialize.yml"), stream=False)
    assert manager.last_outputs == {"rig_outputs": {"url": "http://vm"}}
    assert not os.path.exists(seen["path"])  # temp file cleaned up


def test_run_one_playbook_no_outputs_file_content(tmp_path):
    manager = _ansible(tmp_path)
    manager.last_outputs = {"rig_outputs": {"stale": 1}}
    with patch(
        "cli.remote_dev.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="PLAY RECAP", stderr=""),
    ):
        assert manager._run_one_playbook(str(tmp_path / ".rig" / "materialize.yml"), stream=False)
    assert manager.last_outputs is None


def test_failed_playbook_captures_nothing(tmp_path):
    manager = _ansible(tmp_path)

    def fake_run(cmd, **kwargs):
        with open(_extra_var(cmd, "rig_outputs_file"), "w") as f:
            yaml.safe_dump({"rig_outputs": {"a": 1}}, f)
        return MagicMock(returncode=2, stdout="", stderr="boom")

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        assert not manager._run_one_playbook(str(tmp_path / ".rig" / "materialize.yml"), stream=False)
    assert manager.last_outputs is None


def _apply(tmp_path, kinds, captured_by_kind, run_ok=True):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    env = RemoteDevEnvironment(name="dev-cap")
    env.vm_ip = "203.0.113.20"
    env.ssh_key_name = "dev-cap-key"
    with open("/tmp/dev-cap-key", "w", encoding="utf-8") as handle:
        handle.write("key\n")
    env.manager = MagicMock()
    order = []

    def fake_run(self, playbook_path, extra_vars=None, vault_password=None, stream=False, **kw):
        kind = os.path.basename(playbook_path).split(".")[0]
        order.append(kind)
        self.last_outputs = captured_by_kind.get(kind)
        return run_ok

    try:
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run
        ):
            result = env.apply_playbooks(
                project_dir=str(tmp_path), playbooks=kinds, action="apply", stream=False
            )
    finally:
        try:
            os.remove("/tmp/dev-cap-key")
        except OSError:
            pass
    return env, result


def test_apply_playbooks_saves_captured_outputs(tmp_path):
    env, ok = _apply(
        tmp_path,
        ("authorize", "materialize"),
        {"materialize": {"rig_outputs": {"url": "u"}}},
    )
    assert ok
    env.manager.save_outputs.assert_called_once_with("dev-cap", {"rig_outputs": {"url": "u"}})


def test_apply_playbooks_skips_when_nothing_captured(tmp_path):
    env, ok = _apply(tmp_path, ("materialize",), {})
    assert ok
    env.manager.save_outputs.assert_not_called()


def test_apply_playbooks_does_not_capture_destroy(tmp_path):
    env, ok = _apply(tmp_path, ("destroy",), {"destroy": {"rig_outputs": {"x": 1}}})
    env.manager.save_outputs.assert_not_called()


def test_apply_playbooks_failed_run_saves_nothing(tmp_path):
    env, ok = _apply(
        tmp_path, ("materialize",), {"materialize": {"rig_outputs": {"x": 1}}}, run_ok=False
    )
    assert not ok
    env.manager.save_outputs.assert_not_called()


def test_run_one_playbook_tolerates_outputs_file_removed_by_playbook(tmp_path):
    manager = _ansible(tmp_path)

    def fake_run(cmd, **kwargs):
        os.unlink(_extra_var(cmd, "rig_outputs_file"))
        return MagicMock(returncode=0, stdout="PLAY RECAP", stderr="")

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        # a playbook that deletes the file must not crash rig
        try:
            manager._run_one_playbook(str(tmp_path / ".rig" / "materialize.yml"), stream=False)
        except FileNotFoundError:
            pytest.fail("missing outputs file should be treated as 'no outputs'")
    assert manager.last_outputs is None
