"""CLI coverage for interactive environment initialization."""

import json
import os
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.main import main
from cli.remote_dev import (
    _VarsLoader,
    discover_git_projects,
    interactive_env_init,
    resolve_init_context,
)


@pytest.fixture(autouse=True)
def _hetzner_env(monkeypatch):
    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "test-token")
    monkeypatch.setenv("HETZNER_S3_ACCESS_KEY", "test-access")
    monkeypatch.setenv("HETZNER_S3_SECRET_KEY", "test-secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://fsn1.your-objectstorage.com")
    monkeypatch.delenv("RIG_GITHUB_PAT", raising=False)
    monkeypatch.delenv("RIG_CODING_AGENT_API_KEY", raising=False)


def _git_init(path, remote=None):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("test\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    if remote:
        subprocess.run(
            ["git", "remote", "add", "origin", remote],
            cwd=path,
            check=True,
            capture_output=True,
        )


def test_resolve_context_from_git_repo_offers_siblings(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    web = root / "web"
    _git_init(api, "https://github.com/org/api.git")
    _git_init(web, "https://github.com/org/web.git")

    context = resolve_init_context(str(api))
    assert context["root"] == str(api)
    assert context["in_git_repo"] is True
    assert context["current"] == str(api)
    names = {project["name"] for project in context["projects"]}
    assert names == {"api", "web"}


def test_discover_projects_in_parent_folder(tmp_path):
    root = tmp_path / "workspace"
    _git_init(root / "alpha", "https://github.com/org/alpha.git")
    _git_init(root / "beta")
    (root / "notes.txt").write_text("ignore me\n")

    projects = discover_git_projects(str(root))
    assert [project["name"] for project in projects] == ["alpha", "beta"]
    assert projects[0]["url"] == "https://github.com/org/alpha.git"
    assert "url" not in projects[1]


def test_interactive_init_selects_projects(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    rig = root / "rig"
    _git_init(api, "https://github.com/org/api.git")
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = iter(["all", "rig", ""])
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: "",  # skip PAT
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    # Config belongs in the current git project so it can be committed.
    assert not (root / "rig.yml").exists()
    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["kind"] == "workspace"
    assert manifest["name"] == "rig"
    assert manifest["vm"]["type"] == "cax11"
    assert manifest["vm"]["availability_zone"] == "hel1"
    assert {repo["name"] for repo in manifest["repos"]} == {"api", "rig"}
    api_entry = next(repo for repo in manifest["repos"] if repo["name"] == "api")
    assert api_entry["path"] == "../api"
    assert api_entry["url"] == "https://github.com/org/api.git"

    vars_text = (rig / ".rig" / "vars.yml").read_text()
    assert "git:" in vars_text
    assert "https://github.com/org/api.git" in vars_text
    assert "https://github.com/mystack-cloud/rig.git" in vars_text
    assert "git_pat:" not in vars_text


def test_interactive_init_defaults_to_current_repo(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    rig = root / "rig"
    _git_init(api, "https://github.com/org/api.git")
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = iter(["", "", ""])  # current repo, name, agent
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: "",
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["name"] == "rig"
    assert [repo["name"] for repo in manifest["repos"]] == ["rig"]


def test_cli_init_uses_interactive_flow(tmp_path, monkeypatch):
    repo = tmp_path / "solo"
    _git_init(repo, "https://github.com/org/solo.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")
    answers = iter(["all", "solo", ""])
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "cli.main.interactive_env_init",
        lambda: interactive_env_init(
            str(repo),
            input_fn=lambda _prompt: next(answers),
            secret_fn=lambda _prompt: "",
            config_path=str(tmp_path / "rig-config"),
            data_key_path=str(data_key),
        ),
    )

    assert main(["init"]) == 0
    assert (repo / "rig.yml").is_file()
    assert not (repo / ".rig" / "rig.yml").exists()
    assert (repo / ".rig" / "system.yml").is_file()
    assert (repo / ".rig" / "workspace.yml").is_file()
    assert (repo / ".rig" / "finalizers.yml").is_file()
    assert not (tmp_path / "rig.yml").exists()


def test_interactive_init_stores_encrypted_git_pat(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    # Decline default → repo-specific {project}_git_pat
    answers = iter(["all", "rig", "", "n"])  # agent default, decline default PAT
    secrets = iter(["ghp_test_pat_value", "agent-key-value"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file):
        return (
            f"{name}: !vault |\n"
            "          $ANSIBLE_VAULT;1.1;AES256\n"
            f"          {plaintext}\n"
        )

    with patch("cli.encrypt.encrypt_named_value", side_effect=_fake_encrypt) as enc:
        assert interactive_env_init(
            str(rig),
            input_fn=lambda _prompt: next(answers),
            secret_fn=lambda _prompt: next(secrets),
            config_path=str(tmp_path / "rig-config"),
            data_key_path=str(data_key),
        )

    assert enc.call_count == 2
    vars_text = (rig / ".rig" / "vars.yml").read_text()
    assert "rig_git_pat: !vault |" in vars_text
    assert "github_pat:" not in vars_text
    assert "coding_agent_api_key: !vault |" in vars_text
    clone_repos = yaml.load(vars_text, Loader=_VarsLoader)["git"]["clone_repos"]
    assert clone_repos[0]["password_var"] == "rig_git_pat"
    assert (rig / ".rig" / "system.yml").is_file()
    assert (rig / ".rig" / "workspace.yml").is_file()
    assert not (rig / ".rig" / "setup.yml").exists()
    assert not (rig / ".rig" / "init.yml").exists()
    assert not (rig / ".rig" / "rig.yml").exists()
    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["playbooks"]["system"] == ".rig/system.yml"
    assert manifest["playbooks"]["workspace"] == ".rig/workspace.yml"
    assert manifest["playbooks"]["finalizers"] == ".rig/finalizers.yml"
    assert (rig / ".rig" / "finalizers.yml").is_file()
    assert manifest["agent"]["credentials_var"] == "coding_agent_api_key"


def test_interactive_init_scaffolds_opencode_agent_files(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = iter(["all", "rig", "opencode"])
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: "",
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["agent"]["type"] == "opencode"
    assert (rig / ".rig" / "AGENTS.md").is_file()
    assert (rig / ".rig" / ".opencode" / "opencode.json").is_file()
    assert (rig / ".rig" / ".opencode" / "mcp.json").is_file()
    config = json.loads((rig / ".rig" / ".opencode" / "opencode.json").read_text())
    assert config["model"].startswith("opencode/")
    assert "free" in config["model"] or config["model"] == "opencode/big-pickle"
    assert "CODING_AGENT_API_KEY" not in json.dumps(config)
    system = (rig / ".rig" / "system.yml").read_text()
    assert "Install OpenCode CLI" in system
    assert 'test -s "$bin"' in system
    assert "creates:" not in system.split("Install OpenCode CLI", 1)[1].split("Symlink OpenCode", 1)[0]
    assert "Install Claude Code CLI" in system


def test_interactive_init_scaffolds_claude_agent_files(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = iter(["all", "rig", "claude"])
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: "",
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["agent"]["type"] == "claude"
    vars_data = yaml.load((rig / ".rig" / "vars.yml").read_text(), Loader=_VarsLoader)
    assert vars_data["coding_agent_type"] == "claude"
    assert (rig / ".rig" / "AGENTS.md").is_file()
    assert (rig / ".rig" / ".claude" / "settings.json").is_file()
    assert (rig / ".rig" / ".claude" / "mcp.json").is_file()
    system = (rig / ".rig" / "system.yml").read_text()
    assert "Deploy AGENTS.md as Claude global rules" in system


def test_interactive_init_converts_ssh_remote_to_https_and_stores_pat(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    _git_init(api, "git@github.com:org/api.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = iter(["all", "api", "", "y"])  # agent default, accept default PAT
    secrets = iter(["ghp_from_ssh", "agent-key"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file):
        return (
            f"{name}: !vault |\n"
            "          $ANSIBLE_VAULT;1.1;AES256\n"
            f"          {plaintext}\n"
        )

    with patch("cli.encrypt.encrypt_named_value", side_effect=_fake_encrypt):
        assert interactive_env_init(
            str(api),
            input_fn=lambda _prompt: next(answers),
            secret_fn=lambda _prompt: next(secrets),
            config_path=str(tmp_path / "rig-config"),
            data_key_path=str(data_key),
        )

    manifest = yaml.safe_load((api / "rig.yml").read_text())
    assert manifest["repos"][0]["url"] == "https://github.com/org/api.git"
    vars_text = (api / ".rig" / "vars.yml").read_text()
    assert "github_pat: !vault |" in vars_text
    clone_repos = yaml.load(vars_text, Loader=_VarsLoader)["git"]["clone_repos"]
    assert clone_repos[0]["repo"] == "https://github.com/org/api.git"
    assert clone_repos[0]["password_var"] == "github_pat"


def test_interactive_init_stores_default_provider_pat_when_requested(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    # Accept default (Enter / Y) → github_pat even for a single repo
    answers = iter(["all", "rig", "", ""])  # agent + accept default PAT
    secrets = iter(["ghp_default_pat", "agent-key-value"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file):
        return (
            f"{name}: !vault |\n"
            "          $ANSIBLE_VAULT;1.1;AES256\n"
            f"          {plaintext}\n"
        )

    with patch("cli.encrypt.encrypt_named_value", side_effect=_fake_encrypt) as enc:
        assert interactive_env_init(
            str(rig),
            input_fn=lambda _prompt: next(answers),
            secret_fn=lambda _prompt: next(secrets),
            config_path=str(tmp_path / "rig-config"),
            data_key_path=str(data_key),
        )

    assert enc.call_count == 2
    vars_text = (rig / ".rig" / "vars.yml").read_text()
    assert "github_pat: !vault |" in vars_text
    assert "rig_git_pat:" not in vars_text
    clone_repos = yaml.load(vars_text, Loader=_VarsLoader)["git"]["clone_repos"]
    assert clone_repos[0]["password_var"] == "github_pat"


def test_init_records_git_providers_for_system_playbook(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    web = root / "web"
    _git_init(api, "https://github.com/org/api.git")
    _git_init(web, "https://gitlab.com/org/web.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = iter(["all", "mystack", ""])
    assert interactive_env_init(
        str(root),
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: "",
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    vars_data = yaml.load((root / ".rig" / "vars.yml").read_text(), Loader=_VarsLoader)
    assert vars_data["git_providers"] == ["github", "gitlab"]
    manifest = yaml.safe_load((root / "rig.yml").read_text())
    assert manifest["git_providers"] == ["github", "gitlab"]
    system = (root / ".rig" / "system.yml").read_text()
    assert "Install GitHub CLI (gh)" in system
    assert "Install GitLab CLI (glab)" in system
    assert "'github' in (git_providers | default([]))" in system
    assert "'gitlab' in (git_providers | default([]))" in system


def test_interactive_init_reuses_provider_pat_across_repos(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    web = root / "web"
    _git_init(api, "https://github.com/org/api.git")
    _git_init(web, "https://github.com/org/web.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = iter(["all", "mystack", "", "y"])
    # First repo PAT, accept default, second Enter to reuse, then coding agent key.
    secrets = iter(["ghp_shared_pat", "", "agent-key"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file):
        return (
            f"{name}: !vault |\n"
            "          $ANSIBLE_VAULT;1.1;AES256\n"
            f"          {plaintext}\n"
        )

    with patch("cli.encrypt.encrypt_named_value", side_effect=_fake_encrypt) as enc:
        assert interactive_env_init(
            str(root),
            input_fn=lambda _prompt: next(answers),
            secret_fn=lambda _prompt: next(secrets),
            config_path=str(tmp_path / "rig-config"),
            data_key_path=str(data_key),
        )

    # Shared github PAT + coding agent key
    assert enc.call_count == 2
    vars_text = (root / ".rig" / "vars.yml").read_text()
    assert "github_pat: !vault |" in vars_text
    assert "api_git_pat:" not in vars_text
    assert "web_git_pat:" not in vars_text
    clone_repos = yaml.load(vars_text, Loader=_VarsLoader)["git"]["clone_repos"]
    assert {item["password_var"] for item in clone_repos} == {"github_pat"}


def test_interactive_init_stores_repo_specific_pats_when_different(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    web = root / "web"
    _git_init(api, "https://github.com/org/api.git")
    _git_init(web, "https://gitlab.com/org/web.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    # Decline default for both providers → {project}_git_pat each
    answers = iter(["all", "mystack", "", "n", "n"])
    secrets = iter(["ghp_api_only", "glpat_web_only", "agent-key"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file):
        return (
            f"{name}: !vault |\n"
            "          $ANSIBLE_VAULT;1.1;AES256\n"
            f"          {plaintext}\n"
        )

    with patch("cli.encrypt.encrypt_named_value", side_effect=_fake_encrypt) as enc:
        assert interactive_env_init(
            str(root),
            input_fn=lambda _prompt: next(answers),
            secret_fn=lambda _prompt: next(secrets),
            config_path=str(tmp_path / "rig-config"),
            data_key_path=str(data_key),
        )

    assert enc.call_count == 3
    vars_text = (root / ".rig" / "vars.yml").read_text()
    assert "api_git_pat: !vault |" in vars_text
    assert "web_git_pat: !vault |" in vars_text
    assert "github_pat:" not in vars_text
    assert "gitlab_pat:" not in vars_text
    by_dest = {
        item["dest"]: item["password_var"]
        for item in yaml.load(vars_text, Loader=_VarsLoader)["git"]["clone_repos"]
    }
    assert by_dest["/workspace/api"] == "api_git_pat"
    assert by_dest["/workspace/web"] == "web_git_pat"


def test_interactive_init_prompts_for_missing_credentials(tmp_path, monkeypatch):
    for key in (
        "HETZNER_CLOUD_API_TOKEN",
        "HZ_TOKEN",
        "HETZNER_S3_ACCESS_KEY",
        "HETZNER_S3_SECRET_KEY",
        "S3_ENDPOINT_URL",
        "RIG_GITHUB_PAT",
    ):
        monkeypatch.delenv(key, raising=False)

    root = tmp_path / "mystack"
    repo = root / "rig"
    _git_init(repo, "https://github.com/mystack-cloud/rig.git")
    config_path = tmp_path / "home-rig-config"
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")
    answers = iter([
        "cloud-token",
        "s3-access",
        "s3-secret",
        "",
        "all",
        "rig",
        "",  # coding agent default (opencode)
        "",  # skip PAT
        "",  # skip coding agent API key
    ])
    assert interactive_env_init(
        str(repo),
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: next(answers),
        config_path=str(config_path),
        data_key_path=str(data_key),
    )
    saved = (config_path).read_text()
    assert "[default]" in saved
    assert "cloud-token" in saved
    assert (repo / "rig.yml").is_file()
    assert not (root / "rig.yml").exists()


def test_reinit_adds_missing_files_only(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = iter(["all", "rig", "", "all", "rig", ""])
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: "",
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    custom_workspace = "# custom workspace playbook\n"
    (rig / ".rig" / "workspace.yml").write_text(custom_workspace)
    (rig / ".rig" / "system.yml").unlink()
    # Leave a legacy nested file that reinit should remove
    (rig / ".rig" / "setup.yml").write_text("bootstrap: {}\n")

    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: "",
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    assert (rig / ".rig" / "system.yml").is_file()
    assert (rig / ".rig" / "workspace.yml").read_text() == custom_workspace
    assert not (rig / ".rig" / "setup.yml").exists()
    assert not (rig / ".rig" / "rig.yml").exists()


def test_help_lists_top_level_commands_and_hides_internals(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    assert "{init,create,apply,attach,dev,list,cache,destroy,encrypt,decrypt}" in output
    assert "vpn" not in output
    assert "storage" not in output


@pytest.mark.parametrize("command", ["env", "vpn", "storage"])
def test_hidden_commands_are_rejected(command):
    with pytest.raises(SystemExit) as caught:
        main([command])
    assert caught.value.code != 0
