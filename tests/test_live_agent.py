"""Live agents: script builders, parsing, the Claude state hooks and real tmux behaviour."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import uuid

import pytest
import yaml

from cli import live_agent as la
from cli.ansible_filters import rig_live
from cli.last_session import parse_session_id
from cli.remote_dev import RemoteDevEnvironment

need_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
CONF = os.path.join(la.ASSETS_DIR, "tmux.conf")


# ---- pure helpers ---------------------------------------------------------------


def test_session_names_are_per_agent():
    assert la.session_name("claude") == "rig-claude"


def test_parse_live_agents_ignores_foreign_and_malformed_lines():
    out = "\n".join(
        [
            "rig-claude|0|1700000000|2",
            "rig-opencode|1|1700000100|0",
            "someone-else|0|1|0",
            "rig-bogus|0|1|0",
            "rig-cursor|0|notanumber|0",
            "garbage",
        ]
    )
    found = la.parse_live_agents(out)
    assert [(a.agent, a.dead, a.attached) for a in found] == [("claude", False, 2), ("opencode", True, 0)]
    assert la.running_agents(found) == ["claude"]  # a dead pane is not a running agent
    assert la.parse_live_agents("") == [] and la.parse_live_agents(None) == []


def test_describe_shows_age_and_clients():
    now = 1_700_000_000
    assert la.LiveAgent("claude", False, now - 300, 1).describe(now) == "claude (running 5m, 1 attached)"
    assert la.LiveAgent("claude", False, now - 3 * 3600, 0).describe(now) == "claude (running 3h, detached)"
    assert la.LiveAgent("claude", True, now, 0).describe(now) == "claude (exited with an error)"


def test_restart_adds_a_kill_and_attach_ends_with_attach_session():
    plain = la.ensure_session_script("claude", "exec claude")
    restart = la.ensure_session_script("claude", "exec claude", restart=True)
    assert "kill-session" in plain  # dead panes are replaced
    assert restart.count("kill-session") == plain.count("kill-session") + 1
    attach = la.attach_script("claude", "exec claude")
    assert attach.splitlines()[-1].startswith("exec tmux -L rig") and attach.endswith('attach-session -t "rig-claude"')


def test_launch_command_is_quoted_as_one_argument():
    script = la.ensure_session_script("claude", "echo 'it''s'; exec claude")
    assert "new-session -d" in script
    assert "'echo '\"'\"'it'\"'\"''\"'\"'s'\"'\"'; exec claude'" in script


@pytest.mark.parametrize(
    "value, stdin, expected",
    [
        ("fix the bug", None, "fix the bug"),
        ("-", "from stdin\n\n", "from stdin"),
        ("\n  indented\n  more\n\n", None, "  indented\n  more"),
    ],
)
def test_read_prompt_text_and_stdin(value, stdin, expected):
    assert la.read_prompt(value, stdin_text=stdin) == expected


def test_read_prompt_from_a_file_and_errors(tmp_path):
    f = tmp_path / "task.md"
    f.write_text("line one\nline two\n", encoding="utf-8")
    assert la.read_prompt(f"@{f}") == "line one\nline two"
    for bad in ("", "   ", "-"):
        with pytest.raises(ValueError, match="empty"):
            la.read_prompt(bad, stdin_text="")
    with pytest.raises(ValueError, match="cannot read prompt file"):
        la.read_prompt(f"@{tmp_path}/missing.md")
    assert la.read_prompt("@") == "@"  # a lone @ is just text


def test_prompt_is_passed_per_agent_and_the_file_is_deleted_first():
    for agent, tail in (("claude", ' "$p"'), ("cursor", ' "$p"'), ("opencode", ' --prompt "$p"')):
        lead, flags, trailer = la.prompt_launch_fragments(agent, "/tmp/p f", auto_approve=True)
        assert lead == "p=\"$(cat '/tmp/p f')\"; rm -f '/tmp/p f'; "
        assert trailer == tail
        assert flags.split() == la.AUTO_APPROVE_FLAGS[agent]
    assert la.prompt_launch_fragments("claude", "/tmp/p", auto_approve=False)[1] == ""


def test_launch_commands_with_and_without_a_prompt():
    plain = RemoteDevEnvironment.agent_remote_command("claude")
    assert plain.endswith("exec claude") and "bypassPermissions" not in plain
    unattended = RemoteDevEnvironment.agent_remote_command("claude", prompt_file="/tmp/x", auto_approve=True)
    assert unattended.endswith('exec claude --permission-mode bypassPermissions "$p"')
    assert 'p="$(cat /tmp/x)"; rm -f /tmp/x; ' in unattended
    supervised = RemoteDevEnvironment.agent_remote_command("cursor", prompt_file="/tmp/x", auto_approve=False)
    assert supervised.endswith('exec agent --approve-mcps "$p"') and "--force" not in supervised
    assert RemoteDevEnvironment.agent_remote_command("opencode", prompt_file="/tmp/x", auto_approve=True).endswith(
        'exec opencode --auto --prompt "$p"'
    )
    only_flags = RemoteDevEnvironment.agent_remote_command("cursor", auto_approve=True)
    assert only_flags.endswith("exec agent --approve-mcps --force")


def test_queue_refusals_explain_every_failure_status():
    assert set(la.QUEUE_REFUSALS) == {10, 11, 12, 13}
    assert "waiting for approval" in la.QUEUE_REFUSALS[10]


# ---- the Claude hooks ---------------------------------------------------------------


def _commands(settings):
    """Every hook command in a settings dict (ignores values that are not lists of hook groups)."""
    found = []
    for groups in settings["hooks"].values():
        for group in groups if isinstance(groups, list) else []:
            for handler in group.get("hooks", []) if isinstance(group, dict) else []:
                found.append(handler["command"])
    return found


def test_hooks_record_idle_working_waiting_and_ended():
    hooks = rig_live.merge_hooks({})["hooks"]
    state = {}
    for event, groups in hooks.items():
        for group in groups:
            cmd = group["hooks"][0]["command"]
            state[(event, group.get("matcher"))] = cmd.rsplit(" ", 1)[1]
    assert state[("Stop", None)] == "idle" and state[("SessionStart", None)] == "idle"
    assert state[("PreToolUse", None)] == state[("UserPromptSubmit", None)] == "working"
    assert state[("PermissionRequest", None)] == "waiting"
    assert state[("Notification", "permission_prompt")] == state[("Notification", "agent_needs_input")] == "waiting"
    assert state[("Notification", "idle_prompt")] == "idle"
    assert state[("SessionEnd", None)] == "ended"


def test_hooks_keep_the_users_own_and_never_duplicate():
    mine = {"hooks": [{"type": "command", "command": "notify-send done"}]}
    mixed = {
        "hooks": [
            {"type": "command", "command": "notify-send hi"},
            {"type": "command", "command": f"{rig_live.STATE_HELPER} claude idle"},
        ]
    }
    settings = {"theme": "dark", "hooks": {"Stop": [mine, mixed], "Custom": "not-a-list", "Other": [{"nohooks": True}]}}
    once = rig_live.merge_hooks(settings)
    assert once["theme"] == "dark"
    assert once["hooks"]["Custom"] == "not-a-list" and once["hooks"]["Other"] == [{"nohooks": True}]
    stop = [h["command"] for g in once["hooks"]["Stop"] for h in g["hooks"]]
    assert stop.count("notify-send done") == 1 and stop.count("notify-send hi") == 1
    assert stop.count(f"{rig_live.STATE_HELPER} claude idle") == 1
    assert rig_live.merge_hooks(once) == once  # idempotent
    assert len([c for c in _commands(once) if c.startswith(rig_live.STATE_HELPER)]) == len(rig_live.HOOKS)


@pytest.mark.parametrize("bad", [None, "text", [], {"hooks": "nope"}, {"hooks": None}])
def test_hooks_tolerate_missing_or_malformed_settings(bad):
    merged = rig_live.merge_hooks(bad)
    assert len(_commands(merged)) == len(rig_live.HOOKS)


def test_hook_filter_module_exposes_the_filter():
    assert rig_live.FilterModule().filters()["rig_claude_hooks"]({})["hooks"]


# ---- real tmux ---------------------------------------------------------------------------

AGENT_PY = textwrap.dedent(
    """
    import os, sys, termios, time, tty
    out = open(os.environ["FAKE_OUT"], "ab", buffering=0)
    out.write(b"started\\n")
    print("resume with: --resume " + os.environ.get("FAKE_RESUME_ID", "none"), flush=True)
    fd = sys.stdin.fileno()
    tty.setraw(fd)
    sys.stdout.write("\\x1b[?2004h"); sys.stdout.flush()
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        out.write(chunk + b"|")
    """
)


class Tmux:
    """An isolated tmux server (own socket, log dir and state dir) running a fake agent."""

    def __init__(self, tmp_path):
        self.socket = f"rigtest-{uuid.uuid4().hex[:8]}"
        self.log_dir = str(tmp_path / "log")
        self.state_dir = str(tmp_path / "state")
        os.makedirs(self.state_dir)
        self.out = str(tmp_path / "agent-input.bin")
        self.agent_py = str(tmp_path / "agent.py")
        with open(self.agent_py, "w") as handle:
            handle.write(AGENT_PY)
        self.env = dict(
            os.environ,
            FAKE_OUT=self.out,
            RIG_TMUX_SOCKET=self.socket,
            RIG_TMUX_CONF=CONF,
            RIG_STATE_DIR=self.state_dir,
        )

    def launch(self, resume_id="none"):
        return f"FAKE_RESUME_ID={resume_id} {sys.executable} {self.agent_py}"

    def ensure(self, launch=None, restart=False, agent="claude"):
        script = la.ensure_session_script(
            agent, launch or self.launch(), restart=restart, socket=self.socket, conf=CONF, log_dir=self.log_dir
        )
        return subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=self.env, timeout=30)

    def tmux(self, *args):
        return subprocess.run(
            ["tmux", "-L", self.socket, "-f", CONF, *args], capture_output=True, text=True, env=self.env, timeout=15
        )

    def agents(self):
        script = la.list_script(socket=self.socket, conf=CONF)
        out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=self.env, timeout=15).stdout
        return la.parse_live_agents(out)

    def queue(self, prompt, agent="claude"):
        helper = os.path.join(la.ASSETS_DIR, "rig-agent-queue")
        return subprocess.run(
            ["sh", helper, agent], input=prompt, capture_output=True, text=True, env=self.env, timeout=15
        ).returncode

    def set_state(self, state, agent="claude"):
        helper = os.path.join(la.ASSETS_DIR, "rig-agent-state")
        subprocess.run(["sh", helper, agent, state], check=True, env=self.env)

    def wait_for(self, predicate, seconds=5.0):
        end = time.time() + seconds
        while time.time() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def received(self):
        try:
            with open(self.out, "rb") as handle:
                return handle.read()
        except OSError:
            return b""

    def close(self):
        self.tmux("kill-server")


@pytest.fixture
def tm(tmp_path):
    server = Tmux(tmp_path)
    yield server
    server.close()


@need_tmux
def test_agent_keeps_running_with_no_terminal_attached(tm):
    assert tm.ensure().returncode == 0
    assert tm.wait_for(lambda: b"started" in tm.received())
    found = tm.agents()
    assert [(a.agent, a.dead, a.attached) for a in found] == [("claude", False, 0)]
    # the rig config is loaded: no status bar, and the agent's pane has the size rig asked for
    assert tm.tmux("show", "-g", "status").stdout.strip() == "status off"
    assert tm.tmux("show", "-g", "remain-on-exit").stdout.strip() == "remain-on-exit failed"


@need_tmux
def test_a_second_ensure_joins_instead_of_starting_again(tm):
    tm.ensure()
    assert tm.wait_for(lambda: b"started" in tm.received())
    created = tm.agents()[0].created
    log_before = open(os.path.join(tm.log_dir, "rig-claude.log"), "rb").read()
    assert tm.ensure().returncode == 0
    assert tm.received().count(b"started") == 1  # the agent was not launched a second time
    assert tm.agents()[0].created == created
    assert not os.path.exists(os.path.join(tm.log_dir, "rig-claude.log.prev"))
    assert open(os.path.join(tm.log_dir, "rig-claude.log"), "rb").read().startswith(log_before)


@need_tmux
def test_restart_replaces_the_running_agent(tm):
    tm.ensure()
    assert tm.wait_for(lambda: tm.received().count(b"started") == 1)
    assert tm.ensure(restart=True).returncode == 0
    assert tm.wait_for(lambda: tm.received().count(b"started") == 2)
    assert len(tm.agents()) == 1


@need_tmux
def test_a_failed_agent_leaves_a_dead_pane_that_is_replaced_next_time(tm):
    assert tm.ensure(launch="echo boom; exit 3").returncode == 0
    assert tm.wait_for(lambda: tm.agents() and tm.agents()[0].dead)
    assert la.running_agents(tm.agents()) == []
    assert tm.ensure().returncode == 0  # a healthy launch replaces the dead pane
    assert tm.wait_for(lambda: b"started" in tm.received())
    assert [(a.agent, a.dead) for a in tm.agents()] == [("claude", False)]


@need_tmux
def test_output_is_logged_so_the_resume_id_survives_an_unattended_exit(tm):
    resume = "11111111-2222-3333-4444-555555555555"
    tm.ensure(launch=tm.launch(resume))
    log = os.path.join(tm.log_dir, "rig-claude.log")
    assert tm.wait_for(lambda: os.path.exists(log) and resume in open(log, errors="replace").read())
    script = la.recent_log_script("claude", log_dir=tm.log_dir)
    shown = subprocess.run(["sh", "-c", script], capture_output=True, text=True).stdout
    assert parse_session_id(shown) == resume
    # a new session keeps the previous log as .prev and starts a fresh one
    tm.ensure(launch=tm.launch("99999999-aaaa-bbbb-cccc-dddddddddddd"), restart=True)
    assert tm.wait_for(lambda: os.path.exists(log + ".prev"))
    assert resume in open(log + ".prev", errors="replace").read()


def test_recent_log_is_empty_when_there_is_no_log(tmp_path):
    script = la.recent_log_script("claude", log_dir=str(tmp_path / "none"))
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout == ""


@need_tmux
def test_queue_is_gated_on_the_agent_state(tm):
    prompt = 'first line\nsecond "quoted" line'
    assert tm.queue(prompt) == 12  # no live agent yet
    tm.ensure()
    assert tm.wait_for(lambda: b"started" in tm.received())
    assert tm.queue(prompt) == 11  # no hooks have reported a state
    tm.set_state("waiting")
    assert tm.queue(prompt) == 10  # an approval dialog would swallow the keystrokes
    assert b"first line" not in tm.received()
    tm.set_state("ended")
    assert tm.queue(prompt) == 11
    tm.set_state("working")
    assert tm.queue(prompt) == 0
    assert tm.wait_for(lambda: b"quoted" in tm.received())
    got = tm.received()
    # one bracketed paste (so the newline stays inside the message), then Enter
    assert b'\x1b[200~first line\rsecond "quoted" line\x1b[201~' in got
    assert got.rstrip(b"|").endswith(b"\r")
    tm.set_state("idle")
    assert tm.queue("again") == 0


@need_tmux
def test_queue_refuses_a_dead_pane(tm):
    tm.ensure(launch="exit 1")
    assert tm.wait_for(lambda: tm.agents() and tm.agents()[0].dead)
    tm.set_state("idle")
    assert tm.queue("hello") == 12


def test_state_helper_writes_state_and_epoch(tmp_path):
    helper = os.path.join(la.ASSETS_DIR, "rig-agent-state")
    env = dict(os.environ, RIG_STATE_DIR=str(tmp_path / "nested" / "dir"))
    subprocess.run(["sh", helper, "claude", "working"], check=True, env=env)
    state, epoch = (tmp_path / "nested" / "dir" / "claude.state").read_text().split()
    assert state == "working" and abs(int(epoch) - time.time()) < 10
    assert subprocess.run(["sh", helper], env=env, capture_output=True).returncode != 0  # usage error


# ---- the authorize tasks, through real Ansible ---------------------------------------------------


def _run_live_authorize_tasks(tmp_path, claude_settings=None, agents=("claude",)):
    from _roles import authorize_play

    from cli.remote_dev import rig_filter_plugins_path

    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    (root / "claude").mkdir(exist_ok=True)
    (root / "usr" / "local" / "bin").mkdir(parents=True, exist_ok=True)  # exists on a real VM
    if claude_settings is not None:
        (root / "claude" / "settings.json").write_text(json.dumps(claude_settings))
    names = (
        "Create the live-agent directories",
        "Install the live-agent tmux configuration",
        "Install the live-agent helper scripts",
        "Read Claude settings for the live-agent hooks",
        "Record Claude's state for live agents",
    )
    play = authorize_play()
    tasks = [t for t in play["tasks"] if t.get("name", "").startswith(names)]
    assert len(tasks) == len(names)
    text = json.dumps(tasks)
    text = text.replace("/home/{{ rig_user }}/.claude", str(root / "claude"))
    for path in ("/etc/rig", "/run/rig", "/var/log/rig", "/usr/local/bin"):
        text = text.replace(path, str(root) + path)
    playbook = [
        {
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": {"_rig_agent_types": list(agents), "rig_assets_dir": la.ASSETS_DIR},
            "tasks": json.loads(text),
        }
    ]
    (tmp_path / "pb.yml").write_text(yaml.safe_dump(playbook))
    env = dict(os.environ, ANSIBLE_FILTER_PLUGINS=rig_filter_plugins_path(), ANSIBLE_NOCOLOR="1")
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(tmp_path / "pb.yml")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
    )
    return root, result


def _need_ansible():
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not installed")


def test_authorize_installs_config_scripts_and_hooks(tmp_path):
    _need_ansible()
    existing = {"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}]}}
    root, result = _run_live_authorize_tasks(tmp_path, existing)
    assert result.returncode == 0, result.stdout + result.stderr

    assert (root / "etc" / "rig" / "tmux.conf").read_text() == open(CONF).read()
    for script in ("rig-agent-state", "rig-agent-queue"):
        installed = root / "usr" / "local" / "bin" / script
        assert installed.read_text() == open(os.path.join(la.ASSETS_DIR, script)).read()
        assert os.access(installed, os.X_OK)
    assert (root / "run" / "rig" / "agents").is_dir() and (root / "var" / "log" / "rig").is_dir()

    settings = json.loads((root / "claude" / "settings.json").read_text())
    assert settings["theme"] == "dark"
    commands = _commands(settings)
    assert "notify-send done" in commands  # the user's hook survives
    assert len([c for c in commands if "rig-agent-state claude" in c]) == len(rig_live.HOOKS)

    again = _run_live_authorize_tasks(tmp_path, None)[1]  # a second run changes nothing
    assert again.returncode == 0 and "changed=0" in again.stdout


def test_authorize_creates_claude_settings_when_missing_and_skips_hooks_without_claude(tmp_path):
    _need_ansible()
    root, result = _run_live_authorize_tasks(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_commands(json.loads((root / "claude" / "settings.json").read_text()))) == len(rig_live.HOOKS)

    other = tmp_path / "second"
    other.mkdir()
    root2, result2 = _run_live_authorize_tasks(other, agents=("opencode",))
    assert result2.returncode == 0, result2.stdout + result2.stderr
    assert (root2 / "etc" / "rig" / "tmux.conf").exists()  # tmux config is for every agent
    assert not (root2 / "claude" / "settings.json").exists()  # Claude hooks only when Claude is installed


@need_tmux
def test_start_check_reports_an_agent_that_died_at_once(tm):
    assert tm.ensure(launch="echo 'no credentials configured'; exit 3").returncode == 0
    script = la.verify_started_script("claude", socket=tm.socket, conf=CONF, wait_seconds=0)
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=tm.env, timeout=20)
    assert tm.wait_for(lambda: tm.agents() and tm.agents()[0].dead)
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=tm.env, timeout=20)
    assert result.returncode == 3 and "no credentials configured" in result.stdout


@need_tmux
def test_start_check_passes_for_a_running_agent(tm):
    tm.ensure()
    assert tm.wait_for(lambda: b"started" in tm.received())
    script = la.verify_started_script("claude", socket=tm.socket, conf=CONF, wait_seconds=0)
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=tm.env, timeout=20)
    assert result.returncode == 0 and result.stdout == ""


# ---- wiring ------------------------------------------------------------------------


def test_tmux_is_installed_once_by_the_live_agent_role_in_provision():
    from _roles import authorize_tasks

    sys.path.insert(0, os.path.dirname(__file__))
    from _roles import role_tasks

    installs = [t for t in role_tasks("rig_live_agent") if "apt" in t]
    assert [t["apt"]["name"] for t in installs] == ["tmux"]
    # It is part of the cached image, so authorize does not install it again.
    assert not [t for t in authorize_tasks() if "apt" in t]


def test_the_assets_ship_with_the_package():
    import tomllib

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), "rb") as handle:
        data = tomllib.load(handle)
    assert "assets/*" in data["tool"]["setuptools"]["package-data"]["cli"]
    assert "roles/**/*.yml" in data["tool"]["setuptools"]["package-data"]["cli"]
    for name in ("tmux.conf", "rig-agent-state", "rig-agent-queue"):
        assert os.path.isfile(os.path.join(la.ASSETS_DIR, name))
    assert os.access(os.path.join(la.ASSETS_DIR, "rig-agent-queue"), os.X_OK)


def test_hook_commands_point_at_the_installed_helper():
    assert rig_live.STATE_HELPER == la.STATE_HELPER


# ---- a real terminal attaching and leaving (the point of the whole feature) -----------------------------


def _attach_in_a_terminal(tm):
    """Run rig's attach script under a pty, as `ssh -tt` would. Returns (pid, master fd)."""
    import pty

    script = la.attach_script("claude", tm.launch(), socket=tm.socket, conf=CONF, log_dir=tm.log_dir)
    pid, master = pty.fork()
    if pid == 0:  # child: becomes the attach client
        os.environ.update(tm.env)
        os.environ["TERM"] = "xterm-256color"
        os.execvp("sh", ["sh", "-c", script])
    return pid, master


def _wait_exit(pid, seconds=10.0):
    end = time.time() + seconds
    while time.time() < end:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.05)
    os.kill(pid, 9)
    os.waitpid(pid, 0)
    return None


@need_tmux
def test_detaching_with_ctrl_b_d_leaves_the_agent_running(tm):
    pty = pytest.importorskip("pty")  # noqa: F841
    pid, master = _attach_in_a_terminal(tm)
    try:
        assert tm.wait_for(lambda: b"started" in tm.received())
        assert tm.wait_for(lambda: tm.agents() and tm.agents()[0].attached == 1)  # this terminal joined
        os.write(master, b"\x02d")  # tmux's default detach
        assert _wait_exit(pid) == 0  # the attach client returns normally
    finally:
        os.close(master)
    assert [(a.agent, a.dead, a.attached) for a in tm.agents()] == [("claude", False, 0)]
    assert tm.received().count(b"started") == 1


@need_tmux
def test_closing_the_terminal_leaves_the_agent_running(tm):
    pytest.importorskip("pty")
    pid, master = _attach_in_a_terminal(tm)
    assert tm.wait_for(lambda: tm.agents() and tm.agents()[0].attached == 1)
    os.close(master)  # the terminal window goes away: the client gets SIGHUP
    _wait_exit(pid)
    assert tm.wait_for(lambda: tm.agents() and tm.agents()[0].attached == 0)
    assert [(a.agent, a.dead) for a in tm.agents()] == [("claude", False)]
    # a later attach joins the same process instead of starting another
    pid2, master2 = _attach_in_a_terminal(tm)
    try:
        assert tm.wait_for(lambda: tm.agents() and tm.agents()[0].attached == 1)
        assert tm.received().count(b"started") == 1
        os.write(master2, b"\x02d")
        _wait_exit(pid2)
    finally:
        os.close(master2)


@need_tmux
def test_ctrl_c_goes_to_the_agent_and_does_not_detach(tm):
    pytest.importorskip("pty")
    pid, master = _attach_in_a_terminal(tm)
    try:
        assert tm.wait_for(lambda: tm.agents() and tm.agents()[0].attached == 1)
        os.write(master, b"\x03")  # Ctrl+C reaches the agent (raw mode here, so as a byte)
        assert tm.wait_for(lambda: b"\x03" in tm.received())
        assert tm.agents()[0].attached == 1  # still attached
        os.write(master, b"\x02d")
        _wait_exit(pid)
    finally:
        os.close(master)


def test_claude_is_seeded_to_skip_the_bypass_mode_warning_dialog():
    """Verified on a real VM: without this an unattended `rig exec` stops at a dialog whose default is "exit"."""
    from _roles import authorize_play

    seed = next(t for t in authorize_play()["tasks"] if t.get("name", "").startswith("Seed Claude Code"))
    assert seed["vars"]["_settings_seed"]["skipDangerousModePermissionPrompt"] is True


def test_claude_as_root_needs_is_sandbox_to_use_bypass_mode():
    """Verified on a real VM: Claude refuses bypass mode as root unless IS_SANDBOX=1."""
    assert la.auto_approve_exports("claude") == "export IS_SANDBOX=1; "
    assert la.auto_approve_exports("cursor") == "" and la.auto_approve_exports("opencode") == ""
    unattended = RemoteDevEnvironment.agent_remote_command("claude", prompt_file="/tmp/x", auto_approve=True)
    assert "export IS_SANDBOX=1; " in unattended
    supervised = RemoteDevEnvironment.agent_remote_command("claude", prompt_file="/tmp/x", auto_approve=False)
    assert "IS_SANDBOX" not in supervised
    only_flag = RemoteDevEnvironment.agent_remote_command("claude", auto_approve=True)
    assert "export IS_SANDBOX=1; " in only_flag and "IS_SANDBOX" not in RemoteDevEnvironment.agent_remote_command(
        "claude"
    )


def test_authorize_hands_live_agent_state_and_log_dirs_to_the_workspace_user():
    from _roles import authorize_play

    play = authorize_play()
    task = next(t for t in play["tasks"] if t.get("name", "").startswith("Let the workspace user write live-agent"))
    assert task["file"]["owner"] == "{{ rig_user }}"
    assert task["loop"] == [la.STATE_DIR, la.LOG_DIR]
