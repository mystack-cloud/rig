"""CLI coverage for interactive environment initialization."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.main import main
from cli.remote_dev import (
    ProjectConfig,
    Workspace,
    _VarsLoader,
    discover_git_projects,
    interactive_env_init,
    resolve_init_context,
)


_MASTER = "test-master-key"


@pytest.fixture(autouse=True)
def _hetzner_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "test-token")
    monkeypatch.setenv("HETZNER_S3_ACCESS_KEY", "test-access")
    monkeypatch.setenv("HETZNER_S3_SECRET_KEY", "test-secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://fsn1.your-objectstorage.com")
    monkeypatch.delenv("RIG_GITHUB_PAT", raising=False)
    monkeypatch.delenv("RIG_CODING_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("RIG_OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("RIG_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RIG_CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("RIG_IDENTITY", raising=False)
    # Isolate ~/.rig/config identity writes (tmp data keys: tests/conftest.py).
    monkeypatch.setenv("RIG_CONFIG", str(tmp_path / "home-rig-config"))


def _answers(*items):
    """Input answers with leading identity prompt (Enter = OS default)."""
    return iter(("", *items))


def _personal_vars_text(project: Path) -> str:
    matches = sorted((project / ".rig").glob("vars.*.yml"))
    assert matches, f"no identity vars under {project / '.rig'}"
    return matches[0].read_text()


def _secrets(*items):
    """Secret answers: master key + confirm, then optional PAT/agent secrets."""
    it = iter((_MASTER, _MASTER, *items))
    return lambda _prompt: next(it, "")


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


def test_resolve_context_from_git_subfolder_keeps_cwd_as_root(tmp_path):
    monorepo = tmp_path / "monorepo"
    apps_web = monorepo / "apps" / "web"
    apps_web.mkdir(parents=True)
    _git_init(monorepo, "https://github.com/org/monorepo.git")

    context = resolve_init_context(str(apps_web))
    assert context["root"] == str(apps_web)
    assert context["in_git_repo"] is True
    assert context["current"] == str(monorepo)
    assert {project["name"] for project in context["projects"]} == {"monorepo"}


def test_interactive_init_in_git_subfolder_writes_locally(tmp_path):
    monorepo = tmp_path / "monorepo"
    apps_web = monorepo / "apps" / "web"
    apps_web.mkdir(parents=True)
    _git_init(monorepo, "https://github.com/org/monorepo.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = _answers("", "web", "", "")  # current repo, name, agent, editor
    assert interactive_env_init(
        str(apps_web),
        input_fn=lambda _prompt: next(answers),
        secret_fn=_secrets(),
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    assert (apps_web / "rig.yml").is_file()
    assert (apps_web / ".rig" / "materialize.yml").is_file()
    assert not (monorepo / "rig.yml").exists()
    assert not (monorepo / ".rig").exists()
    manifest = yaml.safe_load((apps_web / "rig.yml").read_text())
    assert manifest["name"] == "web"
    assert [repo["name"] for repo in manifest["repos"]] == ["monorepo"]
    assert manifest["repos"][0]["path"] == "../.."


def test_discover_projects_in_parent_folder(tmp_path):
    root = tmp_path / "materialize"
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

    answers = _answers("all", "rig", "", "")  # agent, editor defaults
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=_secrets(),  # skip PAT
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    # Config belongs in the current git project so it can be committed.
    assert not (root / "rig.yml").exists()
    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["kind"] == "workspace"
    assert manifest["name"] == "rig"
    assert manifest["host"]["provider"] == "hetzner"
    assert manifest["host"]["type"] == "vm"
    assert manifest["host"]["server_type"] == "cax11"
    assert manifest["host"]["availability_zone"] == "hel1"
    assert "vm" not in manifest
    assert {repo["name"] for repo in manifest["repos"]} == {"api", "rig"}
    api_entry = next(repo for repo in manifest["repos"] if repo["name"] == "api")
    assert api_entry["path"] == "../api"
    assert api_entry["url"] == "https://github.com/org/api.git"

    vars_text = (rig / ".rig" / "vars.yml").read_text()
    assert "git:" in vars_text
    assert "https://github.com/org/api.git" in vars_text
    assert "https://github.com/mystack-cloud/rig.git" in vars_text
    assert "git_pat:" not in vars_text


def test_workspace_init_reduces_repos_on_reinit(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    app = root / "app"
    rig = root / "rig"
    _git_init(api, "https://github.com/org/api.git")
    _git_init(app, "https://github.com/org/app.git")
    _git_init(rig, "https://github.com/org/rig.git")

    ws = Workspace(str(rig))
    both = [
        {"name": "api", "url": "https://github.com/org/api.git", "path": str(api)},
        {"name": "app", "url": "https://github.com/org/app.git", "path": str(app)},
    ]
    assert ws.init(name="rig", repos=both)
    assert ws.init(name="rig", repos=[both[0]])

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert {repo["name"] for repo in manifest["repos"]} == {"api"}

    project = ProjectConfig(str(rig))
    project.load()
    clone_repos = project.get_vars("git.clone_repos") or []
    assert {item["repo"] for item in clone_repos} == {
        "https://github.com/org/api.git"
    }


def test_workspace_init_prunes_git_list_for_path_only_selection(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    notes = root / "notes"
    rig = root / "rig"
    _git_init(api, "https://github.com/org/api.git")
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    notes.mkdir(parents=True)

    ws = Workspace(str(rig))
    both = [
        {"name": "api", "url": "https://github.com/org/api.git", "path": str(api)},
        {"name": "notes", "path": str(notes)},
    ]
    assert ws.init(name="rig", repos=both)

    project = ProjectConfig(str(rig))
    project.load()
    git_cfg = dict(project.get_vars("git") or {})
    git_cfg["clone_repos"] = list(git_cfg.get("clone_repos") or []) + [
        "legacy-non-dict-entry",
        {"repo": "https://github.com/org/ghost.git", "dest": "/workspace/ghost"},
    ]
    project.set_vars("git", git_cfg)

    assert ws.init(name="rig", repos=[{"name": "notes", "path": str(notes)}])

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert {repo["name"] for repo in manifest["repos"]} == {"notes"}

    project.load()
    clone_repos = project.get_vars("git.clone_repos") or []
    assert all(isinstance(item, dict) for item in clone_repos)
    assert not any(
        item.get("repo") == "https://github.com/org/api.git" for item in clone_repos
    )
    assert not any(
        item.get("repo") == "https://github.com/org/ghost.git" for item in clone_repos
    )


def test_interactive_init_defaults_to_current_repo(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    rig = root / "rig"
    _git_init(api, "https://github.com/org/api.git")
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = _answers("", "", "", "")  # current repo, name, agent, editor
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=_secrets(),
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["name"] == "rig"
    assert manifest["editor"]["default"] == "code"
    assert [repo["name"] for repo in manifest["repos"]] == ["rig"]


def test_cli_init_uses_interactive_flow(tmp_path, monkeypatch):
    repo = tmp_path / "solo"
    _git_init(repo, "https://github.com/org/solo.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")
    answers = _answers("all", "solo", "", "")  # agent, editor defaults
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "cli.main.interactive_env_init",
        lambda: interactive_env_init(
            str(repo),
            input_fn=lambda _prompt: next(answers),
            secret_fn=_secrets(),
            config_path=str(tmp_path / "rig-config"),
            data_key_path=str(data_key),
        ),
    )

    assert main(["init"]) == 0
    assert (repo / "rig.yml").is_file()
    assert not (repo / ".rig" / "rig.yml").exists()
    assert (repo / ".rig" / "provision.yml").is_file()
    assert (repo / ".rig" / "materialize.yml").is_file()
    assert (repo / ".rig" / "finalize.yml").is_file()
    assert not (tmp_path / "rig.yml").exists()


def test_interactive_init_stores_encrypted_git_pat(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    # Decline default → repo-specific {project}_git_pat
    answers = _answers("all", "rig", "", "", "n", "", "")  # … decline default PAT; personal PAT; personal agent
    secrets = iter([_MASTER, _MASTER, "ghp_test_pat_value", "agent-key-value"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file, vault_id=None):
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

    assert enc.call_count == 3  # git pat + agent key + vscode_browser_password
    shared_text = (rig / ".rig" / "vars.yml").read_text()
    personal_text = _personal_vars_text(rig)
    assert "rig_git_pat: !vault |" in personal_text
    assert "github_pat:" not in personal_text
    assert "coding_agent_api_key: !vault |" in personal_text
    assert "vscode_browser_password: !vault |" in shared_text
    clone_repos = yaml.load(shared_text, Loader=_VarsLoader)["git"]["clone_repos"]
    assert clone_repos[0]["password_var"] == "rig_git_pat"
    assert (rig / ".rig" / "provision.yml").is_file()
    assert (rig / ".rig" / "materialize.yml").is_file()
    assert not (rig / ".rig" / "setup.yml").exists()
    assert not (rig / ".rig" / "init.yml").exists()
    assert not (rig / ".rig" / "rig.yml").exists()
    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["playbooks"]["provision"] == ".rig/provision.yml"
    assert manifest["playbooks"]["materialize"] == ".rig/materialize.yml"
    assert manifest["playbooks"]["authorize"] == ".rig/authorize.yml"
    assert manifest["playbooks"]["finalize"] == ".rig/finalize.yml"
    assert (rig / ".rig" / "authorize.yml").is_file()
    assert (rig / ".rig" / "finalize.yml").is_file()
    assert manifest["agent"]["credentials_var"] == "coding_agent_api_key"


def test_interactive_init_scaffolds_opencode_agent_files(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = _answers("all", "rig", "opencode", "cursor")
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=_secrets(),
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["agent"]["default"] == "opencode"
    assert manifest["editor"]["default"] == "cursor"
    assert (rig / ".rig" / "AGENTS.md").is_file()
    assert (rig / ".rig" / ".opencode" / "opencode.json").is_file()
    assert (rig / ".rig" / ".opencode" / "mcp.json").is_file()
    config = json.loads((rig / ".rig" / ".opencode" / "opencode.json").read_text())
    assert config["model"].startswith("opencode/")
    assert "free" in config["model"] or config["model"] == "opencode/big-pickle"
    assert "CODING_AGENT_API_KEY" not in json.dumps(config)
    system = (rig / ".rig" / "provision.yml").read_text()
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

    answers = _answers("all", "rig", "claude", "")  # editor default
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=_secrets(),
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["agent"]["default"] == "claude"
    assert manifest["editor"]["default"] == "code"
    vars_data = yaml.load((rig / ".rig" / "vars.yml").read_text(), Loader=_VarsLoader)
    assert vars_data["coding_agent_type"] == "claude"
    assert (rig / ".rig" / "AGENTS.md").is_file()
    assert (rig / ".rig" / ".claude" / "settings.json").is_file()
    assert (rig / ".rig" / ".claude" / "mcp.json").is_file()
    system = (rig / ".rig" / "provision.yml").read_text()
    assert "Deploy AGENTS.md as Claude global rules" in system


def test_interactive_init_scaffolds_cursor_agent_files(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = _answers("all", "rig", "cursor", "code")
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=_secrets(),
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["agent"]["default"] == "cursor"
    assert manifest["editor"]["default"] == "code"
    vars_data = yaml.load((rig / ".rig" / "vars.yml").read_text(), Loader=_VarsLoader)
    assert vars_data["coding_agent_type"] == "cursor"
    assert (rig / ".rig" / ".cursor" / "cli-config.json").is_file()
    assert (rig / ".rig" / ".cursor" / "mcp.json").is_file()
    system = (rig / ".rig" / "provision.yml").read_text()
    assert "Install Cursor CLI" in system
    assert "'cursor' in _rig_agent_types" in system
    authnz = (rig / ".rig" / "authorize.yml").read_text()
    assert "CURSOR_API_KEY" in authnz
    assert "CURSOR_API_KEY" not in system


def test_interactive_init_selects_multiple_agents(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    # projects, name, agents, default attach agent, editor
    answers = _answers("all", "rig", "opencode,claude", "claude", "code")
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=_secrets(),
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["agent"]["default"] == "claude"
    assert manifest["agent"]["enabled"] == ["opencode", "claude"]
    assert manifest["agent"]["credentials_var"] == "claude_api_key"
    assert manifest["agent"]["credentials"] == {
        "opencode": "opencode_api_key",
        "claude": "claude_api_key",
    }
    assert manifest["editor"]["default"] == "code"
    assert manifest["editor"]["enabled"] == ["code"]
    vars_data = yaml.load((rig / ".rig" / "vars.yml").read_text(), Loader=_VarsLoader)
    assert vars_data["coding_agent_type"] == "claude"
    assert vars_data["coding_agent_types"] == ["opencode", "claude"]
    assert (rig / ".rig" / ".opencode" / "opencode.json").is_file()
    assert (rig / ".rig" / ".claude" / "settings.json").is_file()
    system = (rig / ".rig" / "provision.yml").read_text()
    assert "'opencode' in _rig_agent_types" in system
    assert "'claude' in _rig_agent_types" in system
    authnz = (rig / ".rig" / "authorize.yml").read_text()
    assert "opencode_api_key" in authnz
    assert "claude_api_key" in authnz


def test_interactive_init_selects_multiple_editors(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    # projects, name, agent default, editors, default editor
    answers = _answers("all", "rig", "", "1,3", "cursor")
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=_secrets(),
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    manifest = yaml.safe_load((rig / "rig.yml").read_text())
    assert manifest["editor"]["default"] == "cursor"
    assert manifest["editor"]["enabled"] == ["code", "cursor"]
    assert manifest["agent"]["default"] == "opencode"


def test_interactive_init_stores_distinct_agent_api_keys(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    # projects, name, agents 1+3, default (Enter), editor, personal storage
    answers = _answers("all", "rig", "1,3", "", "code", "y")
    secrets = iter([_MASTER, _MASTER, "", "oc-key", "cursor-key"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file, vault_id=None):
        return (
            f"{name}: !vault |\n"
            "          $ANSIBLE_VAULT;1.1;AES256\n"
            f"          {plaintext}\n"
        )

    with patch("cli.encrypt.encrypt_named_value", side_effect=_fake_encrypt):
        assert interactive_env_init(
            str(rig),
            input_fn=lambda _prompt: next(answers),
            secret_fn=lambda _prompt: next(secrets),
            config_path=str(tmp_path / "rig-config"),
            data_key_path=str(data_key),
        )

    personal = _personal_vars_text(rig)
    assert "opencode_api_key: !vault |" in personal
    assert "cursor_api_key: !vault |" in personal
    assert "oc-key" in personal
    assert "cursor-key" in personal
    assert "coding_agent_api_key:" not in personal


def test_interactive_init_converts_ssh_remote_to_https_and_stores_pat(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    _git_init(api, "git@github.com:org/api.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = _answers("all", "api", "", "", "y", "", "")  # … accept default PAT; personal PAT; personal agent
    secrets = iter([_MASTER, _MASTER, "ghp_from_ssh", "agent-key"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file, vault_id=None):
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
    shared_text = (api / ".rig" / "vars.yml").read_text()
    personal_text = _personal_vars_text(api)
    assert "github_pat: !vault |" in personal_text
    clone_repos = yaml.load(shared_text, Loader=_VarsLoader)["git"]["clone_repos"]
    assert clone_repos[0]["repo"] == "https://github.com/org/api.git"
    assert clone_repos[0]["password_var"] == "github_pat"


def test_interactive_init_stores_default_provider_pat_when_requested(tmp_path):
    root = tmp_path / "mystack"
    rig = root / "rig"
    _git_init(rig, "https://github.com/mystack-cloud/rig.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    # Accept default (Enter / Y) → github_pat even for a single repo
    answers = _answers("all", "rig", "", "", "", "", "")  # … accept default PAT; personal PAT; personal agent
    secrets = iter([_MASTER, _MASTER, "ghp_default_pat", "agent-key-value"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file, vault_id=None):
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

    assert enc.call_count == 3  # github_pat + agent key + vscode_browser_password
    shared_text = (rig / ".rig" / "vars.yml").read_text()
    personal_text = _personal_vars_text(rig)
    assert "github_pat: !vault |" in personal_text
    assert "rig_git_pat:" not in personal_text
    assert "vscode_browser_password: !vault |" in shared_text
    clone_repos = yaml.load(shared_text, Loader=_VarsLoader)["git"]["clone_repos"]
    assert clone_repos[0]["password_var"] == "github_pat"


def test_init_records_git_providers_for_system_playbook(tmp_path):
    root = tmp_path / "mystack"
    api = root / "api"
    web = root / "web"
    _git_init(api, "https://github.com/org/api.git")
    _git_init(web, "https://gitlab.com/org/web.git")
    data_key = tmp_path / "data-key"
    data_key.write_text("vault-pass\n")

    answers = _answers("all", "mystack", "", "")  # agent, editor
    assert interactive_env_init(
        str(root),
        input_fn=lambda _prompt: next(answers),
        secret_fn=_secrets(),
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    vars_data = yaml.load((root / ".rig" / "vars.yml").read_text(), Loader=_VarsLoader)
    assert vars_data["git_providers"] == ["github", "gitlab"]
    manifest = yaml.safe_load((root / "rig.yml").read_text())
    assert manifest["git_providers"] == ["github", "gitlab"]
    system = (root / ".rig" / "provision.yml").read_text()
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

    answers = _answers("all", "mystack", "", "", "y", "", "")  # default PAT; personal PAT; personal agent
    # First repo PAT, accept default, second Enter to reuse, then coding agent key.
    secrets = iter([_MASTER, _MASTER, "ghp_shared_pat", "", "agent-key"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file, vault_id=None):
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

    # Shared github PAT + coding agent key + vscode_browser_password
    assert enc.call_count == 3
    shared_text = (root / ".rig" / "vars.yml").read_text()
    personal_text = _personal_vars_text(root)
    assert "github_pat: !vault |" in personal_text
    assert "api_git_pat:" not in personal_text
    assert "web_git_pat:" not in personal_text
    assert "vscode_browser_password: !vault |" in shared_text
    clone_repos = yaml.load(shared_text, Loader=_VarsLoader)["git"]["clone_repos"]
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
    answers = _answers("all", "mystack", "", "", "n", "n", "", "")  # decline defaults; personal PAT; personal agent
    secrets = iter([_MASTER, _MASTER, "ghp_api_only", "glpat_web_only", "agent-key"])

    from unittest.mock import patch

    def _fake_encrypt(plaintext, name, vault_password_file, vault_id=None):
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

    assert enc.call_count == 4  # 2 repo pats + agent key + vscode_browser_password
    shared_text = (root / ".rig" / "vars.yml").read_text()
    personal_text = _personal_vars_text(root)
    assert "api_git_pat: !vault |" in personal_text
    assert "web_git_pat: !vault |" in personal_text
    assert "vscode_browser_password: !vault |" in shared_text
    assert "github_pat:" not in personal_text
    assert "gitlab_pat:" not in personal_text
    by_dest = {
        item["dest"]: item["password_var"]
        for item in yaml.load(shared_text, Loader=_VarsLoader)["git"]["clone_repos"]
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
        "",  # endpoint
        "",  # identity
        _MASTER,
        _MASTER,
        "all",
        "rig",
        "",  # coding agent default (opencode)
        "",  # editor default (code)
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

    answers = iter([
        "", "all", "rig", "", "",  # identity + first init
        "all", "rig", "", "",      # reinit (keys already materialized)
    ])
    secrets = _secrets()
    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=secrets,
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    custom_workspace = "# custom workspace playbook\n"
    (rig / ".rig" / "materialize.yml").write_text(custom_workspace)
    (rig / ".rig" / "provision.yml").unlink()
    # Leave a legacy nested file that reinit should remove
    (rig / ".rig" / "setup.yml").write_text("bootstrap: {}\n")

    assert interactive_env_init(
        str(rig),
        input_fn=lambda _prompt: next(answers),
        secret_fn=secrets,
        config_path=str(tmp_path / "rig-config"),
        data_key_path=str(data_key),
    )

    assert (rig / ".rig" / "provision.yml").is_file()
    assert (rig / ".rig" / "materialize.yml").read_text() == custom_workspace
    assert not (rig / ".rig" / "setup.yml").exists()
    assert not (rig / ".rig" / "rig.yml").exists()


def test_help_lists_top_level_commands_and_hides_internals(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    assert "{init,create,apply,attach,ssh,dev,list,cache,delete,vars}" in output
    assert "vpn" not in output
    assert "storage" not in output


@pytest.mark.parametrize("command", ["env", "vpn", "storage"])
def test_hidden_commands_are_rejected(command):
    with pytest.raises(SystemExit) as caught:
        main([command])
    assert caught.value.code != 0
