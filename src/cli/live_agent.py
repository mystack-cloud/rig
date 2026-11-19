"""Live agents: an Agent's process kept running on an Environment.

A live agent is a tmux session on a socket of its own (``tmux -L rig``) with a
config file that rig ships, so it never touches a tmux the user runs. The agent
keeps running when the SSH connection drops or the user detaches.

* ``rig attach`` starts the live agent or joins the existing one.
* ``rig exec`` starts one with a prompt (unattended), or queues the prompt into
  a running Claude Code when its hooks say that is safe.

The helper scripts and config under ``cli/assets`` are deployed by authorize.yml.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from cli.agents import SUPPORTED_AGENTS

TMUX_SOCKET = "rig"
TMUX_CONF = "/etc/rig/tmux.conf"
LOG_DIR = "/var/log/rig"
STATE_DIR = "/run/rig/agents"
QUEUE_HELPER = "/usr/local/bin/rig-agent-queue"
STATE_HELPER = "/usr/local/bin/rig-agent-state"
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# Flags that make an unattended agent run without stopping at approval prompts.
AUTO_APPROVE_FLAGS: dict[str, list[str]] = {
    "claude": ["--permission-mode", "bypassPermissions"],
    "cursor": ["--force"],
    "opencode": ["--auto"],
}

# Exit status of rig-agent-queue -> why a prompt could not be queued.
QUEUE_RESULTS = {
    0: "queued",
    10: "waiting for approval",
    11: "state unknown",
    12: "no live agent",
    13: "delivery failed",
}
QUEUE_REFUSALS = {
    10: "it is waiting for approval; attach to answer it first",
    11: "its state is unknown (the Environment predates live-agent hooks; run `rig apply --authorize`)",
    12: "no live agent is running",
    13: "tmux could not deliver the prompt",
}


@dataclass(frozen=True)
class LiveAgent:
    agent: str
    dead: bool
    created: int
    attached: int

    def describe(self, now: int) -> str:
        if self.dead:
            return f"{self.agent} (exited with an error)"
        minutes = max(0, now - self.created) // 60
        age = f"{minutes}m" if minutes < 120 else f"{minutes // 60}h"
        clients = f"{self.attached} attached" if self.attached else "detached"
        return f"{self.agent} (running {age}, {clients})"


def session_name(agent: str) -> str:
    return f"rig-{agent}"


def _tmux(socket: str, conf: str) -> str:
    return f"tmux -L {shlex.quote(socket)} -f {shlex.quote(conf)}"


def ensure_session_script(
    agent: str,
    launch_cmd: str,
    *,
    restart: bool = False,
    socket: str = TMUX_SOCKET,
    conf: str = TMUX_CONF,
    log_dir: str = LOG_DIR,
) -> str:
    """Shell that makes sure the agent's session exists, starting it when needed.

    An existing healthy session is left alone (a later attach joins it). A dead
    pane (the agent failed) and ``restart`` both replace it. The previous run's
    output log is kept as ``.prev`` and the new one is recorded by tmux so the
    resume id survives an unattended exit.
    """
    name = session_name(agent)
    tmux = _tmux(socket, conf)
    log = f"{log_dir}/{name}.log"
    lines = [
        "set -u",
        f"mkdir -p {shlex.quote(log_dir)}",
        f"S={shlex.quote(name)}",
        f"LOG={shlex.quote(log)}",
    ]
    if restart:
        lines.append(f'{tmux} kill-session -t "$S" 2>/dev/null || true')
    lines += [
        f'if {tmux} has-session -t "$S" 2>/dev/null; then',
        f'  if [ "$({tmux} list-panes -t "$S" -F \'#{{pane_dead}}\' | head -n 1)" = 1 ]; then',
        f'    {tmux} kill-session -t "$S"',
        "  fi",
        "fi",
        f'if ! {tmux} has-session -t "$S" 2>/dev/null; then',
        '  [ -f "$LOG" ] && mv -f "$LOG" "$LOG.prev"',
        '  : > "$LOG"',
        f'  {tmux} new-session -d -s "$S" -x 200 -y 50 {shlex.quote(launch_cmd)} || exit 1',
        # Best effort: an agent that dies at once leaves a dead pane to pipe.
        f'  {tmux} pipe-pane -o -t "$S" "cat >> $LOG" 2>/dev/null || true',
        "fi",
    ]
    return "\n".join(lines)


def attach_script(agent: str, launch_cmd: str, **kwargs: object) -> str:
    """Ensure the session, then attach this terminal to it (needs a tty)."""
    socket = str(kwargs.get("socket", TMUX_SOCKET))
    conf = str(kwargs.get("conf", TMUX_CONF))
    body = ensure_session_script(agent, launch_cmd, **kwargs)  # type: ignore[arg-type]
    return f'{body}\nexec {_tmux(socket, conf)} attach-session -t "{session_name(agent)}"'


def verify_started_script(
    agent: str, *, socket: str = TMUX_SOCKET, conf: str = TMUX_CONF, wait_seconds: int = 2
) -> str:
    """Shell that waits briefly, then fails (status 3) with the pane's text if the agent already died.

    Catches the common unattended failures (no credentials, a bad flag) that would
    otherwise look like a successful start.
    """
    name = shlex.quote(session_name(agent))
    tmux = _tmux(socket, conf)
    return "\n".join(
        [
            f"sleep {int(wait_seconds)}",
            f"if [ \"$({tmux} list-panes -t {name} -F '#{{pane_dead}}' 2>/dev/null | head -n 1)\" = 1 ]; then",
            f"  {tmux} capture-pane -p -S - -t {name} | grep -v '^$' | tail -n 20",
            "  exit 3",
            "fi",
        ]
    )


def list_script(*, socket: str = TMUX_SOCKET, conf: str = TMUX_CONF) -> str:
    """Shell printing one line per live agent: name|dead|created|attached."""
    fmt = "#{session_name}|#{pane_dead}|#{session_created}|#{session_attached}"
    return f"{_tmux(socket, conf)} list-panes -a -F {shlex.quote(fmt)} 2>/dev/null || true"


def parse_live_agents(output: str) -> list[LiveAgent]:
    """Parse :func:`list_script` output; ignores sessions that are not rig's."""
    found: dict[str, LiveAgent] = {}
    for line in (output or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) != 4 or not parts[0].startswith("rig-"):
            continue
        agent = parts[0][len("rig-") :]
        if agent not in SUPPORTED_AGENTS:
            continue
        try:
            created, attached = int(parts[2]), int(parts[3])
        except ValueError:
            continue
        found[agent] = LiveAgent(agent, parts[1] == "1", created, attached)
    return sorted(found.values(), key=lambda la: la.agent)


def running_agents(live: list[LiveAgent]) -> list[str]:
    """Agent types with a healthy (not dead) live agent."""
    return [la.agent for la in live if not la.dead]


def recent_log_script(agent: str, *, log_dir: str = LOG_DIR, max_bytes: int = 65536) -> str:
    """Shell printing the tail of the agent's recorded output (empty when none)."""
    log = shlex.quote(f"{log_dir}/{session_name(agent)}.log")
    return f"tail -c {int(max_bytes)} {log} 2>/dev/null || true"


def queue_script(agent: str, *, helper: str = QUEUE_HELPER) -> str:
    """Shell reading a prompt on stdin and queueing it via the helper."""
    return f"{shlex.quote(helper)} {shlex.quote(agent)}"


def upload_prompt_script() -> str:
    """Shell that stores stdin in a private temp file and prints its path."""
    return 'umask 077; f="$(mktemp /tmp/rig-prompt.XXXXXX)" && cat > "$f" && printf "%s\\n" "$f"'


def prompt_launch_fragments(agent: str, prompt_file: str, auto_approve: bool) -> tuple[str, str, str]:
    """Pieces to splice into an agent's launch command for an unattended prompt.

    Returns ``(lead, flags, trailer)``: ``lead`` reads and deletes the prompt
    file into ``$p`` before the agent starts, ``flags`` are inserted after the
    agent binary, and ``trailer`` passes the prompt (OpenCode needs ``--prompt``;
    Claude Code and Cursor take it as the positional argument).
    """
    lead = f'p="$(cat {shlex.quote(prompt_file)})"; rm -f {shlex.quote(prompt_file)}; '
    flags = ""
    if auto_approve:
        flags = " " + " ".join(shlex.quote(f) for f in AUTO_APPROVE_FLAGS[agent])
    trailer = ' --prompt "$p"' if agent == "opencode" else ' "$p"'
    return lead, flags, trailer


def read_prompt(value: str, *, stdin_text: str | None = None) -> str:
    """Resolve ``rig exec``'s prompt argument: text, ``@file`` or ``-`` (stdin)."""
    if value == "-":
        text = stdin_text if stdin_text is not None else ""
    elif value.startswith("@") and len(value) > 1:
        path = os.path.expanduser(value[1:])
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            raise ValueError(f"cannot read prompt file {path}: {exc.strerror or exc}") from exc
    else:
        text = value
    text = text.strip("\n")
    if not text.strip():
        raise ValueError("the prompt is empty")
    return text
