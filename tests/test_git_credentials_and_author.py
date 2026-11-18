"""Provider tokens stay out of repo URLs; the VM gets a git author."""

import os
import stat
import subprocess
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.git_author import derive_git_author, resolve_git_author
from cli.remote_dev import (
    AUTHORIZE_PLAYBOOK,
    MATERIALIZE_PLAYBOOK,
    PROVIDER_TOKENS_ENV_FILE,
    ProjectConfig,
    RemoteDevEnvironment,
    build_rig_ansible_vars,
)

# --- git author resolution ------------------------------------------------------


def test_author_is_derived_from_the_identity():
    assert derive_git_author("mlewk") == {"name": "mlewk", "email": "mlewk@rig.local"}
    assert resolve_git_author("mlewk") == {"name": "mlewk", "email": "mlewk@rig.local"}


def test_derived_email_is_not_a_github_noreply_address():
    # users.noreply.github.com links commits to whoever owns that handle.
    assert "github" not in resolve_git_author("mlewk")["email"]


def test_vars_override_each_field_separately():
    both = {"git_author_name": "Ada  Lovelace", "git_author_email": "ada@example.org"}.get
    assert resolve_git_author("mlewk", both) == {
        "name": "Ada Lovelace",
        "email": "ada@example.org",
    }
    only_name = {"git_author_name": "Ada"}.get
    assert resolve_git_author("mlewk", only_name) == {
        "name": "Ada",
        "email": "mlewk@rig.local",
    }


def test_unusable_vars_fall_back_to_the_identity():
    assert resolve_git_author("mlewk", lambda key: 42) == derive_git_author("mlewk")
    assert resolve_git_author("mlewk", lambda key: None) == derive_git_author("mlewk")

    def boom(key):
        raise RuntimeError("vars unreadable")

    assert resolve_git_author("mlewk", boom) == derive_git_author("mlewk")


def test_runtime_vars_carry_the_author(tmp_path, monkeypatch):
    monkeypatch.setenv("RIG_IDENTITY", "mlewk")
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="demo", agent_type="opencode")
    cfg.load()
    env = RemoteDevEnvironment(name="dev-abc123", config=cfg.config)

    out = build_rig_ansible_vars(env, cfg, action="create")
    assert out["rig_identity"] == "mlewk"
    assert out["rig_git_author_name"] == "mlewk"
    assert out["rig_git_author_email"] == "mlewk@rig.local"


def test_runtime_vars_honour_the_vars_override(tmp_path, monkeypatch):
    monkeypatch.setenv("RIG_IDENTITY", "mlewk")
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="demo", agent_type="opencode")
    cfg.load()
    monkeypatch.setattr(
        cfg,
        "get_vars",
        lambda key, _real=cfg.get_vars: {
            "git_author_name": "Ada Lovelace",
            "git_author_email": "ada@example.org",
        }.get(key, _real(key)),
    )
    env = RemoteDevEnvironment(name="dev-abc123", config=cfg.config)

    out = build_rig_ansible_vars(env, cfg, action="create")
    assert out["rig_git_author_name"] == "Ada Lovelace"
    assert out["rig_git_author_email"] == "ada@example.org"


# --- materialize: no token in the clone URL -------------------------------------


def _clone_task():
    tasks = yaml.safe_load(MATERIALIZE_PLAYBOOK)[0]["tasks"]
    return next(t for t in tasks if t["name"] == "Clone configured git repositories")


def test_materialize_clone_uses_the_plain_repo_url():
    assert "replace('https://'" not in MATERIALIZE_PLAYBOOK
    assert "x-access-token@" not in MATERIALIZE_PLAYBOOK
    clone = _clone_task()
    assert clone["git"]["repo"] == "{{ item.repo }}"
    assert clone["environment"]["GIT_TERMINAL_PROMPT"] == "0"


def test_materialize_clone_keeps_per_repo_tokens():
    clone = _clone_task()
    pat = clone["vars"]["_pat"]
    assert "lookup('vars', _pat_var" in pat  # password_var still wins
    assert "github_pat" in clone["vars"]["_provider_pat"]
    assert "gitlab_pat" in clone["vars"]["_provider_pat"]
    env = clone["environment"]
    assert env["RIG_GIT_PASSWORD"] == "{{ _pat }}"
    assert env["RIG_GIT_USERNAME"] == "{{ _auth_user }}"
    # no helper is wired when there is no token at all
    assert "_pat | length > 0" in env["GIT_CONFIG_COUNT"]


def test_materialize_inline_helper_answers_from_the_environment():
    env = _clone_task()["environment"]
    helper = env["GIT_CONFIG_VALUE_1"]
    assert env["GIT_CONFIG_KEY_0"] == env["GIT_CONFIG_KEY_1"] == "credential.helper"
    assert env["GIT_CONFIG_VALUE_0"] == ""  # reset inherited helpers first

    run_env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "RIG_GIT_USERNAME": "x-access-token",
        "RIG_GIT_PASSWORD": "ghp_pertoken",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": helper,
    }
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        env=run_env,
        check=True,
    ).stdout
    assert "username=x-access-token" in out
    assert "password=ghp_pertoken" in out


# --- authorize: token file, helper, author --------------------------------------


def _authorize_tasks():
    return yaml.safe_load(AUTHORIZE_PLAYBOOK)[0]["tasks"]


def _provider_block():
    task = next(t for t in _authorize_tasks() if t["name"] == "Provider tokens for git, gh and glab")
    return task["block"]


def test_authorize_sets_the_git_author():
    task = next(t for t in _authorize_tasks() if t["name"] == "Set the git author for commits made on the VM")
    assert task["command"]["argv"][:3] == ["git", "config", "--global"]
    keys = [item["key"] for item in task["loop"]]
    assert keys == ["user.name", "user.email"]
    rendered = yaml.safe_dump(task["loop"])
    assert "rig_git_author_name" in rendered and "rig_git_author_email" in rendered


def test_authorize_writes_tokens_to_a_root_only_file():
    block = _provider_block()
    env_file = next(t for t in block if t["name"].startswith("Write the provider token file"))
    assert env_file["copy"]["dest"] == "/root/.config/rig/git-provider.env"
    assert env_file["copy"]["mode"] == "0600"
    assert env_file["no_log"] is True
    assert "GH_TOKEN=" in env_file["copy"]["content"]
    assert "GITLAB_TOKEN=" in env_file["copy"]["content"]

    directory = next(t for t in block if t["name"].startswith("Ensure the rig config directory"))
    assert directory["file"]["mode"] == "0700"


def test_authorize_exports_tokens_for_login_shells():
    block = _provider_block()
    profile = next(t for t in block if "login shells" in t["name"])
    assert profile["copy"]["dest"] == "/etc/profile.d/rig-git-provider.sh"
    # World-readable script, but it only sources the root-only file.
    assert "git-provider.env" in profile["copy"]["content"]
    assert "GH_TOKEN" not in profile["copy"]["content"]


def test_authorize_registers_the_helper_system_wide():
    block = _provider_block()
    cfg = next(t for t in block if t["name"].startswith("Use the helper"))
    assert cfg["command"]["argv"] == [
        "git",
        "config",
        "--system",
        "credential.helper",
        "/usr/local/bin/rig-git-credential",
    ]


def _helper_script(tmp_path):
    block = _provider_block()
    helper = next(t for t in block if "credential helper" in t["name"])
    assert helper["copy"]["mode"] == "0755"
    token_file = tmp_path / "git-provider.env"
    script = tmp_path / "rig-git-credential"
    script.write_text(helper["copy"]["content"].replace("/root/.config/rig/git-provider.env", str(token_file)))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, token_file


def _ask(script, op, host):
    return subprocess.run(
        [str(script), op],
        input=f"protocol=https\nhost={host}\n\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_helper_serves_credentials_per_host(tmp_path):
    script, token_file = _helper_script(tmp_path)
    token_file.write_text("GH_TOKEN=ghp_abc\nGITLAB_TOKEN=glpat-xyz\n")

    assert _ask(script, "get", "github.com") == "username=x-access-token\npassword=ghp_abc\n"
    assert _ask(script, "get", "gitlab.com") == "username=oauth2\npassword=glpat-xyz\n"


def test_helper_stays_silent_when_it_has_nothing_to_say(tmp_path):
    script, token_file = _helper_script(tmp_path)

    assert _ask(script, "get", "github.com") == ""  # no token file yet
    token_file.write_text("GH_TOKEN=ghp_abc\n")
    assert _ask(script, "get", "example.com") == ""  # unknown host
    assert _ask(script, "get", "gitlab.com") == ""  # no GitLab token
    assert _ask(script, "store", "github.com") == ""  # only answers `get`
    assert _ask(script, "erase", "github.com") == ""


def test_helper_works_through_real_git(tmp_path):
    script, token_file = _helper_script(tmp_path)
    token_file.write_text("GH_TOKEN=ghp_abc\n")
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": str(script),
    }
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout
    assert "username=x-access-token" in out
    assert "password=ghp_abc" in out


# --- the agent's non-login shell must get the tokens too ------------------------


def test_agent_launch_sources_the_provider_tokens():
    """`rig attach` runs `ssh host '<cmd>'` (non-login): /etc/profile.d is never
    read, so the launch command itself must export GH_TOKEN / GITLAB_TOKEN or
    `gh` inside the agent says it is not authenticated."""
    assert PROVIDER_TOKENS_ENV_FILE == "/root/.config/rig/git-provider.env"
    for agent in ("claude", "cursor", "opencode"):
        cmd = RemoteDevEnvironment.agent_remote_command(agent)
        source = f"[ -f {PROVIDER_TOKENS_ENV_FILE} ] && . {PROVIDER_TOKENS_ENV_FILE}"
        assert source in cmd
        # sourced between `set -a` and `set +a` so the variables are exported
        assert cmd.index("set -a") < cmd.index(source) < cmd.index("set +a")
        # and before the agent is exec'd
        assert cmd.index(source) < cmd.index("exec ")


def test_agent_launch_exports_the_token_to_the_agent_process(tmp_path):
    env_file = tmp_path / "git-provider.env"
    env_file.write_text("GH_TOKEN=ghp_agent\n")
    cmd = RemoteDevEnvironment.agent_remote_command("claude")
    cmd = cmd.replace(PROVIDER_TOKENS_ENV_FILE, str(env_file))
    cmd = cmd.replace("mkdir -p /workspace && cd /workspace && exec claude", 'printf "%s" "$GH_TOKEN"')
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=True).stdout
    assert out == "ghp_agent"
