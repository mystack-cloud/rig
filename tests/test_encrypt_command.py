"""Tests for rig vars encrypt/decrypt and temporary Ansible vault data-key handling."""

import io
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.encrypt import (
    VSCODE_BROWSER_PASSWORD_VAR,
    collect_repo_pats,
    decrypt_var,
    decrypt_vault_blob,
    encrypt_named_value,
    encrypt_stdin,
    ensure_data_key,
    ensure_vscode_browser_password,
    interactive_decrypt,
    interactive_encrypt,
    read_data_key,
    read_single_line_value,
    read_variable_name,
    read_vscode_browser_password,
    resolve_repo_pat_var_names,
    to_https_git_url,
    upsert_vault_var,
    validate_var_name,
    vars_path_for_project,
)
from cli.main import main


def test_ensure_data_key_creates_file(tmp_path):
    path = tmp_path / ".rig" / ".data-key"
    secrets = iter(["super-secret", "super-secret"])
    key = ensure_data_key(
        path=str(path),
        secret_fn=lambda _prompt: next(secrets),
    )
    assert key == "super-secret"
    assert path.read_text() == "super-secret\n"
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)
    assert read_data_key(str(path)) == "super-secret"


def test_ensure_data_key_rejects_mismatch(tmp_path):
    path = tmp_path / ".data-key"
    secrets = iter(["one", "two"])
    with pytest.raises(ValueError, match="do not match"):
        ensure_data_key(path=str(path), secret_fn=lambda _prompt: next(secrets))
    assert not path.exists()


def test_validate_var_name():
    assert validate_var_name("db_password") == "db_password"
    with pytest.raises(ValueError):
        validate_var_name("bad-name")


def test_read_variable_and_single_line():
    assert read_variable_name(input_fn=lambda _prompt: "api_token") == "api_token"
    assert read_single_line_value(input_fn=lambda _prompt: "only-one-line") == "only-one-line"


def test_upsert_vault_var_replaces_existing(tmp_path):
    vars_path = tmp_path / ".rig" / "vars.yml"
    upsert_vault_var(
        str(vars_path),
        "api_token",
        "api_token: !vault |\n          $ANSIBLE_VAULT;1.1;AES256\n          old\n",
    )
    upsert_vault_var(
        str(vars_path),
        "keep_me",
        "keep_me: plain\n",
    )
    upsert_vault_var(
        str(vars_path),
        "api_token",
        "api_token: !vault |\n          $ANSIBLE_VAULT;1.1;AES256\n          new\n",
    )
    text = vars_path.read_text()
    assert "keep_me: plain" in text
    assert "new" in text
    assert "old" not in text
    assert text.count("api_token:") == 1


def test_encrypt_named_value_uses_stdin_name(tmp_path):
    path = tmp_path / ".data-key"
    path.write_text("vault-pass\n")

    with patch("cli.encrypt.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = (
            "db_password: !vault |\n"
            "          $ANSIBLE_VAULT;1.1;AES256\n"
            "          deadbeef\n"
        )
        run.return_value.stderr = ""
        out = encrypt_named_value(
            "secret-value",
            name="db_password",
            vault_password_file=str(path),
        )

    assert out.startswith("db_password: !vault |")
    cmd = run.call_args.args[0]
    assert cmd[:2] == ["ansible-vault", "encrypt_string"]
    assert "--stdin-name" in cmd
    assert "db_password" in cmd
    assert run.call_args.kwargs["input"] == "secret-value"


def test_encrypt_stdin_reads_until_eof(tmp_path):
    path = tmp_path / ".data-key"
    path.write_text("vault-pass\n")
    stdin = io.StringIO("line 1\nline 2\n")

    with patch("cli.encrypt.encrypt_named_value", return_value="db_password: !vault |\n  x") as enc:
        out = encrypt_stdin(
            vault_password_file=str(path),
            stdin_name="db_password",
            stdin=stdin,
        )

    assert out.startswith("db_password:")
    enc.assert_called_once_with("line 1\nline 2\n", "db_password", str(path))


def test_interactive_encrypt_creates_key_and_continues(tmp_path):
    key_path = tmp_path / ".rig-tmp" / ".data-key"
    project = tmp_path / "project"
    project.mkdir()
    answers = iter(["data-key", "data-key", "api_token", "super-secret"])

    with patch(
        "cli.encrypt.encrypt_named_value",
        return_value="api_token: !vault |\n          $ANSIBLE_VAULT;1.1;AES256\n          aa\n",
    ) as enc:
        out = interactive_encrypt(
            path=str(key_path),
            project_dir=str(project),
            input_fn=lambda _prompt: next(answers),
            secret_fn=lambda _prompt: next(answers),
        )

    assert key_path.exists()
    assert "$ANSIBLE_VAULT" in out
    enc.assert_called_once_with(
        "super-secret",
        name="api_token",
        vault_password_file=str(key_path),
    )
    vars_file = project / ".rig" / "vars.yml"
    assert vars_file.is_file()
    assert "api_token: !vault |" in vars_file.read_text()


def test_interactive_encrypt_stdin_name_writes_vars(tmp_path):
    key_path = tmp_path / ".data-key"
    key_path.write_text("data-key\n")
    project = tmp_path / "project"
    project.mkdir()
    stdin = io.StringIO("multi\nline\n")

    with patch(
        "cli.encrypt.encrypt_named_value",
        return_value="api_token: !vault |\n          $ANSIBLE_VAULT;1.1;AES256\n          bb\n",
    ):
        interactive_encrypt(
            path=str(key_path),
            project_dir=str(project),
            stdin_name="api_token",
            stdin=stdin,
        )

    text = (project / ".rig" / "vars.yml").read_text()
    assert "api_token: !vault |" in text


def test_vars_path_for_project(tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    assert vars_path_for_project(str(project)) == str(project / ".rig" / "vars.yml")


def test_encrypt_named_value_roundtrip_with_real_ansible_vault(tmp_path):
    if not any(
        os.path.exists(os.path.join(part, "ansible-vault"))
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if part
    ):
        pytest.skip("ansible-vault not installed")

    path = tmp_path / ".data-key"
    path.write_text("test-vault-password\n")
    ciphertext = encrypt_named_value(
        "hello-secret",
        name="greeting",
        vault_password_file=str(path),
    )
    assert ciphertext.startswith("greeting: !vault |")
    assert "$ANSIBLE_VAULT" in ciphertext


def test_decrypt_uses_default_data_key_without_flag(tmp_path):
    if not any(
        os.path.exists(os.path.join(part, "ansible-vault"))
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if part
    ):
        pytest.skip("ansible-vault not installed")

    key_path = tmp_path / ".data-key"
    key_path.write_text("test-vault-password\n")
    project = tmp_path / "project"
    project.mkdir()
    block = encrypt_named_value(
        "hello-secret",
        name="greeting",
        vault_password_file=str(key_path),
    )
    vars_path = project / ".rig" / "vars.yml"
    upsert_vault_var(str(vars_path), "greeting", block)

    assert decrypt_var(
        "greeting",
        vars_path=str(vars_path),
        vault_password_file=str(key_path),
    ) == "hello-secret"
    assert interactive_decrypt(
        path=str(key_path),
        name="greeting",
        project_dir=str(project),
    ) == "hello-secret"


def test_decrypt_requires_data_key(tmp_path):
    with pytest.raises(ValueError, match="Temporary data key not found"):
        interactive_decrypt(path=str(tmp_path / "missing-key"), name="x")


def test_help_lists_encrypt_and_stdin_name(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["vars", "encrypt", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--stdin-name" in help_text
    assert "Ctrl+D" in help_text


def test_help_lists_vars_and_decrypt(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "vars" in help_text
    with pytest.raises(SystemExit) as caught:
        main(["vars", "--help"])
    assert caught.value.code == 0
    assert "decrypt" in capsys.readouterr().out


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("git@github.com:org/api.git", "https://github.com/org/api.git"),
        ("git@gitlab.com:org/web", "https://gitlab.com/org/web.git"),
        ("ssh://git@github.com/org/api.git", "https://github.com/org/api.git"),
        ("https://github.com/org/api.git", "https://github.com/org/api.git"),
    ],
)
def test_to_https_git_url(remote, expected):
    assert to_https_git_url(remote) == expected


def test_collect_repo_pats_asks_for_ssh_remotes():
    repos = [
        {"name": "api", "url": "git@github.com:org/api.git"},
        {"name": "web", "url": "git@github.com:org/web.git"},
    ]
    secrets = iter(["ghp_ssh_pat", ""])
    answers = iter(["y"])
    assignments, defaults = collect_repo_pats(
        repos,
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: next(secrets),
    )
    assert assignments == {"api": "ghp_ssh_pat", "web": "ghp_ssh_pat"}
    assert defaults == {"github": "ghp_ssh_pat"}
    password_vars, to_store = resolve_repo_pat_var_names(
        assignments, repos, default_pats=defaults
    )
    assert password_vars == {"api": "github_pat", "web": "github_pat"}
    assert to_store == {"github_pat": "ghp_ssh_pat"}


def test_ensure_vscode_browser_password_generates_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "vars.yml").write_text("ansible_user: root\n")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    created = []

    def fake_store(name, plaintext, project_dir=None, data_key_path=None):
        created.append((name, plaintext))
        path = vars_path_for_project(project_dir)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}: !vault |\n  $ANSIBLE_VAULT;1.1;AES256\n  aabb\n")
        return "block"

    with patch("cli.encrypt.store_encrypted_var", side_effect=fake_store):
        assert ensure_vscode_browser_password(
            project_dir=str(tmp_path),
            data_key_path=str(data_key),
        ) is True
        assert ensure_vscode_browser_password(
            project_dir=str(tmp_path),
            data_key_path=str(data_key),
        ) is False

    assert len(created) == 1
    assert created[0][0] == VSCODE_BROWSER_PASSWORD_VAR
    assert len(created[0][1]) >= 32


def test_system_playbook_uses_vscode_browser_password():
    from cli.remote_dev import SYSTEM_PLAYBOOK

    assert "vscode_browser_password" in SYSTEM_PLAYBOOK
    assert "changeme" not in SYSTEM_PLAYBOOK


def test_read_vscode_browser_password_missing(tmp_path):
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "vars.yml").write_text("ansible_user: root\n")
    assert read_vscode_browser_password(project_dir=str(tmp_path)) is None
