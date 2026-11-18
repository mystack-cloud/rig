"""Unit tests for cli.git.GitManager."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.git import GitManager
from cli.remote_dev import ProjectConfig


def _project_with_git(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    return cfg


def test_load_and_generate_clone_tasks(tmp_path):
    cfg = _project_with_git(tmp_path)
    cfg.set_vars(
        "git",
        {
            "clone_repos": [
                {
                    "repo": "https://github.com/org/api.git",
                    "dest": "/workspace/api",
                    "accept_hostkey": True,
                }
            ],
            "ssh_repos": [
                {
                    "repo": "git@github.com:org/web.git",
                    "dest": "/workspace/web",
                    "accept_hostkey": True,
                }
            ],
        },
    )
    mgr = GitManager(cfg)
    assert mgr.load_git_config() is True
    tasks = mgr.generate_clone_tasks()
    assert any("ssh-keyscan" in (t.get("shell") or "") for t in tasks)
    assert any(t.get("git", {}).get("repo") == "https://github.com/org/api.git" for t in tasks)
    assert any(
        t.get("git", {}).get("repo") == "git@github.com:org/web.git" and t.get("git", {}).get("ssh_key") for t in tasks
    )


def test_hostname_and_repo_name_helpers():
    mgr = GitManager()
    assert mgr._get_hostname("https://github.com/org/api.git") == "github.com"
    assert mgr._get_hostname("http://gitlab.com/org/api.git") == "gitlab.com"
    assert mgr._get_hostname("git@github.com:org/api.git") == "github.com"
    assert mgr._get_hostname("weird") == "github.com"
    assert mgr._get_repo_name("https://github.com/org/api.git") == "api"
    assert mgr._get_repo_name("api") == "api"


def test_add_set_password_normalize_and_remove(tmp_path):
    cfg = _project_with_git(tmp_path)
    mgr = GitManager(cfg)
    assert mgr.setup_git_environment(str(tmp_path)) is True
    assert mgr.add_repo(
        "https://github.com/org/api.git",
        "/root/api",
        password_var="api_git_pat",
    )
    assert mgr.add_repo("git@github.com:org/web.git", "/workspace/web", is_ssh=True)
    assert mgr.set_repo_password_var("https://github.com/org/api.git", "shared_pat")
    assert mgr.set_repo_dest("https://github.com/org/api.git", "/workspace/api") is True

    changed = mgr.normalize_workspace_dests()
    # dest already under /workspace after set_repo_dest; ssh dest already ok
    assert changed == 0

    # Re-add a legacy /root path and normalize it.
    assert mgr.add_repo("https://github.com/org/legacy.git", "/root/legacy")
    assert mgr.normalize_workspace_dests() == 1
    cfg.load()
    dests = [r["dest"] for r in cfg.get_vars("git")["clone_repos"]]
    assert "/workspace/legacy" in dests

    assert mgr.remove_repo("https://github.com/org/legacy.git") is True
    cfg.load()
    urls = [r["repo"] for r in cfg.get_vars("git")["clone_repos"]]
    assert "https://github.com/org/legacy.git" not in urls


def test_set_password_var_missing_repo(tmp_path):
    cfg = _project_with_git(tmp_path)
    mgr = GitManager(cfg)
    mgr.add_repo("https://github.com/org/api.git", "/workspace/api")
    assert mgr.set_repo_password_var("https://github.com/org/missing.git", "x") is False
