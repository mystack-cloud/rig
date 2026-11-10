"""Tests for rig create provisioning + agent attach wiring."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.main import main
from cli.remote_dev import AnsibleManager, ProjectConfig, RemoteDevEnvironment


def test_agent_remote_command_opencode_claude_and_cursor():
    opencode = RemoteDevEnvironment.agent_remote_command("opencode")
    assert "opencode" in opencode
    assert "credentials.env" in opencode
    assert "mkdir -p /workspace" in opencode
    assert "cd /workspace" in opencode

    claude = RemoteDevEnvironment.agent_remote_command("claude")
    assert "claude" in claude
    assert ".local/bin" in claude
    assert "mkdir -p /workspace" in claude

    cursor = RemoteDevEnvironment.agent_remote_command("cursor")
    assert "exec agent" in cursor
    assert "/root/.cursor/credentials.env" in cursor
    assert "mkdir -p /workspace" in cursor


def test_provision_requires_rig_yml(tmp_path):
    env = RemoteDevEnvironment(name="demo")
    env.vm_ip = "1.2.3.4"
    env.ssh_key_name = "demo-key"
    assert env.provision(project_dir=str(tmp_path), stream=False) is False


def test_provision_runs_playbooks_in_order(tmp_path):
    project_dir = tmp_path / "ws"
    project_dir.mkdir()
    cfg = ProjectConfig(str(project_dir))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="ws")
    env.vm_ip = "10.0.0.8"
    env.ssh_key_name = "ws-key"
    real_key = "/tmp/ws-key"
    with open(real_key, "w", encoding="utf-8") as handle:
        handle.write("fake\n")

    calls = []

    def fake_run_one(self, playbook_path, extra_vars=None, vault_password=None, stream=False):
        calls.append({"path": playbook_path, "stream": stream})
        return True

    try:
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run_one
        ):
            assert env.provision(project_dir=str(project_dir), stream=True) is True
    finally:
        try:
            os.remove(real_key)
        except OSError:
            pass

    assert len(calls) == 4
    assert calls[0]["stream"] is True
    assert calls[0]["path"].endswith("provision.yml")
    assert calls[1]["path"].endswith("configure.yml")
    assert calls[2]["path"].endswith("authorize.yml")
    assert calls[3]["path"].endswith("materialize.yml")


def test_playbook_lifecycle_line_hides_skip_and_apply_detail(tmp_path, capsys):
    from cli import log as rig_log

    project_dir = tmp_path / "ws"
    project_dir.mkdir()
    cfg = ProjectConfig(str(project_dir))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="ws-log")
    env.vm_ip = "10.0.0.8"
    env.ssh_key_name = "ws-log-key"
    env.skip_playbooks = ("provision", "configure")
    env.boot_source = "system"
    env.boot_snapshot_description = "rig-0.1.1-abc"
    real_key = "/tmp/ws-log-key"
    with open(real_key, "w", encoding="utf-8") as handle:
        handle.write("fake\n")

    def fake_run_one(self, playbook_path, extra_vars=None, vault_password=None, stream=False):
        return True

    try:
        rig_log.configure_logging()
        capsys.readouterr()
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run_one
        ):
            assert env.provision(project_dir=str(project_dir), stream=True) is True
        quiet = capsys.readouterr().err
        assert "Skipping playbook" not in quiet
        assert "Applying " not in quiet
        assert "Playbooks complete" not in quiet
        assert "Playbook succeeded" not in quiet
        assert "Running authorize playbook" in quiet
        assert quiet.count("Running authorize playbook") == 1
        assert "/authorize.yml" in quiet
        assert "Running materialize playbook" in quiet

        rig_log.configure_logging(verbose=True)
        capsys.readouterr()
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run_one
        ):
            assert env.provision(project_dir=str(project_dir), stream=True) is True
        verbose = capsys.readouterr().err
        assert "Skipping playbook" in verbose
        assert "Applying authorize+materialize" in verbose
    finally:
        rig_log.configure_logging()
        try:
            os.remove(real_key)
        except OSError:
            pass


def test_apply_playbooks_workspace_only_by_default(tmp_path):
    project_dir = tmp_path / "ws"
    project_dir.mkdir()
    cfg = ProjectConfig(str(project_dir))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="ws-apply")
    env.vm_ip = "10.0.0.9"
    env.ssh_key_name = "ws-apply-key"
    real_key = "/tmp/ws-apply-key"
    with open(real_key, "w", encoding="utf-8") as handle:
        handle.write("fake\n")

    calls = []

    def fake_run_one(self, playbook_path, extra_vars=None, vault_password=None, stream=False):
        calls.append(playbook_path)
        return True

    try:
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run_one
        ):
            assert env.apply_playbooks(
                project_dir=str(project_dir),
                stream=False,
                playbooks=("materialize",),
            ) is True
            assert env.apply_playbooks(
                project_dir=str(project_dir),
                stream=False,
                playbooks=("provision",),
            ) is True
    finally:
        try:
            os.remove(real_key)
        except OSError:
            pass

    assert len(calls) == 2
    assert calls[0].endswith("materialize.yml")
    assert calls[1].endswith("provision.yml")


def test_apply_help_mentions_lifecycle_flags(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["apply", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--provision" in help_text
    assert "--authorize" in help_text
    assert "--materialize" in help_text.lower()
    assert "--finalize" in help_text
    assert "--destroy" in help_text
    assert "--tags" in help_text


def test_apply_command_passes_materialize_tags():
    with patch("cli.main._load_credentials"), patch.dict(
        os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}, clear=False
    ), patch("cli.main.StorageManager", side_effect=ValueError("no storage")), patch(
        "cli.main.resolve_env_name", return_value="dev-apply"
    ), patch("cli.main.RemoteDevEnvironment") as env_cls, patch(
        "cli.main.remember_last_env"
    ), patch("cli.main.os.path.exists", return_value=True):
        instance = MagicMock()
        instance.name = "dev-apply"
        instance.vm_ip = "10.0.0.2"
        instance.ssh_key_name = "dev-apply-key"
        instance.resolve_resources.return_value = True
        instance.apply_playbooks.return_value = True
        env_cls.return_value = instance

        assert main(["apply"]) == 0
        kwargs = instance.apply_playbooks.call_args.kwargs
        assert kwargs["playbooks"] == ("authorize", "materialize")

        assert main(["apply", "--provision"]) == 0
        kwargs = instance.apply_playbooks.call_args.kwargs
        assert kwargs["playbooks"] == ("provision", "configure", "authorize")

        assert main(["apply", "--authorize"]) == 0
        kwargs = instance.apply_playbooks.call_args.kwargs
        assert kwargs["playbooks"] == ("authorize",)

        assert main(["apply", "--materialize", "--tags", "git,clone", "--tags", "deps"]) == 0
        kwargs = instance.apply_playbooks.call_args.kwargs
        assert kwargs["playbooks"] == ("authorize", "materialize")
        assert kwargs["ansible_tags"] == ["git,clone", "deps"]

        assert main(["apply", "--finalize"]) == 0
        kwargs = instance.apply_playbooks.call_args.kwargs
        assert kwargs["playbooks"] == ("finalize",)

        assert main(["apply", "--destroy", "--tags", "cleanup"]) == 0
        kwargs = instance.apply_playbooks.call_args.kwargs
        assert kwargs["playbooks"] == ("destroy",)
        assert kwargs["ansible_tags"] == ["cleanup"]

        assert main(["apply", "--finalize", "--destroy"]) == 0
        kwargs = instance.apply_playbooks.call_args.kwargs
        assert kwargs["playbooks"] == ("finalize", "destroy")


def test_materialize_playbook_receives_ansible_tags(tmp_path):
    project_dir = tmp_path / "ws"
    project_dir.mkdir()
    cfg = ProjectConfig(str(project_dir))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="ws-tags")
    env.vm_ip = "10.0.0.8"
    env.ssh_key_name = "ws-tags-key"
    real_key = "/tmp/ws-tags-key"
    with open(real_key, "w", encoding="utf-8") as handle:
        handle.write("fake\n")

    calls = []

    def fake_run_one(self, playbook_path, extra_vars=None, vault_password=None, stream=False, ansible_args=None):
        calls.append({"path": playbook_path, "ansible_args": ansible_args})
        return True

    try:
        with patch.object(AnsibleManager, "setup_bootstrap", return_value=True), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run_one
        ):
            assert env.apply_playbooks(
                project_dir=str(project_dir),
                stream=False,
                playbooks=("authorize", "materialize"),
                ansible_tags=["git,clone", "deps"],
            ) is True
            assert calls[0]["path"].endswith("authorize.yml")
            assert calls[0]["ansible_args"] == ["--tags", "git,clone,deps"]
            assert calls[1]["path"].endswith("materialize.yml")
            assert calls[1]["ansible_args"] == ["--tags", "git,clone,deps"]

            calls.clear()
            assert env.apply_playbooks(
                project_dir=str(project_dir),
                stream=False,
                playbooks=("authorize", "finalize", "destroy"),
                ansible_tags=["cleanup"],
            ) is True
    finally:
        try:
            os.remove(real_key)
        except OSError:
            pass

    assert [os.path.basename(call["path"]) for call in calls] == [
        "authorize.yml",
        "finalize.yml",
        "destroy.yml",
    ]
    assert calls[0]["ansible_args"] == ["--tags", "cleanup"]
    assert calls[1]["ansible_args"] == ["--tags", "cleanup"]
    assert calls[2]["ansible_args"] == ["--tags", "cleanup"]


def _attach_ssh_command(captured_cmd):
    """Extract the ssh argv from either a raw ssh run or a ``script -c`` wrapper."""
    if not captured_cmd:
        return []
    if captured_cmd[0] == "ssh" or captured_cmd[0].endswith("/ssh"):
        return captured_cmd
    # util-linux: script -q -c 'ssh …' logfile
    if "-c" in captured_cmd:
        idx = captured_cmd.index("-c")
        if idx + 1 < len(captured_cmd):
            import shlex
            return shlex.split(captured_cmd[idx + 1])
    # BSD: script -q logfile ssh …
    for i, part in enumerate(captured_cmd):
        if part == "ssh" or part.endswith("/ssh"):
            return captured_cmd[i:]
    return captured_cmd


def test_attach_to_agent_builds_ssh_tty_command(tmp_path, capsys):
    from cli import log as rig_log

    rig_log.configure_logging()
    env = RemoteDevEnvironment(name="attach-demo")
    env.vm_ip = "203.0.113.10"
    env.ssh_key_name = "attach-demo"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0)

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        code = env.attach_to_agent(agent_type="claude")

    assert code == 0
    err = capsys.readouterr().err
    assert "Attaching to claude on attach-demo" in err
    assert "203.0.113.10" not in err
    ssh_cmd = _attach_ssh_command(captured["cmd"])
    assert ssh_cmd[0] == "ssh" or ssh_cmd[0].endswith("/ssh")
    assert "-tt" in ssh_cmd
    assert "root@203.0.113.10" in ssh_cmd
    assert "LogLevel=ERROR" in ssh_cmd
    assert any("exec claude" in part for part in ssh_cmd)

    rig_log.configure_logging(verbosity=1)
    try:
        with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
            assert env.attach_to_agent(agent_type="claude") == 0
        ssh_cmd = _attach_ssh_command(captured["cmd"])
        assert "LogLevel=INFO" in ssh_cmd
    finally:
        rig_log.configure_logging()


def test_open_ssh_starts_a_workspace_shell():
    env = RemoteDevEnvironment(name="dev-ssh")
    env.vm_ip = "203.0.113.20"
    env.ssh_key_name = "dev-ssh-key"
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0)

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        assert env.open_ssh() == 0
    ssh_cmd = _attach_ssh_command(captured["cmd"])
    remote = ssh_cmd[-1]
    assert "cd /workspace" in remote
    assert "exec bash -l" in remote
    assert "root@203.0.113.20" in ssh_cmd

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        assert env.open_ssh(remote_command="uptime") == 0
    ssh_cmd = _attach_ssh_command(captured["cmd"])
    assert ssh_cmd[-1] == "uptime"

    bare = RemoteDevEnvironment(name="no-ip")
    assert bare.open_ssh() == 1
    with patch("cli.remote_dev.RemoteDevEnvironment._run_attach_ssh", side_effect=FileNotFoundError):
        assert env.open_ssh() == 1
    with patch("cli.remote_dev.RemoteDevEnvironment._run_attach_ssh", side_effect=KeyboardInterrupt):
        assert env.open_ssh() == 130


def test_attach_to_agent_forwards_extra_args():
    env = RemoteDevEnvironment(name="attach-args")
    env.vm_ip = "203.0.113.11"
    env.ssh_key_name = "attach-args"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0)

    with patch("cli.remote_dev.subprocess.run", side_effect=fake_run):
        code = env.attach_to_agent(
            agent_type="opencode",
            agent_args=["--continue", "--prompt", "pick up where we left off"],
        )

    assert code == 0
    ssh_cmd = _attach_ssh_command(captured["cmd"])
    remote = ssh_cmd[-1]
    assert "exec opencode" in remote
    assert "--continue" in remote
    assert "pick" in remote


def test_agent_remote_command_quotes_args():
    cmd = RemoteDevEnvironment.agent_remote_command(
        "claude",
        extra_args=["--resume", "sess id"],
    )
    assert "exec claude" in cmd
    assert "--resume" in cmd
    assert "'sess id'" in cmd or '"sess id"' in cmd


def test_create_help_mentions_attach(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["create", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "--no-attach" in help_text
    assert "attach" in help_text.lower()
    assert "--availability-zone" in help_text


def test_env_reads_availability_zone_from_config():
    env = RemoteDevEnvironment(
        name="az-demo",
        config={"vm": {"type": "cax21", "availability_zone": "hel1"}},
    )
    assert env.host_type == "vm"
    assert env.vm_type == "cax21"
    assert env.region == "hel1"


def test_host_block_defaults_kind_and_reads_legacy_vm():
    from cli.remote_dev import host_block

    fresh = host_block({})
    assert fresh["provider"] == "hetzner"
    assert fresh["type"] == "vm"
    assert fresh["server_type"] == "cax11"
    assert fresh["availability_zone"] == "hel1"

    legacy = host_block({"vm": {"type": "cax31", "region": "nbg1"}})
    assert legacy["provider"] == "hetzner"
    assert legacy["type"] == "vm"
    assert legacy["server_type"] == "cax31"
    assert legacy["availability_zone"] == "nbg1"
    assert "region" not in legacy

    explicit = host_block({
        "host": {
            "type": "vm",
            "server_type": "cax21",
            "availability_zone": "fsn1",
        },
        "vm": {"type": "cax11"},
    })
    assert explicit["type"] == "vm"
    assert explicit["server_type"] == "cax21"
    assert explicit["availability_zone"] == "fsn1"


def test_env_reads_host_block():
    env = RemoteDevEnvironment(
        name="host-demo",
        config={
            "host": {
                "server_type": "cax41",
                "availability_zone": "fsn1",
            }
        },
    )
    assert env.provider == "hetzner"
    assert env.host_type == "vm"
    assert env.vm_type == "cax41"
    assert env.region == "fsn1"


def test_migrate_host_leaves_non_dict_and_absent_block(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    cfg.config = "nope"
    cfg._migrate_host()
    assert cfg.config == "nope"

    cfg.config = {"name": "ws"}
    cfg._migrate_host()
    assert "host" not in cfg.config
    assert "vm" not in cfg.config


def test_reinit_renames_vm_block_to_host(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    cfg.config.pop("host")
    cfg.config["vm"] = {"type": "cax21", "region": "fsn1", "image": "debian-12"}
    cfg.save(write_vars=False)

    again = ProjectConfig(str(tmp_path))
    assert again.init(name="ws")
    assert again.config["host"]["type"] == "vm"
    assert again.config["host"]["server_type"] == "cax21"
    assert again.config["host"]["availability_zone"] == "fsn1"
    assert again.config["host"]["image"] == "debian-12"
    assert "vm" not in again.config


def test_env_falls_back_to_legacy_region_key():
    env = RemoteDevEnvironment(
        name="legacy-region",
        config={"vm": {"type": "cax11", "region": "nbg1"}},
    )
    assert env.region == "nbg1"


def test_create_uses_vm_from_rig_yml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    cfg.set("host.server_type", "cax31")
    cfg.set("host.availability_zone", "hel1")
    cfg.save(write_vars=False)

    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "token")
    captured = {}

    class FakeEnv:
        def __init__(self, name=None, config=None, storage_manager=None):
            captured["config"] = config
            self.name = name or "dev-x"
            self.config = config or {}

        def setup(self, project_dir=None):
            return False

    with patch("cli.main.RemoteDevEnvironment", FakeEnv), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ):
        assert main(["create", "--name", "from-yml"]) == 1

    assert captured["config"]["host"]["provider"] == "hetzner"
    assert captured["config"]["host"]["type"] == "vm"
    assert captured["config"]["host"]["server_type"] == "cax31"
    assert captured["config"]["host"]["availability_zone"] == "hel1"


def test_run_all_playbooks_logs_command(tmp_path, capsys):
    from cli import log as rig_log

    cfg = ProjectConfig(str(tmp_path))
    cfg.init(name="play")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    manager.inventory_path = str(tmp_path / "inventory.yml")
    (tmp_path / "inventory.yml").write_text("all: {}\n")

    rig_log.configure_logging(verbose=True)
    with patch(
        "cli.remote_dev.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="ok: [remote_vm]\n", stderr=""),
    ):
        assert manager.run_all_playbooks(stream=False) is True

    err = capsys.readouterr().err
    assert "ansible-playbook" in err
    assert "provision.yml" in err
    assert "@" in err  # -e @vars.yml


def test_setup_paths_are_debug_only(tmp_path, capsys):
    from cli import log as rig_log

    cfg = ProjectConfig(str(tmp_path))
    cfg.init(name="paths", agent_type="opencode")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))

    rig_log.configure_logging()
    capsys.readouterr()
    assert manager.setup_inventory("1.2.3.4", "/tmp/key")
    assert manager.setup_playbook()
    quiet = capsys.readouterr()
    assert "inventory.yml" not in quiet.out
    assert "provision.yml" not in quiet.out
    assert "materialize.yml" not in quiet.out
    assert "inventory.yml" not in quiet.err

    rig_log.configure_logging(verbose=True)
    capsys.readouterr()
    assert manager.setup_inventory("1.2.3.4", "/tmp/key")
    assert manager.setup_playbook()
    verbose = capsys.readouterr()
    assert "inventory.yml" in verbose.err
    assert "provision.yml" in verbose.err
    assert "materialize.yml" in verbose.err
    rig_log.configure_logging()


def test_setup_inventory_excludes_vault_secrets(tmp_path):
    from cli.remote_dev import VaultEncrypted

    cfg = ProjectConfig(str(tmp_path))
    cfg.init(name="inv")
    cfg.vars_config["github_pat"] = VaultEncrypted("$ANSIBLE_VAULT;1.1;AES256\nabc")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    assert manager.setup_inventory("1.2.3.4", "/tmp/key")
    text = (tmp_path / ".rig" / "inventory.yml").read_text()
    assert "remote_vm" in text
    assert "1.2.3.4" in text
    assert "github_pat" not in text
    assert "VaultEncrypted" not in text


def test_playbook_fails_when_no_hosts_matched(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    cfg.init(name="play")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    manager.inventory_path = str(tmp_path / ".rig" / "inventory.yml")
    manager.setup_inventory("1.2.3.4", "/tmp/key")

    fake = MagicMock(
        returncode=0,
        stdout="skipping: no hosts matched\nPLAY RECAP\n",
        stderr="",
    )
    with patch("cli.remote_dev.subprocess.run", return_value=fake):
        assert manager.run_playbook(stream=False) is False


_PLAYBOOK_OUTPUT = """\
PLAY [Provision] ********************************************************

TASK [Gathering Facts] **************************************************
ok: [remote_vm]

TASK [Install git] ******************************************************
changed: [remote_vm]

TASK [Clone private repo] ***********************************************
fatal: [remote_vm]: FAILED! => {
    "msg": "Authentication failed secret-token-value"
}

PLAY RECAP **************************************************************
remote_vm                  : ok=2    changed=1    unreachable=0    failed=1
"""


def test_playbook_failure_excerpt_keeps_the_failed_task():
    from cli.remote_dev import ansible_verbosity_args, playbook_failure_excerpt
    from cli import log as rig_log

    excerpt = playbook_failure_excerpt(_PLAYBOOK_OUTPUT)
    assert "Clone private repo" in excerpt
    assert "Authentication failed" in excerpt
    assert "PLAY RECAP" in excerpt
    assert "Gathering Facts" not in excerpt
    assert "Install git" not in excerpt
    assert playbook_failure_excerpt("") == ""
    assert playbook_failure_excerpt("   \n") == ""
    assert playbook_failure_excerpt("ERROR! syntax\n") == "ERROR! syntax\n"
    errored = playbook_failure_excerpt(
        "TASK [Lookup] *****************************************************\n"
        "[ERROR]: the role 'missing' was not found\n"
        "unreachable: [remote_vm]: UNREACHABLE!\n"
    )
    assert "the role 'missing'" in errored
    assert "UNREACHABLE" in errored

    recap_only = (
        "TASK [Last step] ************************************************\n"
        "ok: [remote_vm]\n\n"
        "PLAY RECAP ******************************************************\n"
        "remote_vm : ok=1 failed=1\n"
    )
    fallback = playbook_failure_excerpt(recap_only)
    assert "Last step" in fallback
    assert "PLAY RECAP" in fallback

    rig_log.configure_logging()
    assert ansible_verbosity_args() == []
    rig_log.configure_logging(verbosity=1)
    assert ansible_verbosity_args() == []
    rig_log.configure_logging(verbosity=2)
    assert ansible_verbosity_args() == ["-v"]
    rig_log.configure_logging(verbosity=4)
    assert ansible_verbosity_args() == ["-vvv"]
    rig_log.configure_logging()


def _fake_playbook_process(lines, returncode):
    class FakeStdout:
        def __iter__(self_inner):
            yield from lines

    class Proc:
        stdout = FakeStdout()

        def wait(self, timeout=None):
            return returncode

    return Proc()


def test_default_playbook_log_is_lifecycle_until_a_task_fails(tmp_path, monkeypatch, capsys):
    from cli import log as rig_log

    rig_log.configure_logging()
    cfg = ProjectConfig(str(tmp_path))
    cfg.init(name="quiet", agent_type="opencode")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    manager.setup_inventory("1.2.3.4", "/key")
    playbook = tmp_path / "provision.yml"
    playbook.write_text("- hosts: all\n")
    monkeypatch.setattr("cli.vault_keys.unlock_data_keys", lambda *a, **k: None)
    capsys.readouterr()

    monkeypatch.setattr(
        "cli.remote_dev.subprocess.Popen",
        lambda *a, **k: _fake_playbook_process(["ok: [remote_vm]\n"], 0),
    )
    assert manager._run_one_playbook(str(playbook), stream=True) is True
    streamed = capsys.readouterr()
    assert streamed.out == ""
    assert "Playbook succeeded" not in streamed.err
    assert "ok: [remote_vm]" not in streamed.err

    monkeypatch.setattr(
        "cli.remote_dev.subprocess.Popen",
        lambda *a, **k: _fake_playbook_process(_PLAYBOOK_OUTPUT.splitlines(keepends=True), 2),
    )
    manager.extra_vars = {"github_pat": "secret-token-value"}
    assert manager._run_one_playbook(str(playbook), stream=True) is False
    failed = capsys.readouterr()
    assert failed.out == ""
    assert "Gathering Facts" not in failed.err
    assert "Clone private repo" in failed.err
    assert "PLAY RECAP" in failed.err
    assert "secret-token-value" not in failed.err
    assert "***" in failed.err


def test_verbose_playbook_streams_tasks_and_passes_ansible_verbosity(tmp_path, monkeypatch, capsys):
    from cli import log as rig_log

    cfg = ProjectConfig(str(tmp_path))
    cfg.init(name="verbose", agent_type="opencode")
    manager = AnsibleManager(cfg)
    manager.configure(str(tmp_path))
    manager.setup_inventory("1.2.3.4", "/key")
    playbook = tmp_path / "provision.yml"
    playbook.write_text("- hosts: all\n")
    monkeypatch.setattr("cli.vault_keys.unlock_data_keys", lambda *a, **k: None)
    captured = {}

    def popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env") or {}
        return _fake_playbook_process(["TASK [Install git]\n", "ok: [remote_vm]\n"], 0)

    monkeypatch.setattr("cli.remote_dev.subprocess.Popen", popen)
    rig_log.configure_logging(verbosity=3)
    assert manager._run_one_playbook(str(playbook), stream=True) is True
    streamed = capsys.readouterr()
    assert "ok: [remote_vm]" in streamed.out
    assert "-vv" in captured["cmd"]
    search = captured["env"]["ANSIBLE_ROLES_PATH"].split(os.pathsep)
    assert search[0].endswith(os.path.join(".rig", "local_roles"))
    assert search[1].endswith(os.path.join(".rig", "roles"))
    assert len(search) == 2
    rig_log.configure_logging()


def test_cloud_provider_defaults_and_aliases():
    from cli.remote_dev import host_block, normalize_cloud_provider

    assert normalize_cloud_provider(None) == "hetzner"
    assert normalize_cloud_provider("  Hetzner Cloud ") == "hetzner"
    assert normalize_cloud_provider("hcloud") == "hetzner"
    assert normalize_cloud_provider("Amazon") == "aws"
    assert host_block({"host": {"provider": 1}})["provider"] == "hetzner"
    with pytest.raises(ValueError, match="Unsupported cloud provider"):
        normalize_cloud_provider("azure")


def test_unimplemented_provider_is_refused():
    with pytest.raises(ValueError, match="not available yet"):
        RemoteDevEnvironment(
            name="aws-later",
            config={"host": {"provider": "aws", "type": "vm"}},
        )


def test_create_provider_override(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "token")
    captured = {}

    class FakeEnv:
        def __init__(self, name=None, config=None, storage_manager=None):
            captured["config"] = config
            self.name = name or "dev-x"

        def setup(self, project_dir=None):
            return False

    with patch("cli.main.RemoteDevEnvironment", FakeEnv), patch(
        "cli.main.StorageManager", side_effect=ValueError("no storage")
    ):
        assert main(["create", "--name", "from-flag", "--provider", "hcloud"]) == 1
        assert main(["create", "--provider", "gcp"]) == 1

    assert captured["config"]["host"]["provider"] == "hetzner"
    err = capsys.readouterr().err
    assert "not available yet" in err
    assert "gcp" in err
