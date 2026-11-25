"""`rig attach` (live agents), `rig exec`, and the live-agent notes in inspect/apply/delete."""

import contextlib
import io
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cli.live_agent import LiveAgent
from cli.main import main
from cli.remote_dev import RemoteDevEnvironment


@pytest.fixture
def logs(capsys):
    """rig logs to stderr through its own handler (no propagation), so read stderr, accumulating."""

    class _Logs:
        def __init__(self):
            self._seen = ""

        @property
        def text(self):
            self._seen += capsys.readouterr().err
            return self._seen

    return _Logs()


def _live(agent="claude", dead=False, created=1_700_000_000, attached=0):
    return LiveAgent(agent, dead, created, attached)


def _run(
    argv,
    *,
    live=(),
    configured=("claude",),
    default="claude",
    stored=None,
    recovered=None,
    stdin=None,
    token="tok",
    name="dev-x",
    key_exists=True,
    resolves=True,
    **env_attrs,
):
    """Run main(argv) against a mocked Environment; returns (exit code, env mock, patched helpers)."""
    env = MagicMock()
    env.name = "dev-x"
    env.vm_ip = "203.0.113.5"
    env.ssh_key_name = "dev-x-key"
    env.resolve_resources.return_value = resolves
    env.live_agents.return_value = None if live is None else list(live)
    env.recover_session_id.return_value = recovered
    env.attach_to_agent.return_value = 0
    env.queue_prompt.return_value = 0
    env.start_live_agent.return_value = 0
    for key, value in env_attrs.items():
        setattr(env, key, value)
    helpers = {}
    with contextlib.ExitStack() as stack:
        enter = stack.enter_context
        enter(patch("cli.main._load_credentials"))
        enter(patch.dict(os.environ, {"HETZNER_CLOUD_API_TOKEN": token} if token else {}))
        if not token:
            os.environ.pop("HETZNER_CLOUD_API_TOKEN", None)
        enter(patch("cli.main.StorageManager", side_effect=ValueError("no storage")))
        enter(patch("cli.main.RemoteDevEnvironment", return_value=env))
        enter(patch("cli.main.resolve_env_name", return_value=name))
        helpers["remember_env"] = enter(patch("cli.main.remember_last_env"))
        helpers["remember_session"] = enter(patch("cli.main.remember_last_session"))
        enter(patch("cli.main.read_last_session", return_value=stored))
        enter(patch("cli.main.os.getcwd", return_value="/tmp/ws"))
        enter(patch("cli.main.os.path.exists", return_value=key_exists))
        enter(patch("cli.main.ProjectConfig"))
        enter(patch("cli.remote_dev.configured_agents", return_value=(list(configured), default)))
        if stdin is not None:
            enter(patch("cli.main.sys.stdin", io.StringIO(stdin)))
        code = main(argv)
    return code, env, helpers


# ---- rig attach -----------------------------------------------------------------


def test_attach_starts_a_live_agent_by_default_and_says_how_to_leave(logs):
    code, env, _ = _run(["attach"], live=[])
    assert code == 0
    kwargs = env.attach_to_agent.call_args.kwargs
    assert kwargs["persist"] is True and kwargs["restart"] is False and kwargs["agent_type"] == "claude"
    env.ensure_firewall_access.assert_called_once()


def test_attach_joins_a_running_agent_and_ignores_resume_flags(capsys):
    code, env, _ = _run(["attach", "--resume", "abc"], live=[_live()], stored=("old-id", "claude"))
    assert code == 0
    kwargs = env.attach_to_agent.call_args.kwargs
    assert kwargs["agent_args"] == []  # neither the flag nor the remembered session is applied
    assert kwargs["restart"] is False


def test_bare_attach_joins_whichever_agent_is_running():
    code, env, _ = _run(["attach"], live=[_live("opencode")], configured=("claude", "opencode"), default="claude")
    assert code == 0 and env.attach_to_agent.call_args.kwargs["agent_type"] == "opencode"


def test_an_explicit_agent_is_not_swapped_for_the_running_one():
    code, env, _ = _run(["attach", "--agent", "claude"], live=[_live("opencode")], configured=("claude", "opencode"))
    assert env.attach_to_agent.call_args.kwargs["agent_type"] == "claude"


def test_restart_replaces_the_agent_and_still_resumes_the_conversation():
    code, env, _ = _run(["attach", "--restart"], live=[_live()], stored=("sess-1", "claude"))
    assert code == 0
    kwargs = env.attach_to_agent.call_args.kwargs
    assert kwargs["restart"] is True
    assert kwargs["agent_args"] == ["--resume", "sess-1"]  # same conversation, fresh process


def test_restart_cannot_be_combined_with_no_persist():
    code, env, _ = _run(["attach", "--restart", "--no-persist"], live=[])
    assert code == 1
    env.attach_to_agent.assert_not_called()


def test_no_persist_runs_the_agent_as_the_ssh_sessions_own_process():
    code, env, _ = _run(["attach", "--no-persist"], live=[_live()])
    assert code == 0
    env.live_agents.assert_not_called()
    assert env.attach_to_agent.call_args.kwargs["persist"] is False


def test_attach_recovers_the_resume_id_of_an_agent_that_finished_unattended():
    code, env, helpers = _run(["attach"], live=[], recovered="rec-id-1", stored=("rec-id-1", "claude"))
    helpers["remember_session"].assert_called_once_with("dev-x", "rec-id-1", agent_type="claude")
    assert env.attach_to_agent.call_args.kwargs["agent_args"] == ["--resume", "rec-id-1"]


def test_attach_does_not_recover_when_resume_flags_were_given_or_the_vm_is_unreachable():
    _, env, helpers = _run(["attach", "--resume", "mine"], live=[])
    env.recover_session_id.assert_not_called()
    helpers["remember_session"].assert_not_called()
    _, env2, helpers2 = _run(["attach"], live=None)  # SSH check failed: attach anyway
    env2.recover_session_id.assert_not_called()
    assert env2.attach_to_agent.called


# ---- rig exec ---------------------------------------------------------------------


def test_exec_starts_a_live_agent_with_approvals_skipped_by_default(logs):
    code, env, helpers = _run(["exec", "fix the tests"], live=[])
    assert code == 0
    env.start_live_agent.assert_called_once_with("claude", "fix the tests", auto_approve=True, restart=False)
    env.attach_to_agent.assert_not_called()  # exec never attaches
    helpers["remember_env"].assert_called_once_with("dev-x")
    assert "Approvals are skipped" in logs.text and "rig attach dev-x" in logs.text


def test_exec_no_auto_approve_supervises_the_run(logs):
    code, env, _ = _run(["exec", "--no-auto-approve", "task"], live=[])
    assert code == 0
    assert env.start_live_agent.call_args.kwargs["auto_approve"] is False
    assert "Approvals are skipped" not in logs.text


def test_exec_reads_the_prompt_from_a_file_or_stdin(tmp_path):
    f = tmp_path / "task.md"
    f.write_text("do the thing\nthen the other\n", encoding="utf-8")
    _, env, _ = _run(["exec", f"@{f}"], live=[])
    assert env.start_live_agent.call_args.args[1] == "do the thing\nthen the other"
    _, env2, _ = _run(["exec", "-"], live=[], stdin="piped task\n")
    assert env2.start_live_agent.call_args.args[1] == "piped task"


def test_exec_rejects_an_empty_prompt_and_a_missing_file(capsys):
    for bad in ("   ", "@/nonexistent/task.md"):
        code, env, _ = _run(["exec", bad], live=[])
        assert code == 1
        env.start_live_agent.assert_not_called()


def test_exec_picks_the_agent_with_agent_and_rejects_one_that_is_not_enabled():
    _, env, _ = _run(["exec", "--agent", "opencode", "go"], live=[], configured=("claude", "opencode"))
    assert env.start_live_agent.call_args.args[0] == "opencode"
    code, env2, _ = _run(["exec", "--agent", "cursor", "go"], live=[], configured=("claude",))
    assert code == 1
    env2.start_live_agent.assert_not_called()
    code, _, _ = _run(["exec", "--agent", "nonsense", "go"], live=[])
    assert code == 1


def test_exec_reports_an_unreachable_environment():
    code, env, _ = _run(["exec", "go"], live=None)
    assert code == 1
    env.start_live_agent.assert_not_called()


def test_exec_propagates_a_failed_start():
    code, env, _ = _run(["exec", "go"], live=[], start_live_agent=MagicMock(return_value=1))
    assert code == 1


def test_exec_queues_into_a_running_claude():
    code, env, _ = _run(["exec", "next task"], live=[_live("claude")])
    assert code == 0
    env.queue_prompt.assert_called_once_with("claude", "next task")
    env.start_live_agent.assert_not_called()


@pytest.mark.parametrize("status", [10, 11, 12, 13, 99])
def test_exec_explains_why_a_prompt_could_not_be_queued(status, logs):
    code, env, _ = _run(["exec", "next"], live=[_live("claude")], queue_prompt=MagicMock(return_value=status))
    assert code == 1
    assert "Could not queue the prompt" in logs.text and "--restart" in logs.text
    env.start_live_agent.assert_not_called()


def test_exec_refuses_to_queue_for_agents_other_than_claude(logs):
    code, env, _ = _run(
        ["exec", "--agent", "opencode", "next"],
        live=[_live("opencode")],
        configured=("claude", "opencode"),
    )
    assert code == 1
    assert "only supported for Claude Code" in logs.text
    env.queue_prompt.assert_not_called()


def test_exec_restart_replaces_a_running_agent_instead_of_queueing():
    code, env, _ = _run(["exec", "--restart", "fresh start"], live=[_live("claude")])
    assert code == 0
    env.queue_prompt.assert_not_called()
    assert env.start_live_agent.call_args.kwargs["restart"] is True


def test_exec_replaces_a_dead_agent_without_needing_restart():
    code, env, _ = _run(["exec", "go"], live=[_live("claude", dead=True)])
    assert code == 0
    env.start_live_agent.assert_called_once()
    env.queue_prompt.assert_not_called()


# ---- inspect / apply / delete ------------------------------------------------------------


def test_inspect_lists_live_agents(capsys):
    env = MagicMock()
    env.inspect_details.return_value = {
        "name": "dev-x",
        "live_agents": ["claude (running 5m, detached)"],
        "stale": False,
    }
    with (
        patch("cli.main._env_for_readonly_cmd", return_value=(env, "dev-x")),
        patch("cli.main.remember_last_env"),
    ):
        assert main(["inspect"]) == 0
    assert "live_agents: claude (running 5m, detached)" in capsys.readouterr().out


def test_apply_warns_that_live_agents_keep_their_startup_environment(logs):
    env = MagicMock()
    env.name = "dev-x"
    env.vm_ip = "203.0.113.5"
    env.resolve_resources.return_value = True
    env.apply_playbooks.return_value = True
    env.live_agents.return_value = [_live("claude", created=1_700_000_000, attached=1)]
    with (
        patch("cli.main._load_credentials"),
        patch.dict(os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}),
        patch("cli.main.StorageManager", side_effect=ValueError("no storage")),
        patch("cli.main.RemoteDevEnvironment", return_value=env),
        patch("cli.main.resolve_env_name", return_value="dev-x"),
        patch("cli.main.remember_last_env"),
        patch("cli.main.os.path.exists", return_value=True),
        patch("cli.main.ProjectConfig"),
        patch("cli.main.time.time", return_value=1_700_000_600),
    ):
        main(["apply", "--authorize"])
    assert "Live agents on 'dev-x': claude (running 10m, 1 attached)" in logs.text
    assert "rig attach --restart" in logs.text


def test_delete_warns_before_ending_live_agents(logs):
    env = MagicMock()
    env.vm_ip = "203.0.113.5"
    env.resolve_resources.return_value = True
    env.live_agents.return_value = [_live("claude")]
    with (
        patch("cli.main._load_credentials"),
        patch.dict(os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}),
        patch("cli.main.StorageManager", side_effect=ValueError("no storage")),
        patch("cli.main.RemoteDevEnvironment", return_value=env),
        patch("cli.main.clear_last_env"),
        patch("cli.main.clear_last_session"),
    ):
        assert main(["delete", "dev-x"]) == 0
    assert "has live agents" in logs.text and "unsaved work" in logs.text
    env.cleanup.assert_called_once()  # a warning, not a stop


def test_delete_still_works_when_the_live_agent_check_fails(logs):
    env = MagicMock()
    env.resolve_resources.side_effect = RuntimeError("api down")
    with (
        patch("cli.main._load_credentials"),
        patch.dict(os.environ, {"HETZNER_CLOUD_API_TOKEN": "tok"}),
        patch("cli.main.StorageManager", side_effect=ValueError("no storage")),
        patch("cli.main.RemoteDevEnvironment", return_value=env),
        patch("cli.main.clear_last_env"),
        patch("cli.main.clear_last_session"),
    ):
        assert main(["delete", "dev-x"]) == 0
    env.cleanup.assert_called_once()


# ---- the Environment methods behind them ---------------------------------------------------------


@pytest.fixture
def real_env():
    env = RemoteDevEnvironment(name="dev-y")
    env.vm_ip = "203.0.113.7"
    env.ssh_key_name = "dev-y-key"
    return env


def _done(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_non_interactive_ssh_is_batch_mode_without_a_tty(real_env):
    cmd = real_env._ssh_command("true", tty=False, connect_timeout=5)
    assert "-tt" not in cmd and "BatchMode=yes" in cmd and "ConnectTimeout=5" in cmd
    assert cmd[-2:] == ["dev@203.0.113.7", "true"]
    assert "-tt" in real_env._interactive_ssh_command("true")


def test_live_agents_parses_the_remote_listing_and_returns_none_when_unreachable(real_env):
    listing = "rig-claude|0|1700000000|1\n"
    with (
        patch("cli.remote_dev.os.path.exists", return_value=True),
        patch.object(real_env, "run_remote", return_value=_done(0, listing)),
    ):
        assert [a.agent for a in real_env.live_agents()] == ["claude"]
    with patch("cli.remote_dev.os.path.exists", return_value=True):
        with patch.object(real_env, "run_remote", return_value=_done(255, "", "timed out")):
            assert real_env.live_agents() is None
        with patch.object(real_env, "run_remote", side_effect=subprocess.TimeoutExpired("ssh", 5)):
            assert real_env.live_agents() is None
    with patch("cli.remote_dev.os.path.exists", return_value=False):
        assert real_env.live_agents() is None  # no local key: do not even try


def test_recover_session_id_reads_the_recorded_output(real_env):
    uuid_ = "11111111-2222-3333-4444-555555555555"
    with patch.object(real_env, "run_remote", return_value=_done(0, f"\x1b[1mclaude --resume {uuid_}\x1b[0m\r\n")):
        assert real_env.recover_session_id("claude") == uuid_
    with patch.object(real_env, "run_remote", return_value=_done(0, "")):
        assert real_env.recover_session_id("claude") is None
    with patch.object(real_env, "run_remote", return_value=_done(255)):
        assert real_env.recover_session_id("claude") is None
    with patch.object(real_env, "run_remote", side_effect=OSError("no ssh")):
        assert real_env.recover_session_id("claude") is None


def test_queue_prompt_returns_the_helpers_status(real_env):
    with patch.object(real_env, "run_remote", return_value=_done(10)) as run:
        assert real_env.queue_prompt("claude", "hello") == 10
    assert run.call_args.kwargs["input_text"] == "hello"
    assert "rig-agent-queue" in run.call_args.args[0]
    with patch.object(real_env, "run_remote", side_effect=OSError):
        assert real_env.queue_prompt("claude", "hello") == 255


def test_start_live_agent_uploads_the_prompt_then_starts_and_checks(real_env):
    calls = []

    def fake(command, **kwargs):
        calls.append((command, kwargs))
        if "mktemp" in command:
            return _done(0, "/tmp/rig-prompt.AbC\n")
        return _done(0)

    with patch.object(real_env, "run_remote", side_effect=fake):
        assert real_env.start_live_agent("claude", "the task", auto_approve=True) == 0
    assert calls[0][1]["input_text"] == "the task"  # the prompt travels on stdin, never in a command line
    started = calls[1][0]
    assert "--permission-mode bypassPermissions" in started and "/tmp/rig-prompt.AbC" in started
    assert "pane_dead" in calls[2][0]  # the start check


def test_start_live_agent_reports_each_kind_of_failure(real_env, logs):
    with patch.object(real_env, "run_remote", return_value=_done(255, "", "no route")):
        assert real_env.start_live_agent("claude", "x") == 1  # upload failed
    assert "Could not upload the prompt" in logs.text

    seen = []

    def start_fails(command, **kwargs):
        seen.append(command)
        if "mktemp" in command:
            return _done(0, "/tmp/p1\n")
        if command.startswith("rm -f"):
            return _done(0)
        return _done(1, "", "tmux: boom")

    with patch.object(real_env, "run_remote", side_effect=start_fails):
        assert real_env.start_live_agent("claude", "x") == 1
    assert any(c.startswith("rm -f") for c in seen)  # the uploaded prompt is cleaned up
    assert "Could not start the live agent" in logs.text

    def dies(command, **kwargs):
        if "mktemp" in command:
            return _done(0, "/tmp/p2\n")
        if "pane_dead" in command and "capture-pane" in command:
            return _done(3, "Error: no API key\n")
        return _done(0)

    with patch.object(real_env, "run_remote", side_effect=dies):
        assert real_env.start_live_agent("claude", "x") == 1
    assert "exited right after starting" in logs.text and "no API key" in logs.text


def test_attach_builds_a_persistent_remote_command_unless_no_persist(real_env, tmp_path):
    seen = {}

    def fake_attach(ssh_cmd, attach_log):
        seen["remote"] = ssh_cmd[-1]
        return 0

    with patch.object(RemoteDevEnvironment, "_run_attach_ssh", side_effect=fake_attach):
        assert real_env.attach_to_agent(agent_type="claude", project_dir=str(tmp_path)) == 0
        assert "new-session -d" in seen["remote"] and seen["remote"].rstrip().endswith('attach-session -t "rig-claude"')
        real_env.attach_to_agent(agent_type="claude", project_dir=str(tmp_path), restart=True)
        assert seen["remote"].count("kill-session") == 2
        real_env.attach_to_agent(agent_type="claude", project_dir=str(tmp_path), persist=False)
        assert "tmux" not in seen["remote"] and seen["remote"].endswith("exec claude")


def test_inspect_details_adds_live_agents_only_for_a_running_server(real_env):
    real_env.manager = MagicMock()
    real_env.manager.find_record.return_value = {"owner": "me"}
    real_env.vm_id = 7
    real_env.manager.get_vm.return_value = {"server": {"status": "running", "public_net": {}}}
    with (
        patch.object(real_env, "resolve_resources"),
        patch.object(real_env, "live_agents", return_value=[_live("claude", created=1_700_000_000)]) as live,
        patch("cli.remote_dev.time.time", return_value=1_700_000_300),
    ):
        details = real_env.inspect_details()
    assert details["live_agents"] == ["claude (running 5m, detached)"]
    assert live.call_args.kwargs["connect_timeout"] == 5

    real_env.manager.get_vm.return_value = {"server": {"status": "off", "public_net": {}}}
    with patch.object(real_env, "resolve_resources"), patch.object(real_env, "live_agents") as live2:
        assert "live_agents" not in real_env.inspect_details()
    live2.assert_not_called()
    real_env.manager.get_vm.return_value = {"server": {"status": "running", "public_net": {}}}
    with patch.object(real_env, "resolve_resources"), patch.object(real_env, "live_agents", return_value=None):
        assert "live_agents" not in real_env.inspect_details()  # unreachable: say nothing


# ---- exec: every way it can fail before starting anything ---------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(token=None),  # no Hetzner credentials
        dict(name=None),  # no environment given or remembered
        dict(resolves=False),  # environment not found
        dict(key_exists=False),  # SSH key missing on this machine
        dict(live_agents=MagicMock(side_effect=RuntimeError("boom"))),  # unexpected failure
    ],
    ids=["no-credentials", "no-environment", "not-found", "no-ssh-key", "unexpected-error"],
)
def test_exec_stops_early_with_a_clear_failure(kwargs):
    code, env, _ = _run(["exec", "go"], live=[], **kwargs)
    assert code == 1
    env.start_live_agent.assert_not_called()
    env.queue_prompt.assert_not_called()


def test_the_live_agent_note_never_breaks_apply_or_delete():
    from cli.main import _live_agent_summary

    env = MagicMock()
    env.live_agents.side_effect = RuntimeError("ssh exploded")
    assert _live_agent_summary(env) == []
    env.live_agents.side_effect = None
    env.live_agents.return_value = None
    assert _live_agent_summary(env) == []


def test_run_remote_pipes_stdin_and_uses_the_quick_connect_timeout(real_env):
    with patch("cli.remote_dev.subprocess.run", return_value=_done(0, "ok")) as run:
        result = real_env.run_remote("echo hi", input_text="payload", timeout=7, connect_timeout=3)
    assert result.stdout == "ok"
    cmd = run.call_args.args[0]
    assert "ConnectTimeout=3" in cmd and cmd[-1] == "echo hi"
    assert run.call_args.kwargs["input"] == "payload" and run.call_args.kwargs["timeout"] == 7
