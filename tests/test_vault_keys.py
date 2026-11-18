"""Tests for identity resolution and master-key data-key wraps."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cli.vault_keys as vault_keys
from cli.config import RigConfig
from cli.snapshots import iter_workspace_hash_paths
from cli.vault_keys import (
    IDENTITY_DATA_KEY_VAR,
    SHARED_DATA_KEY_VAR,
    bootstrap_identity_keys,
    personal_vars_path,
    read_data_key_file,
    read_wrap_fields,
    resolve_identity,
    sanitize_identity,
    unlock_data_keys,
    unwrap_with_master_key,
    wrap_with_master_key,
)


def test_sanitize_and_resolve_identity(tmp_path, monkeypatch):
    assert sanitize_identity("Alice") == "alice"
    assert sanitize_identity("Bob_1") == "bob_1"
    monkeypatch.setenv("RIG_IDENTITY", "from-env")
    assert resolve_identity() == "from-env"
    monkeypatch.delenv("RIG_IDENTITY")
    cfg = RigConfig(path=str(tmp_path / "cfg"))
    cfg.set_identity("cfg-user")
    assert resolve_identity(config=cfg) == "cfg-user"


def test_wrap_unwrap_roundtrip():
    blob = wrap_with_master_key("secret-data-key", "master-pass")
    assert blob.startswith("rig1$")
    assert unwrap_with_master_key(blob, "master-pass") == "secret-data-key"
    with pytest.raises(ValueError):
        unwrap_with_master_key(blob, "wrong")


def test_bootstrap_and_unlock(tmp_path, monkeypatch):
    monkeypatch.setenv("RIG_CONFIG", str(tmp_path / "cfg"))
    project = tmp_path / "ws"
    project.mkdir()
    (project / "rig.yml").write_text("kind: workspace\n")
    answers = iter(["tester"])
    secrets = iter(["master-one", "master-one"])
    ident, personal_path, shared_path = bootstrap_identity_keys(
        str(project),
        input_fn=lambda _p: next(answers),
        secret_fn=lambda _p: next(secrets),
        config=RigConfig(path=str(tmp_path / "cfg")),
    )
    assert ident == "tester"
    assert read_data_key_file(personal_path)
    assert read_data_key_file(shared_path)
    id_file = personal_vars_path(str(project), "tester")
    wraps = read_wrap_fields(id_file)
    assert IDENTITY_DATA_KEY_VAR in wraps
    assert SHARED_DATA_KEY_VAR in wraps
    identity_before = wraps[IDENTITY_DATA_KEY_VAR]
    shared_before = wraps[SHARED_DATA_KEY_VAR]

    # Clear tmp and unlock again — wraps must stay the same.
    for path in (
        vault_keys.PERSONAL_DATA_KEY_PATH,
        vault_keys.SHARED_DATA_KEY_PATH,
        vault_keys.DEFAULT_DATA_KEY_PATH,
    ):
        if os.path.exists(path):
            os.unlink(path)
    unlock_data_keys(
        str(project),
        identity="tester",
        master_key="master-one",
    )
    assert read_data_key_file(vault_keys.PERSONAL_DATA_KEY_PATH)
    assert read_data_key_file(vault_keys.SHARED_DATA_KEY_PATH)
    wraps_after = read_wrap_fields(id_file)
    assert wraps_after[IDENTITY_DATA_KEY_VAR] == identity_before
    assert wraps_after[SHARED_DATA_KEY_VAR] == shared_before


def test_write_filtered_vars_copy_skips_wraps_only(tmp_path):
    from cli.vault_keys import write_filtered_vars_copy

    src = tmp_path / "vars.mlewk.yml"
    src.write_text('rig_identity_data_key: "rig1$aaa$bbb$ccc"\nrig_shared_data_key: "rig1$ddd$eee$fff"\n')
    assert write_filtered_vars_copy(str(src), str(tmp_path / "out.yml")) is None

    src.write_text(
        'rig_identity_data_key: "rig1$aaa$bbb$ccc"\n'
        "github_pat: !vault |\n"
        "          $ANSIBLE_VAULT;1.2;AES256;personal\n"
        "          deadbeef\n"
    )
    out = write_filtered_vars_copy(str(src), str(tmp_path / "out2.yml"))
    assert out is not None
    text = open(out, encoding="utf-8").read()
    assert "github_pat" in text
    assert "rig_identity_data_key" not in text


def test_unlock_skips_prompt_when_tmp_keys_present(tmp_path, monkeypatch):
    monkeypatch.setenv("RIG_CONFIG", str(tmp_path / "cfg"))
    project = tmp_path / "ws"
    project.mkdir()
    (project / "rig.yml").write_text("kind: workspace\n")
    answers = iter(["tester"])
    secrets = iter(["master-one", "master-one"])
    bootstrap_identity_keys(
        str(project),
        input_fn=lambda _p: next(answers),
        secret_fn=lambda _p: next(secrets),
        config=RigConfig(path=str(tmp_path / "cfg")),
    )

    def boom(_prompt):
        raise AssertionError("master key should not be prompted when tmp keys exist")

    unlock_data_keys(str(project), identity="tester", secret_fn=boom)


def test_snapshot_hash_skips_identity_vars(tmp_path):
    project = tmp_path / "ws"
    rig = project / ".rig"
    rig.mkdir(parents=True)
    (project / "rig.yml").write_text("kind: workspace\n")
    (rig / "vars.yml").write_text("x: 1\n")
    (rig / "vars.alice.yml").write_text('rig_identity_data_key: "rig1$x"\n')
    (rig / "materialize.yml").write_text("---\n")
    paths = [os.path.basename(p) for p in iter_workspace_hash_paths(str(project))]
    assert "vars.yml" in paths
    assert "vars.alice.yml" not in paths
    assert "materialize.yml" in paths


def test_write_filtered_vars_copy_drops_connection_vars(tmp_path):
    """ansible_connection etc. as extra vars would force ssh on delegate_to: localhost."""
    from cli.vault_keys import write_filtered_vars_copy

    src = tmp_path / "vars.yml"
    src.write_text(
        "ansible_user: root\n"
        "ansible_connection: ssh\n"
        "ansible_python_interpreter: /usr/bin/python3\n"
        "code_server_port: 8080\n"
    )
    out = write_filtered_vars_copy(str(src), str(tmp_path / "out.yml"))
    text = open(out, encoding="utf-8").read()
    assert "ansible_" not in text
    assert "code_server_port: 8080" in text

    only = tmp_path / "only.yml"
    only.write_text("ansible_connection: ssh\n")
    assert write_filtered_vars_copy(str(only), str(tmp_path / "o2.yml")) is None
