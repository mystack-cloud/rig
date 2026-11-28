"""Extra mocked coverage for cli.encrypt helpers."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_ensure_data_key_prompts_and_writes(tmp_path, monkeypatch):
    from cli import encrypt as enc

    path = str(tmp_path / "key")
    monkeypatch.setattr(enc, "PERSONAL_DATA_KEY_PATH", str(tmp_path / "p"))
    monkeypatch.setattr(enc, "SHARED_DATA_KEY_PATH", str(tmp_path / "s"))
    monkeypatch.setattr(enc, "DEFAULT_DATA_KEY_PATH", str(tmp_path / "d"))

    with (
        patch("cli.encrypt.read_data_key", return_value=None),
        patch("cli.vault_keys.read_wrap_fields", return_value={}),
        patch("cli.encrypt.write_data_key") as write,
    ):
        key = enc.ensure_data_key(
            path,
            input_fn=lambda _p: "secret-key",
            secret_fn=lambda _p: (_ for _ in ()).throw(RuntimeError("no getpass")),
        )
    assert key == "secret-key"
    assert write.call_count >= 1


def test_ensure_data_key_mismatch(tmp_path):
    from cli import encrypt as enc

    path = str(tmp_path / "key")
    with (
        patch("cli.encrypt.read_data_key", return_value=None),
        patch("cli.vault_keys.read_wrap_fields", return_value={}),
    ):
        with pytest.raises(ValueError, match="match"):
            enc.ensure_data_key(
                path,
                secret_fn=lambda p: "a" if "Confirm" not in p else "b",
            )


def test_prompt_and_store_git_pat_legacy(tmp_path, monkeypatch):
    from cli import encrypt as enc

    monkeypatch.chdir(tmp_path)
    (tmp_path / "rig.yml").write_text("name: demo\nkind: workspace\n")
    (tmp_path / ".rig").mkdir()

    with (
        patch("cli.encrypt.unlock_data_keys", return_value=("p", "s")),
        patch("cli.encrypt.store_encrypted_var") as store,
        patch.dict(os.environ, {"RIG_GITHUB_PAT": "tok"}),
    ):
        out = enc.prompt_and_store_git_pat(
            project_dir=str(tmp_path),
            repos=None,
            input_fn=lambda *_: "",
            secret_fn=lambda *_: "",
        )
    assert out == {}
    store.assert_called()


def test_prompt_coding_agent_credentials(tmp_path, monkeypatch):
    from cli import encrypt as enc

    monkeypatch.chdir(tmp_path)
    (tmp_path / "rig.yml").write_text("name: demo\nkind: workspace\nagent:\n  type: claude\n")
    (tmp_path / ".rig").mkdir()

    with (
        patch("cli.encrypt.unlock_data_keys", return_value=("p", "s")),
        patch("cli.encrypt.store_encrypted_var") as store,
        patch("cli.encrypt._prompt_secret_with_env_fallback", return_value="ak"),
    ):
        assert (
            enc.prompt_and_store_coding_agent_credentials(
                project_dir=str(tmp_path),
                agent_type="claude",
                input_fn=lambda *_: "",
                secret_fn=lambda *_: "",
            )
            is True
        )
    store.assert_called()


def test_coding_agent_key_env_selection_and_invalid_type(tmp_path, monkeypatch):
    from cli import encrypt as enc

    monkeypatch.chdir(tmp_path)
    (tmp_path / "rig.yml").write_text("name: demo\nkind: workspace\n")
    (tmp_path / ".rig").mkdir()

    with (
        patch("cli.encrypt.unlock_data_keys", return_value=("p", "s")),
        patch("cli.encrypt.store_encrypted_var"),
        patch("cli.encrypt._prompt_secret_with_env_fallback", return_value="") as ask,
    ):
        assert (
            enc.prompt_and_store_coding_agent_credentials(
                project_dir=str(tmp_path),
                agent_type="nope",
                env_name="RIG_CUSTOM_AGENT_KEY",
                input_fn=lambda *_: "",
                secret_fn=lambda *_: "",
            )
            is False
        )
        assert "RIG_CUSTOM_AGENT_KEY" in ask.call_args.args[1]
        assert (
            enc.prompt_and_store_coding_agent_credentials(
                project_dir=str(tmp_path),
                agent_type="claude",
                var_name="claude_api_key",
                input_fn=lambda *_: "",
                secret_fn=lambda *_: "",
            )
            is False
        )
        assert ask.call_args.args[1] == "RIG_ANTHROPIC_API_KEY"


def test_multi_agent_keys_skip_each_provider(tmp_path, monkeypatch):
    from cli import encrypt as enc

    monkeypatch.chdir(tmp_path)
    (tmp_path / "rig.yml").write_text("name: demo\nkind: workspace\n")
    (tmp_path / ".rig").mkdir()

    with (
        patch("cli.encrypt.unlock_data_keys", return_value=("p", "s")),
        patch("cli.encrypt.store_encrypted_var"),
        patch("cli.encrypt._prompt_secret_with_env_fallback", return_value=""),
    ):
        stored = enc.prompt_and_store_coding_agents_credentials(
            ["opencode", "cursor", "claude"],
            project_dir=str(tmp_path),
            input_fn=lambda *_: "",
            secret_fn=lambda *_: "",
        )
    assert stored == {"opencode": False, "cursor": False, "claude": False}


def test_list_all_and_export_shared(tmp_path, monkeypatch):
    from cli import encrypt as enc

    monkeypatch.chdir(tmp_path)
    (tmp_path / "rig.yml").write_text("name: demo\nkind: workspace\n")
    (tmp_path / ".rig").mkdir()

    with (
        patch("cli.encrypt.list_vault_var_names", return_value=["foo"]),
        patch("cli.encrypt.personal_vars_path", return_value=str(tmp_path / "missing.yml")),
    ):
        rows = enc.list_all_vault_vars(project_dir=str(tmp_path))
    assert any(r[0] == "foo" for r in rows)

    with (
        patch("cli.encrypt.unlock_data_keys", return_value=("p", "s")),
        patch("cli.encrypt.read_data_key", return_value="SHAREDKEY"),
    ):
        assert enc.export_shared_data_key(project_dir=str(tmp_path)) == "SHAREDKEY"


def test_interactive_encrypt_decrypt_paths(tmp_path, monkeypatch):
    from cli import encrypt as enc

    monkeypatch.chdir(tmp_path)
    (tmp_path / "rig.yml").write_text("name: demo\nkind: workspace\n")
    (tmp_path / ".rig").mkdir()

    with (
        patch("cli.encrypt.ensure_data_key", return_value="k"),
        patch("cli.encrypt.unlock_data_keys", return_value=("p", "s")),
        patch("cli.encrypt.encrypt_stdin", return_value="CIPHER"),
        patch("cli.encrypt.upsert_vault_var"),
    ):
        assert (
            enc.interactive_encrypt(
                stdin_name="foo",
                personal=True,
                input_fn=lambda *_: "foo",
                secret_fn=lambda *_: "val",
            )
            == "CIPHER"
        )

    with (
        patch("cli.encrypt.unlock_data_keys", return_value=("p", "s")),
        patch("cli.encrypt.decrypt_var", return_value="plain\n"),
    ):
        assert enc.interactive_decrypt(name="foo", project_dir=str(tmp_path)) == "plain\n"


def test_git_url_helpers():
    from cli import encrypt as enc

    assert enc.detect_git_provider("git@github.com:o/r.git") == "github"
    assert enc.detect_git_provider("https://gitlab.com/o/r") == "gitlab"
    assert enc.to_https_git_url("git@github.com:o/r.git") == "https://github.com/o/r.git"
    assert "pat" in enc.project_pat_var_name("My App")
    assert enc.provider_pat_var_name("github") == "github_pat"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://gitlab.mystack.cloud/o/r.git", "gitlab"),
        ("git@gitlab.example.com:o/r.git", "gitlab"),
        ("ssh://git@gitlab.corp.io:2222/o/r.git", "gitlab"),
        ("https://github.com/o/gitlab-tools.git", "github"),
        ("https://git.example.com/o/gitlab.git", None),
    ],
)
def test_detect_git_provider_self_hosted_gitlab(url, expected):
    from cli import encrypt as enc

    assert enc.detect_git_provider(url) == expected


def test_detect_claude_auth_type():
    from cli.agents import detect_claude_auth_type

    assert detect_claude_auth_type("sk-ant-oat01-x") == "oauth"
    assert detect_claude_auth_type("sk-ant-api03-x") == "api_key"
    assert detect_claude_auth_type("mystery") is None
    assert detect_claude_auth_type(None) is None


def _store_claude_key(tmp_path, monkeypatch, key, answers, agents=None):
    from cli import encrypt as enc

    monkeypatch.chdir(tmp_path)
    (tmp_path / "rig.yml").write_text("name: demo\nkind: workspace\n")
    (tmp_path / ".rig").mkdir(exist_ok=True)
    asked = []

    def ask(prompt=""):
        asked.append(prompt)
        return answers

    with (
        patch("cli.encrypt.unlock_data_keys", return_value=("p", "s")),
        patch("cli.encrypt.store_encrypted_var") as store,
        patch("cli.encrypt._prompt_secret_with_env_fallback", return_value=key),
    ):
        if agents:
            enc.prompt_and_store_coding_agents_credentials(
                agents, project_dir=str(tmp_path), input_fn=ask, secret_fn=lambda *_: "", personal=True
            )
        else:
            enc.prompt_and_store_coding_agent_credentials(
                project_dir=str(tmp_path), agent_type="claude", input_fn=ask, secret_fn=lambda *_: "", personal=True
            )
    stored = {c.args[0]: c.args[1] for c in store.call_args_list}
    return stored, asked


def test_claude_auth_type_detected_without_asking(tmp_path, monkeypatch):
    stored, asked = _store_claude_key(tmp_path, monkeypatch, "sk-ant-oat01-x", "")
    assert stored["claude_auth_type"] == "oauth"
    assert asked == []


def test_claude_auth_type_asks_when_unknown(tmp_path, monkeypatch):
    stored, asked = _store_claude_key(tmp_path, monkeypatch, "mystery", "o")
    assert stored["claude_auth_type"] == "oauth"
    assert len(asked) == 1
    stored, _ = _store_claude_key(tmp_path, monkeypatch, "mystery", "k")
    assert stored["claude_auth_type"] == "api_key"
    stored, _ = _store_claude_key(tmp_path, monkeypatch, "mystery", "")
    assert "claude_auth_type" not in stored


def test_claude_auth_type_stored_for_multi_agent(tmp_path, monkeypatch):
    stored, _ = _store_claude_key(tmp_path, monkeypatch, "sk-ant-api03-x", "", agents=["claude"])
    assert stored["claude_auth_type"] == "api_key"
