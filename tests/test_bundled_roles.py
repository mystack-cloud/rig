"""The playbooks rig generates are thin: the work lives in bundled roles, tagged so users can skip it."""

import os
import shutil
import subprocess
import sys
from unittest.mock import MagicMock

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from _roles import ROLES_DIR, role_names, role_tasks

from cli import remote_dev, snapshots
from cli.remote_dev import (
    AUTHORIZE_PLAYBOOK,
    BUNDLED_ROLES_DIR,
    DESTROY_PLAYBOOK,
    MATERIALIZE_PLAYBOOK,
    PROVISION_PLAYBOOK,
    ProjectConfig,
    RemoteDevEnvironment,
    ansible_tag_args,
    configured_skip_tags,
    roles_search_path,
)

CROSS_CUTTING = {"packages", "credentials", "agents", "mcp", "git", "always"}


def _entry_points():
    for role in role_names():
        tasks_dir = os.path.join(ROLES_DIR, role, "tasks")
        for name in sorted(os.listdir(tasks_dir)):
            yield role, name[: -len(".yml")]


def test_bundled_roles_dir_is_the_package_roles_dir():
    assert os.path.realpath(BUNDLED_ROLES_DIR) == os.path.realpath(ROLES_DIR)
    assert os.path.realpath(snapshots.BUNDLED_ROLES_DIR) == os.path.realpath(ROLES_DIR)


def test_every_role_has_defaults_and_an_entry_point():
    assert role_names(), "no bundled roles"
    for role in role_names():
        assert role.startswith("rig_"), role
        assert os.path.isfile(os.path.join(ROLES_DIR, role, "defaults", "main.yml")), role
        assert list(_entry_points()), role


@pytest.mark.parametrize(("role", "stage"), list(_entry_points()))
def test_every_task_carries_its_role_tag_and_a_group_tag(role, stage):
    tasks = role_tasks(role, stage)
    assert tasks
    for task in tasks:
        tags = task["_tags"]
        assert role in tags, f"{role}/{stage}: {task.get('name')} lacks the role tag"
        groups = {t for t in tags if t.startswith(role + "_")}
        assert groups, f"{role}/{stage}: {task.get('name')} lacks a group tag ({role}_<group>)"
        for tag in tags - groups - {role}:
            assert tag in CROSS_CUTTING, f"{role}/{stage}: unknown tag {tag!r} on {task.get('name')}"


def test_home_ownership_always_runs():
    (task,) = role_tasks("rig_home_ownership")
    assert "always" in task["_tags"]


def test_provision_playbook_is_thin_and_calls_the_roles_in_order():
    play = yaml.safe_load(PROVISION_PLAYBOOK)[0]
    names = []
    for task in play["tasks"]:
        include = task["include_role"]
        assert include["tasks_from"] == "install"
        assert task["tags"] == ["always"], "the include must run for --tags to reach the role's own tags"
        names.append(include["name"])
    assert names == [
        "rig_base",
        "rig_workspace_user",
        "rig_git",
        "rig_live_agent",
        "rig_code_server",
        "{{ _rig_agent_roles[item] }}",
        "rig_home_ownership",
    ]
    assert len(PROVISION_PLAYBOOK.splitlines()) < 80
    assert play["vars"]["_rig_agent_roles"] == {
        "opencode": "rig_opencode",
        "claude": "rig_claude_code",
        "cursor": "rig_cursor_cli",
    }
    for role in [n for n in names if n.startswith("rig_")] + list(play["vars"]["_rig_agent_roles"].values()):
        assert os.path.isfile(os.path.join(ROLES_DIR, role, "tasks", "install.yml")), role


def test_authorize_playbook_is_thin_and_orders_the_roles_by_dependency():
    play = yaml.safe_load(AUTHORIZE_PLAYBOOK)[0]
    names = []
    for task in play["tasks"]:
        include = task["include_role"]
        assert include["tasks_from"] == "authorize"
        assert task["tags"] == ["always"]
        names.append(include["name"])
    # The Agent roles seed ~/.claude.json and settings.json first; rig_mcp merges into them and
    # rig_live_agent adds hooks to settings.json; ownership is handed over last.
    assert names == [
        "rig_workspace_user",
        "rig_code_server",
        "{{ _rig_agent_roles[item] }}",
        "rig_mcp",
        "rig_live_agent",
        "rig_git",
        "rig_home_ownership",
    ]
    assert len(AUTHORIZE_PLAYBOOK.splitlines()) < 80
    for role in [n for n in names if n.startswith("rig_")] + list(play["vars"]["_rig_agent_roles"].values()):
        assert os.path.isfile(os.path.join(ROLES_DIR, role, "tasks", "authorize.yml")), role


def test_authorize_roles_never_install_packages():
    """Packages belong to the cached image (provision); authorize is for credentials and per-run state."""
    from _roles import authorize_tasks

    assert not [t for t in authorize_tasks() if "apt" in t]


def test_each_agent_role_resolves_only_its_own_key():
    for role, var in (("rig_opencode", "opencode"), ("rig_claude_code", "claude"), ("rig_cursor_cli", "cursor")):
        facts = [t["set_fact"] for t in role_tasks(role, "authorize") if "set_fact" in t]
        assert facts
        assert all(set(f) == {f"rig_{var}_api_key"} for f in facts), role


def test_materialize_and_destroy_playbooks_are_thin_role_calls():
    for playbook, role, stage in (
        (MATERIALIZE_PLAYBOOK, "rig_workspace_repos", "materialize"),
        (DESTROY_PLAYBOOK, "rig_destroy", "destroy"),
    ):
        (task,) = yaml.safe_load(playbook)[0]["tasks"]
        assert task["include_role"] == {"name": role, "tasks_from": stage}
        assert task["tags"] == ["always"]
        assert os.path.isfile(os.path.join(ROLES_DIR, role, "tasks", f"{stage}.yml"))
        assert len(playbook.splitlines()) < 30


def test_the_clone_role_makes_sure_the_workspace_directory_exists():
    """It must run alone (`rig apply --materialize`), so it cannot rely on provision's directory task."""
    names = [t["name"] for t in role_tasks("rig_workspace_repos", "materialize")]
    assert names == ["Ensure project workspace directory exists", "Clone configured git repositories"]
    with open(os.path.join(ROLES_DIR, "rig_workspace_repos", "defaults", "main.yml")) as handle:
        assert yaml.safe_load(handle)["workspace_root"] == "/workspace"


def test_the_clone_group_carries_the_git_tag_users_already_pass_to_apply():
    clone = next(t for t in role_tasks("rig_workspace_repos", "materialize") if "git" in t)
    assert {"git", "rig_workspace_repos_clone", "rig_workspace_repos"} <= clone["_tags"]


def test_roles_search_path_puts_workspace_roles_first_and_bundled_last():
    parts = roles_search_path("/ws/.rig").split(os.pathsep)
    assert parts == ["/ws/.rig/local_roles", "/ws/.rig/roles", BUNDLED_ROLES_DIR]


# ---- tags and skip tags ------------------------------------------------------------------------


def test_configured_skip_tags_accepts_a_list_or_a_string():
    assert configured_skip_tags(None) == []
    assert configured_skip_tags({}) == []
    assert configured_skip_tags({"ansible": "nope"}) == []
    assert configured_skip_tags({"ansible": {"skip_tags": ["rig_git", "rig_opencode, rig_cursor_cli"]}}) == [
        "rig_git",
        "rig_opencode",
        "rig_cursor_cli",
    ]
    assert configured_skip_tags({"ansible": {"skip_tags": "rig_git"}}) == ["rig_git"]
    assert configured_skip_tags({"ansible": {"skip_tags": 3}}) == []


def test_ansible_tag_args_merge_the_config_with_the_command_line():
    assert ansible_tag_args() == []
    assert ansible_tag_args(["a,b"]) == ["--tags", "a,b"]
    cfg = {"ansible": {"skip_tags": ["rig_git"]}}
    assert ansible_tag_args(None, None, cfg) == ["--skip-tags", "rig_git"]
    assert ansible_tag_args(["x"], ["rig_mcp"], cfg) == ["--tags", "x", "--skip-tags", "rig_git,rig_mcp"]


@pytest.mark.skipif(shutil.which("ansible-playbook") is None, reason="ansible-playbook not installed")
@pytest.mark.parametrize(
    ("stage", "playbook"),
    [
        ("provision", PROVISION_PLAYBOOK),
        ("authorize", AUTHORIZE_PLAYBOOK),
        ("materialize", MATERIALIZE_PLAYBOOK),
        ("destroy", DESTROY_PLAYBOOK),
    ],
)
def test_the_generated_playbooks_pass_ansible_syntax_check(tmp_path, stage, playbook):
    (tmp_path / f"{stage}.yml").write_text(playbook.lstrip())
    (tmp_path / "inv.yml").write_text("all:\n  hosts:\n    remote_vm: {ansible_host: 127.0.0.1}\n")
    env = {**os.environ, "ANSIBLE_ROLES_PATH": BUNDLED_ROLES_DIR, "ANSIBLE_LOCAL_TEMP": str(tmp_path / "tmp")}
    result = subprocess.run(
        ["ansible-playbook", "-i", "inv.yml", f"{stage}.yml", "--syntax-check", "-e", "rig_user=dev"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("ansible-playbook") is None, reason="ansible-playbook not installed")
def test_tags_and_skip_tags_select_tasks_inside_an_included_role(tmp_path):
    """The playbook pattern: include_role tagged `always`, tags living inside the role."""
    role = tmp_path / "roles" / "demo" / "tasks"
    role.mkdir(parents=True)
    (role / "install.yml").write_text(
        "- tags: [demo]\n  block:\n"
        "    - {debug: {msg: group-a}, tags: [demo_a]}\n"
        "    - {debug: {msg: group-b}, tags: [demo_b]}\n"
    )
    (tmp_path / "p.yml").write_text(
        "- hosts: localhost\n  gather_facts: false\n  tasks:\n"
        "    - {include_role: {name: demo, tasks_from: install}, tags: [always]}\n"
    )
    env = {**os.environ, "ANSIBLE_ROLES_PATH": str(tmp_path / "roles"), "ANSIBLE_LOCAL_TEMP": str(tmp_path / "tmp")}

    def ran(*extra):
        out = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", "-c", "local", "p.yml", *extra],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        ).stdout
        return {name for name in ("group-a", "group-b") if f'"msg": "{name}"' in out}

    assert ran() == {"group-a", "group-b"}
    assert ran("--tags", "demo_a") == {"group-a"}
    assert ran("--skip-tags", "demo_b") == {"group-a"}
    assert ran("--skip-tags", "demo") == set()


# ---- snapshot hash -----------------------------------------------------------------------------


def _copy_roles(tmp_path):
    dest = tmp_path / "roles"
    shutil.copytree(ROLES_DIR, dest)
    return dest


def test_hash_follows_the_cached_stage_files_of_a_role_but_not_authorize(tmp_path, monkeypatch):
    roles = _copy_roles(tmp_path)
    monkeypatch.setattr(snapshots, "BUNDLED_ROLES_DIR", str(roles))
    base = snapshots.bundled_roles_digest()
    assert base

    (roles / "rig_base" / "tasks" / "authorize.yml").write_text("- debug: {msg: x}\n")
    (roles / "rig_base" / "tasks" / "materialize.yml").write_text("- debug: {msg: x}\n")
    assert snapshots.bundled_roles_digest() == base

    with open(roles / "rig_base" / "tasks" / "install.yml", "a") as handle:
        handle.write("# edited\n")
    changed = snapshots.bundled_roles_digest()
    assert changed != base

    (roles / "rig_base" / "defaults" / "main.yml").write_text("rig_base_packages: [curl]\n")
    assert snapshots.bundled_roles_digest() != changed


def test_hash_ignores_roles_that_only_have_later_stages(tmp_path, monkeypatch):
    roles = _copy_roles(tmp_path)
    (roles / "rig_later" / "tasks").mkdir(parents=True)
    monkeypatch.setattr(snapshots, "BUNDLED_ROLES_DIR", str(roles))
    (roles / "rig_later" / "tasks" / "authorize.yml").write_text("- debug: {msg: a}\n")
    before = snapshots.bundled_roles_digest()
    (roles / "rig_later" / "tasks" / "authorize.yml").write_text("- debug: {msg: b}\n")
    assert snapshots.bundled_roles_digest() == before


def test_hash_includes_a_workspace_role_that_shadows_a_bundled_one(tmp_path):
    base = snapshots.bundled_roles_digest(str(tmp_path))
    shadow = tmp_path / ".rig" / "local_roles" / "rig_base" / "tasks"
    shadow.mkdir(parents=True)
    (shadow / "install.yml").write_text("- debug: {msg: mine}\n")
    assert snapshots.bundled_roles_digest(str(tmp_path)) != base
    # Other roles in local_roles are the user's own and stay out of the hash.
    other = tmp_path / ".rig" / "local_roles" / "mine" / "tasks"
    other.mkdir(parents=True)
    (other / "install.yml").write_text("- debug: {msg: mine}\n")
    with_other = snapshots.bundled_roles_digest(str(tmp_path))
    (other / "install.yml").write_text("- debug: {msg: changed}\n")
    assert snapshots.bundled_roles_digest(str(tmp_path)) == with_other


def test_system_hash_changes_with_the_role_files_and_the_skip_list(tmp_path, monkeypatch):
    project = ProjectConfig(str(tmp_path))
    assert project.init(name="ws", agent_type="opencode")
    project.load()
    plain = snapshots.system_content_hash(project=project, project_dir=str(tmp_path))
    assert snapshots.system_content_hash(project=project, project_dir=str(tmp_path)) == plain

    project.config["ansible"] = {"skip_tags": ["rig_git"]}
    skipped = snapshots.system_content_hash(project=project, project_dir=str(tmp_path))
    assert skipped != plain
    project.config["ansible"] = {"skip_tags": ["rig_git", "rig_git"]}
    assert snapshots.system_content_hash(project=project, project_dir=str(tmp_path)) == skipped

    roles = _copy_roles(tmp_path)
    monkeypatch.setattr(snapshots, "BUNDLED_ROLES_DIR", str(roles))
    with open(roles / "rig_code_server" / "tasks" / "install.yml", "a") as handle:
        handle.write("# edited\n")
    assert snapshots.system_content_hash(project=project, project_dir=str(tmp_path)) != skipped


# ---- runs limited by tags never touch the snapshot cache -----------------------------------------


def test_boot_image_ignores_snapshots_for_a_run_limited_by_skip_tags(tmp_path):
    env = RemoteDevEnvironment(name="demo")
    env.manager = MagicMock()
    env.bypass_snapshots = True
    decision = env.resolve_boot_image(project_dir=str(tmp_path))
    assert decision["source"] == "base"
    assert decision["skip_playbooks"] == ()
    env.manager.list_snapshots.assert_not_called()


def _run_apply(tmp_path, monkeypatch, **kwargs):
    project = ProjectConfig(str(tmp_path))
    assert project.init(name="ws", agent_type="opencode")
    env = RemoteDevEnvironment(name="ws")
    env.vm_ip = "10.0.0.9"
    env.ssh_key_name = "ws-tags-key"
    key = "/tmp/ws-tags-key"
    with open(key, "w", encoding="utf-8") as handle:
        handle.write("fake\n")
    snapshots_taken = []
    monkeypatch.setattr(
        RemoteDevEnvironment,
        "_snapshot_after_playbook",
        lambda self, kind, project: snapshots_taken.append(kind) or True,
    )
    seen = []

    def fake_run(self, path, **kw):
        seen.append((os.path.basename(path), kw.get("ansible_args")))
        return True

    monkeypatch.setattr(remote_dev.AnsibleManager, "_run_one_playbook", fake_run)
    monkeypatch.setattr(remote_dev.AnsibleManager, "setup_bootstrap", lambda self, *a, **k: True)
    try:
        ok = env.apply_playbooks(
            project_dir=str(tmp_path), stream=False, playbooks=("provision", "configure"), **kwargs
        )
    finally:
        os.remove(key)
    return ok, seen, snapshots_taken


def test_apply_caches_a_snapshot_after_configure_without_tag_flags(tmp_path, monkeypatch):
    ok, seen, snapshots_taken = _run_apply(tmp_path, monkeypatch)
    assert ok
    assert snapshots_taken == ["configure"]
    assert all(args is None for _name, args in seen)


def test_apply_with_skip_tags_passes_them_on_and_caches_nothing(tmp_path, monkeypatch):
    ok, seen, snapshots_taken = _run_apply(tmp_path, monkeypatch, ansible_skip_tags=["rig_git"])
    assert ok
    assert snapshots_taken == []
    assert seen == [("provision.yml", ["--skip-tags", "rig_git"]), ("configure.yml", ["--skip-tags", "rig_git"])]


def test_apply_with_tags_caches_nothing(tmp_path, monkeypatch):
    ok, _seen, snapshots_taken = _run_apply(tmp_path, monkeypatch, ansible_tags=["rig_base"])
    assert ok
    assert snapshots_taken == []


def test_apply_with_only_the_config_skip_list_still_caches_its_own_snapshot(tmp_path, monkeypatch):
    project = ProjectConfig(str(tmp_path))
    assert project.init(name="ws", agent_type="opencode")
    path = tmp_path / "rig.yml"
    path.write_text(path.read_text() + "\nansible:\n  skip_tags: [rig_git]\n")
    ok, seen, snapshots_taken = _run_apply(tmp_path, monkeypatch)
    assert ok
    assert snapshots_taken == ["configure"]
    assert seen[0][1] == ["--skip-tags", "rig_git"]


# ---- the command line ----------------------------------------------------------------------------


def _patched_cli():
    from unittest.mock import patch

    return (
        patch("cli.main._load_credentials"),
        patch.dict(os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False),
        patch("cli.main.StorageManager", side_effect=ValueError("no storage")),
    )


def test_apply_and_delete_pass_skip_tags_through():
    from unittest.mock import patch

    from cli.main import main

    a, b, c = _patched_cli()
    with (
        a,
        b,
        c,
        patch("cli.main.resolve_env_name", return_value="dev-skip"),
        patch("cli.main.RemoteDevEnvironment") as env_cls,
        patch("cli.main.remember_last_env"),
        patch("cli.main.clear_last_env"),
        patch("cli.main.clear_last_session"),
        patch("cli.main.os.path.exists", return_value=True),
    ):
        instance = MagicMock()
        instance.name = "dev-skip"
        instance.vm_ip = "10.0.0.2"
        instance.ssh_key_name = "dev-skip-key"
        instance.resolve_resources.return_value = True
        instance.apply_playbooks.return_value = True
        env_cls.return_value = instance

        assert main(["apply"]) == 0
        assert instance.apply_playbooks.call_args.kwargs["ansible_skip_tags"] is None
        assert main(["apply", "--skip-tags", "rig_git,rig_mcp", "--skip-tags", "rig_cursor_cli"]) == 0
        assert instance.apply_playbooks.call_args.kwargs["ansible_skip_tags"] == ["rig_git,rig_mcp", "rig_cursor_cli"]

        assert main(["delete", "--name", "dev-skip", "--skip-tags", "cleanup"]) == 0
        assert instance.cleanup.call_args.kwargs["ansible_skip_tags"] == ["cleanup"]


def test_create_with_skip_tags_boots_from_the_base_image_and_forwards_them(tmp_path, monkeypatch):
    from cli.main import main

    monkeypatch.chdir(tmp_path)
    assert ProjectConfig(str(tmp_path)).init(name="ws", agent_type="opencode")
    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "token")
    seen = {}

    class FakeEnv:
        def __init__(self, name=None, config=None, storage_manager=None):
            self.name = name or "dev-x"
            self.bypass_snapshots = False

        def setup(self, project_dir=None):
            seen["bypass_at_setup"] = self.bypass_snapshots
            return True

        def provision(self, **kwargs):
            seen["provision"] = kwargs
            return False  # stop before attaching

    from unittest.mock import patch

    with (
        patch("cli.main.RemoteDevEnvironment", FakeEnv),
        patch("cli.main.StorageManager", side_effect=ValueError("no storage")),
        patch("cli.main.remember_last_env"),
    ):
        assert main(["create", "--name", "x", "--skip-tags", "rig_git"]) == 1
        assert seen["bypass_at_setup"] is True
        assert seen["provision"]["ansible_skip_tags"] == ["rig_git"]
        assert main(["create", "--name", "x"]) == 1
        assert seen["bypass_at_setup"] is False
        assert seen["provision"]["ansible_skip_tags"] is None
