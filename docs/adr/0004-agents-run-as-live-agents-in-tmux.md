# Agents run as live agents in a rig-owned tmux, and `rig exec` starts them unattended

`rig attach` used to run the agent as the SSH session's own process, so closing the terminal killed it. Agents now run as live agents in a tmux session on a socket of their own (`tmux -L rig`, a config rig ships), which keeps them alive when the terminal goes away. `rig exec` starts one with a prompt and returns; it skips the agent's approval prompts by default, and queues a prompt into an already-running Claude Code only when Claude's hooks say that is safe.

## Considered Options

- **dtach / abduco**: rejected. They keep no screen state and give no scrollback, the redraw on reattach is uncertain, and abduco was not confirmed in Ubuntu 24.04. tmux is in the default repositories (3.4), and Claude Code documents a tmux configuration.
- **screen**: rejected. No evidence it handles the Agents' key sequences, and Cursor's docs say it intercepts Shift+Enter like tmux does.
- **Sharing the user's tmux or a default tmux config**: rejected. A separate socket and `-f` file mean rig never changes the behaviour of a tmux someone runs on the VM, and rig can set status bar off and the settings Claude Code recommends.
- **Queueing by sending keystrokes to every Agent**: rejected. A pasted prompt and Enter would answer an approval or trust dialog, and the Agents differ (Claude queues, OpenCode steers, Cursor is undocumented). Queueing is gated on Claude's hook-written state and supported for Claude Code only; the others refuse with a reason until verified.
- **Opt-in persistence (`--persist`)**: rejected. Losing an agent's work to a dropped connection is the worse default; `--no-persist` keeps the old behaviour.
- **A separate flag for unattended approval skipping**: rejected in favour of making it the default for `rig exec`, because an unattended agent that stops at a prompt is not unattended. `--no-auto-approve` opts out.

## Consequences

- Claude Code refuses its bypass mode as root, so `rig exec` launches it with `IS_SANDBOX=1`, and authorize pre-accepts its first-use warning dialog. Both were found by running against a real VM, and both would otherwise stop an unattended run silently.
- An unattended agent can run any command on the Environment, which has internet access, tokens and root. Prompt injection from repository content is a real risk, and `rig exec` says so every time it skips approvals.
- A live agent keeps the credentials and MCP config it started with; `rig attach --restart` picks up changes. `rig apply` and `rig delete` warn about running live agents but do not stop.
- tmux is part of the provisioned image, and Claude Code's settings carry rig-owned hooks (merged with the user's, identified by their command path).
- Agent output is logged on the VM so a resume id survives an unattended exit. The log can contain whatever the agent printed.
